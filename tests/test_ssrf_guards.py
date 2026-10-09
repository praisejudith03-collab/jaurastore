"""SSRF safeguards on every backend URL fetch.

Supplier links are owner-entered and third-party APIs are reached with a URL
the app does not fully control, so a fetch must never become a request into
the hosting network:

  * only https is allowed (no http / file / ftp / gopher targets);
  * the hostname must resolve, and EVERY resolved address must be public -
    loopback, private, link-local, reserved and carrier-NAT ranges are
    refused (127.0.0.1, 169.254.169.254 cloud metadata, 10/8, ::1, ...);
  * a hard timeout and a hard response-size cap, so a hostile or broken
    target can neither hang a worker nor exhaust memory;
  * every redirect hop is re-validated, so a public host cannot bounce the
    fetch to a private one.

Run with:  python3 -m pytest tests/test_ssrf_guards.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("SECRET_KEY", "test-secret-key")

import pytest  # noqa: E402

import security  # noqa: E402

GOOD = "https://supplier.example/item"


def _req(url):
    import urllib.request
    return urllib.request.Request(url, headers={"User-Agent": "test"})


@pytest.fixture
def public_dns(monkeypatch):
    """Make every hostname resolve to one PUBLIC address.

    `.example` is reserved and this suite runs without outbound DNS, so the
    resolver is stubbed; the guard's own job (refuse non-public answers) is
    unchanged, and the "does not resolve" path is covered separately.
    """
    import socket

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def private_dns(monkeypatch):
    """A hostname that resolves straight into the private range."""
    import socket

    def fake_getaddrinfo(host, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


# ------------------------------------------------------------ scheme / host
@pytest.mark.parametrize("url", [
    "http://supplier.example/item",        # plain http is refused
    "file:///etc/passwd",
    "ftp://supplier.example/item",
    "gopher://supplier.example/",
    "https://",                            # no host
    "https:///item",
    "",
    "not a url at all",
])
def test_non_https_and_hostless_urls_are_refused(url):
    with pytest.raises(security.UnsafeURLError):
        security.check_public_url(url)


def test_a_public_https_url_is_allowed(public_dns):
    parsed = security.check_public_url(GOOD)
    assert parsed.scheme == "https"


# --------------------------------------------------------- private address space
@pytest.mark.parametrize("url", [
    "https://127.0.0.1/admin",
    "https://127.0.0.1/",
    "https://localhost/admin",
    "https://169.254.169.254/latest/meta-data/",   # cloud metadata
    "https://10.0.0.5/internal",
    "https://192.168.1.1/",
    "https://172.16.0.1/",
    "https://[::1]/admin",
    "https://[fc00::1]/internal",
    "https://100.64.0.1/",                          # carrier NAT / Render mesh
    "https://0.0.0.0/",
])
def test_private_loopback_and_metadata_targets_are_refused(url):
    """A fetch must never reach the hosting network or its metadata service."""
    with pytest.raises(security.UnsafeURLError):
        security.check_public_url(url)


# -------------------------------------------------------------------- size cap
def test_a_response_over_the_size_limit_is_aborted(monkeypatch, public_dns):
    """The body is read in chunks and abandoned past the cap - a 10 MB target
    must not be buffered into the worker."""
    import urllib.request

    class Big:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            return b"x" * limit

    monkeypatch.setattr(security, "_guarded_opener",
                        lambda: urllib.request.build_opener())
    monkeypatch.setattr(urllib.request.OpenerDirector, "open",
                        lambda self, req, timeout=None: Big())
    with pytest.raises(security.UnsafeURLError, match="size limit"):
        security.guarded_open(_req(GOOD), max_bytes=1024)


def test_a_body_under_the_cap_is_returned_whole(monkeypatch, public_dns):
    import urllib.request

    class Small:
        def __init__(self):
            self.sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            if self.sent:
                return b""
            self.sent = True
            return b"hello"

    monkeypatch.setattr(security, "_guarded_opener",
                        lambda: urllib.request.build_opener())
    monkeypatch.setattr(urllib.request.OpenerDirector, "open",
                        lambda self, req, timeout=None: Small())
    assert security.guarded_open(_req(GOOD), max_bytes=1024) == b"hello"


# ----------------------------------------------------------------- the timeout
def test_every_fetch_carries_a_timeout(monkeypatch, public_dns):
    """A hard timeout is passed to the opener: a hung target cannot pin a
    worker for the duration of a request."""
    import urllib.request

    seen = {}

    class Opener:
        def open(self, req, timeout=None):
            seen["timeout"] = timeout
            raise OSError("stop here")

    monkeypatch.setattr(security, "_guarded_opener", Opener)
    with pytest.raises(OSError):
        security.guarded_open(_req(GOOD), timeout=7)
    assert seen["timeout"] == 7


# ------------------------------------------------------------------ redirects
def test_the_redirect_handler_validates_every_hop():
    """The shipped opener re-checks the target on each redirect, so an open
    redirect cannot launder a private address."""
    import urllib.parse
    import urllib.request

    opener = security._guarded_opener()
    handlers = [h for h in opener.handlers
                if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert handlers, "the guarded opener must install a redirect handler"

    handler = handlers[0]
    req = _req("https://supplier.example/start")
    # A hop back into the private range is refused instead of followed.
    with pytest.raises(security.UnsafeURLError):
        handler.redirect_request(req, None, 302, "Found",
                                 {"Location": "https://127.0.0.1/secret"},
                                 "https://127.0.0.1/secret")


def test_a_public_hostname_that_resolves_into_the_private_range_is_refused(private_dns):
    """DNS rebinding / a hostile A record must not launder a private target."""
    with pytest.raises(security.UnsafeURLError):
        security.check_public_url(GOOD)


def test_an_unresolvable_host_is_refused(monkeypatch):
    import socket

    def boom(host, *args, **kwargs):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(security.UnsafeURLError):
        security.check_public_url(GOOD)


# ------------------------------------------------------------- helper surface
def test_safe_fetch_text_decodes_and_the_defaults_are_bounded():
    assert security.DEFAULT_FETCH_TIMEOUT > 0
    assert security.DEFAULT_FETCH_MAX_BYTES > 0
    # UnsafeURLError is a ValueError, so existing `except ValueError` handlers
    # around supplier fetching keep working.
    assert issubclass(security.UnsafeURLError, ValueError)


def test_the_supplier_watchdog_fetches_through_the_guard(monkeypatch):
    """The supplier page fetch - the one path fed by owner-entered URLs -
    goes through the guarded opener, not a bare urlopen."""
    import supplier_watchdog

    seen = {}

    def fake(url, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return "<html>ok</html>"

    monkeypatch.setattr(security, "safe_fetch_text", fake)
    supplier_watchdog._url_cache.clear()
    assert supplier_watchdog.fetch_url(GOOD) == "<html>ok</html>"
    assert seen["url"] == GOOD
    # The hard timeout and size cap are always passed down.
    assert seen["kwargs"]["timeout"] == supplier_watchdog.FETCH_TIMEOUT
    assert seen["kwargs"]["max_bytes"] == supplier_watchdog.MAX_BYTES
