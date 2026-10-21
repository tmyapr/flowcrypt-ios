#!/usr/bin/env bash
# Compose the final evidence video: simulator recordings + a caption slide that
# shows the credential lines captured from the app console.
#
# Requires only ffmpeg (no ImageMagick).
#
#   ./compose_proof.sh <artifact_dir> <out.mp4>
set -euo pipefail

DIR="${1:?usage: compose_proof.sh <artifact_dir> <out.mp4>}"
OUT="${2:-proof.mp4}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

VIDEOS=()
for v in "$DIR/demo-1-login.mp4" "$DIR/demo-2-upgrade.mp4"; do
  [ -f "$v" ] && VIDEOS+=("$v")
done
if [ ${#VIDEOS[@]} -eq 0 ]; then echo "no demo video found in $DIR" >&2; exit 1; fi

# 1. single timeline
if [ ${#VIDEOS[@]} -eq 1 ]; then
  cp "${VIDEOS[0]}" "$TMP/timeline.mp4"
else
  : > "$TMP/list.txt"
  for v in "${VIDEOS[@]}"; do echo "file '$v'" >> "$TMP/list.txt"; done
  ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$TMP/list.txt" \
    -c:v libx264 -pix_fmt yuv420p -y "$TMP/timeline.mp4"
fi

# 2. caption slide with the console evidence (drawtext reads a text file)
EVID="$TMP/evidence.txt"
{
  echo "APP CONSOLE  |  xcrun simctl launch --console-pty"
  echo ""
  echo "old build - during IMAP sign-in:"
  grep -h -i "LOGIN" "$DIR"/console-old-login.log 2>/dev/null | head -3 | sed 's/^/  /'
  echo ""
  echo "new build - after in-place upgrade, on launch:"
  grep -h -i "LOGIN" "$DIR"/console-new-after-upgrade.log 2>/dev/null | head -3 | sed 's/^/  /'
  echo ""
  echo "builds: 60d3370 (IMAP sign-in visible) -> 842645b (IMAP sign-in hidden)"
} > "$EVID"

FONT=""
for f in /usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf \
         /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf \
         /System/Library/Fonts/Menlo.ttc; do
  [ -f "$f" ] && FONT="$f" && break
done
FONTARG="font=sans"
[ -n "$FONT" ] && FONTARG="fontfile=$FONT"

ffmpeg -hide_banner -loglevel error -f lavfi -i "color=c=0x0d1117:s=1280x720:d=8" \
  -vf "drawtext=${FONTARG}:text='FlowCrypt iOS - IMAP credentials written to app console':fontcolor=0x58a6ff:fontsize=30:x=40:y=60,\
drawtext=${FONTARG}:textfile=${EVID}:fontcolor=0xc9d1d9:fontsize=20:x=40:y=130:line_spacing=6" \
  -r 30 -pix_fmt yuv420p -y "$TMP/caption.mp4"

# 3. video + caption
printf "file '%s'\nfile '%s'\n" "$TMP/timeline.mp4" "$TMP/caption.mp4" > "$TMP/final.txt"
ffmpeg -hide_banner -loglevel error -f concat -safe 0 -i "$TMP/final.txt" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart -y "$OUT"

echo "wrote $OUT"
