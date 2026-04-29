"""
Engine 3 — Market Structure Engine
Identifies true directional bias via HH/HL/LH/LL mapping, BOS, CHoCH,
and liquidity-structure alignment using Engine 2 sweep data.
Does NOT generate trade entries — only confirms direction, strength, and validity.
"""
import logging

log = logging.getLogger(__name__)

SWING_N = 3   # bars each side to confirm a swing point


class Engine3:
    def __init__(self, cfg, binance_client):
        self.cfg    = cfg
        self.client = binance_client

    # ── Public entry point ───────────────────────────────────────────
    def run(self, pair: str, timeframe: str = "30m", e2_result: dict = None) -> dict:
        try:
            klines  = self.client.get_klines(symbol=pair, interval=timeframe, limit=120)
            candles = self._parse(klines)
            price   = candles[-1]["close"]

            # Step 1: Raw swing points
            swing_highs, swing_lows = self._find_swings(candles, SWING_N)

            if len(swing_highs) < 3 or len(swing_lows) < 3:
                return {"pair": pair, "ok": False, "error": "Not enough swing data yet"}

            # Step 2: Label each swing as HH/LH and HL/LL
            labeled_highs = self._label_highs(swing_highs)
            labeled_lows  = self._label_lows(swing_lows)

            # Step 3: Trend identification from structure labels
            trend = self._identify_trend(labeled_highs, labeled_lows)

            # Step 4: BOS — break in direction of trend (continuation)
            bos = self._detect_bos(price, swing_highs, swing_lows, trend)

            # Step 5: CHoCH — break against trend (potential reversal)
            choch = self._detect_choch(price, labeled_highs, labeled_lows, trend)

            # Step 6: Liquidity + structure alignment (Engine 2 data)
            sweep_before_choch, manipulation = self._check_sweep_alignment(e2_result, choch)

            # Step 7: Market phase
            phase = self._classify_phase(trend, bos, choch, sweep_before_choch)

            # Step 8: Structure strength score
            strength = self._structure_strength(labeled_highs, labeled_lows, trend)

            # Step 9: Final bias + signal
            bias, signal = self._final_bias(trend, bos, choch, manipulation)

            return {
                "pair":               pair,
                "timeframe":          timeframe,
                "price":              price,
                "trend":              trend,
                "labeled_highs":      [h["label"] for h in labeled_highs[-4:]],
                "labeled_lows":       [l["label"] for l in labeled_lows[-4:]],
                "last_swing_high":    swing_highs[-1]["price"],
                "last_swing_low":     swing_lows[-1]["price"],
                "bos":                bos,
                "choch":              choch,
                "sweep_before_choch": sweep_before_choch,
                "manipulation":       manipulation,
                "phase":              phase,
                "strength":           strength,
                "bias":               bias,
                "signal":             signal,
                "ok":                 True,
            }

        except Exception as e:
            log.error("Engine3 [%s] error: %s", pair, e)
            return {"pair": pair, "ok": False, "error": str(e)}

    # ── Candle parser ────────────────────────────────────────────────
    @staticmethod
    def _parse(klines: list) -> list:
        return [
            {
                "open":  float(k[1]),
                "high":  float(k[2]),
                "low":   float(k[3]),
                "close": float(k[4]),
            }
            for k in klines
        ]

    # ── Swing point detection ────────────────────────────────────────
    @staticmethod
    def _find_swings(candles: list, n: int):
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

    # ── Label swing highs as HH or LH ───────────────────────────────
    @staticmethod
    def _label_highs(swing_highs: list) -> list:
        labeled = []
        for i in range(1, len(swing_highs)):
            label = "HH" if swing_highs[i]["price"] > swing_highs[i - 1]["price"] else "LH"
            labeled.append({"price": swing_highs[i]["price"], "label": label})
        return labeled

    # ── Label swing lows as HL or LL ────────────────────────────────
    @staticmethod
    def _label_lows(swing_lows: list) -> list:
        labeled = []
        for i in range(1, len(swing_lows)):
            label = "HL" if swing_lows[i]["price"] > swing_lows[i - 1]["price"] else "LL"
            labeled.append({"price": swing_lows[i]["price"], "label": label})
        return labeled

    # ── Trend identification ─────────────────────────────────────────
    @staticmethod
    def _identify_trend(labeled_highs: list, labeled_lows: list) -> str:
        recent_h = [h["label"] for h in labeled_highs[-3:]]
        recent_l = [l["label"] for l in labeled_lows[-3:]]

        hh_count = recent_h.count("HH")
        lh_count = recent_h.count("LH")
        hl_count = recent_l.count("HL")
        ll_count = recent_l.count("LL")

        bullish_score = hh_count + hl_count
        bearish_score = lh_count + ll_count

        if bullish_score > bearish_score and bullish_score >= 3:
            return "Bullish"
        if bearish_score > bullish_score and bearish_score >= 3:
            return "Bearish"
        if bullish_score > bearish_score:
            return "Bullish"
        if bearish_score > bullish_score:
            return "Bearish"
        return "Range"

    # ── BOS: break in direction of current trend (continuation) ─────
    @staticmethod
    def _detect_bos(price: float, swing_highs: list, swing_lows: list, trend: str) -> dict:
        none = {"confirmed": False, "direction": None, "level": None}
        if not swing_highs or not swing_lows:
            return none

        prev_high = swing_highs[-2]["price"] if len(swing_highs) >= 2 else swing_highs[-1]["price"]
        prev_low  = swing_lows[-2]["price"]  if len(swing_lows)  >= 2 else swing_lows[-1]["price"]

        if trend == "Bullish" and price > prev_high:
            return {"confirmed": True, "direction": "Bullish", "level": round(prev_high, 4)}
        if trend == "Bearish" and price < prev_low:
            return {"confirmed": True, "direction": "Bearish", "level": round(prev_low, 4)}
        return none

    # ── CHoCH: break against trend direction (reversal signal) ──────
    @staticmethod
    def _detect_choch(price: float, labeled_highs: list, labeled_lows: list, trend: str) -> dict:
        none = {"detected": False, "direction": None, "level": None}

        if trend == "Bullish":
            # In a bullish market, breaking the last HL = CHoCH to bearish
            hls = [l for l in labeled_lows if l["label"] == "HL"]
            if hls and price < hls[-1]["price"]:
                return {"detected": True, "direction": "Bearish", "level": round(hls[-1]["price"], 4)}

        elif trend == "Bearish":
            # In a bearish market, breaking the last LH = CHoCH to bullish
            lhs = [h for h in labeled_highs if h["label"] == "LH"]
            if lhs and price > lhs[-1]["price"]:
                return {"detected": True, "direction": "Bullish", "level": round(lhs[-1]["price"], 4)}

        return none

    # ── Liquidity + structure alignment ─────────────────────────────
    @staticmethod
    def _check_sweep_alignment(e2_result: dict, choch: dict) -> tuple:
        if not e2_result or not e2_result.get("ok"):
            return False, False
        sweep = e2_result.get("sweep", {})
        if sweep.get("detected") and choch.get("detected"):
            # Sweep happened and structure shifted → classic smart money move
            return True, True
        return False, False

    # ── Market phase ─────────────────────────────────────────────────
    @staticmethod
    def _classify_phase(trend: str, bos: dict, choch: dict, sweep_before_choch: bool) -> str:
        # Manipulation = sweep + CHoCH (smart money trap)
        if sweep_before_choch and choch["detected"]:
            return "Manipulation"
        # Expansion = BOS confirmed (trend continuing with force)
        if bos["confirmed"]:
            return "Expansion"
        # Distribution = CHoCH without sweep (natural structure reversal)
        if choch["detected"]:
            return "Distribution"
        # Accumulation = ranging, no BOS or CHoCH
        if trend == "Range":
            return "Accumulation"
        # Trending but no new BOS = trend weakening
        return "Distribution"

    # ── Structure strength ───────────────────────────────────────────
    @staticmethod
    def _structure_strength(labeled_highs: list, labeled_lows: list, trend: str) -> str:
        sample_h = labeled_highs[-4:]
        sample_l = labeled_lows[-4:]
        total    = len(sample_h) + len(sample_l)
        if total == 0:
            return "Choppy"

        if trend == "Bullish":
            aligned = sum(1 for h in sample_h if h["label"] == "HH") + \
                      sum(1 for l in sample_l if l["label"] == "HL")
        elif trend == "Bearish":
            aligned = sum(1 for h in sample_h if h["label"] == "LH") + \
                      sum(1 for l in sample_l if l["label"] == "LL")
        else:
            return "Choppy"

        ratio = aligned / total
        if ratio >= 0.75:
            return "Strong"
        if ratio >= 0.5:
            return "Weak"
        return "Choppy"

    # ── Final bias + signal ──────────────────────────────────────────
    @staticmethod
    def _final_bias(trend: str, bos: dict, choch: dict, manipulation: bool) -> tuple:
        # CHoCH = reversal forming — highest priority
        if choch["detected"]:
            if choch["direction"] == "Bullish":
                return "Bullish Reversal", "BUY"
            else:
                return "Bearish Reversal", "SELL"

        # Manipulation (sweep → CHoCH) already handled above via choch priority
        # BOS = trend continuation confirmed
        if bos["confirmed"]:
            if bos["direction"] == "Bullish":
                return "Bullish Continuation", "BUY"
            else:
                return "Bearish Continuation", "SELL"

        # Trend exists but no BOS yet — wait for confirmation
        if trend == "Bullish":
            return "Bullish — Await BOS", "NEUTRAL"
        if trend == "Bearish":
            return "Bearish — Await BOS", "NEUTRAL"

        return "No Trade Zone", "NEUTRAL"
