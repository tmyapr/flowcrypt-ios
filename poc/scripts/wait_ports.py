#!/usr/bin/env python3
"""Wait until TCP ports accept connections (used to gate the mock servers).

Usage: wait_ports.py 1143 2525 [--timeout 30]
Exit 0 when all ports accept a connection, 1 otherwise.
"""
import socket
import sys
import time

args = [a for a in sys.argv[1:] if not a.startswith("--")]
timeout = 30
for a in sys.argv[1:]:
    if a.startswith("--timeout"):
        timeout = int(a.split("=", 1)[1]) if "=" in a else timeout
ports = [int(a) for a in args]
deadline = time.time() + timeout
while time.time() < deadline:
    bad = []
    for port in ports:
        try:
            socket.create_connection(("127.0.0.1", port), 2).close()
        except OSError as exc:
            bad.append((port, exc))
    if not bad:
        print("reachable: %s" % ", ".join(str(p) for p in ports))
        sys.exit(0)
    time.sleep(1)
print("NOT reachable: %s" % bad)
sys.exit(1)
