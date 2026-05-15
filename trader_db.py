import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(__file__), "trader.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trading_settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS open_trades (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            pair           TEXT,
            direction      TEXT,
            entry_price    REAL,
            sl             REAL,
            tp1            REAL,
            tp2            REAL,
            tp3            REAL,
            qty            REAL,
            notional       REAL,
            entry_order_id TEXT,
            sl_order_id    TEXT,
            tp1_order_id   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS closed_trades (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            pair           TEXT,
            direction      TEXT,
            entry_price    REAL,
            close_price    REAL,
            sl             REAL,
            tp1            REAL,
            qty            REAL,
            notional       REAL,
            pnl            REAL,
            close_reason   TEXT,
            confidence     INTEGER,
            timeframe      TEXT,
            opened_at      TIMESTAMP,
            closed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def get_setting(key, default=""):
    conn = get_conn()
    row  = conn.execute("SELECT value FROM trading_settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_conn()
    conn.execute("INSERT OR REPLACE INTO trading_settings (key, value) VALUES (?,?)", (key, value))
    conn.commit()
    conn.close()

def add_open_trade(data):
    conn = get_conn()
    c    = conn.cursor()
    c.execute("""INSERT INTO open_trades
                 (pair, direction, entry_price, sl, tp1, tp2, tp3,
                  qty, notional, entry_order_id, sl_order_id, tp1_order_id,
                  confidence, timeframe)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (data['pair'], data['direction'], data['entry_price'],
               data['sl'], data['tp1'], data.get('tp2'), data.get('tp3'),
               data['qty'], data['notional'],
               data.get('entry_order_id'), data.get('sl_order_id'), data.get('tp1_order_id'),
               data.get('confidence'), data.get('timeframe')))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def get_open_trades():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM open_trades ORDER BY opened_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_open_trade_by_pair(pair):
    conn = get_conn()
    row  = conn.execute("SELECT * FROM open_trades WHERE pair=?", (pair,)).fetchone()
    conn.close()
    return dict(row) if row else None

def close_trade(trade_id, close_price, pnl, close_reason):
    conn  = get_conn()
    trade = conn.execute("SELECT * FROM open_trades WHERE id=?", (trade_id,)).fetchone()
    if trade:
        conn.execute("""INSERT INTO closed_trades
                        (pair, direction, entry_price, close_price, sl, tp1,
                         qty, notional, pnl, close_reason, confidence, timeframe, opened_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (trade['pair'], trade['direction'], trade['entry_price'], close_price,
                      trade['sl'], trade['tp1'], trade['qty'], trade['notional'],
                      pnl, close_reason, trade['confidence'], trade['timeframe'], trade['opened_at']))
        conn.execute("DELETE FROM open_trades WHERE id=?", (trade_id,))
    conn.commit()
    conn.close()

def get_closed_trades(limit=100):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM closed_trades ORDER BY closed_at DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
