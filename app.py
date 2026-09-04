"""J Aura Store - Flask app: serves the storefront plus a JSON API."""
import os, html, datetime
from urllib.parse import quote
from flask import (Flask, send_from_directory, jsonify, request, redirect,
                   Response, abort, make_response as _make_response)
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
    (shop.html?cat=<id>) and one per LIVE product (product.html?id=<id>).

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
        pid = str((p or {}).get("id") or "").strip()
        if not pid:
            continue
        urls.append(_sitemap_entry(
            url_for("/product.html?id=" + quote(pid, safe="")), today, "weekly", "0.6"))

    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(urls) + "\n</urlset>\n")


def create_app():
    app = Flask(__name__, static_folder=None)
    app.config.from_mapping(
        SECRET_KEY=Config.SECRET_KEY,
        ENV=Config.ENV,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=(Config.ENV == "production"),
        SESSION_COOKIE_NAME="jaura_session",
        PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME,
        # allows product + hero videos up to 40 MB (storage.MAX_VIDEO_BYTES)
        MAX_CONTENT_LENGTH=45 * 1024 * 1024,
    )
    app.register_blueprint(api)

    init_db()
    try:
        migrate()                      # add columns added after the first release
        analytics_mod.prune()          # drop raw analytics past the retention window
        # Restore the category table from Supabase (growth_settings) so a
        # redeploy that wiped the disk still has the owner's list. Must run
        # before the one-shot category merge.
        try:
            from supabase_store import load_categories
            remote = load_categories()
            if remote:
                import api as _api_mod
                _api_mod._save_categories(remote, actor="supabase-restore")
                app.logger.info("category table restored from growth_settings")
        except Exception as exc:
            app.logger.warning("category restore skipped: %s", exc)
        # Restore orders and receipts from Supabase so a redeploy that wiped the
        # disk still has them. Never blocks boot on failure.
        try:
            from supabase_store import load_orders, load_receipts
            from db import upsert_orders, upsert_receipts
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
        # Restore per-variant stock levels: the Stock panel is SQLite-only, so
        # without this a redeploy that wipes the disk resets every quantity.
        try:
            from supabase_store import load_variant_stock
            from db import upsert_variant_stock
            stock_data = load_variant_stock()
            if stock_data:
                saved_s = upsert_variant_stock(stock_data)
                app.logger.info("restored %d variant stock rows from Supabase", saved_s)
        except Exception as exc:
            app.logger.warning("variant stock restore skipped: %s", exc)
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
    except Exception as exc:           # never let housekeeping stop the boot
        app.logger.warning("startup maintenance skipped: %s", exc)
    authmod.ensure_seed_admins()

    # One-shot access recovery: when the admin password is lost and no reset
    # code can be received, the shared admin password is forced once on boot to
    # ADMIN_BOOTSTRAP_PASSWORD (or Config.BOOTSTRAP_ADMIN_PASSWORD when the
    # variable is absent). auth.apply_bootstrap_password stamps an
    # `admin_bootstrap_applied` marker so this can never fire twice - sign in,
    # change the password from the admin portal, and reboot as often as needed.
    # Skipped under FLASK_ENV=testing so test passwords are never overwritten.
    if Config.ENV != "testing":
        try:
            if authmod.apply_bootstrap_password(
                    os.environ.get("ADMIN_BOOTSTRAP_PASSWORD", "")
                    or Config.BOOTSTRAP_ADMIN_PASSWORD):
                app.logger.warning(
                    "admin bootstrap password applied once - sign in and change "
                    "it from the admin portal now")
        except Exception as exc:                       # pragma: no cover
            app.logger.warning("admin bootstrap password not applied: %s", exc)

    # abandoned-cart reminders + the midnight products/orders backup
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
        """Serve a file from the repo root, refusing anything outside it."""
        full = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
        if not full.startswith(ROOT):
            return None
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            rel = _flat_fallback(path)
            if rel is None:
                return None
            full = os.path.normpath(os.path.join(ROOT, rel))
        return send_from_directory(os.path.dirname(full), os.path.basename(full))

    @app.after_request
    def _headers(resp):
        return sec.apply_headers(resp)

    @app.route("/healthz")
    def healthz():
        # no-store: a CDN (Cloudflare in front of the custom domain, and the
        # edge that sits in front of *.onrender.com) must never answer the
        # keep-alive ping from its own cache. A cached 200 never reaches the
        # dyno, so Render would still count the service as idle and spin it
        # down - and the next real visitor eats the ~50s cold start.
        resp = jsonify(ok=True, env=Config.ENV)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    @app.route("/uploads/<path:p>")
    def uploaded(p):
        """Serve stored images (payment proofs, product photos).

        Requests are admin-restricted for proofs (they contain customer
        payment evidence); product photos stay public.
        """
        key = (p or "").lstrip("/")
        if key.split("/", 1)[0].lower() == "proofs" and not authmod.current_admin():
            abort(404)
        full = storage.resolve_local(key)
        if not full:
            abort(404)
        resp = _make_response(send_from_directory(os.path.dirname(full), os.path.basename(full)))
        resp.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
        # Every non-image / non-video type (PDF, DOC, DOCX, executable-like
        # containers) is forced to download. A stored .html/.svg must never be
        # served inline in this origin; attachments cannot execute scripts here.
        ext = os.path.splitext(full)[1].lstrip(".").lower()
        if ext and not storage.is_inline_renderable(ext):
            resp.headers["Content-Disposition"] = "attachment"
        return resp

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

    @app.route(LEGACY_PREFIX)
    @app.route(LEGACY_PREFIX + "/")
    def legacy_index():
        return redirect("/")

    @app.route(LEGACY_PREFIX + "/<path:p>")
    def legacy_path(p):
        return redirect("/" + p)

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
                       error="That upload is too large. Please send an image under 6 MB or a video/doc under 40 MB."), 413

    @app.errorhandler(404)
    def _404(e):
        return jsonify(ok=False, error="Not found"), 404

    @app.errorhandler(500)
    def _500(e):
        return jsonify(ok=False, error="Server error"), 500

    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port, debug=(Config.ENV != "production"))
