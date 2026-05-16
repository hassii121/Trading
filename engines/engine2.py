"""
Engine 2 — Liquidity Engine
Infers liquidity pools, stop hunt zones, and sweep events purely from Binance OHLC candles.
No external APIs required. 100% logic-based.
"""
import logging

log = logging.getLogger(__name__)

SWING_N        = 3      # bars each side to confirm swing high/low
EQ_TOLERANCE   = 0.002  # 0.2% — price within this range = "equal" level
SWEEP_LOOKBACK = 20     # last N candles checked for sweeps

# Liquidity TF always one step above selected; sweep TF = selected or one below
TF_MAP = {
    "1m":  ("15m", "5m"),
    "5m":  ("30m", "15m"),
    "15m": ("1h",  "15m"),
    "30m": ("1h",  "15m"),
    "1h":  ("4h",  "30m"),
    "4h":  ("1d",  "1h"),
}


class Engine2:
    def __init__(self, cfg, binance_client):
        self.cfg    = cfg
        self.client = binance_client

    # ── Public entry point ───────────────────────────────────────────
    def run(self, pair: str, timeframe: str = "30m") -> dict:
        try:
            tf_liq, tf_sweep = TF_MAP.get(timeframe, ("1h", "15m"))
            klines_1h  = self.client.get_klines(symbol=pair, interval=tf_liq,   limit=100)
            klines_15m = self.client.get_klines(symbol=pair, interval=tf_sweep, limit=60)

            candles_1h  = self._parse(klines_1h)
            candles_15m = self._parse(klines_15m)

            price = candles_15m[-1]["close"]

            # Step 1: swing highs/lows on 1h
            swing_highs, swing_lows = self._find_swings(candles_1h, SWING_N)

            # Step 2: equal highs/lows → liquidity clusters
            bsl_zones = self._cluster_levels([s["price"] for s in swing_highs], EQ_TOLERANCE)
            ssl_zones = self._cluster_levels([s["price"] for s in swing_lows],  EQ_TOLERANCE)

            # Nearest BSL above price / nearest SSL below price
            nearest_bsl = min((z for z in bsl_zones if z > price), default=None)
            nearest_ssl = max((z for z in ssl_zones if z < price), default=None)

            # Step 3: sweep detection on 15m
            sweep = self._detect_sweep(candles_15m, swing_highs, swing_lows)

            # Step 4: reaction after sweep
            sweep["reaction"] = (
                self._analyze_reaction(candles_15m, sweep) if sweep["detected"] else "None"
            )

            # Step 5: signal + bias
            signal, bias = self._generate_signal(sweep, price, nearest_bsl, nearest_ssl)

            return {
                "pair":         pair,
                "price":        price,
                "swing_highs":  [round(s["price"], 4) for s in swing_highs[-5:]],
                "swing_lows":   [round(s["price"], 4) for s in swing_lows[-5:]],
                "bsl_zones":    sorted(bsl_zones),
                "ssl_zones":    sorted(ssl_zones),
                "nearest_bsl":  nearest_bsl,
                "nearest_ssl":  nearest_ssl,
                "sweep":        sweep,
                "bias":         bias,
                "signal":       signal,
                "ok":           True,
            }

        except Exception as e:
            log.error("Engine2 [%s] error: %s", pair, e)
            return {"pair": pair, "ok": False, "error": str(e)}

    # ── Candle parser ────────────────────────────────────────────────
    @staticmethod
    def _parse(klines: list) -> list:
        return [
            {
                "open":   float(k[1]),
                "high":   float(k[2]),
                "low":    float(k[3]),
                "close":  float(k[4]),
                "volume": float(k[5]),
            }
            for k in klines
        ]

    # ── Swing high / low detection ───────────────────────────────────
    def _find_swings(self, candles: list, n: int):
        highs, lows = [], []
        for i in range(n, len(candles) - n):
            window = range(i - n, i + n + 1)
            h = candles[i]["high"]
            l = candles[i]["low"]
            if all(h >= candles[j]["high"] for j in window if j != i):
                highs.append({"index": i, "price": h})
            if all(l <= candles[j]["low"]  for j in window if j != i):
                lows.append( {"index": i, "price": l})
        return highs, lows

    # ── Equal-level clustering (buy-side / sell-side liquidity) ──────
    @staticmethod
    def _cluster_levels(prices: list, tol: float) -> list:
        if not prices:
            return []
        used     = set()
        clusters = []
        for i, p in enumerate(prices):
            if i in used:
                continue
            group = [p]
            for j, q in enumerate(prices):
                if j != i and j not in used and abs(p - q) / p <= tol:
                    group.append(q)
                    used.add(j)
            used.add(i)
            if len(group) >= 2:  # must be touched at least twice
                clusters.append(round(sum(group) / len(group), 4))
        return clusters

    # ── Sweep detection ──────────────────────────────────────────────
    def _detect_sweep(self, candles_15m: list, swing_highs: list, swing_lows: list) -> dict:
        none_result = {"detected": False, "direction": None, "swept_level": None, "wick_size": None}
        recent = candles_15m[-SWEEP_LOOKBACK:]

        high_levels = [s["price"] for s in swing_highs]
        low_levels  = [s["price"] for s in swing_lows]

        for c in reversed(recent):  # most recent first
            body       = abs(c["close"] - c["open"])
            wick_up    = c["high"]  - max(c["open"], c["close"])
            wick_down  = min(c["open"], c["close"]) - c["low"]

            # Upside sweep: wick pokes above swing high, close back below
            for lvl in high_levels:
                if c["high"] > lvl and c["close"] < lvl and wick_up > body * 0.5:
                    return {
                        "detected":    True,
                        "direction":   "up",
                        "swept_level": round(lvl, 4),
                        "wick_size":   round(wick_up, 4),
                    }

            # Downside sweep: wick pokes below swing low, close back above
            for lvl in low_levels:
                if c["low"] < lvl and c["close"] > lvl and wick_down > body * 0.5:
                    return {
                        "detected":    True,
                        "direction":   "down",
                        "swept_level": round(lvl, 4),
                        "wick_size":   round(wick_down, 4),
                    }

        return none_result

    # ── Post-sweep reaction ──────────────────────────────────────────
    @staticmethod
    def _analyze_reaction(candles_15m: list, sweep: dict) -> str:
        # Use last 5 candles to measure reaction
        sample = candles_15m[-5:]
        if len(sample) < 2:
            return "None"
        first_close = sample[0]["close"]
        last_close  = sample[-1]["close"]

        if sweep["direction"] == "up":
            # After sweeping highs, expect price to drop
            return "Reversal" if last_close < first_close else "Continuation"
        else:
            # After sweeping lows, expect price to rise
            return "Reversal" if last_close > first_close else "Continuation"

    # ── Signal / bias ────────────────────────────────────────────────
    @staticmethod
    def _generate_signal(sweep: dict, price: float, bsl, ssl) -> tuple:
        # Sweep + reversal = highest conviction
        if sweep["detected"] and sweep["reaction"] == "Reversal":
            if sweep["direction"] == "up":
                return "SELL", "Bearish"   # grabbed buy-side stops, now drops
            else:
                return "BUY", "Bullish"    # grabbed sell-side stops, now rises

        # Sweep + continuation = momentum signal
        if sweep["detected"] and sweep["reaction"] == "Continuation":
            if sweep["direction"] == "up":
                return "BUY", "Bullish"
            else:
                return "SELL", "Bearish"

        # No sweep: proximity to nearest liquidity pool
        if bsl and ssl:
            dist_up   = (bsl - price) / price
            dist_down = (price - ssl) / price
            if dist_down < dist_up * 0.4:
                return "SELL", "Bearish"   # price very close to SSL, likely sweeps it
            if dist_up < dist_down * 0.4:
                return "BUY", "Bullish"    # price very close to BSL, likely sweeps it

        return "NEUTRAL", "Neutral"
