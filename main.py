"""
main.py — HASSII Institute entry point.
Starts Flask + SocketIO server. Open http://localhost:5050 in browser.
"""
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import threading, logging, time
from concurrent.futures import ThreadPoolExecutor

from binance.client import Client

from config import cfg
from engines.engine1 import Engine1
from engines.engine2 import Engine2
from engines.engine3 import Engine3
from engines.engine4 import Engine4
from engines.engine5 import Engine5
from engines.trader import AutoTrader
import trader_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


app      = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Shared Binance client (public market data — no auth required for candles)
try:
    binance_client = Client(cfg.API_KEY, cfg.API_SECRET)
except Exception as e:
    log.warning("Binance client init failed (geo-restriction?): %s", e)
    binance_client = Client(cfg.API_KEY, cfg.API_SECRET, requests_params={"timeout": 30})

# Engine instances
engine1 = Engine1(cfg, binance_client)
engine2 = Engine2(cfg, binance_client)
engine3 = Engine3(cfg, binance_client)
engine4 = Engine4(cfg)
engine5 = Engine5(cfg)

# Auto-trader
auto_trader = AutoTrader(socketio)

# Cache: last known result per pair — replayed to newly connected clients
_latest: dict = {}

# Currently selected timeframe (global — same for all pairs)
_current_tf: str = "30m"



# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("dashboard.html", pairs=cfg.PAIRS)


# ── Replay cache to new clients ───────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    for payload in _latest.values():
        emit("signal", payload)


# ── Timeframe change (client → server) ───────────────────────────────────────

@socketio.on("set_timeframe")
def on_set_timeframe(data):
    global _current_tf
    tf = data.get("tf", "30m")
    if tf not in ("1m", "5m", "15m", "30m", "1h", "4h"):
        return
    _current_tf = tf
    log.info("Timeframe switched to %s — reanalysing all pairs", tf)
    # Re-run engines immediately for all pairs with the new TF
    socketio.start_background_task(_run_all, tf)


# ── Per-pair worker ───────────────────────────────────────────────────────────

def _analyse_pair(pair: str, tf: str):
    try:
        e1 = engine1.run(pair, timeframe=tf)
        e2 = engine2.run(pair, timeframe=tf)
        e3 = engine3.run(pair, timeframe=tf, e2_result=e2)
        e4 = engine4.run(pair, e1, e2, e3)
        e5 = engine5.run(pair, e1, e2, e3, e4)
        payload = _emit_payload(pair, e1, e2, e3, e4, e5)
        auto_trader.execute_signal(pair, payload)
    except Exception as e:
        log.error("_analyse_pair error [%s]: %s", pair, e)


# ── Background analysis loop (parallel) ──────────────────────────────────────

def _run_all(tf: str = None):
    tf = tf or _current_tf
    with ThreadPoolExecutor(max_workers=cfg.PARALLEL_PAIRS) as pool:
        list(pool.map(lambda pair: _analyse_pair(pair, tf), cfg.PAIRS))


def analysis_loop():
    """Runs all engines on all pairs in parallel, then sleeps REFRESH_SECONDS."""
    while True:
        _run_all(_current_tf)
        auto_trader.monitor_trades()
        time.sleep(cfg.REFRESH_SECONDS)




# ── Emit helper ──────────────────────────────────────────────────────────────

def _emit_payload(pair: str, e1: dict, e2: dict, e3: dict, e4: dict, e5: dict):
    final_dec = e5.get("final_decision") or e4.get("decision", "NO_TRADE")
    confidence = e5.get("confidence", 0)
    payload = {
        "pair":      pair,
        "timeframe": e1.get("timeframe", _current_tf),
        "signal": {
            "bias":       final_dec,
            "entry_low":  e4.get("entry_low"),
            "entry_high": e4.get("entry_high"),
            "stop_loss":  e4.get("stop_loss"),
            "tp1":        e4.get("tp1"),
            "tp2":        e4.get("tp2"),
            "tp3":        e4.get("tp3"),
            "confidence": confidence,
            "reason":     e5.get("summary") or e4.get("reason", "—"),
        },
        "engines": {
            "engine1": _flatten_e1(e1),
            "engine2": _flatten_e2(e2),
            "engine3": _flatten_e3(e3),
            "engine4": _flatten_e4(e4),
            "engine5": _flatten_e5(e5),
        },
    }
    _latest[pair] = payload
    socketio.emit("signal", payload)
    log.info("[%s/%s] E4→%s  E5→%d/100 (%s) risk:%.2f%%",
             pair, e1.get("timeframe", "?"),
             final_dec, confidence,
             e5.get("label", "?"), e5.get("risk_pct", 0))
    return payload


# ── Flatten helpers ───────────────────────────────────────────────────────────

def _flatten_e1(e1: dict) -> dict:
    if not e1.get("ok"):
        return {}
    pd = e1.get("price_data", {})
    dv = e1.get("derivatives", {})
    sn = e1.get("sentiment", {})
    ss = e1.get("session", {})
    return {
        "price":            pd.get("price"),
        "trend":            pd.get("trend"),
        "volatility":       pd.get("volatility"),
        "volume_condition": pd.get("volume_condition"),
        "atr":              pd.get("atr"),
        "change_24h":       pd.get("change_24h"),
        "volume_24h":       pd.get("volume_24h"),
        "high_20bar":       pd.get("high_20bar"),
        "low_20bar":        pd.get("low_20bar"),
        "funding_rate":     dv.get("funding_rate"),
        "funding_bias":     dv.get("funding_bias"),
        "oi_trend":         dv.get("oi_trend"),
        "open_interest":    dv.get("open_interest"),
        "long_short_ratio": dv.get("long_short_ratio"),
        "liq_above":        dv.get("liq_above"),
        "liq_below":        dv.get("liq_below"),
        "sentiment_score":  sn.get("score"),
        "sentiment_label":  sn.get("label"),
        "risk_appetite":    sn.get("risk_appetite"),
        "session":          ss.get("active"),
        "session_behavior": ss.get("behavior"),
        "classification":   e1.get("classification"),
        "timestamp":        e1.get("timestamp"),
    }


def _flatten_e5(e5: dict) -> dict:
    if not e5.get("ok"):
        return {}
    sb = e5.get("score_breakdown", {})
    return {
        "confidence":    e5.get("confidence"),
        "label":         e5.get("label"),
        "risk_pct":      e5.get("risk_pct"),
        "final_decision": e5.get("final_decision"),
        "sl_valid":      e5.get("sl_valid"),
        "sl_note":       e5.get("sl_note"),
        "summary":       e5.get("summary"),
        "score_session":     sb.get("session", 0),
        "score_liquidity":   sb.get("liquidity", 0),
        "score_structure":   sb.get("structure", 0),
        "score_funding":     sb.get("funding", 0),
        "score_sentiment":   sb.get("sentiment", 0),
        "score_volatility":  sb.get("volatility", 0),
        "score_manipulation": sb.get("manipulation", 0),
        "score_rr":          sb.get("rr_bonus", 0),
        "management":    e5.get("management", {}),
    }


def _flatten_e4(e4: dict) -> dict:
    if not e4.get("ok"):
        return {}
    return {
        "decision":    e4.get("decision"),
        "bias":        e4.get("bias"),
        "setup":       e4.get("setup"),
        "entry_low":   e4.get("entry_low"),
        "entry_high":  e4.get("entry_high"),
        "stop_loss":   e4.get("stop_loss"),
        "tp1":         e4.get("tp1"),
        "tp2":         e4.get("tp2"),
        "tp3":         e4.get("tp3"),
        "risk_reward": e4.get("risk_reward"),
        "prob_score":  e4.get("prob_score"),
        "reason":      e4.get("reason"),
    }


def _flatten_e3(e3: dict) -> dict:
    if not e3.get("ok"):
        return {}
    bos   = e3.get("bos",   {})
    choch = e3.get("choch", {})
    return {
        "trend":               e3.get("trend"),
        "labeled_highs":       e3.get("labeled_highs", []),
        "labeled_lows":        e3.get("labeled_lows",  []),
        "last_swing_high":     e3.get("last_swing_high"),
        "last_swing_low":      e3.get("last_swing_low"),
        "bos_confirmed":       bos.get("confirmed", False),
        "bos_direction":       bos.get("direction"),
        "bos_level":           bos.get("level"),
        "choch_detected":      choch.get("detected", False),
        "choch_direction":     choch.get("direction"),
        "choch_level":         choch.get("level"),
        "sweep_before_choch":  e3.get("sweep_before_choch", False),
        "manipulation":        e3.get("manipulation", False),
        "phase":               e3.get("phase"),
        "strength":            e3.get("strength"),
        "bias":                e3.get("bias"),
        "signal":              e3.get("signal"),
    }


def _flatten_e2(e2: dict) -> dict:
    if not e2.get("ok"):
        return {}
    sw = e2.get("sweep", {})
    return {
        "nearest_bsl":  e2.get("nearest_bsl"),
        "nearest_ssl":  e2.get("nearest_ssl"),
        "bsl_zones":    e2.get("bsl_zones", []),
        "ssl_zones":    e2.get("ssl_zones", []),
        "swing_highs":  e2.get("swing_highs", []),
        "swing_lows":   e2.get("swing_lows", []),
        "sweep":        sw.get("detected", False),
        "sweep_dir":    sw.get("direction"),
        "swept_level":  sw.get("swept_level"),
        "wick_size":    sw.get("wick_size"),
        "reaction":     sw.get("reaction", "None"),
        "bias":         e2.get("bias"),
        "signal":       e2.get("signal"),
    }


# ── Trading Settings ──────────────────────────────────────────────────────────

@app.route("/api/trading/settings", methods=["GET"])
def api_get_trading_settings():
    return jsonify({
        "enabled":           trader_db.get_setting("enabled",        "0") == "1",
        "testnet":           trader_db.get_setting("testnet",        "0") == "1",
        "api_key":           "****" if trader_db.get_setting("api_key")        else "",
        "api_secret":        "****" if trader_db.get_setting("api_secret")     else "",
        "tn_api_key":        "****" if trader_db.get_setting("tn_api_key")     else "",
        "tn_api_secret":     "****" if trader_db.get_setting("tn_api_secret")  else "",
        "min_confidence":    int(trader_db.get_setting("min_confidence", "75")),
        "max_trades":        int(trader_db.get_setting("max_trades",     "6")),
        "leverage":          int(trader_db.get_setting("leverage",       "10")),
        "risk_pct":          float(trader_db.get_setting("risk_pct",     "0.5")),
    })

@app.route("/api/trading/settings", methods=["POST"])
def api_save_trading_settings():
    data = request.json
    if data.get("api_key")        and "****" not in data["api_key"]:
        trader_db.set_setting("api_key",        data["api_key"])
    if data.get("api_secret")     and "****" not in data["api_secret"]:
        trader_db.set_setting("api_secret",     data["api_secret"])
    if data.get("tn_api_key")     and "****" not in data["tn_api_key"]:
        trader_db.set_setting("tn_api_key",     data["tn_api_key"])
    if data.get("tn_api_secret")  and "****" not in data["tn_api_secret"]:
        trader_db.set_setting("tn_api_secret",  data["tn_api_secret"])
    trader_db.set_setting("enabled",        "1" if data.get("enabled") else "0")
    trader_db.set_setting("testnet",        "1" if data.get("testnet") else "0")
    trader_db.set_setting("min_confidence", str(int(data.get("min_confidence", 75))))
    trader_db.set_setting("max_trades",     str(int(data.get("max_trades",     6))))
    trader_db.set_setting("leverage",       str(int(data.get("leverage",       10))))
    trader_db.set_setting("risk_pct",       str(float(data.get("risk_pct",     0.5))))
    return jsonify({"ok": True})


# ── Trading Account ───────────────────────────────────────────────────────────

@app.route("/api/trading/account")
def api_trading_account():
    return jsonify(auto_trader.get_account_info())

@app.route("/api/trading/open")
def api_open_trades():
    return jsonify(trader_db.get_open_trades())

@app.route("/api/trading/history")
def api_trading_history():
    return jsonify(trader_db.get_closed_trades())


# ── Start ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log.info("=" * 55)
    log.info("  HASSII Institute — Market Analysis System")
    log.info("  Pairs   : %s", ", ".join(cfg.PAIRS))
    log.info("  Refresh : every %ds", cfg.REFRESH_SECONDS)
    log.info("  Open    : http://%s:%d", cfg.HOST, cfg.PORT)
    log.info("=" * 55)

    t = threading.Thread(target=analysis_loop, daemon=True)
    t.start()


    socketio.run(app, host=cfg.HOST, port=cfg.PORT, debug=False, allow_unsafe_werkzeug=True)
