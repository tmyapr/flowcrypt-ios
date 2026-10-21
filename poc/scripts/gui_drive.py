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


def run(cmd, check=False):
    return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                          text=True, check=check)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--actions", required=True)
    ap.add_argument("--out", default="poc-out")
    ap.add_argument("--device-width", type=int, default=390)
    ap.add_argument("--device-height", type=int, default=844)
    ap.add_argument("--map", default=None, help="write measured mapping here")
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
                time.sleep(float(act.get("wait", 0.6)))
                print(f"[{i}] type {len(text)} chars")
            elif kind == "key":
                focus_simulator()
                osa(f'tell application "System Events" to key code {int(act["code"])}')
                time.sleep(float(act.get("wait", 0.8)))
                print(f"[{i}] keycode {act['code']}")
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
