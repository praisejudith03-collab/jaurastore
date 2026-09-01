"""Google reCAPTCHA v3 gate — behaviour contract.

The gate must be invisible until the keys are configured (local dev, tests
and static hosting keep working), reject bad tokens once a secret exists,
and never lock paying customers out when Google itself is unreachable.
"""
import security as sec
from config import Config


def test_not_configured_passes(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "")
    ok, why = sec.verify_recaptcha("anything")
    assert ok and why == "not configured"


def test_missing_token_lenient_by_default(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")
    monkeypatch.setattr(Config, "RECAPTCHA_REQUIRED", False)
    ok, why = sec.verify_recaptcha("")
    assert ok and why == "missing token"


def test_missing_token_rejected_when_required(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")
    monkeypatch.setattr(Config, "RECAPTCHA_REQUIRED", True)
    ok, _ = sec.verify_recaptcha("")
    assert not ok


def test_failed_verification_rejected(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")

    class FakeResp:
        def read(self):
            return b'{"success": false}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    import flask
    app = flask.Flask(__name__)
    with app.test_request_context("/"):
        ok, why = sec.verify_recaptcha("bad-token")
    assert not ok and why == "verification failed"


def test_low_score_rejected(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")
    monkeypatch.setattr(Config, "RECAPTCHA_MIN_SCORE", 0.3)

    class FakeResp:
        def read(self):
            return b'{"success": true, "score": 0.1}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    import flask
    app = flask.Flask(__name__)
    with app.test_request_context("/"):
        ok, why = sec.verify_recaptcha("token")
    assert not ok and "low score" in why


def test_google_outage_fails_open(monkeypatch):
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")

    import urllib.request
    def boom(*a, **k):
        raise OSError("network down")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    import flask
    app = flask.Flask(__name__)
    with app.test_request_context("/"):
        ok, why = sec.verify_recaptcha("token")
    assert ok and why == "verify unreachable"


def test_public_config_exposes_site_key(monkeypatch, client=None):
    monkeypatch.setattr(Config, "RECAPTCHA_SITE_KEY", "site-key-123")
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        d = c.get("/api/config").get_json()
        assert d["ok"] and d["recaptchaSiteKey"] == "site-key-123"
