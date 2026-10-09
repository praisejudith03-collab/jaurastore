"""J Aura Store - Flask app: serves the storefront plus a JSON API."""
import os, re, html, gzip, datetime
from urllib.parse import quote
from flask import (Flask, send_from_directory, jsonify, request, redirect,
                   Response, abort, session, make_response as _make_response)
from config import Config
from db import init_db, migrate
import security as sec
import auth as authmod
import storage
import analytics as analytics_mod
import catalog as catalog_mod
import api as api_mod
from api import api

ROOT = os.path.dirname(os.path.abspath(__file__))
LEGACY_PREFIX = "/JauraStore"   # keeps old project-site links working

# ------------------------------------------------ what the catch-all may serve
# The storefront is flat files at the repo root - but so is everything else:
# the Python sources, the SQLite database, the CI config, and (until this was
# fixed) a stray copy of git's own internals. Serving "any file that exists"
# published the lot: /config.py answered 200 in production, and with it the
# public admin bootstrap password. Only the asset types the shop actually
# ships are served now; everything else falls through to the 404 page.
ALLOWED_STATIC_EXT = frozenset({
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".xml", ".ico",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".avif",
    ".mp4", ".webm", ".mov", ".woff", ".woff2", ".ttf", ".otf", ".map",
})
# Allowed by name: `.txt` is not an allowed extension, so requirements.txt,
# Procfile-adjacent text and the rest stay private while this stays public.
ALLOWED_STATIC_NAMES = frozenset({"robots.txt"})
# Whole trees that are never public whatever they contain.
BLOCKED_STATIC_DIRS = frozenset({
    "data", "tests", "node_modules", "__pycache__", "venv", ".venv",
})


def servable(rel):
    """True when a repo-root-relative path may be handed to a browser."""
    parts = [p for p in re.split(r"[/\\]+", str(rel or "").replace(os.sep, "/"))
             if p and p not in (".", "..")]
    if not parts:
        return False
    for p in parts:                       # dotfiles and dot-dirs: .env, .github
        if p.startswith("."):
            return False
    for p in parts[:-1]:                  # trees that are private whatever is in them
        if p.lower() in BLOCKED_STATIC_DIRS:
            return False
    name = parts[-1]
    if name in ALLOWED_STATIC_NAMES:
        return True
    return os.path.splitext(name)[1].lower() in ALLOWED_STATIC_EXT

# The fixed pages of the storefront (never change unless a page ships).
SITEMAP_STATIC_PAGES = (
    ("/", "1.0", "daily"),
    ("/shop.html", "0.9", "daily"),
    ("/categories.html", "0.8", "weekly"),
    ("/about.html", "0.6", "monthly"),
    ("/faq.html", "0.5", "monthly"),
    ("/delivery.html", "0.6", "monthly"),
    ("/contact.html", "0.6", "monthly"),
    ("/checkout.html", "0.5", "monthly"),
    ("/terms.html", "0.4", "monthly"),
    ("/privacy.html", "0.4", "monthly"),
    ("/returns.html", "0.4", "monthly"),
    ("/shipping.html", "0.4", "monthly"),
)


def _sitemap_entry(loc: str, lastmod: str, changefreq: str, priority: str) -> str:
    return ("  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>")


def build_sitemap() -> str:
    """The live sitemap, rebuilt on every request from what the store serves.

    One URL per fixed page, one per LIVE non-hidden category
    (shop.html?cat=<id>) and one per LIVE product using its clean public slug.

    The categories come from the same table the storefront reads -
    api_mod._categories_data(), which on boot is restored from Supabase
    (growth_settings) before any request is served - and the products from
    catalog_mod.merged(), the live catalogue with deletions and hidden rows
    already removed. A category or product that the owner deleted therefore
    can never appear here: there is no committed sitemap.xml snapshot to go
    stale and list pages that no longer exist (the old static file kept four
    deleted categories, which is exactly what Search Console flagged).
    """
    origin = (Config.SITE_ORIGIN or "").rstrip("/")
    today = datetime.date.today().isoformat()

    def url_for(path: str) -> str:
        return html.escape(origin + path, quote=False)

    urls = [_sitemap_entry(url_for(path), today, freq, pri)
            for path, pri, freq in SITEMAP_STATIC_PAGES]

    try:
        categories = api_mod._categories_data().get("categories") or []
    except Exception:
        categories = []
    for c in categories:
        cid = str((c or {}).get("id") or "").strip()
        if not cid or (c or {}).get("hidden"):
            continue                     # hidden from the store -> not indexed
        urls.append(_sitemap_entry(
            url_for("/shop.html?cat=" + quote(cid, safe="")), today, "weekly", "0.7"))

    try:
        products = catalog_mod.merged()
    except Exception:
        products = []
    for p in products:
        slug = catalog_mod.public_slug(p)
        if not slug:
            continue
        urls.append(_sitemap_entry(
            url_for("/products/" + quote(slug, safe="")), today, "weekly", "0.6"))

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


def _abs_asset_url(value):
    """A product photo (or any repo/uploads path) as an absolute URL.

    Product images are either already absolute (an admin upload living in
    Supabase Storage) or a path relative to the repo root (a seeded
    `images/products/....jpg`, or a local `/uploads/...` file) - never a
    private/signed one, so no storage.signed_url_for() call is needed here.
    """
    raw = (value or "").strip()
    if not raw:
        raw = "images/products/_placeholder.jpg"
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    origin = (Config.SITE_ORIGIN or "").rstrip("/")
    return origin + "/" + raw.lstrip("/")


def _product_price_line(p):
    """Active selling price only, in both currencies - never the
    struck-through "compare at" price. The CFA figure is always derived
    live from the NGN price at the admin's current exchange rate (see
    toCfa() in js/admin.js / js/store.js) rather than the possibly-stale
    priceCfa the catalogue happens to have stored, so a shared link always
    quotes the same CFA figure the storefront itself is showing today."""
    ngn = int(round(float(p.get("priceNgn") or 0)))
    parts = []
    if ngn > 0:
        parts.append("₦{:,}".format(ngn))
        # The live admin-controlled rate (growth setting cfaRate) - never a
        # hardcoded literal, so a shared post always quotes today's rate.
        try:
            import growth
            rate = float(growth.settings().get("cfaRate") or 0)
        except Exception:
            rate = 0.0
        import math
        cfa = int(math.ceil((ngn * rate) / 50) * 50) if rate > 0 else 0
        if cfa > 0:
            parts.append("{:,} CFA".format(cfa))
    elif p.get("priceCfa"):
        cfa = int(round(float(p.get("priceCfa") or 0)))
        if cfa > 0:
            parts.append("{:,} CFA".format(cfa))
    return " · ".join(parts) if parts else ""


def _product_options_line(p):
    """A short, human-readable list of what the product comes in - colour
    names and sizes only (never raw hex swatches), so a shared post tells a
    customer what choices are in stock without them having to click
    through first."""
    seen, names = set(), []
    for c in (p.get("colors") or []):
        c = str(c or "").strip()
        if c and not c.startswith("#") and c.lower() not in seen:
            seen.add(c.lower())
            names.append(c)
    sizes = []
    seen_sizes = set()
    for o in (p.get("options") or []):
        title = str((o or {}).get("title") or "").lower()
        if "colou" in title or "scent" in title:
            for v in (o or {}).get("values") or []:
                v = str(v or "").strip()
                if v and not v.startswith("#") and v.lower() not in seen:
                    seen.add(v.lower())
                    names.append(v)
        elif ("size" in title or "length" in title) and "colou" not in title:
            for v in (o or {}).get("values") or []:
                v = str(v or "").strip()
                if v and v.lower() not in seen_sizes:
                    seen_sizes.add(v.lower())
                    sizes.append(v)
    bits = []
    if names:
        bits.append("Colours: " + ", ".join(names[:6]))
    if sizes:
        bits.append("Sizes: " + ", ".join(sizes[:8]))
    return " · ".join(bits)


def _product_display_name(p):
    """English and French name together when they genuinely differ, so a
    shared link's headline reads in both languages - never a duplicated
    line when the catalogue has no separate French name."""
    name = str(p.get("name") or "").strip()
    name_fr = str(p.get("nameFr") or "").strip()
    if name_fr and name_fr.lower() != name.lower():
        return f"{name} / {name_fr}"
    return name


def inject_product_meta(html_text, product):
    """Swap the generic Product·Jaura Store head tags for this specific
    product's own name, price and photo.

    Owner request 2026-09-28: WhatsApp (and every other link-preview
    crawler) never runs the page's JavaScript, so the client-side meta
    tag updates in js/store.js were invisible to them - every shared
    product link showed the generic store cover photo. This is the
    server-rendered fix: the exact tags a crawler reads are rewritten
    before the response ever leaves the server, for the one request that
    matters (?slug=<product>), while legacy ?id=<product> links also resolve
    and every other visit to product.html
    (no id, or an id no longer in the catalogue) keeps the generic tags
    unchanged.
    """
    slug = catalog_mod.public_slug(product)
    name = _product_display_name(p=product) or "Product"
    image = _abs_asset_url(product.get("image"))
    price = _product_price_line(product)
    options = _product_options_line(product)
    origin = (Config.SITE_ORIGIN or "").rstrip("/")
    url = f"{origin}/products/{quote(slug, safe='')}"
    desc_bits = [b for b in (price, options) if b]
    description = (" · ".join(desc_bits) or "Shop this piece at Jaura Store in Naira or CFA.")
    description = f"{description} — jaurastore.com.ng"
    title = f"{name} · Jaura Store"

    def esc(s):
        return html.escape(str(s), quote=True)

    out = html_text
    out = re.sub(r"<title>.*?</title>", f"<title>{esc(title)}</title>", out, count=1, flags=re.S)
    out = re.sub(r'(<meta name="description" content=")[^"]*(" />)',
                 rf"\g<1>{esc(description)}\g<2>", out, count=1)
    out = re.sub(r'(<link rel="canonical" href=")[^"]*(" />)',
                 rf"\g<1>{esc(url)}\g<2>", out, count=1)
    out = re.sub(r'(<meta property="og:title" content=")[^"]*(" />)',
                 rf"\g<1>{esc(title)}\g<2>", out, count=1)
    out = re.sub(r'(<meta property="og:description" content=")[^"]*(" />)',
                 rf"\g<1>{esc(description)}\g<2>", out, count=1)
    out = re.sub(r'(<meta property="og:url" content=")[^"]*(" />)',
                 rf"\g<1>{esc(url)}\g<2>", out, count=1)
    out = re.sub(r'(<meta property="og:image" content=")[^"]*(" />)',
                 rf"\g<1>{esc(image)}\g<2>", out, count=1)
    out = re.sub(r'(<meta name="twitter:title" content=")[^"]*(" />)',
                 rf"\g<1>{esc(title)}\g<2>", out, count=1)
    out = re.sub(r'(<meta name="twitter:description" content=")[^"]*(" />)',
                 rf"\g<1>{esc(description)}\g<2>", out, count=1)
    out = re.sub(r'(<meta name="twitter:image" content=")[^"]*(" />)',
                 rf"\g<1>{esc(image)}\g<2>", out, count=1)
    return out


def create_app():
    app = Flask(__name__, static_folder=None)

    # Global Python equivalents for uncaught process/thread/async errors.
    # They record diagnostics and then chain to the runtime's default handler;
    # fatal errors are not swallowed because Gunicorn must be able to recycle
    # a damaged worker. Testing deliberately leaves pytest's own hooks alone.
    try:
        import runtime_errors
        runtime_errors.install(app.logger)
    except Exception as exc:
        app.logger.warning("runtime error hooks could not be installed: %s", exc)

    # A production deployment without SECRET_KEY set used to boot silently
    # with the repository-public development default - the key that signs
    # admin session cookies. It now boots with a random per-boot secret
    # (never forgeable) and this loud warning so the owner fixes the env.
    if getattr(Config, "SECRET_KEY_IS_RANDOM_FALLBACK", False):
        app.logger.critical(
            "SECRET_KEY is not set: this deployment is signing sessions with a "
            "random key generated at boot. Admins will have to sign in again "
            "after every restart. Set a fixed SECRET_KEY in the host's "
            "environment (e.g. `python3 -c \"import secrets; print(secrets.token_hex(32))\"`).")

    app.config.from_mapping(
        SECRET_KEY=Config.SECRET_KEY,
        ENV=Config.ENV,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(Config.ENV == "production"),
        SESSION_COOKIE_NAME="jaura_session",
        PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME,
        # Allows a 50 MB video plus multipart/form-data overhead,
        # and gives receipt/PDF uploads enough headroom before validation.
        MAX_CONTENT_LENGTH=96 * 1024 * 1024,
    )
    import customers as customers_mod
    customers_mod.register_routes(api)
    app.register_blueprint(api)

    init_db()
    try:
        migrate()                      # add columns added after the first release
        # Supabase auto-migration: PostgREST cannot run DDL, so a live
        # `site_settings` table missing a newer column (popup_banner_active on
        # a project created before the welcome pop-up) used to answer every
        # Admin save with a schema error. Apply the additive, idempotent
        # ALTERs on boot; a no-op when Supabase or a direct database
        # connection is not configured, and never blocks boot.
        try:
            import auto_migrate
            auto_migrate.run(app.logger)
        except Exception as exc:
            app.logger.warning("schema auto-migration skipped: %s", exc)
        analytics_mod.prune()          # drop raw analytics past the retention window
        # Categories are read live from the Supabase `categories` table in
        # production (no local file is written on boot). On the test/dev
        # local path the legacy growth_settings JSON mirror is restored so a
        # wiped disk still has the owner's list before the one-shot merge.
        try:
            if Config.ENV == "testing":
                from supabase_store import load_categories
                remote = load_categories()
                if remote:
                    import api as _api_mod
                    _api_mod._save_categories(remote, actor="supabase-restore")
            else:
                from supabase_store import enabled as _sb_enabled
                if _sb_enabled():
                    from supabase_store import load_categories_table
                    cats = load_categories_table()
                    app.logger.info(
                        "Supabase categories table: %s",
                        "ready (%d rows)" % len(cats) if cats is not None else "unavailable")
        except Exception as exc:
            app.logger.warning("category restore skipped: %s", exc)
        # Restore orders and receipts from Supabase so a redeploy that wiped the
        # disk still has them. Never blocks boot on failure.
        try:
            from supabase_store import load_orders, load_receipts, load_customers
            from db import upsert_orders, upsert_receipts, upsert_customers
            customers_data = load_customers()
            if customers_data:
                saved_c = upsert_customers(customers_data)
                app.logger.info("restored %d customers from Supabase", saved_c)
            orders_data = load_orders()
            if orders_data:
                saved_o = upsert_orders(orders_data)
                app.logger.info("restored %d orders from Supabase", saved_o)
            receipts_data = load_receipts()
            if receipts_data:
                saved_r = upsert_receipts(receipts_data)
                app.logger.info("restored %d receipts from Supabase", saved_r)
        except Exception as exc:
            app.logger.warning("orders/receipts restore skipped: %s", exc)
        # Restore store insights from Supabase so a redeploy that wiped the
        # disk still has the retention window. A no-op when the local window
        # is intact; never blocks boot on failure.
        try:
            restored = analytics_mod.restore_from_supabase()
            if restored:
                app.logger.info("restored %d analytics rows from Supabase", restored)
            # Customer search history is a separate table with the same
            # deal: mirrored on write, copied back when the local window
            # is empty, so a deploy never erases the demand signal.
            restored_q = analytics_mod.restore_searches_from_supabase()
            if restored_q:
                app.logger.info("restored %d search rows from Supabase", restored_q)
            # The lifetime counters are restored on EVERY boot (not only
            # after a wipe): they are monotonic, merged by taking the
            # maximum, so this can only ever move them forward. Without it
            # the headline totals restart at zero on a fresh disk.
            moved = analytics_mod.restore_counters()
            if moved:
                app.logger.info("restored %d lifetime analytics counter(s)", moved)
        except Exception as exc:
            app.logger.warning("analytics restore skipped: %s", exc)
        # Restore the local variant-stock cache for compatibility with local
        # admin tooling after an ephemeral-disk deploy. Production request
        # reads still use the strict Supabase path in api.py.
        try:
            from supabase_store import load_variant_stock
            from db import upsert_variant_stock
            stock_data = load_variant_stock()
            if stock_data:
                saved_s = upsert_variant_stock(stock_data)
                app.logger.info("restored %d variant stock rows from Supabase", saved_s)
        except Exception as exc:
            app.logger.warning("variant stock cache restore skipped: %s", exc)
        # Restore the growth module from Supabase: the referral settings the
        # owner configured (thresholds, percentages and toggles),
        # issued referral codes, coupons and product reviews. SQLite is only
        # the working copy - without this, a redeploy resets the settings to
        # defaults and the codes/coupons/reviews disappear from the store.
        try:
            from supabase_store import (load_growth_settings, load_coupons,
                                        load_referral_codes, load_product_reviews,
                                        load_product_reviews_table)
            from db import (restore_growth_settings, upsert_coupons,
                            upsert_referral_codes, upsert_product_reviews)
            gs = load_growth_settings()
            if gs:
                app.logger.info("restored %d growth settings from Supabase",
                                restore_growth_settings(gs))
            coupons = load_coupons()
            if coupons:
                app.logger.info("restored %d coupons from Supabase", upsert_coupons(coupons))
            codes = load_referral_codes()
            if codes:
                app.logger.info("restored %d referral codes from Supabase",
                                upsert_referral_codes(codes))
            # product_reviews: the real table is the source of truth. The
            # legacy growth_settings blob is only a fallback for installs that
            # have not run the migration yet, and is never deleted here.
            reviews = load_product_reviews_table()
            if reviews:
                app.logger.info("restored %d product reviews from "
                                "supabase:product_reviews",
                                upsert_product_reviews(reviews))
            else:
                legacy = load_product_reviews()
                if legacy:
                    app.logger.warning(
                        "product_reviews table empty; restored %d reviews from "
                        "the legacy growth_settings blob - run the review "
                        "migration to move them into the table", len(legacy))
                    upsert_product_reviews(legacy)
        except Exception as exc:
            app.logger.warning("growth restore skipped: %s", exc)
        # One-shot category merge (folds the old `nails` / `packaging`
        # categories, renames `gift-set`, and re-points legacy products). Run
        # on the deployed environments only so the local repo's category table
        # is never rewritten by the test suite or local dev.
        if Config.ENV in ("production", "staging"):
            import catalog as _catalog_mod
            try:
                if _catalog_mod.merge_categories():
                    app.logger.info("category merge applied on boot (category_merge_v2)")
            except Exception as exc:
                app.logger.warning("category merge skipped: %s", exc)
            # Test-suite products (jau-stock-*, jau-mirror-*, "Stock Test …")
            # are never shop pieces, but a test run pointed at the live site
            # (or a bulk catalog push) can leave them in Supabase, and the
            # suite recreates them on every run. Tombstone whatever is there
            # on boot; merged() filters them on every read regardless, so they
            # cannot reach the storefront even if this write fails.
            try:
                fixtures = _catalog_mod.purge_test_fixtures()
                if fixtures.get("found"):
                    app.logger.info(
                        "test products tombstoned on boot: %s",
                        ", ".join(str(x) for x in fixtures["found"]))
            except Exception as exc:
                app.logger.warning("test-product purge skipped: %s", exc)
            # Products the owner deleted for good (catalog.PERMANENTLY_REMOVED_*)
            # are hard-deleted from PostgreSQL and purged from Storage on every
            # boot, so a stale mirror or a restored backup can never put one
            # back on the storefront. merged() also filters them on every read.
            try:
                purged = _catalog_mod.purge_permanently_removed()
                if purged.get("deleted") or purged.get("files"):
                    app.logger.info(
                        "permanently removed products purged: %s (%d file(s))",
                        ", ".join(str(x) for x in purged.get("deleted") or []),
                        purged.get("files") or 0)
            except Exception as exc:
                app.logger.warning("permanent-removal purge skipped: %s", exc)
            # One-shot seed: re-insert a shipped default category the live
            # categories table lost (perfume vanished from the shop pills,
            # the categories page and the menu; no product carried it, so a
            # table rewrite dropped it). Marker-guarded in Supabase
            # growth_settings - it runs once, and if the owner later deletes
            # the category on purpose it stays deleted.
            try:
                import category_seed
                status, ids = category_seed.seed_missing_default_categories()
                if status == "seeded":
                    app.logger.info(
                        "default categories restored to the live table: %s",
                        ", ".join(ids))
                elif status in ("unavailable", "failed"):
                    app.logger.warning(
                        "default category seed %s (retries on the next boot)", status)
            except Exception as exc:
                app.logger.warning("default category seed skipped: %s", exc)
    except Exception as exc:           # never let housekeeping stop the boot
        app.logger.warning("startup maintenance skipped: %s", exc)
    # midnight products/orders backup
    if Config.SCHEDULER_ENABLED and Config.ENV not in ("testing",):
        try:
            import scheduler
            scheduler.start(app)
        except Exception as exc:       # pragma: no cover
            app.logger.warning("scheduler not started: %s", exc)

    def _flat_fallback(path):
        """Resolve a sub-directory asset reference to its flat repo-root file.

        This project ships its assets flat at the repo root (style.css,
        js/*.js, images/**, data/seed.json, ...) but the pages reference them
        with a route prefix (css/style.css, js/app.js, images/products/x.jpg,
        data/seed.json). Walk the path components from the right until we find
        a real file at the root, so existing links work without duplicating or
        moving any asset. Returns a path relative to ROOT, or None.
        """
        parts = [p for p in path.split("/") if p and p not in (".", "..")]
        for i in range(len(parts), 0, -1):
            candidate = os.path.join(ROOT, *parts[i - 1:])
            if os.path.isfile(candidate):
                return os.path.join(*parts[i - 1:])
        return None

    def static_for(path):
        """Serve a file from the repo root, refusing anything outside it - or
        anything that is not a storefront asset (see servable())."""
        full = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not full.startswith(ROOT):
            return None
        # Traversal first (`css/../config.py` collapses to `config.py`), then
        # the allowlist, so a blocked file is blocked however it is spelled.
        if not servable(os.path.relpath(full, ROOT)):
            return None
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
            if not servable(os.path.relpath(full, ROOT)):
                return None
        if not os.path.isfile(full):
            rel = _flat_fallback(path)
            if rel is None or not servable(rel):
                return None
            full = os.path.normpath(os.path.join(ROOT, rel))
        if not os.path.isfile(full):
            return None
        resp = send_from_directory(os.path.dirname(full), os.path.basename(full))
        # The shared ?v=<digits> token is an immutability promise: that exact
        # URL is only ever handed out for one build of the file, so browsers
        # (and any CDN in front of the dyno) may keep it for a year without
        # revalidating - on a 4G phone that is the difference between a
        # ~5s first paint and an instant repeat visit. Every asset whose
        # content changes ships a new token instead. Anything else - HTML
        # pages, sw.js, untokened links - keeps Flask's default no-cache so
        # a visitor always fetches fresh markup on every navigation.
        if re.fullmatch(r"\d+", request.args.get("v", "")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif os.path.relpath(full, ROOT).replace(os.sep, "/") == "index.html":
            # The homepage is static release markup. Let the browser keep it
            # briefly and let the public edge serve it for an hour (including
            # stale-while-revalidate/stale-if-error) so a Google-result click
            # does not wait on an application round-trip or a cold instance.
            # Versioned CSS/JS remain immutable and live catalogue/site data
            # still refresh through their uncached APIs after first paint.
            resp.headers["Cache-Control"] = (
                "public, max-age=300, s-maxage=3600, "
                "stale-while-revalidate=86400, stale-if-error=604800")
        return resp

    @app.after_request
    def _compress(resp):
        """gzip text responses (stdlib only, no extra dependency).

        The catalogue answer is ~125 KB of JSON and every visitor fetches it.
        On a 3G phone that is most of the wait before the first product is
        visible; gzipped it is roughly a tenth of that. Skipped when the
        client did not offer gzip, when the body is small enough that
        compressing costs more than it saves, for already-compressed media,
        for streamed/direct-passthrough responses, and for 304s (which carry
        no body). Vary: Accept-Encoding is set so a shared cache can never
        hand a gzipped body to a client that cannot read it.
        """
        try:
            resp.headers.add("Vary", "Accept-Encoding")
            if "gzip" not in (request.headers.get("Accept-Encoding") or "").lower():
                return resp
            if resp.status_code < 200 or resp.status_code >= 300:
                return resp
            if resp.headers.get("Content-Encoding"):
                return resp
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ok_type = (ctype.startswith("text/")
                       or ctype in ("application/json", "application/javascript",
                                    "application/xml", "image/svg+xml")
                       or ctype.endswith("+json") or ctype.endswith("+xml"))
            if not ok_type:
                return resp
            # Static files (CSS/JS) are sent as a streamed file wrapper. The
            # stylesheet alone is ~190 KB and it blocks the first paint, so
            # they are exactly the responses worth compressing: read the body
            # in and turn passthrough off before replacing it.
            if resp.direct_passthrough:
                resp.direct_passthrough = False
            body = resp.get_data()
            if len(body) < 1024:
                return resp
            packed = gzip.compress(body, 6)
            if len(packed) >= len(body):
                return resp
            resp.set_data(packed)
            resp.headers["Content-Encoding"] = "gzip"
            resp.headers["Content-Length"] = str(len(packed))
        except Exception:
            # Compression is an optimisation: never let it break a response.
            return resp
        return resp

    @app.after_request
    def _headers(resp):
        return sec.apply_headers(resp)

    @app.route("/health")
    @app.route("/healthz")
    def healthz():
        # Render requires a fresh 2xx/3xx response within five seconds. Ping
        # the real database (with a bounded Supabase probe) and verify Python
        # request-thread scheduling on every check; a stuck DB must return 500
        # so Render's health monitor can take the instance out of rotation and
        # restart it. Never let a CDN cache a previous 200.
        import health_checks
        body, status = health_checks.health_report()
        resp = jsonify(body)
        resp.status_code = status
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    @app.route("/uploads/<path:p>")
    def uploaded(p):
        """Serve stored images (payment proofs, product photos).

        Requests are admin-restricted for proofs (they contain customer
        payment evidence); product photos stay public.

        Cache policy is DELIBERATELY short for photos. A photo the owner
        deletes or replaces must stop showing on a customer's phone, and the
        old "keep it for a year" instinct is exactly how a deleted product
        image kept appearing on a device that had already seen it. Five
        minutes still covers a browsing session (a grid opens, a product page
        taps through, a cart refreshes) while a purge becomes invisible by the
        next visit; ``must-revalidate`` forbids serving a stale copy without
        asking. Proofs are never cached at all - they are private payment
        evidence, not shop artwork.
        """
        key = (p or "").lstrip("/")
        is_proof = key.split("/", 1)[0].lower() == "proofs"
        if is_proof and not authmod.current_admin():
            abort(404)
        if Config.ENV == "testing":
            full = storage.resolve_local(key)
            if full:
                response = send_from_directory(os.path.dirname(full), os.path.basename(full))
                if os.path.splitext(full)[1].lower() in (".pdf", ".doc", ".docx"):
                    response.headers["Content-Disposition"] = "attachment"
                response.headers["Cache-Control"] = (
                    "no-store, no-cache, must-revalidate" if is_proof
                    else "public, max-age=300, must-revalidate")
                return response
        # Production uploads are Supabase public HTTPS URLs; the dyno never
        # reads or serves an upload from its ephemeral filesystem.
        redirect_to = storage.public_redirect_for(key)
        if redirect_to:
            response = redirect(redirect_to, code=302)
            # The 302 is cacheable by default (a browser is free to keep a
            # 301/302 for a long time), which would pin a device to the direct
            # object URL - and its own long cache - even after the photo is
            # gone. Bound it here too.
            response.headers["Cache-Control"] = (
                "no-store, no-cache, must-revalidate" if is_proof
                else "public, max-age=300, must-revalidate")
            return response
        abort(404)


    @app.route("/sitemap.xml")
    def sitemap():
        """The dynamic sitemap: rebuilt on every request from the live
        category table and product catalogue (see build_sitemap). Replaces
        the old committed sitemap.xml, which went stale and kept listing
        categories the owner had deleted."""
        resp = Response(build_sitemap(), mimetype="application/xml; charset=utf-8")
        # short cache: search engines re-crawl daily, and a 5-minute-old
        # sitemap is indistinguishable from a live one while keeping the
        # dyno from rebuilding it on every hit
        resp.headers["Cache-Control"] = "public, max-age=300"
        return resp

    @app.route("/")
    def index():
        return static_for("index.html")

    @app.route("/product.html")
    def product_page():
        """Same static page for everyone, except a resolved slug/id/sku gets
        the product's own Open Graph / Twitter tags from inject_product_meta().

        static_for() is still called first (and its response returned
        unchanged) for every other case - no id, an id that no longer
        exists, or the file itself missing - so nothing about normal
        serving, caching or the servable() allowlist changes. Only a
        genuine product hit re-reads the file directly: send_file()
        responses stream with direct_passthrough=True and cannot be
        decoded with get_data().
        """
        resp = static_for("product.html")
        slug = (request.args.get("slug") or "").strip()
        pid = (request.args.get("id") or "").strip()
        sku = (request.args.get("sku") or "").strip()
        requested = slug or pid or sku
        if resp is None or resp.status_code != 200 or not requested:
            return resp
        try:
            products = catalog_mod.merged()
        except Exception:
            products = []
        product = None
        if slug:
            product = next((p for p in products
                            if slug in {str((p or {}).get("slug") or "").strip(),
                                        catalog_mod.public_slug(p)}), None)
        elif pid:
            # Preserve canonical-ID precedence, then old slug and legacyId
            # aliases; an alias can never shadow another product's primary key.
            product = next((p for p in products
                            if str((p or {}).get("id") or "").strip() == pid), None)
            if product is None:
                product = next((p for p in products
                                if pid in {str((p or {}).get("slug") or "").strip(),
                                           catalog_mod.public_slug(p)}), None)
            if product is None:
                product = next((p for p in products
                                if str((p or {}).get("legacyId") or "").strip() == pid), None)
        else:
            product = next((p for p in products
                            if str((p or {}).get("sku") or "").strip() == sku), None)
        if not product:
            return resp
        try:
            with open(os.path.join(ROOT, "product.html"), "r", encoding="utf-8") as f:
                body = inject_product_meta(f.read(), product)
        except Exception:
            return resp
        out = Response(body, mimetype="text/html; charset=utf-8")
        return out

    @app.route("/products/<slug>")
    def product_slug_page(slug):
        """Serve a product at its clean, readable `/products/<slug>` URL.

        Old `product.html?slug=...` links remain supported, but all generated
        links, canonical tags and sitemap entries use this path. An imported
        stored slug is accepted as an incoming alias and permanently redirected
        to the current public slug, so legacy Wix/import artifacts are never
        re-published in a share URL.
        """
        resp = static_for("product.html")
        if resp is None or resp.status_code != 200:
            return resp if resp is not None else ("Not found", 404)
        try:
            products = catalog_mod.merged()
        except Exception:
            products = []
        wanted = str(slug or "").strip()
        # Exact catalog IDs and legacy IDs remain valid incoming aliases even
        # when catalogue normalization has already replaced their old slug.
        product = next((p for p in products
                        if wanted and wanted in {
                            str((p or {}).get("id") or "").strip(),
                            str((p or {}).get("legacyId") or "").strip(),
                        }), None)
        if product is None:
            product = next((p for p in products
                            if wanted in {str((p or {}).get("slug") or "").strip(),
                                          catalog_mod.public_slug(p)}), None)
        if product is None:
            resp.status_code = 404
            return resp
        clean_slug = catalog_mod.public_slug(product)
        if wanted != clean_slug:
            return redirect("/products/" + quote(clean_slug, safe=""), code=301)
        try:
            with open(os.path.join(ROOT, "product.html"), "r", encoding="utf-8") as f:
                body = inject_product_meta(f.read(), product)
        except Exception:
            return resp
        return Response(body, mimetype="text/html; charset=utf-8")

    @app.route(LEGACY_PREFIX)
    @app.route(LEGACY_PREFIX + "/")
    def legacy_index():
        return redirect("/")

    @app.route(LEGACY_PREFIX + "/<path:p>")
    def legacy_path(p):
        return redirect("/" + p)

    def _account_page():
        resp = static_for("account.html")
        if resp is None:
            return "Not found", 404
        return resp

    @app.route("/account")
    @app.route("/account/")
    def account_home():
        return _account_page()

    @app.route("/account/logout")
    def account_logout_get():
        session.pop("customer_id", None)
        return _account_page()

    @app.route("/admin/accounting")
    @app.route("/admin/accounting/")
    def admin_accounting_page():
        """Serve the standalone spreadsheet without booting the main Admin SPA."""
        resp = static_for("accounting.html")
        if resp is None:
            return "Not found", 404
        return resp

    @app.route("/admin")
    @app.route("/admin/")
    @app.route("/admin/<path:rest>")
    def admin_spa(rest=""):
        """Serve the single-page portal on its real desk URLs.

        /admin.html has always been the portal, but a link you can send - or
        bookmark - should not have to be a query string. The whole portal is
        one static shell whose tabs are client-side, so every /admin/... path
        serves the same file and js/admin.js opens the matching desk (see
        pathDesk()), including /admin/marketing/broadcast.

        Deliberately outside any auth gate: the shell carries no data at all,
        and every /api/admin/... call it makes is authenticated on its own.
        """
        return static_for("admin.html")

    @app.route("/account/<path:rest>")
    def account_spa(rest):
        return _account_page()

    @app.route("/<path:p>")
    def catch_all(p):
        if p.startswith("api/"):
            return jsonify(ok=False, error="Not found"), 404
        resp = static_for(p)
        if resp is None:
            resp = static_for("404.html")
            if resp is None:
                return "Not found", 404
            return resp, 404
        return resp

    @app.errorhandler(413)
    def _too_big(_e):
        return jsonify(ok=False,
                       error="That upload is too large. Payment proofs can be up to 24 MB, product photos up to 6 MB, and videos up to 50 MB."), 413

    @app.errorhandler(404)
    def _404(e):
        return jsonify(ok=False, error="Not found"), 404

    @app.errorhandler(500)
    def _500(e):
        try:
            import observability
            original = getattr(e, "original_exception", None) or e
            observability.record_failure("flask.unhandled_exception", original,
                                         logger=app.logger,
                                         payload_id=(request.path or "")[:120])
        except Exception:
            pass
        return jsonify(ok=False, error="Server error"), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=(Config.ENV != "production"))
