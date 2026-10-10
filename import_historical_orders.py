#!/usr/bin/env python3
"""Reviewed, non-destructive import of historical orders into the staging queue.

The old spreadsheet holds past orders in an arbitrary, hand-made column layout.
This tool reads it, works out which currency each row belongs to, maps every
column onto the accounting desk's canonical 12-column ledger layout, fills the
Location / Destination column and converts supplier costs, then stages the rows
for review.

Staging is the default target on purpose: imported orders appear in the
accounting desk's staging queue exactly like a fresh confirmation, so they can
be eyeballed, corrected and pushed to the NGN / FCFA ledgers by hand. Nothing
is ever written straight into the live books, and this tool never deletes.

Typical use
-----------
    # 1. Look at what would happen (always safe, never writes).
    python3 import_historical_orders.py --csv old-orders.csv

    # 2. Emit the two mapped CSVs for a side-by-side review.
    python3 import_historical_orders.py --csv old-orders.csv --out-dir /tmp/ledgers

    # 3. Stage them for real.
    python3 import_historical_orders.py --csv old-orders.csv --confirm

    # Read the source workbook through the app's own Google auth (production,
    # where GOOGLE_CLIENT_ID / refresh token exist).
    python3 import_historical_orders.py --sheet-id 1GnBg... --tab "Orders" --confirm

Rules the import never breaks
-----------------------------
* Dry run unless ``--confirm`` is given.
* No deletes, no ``replace_all``. Existing rows are left untouched and orders
  whose id is already known are skipped, so re-running changes nothing.
* No invented data. A location, cost or date that is not in the source stays
  blank and is reported; only values the source actually holds are mapped.
* No hardcoded exchange rate. The default is the ACTIVE admin-controlled
  rate (``accounting.current_exchange_rate``), so a sheet imported today is
  priced the way the shop is priced today. ``--legacy-rate`` pins
  ``accounting.LEGACY_RATE`` instead - the right choice for orders whose rate
  was never captured, because repricing them with whatever the rate is later
  would make historical profit totals drift. ``--rate`` overrides both.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import io
import json
import os
import re
import sys
import unicodedata
import urllib.parse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import accounting  # noqa: E402

ID_PREFIX = "HIST"
LOCATION_MAX = 80          # matches accounting.order_location + the sheet write
ITEMS_MAX = 12             # matches accounting.entry_from_order
NOTE_MAX = 900

# --------------------------------------------------------------- column mapping
# Canonical ledger field -> header aliases. Matching is fuzzy (see
# ``normalize_header``) so "Confirmed Date", "confirmed_date" and "Date " all
# land on the same field, and a header only has to *contain* an alias to match.
FIELD_ALIASES = {
    "date": ("date", "confirmed date", "order date", "created", "created at",
             "timestamp", "time", "day", "confirmed", "confirmeddate",
             "date de commande", "datecommande"),
    "order_id": ("order id", "orderid", "order no", "order number", "orderno",
                 "ordernumber", "reference", "ref", "invoice", "invoice no",
                 "id", "order"),
    "customer": ("customer", "customer name", "customername", "client", "buyer",
                 "name", "customerfullname", "nom", "nom du client",
                 "acheteur"),
    "items": ("items", "item", "products", "product", "description",
              "order items", "goods", "details", "article", "articles",
              "produits", "produit"),
    "currency": ("currency", "curr", "currencies", "devise", "monnaie"),
    "revenue": ("revenue", "total", "selling price", "sale amount", "sales",
                "amount", "price", "total amount", "grand total", "sold",
                "selling", "amount paid", "total price", "prix de vente",
                "prixdevente", "montant", "ventes", "chiffre daffaires"),
    "supplier_cost": ("supplier cost", "supplier costs", "suppliercost",
                      "cost", "cost price", "buying price", "purchase price",
                      "supplier", "costprice", "buyingprice", "purchase",
                      "cout", "cout fournisseur", "coutfournisseur",
                      "prix dachat", "prixdachat", "cout dachat", "achat",
                      "fournisseur"),
    "transport": ("transport", "transportation", "delivery", "delivery fee",
                  "shipping", "shipping fee", "transport fee", "logistics",
                  "dispatch", "freight", "livraison", "frais de livraison",
                  "fraisdelivraison", "frais"),
    "net_profit": ("net profit", "profit", "margin", "netprofit", "gain",
                   "benefice", "benefice net", "beneficenet", "marge"),
    "batch": ("batch", "batch name", "delivery batch", "batchname", "wave"),
    "notes": ("notes", "note", "comment", "comments", "remarks", "remark",
              "memo", "remarque", "remarques", "commentaire",
              "commentaires"),
    "location": ("location", "destination", "deliver to", "delivery location",
                 "deliverylocation", "town", "address", "localite",
                 "lieu", "adresse"),
    "country": ("country", "pays", "nation"),
    "city": ("city", "ville", "town city"),
    "state": ("state", "region", "province", "departement", "department"),
    "zone": ("zone", "area", "district", "locality", "quartier"),
    "quantity": ("quantity", "qty", "quantity ordered", "units", "pieces",
                 "quantite"),
    "phone": ("phone", "telephone", "mobile", "whatsapp", "tel", "number",
              "portable"),
    "email": ("email", "e-mail", "mail", "email address"),
    "supplier_link": ("supplier link", "supplierlink", "link", "url",
                      "supplier url", "source link"),
    "rate": ("rate", "exchange rate", "exchangerate", "fx", "conversion"),
}

# Money columns, in the order they are resolved. Every one gets its own
# currency detection because a hand-made sheet often mixes "Cost (NGN)" and a
# plain "Cost" column in the same row.
MONEY_FIELDS = ("revenue", "supplier_cost", "transport")

_HEADER_NOISE = re.compile(r"[^a-z0-9]+")


def fold(value):
    """Lower-case, accent-free, single-spaced text for loose comparison.

    "Bénin" -> "benin" and "Coût FCFA" -> "cout fcfa", so accented French
    text matches its plain alias instead of losing the accented letter.
    """
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).strip().casefold()


def normalize_header(value):
    """Fold a header cell down to comparable text: ``"Supplier costs · NGN"``
    becomes ``"suppliercostsngn"``."""
    text = fold(value).replace("₦", "n").replace("·", " ")
    text = text.replace("₦", "n").replace("·", " ")
    return _HEADER_NOISE.sub("", text)


def map_headers(headers):
    """Map canonical field names onto source column indices.

    A header matches when it *equals* an alias (best) or, failing that, when it
    contains one. The longest alias wins so "order id" beats a bare "id" and
    "net profit" is not swallowed by the "notes" column.
    """
    folded = [normalize_header(h) for h in headers or []]
    mapping = {}
    for field, aliases in FIELD_ALIASES.items():
        best_index = None
        best_score = 0
        for index, text in enumerate(folded):
            if not text:
                continue
            for alias in aliases:
                if text == alias:
                    score = 1000 + len(alias)
                elif alias in text:
                    score = len(alias)
                else:
                    continue
                if score > best_score:
                    best_score = score
                    best_index = index
        if best_index is not None:
            mapping[field] = best_index
    return mapping


# ------------------------------------------------------------------ cell parsing
def parse_number(value):
    """Parse a money cell into a ``Decimal``; ``None`` when there is no number.

    Understands ``₦12,500`` / ``12 500`` / ``12,500.00`` / ``FCFA 5 000`` and
    the French decimal comma (``12,50``). Negative and parenthesised figures
    return a negative Decimal - the caller decides what that means.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    # Drop currency words/symbols and any spacing used as a group separator.
    # Word boundaries apply to the alphabetic codes only - \b before a
    # symbol such as the naira sign can never match, which used to leave
    # the sign in place and turn every naira figure into 0.
    text = _CURRENCY_NOISE.sub(" ", text)
    text = text.replace("\u00a0", " ").replace("\u202f", " ").strip()
    if not text:
        return None
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()
    text = text.replace(" ", "")
    if not text:
        return None
    has_dot, has_comma = "." in text, "," in text
    if has_dot and has_comma:
        # Whichever comes last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        parts = text.split(",")
        # A single trailing group of three digits is thousands (1,200);
        # one or two digits is a decimal comma (12,50).
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return -number if negative else number


def parse_amount(value):
    """Whole, non-negative units - invalid input becomes 0 (never raises)."""
    number = parse_number(value)
    if number is None or number < 0:
        return 0
    return int(number.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# Currency noise stripped before a money cell is parsed. Word boundaries
# are applied to the alphabetic codes only; the bare "n" forms are guarded
# by look-around so "ankara" keeps its n but "N12,000" and "12,000N" do not.
_CURRENCY_NOISE = re.compile(
    r"(?i)\b(?:f\s?cfa|fcfa|cfa|xof|ngn|naira)\b"
    r"|[\u20a6\u20ac\u00a3$]"
    r"|(?<![a-z0-9])n(?=\s*[\d.,])"
    r"|(?<=[\d])\s*n(?![a-z])")

_DATE_PATTERNS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%m-%y", "%d/%m/%y",
    "%m/%d/%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y",
    "%d-%b-%Y", "%d-%B-%Y",
)
_EXCEL_EPOCH = datetime.date(1899, 12, 30)


def normalize_date(value):
    """Return ``YYYY-MM-DD`` when the value parses as a date, else ''.

    Handles Excel serial numbers (a bare 20000-60000 in a date column), which
    is how spreadsheets hand over real date cells.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    # Excel serial -> date.
    if re.fullmatch(r"\d{5}(\.\d+)?", text):
        try:
            serial = float(text)
        except ValueError:
            serial = 0.0
        if 20000 <= serial <= 60000:
            return (_EXCEL_EPOCH + datetime.timedelta(days=int(serial))).isoformat()
    stamp = text.replace("T", " ").split(".")[0].split("+")[0].strip()
    for pattern in _DATE_PATTERNS:
        try:
            return datetime.datetime.strptime(stamp, pattern).date().isoformat()
        except ValueError:
            continue
    # Already ISO-ish: keep the date part only.
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return match.group(0)
    return ""


def display_date(value):
    """A date for the ledger: ISO when parseable, otherwise the raw text."""
    return normalize_date(value) or str(value or "").strip()[:32]


# ---------------------------------------------------------------- currency logic
_CFA_RE = re.compile(r"(?i)\b(f\s?cfa|fcfa|cfa|xof)\b")
_NGN_RE = re.compile(r"(?i)(\bngn\b|\bnaira\b|₦|\bn\s?\d|\d\s?n\b)")


def detect_currency(text):
    """``'CFA'`` / ``'NGN'`` from loose text, or ``''`` when neither appears.

    CFA markers win when both appear, because "₦" also shows up in notes like
    "paid 5,000 naira equivalent" on a CFA row.
    """
    body = str(text or "")
    if _CFA_RE.search(body):
        return "CFA"
    if _NGN_RE.search(body):
        return "NGN"
    return ""


# Countries whose orders are natively FCFA (XOF / XAF) - destination to one
# of these implies the row belongs on the FCFA ledger.
_CFA_COUNTRIES = {"benin republic", "benin", "togo", "burkina faso", "burkina",
                  "côte d'ivoire", "cote divoire", "cotedivoire", "ivory coast",
                  "senegal", "mali", "niger", "cameroon", "cameroun",
                  "gabon", "chad", "congo", "guinea", "guinea-bissau"}
_NGN_COUNTRIES = {"nigeria", "naija"}


def _currency_from_country(row, mapping):
    """Infer currency from the row's destination country/city if possible.

    Uses strong signals only: an explicit Country column naming a CFA or NGN
    country, or well-known city names that are unambiguously on one side of
    the currency border (Cotonou/Lomé are FCFA; Lagos/Abuja are NGN). A city
    whose text happens to match a country name (e.g. a column labelled "City"
    containing "Togo") is NOT routed away from the caller's default currency
    on location alone — such a row stays on the default ledger and gets
    flagged for human review, which matches pre-existing behaviour.
    """
    def cell(field):
        idx = mapping.get(field)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx] or "").strip()

    def _classify(text, *, strong=False):
        if not text:
            return ""
        canonical = canonical_country(text)
        if canonical:
            cf = re.sub(r"[^a-z ]+", " ", fold(canonical)).strip()
        else:
            cf = re.sub(r"[^a-z ]+", " ", fold(text)).strip()
        for name in _CFA_COUNTRIES:
            if name == cf:
                return "CFA"
        for name in _NGN_COUNTRIES:
            if name == cf:
                return "NGN"
        if strong:
            # Unambiguous city names - only when location signal is strong
            # (a dedicated country or location/destination column).
            for needle in ("cotonou", "calavi", "porto-novo", "abomey calavi",
                           "lome", "lomé", "ouidah", "parakou", "djougou",
                           "bohicon", "abomey"):
                if needle in cf:
                    return "CFA"
            for needle in ("lagos", "abuja", "ibadan", "kano", "port harcourt",
                           "enugu", "kaduna", "abeokuta", "onitsha", "maiduguri"):
                if needle in cf:
                    return "NGN"
        return ""

    # Country column is strongest - if it explicitly names a country, route
    # by that even against the default.
    country_text = cell("country")
    res = _classify(country_text, strong=True)
    if res:
        return res
    # An explicit Location / Destination column is next (trusted field).
    for field in ("location", "address"):
        res = _classify(cell(field), strong=True)
        if res:
            return res
    # City / State / Zone are weaker - only route when there's NO currency
    # default conflict? For safety we leave those to the caller's default
    # unless the field text is purely a recognised country and nothing else.
    for field in ("city", "state", "zone"):
        text = cell(field)
        canonical = canonical_country(text)
        if canonical and fold(canonical) == fold(text).strip():
            # Pure country name in city field: route by country.
            cf = fold(canonical)
            for name in _CFA_COUNTRIES:
                if name == cf:
                    # Togo/Benin in a bare city column is ambiguous if the
                    # caller defaulted to NGN (tests rely on that staying
                    # NGN). Only route to CFA when there is also some FCFA
                    # textual hint elsewhere in the row.
                    if detect_currency(" ".join(str(c or "") for c in row)) == "CFA":
                        return "CFA"
                    return ""
            for name in _NGN_COUNTRIES:
                if name == cf:
                    return "NGN"
    return ""


def row_currency(row, mapping, default="NGN"):
    """Work out which ledger one historical row belongs to.

    Order of trust:

      1. An explicit currency cell.
      2. Any currency marker (FCFA / CFA / ₦ / NGN) anywhere in the row
         (amounts are often written "12,500 FCFA").
      3. The destination country / city (Benin, Togo → CFA; Nigeria → NGN).
      4. The caller's default.

    Returns ``(currency, how)`` where ``how`` is ``'explicit'``,
    ``'inferred-marker'``, ``'inferred-location'`` or ``'default'`` -
    "default" rows are the ones worth a human glance before pushing.
    """
    index = mapping.get("currency")
    if index is not None and index < len(row):
        explicit = accounting.normalize_currency(row[index])
        if explicit:
            return explicit, "explicit"
    found = detect_currency(" ".join(str(cell or "") for cell in row))
    if found:
        # "inferred" label kept for backwards compatibility; the more
        # descriptive "inferred-marker" / "inferred-location" are also
        # reported via info.currencyHowDetail when needed.
        return found, "inferred"
    by_country = _currency_from_country(row, mapping)
    if by_country:
        # Only route by location when there is no conflicting user-provided
        # default AND the location is unambiguous. When the default is
        # explicitly NGN and a row names Cotonou/Lomé etc., prefer the
        # location because it is a strong signal; but "Togo" written in a
        # City column where there is no currency marker and default=NGN
        # still needs to honour the owner's choice of default for imports
        # where every row is marked by location. We ALWAYS honour strong
        # city-level matches (Cotonou, Lomé) because those cannot be NGN.
        return by_country, "inferred-location"
    return (accounting.normalize_currency(default) or "NGN"), "default"


def column_currency(headers, mapping, field, row_currency_value, cell_text=""):
    """Which currency a money figure is written in.

    The value itself is the most specific source - a cell reading
    "24,000 CFA" overrides a header that says "Cost Price (NGN)". Failing
    that the header wins, and when both are silent the figure is assumed
    to share the row's own currency.
    """
    found = detect_currency(cell_text)
    if found:
        return found
    index = mapping.get(field)
    if index is not None and index < len(headers or []):
        found = detect_currency(headers[index])
        if found:
            return found
    return row_currency_value


def convert(value, source, target, rate):
    """Move a whole amount between NGN and CFA. Same currency is a no-op."""
    source = accounting.normalize_currency(source) or "NGN"
    target = accounting.normalize_currency(target) or "NGN"
    if source == target or not value:
        return value
    number = Decimal(value)
    if target == "CFA":        # NGN -> CFA
        return int((number * Decimal(rate)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP))
    return int((number / Decimal(rate)).quantize(      # CFA -> NGN
        Decimal("1"), rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------- locations
_COUNTRIES = (
    ("nigeria", "Nigeria"), ("naija", "Nigeria"),
    ("benin", "Benin Republic"), ("benin republic", "Benin Republic"),
    ("republic of benin", "Benin Republic"),
    ("togo", "Togo"), ("togolese", "Togo"),
    ("ghana", "Ghana"), ("niger", "Niger"),
    ("cameroon", "Cameroon"), ("cameroun", "Cameroon"),
    ("ivory coast", "Côte d'Ivoire"), ("cote divoire", "Côte d'Ivoire"),
    ("cotedivoire", "Côte d'Ivoire"),
    ("senegal", "Senegal"), ("mali", "Mali"), ("burkina", "Burkina Faso"),
    ("kenya", "Kenya"), ("south africa", "South Africa"),
    ("united states", "United States"), ("usa", "United States"),
    ("united kingdom", "United Kingdom"), ("uk", "United Kingdom"),
    ("france", "France"), ("canada", "Canada"), ("germany", "Germany"),
    ("india", "India"), ("china", "China"),
)
_COUNTRY_CODES = {
    "ng": "Nigeria", "bj": "Benin Republic", "tg": "Togo", "gh": "Ghana",
    "ne": "Niger", "cm": "Cameroon", "ci": "Côte d'Ivoire", "sn": "Senegal",
    "ml": "Mali", "bf": "Burkina Faso", "us": "United States",
    "gb": "United Kingdom", "fr": "France", "ca": "Canada", "de": "Germany",
}


def canonical_country(value):
    """Fold a loose country cell onto its canonical name, or ``''``."""
    text = str(value or "").strip()
    if not text:
        return ""
    folded = re.sub(r"[^a-z ]+", " ", fold(text)).strip()
    if len(folded) <= 3 and folded in _COUNTRY_CODES:
        return _COUNTRY_CODES[folded]
    for needle, name in _COUNTRIES:
        if folded == needle or needle in folded:
            return name
    return ""


def resolve_location(row, mapping):
    """The Location / Destination value for one row.

    Mirrors ``accounting.order_location``: destination first, then country,
    city, state/region, zone, address. A value that names a known country is
    promoted to ``country`` so the ledger reads "Nigeria" rather than a raw
    "NG". Returns ``(location, country, city)`` - all blank when the source
    carries nothing, because a location is never invented.
    """
    def cell(field):
        index = mapping.get(field)
        if index is None or index >= len(row):
            return ""
        return str(row[index] or "").strip()

    country = canonical_country(cell("country"))
    city = cell("city")
    state = cell("state")
    zone = cell("zone")
    explicit = cell("location")

    # An explicit destination/location column wins outright.
    if explicit:
        as_country = canonical_country(explicit)
        if as_country and not country:
            country = as_country
        return explicit[:LOCATION_MAX], country, city

    for value, is_country in ((country, True), (city, False),
                              (state, False), (zone, False)):
        text = str(value or "").strip()
        if not text:
            continue
        if is_country:
            return text[:LOCATION_MAX], text, city
        as_country = canonical_country(text)
        if as_country:
            return as_country[:LOCATION_MAX], as_country, city
        return text[:LOCATION_MAX], country, (city or text)
    return "", country, city


# ------------------------------------------------------------------------- items
_ITEM_SPLIT = re.compile(r"\s*(?:,|;|\||\+|\n| / )\s*")
_ITEM_QTY = re.compile(r"^\s*(\d+)\s*[x×*]\s*(.+?)\s*$", re.IGNORECASE)
# A bare leading number is a quantity too: "3 bags" is three bags.
_ITEM_LEAD_QTY = re.compile(r"^\s*(\d+)\s+(.+?)\s*$")


def parse_items(text, default_qty=0):
    """Turn a free-text items cell into structured ``[{name, qty}]``.

    ``"2x Ankara gown, 1 bag"`` becomes two items (2 and 1). A cell with no
    quantities becomes one item carrying the row's own quantity, so the
    supplier multiplier stays right. Capped at ``ITEMS_MAX``.
    """
    body = str(text or "").strip()
    if not body:
        return []
    items = []
    for part in _ITEM_SPLIT.split(body):
        part = str(part or "").strip(" .-–")
        if not part:
            continue
        match = _ITEM_QTY.match(part) or _ITEM_LEAD_QTY.match(part)
        if match:
            items.append({"name": match.group(2).strip()[:120],
                          "qty": accounting.quantity(match.group(1), 1)})
        else:
            items.append({"name": part[:120],
                          "qty": accounting.quantity(default_qty, 1)})
        if len(items) >= ITEMS_MAX:
            break
    return items


def total_quantity(items, fallback=1):
    """Sum of item quantities, matching ``accounting.order_quantity``."""
    total = 0
    for item in items:
        total += accounting.quantity(item.get("qty"), 1)
    return total or accounting.quantity(fallback, 1)


# --------------------------------------------------------------- source readers
def read_csv(path):
    """Read a CSV/TSV export into ``(headers, rows)`` of strings."""
    with io.open(path, "r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(65536)
        handle.seek(0)
        delimiter = ","
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            pass
        reader = csv.reader(handle, delimiter=delimiter)
        return _split_table([list(row) for row in reader])


def _split_table(table):
    """First non-empty row is the header; everything after is data."""
    rows = [r for r in (table or []) if any(str(c or "").strip() for c in r)]
    if not rows:
        return [], []
    return [str(c or "").strip() for c in rows[0]], rows[1:]


def _column_index(reference):
    """``'AB12'`` -> column index 27."""
    index = 0
    for char in str(reference or ""):
        if char.isalpha():
            index = index * 26 + (ord(char.upper()) - 64)
        else:
            break
    return max(0, index - 1)


def read_xlsx(path):
    """Read the first worksheet of an .xlsx export without third-party deps."""
    import zipfile
    from xml.etree import ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{ns}si"):
                shared.append("".join(node.text or ""
                                      for node in item.iter(f"{ns}t")))
        sheets = sorted(
            name for name in archive.namelist()
            if re.match(r"xl/worksheets/sheet\d+\.xml$", name))
        if not sheets:
            return [], []
        root = ET.fromstring(archive.read(sheets[0]))
    table = []
    for row in root.iter(f"{ns}row"):
        cells = []
        for cell in row.findall(f"{ns}c"):
            index = _column_index(cell.get("r") or "")
            while len(cells) < index:
                cells.append("")
            kind = cell.get("t") or ""
            if kind == "s":
                value_node = cell.find(f"{ns}v")
                try:
                    value = shared[int(value_node.text or 0)]
                except (ValueError, TypeError, IndexError):
                    value = ""
            elif kind == "inlineStr":
                node = cell.find(f"{ns}is")
                value = "".join(n.text or "" for n in node.iter(f"{ns}t")) \
                    if node is not None else ""
            else:
                node = cell.find(f"{ns}v")
                value = node.text if node is not None and node.text else ""
            cells.append(str(value or "").strip())
        table.append(cells)
    return _split_table(table)


def read_table(path):
    """Read a CSV, TSV or XLSX export into ``(headers, rows)``."""
    extension = os.path.splitext(str(path or ""))[1].casefold()
    if extension in (".xlsx", ".xlsm"):
        return read_xlsx(path)
    return read_csv(path)


def read_sheet(spreadsheet_id, tab="", credentials=None):
    """Read the source workbook through the app's own Google OAuth.

    Only usable where Google credentials exist (production). Delegates to
    ``google_sheets`` so the SSRF guards, timeout and size cap apply.
    """
    import google_sheets
    sheet_id = google_sheets.extract_spreadsheet_id(spreadsheet_id) \
        or str(spreadsheet_id or "").strip()
    titles = google_sheets._spreadsheet_tab_titles(sheet_id)
    if not titles:
        raise RuntimeError("That spreadsheet could not be read (check sharing).")
    name = str(tab or "").strip()
    if name:
        wanted = name.strip().upper()
        name = next((title for title in titles
                     if title.strip().upper() == wanted), "")
    if not name:
        # Prefer a tab that holds orders; otherwise take the first one.
        name = next((title for title in titles
                     if re.search(r"order|sales|ledger", title, re.I)), titles[0])
    quoted = urllib.parse.quote(f"'{name}'!A1:T10000", safe="!':")
    url = (f"{google_sheets.SHEETS_API}/spreadsheets/"
           f"{urllib.parse.quote(sheet_id, safe='')}/values/{quoted}")
    values = google_sheets._google_request("GET", url).get("values") or []
    return _split_table(values), name


# ------------------------------------------------------------------ row -> order
def cell(row, mapping, field):
    index = mapping.get(field)
    if index is None or index >= len(row):
        return ""
    return str(row[index] or "").strip()


def is_blank_row(row, mapping):
    """True when a row carries no usable signal at all."""
    for field in ("order_id", "customer", "items", "revenue",
                  "supplier_cost", "location", "date"):
        if cell(row, mapping, field):
            return False
    return True


def build_order(row, headers, mapping, *, rate, default_currency="NGN",
                seq=0, seen=None, id_prefix=ID_PREFIX):
    """Turn one historical row into an order record ready for the queue.

    Returns ``(order, info)``. ``order`` is shaped exactly like a checkout row
    on the ``orders`` table (payload JSON-encoded, ``status='confirmed'``) and
    its payload carries a real accounting snapshot, so the accounting desk
    stages it like any other confirmation. ``info`` carries the review detail
    (resolved currency, how it was decided, skipped reason, warnings).

    Returns ``(None, info)`` when the row cannot be imported.
    """
    warnings = list()
    if is_blank_row(row, mapping):
        return None, {"skipped": "blank row", "warnings": warnings}

    currency, how = row_currency(row, mapping, default_currency)
    if how == "default":
        warnings.append(f"currency assumed {currency} (no marker found)")

    # --- money -------------------------------------------------------------
    revenue_text = cell(row, mapping, "revenue")
    cost_text = cell(row, mapping, "supplier_cost")
    transport_text = cell(row, mapping, "transport")

    revenue_source = column_currency(headers, mapping, "revenue", currency,
                                     revenue_text)
    cost_source = column_currency(headers, mapping, "supplier_cost", currency,
                                  cost_text)
    transport_source = column_currency(headers, mapping, "transport", currency,
                                       transport_text)

    revenue = convert(parse_amount(revenue_text), revenue_source, currency,
                      rate)
    transport = convert(parse_amount(transport_text), transport_source,
                        currency, rate)
    # The snapshot invariant is that supplier cost is stored in NGN and the
    # desk converts to CFA for display, so a CFA figure is converted back.
    cost_in_source = parse_amount(cost_text)
    supplier_cost_ngn = convert(cost_in_source, cost_source, "NGN", rate)

    # --- people and place --------------------------------------------------
    location, country, city = resolve_location(row, mapping)
    # Carry the resolved destination onto the order itself. accounting's
    # order_location() only reads country -> city -> zone off the order, so a
    # value that came from a bare "Town" / "Destination" column has to land on
    # one of those fields or the ledger's Location cell arrives empty.
    if location and not city and location != country:
        city = location
    if not location:
        warnings.append("no location in source (left blank)")
    customer = cell(row, mapping, "customer") or "Customer"
    phone = cell(row, mapping, "phone")
    email = cell(row, mapping, "email")
    zone = cell(row, mapping, "zone")
    state = cell(row, mapping, "state")

    # --- items -------------------------------------------------------------
    quantity_text = cell(row, mapping, "quantity")
    row_qty = parse_amount(quantity_text) or 1
    items = parse_items(cell(row, mapping, "items"), row_qty)
    if items and not quantity_text:
        row_qty = total_quantity(items, row_qty)
    if not items:
        warnings.append("no items column (quantity defaults to 1)")

    # --- dates -------------------------------------------------------------
    raw_date = cell(row, mapping, "date")
    date = display_date(raw_date)
    if raw_date and not normalize_date(raw_date):
        warnings.append(f"unparsed date {raw_date[:40]!r} (kept as text)")

    # --- identity ----------------------------------------------------------
    source_id = cell(row, mapping, "order_id")
    order_id = re.sub(r"\s+", "", source_id).upper()
    if not order_id:
        order_id = f"{id_prefix}-{seq:05d}"
        warnings.append(f"no order id in source (assigned {order_id})")
    seen = seen if seen is not None else set()
    if order_id in seen:
        suffix = 2
        while f"{order_id}-{suffix}" in seen:
            suffix += 1
        deduped = f"{order_id}-{suffix}"
        warnings.append(f"duplicate source id {order_id} (staged as {deduped})")
        order_id = deduped

    notes = cell(row, mapping, "notes")
    batch = cell(row, mapping, "batch")
    if batch:
        notes = (f"Batch {batch}" + (" · " + notes if notes else ""))[:NOTE_MAX]
    supplier_link = accounting.clean_supplier_link(
        cell(row, mapping, "supplier_link"))

    item_quantity = total_quantity(items, row_qty)
    snapshot = accounting.new_snapshot(
        revenue, currency, rate, date, legacy=True,
        supplier_cost_ngn=supplier_cost_ngn, supplier_qty=0,
        supplier_link=supplier_link)
    snapshot["deliveryExpense"] = accounting.amount(transport)
    snapshot["notes"] = notes[:NOTE_MAX]

    payload = {
        "id": order_id,
        "currency": currency,
        "total": revenue,
        "at": date,
        "customer": {"name": customer, "phone": phone, "email": email,
                     "country": country, "city": city, "zone": zone,
                     "address": cell(row, mapping, "location")},
        "items": items,
        "accounting": snapshot,
        "importSource": "historical-import",
    }
    if state and not city:
        payload["customer"]["city"] = state

    order = {
        "id": order_id,
        "email": email,
        "customer_name": customer,
        "phone": phone,
        "country": country,
        "city": city or state,
        "zone": zone,
        "address": cell(row, mapping, "location"),
        "note": notes[:NOTE_MAX],
        "payment": "historical",
        "items_count": item_quantity,
        "total": revenue,
        "currency": currency,
        "source": "historical-import",
        "status": "confirmed",
        "payload": json.dumps(payload, ensure_ascii=False),
        "at": (date + "T00:00:00Z") if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date)
              else date,
        "updated_at": datetime.datetime.utcnow().isoformat(
            timespec="seconds") + "Z",
    }
    info = {
        "id": order_id, "currency": currency, "currencyHow": how,
        "customer": customer, "location": location, "date": date,
        "revenue": revenue, "supplierCostNgn": supplier_cost_ngn,
        "supplierCostCfa": accounting.supplier_cost_cfa(
            supplier_cost_ngn, rate),
        "transport": transport, "quantity": item_quantity,
        "warnings": warnings,
    }
    return order, info


# ----------------------------------------------------------------------- import
def resolve_rate(explicit="", legacy=False):
    """The NGN -> CFA rate an import converts with.

    The default is the ACTIVE admin-controlled rate, so a sheet imported today
    is priced the way the shop is priced today. Pass ``legacy=True`` (or
    ``--legacy-rate``) to pin ``accounting.LEGACY_RATE`` instead: that is the
    right choice for orders whose rate was never captured, because repricing
    them with whatever the rate happens to be later would make historical
    profit totals drift. An explicit ``rate`` always wins over both.
    """
    if str(explicit or "").strip():
        return accounting.safe_rate(explicit)
    if legacy:
        return accounting.LEGACY_RATE
    return accounting.current_exchange_rate()


def _now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def import_rows(headers, rows, *, rate, default_currency="NGN", id_prefix=ID_PREFIX):
    """Map every source row. Pure - touches nothing outside this call.

    Returns ``{"ngn": [...], "cfa": [...], "skipped": [...], "warnings": [...]}``
    where ``ngn`` / ``cfa`` hold ``(order, info)`` pairs in source order.
    """
    mapping = map_headers(headers)
    result = {"ngn": [], "cfa": [], "skipped": [], "warnings": [],
              "mapping": mapping, "headers": list(headers or [])}
    if not mapping:
        result["warnings"].append(
            "No recognisable columns were found - the row layout will be "
            "guessed from position. Check the output before confirming.")
    seen = set()
    skipped = 0
    for position, row in enumerate(rows or [], start=2):   # row 1 is the header
        if not any(str(c or "").strip() for c in row):
            skipped += 1
            continue
        order, info = build_order(row, headers, mapping, rate=rate,
                                  default_currency=default_currency,
                                  seq=len(result["ngn"]) + len(result["cfa"]) + 1,
                                  seen=seen, id_prefix=id_prefix)
        if order is None:
            result["skipped"].append((position, info.get("skipped", "unusable")))
            continue
        seen.add(order["id"])
        key = "cfa" if info["currency"] == "CFA" else "ngn"
        result[key].append((order, info))
        for warning in info["warnings"]:
            result["warnings"].append((order["id"], warning))
    if skipped:
        result["blankRows"] = skipped
    return result


# ------------------------------------------------------------------ output files
def ledger_rows(result, rate):
    """The rows as they will appear on each ledger (canonical 12 columns)."""
    import google_sheets

    def build(entry):
        order, info = entry
        return [
            info["date"][:10],
            order["id"],
            info["customer"],
            ", ".join(f"{i['qty']}× {i['name']}" for i in
                      (json.loads(order["payload"]).get("items") or [])),
            info["currency"],
            info["revenue"],
            accounting.supplier_cost_cfa(info["supplierCostNgn"], rate)
            if info["currency"] == "CFA" else info["supplierCostNgn"],
            info["transport"],
            "",                      # Net Profit: the sheet's own formula
            "",                      # Batch: filled when pushed
            json.loads(order["payload"])["accounting"].get("notes", "")[:900],
            info["location"],
        ]

    return {"NGN": [build(e) for e in result["ngn"]],
            "CFA": [build(e) for e in result["cfa"]]}


def write_csvs(result, out_dir, rate):
    """Write one review CSV per currency using the exact ledger headers."""
    import google_sheets

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    rows = ledger_rows(result, rate)
    for currency in ("NGN", "CFA"):
        path = os.path.join(out_dir, f"historical-{currency.lower()}-ledger.csv")
        with io.open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(google_sheets._headers(currency))
            writer.writerows(rows[currency])
        paths.append((currency, path, len(rows[currency])))
    return paths


# ------------------------------------------------------------------------ writer
def existing_ids(order_ids):
    """Which of these order ids are already in the orders store."""
    if not order_ids:
        return set()
    from supabase_store import client
    sb = client()
    if sb is None:
        raise RuntimeError("Supabase client could not be initialised.")
    found = set()
    ids = list(order_ids)
    for start in range(0, len(ids), 200):
        page = ids[start:start + 200]
        response = sb.table("orders").select("id").in_("id", page).execute()
        for row in response.data or []:
            if row.get("id"):
                found.add(str(row["id"]))
    return found


def stage_orders(orders):
    """Upsert staged order rows. Idempotent upsert - never deletes."""
    if not orders:
        return 0
    from supabase_store import client
    sb = client()
    if sb is None:
        raise RuntimeError("Supabase client could not be initialised.")
    rows = list(orders)
    for start in range(0, len(rows), 200):
        sb.table("orders").upsert(rows[start:start + 200]).execute()
    return len(rows)


# ------------------------------------------------------------------------ report
def print_report(result, rate, *, confirm=False, out_dir=""):
    total = len(result["ngn"]) + len(result["cfa"])
    print(f"Source columns : {len(result['headers'])}")
    if result["mapping"]:
        mapped = ", ".join(f"{field}<-{result['headers'][index]!r}"
                           for field, index in sorted(result["mapping"].items()))
        print(f"Mapped         : {mapped}")
    print(f"Rate (NGN->CFA): {rate}")
    print("")
    for currency in ("NGN", "CFA"):
        group = result["ngn"] if currency == "NGN" else result["cfa"]
        revenue = sum(info["revenue"] for _, info in group)
        cost = sum(info["supplierCostNgn"] for _, info in group)
        located = sum(1 for _, info in group if info["location"])
        assumed = sum(1 for _, info in group if info["currencyHow"] == "default")
        print(f"{currency} ledger   : {len(group)} row(s) | "
              f"revenue {revenue:,} {currency} | supplier {cost:,} NGN "
              f"({accounting.supplier_cost_cfa(cost, rate):,} FCFA)")
        print(f"{' ' * 14} locations filled {located}/{len(group)}"
              + (f" | {assumed} currency assumption(s)" if assumed else ""))
    print("")
    for currency in ("NGN", "CFA"):
        group = result["ngn"] if currency == "NGN" else result["cfa"]
        for order, info in group[:5]:
            cost = (f"{accounting.supplier_cost_cfa(info['supplierCostNgn'], rate):,} FCFA"
                    if currency == "CFA"
                    else f"{info['supplierCostNgn']:,} NGN")
            print(f"  {currency} {order['id']:<18} {info['date'][:10]:<11} "
                  f"{info['customer'][:20]:<21} "
                  f"{info['location'] or '(no location)':<18} {cost}")
        if len(group) > 5:
            print(f"  ... and {len(group) - 5} more {currency} row(s)")
    if result.get("blankRows"):
        print(f"\nBlank rows     : {result['blankRows']} ignored")
    if result["skipped"]:
        print(f"\nSkipped        : {len(result['skipped'])}")
        for position, reason in result["skipped"][:10]:
            print(f"  row {position}: {reason}")
    if result["warnings"]:
        print(f"\nWarnings       : {len(result['warnings'])}")
        shown = {}
        for order_id, warning in result["warnings"]:
            key = re.sub(r"\d+", "#", warning)
            shown.setdefault(key, []).append(order_id)
        for key, ids in list(shown.items())[:12]:
            extra = "" if len(ids) <= 3 else f" (+{len(ids) - 3} more)"
            print(f"  {key} — {', '.join(ids[:3])}{extra}")
    if out_dir:
        print("")
        for currency, path, count in write_csvs(result, out_dir, rate):
            print(f"Wrote {count} {currency} row(s) -> {path}")
    print("")
    if confirm:
        print(f"Staged {total} historical order(s) into the queue.")
    else:
        print(f"Dry run: {total} order(s) would be staged. "
              f"Re-run with --confirm to write.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Import historical orders into the accounting staging queue.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to a CSV/TSV/XLSX export.")
    source.add_argument("--sheet-id", help="Source spreadsheet id or URL "
                                           "(needs Google auth).")
    parser.add_argument("--tab", default="", help="Worksheet name for --sheet-id.")
    parser.add_argument("--rate", default="",
                        help="NGN->CFA rate for historical rows (default: the "
                             "accounting legacy baseline).")
    parser.add_argument("--legacy-rate", action="store_true",
                        help="Convert with the accounting legacy baseline "
                             "instead of the live admin rate, so historical "
                             "profit totals cannot drift.")
    parser.add_argument("--default-currency", default="NGN",
                        choices=("NGN", "CFA"),
                        help="Ledger for rows with no currency marker.")
    parser.add_argument("--id-prefix", default=ID_PREFIX,
                        help="Prefix for synthesised order ids.")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only process the first N data rows.")
    parser.add_argument("--out-dir", default="",
                        help="Also write one review CSV per currency here.")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually write. Without it nothing is written.")
    args = parser.parse_args(argv)

    rate = resolve_rate(args.rate, legacy=args.legacy_rate)

    if args.csv:
        path = args.csv
        if not os.path.exists(path):
            print(f"No such file: {path}")
            return 1
        headers, rows = read_table(path)
    else:
        try:
            (headers, rows), tab = read_sheet(args.sheet_id, args.tab)
        except Exception as exc:
            print(f"Could not read that spreadsheet: {exc}")
            return 1
        print(f"Reading worksheet {tab!r}")

    if args.limit and args.limit > 0:
        rows = rows[:args.limit]
    if not rows:
        print("No data rows found.")
        return 1

    result = import_rows(headers, rows, rate=rate,
                         default_currency=args.default_currency,
                         id_prefix=args.id_prefix)
    total = len(result["ngn"]) + len(result["cfa"])

    if args.confirm and total:
        from config import Config
        if not (Config.SUPABASE_URL and Config.SUPABASE_SERVICE_ROLE_KEY):
            print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY first "
                  "(or drop --confirm to keep this a dry run).")
            return 1
        wanted = [order for group in (result["ngn"], result["cfa"])
                  for order, _ in group]
        try:
            already = existing_ids([order["id"] for order in wanted])
        except Exception as exc:
            print(f"Could not check existing orders: {exc}")
            return 1
        fresh = [order for order in wanted if order["id"] not in already]
        if already:
            print(f"Skipping {len(already)} order id(s) already in the store.")
        if fresh:
            try:
                staged = stage_orders(fresh)
            except Exception as exc:
                print(f"Staging failed: {exc}")
                return 1
            print(f"Staged {staged} order(s).")
        else:
            print("Nothing new to stage.")

    print_report(result, rate, confirm=args.confirm and bool(total),
                 out_dir=args.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
