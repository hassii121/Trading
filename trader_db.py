import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")

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
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_conn()
    cur  = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            email         TEXT UNIQUE NOT NULL,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT DEFAULT 'user',
            is_active     INTEGER DEFAULT 1,
            created_at    TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id             SERIAL PRIMARY KEY,
            user_id        INTEGER NOT NULL,
            plan_name      TEXT,
            amount_usd     FLOAT,
            duration_days  INTEGER,
            payment_id     TEXT,
            pay_address    TEXT,
            pay_amount     FLOAT,
            pay_currency   TEXT,
            payment_status TEXT DEFAULT 'pending',
            started_at     TIMESTAMP,
            expires_at     TIMESTAMP,
            created_at     TIMESTAMP DEFAULT NOW()
        )
    """)

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
            entry_price    FLOAT,
            sl             FLOAT,
            tp1            FLOAT,
            tp2            FLOAT,
            tp3            FLOAT,
            qty            FLOAT,
            notional       FLOAT,
            entry_order_id TEXT,
            sl_order_id    TEXT,
            tp1_order_id   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id             SERIAL PRIMARY KEY,
            pair           TEXT,
            direction      TEXT,
            entry_price    FLOAT,
            close_price    FLOAT,
            sl             FLOAT,
            tp1            FLOAT,
            qty            FLOAT,
            notional       FLOAT,
            pnl            FLOAT,
            close_reason   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP,
            closed_at      TIMESTAMP DEFAULT NOW()
        )
    """)

    conn.commit()
    cur.close()
    conn.close()


def get_user_count():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT COUNT(*) as c FROM users")
    row  = cur.fetchone()
    cur.close()
    conn.close()
    return row["c"]


def create_user(email, username, password_hash, role="user"):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, username, password_hash, role) VALUES (%s,%s,%s,%s)",
        (email.lower().strip(), username.strip(), password_hash, role)
    )
    conn.commit()
    cur.close()
    conn.close()


def get_user_by_email(email):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE email=%s", (email.lower().strip(),))
    row  = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users WHERE id=%s", (user_id,))
    row  = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_all_users():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def set_user_active(user_id, is_active):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("UPDATE users SET is_active=%s WHERE id=%s", (1 if is_active else 0, user_id))
    conn.commit()
    cur.close()
    conn.close()


def delete_user(user_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    conn.commit()
    cur.close()
    conn.close()


def create_subscription(user_id, plan_name, amount_usd, duration_days,
                        payment_id, pay_address, pay_amount, pay_currency):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO subscriptions
        (user_id, plan_name, amount_usd, duration_days,
         payment_id, pay_address, pay_amount, pay_currency)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        RETURNING id
    """, (user_id, plan_name, amount_usd, duration_days,
          payment_id, pay_address, pay_amount, pay_currency))
    sub_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return sub_id


def get_subscription(sub_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT * FROM subscriptions WHERE id=%s", (sub_id,))
    row  = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def activate_subscription(payment_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT duration_days FROM subscriptions WHERE payment_id=%s", (payment_id,))
    sub = cur.fetchone()
    if sub:
        cur.execute("""
            UPDATE subscriptions SET
                payment_status='confirmed',
                started_at=NOW(),
                expires_at=NOW() + (duration_days || ' days')::INTERVAL
            WHERE payment_id=%s
        """, (payment_id,))
    conn.commit()
    cur.close()
    conn.close()


def has_active_subscription(user_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT id FROM subscriptions
        WHERE user_id=%s AND payment_status='confirmed'
        AND expires_at > NOW()
        ORDER BY expires_at DESC LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None


def get_user_subscription(user_id):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT * FROM subscriptions WHERE user_id=%s
        ORDER BY created_at DESC LIMIT 1
    """, (user_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def get_all_subscriptions():
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("""
        SELECT s.*, u.username, u.email FROM subscriptions s
        JOIN users u ON s.user_id=u.id
        ORDER BY s.created_at DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_setting(key, default=""):
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT value FROM trading_settings WHERE key=%s", (key,))
    row  = cur.fetchone()
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
    row  = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def close_trade(trade_id, close_price, pnl, close_reason):
    conn  = get_conn()
    cur   = conn.cursor()
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
