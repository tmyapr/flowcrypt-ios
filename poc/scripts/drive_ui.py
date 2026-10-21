#!/usr/bin/env python3
"""
Drive the iOS Simulator through the host GUI, without XCTest.

Why: on the GitHub macOS runner image every Xcode (16.0 .. 26.3) is missing
`lib_TestingInterop.dylib`, so WebDriverAgent cannot be built and Appium can
never attach. `cliclick` and System Events both work on the runner, so the PoC
clicks the Simulator window directly instead.

Calibration
-----------
Rather than guessing how the Simulator scales the device, the device screen
rectangle is measured from a real desktop capture:

  1. read the Simulator window frame via System Events
  2. `screencapture -x` the desktop
  3. crop the window, and find the large bright region (the app screen) by
     row/column mean brightness -- the device bezel is black, the app is white
  4. device pixels -> screen pixels with that rectangle

Every action logs the resolved absolute coordinates, so a failed run is
diagnosable after the fact.

Usage:
  drive_ui.py geom
  drive_ui.py tap 201 630
  drive_ui.py paste "poc@mockmail.test"
  drive_ui.py type "hello"
  drive_ui.py swipe 201 700 201 300
  drive_ui.py shot /tmp/out/01.png
  drive_ui.py focus
"""
import json
import os
import subprocess
import sys
import time

DEVICE_POINTS = (402, 874)   # iPhone 16 Pro logical size; overridden by --device
TMP = "/tmp/drive_ui"
os.makedirs(TMP, exist_ok=True)


def run(cmd, check=False):
    p = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{cmd} failed rc={p.returncode}: {p.stderr.strip()}")
    return p.stdout.strip()


def osa(script):
    return run(["osascript", "-e", script])


def window_frame():
    out = osa('tell application "System Events" to tell process "Simulator" '
              'to get {position, size} of window 1')
    if not out:
        raise RuntimeError("cannot read Simulator window frame (is Simulator running?)")
    nums = [int(float(v)) for v in out.replace("{", "").replace("}", "").split(",")]
    return nums[0], nums[1], nums[2], nums[3]


def activate():
    osa('tell application "Simulator" to activate')
    time.sleep(0.4)


def point_accurate():
    """Ask Simulator for 1 device point == 1 pixel, so scaling is predictable."""
    osa('tell application "System Events" to tell process "Simulator" '
        'to click menu item "Point Accurate" of menu 1 of menu bar item "Window" '
        'of menu bar 1')
    time.sleep(0.8)


def device_rect():
    """Locate the device screen inside the Simulator window, in screen pixels.

    Brightness heuristics are fragile: they break as soon as the app draws a
    dark screen, a dialog appears, or the wallpaper is dark. So this matches the
    real device screenshot (`simctl io screenshot`) against the desktop capture
    using 1-D luminance profiles -- content independent and cheap.
    """
    from PIL import Image

    activate()
    win = window_frame()
    desktop = os.path.join(TMP, "desktop.png")
    device = os.path.join(TMP, "device.png")
    run(["screencapture", "-x", desktop], check=True)
    run(["xcrun", "simctl", "io", "booted", "screenshot", device], check=True)

    im = Image.open(desktop).convert("L")
    # device screenshots come at 3x; normalise to logical points so the scale
    # search below works in the same units as the taps
    dev = Image.open(device).convert("L").resize(DEVICE_POINTS)
    W, H = im.size
    x, y, w, h = win
    x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
    w = max(1, min(w, W - x)); h = max(1, min(h, H - y))
    crop = im.crop((x, y, x + w, y + h))
    cw, ch = crop.size

    def row_profile(img):
        ww, hh = img.size
        p = img.load()
        return [sum(p[xx, yy] for xx in range(0, ww, 4)) / len(range(0, ww, 4))
                for yy in range(hh)]

    def col_profile(img):
        ww, hh = img.size
        p = img.load()
        return [sum(p[xx, yy] for yy in range(0, hh, 4)) / len(range(0, hh, 4))
                for xx in range(ww)]

    crows, ccols = row_profile(crop), col_profile(crop)
    best = None
    # With "Point Accurate" the window is the device plus a bezel, so the scale
    # is exactly 1 and the two margins are small. Only fall back to a scale
    # search when the window geometry says otherwise -- a blind search happily
    # picks a shrunken template and every tap then lands in the wrong place.
    gw, gh = w - DEVICE_POINTS[0], h - DEVICE_POINTS[1]
    if 0 <= gw <= 140 and 20 <= gh <= 260:
        scales = (1.0,)
    else:
        scales = (1.0, 0.95, 0.9, 0.85, 0.8, 0.75, 0.7, 0.65)
    for scale in scales:
        tw, th = int(round(dev.size[0] * scale)), int(round(dev.size[1] * scale))
        if tw >= cw or th >= ch or tw < 100:
            continue
        small = dev.resize((tw, th))
        drows, dcols = row_profile(small), col_profile(small)
        # slide vertically
        bestdy, bestrow = None, None
        for dy in range(0, ch - th + 1):
            s = 0
            for i in range(0, th, 8):
                s += abs(drows[i] - crows[dy + i])
            if bestrow is None or s < bestrow:
                bestrow, bestdy = s, dy
        bestdx, bestcol = None, None
        for dx in range(0, cw - tw + 1):
            s = 0
            for i in range(0, tw, 8):
                s += abs(dcols[i] - ccols[dx + i])
            if bestcol is None or s < bestcol:
                bestcol, bestdx = s, dx
        score = (bestrow / max(1, th / 8)) + (bestcol / max(1, tw / 8))
        if best is None or score < best[0]:
            best = (score, scale, bestdx, bestdy, tw, th)

    if best is None:
        raise RuntimeError(f"could not match the device screen inside window {win}")
    score, scale, dx, dy, tw, th = best
    return {
        "win": {"x": x, "y": y, "w": w, "h": h},
        "screen": {"x": x + dx, "y": y + dy, "w": tw, "h": th},
        "calibration": {"scale": scale, "score": round(score, 2)},
    }


def device_shot(path):
    run(["xcrun", "simctl", "io", "booted", "screenshot", path], check=True)
    return path


def changed(a, b):
    """Mean absolute difference between two device screenshots (0..255)."""
    from PIL import Image, ImageChops
    ia = Image.open(a).convert("L").resize((120, 260))
    ib = Image.open(b).convert("L").resize((120, 260))
    diff = ImageChops.difference(ia, ib)
    hist = diff.histogram()
    total = sum(i * n for i, n in enumerate(hist))
    mean = total / float(120 * 260)
    print(f"screen diff {mean:.2f} between {os.path.basename(a)} and {os.path.basename(b)}")
    return mean


def tap_green(y_from=560, y_to=874, save=None, tries=3):
    """Tap the centre of the big green primary button, wherever the layout put it.

    Coordinates measured once go stale the moment a keyboard appears or the form
    scrolls; the Connect button is the only large saturated-green area in the
    lower part of the screen, so find it instead of trusting a hardcoded y.
    """
    from PIL import Image

    p = os.path.join(TMP, "device.png")
    before = os.path.join(TMP, "before.png")
    after = os.path.join(TMP, "after.png")
    for attempt in range(1, tries + 1):
        device_shot(p)
        im = Image.open(p).convert("RGB").resize(DEVICE_POINTS)
        if save:
            im.save(save)
        w, h = im.size
        px = im.load()
        pts = []
        for yy in range(max(0, y_from), min(h, y_to)):
            for xx in range(w):
                r, g, b = px[xx, yy]
                if g > 90 and g > r * 1.35 and g > b * 1.25:
                    pts.append((xx, yy))
        if len(pts) < 500:
            print(f"tapgreen: only {len(pts)} green pixels below y={y_from} -- not tapping")
            return False
        xs = [p0[0] for p0 in pts]
        ys = [p0[1] for p0 in pts]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
        print(f"tapgreen: {len(pts)} px bbox=({min(xs)},{min(ys)})-({max(xs)},{max(ys)}) "
              f"centre=({cx},{cy}) attempt={attempt}")
        im.save(before)
        geo = geom()
        sx, sy = to_screen(cx, cy, geo)
        activate()
        subprocess.run(["cliclick", f"m:{sx},{sy}", "w:250", f"c:{sx},{sy}"], check=True)
        time.sleep(3.0)
        device_shot(after)
        d = changed(before, after)
        print(f"tapgreen diff={d:.2f}")
        if d >= 1.0:
            return True
        print("  nothing happened after the colour tap -- retrying")
    return False


def to_screen(dx, dy, geo):
    scr = geo["screen"]
    dw = geo.get("device_w") or DEVICE_POINTS[0]
    dh = geo.get("device_h") or DEVICE_POINTS[1]
    sx = scr["x"] + dx * scr["w"] / dw
    sy = scr["y"] + dy * scr["h"] / dh
    return int(round(sx)), int(round(sy))


def geom():
    geo = device_rect()
    geo["device_w"], geo["device_h"] = DEVICE_POINTS
    print(json.dumps(geo))
    return geo


def click_at(dx, dy):
    geo = geom()
    sx, sy = to_screen(dx, dy, geo)
    subprocess.run(["cliclick", f"m:{sx},{sy}", "w:120", f"c:{sx},{sy}"], check=True)
    print(f"tap device=({dx},{dy}) screen=({sx},{sy}) screen_rect={geo['screen']}")
    time.sleep(0.5)


def tap_nav(dx, dy, tries=4, min_diff=1.0):
    """Tap something that should change the screen, and prove that it did.

    On this runner the first click after a screenshot often gets swallowed --
    the window regains focus and eats it -- so a fire-and-forget click silently
    leaves the app on the old screen and every later coordinate is then wrong.
    Measuring the screen before and after turns that into a visible failure the
    loop can retry instead of a mystery.
    """
    before = os.path.join(TMP, "before.png")
    after = os.path.join(TMP, "after.png")
    for attempt in range(1, tries + 1):
        geo = geom()
        sx, sy = to_screen(dx, dy, geo)
        activate()  # as late as possible: the click must land on Simulator
        device_shot(before)
        subprocess.run(["cliclick", f"m:{sx},{sy}", "w:250", f"c:{sx},{sy}"], check=True)
        time.sleep(2.0)
        device_shot(after)
        d = changed(before, after)
        print(f"tapnav device=({dx},{dy}) screen=({sx},{sy}) attempt={attempt} diff={d:.2f}")
        if d >= min_diff:
            return True
        print("  screen did not change -- retrying the tap")
    print(f"tapnav gave up at ({dx},{dy}): the screen never changed")
    return False


def paste_text(text):
    """Paste via clipboard: reliable for @ . ! and anything else."""
    subprocess.run(["pbcopy"], input=text, text=True, check=True)
    subprocess.run(["cliclick", "kd:cmd", "t:v", "ku:cmd"], check=True)
    print(f"pasted {len(text)} chars")


def type_text(text):
    # keystroke handles plain ascii; fall back to paste for anything exotic
    if all(32 <= ord(c) < 127 for c in text):
        esc = text.replace('"', '\\"')
        osa(f'tell application "System Events" to keystroke "{esc}"')
        print(f"typed {len(text)} chars")
    else:
        paste_text(text)


def type_into(dx, dy, text, tries=3):
    """Focus a text field, paste, and check that the field actually changed."""
    before = os.path.join(TMP, "before.png")
    after = os.path.join(TMP, "after.png")
    # moving the caret from one field to the next is a tiny visual change, so a
    # strict threshold would reject a perfectly good tap and retry four times
    if not tap_nav(dx, dy, tries=tries, min_diff=0.2):
        print(f"type_into: tapping ({dx},{dy}) never changed the screen (no keyboard?)")
        return False
    for attempt in range(1, 3):
        device_shot(before)
        paste_text(text)
        time.sleep(1.2)
        device_shot(after)
        d = changed(before, after)
        print(f"type_into ({dx},{dy}) attempt={attempt} diff={d:.2f}")
        if d >= 0.3:
            return True
        print("  field looks unchanged -- retrying the paste")
    return False


def swipe(x1, y1, x2, y2):
    geo = geom()
    a = to_screen(x1, y1, geo); b = to_screen(x2, y2, geo)
    subprocess.run(["cliclick", f"m:{a[0]},{a[1]}", "w:100", f"dd:{a[0]},{a[1]}",
                    "w:80", f"dm:{int((a[0]+b[0])/2)},{int((a[1]+b[1])/2)}",
                    "w:80", f"du:{b[0]},{b[1]}"], check=True)
    print(f"swipe {x1},{y1} -> {x2},{y2} (screen {a} -> {b})")
    time.sleep(0.6)


def shot(path):
    run(["screencapture", "-x", path], check=True)
    print(f"shot {path}")


DIALOG_PROCS = ["UserNotificationCenter", "CoreServicesUIAgent", "SecurityAgent",
                "SystemUIServer", "NotificationCenter"]


def list_dialogs():
    """Enumerate windows+buttons of the processes macOS uses for prompts."""
    script = """
    set out to ""
    repeat with pname in {%s}
      try
        tell application "System Events" to tell process pname
          repeat with w in windows
            set out to out & pname & "|" & (name of w as text) & "|"
            repeat with b in buttons of w
              set out to out & (name of b as text) & ","
            end repeat
            set out to out & linefeed
          end repeat
        end tell
      end try
    end repeat
    return out
    """ % ", ".join(f'"{p}"' for p in DIALOG_PROCS)
    return osa(script)


def dismiss(targets=("Allow", "OK", "Don't Allow")):
    """Click away a system permission prompt that covers the Simulator window.

    Binding the mock mail servers to a listening port makes macOS 15 raise the
    local-network prompt; if it is left up, a tap meant for the app lands on the
    prompt instead. Returns the label that was clicked, or "".
    """
    for label in targets:
        script = """
        set clicked to ""
        repeat with pname in {%s}
          try
            tell application "System Events" to tell process pname
              repeat with w in windows
                try
                  if exists (button "%s" of w) then
                    click button "%s" of w
                    set clicked to pname & "/" & "%s"
                    exit repeat
                  end if
                end try
              end repeat
            end tell
          end try
          if clicked is not "" then exit repeat
        end repeat
        return clicked
        """ % (", ".join(f'"{p}"' for p in DIALOG_PROCS), label, label, label)
        res = osa(script)
        if res:
            print(f"dismissed prompt by clicking {label!r} ({res})")
            time.sleep(0.6)
            return label
    print("no system prompt to dismiss")
    return ""


def main():
    if len(sys.argv) < 2:
        print(__doc__); return 2
    cmd = sys.argv[1]
    if cmd == "geom":
        geom()
    elif cmd == "focus":
        activate(); point_accurate(); print("focused")
    elif cmd == "tap":
        click_at(int(sys.argv[2]), int(sys.argv[3]))
    elif cmd == "paste":
        paste_text(sys.argv[2])
    elif cmd == "tapnav":
        md = float(sys.argv[4]) if len(sys.argv) > 4 else 1.0
        tap_nav(int(sys.argv[2]), int(sys.argv[3]), min_diff=md)
    elif cmd == "field":
        type_into(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4])
    elif cmd == "type":
        type_text(sys.argv[2])
    elif cmd == "swipe":
        swipe(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    elif cmd == "shot":
        shot(sys.argv[2])
    elif cmd == "devshot":
        device_shot(sys.argv[2])
    elif cmd == "diff":
        changed(sys.argv[2], sys.argv[3])
    elif cmd == "tapgreen":
        lo = int(sys.argv[2]) if len(sys.argv) > 2 else 560
        tap_green(y_from=lo)
    elif cmd == "dialogs":
        print(list_dialogs())
    elif cmd == "dismiss":
        dismiss()
    else:
        print(f"unknown command {cmd}"); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
