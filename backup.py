"""Daily backup of products and orders to GitHub.

`run()` writes data/backups/orders-backup.json (every order, full payload)
and then reuses repo_sync to commit + push it together with the product
data files. The scheduler calls it once a day at midnight; the admin panel
has a "Back up now" button that calls it on demand.
"""
import datetime, json, os
from db import query, execute, one, audit

ROOT = os.path.dirname(os.path.abspath(__file__))
BACKUP_REL = "data/backups/orders-backup.json"

_KV_KEY = "lastBackupDate"


def dump_orders(path=None):
    """Write every order (and payment proof metadata) to a JSON file."""
    path = path or os.path.join(ROOT, BACKUP_REL.replace("/", os.sep))
    rows = query("SELECT id, payload, email, customer_name, phone, country, city, zone, "
                 "address, note, payment, proof_url, items_count, total, currency, "
                 "source, status, at, updated_at FROM orders ORDER BY at")
    orders = []
    for r in rows:
        d = dict(r)
        try:
            d["payload"] = json.loads(d.get("payload") or "{}")
        except ValueError:
            pass
        orders.append(d)
    proofs = [dict(r) for r in query(
        "SELECT id, order_id, name, phone, email, method, file_url, file_name, at "
        "FROM payment_proofs ORDER BY at")]
    out = {
        "generatedAt": datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "orderCount": len(orders),
        "orders": orders,
        "paymentProofs": proofs,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return len(orders)


def run(push=True, actor="scheduler"):
    """Full backup: orders snapshot + product data, committed and pushed.
    Returns (ok, report). Never raises."""
    report = {}
    try:
        report["orders"] = dump_orders()
    except Exception as exc:
        return False, {"error": f"order dump failed: {exc}"}
    try:
        import repo_sync
        ok, sync_report = repo_sync.regenerate(
            commit=True, push=push,
            message=f"Daily backup {datetime.date.today().isoformat()}: products + orders")
        report.update(sync_report)
    except Exception as exc:
        return False, {"error": f"repo sync failed: {exc}", **report}
    audit(actor, "backup.run",
          f"orders={report.get('orders')} committed={report.get('committed')} pushed={report.get('pushed')}", "")
    return bool(ok), report


def last_backup_date():
    row = one("SELECT value FROM growth_settings WHERE key=?", (_KV_KEY,))
    return (row["value"] if row else "") or ""


def mark_backup_done(day=None):
    day = day or datetime.date.today().isoformat()
    execute("INSERT INTO growth_settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_KV_KEY, day))


def due(now=None):
    """True once per day, from midnight onwards."""
    now = now or datetime.datetime.now()
    return last_backup_date() != now.date().isoformat()
