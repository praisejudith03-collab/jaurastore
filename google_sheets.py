"""Owner-authorized Google Sheets integration for dual-currency accounting.

The OAuth grant includes Drive access for the app-created auxiliary ledgers
and the fixed owner-selected batch workbook (NGN Ledger / CFA Ledger). The
refresh token and created-ledger IDs are encrypted before they are kept in the
existing durable growth_settings key/value store. No Google credential is ever
sent to the browser.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from config import Config
from db import execute, one

CREDENTIALS_KEY = "accounting_google_sheets_credentials_v1"
SHEETS_API = "https://sheets.googleapis.com/v4"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
SCOPES = ("openid email https://www.googleapis.com/auth/drive")
CURRENCIES = ("NGN", "CFA")
DEFAULT_BATCH_OPTIONS = tuple(f"Batch {number:02d}" for number in range(1, 21))
HTTP_TIMEOUT = 12
# Hard cap on any single Google API response (SSRF/size safeguard; a Sheets
# values answer is a few hundred KB at most).
MAX_RESPONSE_BYTES = 2_000_000


def _guarded_read(req, *, timeout=HTTP_TIMEOUT):
    """Open one request under the SSRF safeguards and return its body bytes.

    https-only, every resolved IP must be public, every redirect hop is
    re-validated, and the body is read in chunks capped at
    MAX_RESPONSE_BYTES (security.guarded_open).
    """
    import security
    try:
        return security.guarded_open(req, timeout=timeout,
                                     max_bytes=MAX_RESPONSE_BYTES)
    except security.UnsafeURLError as exc:
        raise GoogleSheetsError(f"Blocked Google request: {exc}") from exc

# The owner-selected workbook is the single durable destination for staged
# regional batches. The Drive MIME check below rejects uploaded .xlsx files;
# Google Sheets tab IDs are resolved at runtime before direct links are built.
REFERENCE_SPREADSHEET_ID = "1qT_YYqPt4pfTL7URxbePZnmFLLVljaRbBhh0qt_5AzE"
REFERENCE_WORKBOOK_LABEL = "Jaura Store regional accounting workbook"
# Compatibility alias for older callers; this is a label, not the live Drive title.
REFERENCE_SPREADSHEET_TITLE = REFERENCE_WORKBOOK_LABEL
GOOGLE_SHEETS_MIME_TYPE = "application/vnd.google-apps.spreadsheet"
_XLSX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DEFAULT_REFERENCE_SPREADSHEET_ID = REFERENCE_SPREADSHEET_ID
REFERENCE_TABS = {"NGN": "NGN Ledger", "CFA": "CFA Ledger"}


def environment_reference_spreadsheet_id():
    """Read the deployment hint; the fixed owner-selected ID remains authoritative."""
    return (os.environ.get("GOOGLE_SHEET_ID") or "").strip()


def extract_spreadsheet_id(value):
    """Accept a full Google Sheets URL or a bare id and return the id."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", text)
    return match.group(1) if match else text


def reference_spreadsheet_id(settings=None):
    """Return the owner-selected workbook ID; settings cannot redirect pushes.

    ``settings`` remains accepted for API compatibility, but the supported
    ledger destination is fixed to the workbook requested by the owner. The
    Drive metadata check still validates that it is a native Google Sheet.
    """
    return REFERENCE_SPREADSHEET_ID

_lock = threading.RLock()
_refresh_lock = threading.Lock()
_backfill_lock = threading.Lock()
_backfill_state = {"pending": False, "lastError": ""}


class GoogleSheetsError(RuntimeError):
    """Safe, user-facing Google Sheets integration error."""
    def __init__(self, message, code=""):
        super().__init__(message)
        self.code = str(code or "")


class _GoogleHTTPError(GoogleSheetsError):
    def __init__(self, message, status, code=""):
        super().__init__(message, code=code)
        self.status = status


# Error patterns returned by the Google Sheets API when a spreadsheet id points
# at an uploaded .xlsx file that has never been converted to Google Sheets
# format. The Sheets API refuses to write to such files; the fix is for the
# owner to open it in Drive and click File -> Save as Google Sheets.
_XLSX_ERROR_PATTERNS = (
    "this operation is not supported for this document",
    "the document is not a google sheet",
    "cannot edit a microsoft excel",
    "xlsx",
    "workbook format is not supported",
    "unsupported document format",
)
_XLSX_FIX_HINT = (
    "Please open your spreadsheet in Google Drive and click "
    "File → Save as Google Sheets to enable syncing."
)


def _is_xlsx_format_error(message):
    """True when a Google API error indicates the target is an .xlsx file."""
    text = str(message or "").lower()
    return any(pat in text for pat in _XLSX_ERROR_PATTERNS)


def _annotate_error(message):
    """Wrap an XLSX-format error with the user-facing remediation hint."""
    if _is_xlsx_format_error(message):
        cleaned = str(message).rstrip(". ")
        return f"{cleaned}. {_XLSX_FIX_HINT}"
    return message


def configured():
    return bool((os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
                and (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip())


def redirect_uri():
    explicit = (os.environ.get("GOOGLE_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    origin = (getattr(Config, "SITE_ORIGIN", "") or "").rstrip("/")
    return origin + "/api/admin/accounting/google/callback"


def authorization_url(state, login_hint=""):
    if not configured():
        raise GoogleSheetsError("Google Sheets is not configured on the server yet.")
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"].strip(),
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
        "state": state,
    }
    if login_hint:
        params["login_hint"] = str(login_hint)[:254]
    return AUTHORIZE_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _cipher():
    """Return a Fernet cipher from a stable server-side secret.

    cryptography is an explicit production dependency, imported lazily so the
    rest of the shop and tests continue to run when Google Sheets is unused.
    """
    secret = (os.environ.get("GOOGLE_SHEETS_TOKEN_KEY") or "").strip()
    if not secret:
        secret = str(getattr(Config, "SECRET_KEY", "") or "")
    if not secret:
        raise GoogleSheetsError("Set a stable secret key before connecting Google Sheets.")
    if (getattr(Config, "ENV", "") == "production"
            and getattr(Config, "SECRET_KEY_IS_RANDOM_FALLBACK", False)
            and not os.environ.get("GOOGLE_SHEETS_TOKEN_KEY")):
        raise GoogleSheetsError(
            "Set GOOGLE_SHEETS_TOKEN_KEY (or a stable SECRET_KEY) before connecting Google Sheets.")
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - installed in production
        raise GoogleSheetsError(
            "The server needs the cryptography package to store Google tokens safely.") from exc
    digest = hashlib.sha256(("jaurastore/google-sheets/v1\0" + secret).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _uses_remote_store():
    try:
        import catalog
        return bool(catalog._prod_source())
    except Exception:
        return False


def _load_blob():
    if _uses_remote_store():
        try:
            import supabase_store
            raw = supabase_store.load_accounting_google_credentials()
        except Exception as exc:
            raise GoogleSheetsError("The saved Google connection could not be read.") from exc
        if raw is None:
            raise GoogleSheetsError("The saved Google connection could not be read from Supabase.")
        return str(raw or "")
    row = one("SELECT value FROM growth_settings WHERE key=?", (CREDENTIALS_KEY,))
    return str(row["value"] or "") if row else ""


def _save_blob(blob):
    remote = _uses_remote_store()
    if remote:
        try:
            import supabase_store
            if not supabase_store.save_accounting_google_credentials(str(blob or "")):
                return False
        except Exception as exc:
            print(f"[google-sheets] credential save failed: {exc}")
            return False
    try:
        execute(
            "INSERT INTO growth_settings (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (CREDENTIALS_KEY, str(blob or "")),
        )
    except Exception as exc:
        # The local SQLite file is only a cache when Supabase is the durable
        # source. A successful remote write remains authoritative.
        if not remote:
            print(f"[google-sheets] local credential save failed: {exc}")
            return False
    return True


def _load_credentials():
    blob = _load_blob()
    if not blob:
        return {}
    try:
        plaintext = _cipher().decrypt(blob.encode("ascii"))
        value = json.loads(plaintext.decode("utf-8"))
    except GoogleSheetsError:
        raise
    except Exception as exc:
        raise GoogleSheetsError(
            "The saved Google connection cannot be unlocked. Reconnect Google Sheets.") from exc
    if not isinstance(value, dict):
        raise GoogleSheetsError("The saved Google connection is invalid. Reconnect Google Sheets.")
    return value


def _save_credentials(credentials):
    try:
        plaintext = json.dumps(credentials or {}, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")
        blob = _cipher().encrypt(plaintext).decode("ascii")
    except GoogleSheetsError:
        raise
    except Exception as exc:
        raise GoogleSheetsError("The Google connection could not be secured for storage.") from exc
    if not _save_blob(blob):
        raise GoogleSheetsError("The Google connection could not be saved to the accounting store.")


def _json_request(url, *, method="GET", body=None, headers=None, timeout=HTTP_TIMEOUT):
    data = None
    request_headers = {"Accept": "application/json", "User-Agent": "JauraStore-Accounting/1.0"}
    request_headers.update(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        raw = _guarded_read(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        message = "Google Sheets request failed."
        code = ""
        try:
            detail = json.loads(raw.decode("utf-8"))
            error = detail.get("error") or {}
            if isinstance(error, dict):
                message = str(error.get("message") or message)[:300]
                code = str(error.get("status") or "")[:60]
            elif error:
                message = str(error)[:300]
        except Exception:
            pass
        message = _annotate_error(message)
        err = _GoogleHTTPError(message, exc.code, code=code)
        if _is_xlsx_format_error(message):
            err.code = "XLSX_FORMAT"
        raise err from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GoogleSheetsError("Google Sheets is temporarily unavailable. Please try again.") from exc
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (TypeError, ValueError) as exc:
        raise GoogleSheetsError("Google returned an unreadable response.") from exc


def _token_request(values):
    data = urllib.parse.urlencode(values).encode("utf-8")
    req = urllib.request.Request(
        TOKEN_ENDPOINT, data=data,
        headers={"Accept": "application/json",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "JauraStore-Accounting/1.0"},
        method="POST",
    )
    try:
        raw = _guarded_read(req, timeout=HTTP_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = "Google could not authorize the accounting connection."
        try:
            data = json.loads(exc.read().decode("utf-8"))
            detail = str(data.get("error_description") or data.get("error") or detail)[:300]
        except Exception:
            pass
        raise GoogleSheetsError(detail) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GoogleSheetsError("Google authorization is temporarily unavailable.") from exc
    try:
        result = json.loads(raw.decode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise GoogleSheetsError("Google returned an unreadable authorization response.") from exc
    if not isinstance(result, dict) or not result.get("access_token"):
        raise GoogleSheetsError("Google did not return an access token.")
    return result


def _client_credentials():
    if not configured():
        raise GoogleSheetsError("Google Sheets is not configured on the server yet.")
    return os.environ["GOOGLE_CLIENT_ID"].strip(), os.environ["GOOGLE_CLIENT_SECRET"].strip()


def complete_authorization(code, admin_email=""):
    """Exchange the OAuth code, verify the Google account and persist tokens."""
    client_id, client_secret = _client_credentials()
    token = _token_request({
        "code": str(code or ""),
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    })
    info = _json_request(
        USERINFO_ENDPOINT, headers={"Authorization": "Bearer " + str(token["access_token"])}
    )
    email = str(info.get("email") or "").strip().lower()
    verified = info.get("email_verified") in (True, "true", "True", 1)
    if not email or not verified:
        raise GoogleSheetsError("Choose a verified Google account to connect its Drive.")

    # Google only returns the refresh token the first time consent is granted.
    # Preserve the old one on an explicit reconnect to the same account.
    try:
        old = _load_credentials()
    except GoogleSheetsError:
        old = {}
    refresh_token = str(token.get("refresh_token") or "")
    same_account = str(old.get("email") or "").lower() == email
    if not refresh_token and same_account:
        refresh_token = str(old.get("refreshToken") or "")
    if not refresh_token:
        raise GoogleSheetsError(
            "Google did not grant offline access. Retry and approve the Jaura Store connection.")

    credentials = {
        "accessToken": str(token["access_token"]),
        "refreshToken": refresh_token,
        "expiresAt": time.time() + max(60, int(token.get("expires_in") or 3600)),
        "email": email,
        "connectedAt": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "sheets": dict(old.get("sheets") or {}) if same_account else {},
    }
    _save_credentials(credentials)
    ensure_ledgers()
    return integration_status()


def _valid_credentials(credentials):
    if not credentials or not credentials.get("refreshToken"):
        raise GoogleSheetsError("Connect Google Sheets before opening the ledgers.")
    return credentials


def _refresh_access_token(credentials):
    with _refresh_lock:
        # Re-read after taking the lock; another request may already have
        # refreshed and saved the token while this request waited.
        current = _load_credentials()
        if (current.get("accessToken")
                and float(current.get("expiresAt") or 0) > time.time() + 90):
            return current
        client_id, client_secret = _client_credentials()
        result = _token_request({
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": current.get("refreshToken") or credentials.get("refreshToken") or "",
            "grant_type": "refresh_token",
        })
        current["accessToken"] = str(result["access_token"])
        current["expiresAt"] = time.time() + max(60, int(result.get("expires_in") or 3600))
        if result.get("refresh_token"):
            current["refreshToken"] = str(result["refresh_token"])
        _save_credentials(current)
        return current


def _access_credentials(credentials=None):
    current = credentials if isinstance(credentials, dict) else _load_credentials()
    _valid_credentials(current)
    if (not current.get("accessToken")
            or float(current.get("expiresAt") or 0) <= time.time() + 90):
        current = _refresh_access_token(current)
    return current


def _google_request(method, url, body=None):
    credentials = _access_credentials()
    headers = {"Authorization": "Bearer " + str(credentials["accessToken"])}
    try:
        return _json_request(url, method=method, body=body, headers=headers)
    except _GoogleHTTPError as exc:
        if exc.status != 401:
            raise
    credentials = _refresh_access_token(credentials)
    headers["Authorization"] = "Bearer " + str(credentials["accessToken"])
    return _json_request(url, method=method, body=body, headers=headers)


def _drive_reference_file(spreadsheet_id=REFERENCE_SPREADSHEET_ID):
    """Fetch and validate the one configured native Google spreadsheet."""
    target_id = extract_spreadsheet_id(spreadsheet_id)
    if target_id != REFERENCE_SPREADSHEET_ID:
        raise GoogleSheetsError(
            "Accounting pushes are fixed to the owner's configured regional ledger.",
            code="REFERENCE_SHEET_MISMATCH")
    file_url = ("https://www.googleapis.com/drive/v3/files/"
                f"{urllib.parse.quote(target_id, safe='')}?"
                + urllib.parse.urlencode({
                    "fields": "id,name,mimeType,modifiedTime,webViewLink",
                    "supportsAllDrives": "true",
                }))
    metadata = _google_request("GET", file_url)
    if str(metadata.get("id") or "") != REFERENCE_SPREADSHEET_ID:
        raise GoogleSheetsError("Google Drive returned a different ledger file.",
                                code="REFERENCE_SHEET_MISMATCH")
    mime = str(metadata.get("mimeType") or "")
    if mime != GOOGLE_SHEETS_MIME_TYPE:
        if mime == _XLSX_MIME_TYPE:
            raise GoogleSheetsError(_annotate_error(
                "The configured accounting workbook is an uploaded Excel file."),
                code="XLSX_FORMAT")
        raise GoogleSheetsError(
            "The configured accounting workbook is not a native Google Sheet.",
            code="REFERENCE_SHEET_NOT_NATIVE")
    return {
        "id": REFERENCE_SPREADSHEET_ID,
        "title": str(metadata.get("name") or ""),
        "mimeType": mime,
        "modifiedTime": str(metadata.get("modifiedTime") or ""),
        "url": str(metadata.get("webViewLink") or _spreadsheet_url(target_id)),
        "matchCount": 1,
    }


def resolve_native_reference_spreadsheet(settings=None, preferred_id=""):
    """Validate the exact owner-selected workbook by ID and native MIME type.

    Saved accounting settings, legacy preferences and environment overrides
    cannot redirect financial batches to another workbook.
    """
    del settings, preferred_id
    try:
        return _drive_reference_file(REFERENCE_SPREADSHEET_ID)
    except GoogleSheetsError:
        raise
    except Exception as exc:
        raise GoogleSheetsError("The configured Google Drive spreadsheet could not be read.") from exc


def _currency(value):
    """Canonicalize only the store's active regional currencies.

    Nigeria routes to NGN; Benin and Togo route to the shared West African
    CFA franc (XOF/FCFA). Unknown currencies must fail rather than leak into
    the Naira workbook.
    """
    value = str(value or "").strip().upper()
    if value in ("CFA", "XOF", "FCFA"):
        return "CFA"
    if value in ("NGN", "NAIRA", "N"):
        return "NGN"
    raise GoogleSheetsError(f"Unsupported accounting currency: {value or '(blank)'}.",
                            code="UNSUPPORTED_CURRENCY")


def _spreadsheet_url(spreadsheet_id, sheet_id=None):
    url = ("https://docs.google.com/spreadsheets/d/"
           + urllib.parse.quote(str(spreadsheet_id or ""), safe="") + "/edit")
    if sheet_id is not None and str(sheet_id).strip().isdigit():
        return url + "#gid=" + str(int(sheet_id))
    return url


def _headers(currency):
    unit = "FCFA" if currency == "CFA" else "NGN"
    return [
        "Confirmed date", "Order ID", "Customer", "Items", "Currency",
        f"Revenue · {unit}", f"Supplier costs · {unit}", f"Transport · {unit}",
        f"Net Profit · {unit}", "Batch", "Notes", "Location / Destination",
    ]


def _create_ledger(currency):
    title = "CFA Ledger" if currency == "CFA" else "Naira Ledger"
    result = _google_request("POST", SHEETS_API + "/spreadsheets", {
        "properties": {"title": "Jaura Store — " + title, "locale": "en_GB",
                     "timeZone": "Africa/Lagos"},
        "sheets": [
            {"properties": {"title": "Orders", "gridProperties": {
                "frozenRowCount": 1, "rowCount": 10000, "columnCount": 12}}},
            {"properties": {"title": "Expenses", "gridProperties": {
                "frozenRowCount": 1, "rowCount": 10000, "columnCount": 6}}},
            {"properties": {"title": "Lists", "gridProperties": {
                "frozenRowCount": 1, "rowCount": 100, "columnCount": 2}}},
        ],
    })
    spreadsheet_id = str(result.get("spreadsheetId") or "")
    sheets = {str((sheet.get("properties") or {}).get("title") or ""):
              int((sheet.get("properties") or {}).get("sheetId") or 0)
              for sheet in result.get("sheets") or []}
    if not spreadsheet_id or not all(name in sheets for name in ("Orders", "Expenses", "Lists")):
        raise GoogleSheetsError("Google could not create the accounting spreadsheet.")
    _configure_ledger(spreadsheet_id, currency, sheets)
    return {"id": spreadsheet_id, "url": _spreadsheet_url(spreadsheet_id)}


def _configure_ledger(spreadsheet_id, currency, sheet_ids):
    unit = "FCFA" if currency == "CFA" else "NGN"
    order_id = int(sheet_ids["Orders"])
    expenses_id = int(sheet_ids["Expenses"])
    lists_id = int(sheet_ids["Lists"])
    headers = _headers(currency)
    expense_headers = ["Date", "Batch", "Type", "Description", f"Amount · {unit}", "Notes"]
    list_rows = [["Delivery batch options — edit names here to change each dropdown"]]
    list_rows.extend([[value] for value in DEFAULT_BATCH_OPTIONS])
    formula = '=ARRAYFORMULA(IF(B2:B="","",F2:F-N(G2:G)-N(H2:H)))'
    values_url = f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}/values:batchUpdate"
    _google_request("POST", values_url + "?valueInputOption=USER_ENTERED", {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {"range": "'Orders'!A1:L1", "values": [headers]},
            {"range": "'Orders'!I2", "values": [[formula]]},
            {"range": "'Expenses'!A1:F1", "values": [expense_headers]},
            {"range": "'Lists'!A1:A21", "values": list_rows},
        ],
    })

    batch_range = "=Lists!$A$2:$A$21"
    money_pattern = ('#,##0 "FCFA";[Red](#,##0 "FCFA")' if currency == "CFA"
                     else '#,##0 "NGN";[Red](#,##0 "NGN")')
    requests = [
        {"repeatCell": {
            "range": {"sheetId": order_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.22, "green": 0.17, "blue": 0.14},
                       "textFormat": {"foregroundColor": {"red": 1, "green": 0.98, "blue": 0.94},
                                      "bold": True}, "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)",
        }},
        {"repeatCell": {
            "range": {"sheetId": expenses_id, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.22, "green": 0.17, "blue": 0.14},
                       "textFormat": {"foregroundColor": {"red": 1, "green": 0.98, "blue": 0.94},
                                      "bold": True}, "wrapStrategy": "WRAP"}},
            "fields": "userEnteredFormat(backgroundColor,textFormat,wrapStrategy)",
        }},
        {"repeatCell": {
            "range": {"sheetId": order_id, "startRowIndex": 1, "endRowIndex": 10000,
                       "startColumnIndex": 5, "endColumnIndex": 9},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": money_pattern}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        {"repeatCell": {
            "range": {"sheetId": expenses_id, "startRowIndex": 1, "endRowIndex": 10000,
                       "startColumnIndex": 4, "endColumnIndex": 5},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": money_pattern}}},
            "fields": "userEnteredFormat.numberFormat",
        }},
        {"setDataValidation": {
            "range": {"sheetId": order_id, "startRowIndex": 1, "endRowIndex": 10000,
                       "startColumnIndex": 9, "endColumnIndex": 10},
            "rule": {"condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": batch_range}]},
                     "showCustomUi": True, "strict": False},
        }},
        {"setDataValidation": {
            "range": {"sheetId": expenses_id, "startRowIndex": 1, "endRowIndex": 10000,
                       "startColumnIndex": 1, "endColumnIndex": 2},
            "rule": {"condition": {"type": "ONE_OF_RANGE", "values": [{"userEnteredValue": batch_range}]},
                     "showCustomUi": True, "strict": False},
        }},
        {"setDataValidation": {
            "range": {"sheetId": expenses_id, "startRowIndex": 1, "endRowIndex": 10000,
                       "startColumnIndex": 2, "endColumnIndex": 3},
            "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
                {"userEnteredValue": "Supplier Cost"}, {"userEnteredValue": "Transport"}]},
                     "showCustomUi": True, "strict": True},
        }},
        {"updateSheetProperties": {
            "properties": {"sheetId": lists_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount",
        }},
    ]
    batch_url = (f"{SHEETS_API}/spreadsheets/"
                 f"{urllib.parse.quote(spreadsheet_id, safe='')}:batchUpdate")
    _google_request("POST", batch_url, {"requests": requests})


def ensure_ledgers():
    """Create missing currency workbooks in the connected owner's Drive."""
    with _lock:
        credentials = _valid_credentials(_load_credentials())
        sheets = credentials.get("sheets")
        if not isinstance(sheets, dict):
            sheets = {}
            credentials["sheets"] = sheets
        changed = False
        for currency in CURRENCIES:
            existing = sheets.get(currency)
            if isinstance(existing, dict) and existing.get("id"):
                continue
            sheets[currency] = _create_ledger(currency)
            changed = True
            # Persist each successful workbook creation immediately. If a
            # later Google API call fails, reconnect never creates duplicates.
            _save_credentials(credentials)
        if changed:
            _save_credentials(credentials)
        return integration_status()


def integration_status():
    base = {"configured": configured(), "connected": False, "email": "",
            "ledgers": {}, "syncingExistingOrders": bool(_backfill_state.get("pending")),
            "needsReconnect": False}
    if not base["configured"]:
        base["message"] = "Add Google OAuth client settings on the server to connect Sheets."
        return base
    try:
        credentials = _load_credentials()
    except GoogleSheetsError:
        base["needsReconnect"] = True
        base["message"] = "Reconnect Google Sheets to restore access to the saved ledgers."
        return base
    if not credentials:
        base["message"] = "Connect the store owner's Google Drive to create both ledgers."
        return base
    base["email"] = str(credentials.get("email") or "")
    for currency in CURRENCIES:
        item = (credentials.get("sheets") or {}).get(currency) or {}
        base["ledgers"][currency] = {
            "id": str(item.get("id") or ""),
            "url": str(item.get("url") or _spreadsheet_url(item.get("id"))),
        }
    base["connected"] = bool(base["email"] and all(
        base["ledgers"].get(currency, {}).get("id") for currency in CURRENCIES))
    if not base["connected"]:
        base["message"] = "Finish setting up the NGN and FCFA spreadsheets by reconnecting Google Sheets."
    return base


def verify_sync(reference_id="", *, settings=None):
    """Prove end-to-end Drive access for the Accounting desk's Sheets sync.

    Checked, in order: the OAuth client is configured, a non-expired token
    (with automatic refresh) exists for the owner's account, both app-created
    ledgers are readable, and the fixed owner-selected workbook is a native
    Google Sheet with readable NGN Ledger / CFA Ledger tabs. Returns a
    JSON-safe report; it never raises and never writes to the owner's files.
    """
    report = {"ok": False, "configured": configured(), "connected": False,
              "email": "", "reference": {"id": REFERENCE_SPREADSHEET_ID,
                                         "title": "", "mimeType": "",
                                         "tabs": [], "missingTabs": [],
                                         "readable": False},
              "ledgers": {}, "steps": [], "message": ""}
    # Settings and the legacy reference_id argument cannot redirect the target.
    del reference_id, settings

    def step(name, ok, detail=""):
        report["steps"].append({"step": name, "ok": bool(ok), "detail": str(detail)[:240]})

    if not report["configured"]:
        report["message"] = ("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set on "
                             "the server yet. Add them in Render and redeploy.")
        step("oauth-client", False, "missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET")
        return report
    step("oauth-client", True, "GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set")

    try:
        credentials = _valid_credentials(_load_credentials())
    except GoogleSheetsError as exc:
        report["message"] = str(exc)
        step("owner-token", False, str(exc))
        return report
    if not credentials:
        report["message"] = ("No Google account is connected yet. Open Admin -> "
                             "Accounting and use Connect Google Sheets.")
        step("owner-token", False, "no stored refresh token")
        return report
    report["connected"] = True
    report["email"] = str(credentials.get("email") or "")
    step("owner-token", True, report["email"] or "connected")

    for currency in CURRENCIES:
        item = (credentials.get("sheets") or {}).get(currency) or {}
        spreadsheet_id = str(item.get("id") or "")
        entry = {"id": spreadsheet_id, "tab": "Orders", "readable": False,
                 "url": _spreadsheet_url(spreadsheet_id), "title": ""}
        report["ledgers"][currency] = entry
        if not spreadsheet_id:
            step(f"{currency}-ledger", False, "not created yet")
            continue
        try:
            titles = _spreadsheet_tab_titles(spreadsheet_id)
            entry["readable"] = "Orders" in [t for t in titles] or bool(titles)
            step(f"{currency}-ledger", entry["readable"],
                 "tabs: " + ", ".join(titles[:6]) if titles else "no tabs visible")
        except GoogleSheetsError as exc:
            step(f"{currency}-ledger", False, str(exc))

    try:
        native = resolve_native_reference_spreadsheet()
        target = native["id"]
        report["reference"].update(native)
        step("fixed-reference-sheet", True,
             f'{native["title"] or target} · native Google Sheets · exact configured ID')
    except GoogleSheetsError as exc:
        target = ""
        report["message"] = str(exc)
        step("fixed-reference-sheet", False, str(exc))

    if target:
        try:
            tabs = _spreadsheet_tab_properties(target)
            titles = [item["title"] for item in tabs]
            report["reference"]["tabs"] = titles
            missing = [wanted for wanted in REFERENCE_TABS.values()
                       if wanted.casefold() not in {title.casefold() for title in titles}]
            report["reference"]["missingTabs"] = missing
            report["reference"]["readable"] = True
            detail = f'{report["reference"].get("title") or target} · readable'
            detail += (f" · missing tabs: {', '.join(missing)}"
                       if missing else " · NGN Ledger and CFA Ledger tabs present")
            step("reference-tabs", not missing, detail)
        except GoogleSheetsError as exc:
            report["reference"]["readable"] = False
            report["message"] = report["message"] or str(exc)
            step("reference-tabs", False, str(exc))

    report["ok"] = bool(report["connected"]
                        and all((report["ledgers"].get(c) or {}).get("readable")
                                for c in CURRENCIES)
                        and report["reference"]["readable"]
                        and not report["reference"].get("missingTabs"))
    if report["ok"]:
        report["message"] = ("Google Drive sync is working. The active native "
                             "reference spreadsheet and ledgers are readable.")
    elif not report["message"]:
        report["message"] = "Some Google checks failed - see the steps above."
    return report


def _spreadsheet_id(currency):
    credentials = _valid_credentials(_load_credentials())
    sheet = (credentials.get("sheets") or {}).get(_currency(currency)) or {}
    spreadsheet_id = str(sheet.get("id") or "")
    if not spreadsheet_id:
        ensure_ledgers()
        credentials = _valid_credentials(_load_credentials())
        sheet = (credentials.get("sheets") or {}).get(_currency(currency)) or {}
        spreadsheet_id = str(sheet.get("id") or "")
    if not spreadsheet_id:
        raise GoogleSheetsError("The Google accounting spreadsheet is not ready yet.")
    return spreadsheet_id


def _values_batch_get(spreadsheet_id, ranges):
    query = urllib.parse.urlencode([("ranges", item) for item in ranges]
                                   + [("valueRenderOption", "UNFORMATTED_VALUE")])
    url = (f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}"
           f"/values:batchGet?{query}")
    result = _google_request("GET", url)
    return [item.get("values") or [] for item in result.get("valueRanges") or []]


def read_ledger(currency):
    """Read the selected currency's pushed-batch tab from the fixed workbook."""
    currency = _currency(currency)
    native = resolve_native_reference_spreadsheet()
    spreadsheet_id = native["id"]
    wanted = REFERENCE_TABS[currency]
    tabs = _spreadsheet_tab_properties(spreadsheet_id)
    tab = next((item["title"] for item in tabs
                if item["title"].strip().casefold() == wanted.casefold()), None)
    if not tab:
        raise GoogleSheetsError(f"The required {wanted} tab is missing.",
                                code="REFERENCE_TAB_MISSING")
    orders = _values_batch_get(spreadsheet_id, [f"'{tab}'!A1:L"])[0]
    return {"currency": currency, "spreadsheetId": spreadsheet_id,
            "orders": orders, "expenses": [], "batchOptions": []}


def _header_map(rows):
    if not rows:
        return {}
    return {str(value or "").strip().lower(): index
            for index, value in enumerate(rows[0]) if str(value or "").strip()}


def _column(row, headers, label, fallback, default=""):
    index = headers.get(label.lower(), fallback)
    return row[index] if len(row) > index else default


def _number(value):
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(round(value)))
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    try:
        return max(0, int(round(float(match.group(0)))))
    except (ValueError, OverflowError):
        return 0


def _signed_number(value):
    """Read a signed ledger amount without clamping a net loss to zero."""
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        return 0
    if isinstance(value, (int, float)):
        return int(round(value))
    text = str(value).strip().replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return 0
    try:
        return int(round(float(match.group(0))))
    except (ValueError, OverflowError):
        return 0


def _row_date(value):
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return (_dt.datetime(1899, 12, 30) + _dt.timedelta(days=float(value))).date()
        except (OverflowError, ValueError):
            return None
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text[:10])
    except ValueError:
        pass
    for fmt in ("%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return _dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def period_bounds(period, today=None):
    """Inclusive date bounds for the current weekly, monthly or yearly view."""
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    value = str(period or "month").strip().lower()
    if value in ("week", "weekly"):
        return today - _dt.timedelta(days=today.weekday()), today
    if value in ("year", "yearly", "annual"):
        return today.replace(month=1, day=1), today
    if value in ("all", "all-time", "all_time"):
        return None, None
    return today.replace(day=1), today


def _in_filters(day, start, end):
    if start is None and end is None:
        return True
    return bool(day and (start is None or day >= start) and (end is None or day <= end))


def summarize(ledger, period="month", batch="", today=None):
    """Currency-safe totals from the Google rows, including unlinked expenses."""
    start, end = period_bounds(period, today=today)
    batch_key = str(batch or "").strip().casefold()
    order_rows = list(ledger.get("orders") or [])
    expense_rows = list(ledger.get("expenses") or [])
    order_headers = _header_map(order_rows)
    expense_headers = _header_map(expense_rows)
    revenue = supplier_costs = transport = net_profit = 0
    unlinked_costs = 0
    order_count = 0
    batches = set()
    net_column = next((index for name, index in order_headers.items()
                       if name.startswith("net profit")), 8)

    for row in order_rows[1:]:
        if not isinstance(row, list):
            continue
        row_batch = str(_column(row, order_headers, "batch", 9) or "").strip()
        if row_batch:
            batches.add(row_batch)
        if batch_key and row_batch.casefold() != batch_key:
            continue
        date = _row_date(_column(row, order_headers, "confirmed date", 0))
        if not _in_filters(date, start, end):
            continue
        order_id = str(_column(row, order_headers, "order id", 1) or "").strip()
        if not order_id:
            continue
        row_revenue = _number(_column(row, order_headers, "revenue", 5))
        row_supplier = _number(_column(row, order_headers, "supplier costs", 6))
        row_transport = _number(_column(row, order_headers, "transport", 7))
        revenue += row_revenue
        supplier_costs += row_supplier
        transport += row_transport
        saved_profit = row[net_column] if len(row) > net_column else ""
        net_profit += (_signed_number(saved_profit)
                       if str(saved_profit or "").strip() != ""
                       else row_revenue - row_supplier - row_transport)
        order_count += 1

    for row in expense_rows[1:]:
        if not isinstance(row, list):
            continue
        row_batch = str(_column(row, expense_headers, "batch", 1) or "").strip()
        if row_batch:
            batches.add(row_batch)
        if batch_key and row_batch.casefold() != batch_key:
            continue
        date = _row_date(_column(row, expense_headers, "date", 0))
        if not _in_filters(date, start, end):
            continue
        kind = str(_column(row, expense_headers, "type", 2) or "").strip().casefold()
        amount = _number(_column(row, expense_headers, "amount", 4))
        if kind in ("supplier", "supplier cost", "supplier costs", "stock", "purchase"):
            supplier_costs += amount
            unlinked_costs += amount
        elif kind in ("transport", "transportation", "delivery", "delivery / transport"):
            transport += amount
            unlinked_costs += amount

    return {
        "currency": _currency(ledger.get("currency")),
        "period": str(period or "month"),
        "periodStart": start.isoformat() if start else "",
        "periodEnd": end.isoformat() if end else "",
        "batch": str(batch or ""),
        "revenue": revenue,
        "supplierCosts": supplier_costs,
        "transport": transport,
        "netProfit": net_profit - unlinked_costs,
        "orderCount": order_count,
        "batches": sorted(batches, key=str.casefold),
    }


def _order_row(order, batch="", include_net=False):
    """One itemized sheet row: date, order id, customer, items, money, batch.

    ``include_net`` writes the computed net-profit value into column I. The
    app-created ledgers leave it blank because their ARRAYFORMULA computes it
    live; a freshly created reference tab has no formula, so the value is
    written there instead.

    An applied discount is deducted from the Revenue cell (and recorded in
    Notes with its percentage), so ``Revenue - Supplier - Transport`` is the
    true Net Profit on every sheet, formula-driven or written.
    """
    import accounting
    order_currency = accounting.order_currency(order)
    if not order_currency:
        raise GoogleSheetsError(
            "This order has an unsupported currency and cannot be written to a ledger.",
            code="UNSUPPORTED_CURRENCY")
    payload = accounting.order_payload(order)
    entry = accounting.entry_from_order(order)
    items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
    lines = []
    for item in items[:24]:
        name = str(item.get("name") or item.get("id") or "Item").strip()
        qty = _number(item.get("qty")) or 1
        variant = str(item.get("color") or item.get("variant") or "").strip()
        lines.append(f"{qty}× {name}" + (f" · {variant}" if variant else ""))
    sale = _number(entry.get("saleAmount"))
    discount = _number(entry.get("discount"))
    revenue = max(0, sale - discount)
    supplier = _number(entry.get("supplierCostInCurrency"))
    transport = _number(entry.get("deliveryExpense"))
    notes = str(entry.get("notes") or "")
    extras = []
    if discount:
        percent = _number(entry.get("discountPercent"))
        label = f"{percent:g}% " if percent else ""
        extras.append(f"Selling {sale:,} less {label}discount {discount:,}")
    unit = _number(entry.get("supplierUnitPriceNgn"))
    qty = _number(entry.get("supplierQty"))
    if unit and qty:
        extras.append(f"Supplier {unit:,} x {qty:g}")
    link = str(entry.get("supplierLink") or "").strip()
    if link:
        extras.append(f"Supplier link: {link}")
    if entry.get("profitOverrideActive"):
        extras.append(f"Manual net profit override: {entry.get('profitOverride')}")
    if extras:
        notes = (notes + " · " if notes else "") + " · ".join(extras)
    net_profit = (entry.get("profitOverride")
                  if entry.get("profitOverrideActive")
                  else revenue - supplier - transport)
    return [
        str(entry.get("date") or "")[:10],
        str(entry.get("id") or ""),
        str(entry.get("customer") or "Customer"),
        ", ".join(lines),
        _currency(entry.get("currency")),
        revenue,
        supplier or "",
        transport or "",
        net_profit if include_net else "",
        str(batch or ""),
        notes[:900],
        # Column L: the customer's destination, mapped from the staging queue
        # (order country, falling back to city / zone). Blank for old orders
        # that predate the checkout country field.
        str(entry.get("location") or "")[:80],
    ]

ORDER_LINES_TAB = "Order Lines"
ORDER_LINE_HEADERS = [
    "Confirmed date", "Order ID", "Line ID", "Customer", "Product",
    "Variant", "Quantity", "Currency", "Selling price", "Supplier cost · NGN",
    "Supplier URL", "NGN → FCFA rate", "Batch",
]


def _line_item_rows(orders, batch=""):
    """One spreadsheet row per distinct ordered product line."""
    import accounting
    rows = []
    for order in orders or []:
        if not isinstance(order, dict) or str(order.get("status") or "") != "confirmed":
            continue
        if not accounting.order_currency(order):
            raise GoogleSheetsError(
                "This order has an unsupported currency and cannot be written to a ledger.",
                code="UNSUPPORTED_CURRENCY")
        entry = accounting.entry_from_order(order)
        for line in entry.get("lineItems") or []:
            if not isinstance(line, dict):
                continue
            rows.append([
                str(entry.get("date") or "")[:10],
                str(entry.get("id") or ""),
                str(line.get("lineId") or ""),
                str(entry.get("customer") or "Customer"),
                str(line.get("name") or "Item"),
                str(line.get("variant") or ""),
                _number(line.get("qty")) or 1,
                _currency(entry.get("currency")),
                _number(line.get("saleAmount")),
                _number(line.get("supplierCostNgn")),
                str(line.get("supplierLink") or ""),
                float(accounting.safe_rate(entry.get("exchangeRate"))),
                str(batch or ""),
            ])
    return rows


def ensure_order_lines_tab(spreadsheet_id):
    """Find or create the native spreadsheet's per-product detail tab."""
    titles = _spreadsheet_tab_titles(spreadsheet_id)
    for title in titles:
        if title.strip().casefold() == ORDER_LINES_TAB.casefold():
            return title
    batch_url = (f"{SHEETS_API}/spreadsheets/"
                 f"{urllib.parse.quote(str(spreadsheet_id), safe='')}:batchUpdate")
    _google_request("POST", batch_url, {"requests": [{"addSheet": {
        "properties": {"title": ORDER_LINES_TAB,
                      "gridProperties": {"frozenRowCount": 1,
                                         "columnCount": len(ORDER_LINE_HEADERS)}}
    }}]})
    encoded_range = urllib.parse.quote(
        f"'{ORDER_LINES_TAB}'!A1:M1", safe="!':")
    values_url = (f"{SHEETS_API}/spreadsheets/"
                  f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
                  f"/values/{encoded_range}?valueInputOption=USER_ENTERED")
    _google_request("PUT", values_url, {"majorDimension": "ROWS",
                                        "values": [ORDER_LINE_HEADERS]})
    return ORDER_LINES_TAB


def _existing_order_line_rows(spreadsheet_id, tab=ORDER_LINES_TAB):
    encoded_range = urllib.parse.quote(f"'{tab}'!A:M", safe="!':")
    url = (f"{SHEETS_API}/spreadsheets/"
           f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
           f"/values/{encoded_range}?valueRenderOption=UNFORMATTED_VALUE")
    values = (_google_request("GET", url).get("values") or [])
    result = {}
    for position, row in enumerate(values[1:], start=2):
        if not isinstance(row, list) or len(row) < 3:
            continue
        order_id, line_id = str(row[1] or "").strip(), str(row[2] or "").strip()
        if order_id and line_id:
            result[f"{order_id}|{line_id}"] = position
    return result


def _append_order_line_rows(spreadsheet_id, tab, rows):
    if not rows:
        return
    encoded_range = urllib.parse.quote(f"'{tab}'!A:M", safe="!':")
    url = (f"{SHEETS_API}/spreadsheets/"
           f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
           f"/values/{encoded_range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
    _google_request("POST", url, {"majorDimension": "ROWS", "values": rows})


def _update_order_line_row(spreadsheet_id, tab, position, row):
    encoded_range = urllib.parse.quote(
        f"'{tab}'!A{position}:M{position}", safe="!':")
    url = (f"{SHEETS_API}/spreadsheets/"
           f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
           f"/values/{encoded_range}?valueInputOption=RAW")
    _google_request("PUT", url, {"majorDimension": "ROWS", "values": [row]})


def _upsert_order_line_rows(spreadsheet_id, tab, rows):
    existing = _existing_order_line_rows(spreadsheet_id, tab)
    fresh = []
    updated = 0
    seen = set()
    for row in rows or []:
        key = f"{str(row[1]).strip()}|{str(row[2]).strip()}"
        position = existing.get(key)
        if position is None:
            if key not in seen:
                fresh.append(row)
                seen.add(key)
        else:
            _update_order_line_row(spreadsheet_id, tab, position, row)
            updated += 1
    for offset in range(0, len(fresh), 250):
        _append_order_line_rows(spreadsheet_id, tab, fresh[offset:offset + 250])
    return {"count": len(rows or []), "appended": len(fresh),
            "updated": updated}


def _append_rows(spreadsheet_id, rows):
    if not rows:
        return
    _append_rows_to(spreadsheet_id, "Orders", rows)


def _append_rows_to(spreadsheet_id, tab, rows):
    """Append whole rows under a tab, below whatever is already there."""
    if not rows:
        return
    range_name = f"'{tab}'!A:L"
    encoded_range = urllib.parse.quote(range_name, safe="!':")
    url = (f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}"
           f"/values/{encoded_range}:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
    _google_request("POST", url, {"majorDimension": "ROWS", "values": rows})


def _update_row_range(spreadsheet_id, tab, position, row):
    """Overwrite one existing row (A..L) in place."""
    range_name = f"'{tab}'!A{position}:L{position}"
    encoded_range = urllib.parse.quote(range_name, safe="!':")
    url = (f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}"
           f"/values/{encoded_range}?valueInputOption=RAW")
    _google_request("PUT", url, {"majorDimension": "ROWS", "values": [row]})


def _existing_order_rows(spreadsheet_id):
    return _existing_order_rows_in(spreadsheet_id, "Orders")


def _existing_order_rows_in(spreadsheet_id, tab):
    """order id -> row position inside one tab, for idempotent upserts."""
    range_name = f"'{tab}'!A:L"
    values = _values_batch_get(spreadsheet_id, [range_name])[0]
    headers = _header_map(values)
    index = headers.get("order id", 1)
    result = {}
    for position, row in enumerate(values[1:], start=2):
        if not isinstance(row, list) or len(row) <= index:
            continue
        order_id = str(row[index] or "").strip()
        if order_id:
            result[order_id] = position
    return result


def _spreadsheet_tab_properties(spreadsheet_id):
    """Every sheet tab's title and stable numeric sheetId (gid)."""
    url = (f"{SHEETS_API}/spreadsheets/"
           f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
           f"?fields=properties(title,timeZone,locale),spreadsheetId,sheets.properties")
    try:
        meta = _google_request("GET", url)
    except _GoogleHTTPError as exc:
        if _is_xlsx_format_error(exc):
            raise GoogleSheetsError(_annotate_error(str(exc)),
                                    code="XLSX_FORMAT") from exc
        raise
    result = []
    for sheet in meta.get("sheets") or []:
        props = sheet.get("properties") or {}
        title = str(props.get("title") or "").strip()
        if title:
            result.append({"title": title, "sheetId": props.get("sheetId")})
    return result


def _spreadsheet_tab_titles(spreadsheet_id):
    """Compatibility helper returning only the visible tab titles."""
    return [item["title"] for item in _spreadsheet_tab_properties(spreadsheet_id)]


def reference_tab_url(currency):
    """Build a direct browser URL for the fixed workbook's currency tab.

    A tab name is not a valid Google Sheets URL selector; resolve its numeric
    gid through the Sheets API so the NGN/CFA buttons reliably open the right
    tab even when the tab order changes.
    """
    currency = _currency(currency)
    wanted = REFERENCE_TABS[currency]
    native = resolve_native_reference_spreadsheet()
    spreadsheet_id = native["id"]
    tabs = _spreadsheet_tab_properties(spreadsheet_id)
    found = next((tab for tab in tabs
                  if str(tab.get("title") or "").strip().casefold()
                  == wanted.casefold()), None)
    if found is None:
        # A button click is an explicit operator request to open that ledger;
        # create the missing named tab (with canonical headers) so the redirect
        # can still target a real gid instead of silently opening another tab.
        ensure_reference_tab(spreadsheet_id, currency)
        tabs = _spreadsheet_tab_properties(spreadsheet_id)
        found = next((tab for tab in tabs
                      if str(tab.get("title") or "").strip().casefold()
                      == wanted.casefold()), None)
    if found is None:
        raise GoogleSheetsError(f"The {wanted} tab could not be resolved.",
                                code="REFERENCE_TAB_MISSING")
    return _spreadsheet_url(spreadsheet_id, found.get("sheetId"))


def ensure_reference_tab(spreadsheet_id, currency):
    """Resolve (or create) the NGN / FCFA tab on the reference workbook.

    Returns the exact tab title. Matching is case- and space-insensitive so an
    existing "ngn " tab is reused instead of duplicated. A missing tab is
    created with the same headers the app ledgers use.
    """
    currency = _currency(currency)
    wanted = REFERENCE_TABS[currency]
    titles = _spreadsheet_tab_titles(spreadsheet_id)
    for title in titles:
        if title.strip().upper() == wanted.upper():
            return title
    batch_url = (f"{SHEETS_API}/spreadsheets/"
                 f"{urllib.parse.quote(str(spreadsheet_id), safe='')}:batchUpdate")
    _google_request("POST", batch_url, {"requests": [
        {"addSheet": {"properties": {"title": wanted,
                                     "gridProperties": {"frozenRowCount": 1,
                                                        "columnCount": 12}}}},
    ]})
    encoded_range = urllib.parse.quote(f"'{wanted}'!A1:L1", safe="!':")
    values_url = (f"{SHEETS_API}/spreadsheets/"
                  f"{urllib.parse.quote(str(spreadsheet_id), safe='')}"
                  f"/values/{encoded_range}?valueInputOption=USER_ENTERED")
    _google_request("PUT", values_url, {"majorDimension": "ROWS",
                                        "values": [_headers(currency)]})
    return wanted


def push_orders(orders, *, batch_name="", reference_id=""):
    """Push staged orders only to the active native reference spreadsheet.

    Drive resolution is strict: the fixed spreadsheet ID must resolve to a
    native Google Sheets MIME type. Uploaded .xlsx documents and legacy Office
    backups are never fallbacks. NGN and FCFA orders land on their named
    currency tabs; item details are upserted separately in ``Order Lines``.
    Rows are idempotently upserted by order/line ID.

    Returns {currency: {spreadsheetId, tab, url, count, updated}}.
    """
    grouped = {}
    for order in orders or []:
        if not isinstance(order, dict):
            continue
        if str(order.get("status") or "") != "confirmed":
            continue
        import accounting
        currency = _currency(accounting.order_currency(order))
        grouped.setdefault(currency, []).append(order)

    # A legacy saved preference is intentionally ignored; the workbook ID is
    # fixed to the owner-selected destination and validated by Drive MIME type.
    native = resolve_native_reference_spreadsheet(preferred_id=reference_id)
    spreadsheet_id = native["id"]
    report = {}
    with _lock:
        for currency in CURRENCIES:
            group = grouped.get(currency) or []
            if not group:
                continue
            tab = ensure_reference_tab(spreadsheet_id, currency)
            existing = _existing_order_rows_in(spreadsheet_id, tab)
            fresh, updated = [], 0
            for order in group:
                row = _order_row(order, batch=batch_name, include_net=True)
                order_id = str(row[1])
                position = existing.get(order_id)
                if position is None:
                    fresh.append(row)
                    existing[order_id] = -1
                elif position > 0:
                    _update_row_range(spreadsheet_id, tab, position, row)
                    updated += 1
            for offset in range(0, len(fresh), 250):
                _append_rows_to(spreadsheet_id, tab, fresh[offset:offset + 250])
            tab_meta = next((item for item in _spreadsheet_tab_properties(spreadsheet_id)
                             if item["title"].casefold() == tab.casefold()), {})
            report[currency] = {
                "spreadsheetId": spreadsheet_id,
                "tab": tab,
                "url": _spreadsheet_url(spreadsheet_id, tab_meta.get("sheetId")),
                "count": len(group),
                "appended": len(fresh),
                "updated": updated,
            }

        line_rows = _line_item_rows(orders, batch=batch_name)
        if line_rows:
            line_tab = ensure_order_lines_tab(spreadsheet_id)
            line_result = _upsert_order_line_rows(spreadsheet_id, line_tab, line_rows)
            for details in report.values():
                details["lineTab"] = line_tab
                details["lineCount"] = line_result["count"]
                details["lineAppended"] = line_result["appended"]
                details["lineUpdated"] = line_result["updated"]
    return report


def sync_order(order):
    """Idempotently write one confirmed order, preserving every manual cell."""
    if str((order or {}).get("status") or "") != "confirmed":
        return False
    import accounting
    currency = _currency(accounting.order_currency(order))
    row = _order_row(order)
    spreadsheet_id = _spreadsheet_id(currency)
    with _lock:
        existing = _existing_order_rows(spreadsheet_id)
        position = existing.get(str(row[1]))
        if position is None:
            _append_rows(spreadsheet_id, [row])
            return True
        # A reconfirm/sync refreshes order facts only. Supplier costs, transport,
        # batch and notes are always owner-entered in the Google Sheet.
        range_name = f"'Orders'!A{position}:F{position}"
        encoded_range = urllib.parse.quote(range_name, safe="!':")
        url = (f"{SHEETS_API}/spreadsheets/{urllib.parse.quote(spreadsheet_id, safe='')}"
               f"/values/{encoded_range}?valueInputOption=RAW")
        _google_request("PUT", url, {"majorDimension": "ROWS", "values": [row[:6]]})
        return True


def sync_order_async(order):
    """Confirmation hook: never delay or fail an order status change."""
    if not configured():
        return

    def _run():
        # Credential status may involve a Supabase round trip, so even this
        # small check belongs in the worker rather than the order request.
        try:
            if not integration_status().get("connected"):
                return
        except Exception:
            return
        for attempt in range(3):
            try:
                sync_order(dict(order or {}))
                return
            except Exception as exc:
                # Do not print access/refresh tokens or customer details.
                print(f"[google-sheets] confirmed order sync attempt {attempt + 1} failed: "
                      f"{type(exc).__name__}")
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
    try:
        threading.Thread(target=_run, name="jaura-sheets-order-sync", daemon=True).start()
    except Exception as exc:
        # Confirmation remains successful if the worker pool is unavailable.
        print(f"[google-sheets] order sync worker could not start: {type(exc).__name__}")


def _do_backfill(orders):
    result = {"NGN": 0, "CFA": 0}
    for currency in CURRENCIES:
        matching = []
        for order in orders or []:
            if not isinstance(order, dict) or str(order.get("status") or "") != "confirmed":
                continue
            import accounting
            order_currency = _currency(accounting.order_currency(order))
            if order_currency == currency:
                matching.append(order)
        matching.sort(key=lambda row: str(row.get("at") or row.get("updated_at") or ""))
        spreadsheet_id = _spreadsheet_id(currency)
        # Serialize historical append with the live confirmation upsert so
        # OAuth backfill and a concurrent order confirmation cannot duplicate
        # the same order row in this worker.
        with _lock:
            existing = _existing_order_rows(spreadsheet_id)
            rows = []
            for order in matching:
                row = _order_row(order)
                if row[1] and row[1] not in existing:
                    rows.append(row)
                    existing[row[1]] = -1  # duplicates within the input are ignored
            for offset in range(0, len(rows), 250):
                _append_rows(spreadsheet_id, rows[offset:offset + 250])
        result[currency] = len(rows)
    return result


def start_backfill(orders):
    """Import existing confirmed orders after OAuth without blocking redirect."""
    copied = [dict(row) for row in (orders or []) if isinstance(row, dict)]
    with _backfill_lock:
        if _backfill_state.get("pending"):
            return False
        _backfill_state.update(pending=True, lastError="")

    def _run():
        try:
            counts = _do_backfill(copied)
            print(f"[google-sheets] backfill complete: NGN={counts['NGN']} CFA={counts['CFA']}")
        except Exception as exc:
            _backfill_state["lastError"] = str(exc)[:180]
            print(f"[google-sheets] confirmed-order backfill failed: {type(exc).__name__}: {str(exc)[:180]}")
        finally:
            _backfill_state["pending"] = False
    try:
        threading.Thread(target=_run, name="jaura-sheets-backfill", daemon=True).start()
        return True
    except Exception:
        _run()
        return True


def aggregate_fallback(entries, currency, period="month", batch="", today=None):
    """Local order-snapshot fallback before Drive connects (no unlinked expenses)."""
    currency = _currency(currency)
    start, end = period_bounds(period, today=today)
    revenue = supplier_costs = transport = net_profit = count = 0
    for entry in entries or []:
        if _currency(entry.get("currency")) != currency:
            continue
        if batch:
            entry_batch = str(entry.get("batch") or "").strip()
            if entry_batch.casefold() != str(batch).strip().casefold():
                continue
        day = _row_date(entry.get("date"))
        if not _in_filters(day, start, end):
            continue
        if entry.get("deleted"):
            continue
        sale = _number(entry.get("saleAmount"))
        discount = _number(entry.get("discount"))
        supplier = _number(entry.get("supplierCostInCurrency"))
        row_transport = _number(entry.get("deliveryExpense"))
        revenue += max(0, sale - discount)
        supplier_costs += supplier
        transport += row_transport
        saved_profit = entry.get("netProfit")
        if saved_profit is None or str(saved_profit).strip() == "":
            saved_profit = sale - discount - supplier - row_transport
        net_profit += _signed_number(saved_profit)
        count += 1
    return {"currency": currency, "period": str(period or "month"),
            "periodStart": start.isoformat() if start else "",
            "periodEnd": end.isoformat() if end else "", "batch": str(batch or ""),
            "revenue": revenue, "supplierCosts": supplier_costs, "transport": transport,
            "netProfit": net_profit,
            "orderCount": count, "batches": []}


def revoke_and_disconnect():
    """Revoke the owner's grant and clear the encrypted token record."""
    credentials = _load_credentials()
    token = str(credentials.get("refreshToken") or credentials.get("accessToken") or "")
    if token:
        data = urllib.parse.urlencode({"token": token}).encode("utf-8")
        req = urllib.request.Request(REVOKE_ENDPOINT, data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            _guarded_read(req, timeout=HTTP_TIMEOUT)
        except Exception as exc:
            # A revoked/expired token is already safe to remove locally.
            print(f"[google-sheets] Google revoke skipped: {type(exc).__name__}")
    if not _save_blob(""):
        raise GoogleSheetsError("The saved Google connection could not be removed.")
    return True
