"""
Engine 1 — Market Data Engine
Collects price/candle data, derivatives positioning, sentiment, and session context.
Returns a structured market snapshot. Does NOT generate trade signals.
"""
import logging
import time as _time
import requests
from datetime import datetime, timezone

log = logging.getLogger(__name__)

BINANCE_FUTURES = "https://fapi.binance.com"
FEAR_GREED_URL  = "https://api.alternative.me/fng/?limit=1"

SESSION_BEHAVIOR = {
    "Asia":      "Consolidation / manipulation / ranging",
    "London":    "Liquidity sweep / fake moves",
    "New York":  "Expansion / trend continuation",
    "Off-Hours": "Low liquidity / off-hours",
}


class Engine1:
    # Shared caches across all instances/pairs
    _fng_cache:   dict = {"data": None, "ts": 0.0}
    _deriv_cache: dict = {}          # keyed by pair
    FNG_TTL   = 600   # Fear & Greed valid 10 min (same for all pairs)
    DERIV_TTL = 180   # Funding/OI/L-S valid 3 min per pair

    def __init__(self, cfg, binance_client):
        self.cfg    = cfg
        self.client = binance_client

    # Timeframe pairs: selected TF → (primary candles, confirmation candles)
    TF_MAP = {
        "1m":  ("1m",  "5m"),
        "5m":  ("5m",  "15m"),
        "15m": ("15m", "30m"),
        "30m": ("30m", "1h"),
        "1h":  ("1h",  "4h"),
        "4h":  ("4h",  "1d"),
    }

    # ── Public entry point ───────────────────────────────────────────
    def run(self, pair: str, timeframe: str = "30m") -> dict:
        tf_primary, tf_confirm = self.TF_MAP.get(timeframe, ("30m", "1h"))
        try:
            price_layer = self._fetch_price_data(pair, tf_primary, tf_confirm)
            deriv_layer = self._fetch_derivatives(pair)
            sent_layer  = self._fetch_sentiment()
            sess_layer  = self._get_session()
            classif     = self._classify(price_layer, deriv_layer, sent_layer, sess_layer)

            return {
                "pair":           pair,
                "timeframe":      timeframe,
                "tf_primary":     tf_primary,
                "tf_confirm":     tf_confirm,
                "price_data":     price_layer,
                "derivatives":    deriv_layer,
                "sentiment":      sent_layer,
                "session":        sess_layer,
                "classification": classif,
                "timestamp":      datetime.now(timezone.utc).isoformat(),
                "ok":             True,
            }
        except Exception as e:
            log.error("Engine1 [%s/%s] error: %s", pair, timeframe, e)
            return {"pair": pair, "ok": False, "error": str(e)}

    # ── Layer 1: Price & candles ─────────────────────────────────────
    def _fetch_price_data(self, pair: str, tf_primary: str, tf_confirm: str) -> dict:
        klines_p = self.client.get_klines(symbol=pair, interval=tf_primary, limit=55)
        klines_c = self.client.get_klines(symbol=pair, interval=tf_confirm, limit=55)

        closes = [float(k[4]) for k in klines_p]
        price  = closes[-1]

        # Volume condition: last 3 vs 20-period avg
        vols      = [float(k[5]) for k in klines_p]
        avg_vol   = sum(vols[-21:-1]) / 20
        recent_vol = sum(vols[-3:]) / 3
        vol_condition = "Expanding" if recent_vol > avg_vol * 1.1 else "Contracting"

        # ATR (14) on primary TF
        atr = self._calc_atr(klines_p, 14)

        # EMA trend on primary TF
        ema20 = self._ema(closes, 20)
        ema50 = self._ema(closes, 50)

        if price > ema20 and ema20 > ema50:
            trend = "Bullish"
        elif price < ema20 and ema20 < ema50:
            trend = "Bearish"
        else:
            trend = "Range"

        # Volatility via ATR % of price
        atr_pct = (atr / price) * 100 if price else 0
        if atr_pct < 0.3:
            volatility = "Low"
        elif atr_pct < 0.8:
            volatility = "Medium"
        else:
            volatility = "High"

        # Recent swing high/low (last 20 candles on primary TF)
        highs = [float(k[2]) for k in klines_p[-20:]]
        lows  = [float(k[3]) for k in klines_p[-20:]]

        # 24h change: compare current close to oldest candle open in confirm TF
        open_ref   = float(klines_c[0][1])
        change_24h = round(((price - open_ref) / open_ref) * 100, 2) if open_ref else 0.0

        return {
            "price":            round(price, 4),
            "trend":            trend,
            "volatility":       volatility,
            "volume_condition": vol_condition,
            "atr":              round(atr, 4),
            "ema20":            round(ema20, 4),
            "ema50":            round(ema50, 4),
            "high_20bar":       round(max(highs), 4),
            "low_20bar":        round(min(lows), 4),
            "change_24h":       change_24h,
            "volume_24h":       round(sum(vols), 2),
        }

    # ── Layer 2: Derivatives (TTL cached per pair) ───────────────────
    def _fetch_derivatives(self, pair: str) -> dict:
        cached = Engine1._deriv_cache.get(pair)
        if cached and (_time.monotonic() - cached["ts"]) < self.DERIV_TTL:
            return cached["data"]
        result = {
            "funding_rate":     None,
            "funding_bias":     "N/A",
            "open_interest":    None,
            "oi_trend":         "N/A",
            "long_short_ratio": None,
            "liq_above":        None,
            "liq_below":        None,
        }

        # Funding rate
        try:
            r = requests.get(f"{BINANCE_FUTURES}/fapi/v1/premiumIndex",
                             params={"symbol": pair}, timeout=5)
            if r.ok:
                fr = float(r.json().get("lastFundingRate", 0)) * 100
                result["funding_rate"] = round(fr, 4)
                if fr > 0.01:
                    result["funding_bias"] = "Long-heavy"
                elif fr < -0.01:
                    result["funding_bias"] = "Short-heavy"
                else:
                    result["funding_bias"] = "Neutral"
        except Exception as e:
            log.warning("Funding rate failed [%s]: %s", pair, e)

        # Open interest + trend
        try:
            r = requests.get(f"{BINANCE_FUTURES}/fapi/v1/openInterest",
                             params={"symbol": pair}, timeout=5)
            if r.ok:
                result["open_interest"] = float(r.json().get("openInterest", 0))

            r2 = requests.get(f"{BINANCE_FUTURES}/futures/data/openInterestHist",
                              params={"symbol": pair, "period": "30m", "limit": 6}, timeout=5)
            if r2.ok:
                hist = r2.json()
                if len(hist) >= 2:
                    first_oi = float(hist[0]["sumOpenInterest"])
                    last_oi  = float(hist[-1]["sumOpenInterest"])
                    if last_oi > first_oi * 1.002:
                        result["oi_trend"] = "Rising"
                    elif last_oi < first_oi * 0.998:
                        result["oi_trend"] = "Falling"
                    else:
                        result["oi_trend"] = "Neutral"
        except Exception as e:
            log.warning("Open interest failed [%s]: %s", pair, e)

        # Long/short ratio
        try:
            r = requests.get(f"{BINANCE_FUTURES}/futures/data/globalLongShortAccountRatio",
                             params={"symbol": pair, "period": "30m", "limit": 1}, timeout=5)
            if r.ok and r.json():
                result["long_short_ratio"] = round(float(r.json()[0].get("longShortRatio", 1)), 3)
        except Exception as e:
            log.warning("Long/short ratio failed [%s]: %s", pair, e)

        # Coinglass liquidation zones (optional — only if key configured)
        cg_key = getattr(self.cfg, "COINGLASS_API_KEY", "")
        if cg_key:
            try:
                sym = pair.replace("USDT", "")
                r = requests.get(
                    "https://open-api.coinglass.com/public/v2/liquidation_ex",
                    params={"symbol": sym, "currency": "USD"},
                    headers={"coinglassSecret": cg_key},
                    timeout=5,
                )
                if r.ok:
                    data = r.json().get("data", {})
                    result["liq_above"] = data.get("buyLiquidationLine")
                    result["liq_below"] = data.get("sellLiquidationLine")
            except Exception as e:
                log.warning("Coinglass failed [%s]: %s", pair, e)

        Engine1._deriv_cache[pair] = {"data": result, "ts": _time.monotonic()}
        return result

    # ── Layer 3: Sentiment (TTL cached, same value for all pairs) ────
    def _fetch_sentiment(self) -> dict:
        c = Engine1._fng_cache
        if c["data"] and (_time.monotonic() - c["ts"]) < self.FNG_TTL:
            return c["data"]
        try:
            r = requests.get(FEAR_GREED_URL, timeout=5)
            if r.ok:
                item  = r.json()["data"][0]
                score = int(item["value"])
                if score <= 45:
                    label, appetite = "Fear", "Accumulation zone"
                elif score <= 55:
                    label, appetite = "Neutral", "Wait and watch"
                else:
                    label, appetite = "Greed", "Distribution zone"
                data = {"score": score, "label": label, "risk_appetite": appetite}
                Engine1._fng_cache.update({"data": data, "ts": _time.monotonic()})
                return data
        except Exception as e:
            log.warning("Fear & Greed fetch failed: %s", e)
        fallback = {"score": None, "label": "N/A", "risk_appetite": "N/A"}
        if c["data"]:
            return c["data"]   # serve stale rather than N/A
        return fallback

    # ── Layer 4: Session ─────────────────────────────────────────────
    def _get_session(self) -> dict:
        hour = datetime.now(timezone.utc).hour
        if 13 <= hour < 22:
            active = "New York"
        elif 7 <= hour < 16:
            active = "London"
        elif 0 <= hour < 8:
            active = "Asia"
        else:
            active = "Off-Hours"
        return {
            "active":   active,
            "behavior": SESSION_BEHAVIOR[active],
        }

    # ── Classification ───────────────────────────────────────────────
    def _classify(self, price: dict, deriv: dict, sent: dict, sess: dict) -> str:
        trend     = price.get("trend", "Range")
        vol_cond  = price.get("volume_condition", "Contracting")
        volatility = price.get("volatility", "Low")
        session   = sess.get("active", "")
        score     = sent.get("score")
        oi_trend  = deriv.get("oi_trend", "Neutral")

        # Expansion: strong trend + expanding volume + rising OI
        if trend != "Range" and vol_cond == "Expanding" and oi_trend == "Rising":
            return "Expansion Phase"

        # Distribution: greed + any trend + OI falling (positions closing)
        if score and score > 65 and trend != "Range" and oi_trend == "Falling":
            return "Distribution Phase"

        # Manipulation: London session + ranging market
        if session == "London" and trend == "Range":
            return "Manipulation Phase"

        # Accumulation: fear + low/medium volatility + contracting volume
        if score and score < 35 and vol_cond == "Contracting" and volatility in ("Low", "Medium"):
            return "Accumulation Phase"

        # Fallback heuristics
        if trend == "Bullish" and vol_cond == "Expanding":
            return "Expansion Phase"
        if trend == "Bearish" and oi_trend == "Falling":
            return "Distribution Phase"
        if trend == "Range":
            return "Accumulation Phase"
        return "Accumulation Phase"

    # ── Indicators ───────────────────────────────────────────────────
    @staticmethod
    def _ema(closes: list, period: int) -> float:
        if len(closes) < period:
            return closes[-1] if closes else 0.0
        k   = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for c in closes[period:]:
            ema = c * k + ema * (1 - k)
        return ema

    @staticmethod
    def _calc_atr(klines: list, period: int = 14) -> float:
        trs = []
        for i in range(1, len(klines)):
            high       = float(klines[i][2])
            low        = float(klines[i][3])
            prev_close = float(klines[i - 1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        if not trs:
            return 0.0
        return sum(trs[-period:]) / min(len(trs), period)
