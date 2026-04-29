"""
Engine 4 — Strategy Decision Engine (Core Brain)
Combines Engine 1 (context) + Engine 2 (liquidity) + Engine 3 (structure)
into a final trade decision: BUY / SELL / NO TRADE + Entry / SL / TP levels.
Rule: trade ONLY when liquidity + structure + session are all aligned.
"""
import logging

log = logging.getLogger(__name__)

MIN_RR      = 1.5    # minimum risk/reward to accept a trade
ATR_SL_MULT = 1.5    # ATR multiplier for stop loss buffer
ATR_ENTRY   = 0.3    # ATR multiplier for entry zone width


class Engine4:
    def __init__(self, cfg):
        self.cfg = cfg

    # ── Public entry point ───────────────────────────────────────────
    def run(self, pair: str, e1: dict, e2: dict, e3: dict) -> dict:
        try:
            # ── Unpack Engine 1
            pd       = e1.get("price_data", {})
            dv       = e1.get("derivatives", {})
            sn       = e1.get("sentiment",  {})
            ss       = e1.get("session",    {})
            price    = pd.get("price", 0)
            atr      = pd.get("atr", price * 0.003)   # fallback 0.3% of price
            vol      = pd.get("volatility", "Low")
            session  = ss.get("active", "")
            funding  = dv.get("funding_bias", "Neutral")
            sent_lbl = sn.get("label", "Neutral")
            sent_scr = sn.get("score") or 50

            # ── Unpack Engine 2
            sw2          = e2.get("sweep", {})
            sweep_ok     = sw2.get("detected", False)
            sweep_dir    = sw2.get("direction")        # "up" or "down"
            swept_level  = sw2.get("swept_level")
            reaction     = sw2.get("reaction", "None")
            nearest_bsl  = e2.get("nearest_bsl")
            nearest_ssl  = e2.get("nearest_ssl")
            e2_signal    = e2.get("signal", "NEUTRAL")

            # ── Unpack Engine 3
            trend        = e3.get("trend", "Range")
            choch        = e3.get("choch", {})
            bos          = e3.get("bos",   {})
            choch_ok     = choch.get("detected", False)
            choch_dir    = choch.get("direction", "")
            choch_level  = choch.get("level")
            last_s_high  = e3.get("last_swing_high")
            last_s_low   = e3.get("last_swing_low")
            strength     = e3.get("strength", "Weak")
            manip        = e3.get("manipulation", False)

            reasons = []

            # ════════════════════════════════════════════════════════
            # NOTE 1 — Session context (informational only, not a gate)
            # System runs 24/7 — session is used as a probability note
            # ════════════════════════════════════════════════════════
            session_ok = session in ("London", "New York")
            if session_ok:
                reasons.append(f"Session: {session} — high liquidity window")
            else:
                reasons.append(f"Session: {session} — lower liquidity, proceed with caution")

            # ════════════════════════════════════════════════════════
            # GATE 2 — Liquidity sweep required
            # ════════════════════════════════════════════════════════
            if not sweep_ok:
                return self._no_trade(pair, price, "No liquidity sweep detected — no setup", reasons)
            reasons.append(
                f"Sweep: {'sell-side (below lows)' if sweep_dir == 'down' else 'buy-side (above highs)'}"
                f" @ {swept_level}"
            )

            # ════════════════════════════════════════════════════════
            # GATE 3 — Sweep reaction must be reversal
            # ════════════════════════════════════════════════════════
            if reaction != "Reversal":
                return self._no_trade(pair, price, f"Sweep reaction is '{reaction}' — not a reversal, skipping", reasons)
            reasons.append("Sweep reaction: Reversal confirmed")

            # ════════════════════════════════════════════════════════
            # GATE 4 — CHoCH structure confirmation
            # ════════════════════════════════════════════════════════
            if not choch_ok:
                return self._no_trade(pair, price, "No CHoCH — structure not confirmed, no entry", reasons)

            # Alignment: sell-side sweep + bullish CHoCH = BUY
            #            buy-side sweep  + bearish CHoCH = SELL
            aligned = (
                (sweep_dir == "down" and choch_dir == "Bullish") or
                (sweep_dir == "up"   and choch_dir == "Bearish")
            )
            if not aligned:
                return self._no_trade(
                    pair, price,
                    f"Sweep ({sweep_dir}) and CHoCH ({choch_dir}) are misaligned — conflicting signals",
                    reasons
                )

            decision = "BUY" if choch_dir == "Bullish" else "SELL"
            reasons.append(f"CHoCH: {choch_dir} — structure shifted, confirms {decision}")

            # ════════════════════════════════════════════════════════
            # GATE 5 — Volatility (must not be low)
            # ════════════════════════════════════════════════════════
            if vol == "Low":
                return self._no_trade(pair, price, "Volatility too low — no significant movement expected", reasons)
            reasons.append(f"Volatility: {vol} — sufficient for trade")

            # ════════════════════════════════════════════════════════
            # FILTER — Funding & Sentiment (probability booster)
            # ════════════════════════════════════════════════════════
            prob_score = 0
            if decision == "BUY":
                if funding == "Short-heavy":
                    prob_score += 2
                    reasons.append("Funding: short-heavy — shorts will fuel the rally")
                elif funding == "Neutral":
                    prob_score += 1
                if sent_scr <= 35:
                    prob_score += 2
                    reasons.append("Sentiment: extreme fear — accumulation zone, favors BUY")
                elif sent_scr <= 50:
                    prob_score += 1
            else:  # SELL
                if funding == "Long-heavy":
                    prob_score += 2
                    reasons.append("Funding: long-heavy — longs will fuel the drop")
                elif funding == "Neutral":
                    prob_score += 1
                if sent_scr >= 75:
                    prob_score += 2
                    reasons.append("Sentiment: extreme greed — distribution zone, favors SELL")
                elif sent_scr >= 55:
                    prob_score += 1

            if manip:
                prob_score += 2
                reasons.append("Smart money pattern: Sweep → CHoCH (manipulation confirmed)")

            if strength == "Strong":
                prob_score += 1
                reasons.append("Structure strength: Strong")

            # ════════════════════════════════════════════════════════
            # CALCULATE LEVELS
            # ════════════════════════════════════════════════════════
            entry_low, entry_high, stop_loss, tp1, tp2, tp3 = self._calc_levels(
                decision, price, atr, swept_level, choch_level,
                nearest_bsl, nearest_ssl, last_s_high, last_s_low
            )

            if entry_low is None:
                return self._no_trade(pair, price, "Could not calculate valid trade levels", reasons)

            entry_mid = (entry_low + entry_high) / 2

            # ════════════════════════════════════════════════════════
            # RISK/REWARD CHECK
            # ════════════════════════════════════════════════════════
            rr = self._calc_rr(decision, entry_mid, stop_loss, tp1)
            if rr < MIN_RR:
                return self._no_trade(
                    pair, price,
                    f"Risk/reward {rr:.2f}:1 below minimum {MIN_RR}:1 — not worth the risk",
                    reasons
                )
            reasons.append(f"Risk/Reward: {rr:.2f}:1 — acceptable")

            # Setup label
            if manip:
                setup = "Sweep + CHoCH Reversal (Manipulation)"
            elif choch_ok and bos.get("confirmed"):
                setup = "BOS + CHoCH Confirmation"
            else:
                setup = "Sweep + CHoCH Reversal"

            reason_text = " | ".join(reasons)

            return {
                "pair":        pair,
                "decision":    decision,
                "bias":        "Bullish" if decision == "BUY" else "Bearish",
                "setup":       setup,
                "entry_low":   round(entry_low,  4),
                "entry_high":  round(entry_high, 4),
                "stop_loss":   round(stop_loss,  4),
                "tp1":         round(tp1, 4),
                "tp2":         round(tp2, 4) if tp2 else None,
                "tp3":         round(tp3, 4) if tp3 else None,
                "risk_reward": round(rr, 2),
                "prob_score":  prob_score,
                "reason":      reason_text,
                "ok":          True,
            }

        except Exception as e:
            log.error("Engine4 [%s] error: %s", pair, e)
            return {"pair": pair, "ok": False, "decision": "NO_TRADE",
                    "reason": f"Engine error: {e}", "error": str(e)}

    # ── Level calculation ────────────────────────────────────────────
    def _calc_levels(self, decision, price, atr, swept_level, choch_level,
                     nearest_bsl, nearest_ssl, last_s_high, last_s_low):
        buf = atr * ATR_SL_MULT
        ez  = atr * ATR_ENTRY

        if decision == "BUY":
            entry_low  = price - ez
            entry_high = price + ez
            stop_loss  = (swept_level - buf) if swept_level else (price - atr * 3)
            tp1        = last_s_high if last_s_high and last_s_high > price else price + atr * 3
            tp2        = nearest_bsl if nearest_bsl and nearest_bsl > tp1 else tp1 + atr * 5
            tp3        = tp2 + (tp2 - price) * 0.5 if tp2 else None

        else:  # SELL
            entry_low  = price - ez
            entry_high = price + ez
            stop_loss  = (swept_level + buf) if swept_level else (price + atr * 3)
            tp1        = last_s_low  if last_s_low  and last_s_low  < price else price - atr * 3
            tp2        = nearest_ssl if nearest_ssl and nearest_ssl < tp1 else tp1 - atr * 5
            tp3        = tp2 - (price - tp2) * 0.5 if tp2 else None

        # Sanity: SL must not be on wrong side of entry
        entry_mid = (entry_low + entry_high) / 2
        if decision == "BUY"  and stop_loss >= entry_mid:
            return None, None, None, None, None, None
        if decision == "SELL" and stop_loss <= entry_mid:
            return None, None, None, None, None, None

        return entry_low, entry_high, stop_loss, tp1, tp2, tp3

    # ── Risk / reward ────────────────────────────────────────────────
    @staticmethod
    def _calc_rr(decision, entry_mid, sl, tp1) -> float:
        try:
            if decision == "BUY":
                risk   = entry_mid - sl
                reward = tp1 - entry_mid
            else:
                risk   = sl - entry_mid
                reward = entry_mid - tp1
            return reward / risk if risk > 0 else 0.0
        except Exception:
            return 0.0

    # ── No trade helper ──────────────────────────────────────────────
    @staticmethod
    def _no_trade(pair, price, reason, reasons) -> dict:
        reasons.append(f"NO TRADE: {reason}")
        return {
            "pair":        pair,
            "decision":    "NO_TRADE",
            "bias":        "Neutral",
            "setup":       "No Setup",
            "entry_low":   None,
            "entry_high":  None,
            "stop_loss":   None,
            "tp1":         None,
            "tp2":         None,
            "tp3":         None,
            "risk_reward": None,
            "prob_score":  0,
            "reason":      " | ".join(reasons),
            "ok":          True,
        }
