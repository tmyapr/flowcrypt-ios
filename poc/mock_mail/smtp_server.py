#!/usr/bin/env python3
"""
Minimal plaintext SMTP server (AUTH LOGIN / AUTH PLAIN / plain mail) for the PoC.

The FlowCrypt SetupImap screen validates IMAP *and* SMTP on "Connect", so both
mock services must answer. Every command, plus every decoded AUTH credential,
is appended to LOG_FILE as a timestamped JSON line.

Usage: python3 smtp_server.py [--port 2525] [--log /tmp/mock_mail/smtp.log]
"""
import argparse
import base64
import json
import socketserver
import threading
from datetime import datetime, timezone

LOG_FILE = "/tmp/mock_mail/smtp.log"
_log_lock = threading.Lock()


def log(event, **fields):
    rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
           "proto": "SMTP", "event": event, **fields}
    line = json.dumps(rec)
    with _log_lock:
        print(line, flush=True)
        with open(LOG_FILE, "a") as fh:
            fh.write(line + "\n")


def b64dec(value):
    try:
        return base64.b64decode(value or "").decode("utf-8", "replace")
    except Exception:
        return "<undecodable>"


class SMTPHandler(socketserver.StreamRequestHandler):
    def send(self, text):
        self.wfile.write((text + "\r\n").encode())
        self.wfile.flush()

    def handle(self):
        peer = "%s:%s" % self.client_address
        log("connect", peer=peer)
        self.send("220 mock-smtp ESMTP ready")
        in_data = False
        while True:
            raw = self.rfile.readline()
            if not raw:
                log("disconnect", peer=peer)
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")

            if in_data:
                if line == ".":
                    in_data = False
                    self.send("250 2.0.0 queued as MOCK1")
                continue

            log("recv", line=line)
            verb = line.split(" ")[0].split(":")[0].upper()

            if verb in ("EHLO", "HELO"):
                self.send("250-mock-smtp greets you")
                self.send("250-AUTH LOGIN PLAIN")
                self.send("250 HELP")
            elif verb == "AUTH":
                parts = line.split(" ")
                mech = parts[1].upper() if len(parts) > 1 else ""
                if mech == "LOGIN":
                    self.send("334 VXNlcm5hbWU6")  # "Username:"
                    user = b64dec(self.rfile.readline().decode().strip())
                    log("auth_username", value=user)
                    self.send("334 UGFzc3dvcmQ6")  # "Password:"
                    password = b64dec(self.rfile.readline().decode().strip())
                    log("auth_password", value=password)
                    self.send("235 2.7.0 authentication succeeded")
                elif mech == "PLAIN":
                    payload = parts[2] if len(parts) > 2 else ""
                    if not payload:
                        self.send("334 ")
                        payload = self.rfile.readline().decode().strip()
                    decoded = b64dec(payload).split("\x00")
                    log("auth_plain", value=decoded)
                    self.send("235 2.7.0 authentication succeeded")
                else:
                    self.send("504 5.5.4 unrecognized mechanism")
            elif verb in ("MAIL", "RCPT", "RSET", "NOOP"):
                self.send("250 2.0.0 ok")
            elif verb == "DATA":
                self.send("354 end data with <CR><LF>.<CR><LF>")
                in_data = True
            elif verb == "QUIT":
                self.send("221 2.0.0 bye")
                log("disconnect", peer=peer)
                return
            else:
                self.send("250 2.0.0 ok")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global LOG_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=2525)
    ap.add_argument("--log", default=LOG_FILE)
    args = ap.parse_args()
    LOG_FILE = args.log
    with open(LOG_FILE, "w") as fh:
        fh.write("")
    srv = Server(("127.0.0.1", args.port), SMTPHandler)
    log("listening", port=args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
