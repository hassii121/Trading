import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Environment variable fallbacks — survive Railway redeploys
_ENV_FALLBACKS = {
    "api_key":        "TRADE_API_KEY",
    "api_secret":     "TRADE_API_SECRET",
    "tn_api_key":     "TRADE_TN_API_KEY",
    "tn_api_secret":  "TRADE_TN_API_SECRET",
    "enabled":        "TRADE_ENABLED",
    "testnet":        "TRADE_TESTNET",
    "min_confidence": "TRADE_MIN_CONFIDENCE",
    "max_trades":     "TRADE_MAX_TRADES",
    "leverage":       "TRADE_LEVERAGE",
    "risk_pct":       "TRADE_RISK_PCT",
    "trade_tp_usd":   "TRADE_TP_USD",
    "basket_tp_usd":  "TRADE_BASKET_TP_USD",
}


def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trading_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS open_trades (
            id             SERIAL PRIMARY KEY,
            pair           TEXT,
            direction      TEXT,
            entry_price    DOUBLE PRECISION,
            sl             DOUBLE PRECISION,
            tp1            DOUBLE PRECISION,
            tp2            DOUBLE PRECISION,
            tp3            DOUBLE PRECISION,
            qty            DOUBLE PRECISION,
            notional       DOUBLE PRECISION,
            entry_order_id TEXT,
            sl_order_id    TEXT,
            tp1_order_id   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id             SERIAL PRIMARY KEY,
            pair           TEXT,
            direction      TEXT,
            entry_price    DOUBLE PRECISION,
            close_price    DOUBLE PRECISION,
            sl             DOUBLE PRECISION,
            tp1            DOUBLE PRECISION,
            qty            DOUBLE PRECISION,
            notional       DOUBLE PRECISION,
            pnl            DOUBLE PRECISION,
            close_reason   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP,
            closed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def get_setting(key, default=""):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT value FROM trading_settings WHERE key=%s", (key,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    if row and row["value"]:
        return row["value"]
    env_var = _ENV_FALLBACKS.get(key)
    if env_var:
        return os.environ.get(env_var, default)
    return default


def set_setting(key, value):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO trading_settings (key, value) VALUES (%s, %s)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
    """, (key, value))
    conn.commit()
    cur.close()
    conn.close()


def add_open_trade(data):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO open_trades
            (pair, direction, entry_price, sl, tp1, tp2, tp3,
             qty, notional, entry_order_id, sl_order_id, tp1_order_id,
             confidence, timeframe)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (data['pair'], data['direction'], data['entry_price'],
          data['sl'], data['tp1'], data.get('tp2'), data.get('tp3'),
          data['qty'], data['notional'],
          data.get('entry_order_id'), data.get('sl_order_id'), data.get('tp1_order_id'),
          data.get('confidence'), data.get('timeframe')))
    trade_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return trade_id


def get_open_trades():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM open_trades ORDER BY opened_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_open_trade_by_pair(pair):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM open_trades WHERE pair=%s", (pair,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def close_trade(trade_id, close_price, pnl, close_reason):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM open_trades WHERE id=%s", (trade_id,))
    trade = cur.fetchone()
    if trade:
        cur.execute("""
            INSERT INTO closed_trades
                (pair, direction, entry_price, close_price, sl, tp1,
                 qty, notional, pnl, close_reason, confidence, timeframe, opened_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (trade['pair'], trade['direction'], trade['entry_price'], close_price,
              trade['sl'], trade['tp1'], trade['qty'], trade['notional'],
              pnl, close_reason, trade['confidence'], trade['timeframe'], trade['opened_at']))
        cur.execute("DELETE FROM open_trades WHERE id=%s", (trade_id,))
    conn.commit()
    cur.close()
    conn.close()


def get_closed_trades(limit=100):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM closed_trades ORDER BY closed_at DESC LIMIT %s", (limit,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def add_closed_trade_direct(data):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO closed_trades
            (pair, direction, entry_price, close_price, sl, tp1,
             qty, notional, pnl, close_reason, confidence, timeframe, opened_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (data['pair'], data['direction'], data['entry_price'], data['close_price'],
          data.get('sl'), data.get('tp1'), data['qty'], data.get('notional'),
          data['pnl'], data['close_reason'], data.get('confidence'), data.get('timeframe'),
          data.get('opened_at')))
    conn.commit()
    cur.close()
    conn.close()
