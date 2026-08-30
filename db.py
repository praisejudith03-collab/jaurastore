"""SQLite access layer. All statements are parameterised (no string-built SQL)."""
import os, sqlite3, threading
from config import Config

_local = threading.local()
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS admins (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT    NOT NULL,
  role          TEXT    NOT NULL DEFAULT 'admin',
  created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS otp_codes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  email       TEXT    NOT NULL COLLATE NOCASE,
  code_hash   TEXT    NOT NULL,
  purpose     TEXT    NOT NULL DEFAULT 'reset',
  expires_at  TEXT    NOT NULL,
  attempts    INTEGER NOT NULL DEFAULT 0,
  consumed_at TEXT,
  created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_otp_email ON otp_codes(email, purpose);

CREATE TABLE IF NOT EXISTS rate_limits (
  key        TEXT    NOT NULL,
  action     TEXT    NOT NULL,
  hits       INTEGER NOT NULL DEFAULT 0,
  window_end REAL    NOT NULL,
  PRIMARY KEY (key, action)
);

CREATE TABLE IF NOT EXISTS audit_log (
  id     INTEGER PRIMARY KEY AUTOINCREMENT,
  actor  TEXT,
  action TEXT NOT NULL,
  detail TEXT,
  ip     TEXT,
  at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);

CREATE TABLE IF NOT EXISTS product_views (
  product_id TEXT PRIMARY KEY,
  views      INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_views_count ON product_views(views DESC);

CREATE TABLE IF NOT EXISTS variant_stock (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id   TEXT NOT NULL,
  variant_key  TEXT NOT NULL DEFAULT '__default__',
  variant_label TEXT,
  qty          INTEGER NOT NULL DEFAULT 0,
  low_threshold INTEGER NOT NULL DEFAULT 5,
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(product_id, variant_key)
);
CREATE INDEX IF NOT EXISTS idx_stock_pid ON variant_stock(product_id);

CREATE TABLE IF NOT EXISTS activity_events (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,
  product_id  TEXT,
  product_name TEXT,
  city        TEXT,
  qty         INTEGER DEFAULT 1,
  at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activity_at ON activity_events(at DESC);

CREATE TABLE IF NOT EXISTS orders (
  id           TEXT PRIMARY KEY,
  payload      TEXT NOT NULL,
  email        TEXT,
  customer_name TEXT,
  phone        TEXT,
  country      TEXT,
  city         TEXT,
  zone         TEXT,
  address      TEXT,
  note         TEXT,
  payment      TEXT,
  proof_url    TEXT,
  items_count  INTEGER NOT NULL DEFAULT 0,
  total        REAL,
  currency     TEXT,
  source       TEXT NOT NULL DEFAULT 'web',
  status       TEXT NOT NULL DEFAULT 'pending',
  at           TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_at ON orders(at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- Payment confirmations sent from the public payment form. The uploaded
-- receipt is kept, together with everything the customer typed.
CREATE TABLE IF NOT EXISTS payment_proofs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id   TEXT,
  name       TEXT,
  phone      TEXT,
  email      TEXT,
  method     TEXT,
  items      TEXT,
  quantity   TEXT,
  amount     TEXT,
  note       TEXT,
  file_url   TEXT,
  file_name  TEXT,
  file_size  INTEGER,
  mime       TEXT,
  emailed    INTEGER NOT NULL DEFAULT 0,
  email_info TEXT,
  at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_proof_at ON payment_proofs(at DESC);
CREATE INDEX IF NOT EXISTS idx_proof_order ON payment_proofs(order_id);

-- ---------------------------------------------------------------- analytics
-- One row per browser (vid cookie). Used for "unique visitors".
CREATE TABLE IF NOT EXISTS visitors (
  vid       TEXT PRIMARY KEY,
  first_at  TEXT NOT NULL,
  last_at   TEXT NOT NULL,
  sessions  INTEGER NOT NULL DEFAULT 1,
  city      TEXT,
  region    TEXT,
  country   TEXT,
  referrer  TEXT,
  ua        TEXT
);
CREATE INDEX IF NOT EXISTS idx_visitors_last ON visitors(last_at DESC);

-- Every page view.
CREATE TABLE IF NOT EXISTS page_views (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  vid     TEXT,
  sid     TEXT,
  path    TEXT,
  page    TEXT,
  ref     TEXT,
  city    TEXT,
  country TEXT,
  day     TEXT NOT NULL,
  at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pv_day ON page_views(day);
CREATE INDEX IF NOT EXISTS idx_pv_path ON page_views(path);
CREATE INDEX IF NOT EXISTS idx_pv_vid ON page_views(vid);

-- Engagement: product views, add-to-cart, checkout attempts, purchases.
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  type         TEXT NOT NULL,
  vid          TEXT,
  sid          TEXT,
  product_id   TEXT,
  product_name TEXT,
  page         TEXT,
  path         TEXT,
  value        REAL,
  currency     TEXT,
  city         TEXT,
  country      TEXT,
  day          TEXT NOT NULL,
  at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ev_type_day ON events(type, day);
CREATE INDEX IF NOT EXISTS idx_ev_product ON events(product_id);
CREATE INDEX IF NOT EXISTS idx_ev_at ON events(at DESC);

-- Heartbeat table: who is on the site right now.
CREATE TABLE IF NOT EXISTS presence (
  vid     TEXT PRIMARY KEY,
  sid     TEXT,
  page    TEXT,
  path    TEXT,
  city    TEXT,
  country TEXT,
  at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_presence_at ON presence(at DESC);
"""

def connect():
    cx = getattr(_local, "conn", None)
    if cx is None:
        os.makedirs(os.path.dirname(Config.DB_PATH), exist_ok=True)
        cx = sqlite3.connect(Config.DB_PATH, timeout=15, check_same_thread=False)
        cx.row_factory = sqlite3.Row
        cx.execute("PRAGMA foreign_keys=ON")
        _local.conn = cx
    return cx

def init_db():
    cx = connect()
    cx.executescript(SCHEMA)
    cx.commit()
    return cx

# Columns added to `orders` after the first release. CREATE TABLE IF NOT EXISTS
# will not touch an existing database, so these are applied by migrate().
ORDER_COLUMNS = {
    "customer_name": "TEXT",
    "phone": "TEXT",
    "country": "TEXT",
    "city": "TEXT",
    "zone": "TEXT",
    "address": "TEXT",
    "note": "TEXT",
    "payment": "TEXT",
    "proof_url": "TEXT",
    "items_count": "INTEGER NOT NULL DEFAULT 0",
    "source": "TEXT NOT NULL DEFAULT 'web'",
    "updated_at": "TEXT NOT NULL DEFAULT (datetime('now'))",
}


def migrate():
    """Add any missing column to an existing database. Safe to run every boot."""
    have = {r["name"] for r in query("PRAGMA table_info(orders)")}
    if not have:
        return 0
    added = 0
    for col, ddl in ORDER_COLUMNS.items():
        if col not in have:
            execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
            added += 1
    return added


def query(sql, params=()):
    return connect().execute(sql, params).fetchall()

def one(sql, params=()):
    return connect().execute(sql, params).fetchone()

def execute(sql, params=()):
    cx = connect()
    cur = cx.execute(sql, params)
    cx.commit()
    return cur

def audit(actor, action, detail="", ip=""):
    execute(
        "INSERT INTO audit_log (actor, action, detail, ip) VALUES (?,?,?,?)",
        (actor, action, str(detail)[:500], ip),
    )
