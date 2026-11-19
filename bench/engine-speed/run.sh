#!/usr/bin/env bash
# Measure Godot and Three.js software-render speed. Not part of the production renderer.
set -u
set -o pipefail
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/results"
GODOT_VERSION="4.7.2"
FRAMES="${BENCH_FRAMES:-72}"
FPS="${BENCH_FPS:-24}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This probe runs on Linux. GitHub Actions ubuntu-latest is the machine it is measuring."
  exit 2
fi
if ! [[ "$FRAMES" =~ ^[0-9]+$ && "$FPS" =~ ^[0-9]+$ ]]; then
  echo "BENCH_FRAMES and BENCH_FPS must be integers"
  exit 2
fi

export BENCH_FRAMES="$FRAMES"
export BENCH_FPS="$FPS"
export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe

rm -rf "$OUT"
mkdir -p "$OUT/movie" "$ROOT/.cache"

python3 - "$OUT" "$FRAMES" "$FPS" <<'PY'
import json, os, pathlib, sys
out, frames, fps = sys.argv[1:]
cpu = "unknown"
memory = "unknown"
try:
    for line in pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith("model name"):
            cpu = line.split(":", 1)[1].strip()
            break
except OSError:
    pass
try:
    for line in pathlib.Path("/proc/meminfo").read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MemTotal"):
            memory = line.split(":", 1)[1].strip()
            break
except OSError:
    pass
pathlib.Path(out, "host.json").write_text(json.dumps({
    "cpu": cpu,
    "cpus": os.cpu_count(),
    "memory": memory,
    "frames": int(frames),
    "fps": int(fps),
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

echo "---- software GL / Vulkan ----"
xvfb-run -a -s "-screen 0 1920x1080x24" glxinfo -B > "$OUT/glxinfo.txt" 2>&1 || true
vulkaninfo --summary > "$OUT/vulkan.txt" 2>&1 || true

echo "---- install Godot ${GODOT_VERSION} ----"
STAMP="$ROOT/.cache/godot-bin-path"
if [[ ! -s "$STAMP" || ! -x "$(cat "$STAMP")" ]]; then
  DIST="$ROOT/.cache/godot-dist"
  rm -rf "$DIST"
  mkdir -p "$DIST"
  curl -fL --retry 5 --retry-delay 2 \
    -o "$ROOT/.cache/godot.zip" \
    "https://github.com/godotengine/godot-builds/releases/download/${GODOT_VERSION}-stable/Godot_v${GODOT_VERSION}-stable_linux.x86_64.zip"
  unzip -qo "$ROOT/.cache/godot.zip" -d "$DIST"
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

run_godot() {
  local method="$1"
  local driver="$2"
  local mode="$3"
  local log="$OUT/godot-${method}-${mode}.log"
  local result="$OUT/godot-${method}-${mode}.json"
  local artifact=""
  local -a movie=()
  if [[ "$mode" == "movie" ]]; then
    artifact="$OUT/movie/${method}.avi"
    movie=(--write-movie "$artifact")
  fi
  echo "---- Godot ${method} ${mode} ----"
  local start end code
  start="$(date +%s.%N)"
  set +e
  BENCH_METHOD="$method" BENCH_MODE="$mode" BENCH_RESULT="$result" \
    xvfb-run -a -s "-screen 0 1920x1080x24" \
    timeout 600 "$GODOT" \
      --path "$ROOT/godot" \
      --display-driver x11 \
      --rendering-method "$method" \
      --rendering-driver "$driver" \
      --audio-driver Dummy \
      --resolution 1280x720 \
      --fixed-fps "$FPS" \
      --disable-vsync \
      --quit-after "$((FRAMES + 120))" \
      "${movie[@]}" \
      >"$log" 2>&1
  code=$?
  set -e
  end="$(date +%s.%N)"
  echo "exit ${code}, log ${log}"
  python3 "$ROOT/report.py" annotate \
    --json "$result" \
    --log "$log" \
    --exit "$code" \
    --start "$start" \
    --end "$end" \
    --artifact "$artifact" || true
}

run_godot gl_compatibility opengl3 readback
run_godot gl_compatibility opengl3 movie
run_godot forward_plus vulkan movie

echo "---- install Three.js ----"
cd "$ROOT/three"
set +e
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm_code=$?
set -e
cd "$ROOT"
if [[ "$npm_code" -ne 0 ]]; then
  python3 - "$OUT" "$npm_code" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
(out / "three-error.json").write_text(json.dumps({
    "engine": "three",
    "pixel_ok": False,
    "error": "npm install failed with exit %s" % sys.argv[2],
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
else
  echo "---- Three.js ----"
  set +e
  xvfb-run -a -s "-screen 0 1920x1080x24" node "$ROOT/three/bench.mjs"
  echo "three.js exit $?"
  set -e
fi

python3 "$ROOT/report.py" summary "$OUT"
exit $?
