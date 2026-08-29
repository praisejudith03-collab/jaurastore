#!/usr/bin/env python3
"""Serve J Aura Store at / and /JauraStore/ for the live preview."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from urllib.parse import unquote, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8080"))
PREFIX = "/JauraStore"


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def translate_path(self, path):
        parsed = urlparse(path)
        clean = unquote(parsed.path)
        if clean == PREFIX:
            clean = PREFIX + "/"
        if clean.startswith(PREFIX + "/"):
            clean = clean[len(PREFIX):] or "/"
        return super().translate_path(clean)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", PREFIX):
            loc = PREFIX + "/"
            if parsed.query:
                loc += "?" + parsed.query
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return super().do_GET()

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args), flush=True)


if __name__ == "__main__":
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"J Aura Store on http://0.0.0.0:{PORT}{PREFIX}/", flush=True)
    httpd.serve_forever()
