#!/usr/bin/env python3
"""GitHub device-flow login for the local `gh` CLI.

Prints the one-time code + URL, then polls until the user authorizes, and hands
the token straight to `gh auth login --with-token` (never printed to stdout).
"""
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CLIENT_ID = "178c6fc778ccc68e1d6a"  # public gh CLI OAuth app id
SCOPE = "repo workflow read:org"

STATE_FILE = "/tmp/gh_device_state.json"


def post(url, fields):
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main():
    device = post("https://github.com/login/device/code",
                  {"client_id": CLIENT_ID, "scope": SCOPE})
    with open(STATE_FILE, "w") as fh:
        json.dump(device, fh)
    print("USER_CODE=%s" % device["user_code"])
    print("URL=%s" % device["verification_uri"])
    sys.stdout.flush()

    interval = int(device.get("interval", 5))
    deadline = time.time() + int(device.get("expires_in", 900))
    while time.time() < deadline:
        time.sleep(interval)
        res = post("https://github.com/login/oauth/access_token", {
            "client_id": CLIENT_ID,
            "device_code": device["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        })
        if res.get("access_token"):
            token = res["access_token"]
            p = subprocess.run(["gh", "auth", "login", "--with-token"],
                               input=token.encode(), capture_output=True)
            if p.returncode != 0:
                print("gh auth login failed: %s" % p.stderr.decode()[:300])
                sys.exit(1)
            print("AUTH_OK")
            try:
                import os
                os.remove(STATE_FILE)
            except OSError:
                pass
            return
        err = res.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        print("ERROR=%s %s" % (err, res.get("error_description", "")))
        sys.exit(2)
    print("ERROR=expired")


if __name__ == "__main__":
    main()
