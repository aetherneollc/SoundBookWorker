#!/usr/bin/env bash
# Render stage.json with Godot 4.7.2 (xvfb + llvmpipe) and mux the silent AVI to MP4.
# Same software-GL path as scripts/run_godot2d_probe.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GODOT_VERSION="4.7.2"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "Godot 2D rendering runs on Linux. GitHub Actions ubuntu-latest is the supported host."
  exit 2
fi
if [[ $# -ne 2 ]]; then
  echo "usage: bash scripts/render_godot2d.sh stage.json output.mp4"
  exit 2
fi

STAGE_ABS="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$1")"
OUTPUT_ABS="$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$2")"
if [[ ! -f "$STAGE_ABS" ]]; then
  echo "stage.json not found: $STAGE_ABS"
  exit 2
fi
OUT_DIR="$(dirname "$OUTPUT_ABS")"
mkdir -p "$OUT_DIR"

for tool in xvfb-run curl unzip python3; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "missing required tool: $tool"
    exit 1
  fi
done

eval "$(python3 - "$STAGE_ABS" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
fps = int(doc.get("fps") or 24)
frames = int(doc.get("total_frames") or 0)
width = int(doc.get("width") or 0)
height = int(doc.get("height") or 0)
print("FPS=%d" % fps)
print("FRAMES=%d" % frames)
print("WIDTH=%d" % width)
print("HEIGHT=%d" % height)
PY
)"
if [[ "$FRAMES" -le 0 ]]; then
  echo "stage.json total_frames must be positive"
  exit 1
fi
if [[ "$WIDTH" -ne 1280 || "$HEIGHT" -ne 720 ]]; then
  echo "stage.json must be 1280x720 (got ${WIDTH}x${HEIGHT})"
  exit 1
fi

CJK_FONT="$(python3 - <<'PY'
import os
roots = ["/usr/share/fonts"]
home = os.environ.get("HOME") or ""
if home:
    roots.append(os.path.join(home, ".local", "share", "fonts"))
cands = []
for root in roots:
    if not os.path.isdir(root):
        continue
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            lower = name.lower()
            if "noto" in lower and "cjk" in lower and lower.endswith((".ttc", ".otf", ".ttf")):
                cands.append(os.path.join(dirpath, name))

def score(path):
    name = os.path.basename(path).lower()
    value = 0
    if name.endswith(".otf"):
        value += 50
    elif name.endswith(".ttf"):
        value += 40
    elif name.endswith(".ttc"):
        value += 10
    if "sans" in name:
        value += 20
    if "sc" in name or "simplified" in name:
        value += 30
    if "regular" in name:
        value += 15
    if "bold" in name:
        value -= 5
    if "mono" in name:
        value -= 3
    return value

cands.sort(key=score, reverse=True)
print(cands[0] if cands else "")
PY
)"
export STAGE_CJK_FONT="$CJK_FONT"
if [[ -z "$CJK_FONT" ]]; then
  echo "warning: no Noto CJK font found; captions may miss glyphs"
else
  echo "CJK font: $CJK_FONT"
fi

export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export STAGE_JSON="$STAGE_ABS"
export STAGE_RESULT_JSON="$OUT_DIR/stage-result.json"

echo "---- software GL ----"
xvfb-run -a -s "-screen 0 1920x1080x24" glxinfo -B > "$OUT_DIR/glxinfo.txt" 2>&1 || true
grep -E "OpenGL renderer|OpenGL version|llvmpipe" "$OUT_DIR/glxinfo.txt" || true

echo "---- install Godot ${GODOT_VERSION} ----"
CACHE="$ROOT/.cache/godot-4.7.2"
STAMP="$CACHE/godot-bin-path"
mkdir -p "$CACHE"
if [[ ! -s "$STAMP" || ! -x "$(cat "$STAMP")" ]]; then
  DIST="$CACHE/godot-dist"
  rm -rf "$DIST"
  mkdir -p "$DIST"
  curl -fL --retry 5 --retry-delay 2 \
    -o "$CACHE/godot.zip" \
    "https://github.com/godotengine/godot-builds/releases/download/${GODOT_VERSION}-stable/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip"
  unzip -qo "$CACHE/godot.zip" -d "$DIST"
  BIN="$(find "$DIST" -type f -name 'Godot_v*_linux.x86_64' -print -quit)"
  if [[ -z "$BIN" ]]; then
    echo "Godot binary not found in the zip"
    exit 1
  fi
  chmod +x "$BIN"
  printf '%s\n' "$BIN" > "$STAMP"
fi
GODOT="$(cat "$STAMP")"
echo "Godot binary: $GODOT"

AVI="$OUT_DIR/godot2d-silent.avi"
LOG="$OUT_DIR/godot2d.log"
SCENE="$ROOT/godot/player/main.tscn"
echo "---- Godot 2D ${FRAMES} frames @ ${FPS} fps ----"
start="$(date +%s.%N)"
set +e
xvfb-run -a -s "-screen 0 1920x1080x24" \
  timeout 14400 "$GODOT" \
    --path "$ROOT/godot" \
    --display-driver x11 \
    --rendering-method gl_compatibility \
    --rendering-driver opengl3 \
    --audio-driver Dummy \
    --resolution 1280x720 \
    --fixed-fps "$FPS" \
    --disable-vsync \
    --quit-after "$((FRAMES + 180))" \
    --write-movie "$AVI" \
    "$SCENE" \
    >"$LOG" 2>&1
code=$?
set -e
end="$(date +%s.%N)"
python3 -c "print('godot exit ${code}, wall', round(${end} - ${start}, 3), 's')" || echo "godot exit ${code}"
echo "---- godot log (tail) ----"
tail -n 80 "$LOG" || true
if [[ "$code" -ne 0 ]]; then
  echo "Godot exited ${code}"
  exit "$code"
fi
if [[ ! -s "$AVI" ]]; then
  echo "Godot did not write $AVI"
  exit 1
fi

echo "---- mux ----"
python3 "$ROOT/renderer/mux.py" "$AVI" "$STAGE_ABS" "$OUTPUT_ABS"
rm -f "$AVI"
echo "Rendered $OUTPUT_ABS"
