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

-- Verified customer product reviews (only shoppers who bought the product).
CREATE TABLE IF NOT EXISTS product_reviews (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id TEXT NOT NULL,
  order_id   TEXT,
  email      TEXT NOT NULL,
  name       TEXT,
  stars      INTEGER NOT NULL DEFAULT 5,
  note       TEXT,
  at         TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(product_id, email)
);
CREATE INDEX IF NOT EXISTS idx_reviews_pid ON product_reviews(product_id);

-- Referral codes: minted automatically when an order reaches the minimum
-- spend. `uses` counts successful purchases made with the code.
CREATE TABLE IF NOT EXISTS referral_codes (
  code           TEXT PRIMARY KEY,
  email          TEXT NOT NULL,
  name           TEXT,
  uses           INTEGER NOT NULL DEFAULT 0,
  reward_issued  INTEGER NOT NULL DEFAULT 0,
  reward_coupon  TEXT,
  created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_referral_email ON referral_codes(email);

-- Discount coupons: manual (admin-made) and automatic referrer rewards.
CREATE TABLE IF NOT EXISTS coupons (
  code       TEXT PRIMARY KEY,
  percent    INTEGER NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'manual',
  email      TEXT,
  note       TEXT,
  active     INTEGER NOT NULL DEFAULT 1,
  max_uses   INTEGER,
  uses       INTEGER NOT NULL DEFAULT 0,
  expires_at TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Abandoned checkouts: captured early, reminded once by email.
CREATE TABLE IF NOT EXISTS abandoned_carts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token        TEXT UNIQUE NOT NULL,
  email        TEXT NOT NULL,
  cart_json    TEXT NOT NULL,
  currency     TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
  reminded_at  TEXT,
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_abandoned_email ON abandoned_carts(email);

-- Growth module settings (referral / coupons / abandoned cart), key-value.
CREATE TABLE IF NOT EXISTS growth_settings (
  key   TEXT PRIMARY KEY,
  value TEXT
);
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


def upsert_orders(orders):
    """Upsert restored orders into SQLite. Idempotent by primary key (id)."""
    if not orders:
        return 0
    count = 0
    for o in orders:
        try:
            execute(
                "INSERT INTO orders (id, payload, email, customer_name, phone, country, city, zone, "
                "address, note, payment, proof_url, items_count, total, currency, source, status, at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "payload=excluded.payload, email=excluded.email, customer_name=excluded.customer_name, "
                "phone=excluded.phone, country=excluded.country, city=excluded.city, zone=excluded.zone, "
                "address=excluded.address, note=excluded.note, payment=excluded.payment, "
                "proof_url=excluded.proof_url, items_count=excluded.items_count, total=excluded.total, "
                "currency=excluded.currency, source=excluded.source, status=excluded.status, "
                "at=excluded.at, updated_at=excluded.updated_at",
                (
                    o.get("id"),
                    o.get("payload"),
                    o.get("email"),
                    o.get("customer_name"),
                    o.get("phone"),
                    o.get("country"),
                    o.get("city"),
                    o.get("zone"),
                    o.get("address"),
                    o.get("note"),
                    o.get("payment"),
                    o.get("proof_url"),
                    o.get("items_count", 0),
                    o.get("total"),
                    o.get("currency"),
                    o.get("source", "web"),
                    o.get("status", "pending"),
                    o.get("at"),
                    o.get("updated_at"),
                )
            )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_order failed for {o.get('id')}: {exc}")
    return count


def upsert_receipts(receipts):
    """Upsert restored receipts into payment_proofs. Idempotent — never duplicates."""
    if not receipts:
        return 0
    count = 0
    for r in receipts:
        try:
            order_id = r.get("order_id") or r.get("id")
            file_url = r.get("file_url") or ""
            existing = one("SELECT id FROM payment_proofs WHERE order_id=? AND file_url=?", (order_id, file_url))
            if not existing and order_id:
                existing = one("SELECT id FROM payment_proofs WHERE order_id=?", (order_id,))
            mime = r.get("mime") or r.get("file_type") or ""
            at = r.get("at") or r.get("created_at") or ""
            emailed = 1 if r.get("emailed") else 0
            if existing:
                execute(
                    "UPDATE payment_proofs SET name=?, phone=?, email=?, method=?, items=?, quantity=?, "
                    "amount=?, note=?, file_url=?, file_name=?, file_size=?, mime=?, emailed=?, email_info=?, at=? "
                    "WHERE id=?",
                    (
                        r.get("name"), r.get("phone"), r.get("email"), r.get("method"),
                        r.get("items"), r.get("quantity"), r.get("amount"), r.get("note"),
                        file_url, r.get("file_name"), r.get("file_size"), mime,
                        emailed, r.get("email_info"), at, existing["id"]
                    )
                )
            else:
                execute(
                    "INSERT INTO payment_proofs (order_id, name, phone, email, method, items, quantity, "
                    "amount, note, file_url, file_name, file_size, mime, emailed, email_info, at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        order_id, r.get("name"), r.get("phone"), r.get("email"), r.get("method"),
                        r.get("items"), r.get("quantity"), r.get("amount"), r.get("note"),
                        file_url, r.get("file_name"), r.get("file_size"), mime,
                        emailed, r.get("email_info"), at
                    )
                )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_receipt failed: {exc}")
    return count


def upsert_variant_stock(rows):
    """Upsert restored variant-stock rows into SQLite. Idempotent by
    (product_id, variant_key) - the same conflict rule the Stock panel uses."""
    if not rows:
        return 0
    count = 0
    for r in rows:
        try:
            execute(
                "INSERT INTO variant_stock (product_id, variant_key, variant_label, qty, low_threshold, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(product_id, variant_key) DO UPDATE SET "
                "qty=excluded.qty, low_threshold=excluded.low_threshold, "
                "variant_label=COALESCE(excluded.variant_label, variant_stock.variant_label), "
                "updated_at=excluded.updated_at",
                (r.get("product_id"), r.get("variant_key") or "__default__",
                 r.get("variant_label"), r.get("qty") or 0,
                 r.get("low_threshold") if r.get("low_threshold") is not None else 5,
                 r.get("updated_at"))
            )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_variant_stock failed: {exc}")
    return count


def restore_growth_settings(mapping):
    """Upsert growth_settings rows restored from Supabase.

    Upsert only: local-only keys (the bootstrap marker, the category-merge
    marker, the categories JSON) are never deleted, so a restore can only
    add or refresh values, never erase state Supabase does not hold."""
    if not mapping:
        return 0
    count = 0
    for k, v in mapping.items():
        try:
            execute("INSERT INTO growth_settings (key, value) VALUES (?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(k), str(v)))
            count += 1
        except Exception as exc:
            print(f"[db] restore_growth_setting failed: {exc}")
    return count


def upsert_coupons(rows):
    """Upsert restored coupons into SQLite. Idempotent by code."""
    if not rows:
        return 0
    count = 0
    for r in rows:
        try:
            execute(
                "INSERT INTO coupons (code, percent, kind, email, note, active, max_uses, uses, expires_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "percent=excluded.percent, kind=excluded.kind, email=excluded.email, note=excluded.note, "
                "active=excluded.active, max_uses=excluded.max_uses, uses=excluded.uses, "
                "expires_at=excluded.expires_at, created_at=excluded.created_at",
                (r.get("code"), r.get("percent") or 0, r.get("kind") or "manual",
                 r.get("email"), r.get("note"), 1 if r.get("active") else 0,
                 r.get("max_uses"), r.get("uses") or 0,
                 r.get("expires_at"), r.get("created_at"))
            )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_coupon failed: {exc}")
    return count


def upsert_referral_codes(rows):
    """Upsert restored referral codes into SQLite. Idempotent by code."""
    if not rows:
        return 0
    count = 0
    for r in rows:
        try:
            execute(
                "INSERT INTO referral_codes (code, email, name, uses, reward_issued, reward_coupon, created_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(code) DO UPDATE SET "
                "email=excluded.email, name=excluded.name, uses=excluded.uses, "
                "reward_issued=excluded.reward_issued, reward_coupon=excluded.reward_coupon, "
                "created_at=excluded.created_at",
                (r.get("code"), r.get("email"), r.get("name"), r.get("uses") or 0,
                 1 if r.get("reward_issued") else 0, r.get("reward_coupon"),
                 r.get("created_at"))
            )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_referral_code failed: {exc}")
    return count


def upsert_product_reviews(rows):
    """Upsert restored product reviews into SQLite. Idempotent by
    (product_id, email) - the same conflict rule reviews_create uses."""
    if not rows:
        return 0
    count = 0
    for r in rows:
        try:
            execute(
                "INSERT INTO product_reviews (product_id, order_id, email, name, stars, note, at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(product_id, email) DO UPDATE SET "
                "order_id=excluded.order_id, name=excluded.name, stars=excluded.stars, "
                "note=excluded.note, at=excluded.at",
                (r.get("product_id"), r.get("order_id"), r.get("email"),
                 r.get("name"), r.get("stars") or 5, r.get("note"), r.get("at"))
            )
            count += 1
        except Exception as exc:
            print(f"[db] upsert_product_review failed: {exc}")
    return count
