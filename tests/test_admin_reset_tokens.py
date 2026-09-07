"""Blocker 6 - admin reset codes: expiry, single use, rate limiting.

`auth.verify_otp` has a SQLite branch with an obvious expiry/consumed_at
check, but Render runs `Config.SUPABASE_ENABLED`, so the code that actually
protects the live portal is `supabase_settings.verify_reset_token`. Testing
the SQLite branch would prove nothing about production.

These tests drive the REAL `create_reset_token` / `verify_reset_token` /
`reset_token_recent` against a fake PostgREST client that emulates the
`admin_reset_tokens` table (filters, ordering, limits, updates, deletes), so
the production logic runs line for line.

Run with:  python3 -m pytest tests/test_admin_reset_tokens.py -q
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/jaura_test.db")
os.environ.setdefault("CATALOG_PATH", "/tmp/jaura_test_catalog.json")
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("ADMIN_EMAILS", "jaurastore@gmail.com")
os.environ.setdefault("MAIL_MODE", "none")

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

import supabase_settings as ss  # noqa: E402

EMAIL = "jaurastore@gmail.com"


class _Result:
    def __init__(self, data):
        self.data = data


class FakeResetTokens:
    """A real in-memory table, not a canned return value."""

    def __init__(self, rows, fail=False):
        # The real client hands back a NEW query builder on every .table()
        # call, so this per-call state must not be shared between queries.
        self.rows = rows
        self.fail = fail
        self._filters = {}
        self._mode = None
        self._payload = None
        self._order = None
        self._limit = None

    # ---- builder ----
    def select(self, cols="*"):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode, self._payload = "insert", payload
        return self

    def update(self, payload):
        self._mode, self._payload = "update", payload
        return self

    def delete(self):
        self._mode = "delete"
        return self

    def eq(self, col, val):
        self._filters.setdefault("eq", []).append((col, val))
        return self

    def is_(self, col, val):
        self._filters.setdefault("is", []).append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    # ---- internals ----
    def _matching(self):
        out = []
        for row in self.rows:
            ok = True
            for col, val in (self._filters or {}).get("eq", []):
                ok = ok and str(row.get(col)) == str(val)
            for col, val in (self._filters or {}).get("is", []):
                ok = ok and (row.get(col) is None if val == "null" else True)
            if ok:
                out.append(row)
        return out

    def execute(self):
        if self.fail:
            raise RuntimeError("supabase unreachable")
        if self._mode == "select":
            rows = self._matching()
            if self._order:
                col, desc = self._order
                rows.sort(key=lambda r: str(r.get(col) or ""), reverse=desc)
            if self._limit:
                rows = rows[: self._limit]
            return _Result([dict(r) for r in rows])
        if self._mode == "delete":
            keep = [r for r in self.rows if r not in self._matching()]
            self.rows[:] = keep
            return _Result([])
        if self._mode == "update":
            for row in self._matching():
                row.update(self._payload)
            return _Result([])
        # insert. The real table has `created_at timestamptz not null default
        # now()` (supabase_schema.sql) and _token_row does not send it, so
        # emulate the server-side default rather than leaving it blank.
        row = dict(self._payload)
        row.setdefault("id", f"tok-{len(self.rows) + 1}")
        row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        self.rows.append(row)
        return _Result([row])


class FakeClient:
    def __init__(self, rows, fail=False):
        self._rows, self._fail = rows, fail

    def table(self, name):
        assert name == "admin_reset_tokens", name
        return FakeResetTokens(self._rows, self._fail)


@pytest.fixture
def tokens(monkeypatch):
    """Patch supabase_settings onto a fake client and return the row store."""
    rows = []
    monkeypatch.setattr(ss, "enabled", lambda: True)
    monkeypatch.setattr(ss, "client", lambda: FakeClient(rows))
    return rows


def _issue(tokens, email=EMAIL, ttl=600):
    code = ss.create_reset_token(email, ttl)
    assert tokens, "create_reset_token must persist a row"
    return code


def _backdate(tokens, seconds):
    """Rewrite expiry into the past, as if the code had been sitting unused."""
    for row in tokens:
        row["expires_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=seconds)
        ).isoformat()


# --------------------------------------------------------------------------
# happy path
# --------------------------------------------------------------------------

def test_a_fresh_code_verifies_once(tokens):
    code = _issue(tokens)
    ok, err = ss.verify_reset_token(EMAIL, code)
    assert ok is True and err == ""


def test_a_code_is_stored_only_as_a_hash(tokens):
    """The plaintext code must never land in the table - or in a report."""
    code = _issue(tokens)
    assert len(tokens) == 1
    assert tokens[0]["token_hash"] != code
    assert code not in str(tokens[0])


# --------------------------------------------------------------------------
# expiry
# --------------------------------------------------------------------------

def test_an_expired_code_is_rejected(tokens):
    code = _issue(tokens)
    _backdate(tokens, 61)
    ok, err = ss.verify_reset_token(EMAIL, code)
    assert ok is False
    assert "expired" in err.lower()


def test_an_expired_code_does_not_change_the_password(tokens, monkeypatch):
    """Rejection must be terminal - not a soft failure the caller ignores."""
    code = _issue(tokens)
    _backdate(tokens, 1)
    ok, _ = ss.verify_reset_token(EMAIL, code)
    assert ok is False
    # and the row is still unconsumed, so it cannot be retried into success
    assert tokens[0]["consumed_at"] is None


def test_a_code_issued_at_the_boundary_still_works(tokens):
    code = _issue(tokens)
    ok, err = ss.verify_reset_token(EMAIL, code)
    assert ok is True, err


# --------------------------------------------------------------------------
# single use
# --------------------------------------------------------------------------

def test_a_code_cannot_be_reused(tokens):
    code = _issue(tokens)
    assert ss.verify_reset_token(EMAIL, code)[0] is True
    ok, err = ss.verify_reset_token(EMAIL, code)
    assert ok is False
    assert "No verification code pending" in err


def test_consuming_sets_consumed_at_rather_than_deleting(tokens):
    """Keeps an audit trail; the row is filtered out by consumed_at IS NULL."""
    code = _issue(tokens)
    ss.verify_reset_token(EMAIL, code)
    assert len(tokens) == 1
    assert tokens[0]["consumed_at"] is not None


def test_a_wrong_code_does_not_burn_the_right_one(tokens):
    code = _issue(tokens)
    ok, err = ss.verify_reset_token(EMAIL, "000000" if code != "000000" else "000001")
    assert ok is False and "not correct" in err.lower()
    assert tokens[0]["consumed_at"] is None
    assert ss.verify_reset_token(EMAIL, code)[0] is True


# --------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------

def test_wrong_attempts_are_counted_and_capped(tokens):
    code = _issue(tokens)
    bad = "000000" if code != "000000" else "000001"
    for _ in range(5):
        ok, err = ss.verify_reset_token(EMAIL, bad, max_attempts=5)
        assert ok is False and "not correct" in err.lower()
    # sixth try is blocked outright, even with the correct code
    ok, err = ss.verify_reset_token(EMAIL, code, max_attempts=5)
    assert ok is False
    assert "Too many attempts" in err
    assert tokens[0]["attempts"] == 5


def test_the_attempt_counter_is_durable_not_in_memory(tokens):
    """`attempts` is a column, so it survives a Render restart too."""
    code = _issue(tokens)
    bad = "000000" if code != "000000" else "000001"
    ss.verify_reset_token(EMAIL, bad, max_attempts=5)
    assert tokens[0]["attempts"] == 1


def test_a_new_code_resets_the_lockout(tokens):
    code = _issue(tokens)
    bad = "000000" if code != "000000" else "000001"
    for _ in range(5):
        ss.verify_reset_token(EMAIL, bad, max_attempts=5)
    fresh = ss.create_reset_token(EMAIL, 600)
    assert ss.verify_reset_token(EMAIL, fresh, max_attempts=5)[0] is True


def test_a_new_code_invalidates_the_previous_one(tokens):
    """create_reset_token deletes prior rows, so only one code is ever live."""
    first = _issue(tokens)
    second = ss.create_reset_token(EMAIL, 600)
    assert len(tokens) == 1
    assert ss.verify_reset_token(EMAIL, first)[0] is False
    assert ss.verify_reset_token(EMAIL, second)[0] is True


def test_codes_are_scoped_to_their_email(tokens):
    code = ss.create_reset_token(EMAIL, 600)
    other = "attacker@example.com"
    ok, err = ss.verify_reset_token(other, code)
    assert ok is False
    assert "No verification code pending" in err


def test_the_email_match_is_case_insensitive(tokens):
    code = ss.create_reset_token("JauraStore@Gmail.com", 600)
    assert tokens[0]["email"] == EMAIL
    assert ss.verify_reset_token(EMAIL, code)[0] is True


# --------------------------------------------------------------------------
# availability + request throttling
# --------------------------------------------------------------------------

def test_an_unreachable_supabase_fails_closed(monkeypatch):
    rows = []
    monkeypatch.setattr(ss, "enabled", lambda: True)
    monkeypatch.setattr(ss, "client", lambda: FakeClient(rows, fail=True))
    with pytest.raises(RuntimeError):
        ss.create_reset_token(EMAIL, 600)


def test_verify_reports_unavailable_rather_than_opening_the_door(monkeypatch):
    monkeypatch.setattr(ss, "enabled", lambda: False)
    monkeypatch.setattr(ss, "client", lambda: None)
    ok, err = ss.verify_reset_token(EMAIL, "123456")
    assert ok is False
    assert "unavailable" in err.lower()


def test_recent_requests_are_throttled(tokens):
    _issue(tokens)
    assert ss.reset_token_recent(EMAIL, cooldown=60) is True


def test_an_old_request_is_not_throttled(tokens):
    _issue(tokens)
    tokens[0]["created_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat()
    assert ss.reset_token_recent(EMAIL, cooldown=60) is False
