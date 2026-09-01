"""J Aura Store - Flask app: serves the storefront plus a JSON API."""
import os
from flask import (Flask, send_from_directory, jsonify, request, redirect,
                   Response, abort, make_response as _make_response)
from config import Config
from db import init_db, migrate
import security as sec
import auth as authmod
import storage
import analytics as analytics_mod
from api import api

ROOT = os.path.dirname(os.path.abspath(__file__))
LEGACY_PREFIX = "/JauraStore"   # keeps old project-site links working


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
        MAX_CONTENT_LENGTH=9 * 1024 * 1024,
    )
    app.register_blueprint(api)

    init_db()
    try:
        migrate()                      # add columns added after the first release
        analytics_mod.prune()          # drop raw analytics past the retention window
    except Exception as exc:           # never let housekeeping stop the boot
        app.logger.warning("startup maintenance skipped: %s", exc)
    authmod.ensure_seed_admins()

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
                       error="That upload is too large. Please send a file under 8 MB."), 413

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
