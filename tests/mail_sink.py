"""A tiny SMTP server used by the tests to prove mail really leaves the app.

It speaks just enough SMTP for smtplib: greeting, EHLO, MAIL FROM, RCPT TO,
DATA, QUIT. It never delivers anything - it keeps every message in
``server.messages`` so a test can open it and check the attachment is the
original file, byte for byte.

Standard library only, so the delivery check runs in CI with no setup.
"""
import socketserver
import threading


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"220 test.local SMTP ready\r\n")
        mail_from, rcpt, lines = "", [], []
        in_data = False
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            if in_data:
                if raw in (b".\r\n", b".\n"):
                    self.server.messages.append(
                        {"from": mail_from, "to": list(rcpt),
                         "data": "".join(lines).encode("utf-8", "surrogateescape")})
                    self.wfile.write(b"250 ok queued\r\n")
                    in_data, lines, rcpt = False, [], []
                    continue
                lines.append(raw.decode("utf-8", "surrogateescape")
                             .lstrip(".") if raw.startswith(b"..") else
                             raw.decode("utf-8", "surrogateescape"))
                continue
            cmd = raw.decode("utf-8", "surrogateescape").strip()
            up = cmd.upper()
            if up.startswith("EHLO") or up.startswith("HELO"):
                self.wfile.write(b"250-test.local\r\n250 SIZE 10485760\r\n")
            elif up.startswith("MAIL FROM"):
                mail_from = cmd[10:].strip()
                self.wfile.write(b"250 ok\r\n")
            elif up.startswith("RCPT TO"):
                rcpt.append(cmd[8:].strip())
                self.wfile.write(b"250 ok\r\n")
            elif up.startswith("DATA"):
                in_data = True
                self.wfile.write(b"354 end with .\r\n")
            elif up.startswith("RSET"):
                rcpt, lines = [], []
                self.wfile.write(b"250 ok\r\n")
            elif up.startswith("QUIT"):
                self.wfile.write(b"221 bye\r\n")
                return
            else:
                self.wfile.write(b"250 ok\r\n")


class MailSink(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port=0):
        """port=0 picks a free port (used by the tests)."""
        super().__init__(("127.0.0.1", port), _Handler)
        self.messages = []

    @property
    def host(self):
        return "127.0.0.1"

    @property
    def port(self):
        return self.server_address[1]

    def __enter__(self):
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        self._thread = t
        return self

    def __exit__(self, *exc):
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=2)
        return False
