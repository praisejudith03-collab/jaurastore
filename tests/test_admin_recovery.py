"""Security regression tests for the one-time admin recovery endpoint."""
import os
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("ADMIN_RECOVERY_SECRET", "test-recovery-secret")

from config import Config
from db import execute, one

EMAIL = "jaurastore@gmail.com"
PW = "RecoveryNew2026x"

def reset_state():
    execute("DELETE FROM admin_recovery_state")

def payload(secret="test-recovery-secret", password=PW):
    return {"headers": {"X-Admin-Recovery-Secret": secret},
            "json": {"email": EMAIL, "newPassword": password}}

def test_successful_recovery(client):
    reset_state()
    r = client.post("/api/admin/recovery", **payload())
    assert r.status_code == 200 and r.json["ok"]
    assert one("SELECT used_at FROM admin_recovery_state WHERE id=1")["used_at"]
    row = one("SELECT password_hash FROM admins WHERE email=?", (EMAIL,))
    assert row and PW not in row["password_hash"]

def test_wrong_secret(client):
    reset_state()
    r = client.post("/api/admin/recovery", **payload("wrong-secret"))
    assert r.status_code == 403

def test_replay_prevention(client):
    reset_state()
    assert client.post("/api/admin/recovery", **payload()).status_code == 200
    assert client.post("/api/admin/recovery", **payload()).status_code == 410

def test_rate_limiting(client):
    reset_state()
    results = [client.post("/api/admin/recovery", **payload("wrong")) for _ in range(6)]
    assert results[-1].status_code == 429

def test_https_enforcement(client, monkeypatch):
    reset_state(); monkeypatch.setattr(Config, "ENV", "production")
    r = client.post("/api/admin/recovery", base_url="http://example.test", **payload())
    assert r.status_code == 400

def test_plaintext_password_is_not_persisted(client):
    reset_state()
    secret_password = "NeverPersistThis2026x"
    assert client.post("/api/admin/recovery", **payload(password=secret_password)).status_code == 200
    assert secret_password not in str(one("SELECT password_hash FROM admins WHERE email=?", (EMAIL,)))
