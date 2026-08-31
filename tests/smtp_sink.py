"""Run a local mailbox while you test the payment-receipt form by hand.

    python3 tests/smtp_sink.py 1025 /tmp/mail

Then set MAIL_MODE=smtp, SMTP_HOST=127.0.0.1 and SMTP_PORT=1025 in .env and
restart the server. Every receipt the site sends is written to /tmp/mail as a
real .eml file you can open in a mail client - attachment and all.

Nothing is forwarded to a real address. Standard library only.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mail_sink import MailSink  # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1025
    folder = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mail"
    os.makedirs(folder, exist_ok=True)

    with MailSink(port) as sink:
        print(f"SMTP sink listening on 127.0.0.1:{port} -> {folder}")
        print("Press Ctrl+C to stop.")
        written = 0
        try:
            while True:
                while written < len(sink.messages):
                    msg = sink.messages[written]
                    written += 1
                    name = f"msg-{written:03d}.eml"
                    with open(os.path.join(folder, name), "wb") as fh:
                        fh.write(msg["data"])
                    with open(os.path.join(folder, name + ".meta"), "w") as fh:
                        fh.write(f"from={msg['from']} to={','.join(msg['to'])}\n")
                    print(f"  saved {name}  -> {','.join(msg['to'])}")
                time.sleep(0.3)
        except KeyboardInterrupt:
            print("\nstopped.")


if __name__ == "__main__":
    main()
