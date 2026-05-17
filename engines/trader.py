"""
AutoTrader — Binance Futures execution engine.
Fires when: enabled + confidence >= min_confidence + open_trades < max_trades + no existing position for pair.
Position size: margin = equity * risk_pct%, notional = margin * leverage.
"""
import logging, math, time
import trader_db
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger(__name__)


class AutoTrader:
    def __init__(self, socketio):
        self.socketio = socketio
        trader_db.init_db()

    # ── Public: called from main after every engine run ─────────────────

    def execute_signal(self, pair: str, payload: dict):
        try:
            if not self._is_enabled():
                return

            sig        = payload.get("signal", {})
            decision   = sig.get("bias", "NO_TRADE")
            confidence = sig.get("confidence", 0) or 0
            timeframe  = payload.get("timeframe", "")

            if decision not in ("BUY", "SELL"):
                return

            min_conf = int(trader_db.get_setting("min_confidence", "75"))
            if confidence < min_conf:
                return

            max_trades  = int(trader_db.get_setting("max_trades", "6"))
            open_trades = trader_db.get_open_trades()
            if len(open_trades) >= max_trades:
                log.info("AutoTrader [%s]: max open trades reached (%d)", pair, max_trades)
                return

            if trader_db.get_open_trade_by_pair(pair):
                return  # already in this pair

            sl  = sig.get("stop_loss")
            tp1 = sig.get("tp1")
            tp2 = sig.get("tp2")
            tp3 = sig.get("tp3")

            if not sl or not tp1:
                log.warning("AutoTrader [%s]: missing SL or TP1 — skipping", pair)
                return

            self._place_trade(pair, decision, float(sl), float(tp1),
                              tp2, tp3, int(confidence), timeframe)

        except Exception as e:
            log.error("AutoTrader execute_signal error [%s]: %s", pair, e)

    # ── Public: monitor open trades (called in analysis loop) ────────────

    def monitor_trades(self):
        try:
            client = self._get_client()
        except Exception as e:
            log.error("AutoTrader monitor: client init failed: %s", e)
            return

        trade_tp_usd  = float(trader_db.get_setting("trade_tp_usd",  "0") or 0)
        basket_tp_usd = float(trader_db.get_setting("basket_tp_usd", "0") or 0)

        # ── Fetch ALL live positions directly from Binance ────────────
        try:
            all_positions = client.futures_position_information()
        except Exception as e:
            log.error("AutoTrader monitor: position fetch failed: %s", e)
            return

        active = [(p["symbol"], float(p["positionAmt"]),
                   round(float(p.get("unRealizedProfit", 0)), 4),
                   float(p.get("entryPrice", 0)))
                  for p in all_positions if float(p.get("positionAmt", 0)) != 0]

        if not active:
            for trade in trader_db.get_open_trades():
                self._handle_closed(client, trade)
            return

        total_upnl = 0.0
        remaining  = []

        for pair, amt, upnl, entry_price in active:
            self.socketio.emit("trade_pnl", {"pair": pair, "unrealized_pnl": upnl})

            if trade_tp_usd > 0 and upnl >= trade_tp_usd:
                log.info("AutoTrader [%s]: per-trade TP hit ($%.2f >= $%.2f) — closing",
                         pair, upnl, trade_tp_usd)
                self._close_position(client, pair, amt, entry_price, "TP_USD")
            else:
                total_upnl += upnl
                remaining.append((pair, amt, entry_price))

        # Basket TP — close everything if combined PnL target hit
        if basket_tp_usd > 0 and remaining and total_upnl >= basket_tp_usd:
            log.info("AutoTrader: basket TP hit ($%.2f >= $%.2f) — closing %d positions",
                     total_upnl, basket_tp_usd, len(remaining))
            for pair, amt, entry_price in remaining:
                self._close_position(client, pair, amt, entry_price, "BASKET_TP")

    # ── Public: account info for dashboard ───────────────────────────────

    def get_account_info(self) -> dict:
        try:
            client  = self._get_client()
            account = client.futures_account()
            return {
                "ok":              True,
                "balance":         round(float(account['totalWalletBalance']),   2),
                "equity":          round(float(account['totalMarginBalance']),   2),
                "unrealized_pnl":  round(float(account['totalUnrealizedProfit']), 4),
                "available":       round(float(account['availableBalance']),     2),
            }
        except BinanceAPIException as e:
            return {"ok": False, "error": e.message}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Internal: place trade ────────────────────────────────────────────

    def _place_trade(self, pair, direction, sl, tp1, tp2, tp3, confidence, timeframe):
        try:
            client   = self._get_client()
            equity   = self._get_equity(client)
            if equity <= 0:
                log.error("AutoTrader [%s]: equity is 0", pair)
                return

            leverage = int(trader_db.get_setting("leverage", "10"))
            risk_pct = float(trader_db.get_setting("risk_pct", "0.5")) / 100

            # Set leverage on symbol
            try:
                client.futures_change_leverage(symbol=pair, leverage=leverage)
            except Exception as e:
                log.warning("AutoTrader [%s]: set leverage failed: %s", pair, e)

            # Get current mark price for qty calc
            mark    = float(client.futures_mark_price(symbol=pair)['markPrice'])
            step_sz, min_qty = self._get_filters(client, pair)

            # margin = equity * risk_pct  |  notional = margin * leverage
            margin   = equity * risk_pct
            notional = margin * leverage
            qty      = self._floor_qty(notional / mark, step_sz)

            if qty < float(min_qty):
                log.warning("AutoTrader [%s]: qty %.6f < min %.6f", pair, qty, float(min_qty))
                return

            side    = "BUY" if direction == "BUY" else "SELL"
            sl_side = "SELL" if side == "BUY" else "BUY"

            # ── Market entry ────────────────────────────────────────────
            entry_order = client.futures_create_order(
                symbol=pair, side=side, type="MARKET", quantity=qty
            )
            actual_entry   = float(entry_order.get("avgPrice") or mark)
            entry_order_id = str(entry_order["orderId"])
            log.info("AutoTrader [%s]: %s entry %.4f qty=%.6f conf=%d",
                     pair, side, actual_entry, qty, confidence)

            # ── Stop Loss ───────────────────────────────────────────────
            sl_order_id = None
            try:
                sl_order = client.futures_create_order(
                    symbol=pair, side=sl_side, type="STOP_MARKET",
                    stopPrice=self._fmt_price(sl),
                    closePosition=True, workingType="MARK_PRICE"
                )
                sl_order_id = str(sl_order["orderId"])
            except BinanceAPIException as e:
                log.error("AutoTrader [%s]: SL order failed: %s", pair, e.message)

            # ── Take Profit 1 ───────────────────────────────────────────
            tp1_order_id = None
            try:
                tp_order = client.futures_create_order(
                    symbol=pair, side=sl_side, type="TAKE_PROFIT_MARKET",
                    stopPrice=self._fmt_price(tp1),
                    closePosition=True, workingType="MARK_PRICE"
                )
                tp1_order_id = str(tp_order["orderId"])
            except BinanceAPIException as e:
                log.error("AutoTrader [%s]: TP1 order failed: %s", pair, e.message)

            # ── Save to DB ──────────────────────────────────────────────
            trade_id = trader_db.add_open_trade({
                'pair':           pair,
                'direction':      direction,
                'entry_price':    actual_entry,
                'sl':             sl,
                'tp1':            tp1,
                'tp2':            tp2,
                'tp3':            tp3,
                'qty':            qty,
                'notional':       round(notional, 4),
                'entry_order_id': entry_order_id,
                'sl_order_id':    sl_order_id,
                'tp1_order_id':   tp1_order_id,
                'confidence':     confidence,
                'timeframe':      timeframe,
            })

            self.socketio.emit("trade_opened", {
                "id":         trade_id,
                "pair":       pair,
                "direction":  direction,
                "entry":      actual_entry,
                "sl":         sl,
                "tp1":        tp1,
                "tp2":        tp2,
                "tp3":        tp3,
                "qty":        qty,
                "notional":   round(notional, 4),
                "confidence": confidence,
                "timeframe":  timeframe,
            })

        except BinanceAPIException as e:
            log.error("AutoTrader [%s]: Binance error: %s", pair, e.message)
        except Exception as e:
            log.error("AutoTrader [%s]: _place_trade error: %s", pair, e)

    # ── Internal: close any live position by pair (works for manual trades too) ──

    def _close_position(self, client, pair: str, pos_amt: float, entry_price: float, reason: str):
        try:
            qty        = abs(pos_amt)
            direction  = "BUY" if pos_amt > 0 else "SELL"
            close_side = "SELL" if pos_amt > 0 else "BUY"

            try:
                client.futures_cancel_all_open_orders(symbol=pair)
            except Exception:
                pass

            client.futures_create_order(
                symbol=pair, side=close_side, type="MARKET",
                quantity=qty, reduceOnly=True
            )

            fills       = client.futures_account_trades(symbol=pair, limit=5)
            close_price = float(fills[-1]["price"]) if fills else entry_price

            pnl = (close_price - entry_price) * qty
            if direction == "SELL":
                pnl = -pnl
            pnl = round(pnl, 4)

            # Bot-opened trade — use existing DB close flow
            db_trade = trader_db.get_open_trade_by_pair(pair)
            if db_trade:
                trader_db.close_trade(db_trade["id"], close_price, pnl, reason)
            else:
                # Manually opened — write directly to closed_trades so history shows it
                trader_db.add_closed_trade_direct({
                    "pair":        pair,
                    "direction":   direction,
                    "entry_price": entry_price,
                    "close_price": close_price,
                    "qty":         qty,
                    "notional":    round(qty * close_price, 2),
                    "pnl":         pnl,
                    "close_reason": reason,
                })

            self.socketio.emit("trade_closed", {
                "pair": pair, "close_price": close_price,
                "close_reason": reason, "pnl": pnl,
            })
            log.info("AutoTrader [%s]: closed | %s @ %.4f | PnL: %.4f", pair, reason, close_price, pnl)

        except Exception as e:
            log.error("AutoTrader [%s]: _close_position error: %s", pair, e)

    # ── Internal: force-close at market (dollar TP / basket TP) ─────────

    def _close_at_market(self, client, trade, reason: str):
        pair = trade['pair']
        try:
            # Cancel remaining SL/TP orders
            for oid in (trade.get('sl_order_id'), trade.get('tp1_order_id')):
                if oid:
                    try:
                        client.futures_cancel_order(symbol=pair, orderId=int(oid))
                    except Exception:
                        pass

            # Confirm position is still open and get actual qty
            positions = client.futures_position_information(symbol=pair)
            pos = next((p for p in positions if float(p['positionAmt']) != 0), None)
            if not pos:
                self._handle_closed(client, trade)
                return

            actual_qty = abs(float(pos['positionAmt']))
            close_side = "SELL" if trade['direction'] == "BUY" else "BUY"

            client.futures_create_order(
                symbol=pair, side=close_side, type="MARKET",
                quantity=actual_qty, reduceOnly=True
            )

            fills = client.futures_account_trades(symbol=pair, limit=5)
            close_price = float(fills[-1]['price']) if fills else trade['entry_price']

            pnl = (close_price - trade['entry_price']) * trade['qty']
            if trade['direction'] == "SELL":
                pnl = -pnl
            pnl = round(pnl, 4)

            trader_db.close_trade(trade['id'], close_price, pnl, reason)
            self.socketio.emit("trade_closed", {
                "pair": pair, "pnl": pnl,
                "close_price": close_price, "close_reason": reason,
            })
            log.info("AutoTrader [%s]: closed | %s | PnL: %.4f", pair, reason, pnl)

        except Exception as e:
            log.error("AutoTrader [%s]: _close_at_market error: %s", pair, e)

    # ── Internal: handle detected close ─────────────────────────────────

    def _handle_closed(self, client, trade):
        pair = trade['pair']
        try:
            # Cancel any remaining SL/TP orders
            for oid in (trade.get('sl_order_id'), trade.get('tp1_order_id')):
                if oid:
                    try:
                        client.futures_cancel_order(symbol=pair, orderId=int(oid))
                    except Exception:
                        pass

            # Get last fill price
            fills = client.futures_account_trades(symbol=pair, limit=5)
            close_price = float(fills[-1]['price']) if fills else trade['entry_price']

            direction = trade['direction']
            pnl       = (close_price - trade['entry_price']) * trade['qty']
            if direction == "SELL":
                pnl = -pnl
            pnl = round(pnl, 4)

            close_reason = "TP" if pnl > 0 else "SL"
            trader_db.close_trade(trade['id'], close_price, pnl, close_reason)

            self.socketio.emit("trade_closed", {
                "pair":        pair,
                "pnl":         pnl,
                "close_price": close_price,
                "close_reason": close_reason,
            })
            log.info("AutoTrader [%s]: closed | %s | PnL: %.4f", pair, close_reason, pnl)

        except Exception as e:
            log.error("AutoTrader [%s]: _handle_closed error: %s", pair, e)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _is_enabled(self) -> bool:
        return trader_db.get_setting("enabled", "0") == "1"

    def _get_client(self) -> Client:
        testnet = trader_db.get_setting("testnet", "0") == "1"
        if testnet:
            api_key    = trader_db.get_setting("tn_api_key")
            api_secret = trader_db.get_setting("tn_api_secret")
            if not api_key or not api_secret:
                raise ValueError("Testnet API keys not configured")
            return Client(api_key, api_secret, testnet=True)
        else:
            api_key    = trader_db.get_setting("api_key")
            api_secret = trader_db.get_setting("api_secret")
            if not api_key or not api_secret:
                raise ValueError("Real API keys not configured")
            return Client(api_key, api_secret)

    def _get_equity(self, client) -> float:
        account = client.futures_account()
        return float(account['availableBalance'])

    def _get_filters(self, client, pair) -> tuple:
        try:
            info = client.futures_exchange_info()
            for sym in info['symbols']:
                if sym['symbol'] == pair:
                    for f in sym['filters']:
                        if f['filterType'] == 'LOT_SIZE':
                            return f['stepSize'], f['minQty']
        except Exception:
            pass
        return "0.001", "0.001"

    def _floor_qty(self, qty: float, step_size: str) -> float:
        if '.' in step_size:
            decimals = len(step_size.rstrip('0').split('.')[-1])
        else:
            decimals = 0
        factor = 10 ** decimals
        return math.floor(qty * factor) / factor

    def _fmt_price(self, price: float) -> str:
        return f"{price:.4f}"
