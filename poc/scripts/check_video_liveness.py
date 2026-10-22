#!/usr/bin/env python3
"""
Refuse to ship a frozen PoC video.

Run 2 recorded 145 seconds of h264 that compressed to 88KB: the app navigated,
the console proved SetupImapViewController had loaded, and the video showed one
static welcome frame the whole time. A reviewer watching that learns nothing
about where the bug is, so the job must fail instead of uploading it.

This script samples the recording, measures frame-to-frame change, and exits
non-zero when the video never moves or stops moving before the end (the money
shot is always at the end: form fill -> Connect).

Usage:
  check_video_liveness.py demo-1-login.mp4 [--fps 2] [--min-delta 1.0]
                                [--min-moving-pairs 5]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--min-delta", type=float, default=1.0,
                    help="mean |dL| between consecutive frames that counts as motion")
    ap.add_argument("--min-moving-pairs", type=int, default=3,
                    help="how many moving frame pairs a real recording has; a "
                         "frozen one has exactly 0, a successful run has one "
                         "per screen transition plus every keystroke")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print(f"FROZEN/ABSENT: {args.video} does not exist")
        return 1
    size = os.path.getsize(args.video)
    print(f"video: {args.video} ({size} bytes)")

    tmp = tempfile.mkdtemp(prefix="vidliveness")
    try:
        rc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", args.video,
             "-vf", f"fps={args.fps},scale=402:-1",
             os.path.join(tmp, "f%05d.png")],
            capture_output=True, text=True).returncode
        if rc != 0:
            print(f"ffmpeg could not decode the video (rc={rc})")
            return 1

        from PIL import Image
        frames = sorted(f for f in os.listdir(tmp) if f.endswith(".png"))
        if len(frames) < 3:
            print(f"FROZEN: only {len(frames)} frame(s) decoded")
            return 1

        def lum(path):
            return Image.open(path).convert("L").resize((402, 874)).load()

        prev = lum(os.path.join(tmp, frames[0]))
        w, h = 402, 874
        deltas = []
        for name in frames[1:]:
            cur = lum(os.path.join(tmp, name))
            total = n = 0
            for yy in range(0, h, 3):
                for xx in range(0, w, 3):
                    total += abs(prev[xx, yy] - cur[xx, yy])
                    n += 1
            deltas.append(total / float(n))
            prev = cur

        moving = [i for i, d in enumerate(deltas) if d > args.min_delta]
        n_pairs = len(deltas)
        last_quarter = [i for i in moving if i >= n_pairs * 3 // 4]
        print(f"frames sampled: {len(frames)} at {args.fps}fps "
              f"({n_pairs} consecutive pairs)")
        print(f"max frame-to-frame delta: {max(deltas):.2f}")
        print(f"moving pairs: {len(moving)} (need >= {args.min_moving_pairs}), "
              f"of which in the final quarter: {len(last_quarter)}")

        if not moving:
            print("VERDICT: FROZEN - the video never changes; it is a still "
                  "image with a timeline and proves nothing")
            return 1
        if len(moving) < args.min_moving_pairs:
            print("VERDICT: FROZEN - almost no motion in the whole recording")
            return 1
        if not last_quarter:
            print("VERDICT: FROZEN LATE - motion stopped before the form fill / "
                  "Connect, which is the part the report is about")
            return 1
        print("VERDICT: LIVE - the recording shows real motion through the end")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
