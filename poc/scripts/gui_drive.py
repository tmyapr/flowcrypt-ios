#!/usr/bin/env python3
"""
Drive the FlowCrypt iOS UI by clicking the Simulator window.

Why this exists: on the GitHub-hosted macOS image every Xcode lacks
lib_TestingInterop.dylib, so WebDriverAgent (and therefore Appium, and any
hand-written XCUITest bundle) cannot be installed. gui-probe proved that
cliclick + System Events do work on this runner, so the UI is driven at the
window level instead.

Coordinates in the action file are always expressed in DEVICE POINTS (the
numbers you would read off `xcrun simctl io booted screenshot` divided by the
device scale). This script converts them to screen coordinates using the live
Simulator window geometry, so a window that moves does not break the script.

Actions (JSON list):
  {"do": "click",  "x": 195, "y": 700}
  {"do": "type",   "text": "poc@mockmail.test"}
  {"do": "key",    "code": 36}              # hardware keycode, 36 = Return
  {"do": "swipe",  "x": 195, "y": 600, "to_y": 250, "steps": 12}
  {"do": "clickrow","row": 2}               # live-measured row, fails loudly if absent
  {"do": "clickgreen"}                      # live-measured green button, fails loudly
  {"do": "open_provider", "x": 200, "y": 629}  # tap "Other email provider" and retry
                                            # until the SetupImap form is really up
                                            # (green Connect below y=700 AND >=10 rows)
  {"do": "liveness"}                        # prove the framebuffer still updates
                                            # (terminate -> home screen -> relaunch);
                                            # a frozen framebuffer once produced a
                                            # 145s video of one static frame while
                                            # the app navigated underneath it
  {"do": "expect_login"}                    # the PoC gate: the mock IMAP log must
                                            # contain a cleartext LOGIN line, or the
                                            # run fails no matter how good the video
                                            # looks
  {"do": "devshot", "name": "baseline-empty"}  # device-pixel screenshot
  {"do": "assert_diff", "ref": "baseline-empty", "min_delta": 1.0}  # tripwire: the
                                            # screen must have changed (input landed)
  {"do": "sleep",  "seconds": 3}
  {"do": "shot",   "name": "after-connect"}
  {"do": "expect", "name": "setup-screen"}   # screenshot for human review

Usage:
  gui_drive.py --actions poc/ui_actions.json --out poc-out \
               --device-width 390 --device-height 844
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

SIM_WINDOW_TITLE = "Simulator"


def run(cmd, check=False, log=None):
    res = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                         text=True, check=check)
    if log is not None:
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        if out or err or res.returncode != 0:
            printable = " ".join(cmd) if isinstance(cmd, list) else cmd
            print(f"    $ {printable} -> rc={res.returncode}"
                  + (f" out={out[:200]}" if out else "")
                  + (f" err={err[:200]}" if err else ""), flush=True)
    return res


def say(msg):
    print(msg, flush=True)


def osa(script):
    return run(["osascript", "-e", script])


def window_rect():
    """Return (x, y, w, h) of the front Simulator window, in screen points."""
    out = osa(
        'tell application "System Events" to tell process "Simulator" '
        'to get {position, size} of window 1'
    ).stdout.strip()
    nums = [int(n) for n in re.findall(r"-?\d+", out)]
    if len(nums) != 4:
        raise RuntimeError(f"cannot read Simulator window geometry: {out!r}")
    return nums[0], nums[1], nums[2], nums[3]


def focus_simulator():
    run(["osascript", "-e", 'tell application "Simulator" to activate'])
    time.sleep(1.0)


def point_accurate():
    """Window > Point Accurate (Cmd+2) makes 1 device point == 1 screen point."""
    focus_simulator()
    run(["osascript", "-e",
         'tell application "System Events" to keystroke "2" using command down'])
    time.sleep(1.5)


def screenshot(path):
    run(["screencapture", "-x", path])


def measure_mapping(device_w, device_h, save_dir="."):
    """Locate the device screen inside a desktop screenshot.

    The window is 456x972 for a 402x874 device, but the leftover 98 points of
    chrome are not centred: ~80 of them sit above the screen (title bar plus
    bezel) and ~18 below. Guessing the split puts every click off by tens of
    points, so match the real device screenshot against the desktop capture
    using 1-D luminance profiles -- content independent and cheap.
    """
    from PIL import Image

    desktop = os.path.join(save_dir, "map-desktop.png")
    device = os.path.join(save_dir, "map-device.png")
    screenshot(desktop)
    run(["xcrun", "simctl", "io", "booted", "screenshot", device])

    x, y, w, h = window_rect()
    im = Image.open(desktop).convert("L")
    W, H = im.size
    x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
    w = max(1, min(w, W - x)); height = max(1, min(h, H - y))
    crop = im.crop((x, y, x + w, y + height))
    cw, ch = crop.size

    dev = Image.open(device).convert("L").resize((device_w, device_h))

    def row_profile(img):
        ww, hh = img.size
        px = img.load()
        step = 4
        cols = list(range(0, ww, step))
        return [sum(px[xx, yy] for xx in cols) / float(len(cols)) for yy in range(hh)]

    def col_profile(img):
        ww, hh = img.size
        px = img.load()
        step = 4
        rows = list(range(0, hh, step))
        return [sum(px[xx, yy] for yy in rows) / float(len(rows)) for xx in range(ww)]

    crows, ccols = row_profile(crop), col_profile(crop)
    drows, dcols = row_profile(dev), col_profile(dev)

    best_y = min(range(0, ch - device_h + 1),
                 key=lambda dy: sum(abs(drows[i] - crows[dy + i]) for i in range(0, device_h, 8)))
    best_x = min(range(0, cw - device_w + 1),
                 key=lambda dx: sum(abs(dcols[i] - ccols[dx + i]) for i in range(0, device_w, 8)))
    print(f"device screen found at window offset ({best_x},{best_y}) size {device_w}x{device_h}")
    return {"window": {"x": x, "y": y, "w": w, "h": height},
            "device": {"w": device_w, "h": device_h},
            "scale": 1.0,
            "origin": {"x": x + best_x, "y": y + best_y}}


def device_shot(path):
    run(["xcrun", "simctl", "io", "booted", "screenshot", path])
    return path


def detect_rows(path, device_h=874, thr=215, lo=110, hi=800, min_dark=6):
    """Vertical centres (device points) of the text rows in a form screenshot.

    The setup table does not open at a fixed scroll offset, so row positions must
    be measured instead of hardcoded. Once the fields contain values every row
    draws dark text and the list is stable: 1 Account info, 2 Email, 3 Username,
    4 Password, 5 Incoming, 6 IMAP server, 7 IMAP port, 8 Security, 9 Outgoing,
    10 SMTP server, 11 SMTP port, 12 Security.

    A freshly typed hostname can split into two dark bands - the text itself and
    its spellcheck underline ~8pt below it. Real rows sit ~55pt apart, so bands
    closer than 25pt are merged; without this, every clickrow after the first
    filled field lands one row off (run 4: "993" never reached the port field).
    """
    from PIL import Image

    im = Image.open(path).convert("L")
    w, h = im.size
    px = im.load()
    scale = h / float(device_h)
    out, start = [], None
    for yy in range(int(lo * scale), min(h, int(hi * scale))):
        dark = sum(1 for xx in range(0, w, 2) if px[xx, yy] < thr)
        if dark >= min_dark and start is None:
            start = yy
        elif dark < min_dark and start is not None:
            if yy - start >= 5:
                out.append(round((start + yy) / 2.0 / scale, 1))
            start = None
    merged = []
    for r in out:
        if merged and r - merged[-1] < 25:
            continue
        merged.append(r)
    return merged

def find_green(path, y_from=500, device_h=874):
    """(centre_x, centre_y, top, bottom) of the big green primary button.

    Returns None when no unambiguous green button is on screen. The bbox is
    needed because the button can extend into the bottom safe-area /
    home-gesture zone, where a centroid tap gets swallowed (run 5).
    """
    from PIL import Image

    im = Image.open(path).convert("RGB")
    w, h = im.size
    px = im.load()
    scale = h / float(device_h)
    pts = []
    for yy in range(int(y_from * scale), h, 2):
        for xx in range(0, w, 2):
            r, g, b = px[xx, yy]
            if g > 90 and g > r * 1.35 and g > b * 1.25:
                pts.append((xx, yy))
    if len(pts) < 500:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (round(sum(xs) / len(xs) / scale, 1),
            round(sum(ys) / len(ys) / scale, 1),
            round(min(ys) / scale, 1),
            round(max(ys) / scale, 1))


def shot_diff(p1, p2, w=402, h=874):
    """Mean absolute luminance difference between two screenshots."""
    from PIL import Image
    a = Image.open(p1).convert("L").resize((w, h))
    b = Image.open(p2).convert("L").resize((w, h))
    pa, pb = a.load(), b.load()
    total = n = 0
    for yy in range(0, h, 3):
        for xx in range(0, w, 3):
            total += abs(pa[xx, yy] - pb[xx, yy])
            n += 1
    return total / float(n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True)
    ap.add_argument("--out", default="poc-out")
    ap.add_argument("--device-width", type=int, default=390)
    ap.add_argument("--device-height", type=int, default=844)
    ap.add_argument("--map", default=None, help="write measured mapping here")
    ap.add_argument("--udid", default=None, help="simulator UDID (needed by liveness)")
    ap.add_argument("--bundle-id", default=None, help="app bundle id (needed by liveness)")
    ap.add_argument("--console-log", default=None,
                    help="stamped console log; liveness appends a fresh "
                         "--console-pty pipe to it after relaunching the app")
    ap.add_argument("--imap-log", default="/tmp/mock_mail/imap.log")
    ap.add_argument("--smtp-log", default="/tmp/mock_mail/smtp.log")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    with open(args.actions) as fh:
        actions = json.load(fh)

    focus_simulator()
    point_accurate()
    mapping = measure_mapping(args.device_width, args.device_height, save_dir=args.out)
    print("mapping:", json.dumps(mapping))
    if args.map:
        with open(args.map, "w") as fh:
            json.dump(mapping, fh, indent=2)

    def to_screen(dx, dy):
        return (mapping["origin"]["x"] + dx * mapping["scale"],
                mapping["origin"]["y"] + dy * mapping["scale"])

    log = []
    for i, act in enumerate(actions, 1):
        kind = act.get("do")
        try:
            if kind == "click":
                sx, sy = to_screen(act["x"], act["y"])
                run(["cliclick", f"m:{sx:.0f},{sy:.0f}"])
                time.sleep(0.2)
                run(["cliclick", f"c:{sx:.0f},{sy:.0f}"])
                time.sleep(float(act.get("wait", 1.2)))
                print(f"[{i}] click device({act['x']},{act['y']}) -> screen({sx:.0f},{sy:.0f})")
            elif kind == "type":
                # Typing needs the field to be focused; cliclick already clicked.
                text = act["text"]
                osa(f'tell application "System Events" to keystroke "{text}"')
                # osa keystroke is fire-and-forget: characters keep trickling in
                # at ~0.4s/char long after the call returns. Proceeding to the
                # next click mid-trickle once let the email field's reload race
                # wipe the username field down to "poc". Wait for the trickle.
                settle = max(float(act.get("wait", 0.6)), 0.6 * len(text))
                time.sleep(settle)
                print(f"[{i}] type {len(text)} chars (settled {settle:.1f}s)")
            elif kind == "clearfield":
                # Select-all + delete in the focused field. The SetupImap form
                # auto-fills username and both server hostnames from the email
                # as it is typed (updateForEmailChanges -> reloadRows), so a
                # bare type appends and produces "imap.mockmail.testimap...".
                focus_simulator()
                osa('tell application "System Events" to keystroke "a" using command down')
                time.sleep(0.4)
                osa('tell application "System Events" to key code 51')
                time.sleep(float(act.get("wait", 0.6)))
                print(f"[{i}] clearfield (cmd+A, delete)")
            elif kind == "key":
                focus_simulator()
                osa(f'tell application "System Events" to key code {int(act["code"])}')
                time.sleep(float(act.get("wait", 0.8)))
                print(f"[{i}] keycode {act['code']}")
            elif kind == "cmdkey":
                # e.g. {"do":"cmdkey","char":"k"} toggles the software keyboard
                focus_simulator()
                ch = act["char"]
                osa(f'tell application "System Events" to keystroke "{ch}" using command down')
                time.sleep(float(act.get("wait", 1.0)))
                print(f"[{i}] cmd+{ch}")
            elif kind == "swipe":
                sx1, sy1 = to_screen(act["x"], act["y"])
                sx2, sy2 = to_screen(act["x"], act["to_y"])
                steps = int(act.get("steps", 12))
                run(["cliclick", f"m:{sx1:.0f},{sy1:.0f}"])
                run(["cliclick", f"dd:{sx1:.0f},{sy1:.0f}"])
                for s in range(1, steps + 1):
                    t = s / float(steps)
                    mx = sx1 + (sx2 - sx1) * t
                    my = sy1 + (sy2 - sy1) * t
                    run(["cliclick", f"dm:{mx:.0f},{my:.0f}"])
                    time.sleep(0.03)
                run(["cliclick", f"du:{sx2:.0f},{sy2:.0f}"])
                time.sleep(float(act.get("wait", 1.2)))
                print(f"[{i}] swipe device y{act['y']}->{act['to_y']}")
            elif kind == "clickrow":
                shot_path = device_shot(os.path.join(args.out, "rows.png"))
                rows = detect_rows(shot_path, device_h=args.device_height)
                n = int(act["row"])
                print(f"[{i}] rows detected: {rows}")
                if n < 1 or n > len(rows):
                    raise RuntimeError(f"clickrow: no row {n} (only {len(rows)})")
                dx, dy = 201, int(round(rows[n - 1]))
                sx, sy = to_screen(dx, dy)
                run(["cliclick", f"m:{sx:.0f},{sy:.0f}"])
                time.sleep(0.2)
                run(["cliclick", f"c:{sx:.0f},{sy:.0f}"])
                time.sleep(float(act.get("wait", 1.0)))
                print(f"[{i}] clickrow {n} device(201,{dy}) -> screen({sx:.0f},{sy:.0f})")
            elif kind == "clickgreen":
                shot_path = device_shot(os.path.join(args.out, "green.png"))
                # y_from=450: after the form scrolls, Connect can sit as high
                # as mid-screen; the green nav bar at the top ends by y=130,
                # and the small green "Done" text never reaches the 500px
                # pixel-count floor, so 450 is still unambiguous.
                found = find_green(shot_path, y_from=450, device_h=args.device_height)
                if not found:
                    raise RuntimeError("clickgreen: no green button on screen")
                cx, cy, top, bottom = found
                # The button can reach into the bottom safe-area / home-gesture
                # zone. Run 5 tapped the centroid of an 824..873pt button
                # (y=848): the tap never fired connect() - no HUD, no alert, no
                # network - and the email field silently took focus instead.
                # Tap progressively higher inside the button and verify the
                # screen actually reacts; a real tap produces a HUD/alert/
                # navigation, a swallowed one changes nothing.
                pre = device_shot(os.path.join(args.out, "green-pre.png"))
                tried = []
                fired = False
                for frac in (0.12, 0.3, 0.5):
                    dy = int(round(top + (bottom - top) * frac))
                    if any(abs(dy - t) < 4 for t in tried):
                        continue
                    tried.append(dy)
                    sx, sy = to_screen(cx, dy)
                    run(["cliclick", f"m:{sx:.0f},{sy:.0f}"])
                    time.sleep(0.2)
                    run(["cliclick", f"c:{sx:.0f},{sy:.0f}"])
                    time.sleep(float(act.get("wait", 2.0)))
                    post = device_shot(os.path.join(args.out, "green-post.png"))
                    d = shot_diff(pre, post)
                    print(f"[{i}] clickgreen tap y={dy} -> screen({sx:.0f},{sy:.0f}) diff={d:.2f}")
                    if d > 3.0:
                        fired = True
                        break
                if not fired:
                    raise RuntimeError(
                        f"clickgreen: no screen reaction after taps at y={tried} "
                        f"(button spans {top}..{bottom}, centroid {cy})")
            elif kind == "clicklastrow":
                # Click the LAST text row detected on screen. On the FlowCrypt
                # welcome screen that is the "Other email provider" button, and
                # because the position is measured live it survives the +23pt
                # row drift between runs that a baked coordinate does not.
                shot_path = device_shot(os.path.join(args.out, "rows.png"))
                rows = detect_rows(shot_path, device_h=args.device_height)
                say(f"[{i}] clicklastrow: rows detected: {rows}")
                if not rows:
                    raise RuntimeError("clicklastrow: no rows on screen")
                dx, dy = 201, int(round(rows[-1]))
                sx, sy = to_screen(dx, dy)
                run(["cliclick", f"m:{sx:.0f},{sy:.0f}"], log=True)
                time.sleep(0.2)
                run(["cliclick", f"c:{sx:.0f},{sy:.0f}"], log=True)
                time.sleep(float(act.get("wait", 1.5)))
                say(f"[{i}] clicklastrow device(201,{dy}) -> screen({sx:.0f},{sy:.0f})")
            elif kind == "open_provider":
                # Tap "Other email provider" (adaptive: last detected row) and
                # retry until the SetupImap form is verifiably on screen. This
                # gate exists because a blind click list once walked through
                # the wrong screen and typed into nothing. The form counts as
                # up only when a wide green primary button sits below y=700 AND
                # at least 10 text rows are detectable: the welcome screen has
                # no green button at all, and a pushed webview can have green
                # pixels but never a bottom-anchored one.
                timeout = float(act.get("timeout", 240))
                deadline = time.time() + timeout
                attempt = 0
                up = False
                while time.time() < deadline:
                    attempt += 1
                    probe = device_shot(os.path.join(args.out, "open-provider-probe.png"))
                    rows = detect_rows(probe, device_h=args.device_height)
                    green = find_green(probe, device_h=args.device_height)
                    say(f"[{i}] open_provider probe: rows={rows} green={green}")
                    if green and green[1] > 700 and len(rows) >= 10:
                        up = True
                        say(f"[{i}] open_provider: form already up (green {green}, {len(rows)} rows)")
                        break
                    if green and green[1] <= 700:
                        # webview-like screen (e.g. Privacy): pop it first
                        bx, by = to_screen(30, 115)
                        run(["cliclick", f"m:{bx:.0f},{by:.0f}"], log=True)
                        time.sleep(0.2)
                        run(["cliclick", f"c:{bx:.0f},{by:.0f}"], log=True)
                        time.sleep(2.0)
                        say(f"[{i}] open_provider: webview detected (green {green}), pressed back")
                    if rows:
                        dx, dy = 201, int(round(rows[-1]))
                    else:
                        dx, dy = float(act.get("x", 200)), float(act.get("y", 629))
                    sx, sy = to_screen(dx, dy)
                    run(["cliclick", f"m:{sx:.0f},{sy:.0f}"], log=True)
                    time.sleep(0.2)
                    run(["cliclick", f"c:{sx:.0f},{sy:.0f}"], log=True)
                    say(f"[{i}] open_provider attempt {attempt}: clicked device({dx},{dy})")
                    t2 = min(time.time() + float(act.get("per_try", 40)), deadline)
                    while time.time() < t2:
                        time.sleep(float(act.get("poll", 3.0)))
                        probe = device_shot(os.path.join(args.out, "open-provider-probe.png"))
                        rows = detect_rows(probe, device_h=args.device_height)
                        green = find_green(probe, device_h=args.device_height)
                        say(f"[{i}] open_provider poll: rows={rows} green={green}")
                        if green and green[1] > 700 and len(rows) >= 10:
                            up = True
                            break
                    if up:
                        break
                if not up:
                    raise RuntimeError(
                        f"open_provider: SetupImap form never appeared in {timeout}s")
                say(f"[{i}] open_provider: form up after {attempt} attempt(s)")
            elif kind == "liveness":
                # Prove the device framebuffer still updates before any
                # screenshot-based verdict is trusted. Run 2 shipped a 145s
                # video of one static welcome frame while the app console
                # proved SetupImapViewController had loaded: the framebuffer
                # had stalled mid-push and every later "form never appeared"
                # probe was reading a dead buffer. Terminating the app must
                # produce the home screen; if the pixels do not move, the run
                # fails loudly instead of shipping convincing fake evidence.
                if not args.udid or not args.bundle_id:
                    raise RuntimeError("liveness: needs --udid and --bundle-id")
                stamp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "stamp.py")

                def liveness_attempt(tag):
                    a = device_shot(os.path.join(args.out, f"liveness-{tag}-app.png"))
                    run(["xcrun", "simctl", "terminate", args.udid, args.bundle_id],
                        log=True)
                    time.sleep(2.5)
                    b = device_shot(os.path.join(args.out, f"liveness-{tag}-home.png"))
                    d1 = shot_diff(a, b)
                    say(f"[{i}] liveness({tag}): app->home mean|dL|={d1:.2f}")
                    if d1 <= 1.0:
                        return False, d1
                    # Relaunch with a fresh console pipe appended to the same
                    # stamped log, so the form-fill run keeps its evidence.
                    run(["pkill", "-f", "console-pty"], log=True)
                    if args.console_log:
                        pipe = (f"nohup bash -c 'xcrun simctl launch --console-pty "
                                f"{args.udid} {args.bundle_id} 2>&1 | python3 {stamp} "
                                f">> {args.console_log}' > /dev/null 2>&1 &")
                    else:
                        pipe = f"nohup xcrun simctl launch {args.udid} {args.bundle_id} > /dev/null 2>&1 &"
                    run(pipe, log=True)
                    c, d2 = None, 0.0
                    deadline = time.time() + 90
                    while time.time() < deadline:
                        time.sleep(3)
                        c = device_shot(os.path.join(args.out,
                                                     f"liveness-{tag}-relaunch.png"))
                        d2 = shot_diff(b, c)
                        if d2 > 1.0:
                            break
                    say(f"[{i}] liveness({tag}): home->app mean|dL|={d2:.2f}")
                    return d2 > 1.0, d2

                ok, delta = liveness_attempt("1")
                if not ok:
                    say(f"[{i}] liveness: framebuffer looks frozen "
                        f"(delta={delta:.2f}); waking display and retrying")
                    run(["caffeinate", "-u", "-t", "3"], log=True)
                    run(["osascript", "-e", 'tell application "Simulator" to activate'])
                    time.sleep(3)
                    ok, delta = liveness_attempt("2")
                if not ok:
                    raise RuntimeError(
                        "liveness: device framebuffer is frozen - screenshots and "
                        "video would be stale, refusing to produce fake evidence")
                say(f"[{i}] liveness: framebuffer live (delta={delta:.2f})")
            elif kind == "expect_login":
                # The vulnerability proof, independent of any screenshot: the
                # mock IMAP server logs every received line, so a LOGIN command
                # carrying the cleartext password IS the receipt. No LOGIN line
                # means the PoC did not happen, however good the video looks.
                email = act.get("email", "poc@mockmail.test")
                txt = ""
                try:
                    with open(args.imap_log) as fh:
                        txt = fh.read()
                except OSError as exc:
                    raise RuntimeError(f"expect_login: cannot read {args.imap_log}: {exc}")
                hits = [ln for ln in txt.splitlines()
                        if "LOGIN" in ln.upper() and email in ln]
                say(f"[{i}] expect_login: {len(hits)} LOGIN line(s) in {args.imap_log}")
                for ln in hits[-3:]:
                    say(f"    {ln[:240]}")
                if not hits:
                    raise RuntimeError(
                        f"expect_login: no LOGIN {email} in {args.imap_log} - "
                        "credentials never reached the mock server")
                try:
                    with open(args.smtp_log) as fh:
                        stxt = fh.read()
                    auth = [ln for ln in stxt.splitlines() if "AUTH" in ln.upper()]
                    say(f"[{i}] expect_login: {len(auth)} SMTP AUTH line(s) "
                        f"in {args.smtp_log}")
                except OSError:
                    pass
            elif kind == "devshot":
                path = os.path.join(args.out, act["name"] + ".png")
                device_shot(path)
                print(f"[{i}] devshot {path}")
            elif kind == "assert_diff":
                # Tripwire: prove the screen actually changed since a reference
                # device shot. Catches "every step ok but nothing was typed".
                from PIL import Image
                ref_path = os.path.join(args.out, act["ref"] + ".png")
                cur_path = os.path.join(args.out, "assert-diff-current.png")
                device_shot(cur_path)
                a = Image.open(ref_path).convert("L").resize((402, 874))
                b = Image.open(cur_path).convert("L").resize((402, 874))
                pa, pb = a.load(), b.load()
                total = 0
                n = 0
                for yy in range(0, 874, 3):
                    for xx in range(0, 402, 3):
                        total += abs(pa[xx, yy] - pb[xx, yy])
                        n += 1
                mean = total / float(n)
                thr = float(act.get("min_delta", 1.0))
                print(f"[{i}] assert_diff vs {act['ref']}: mean|dL|={mean:.2f} (need >{thr})")
                if mean <= thr:
                    raise RuntimeError(
                        f"assert_diff: screen unchanged vs {act['ref']} "
                        f"(mean {mean:.2f}) - input never landed")
            elif kind == "sleep":
                time.sleep(float(act["seconds"]))
                print(f"[{i}] sleep {act['seconds']}s")
            elif kind == "shot":
                path = os.path.join(args.out, act["name"] + ".png")
                screenshot(path)
                print(f"[{i}] shot {path}")
            elif kind == "expect":
                path = os.path.join(args.out, "expect-" + act["name"] + ".png")
                screenshot(path)
                print(f"[{i}] expect-screen {path}")
            else:
                print(f"[{i}] unknown action {kind}")
            log.append({"i": i, "action": act, "ok": True})
        except Exception as exc:                      # noqa: BLE001
            print(f"[{i}] FAILED {kind}: {exc}")
            log.append({"i": i, "action": act, "ok": False, "error": str(exc)})
            break

    with open(os.path.join(args.out, "gui-steps.json"), "w") as fh:
        json.dump({"mapping": mapping, "steps": log}, fh, indent=2)
    failed = [s for s in log if not s["ok"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
