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
    """Locate the device screen inside the Simulator window, in screen pixels."""
    from PIL import Image

    activate()
    win = window_frame()
    shot = os.path.join(TMP, "desktop.png")
    run(["screencapture", "-x", shot], check=True)
    im = Image.open(shot).convert("L")
    W, H = im.size
    x, y, w, h = win
    x = max(0, min(x, W - 1)); y = max(0, min(y, H - 1))
    w = max(1, min(w, W - x)); h = max(1, min(h, H - y))
    crop = im.crop((x, y, x + w, y + h))
    cw, ch = crop.size
    px = crop.load()

    def bright_rows():
        rows = []
        for yy in range(ch):
            s = 0
            for xx in range(0, cw, 3):
                s += px[xx, yy]
            rows.append(s / max(1, len(range(0, cw, 3))))
        return rows

    def bright_cols():
        cols = []
        for xx in range(cw):
            s = 0
            for yy in range(0, ch, 3):
                s += px[xx, yy]
            cols.append(s / max(1, len(range(0, ch, 3))))
        return cols

    rows, cols = bright_rows(), bright_cols()
    THR = 170

    def longest_run(vals):
        best = (0, 0, 0)
        start = None
        for i, v in enumerate(vals):
            if v > THR and start is None:
                start = i
            elif v <= THR and start is not None:
                if i - start > best[0]:
                    best = (i - start, start, i - 1)
                start = None
        if start is not None and len(vals) - start > best[0]:
            best = (len(vals) - start, start, len(vals) - 1)
        return best[1], best[2]

    top, bottom = longest_run(rows)
    left, right = longest_run(cols)
    if bottom <= top or right <= left:
        raise RuntimeError(f"could not find the device screen inside window {win}")
    return {
        "win": {"x": x, "y": y, "w": w, "h": h},
        "screen": {"x": x + left, "y": y + top, "w": right - left + 1, "h": bottom - top + 1},
    }


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
    elif cmd == "type":
        type_text(sys.argv[2])
    elif cmd == "swipe":
        swipe(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5]))
    elif cmd == "shot":
        shot(sys.argv[2])
    elif cmd == "dialogs":
        print(list_dialogs())
    elif cmd == "dismiss":
        dismiss()
    else:
        print(f"unknown command {cmd}"); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
