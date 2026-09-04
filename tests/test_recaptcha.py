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


# ------------------------------------------ v2 "I'm not a robot" CHECKBOX
# The shop must render the v2 checkbox widget, not the invisible v3 badge.
# A v3 key behind a v2 widget is what makes Google answer "Invalid key type",
# so the front end has to load the explicit-render script and read the
# checkbox response - never grecaptcha.execute().
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_net_js_uses_the_invisible_v2_widget():
    """No visible box: the widget renders size:invisible and a token is
    minted with grecaptcha.execute() on submit."""
    src = _read("js", "net.js")
    assert "api.js?render=explicit" in src        # v2 explicit render
    assert "grecaptcha.render(" in src
    assert "sitekey: key" in src
    assert 'size: "invisible"' in src             # invisible widget, no box
    assert "grecaptcha.execute(" in src           # mints the token on submit
    assert "grecaptcha.getResponse(" in src       # polling fallback
    assert "api.js?render=\" + encodeURIComponent(key)" not in src


def test_checkout_page_carries_the_invisible_recaptcha_anchor():
    """The visible checkbox box is gone; only a hidden render anchor and
    the legal note remain."""
    html = _read("checkout.html")
    assert "data-recaptcha-widget" in html
    assert "data-recaptcha-note" in html
    assert "ck-recaptcha-anchor" in html
    assert "id=\"ck-recaptcha-box\"" not in html     # visible box removed
    assert "data-recaptcha-fallback" not in html     # loading hint removed


def test_widget_is_a_no_op_until_the_keys_exist(monkeypatch):
    """No site key configured -> nothing renders and nothing is blocked."""
    src = _read("js", "net.js")
    assert 'if (!key) return "";' in src          # recaptcha() resolves empty
    assert "if (!key) return false;" in src       # mountRecaptcha() bails out
    monkeypatch.setattr(Config, "RECAPTCHA_SITE_KEY", "")
    from app import create_app
    with create_app().test_client() as c:
        assert c.get("/api/config").get_json()["recaptchaSiteKey"] == ""


def test_checkout_is_never_blocked_behind_recaptcha_required():
    """RECAPTCHA_REQUIRED stays off: a missing token must not lose a sale."""
    assert Config.RECAPTCHA_REQUIRED is False
    assert "RECAPTCHA_REQUIRED=1" not in _read("render.yaml")


def test_v2_response_without_a_score_is_accepted(monkeypatch):
    """v2 replies carry no 'score' field - that must not be treated as a bot."""
    monkeypatch.setattr(Config, "RECAPTCHA_SECRET_KEY", "secret")

    class FakeResp:
        def read(self):
            return b'{"success": true, "hostname": "jaurastore.com.ng"}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: FakeResp())
    import flask
    with flask.Flask(__name__).test_request_context("/"):
        ok, why = sec.verify_recaptcha("v2-checkbox-token")
    assert ok and why == "ok"
