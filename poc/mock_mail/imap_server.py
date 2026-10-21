#!/usr/bin/env python3
"""
Minimal plaintext IMAP4rev1 server used as the PoC mail backend.

Why: the FlowCrypt iOS SetupImap screen lets you pick Security type = "none"
(ConnectionType.none -> MCOConnectionType nil -> plaintext). That removes any
TLS/certificate problem, so the whole PoC can run offline on a CI runner.

Every command received is appended to LOG_FILE as a timestamped JSON line.
The `LOGIN` command therefore records, in cleartext, the exact credentials the
app transmitted -- independent evidence from the app's own console log.

Usage: python3 imap_server.py [--port 143] [--log /tmp/mock_mail/imap.log]
"""
import argparse
import json
import re
import socketserver
import threading
import time
from datetime import datetime, timezone

LOG_FILE = "/tmp/mock_mail/imap.log"
_log_lock = threading.Lock()


def log(event, **fields):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
           "proto": "IMAP", "event": event, **fields}
    line = json.dumps(rec)
    with _log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")


class IMAPHandler(socketserver.StreamRequestHandler):
    def send(self, text):
        self.wfile.write((text + "\r\n").encode())
        self.wfile.flush()

    def handle(self):
        peer = "%s:%s" % self.client_address
        log("connect", peer=peer)
        self.send("* OK [CAPABILITY IMAP4rev1] mock imap ready")
        while True:
            raw = self.rfile.readline()
            if not raw:
                log("disconnect", peer=peer)
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                continue
            log("recv", line=line)

            m = re.match(r"^(\S+)\s+(\S+)(?:\s+(.*))?$", line)
            if not m:
                continue
            tag, cmd, rest = m.group(1), m.group(2).upper(), (m.group(3) or "")

            if cmd == "CAPABILITY":
                self.send("* CAPABILITY IMAP4rev1")
                self.send(f"{tag} OK CAPABILITY completed")
            elif cmd == "NOOP":
                self.send(f"{tag} OK NOOP completed")
            elif cmd in ("LOGIN",):
                self.send(f"{tag} OK LOGIN completed")
            elif cmd == "AUTHENTICATE":
                self.send("+ ")
                self.rfile.readline()
                self.send(f"{tag} OK AUTHENTICATE completed")
            elif cmd in ("SELECT", "EXAMINE"):
                self.send("* 0 EXISTS")
                self.send("* 0 RECENT")
                self.send("* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
                self.send(f"{tag} OK [READ-WRITE] {cmd} completed")
            elif cmd in ("LIST", "LSUB"):
                self.send('* LIST (\\HasNoChildren) "/" "INBOX"')
                self.send(f"{tag} OK {cmd} completed")
            elif cmd == "STATUS":
                self.send("* STATUS INBOX (MESSAGES 0 RECENT 0 UIDNEXT 1 UIDVALIDITY 1 UNSEEN 0)")
                self.send(f"{tag} OK STATUS completed")
            elif cmd in ("FETCH", "UID", "SEARCH", "STORE", "COPY", "EXPUNGE", "CHECK"):
                self.send(f"{tag} OK {cmd} completed")
            elif cmd == "ID":
                self.send('* ID ("name" "mock-imap")')
                self.send(f"{tag} OK ID completed")
            elif cmd == "CLOSE":
                self.send(f"{tag} OK CLOSE completed")
            elif cmd == "LOGOUT":
                self.send("* BYE mock imap logging out")
                self.send(f"{tag} OK LOGOUT completed")
                log("disconnect", peer=peer)
                return
            elif cmd == "STARTTLS":
                # deliberately unsupported: PoC uses Security type = none
                self.send(f"{tag} NO STARTTLS not supported by mock")
            else:
                self.send(f"{tag} OK {cmd} completed")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=143)
    ap.add_argument("--log", default=LOG_FILE)
    args = ap.parse_args()
    LOG_FILE = args.log
    with open(LOG_FILE, "w") as fh:
        fh.write("")
    srv = Server(("127.0.0.1", args.port), IMAPHandler)
    log("listening", port=args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
