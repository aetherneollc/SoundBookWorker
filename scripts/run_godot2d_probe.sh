#!/usr/bin/env bash
# Record a 10s 1280x720 Godot 4.7 2D mesh-skin probe on software GL.
# Same Godot 4.7.2 + xvfb + llvmpipe path as bench/engine-speed/run.sh.
set -u
set -o pipefail
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${PROBE_OUT_DIR:-$ROOT/probe-godot2d-out}"
GODOT_VERSION="4.7.2"
FRAMES="${PROBE_FRAMES:-240}"
FPS="${PROBE_FPS:-24}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This probe runs on Linux. GitHub Actions ubuntu-latest is the machine it is measuring."
  exit 2
fi
if ! [[ "$FRAMES" =~ ^[0-9]+$ && "$FPS" =~ ^[0-9]+$ ]]; then
  echo "PROBE_FRAMES and PROBE_FPS must be integers"
  exit 2
fi

export LIBGL_ALWAYS_SOFTWARE=1
export GALLIUM_DRIVER=llvmpipe
export PROBE_FRAMES="$FRAMES"
export PROBE_FPS="$FPS"
export PROBE_OUT_DIR="$OUT"

python3 -c "import PIL" || {
  echo "python3-pil is required"
  exit 1
}

ASSETS="$(mktemp -d)"
trap 'rm -rf "$ASSETS"' EXIT

python3 - "$ASSETS" <<'PY'
import sys
from PIL import Image, ImageDraw

out = sys.argv[1]
W, H = 1920, 1080

def lerp(a, b, t):
    return int(a + (b - a) * t)

def gradient(size, top, bottom):
    img = Image.new("RGBA", size)
    px = img.load()
    width, height = size
    for y in range(height):
        t = y / max(height - 1, 1)
        color = tuple(lerp(top[i], bottom[i], t) for i in range(4))
        for x in range(width):
            px[x, y] = color
    return img

def capsule(draw, a, b, fill, width, outline=(24, 18, 36, 255), outline_extra=16):
    outer = width + outline_extra
    draw.line([a, b], fill=outline, width=outer)
    r = outer // 2
    draw.ellipse([a[0] - r, a[1] - r, a[0] + r, a[1] + r], fill=outline)
    draw.ellipse([b[0] - r, b[1] - r, b[0] + r, b[1] + r], fill=outline)
    draw.line([a, b], fill=fill, width=width)
    r2 = max(width // 2, 1)
    draw.ellipse([a[0] - r2, a[1] - r2, a[0] + r2, a[1] + r2], fill=fill)
    draw.ellipse([b[0] - r2, b[1] - r2, b[0] + r2, b[1] + r2], fill=fill)

# Far layer: night sky, moon, stars, distant ridge.
far = gradient((W, H), (8, 16, 48, 255), (92, 104, 156, 255))
fd = ImageDraw.Draw(far)
for i, (x, y, r) in enumerate((
    (140, 80, 2), (260, 140, 2), (420, 60, 3), (610, 180, 2), (780, 90, 2),
    (960, 40, 3), (1120, 150, 2), (1280, 70, 2), (1460, 130, 3), (1680, 50, 2),
    (1800, 190, 2), (320, 240, 2), (860, 220, 2), (1540, 240, 2),
)):
    fd.ellipse([x - r, y - r, x + r, y + r], fill=(236, 236, 214, 255))
fd.ellipse([1450, 70, 1710, 330], fill=(236, 226, 186, 255))
fd.ellipse([1520, 120, 1580, 180], fill=(214, 204, 166, 255))
fd.ellipse([1600, 180, 1670, 250], fill=(206, 196, 158, 255))
fd.polygon([(0, 760), (180, 640), (360, 720), (560, 600), (820, 730),
            (1100, 610), (1380, 720), (1620, 640), (1920, 760), (1920, 1080), (0, 1080)],
           fill=(46, 58, 108, 255))
fd.polygon([(0, 820), (240, 740), (480, 800), (760, 700), (1040, 790),
            (1320, 710), (1600, 800), (1920, 740), (1920, 1080), (0, 1080)],
           fill=(28, 36, 78, 255))
far.save(f"{out}/bg_far.png")

# Mid layer: teal ridges and a lunar outpost. Upper area stays transparent.
mid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
md = ImageDraw.Draw(mid)
md.polygon([(0, 860), (160, 620), (340, 760), (520, 540), (760, 780),
            (980, 500), (1240, 740), (1480, 560), (1700, 760), (1920, 640),
            (1920, 980), (0, 980)], fill=(58, 112, 124, 255))
md.polygon([(160, 620), (250, 700), (340, 760), (160, 620)], fill=(168, 206, 196, 255))
md.polygon([(520, 540), (640, 660), (760, 780)], fill=(176, 214, 204, 255))
md.polygon([(980, 500), (1110, 640), (1240, 740)], fill=(184, 220, 208, 255))
md.polygon([(1480, 560), (1590, 680), (1700, 760)], fill=(160, 198, 190, 255))
md.rectangle([860, 560, 1120, 820], fill=(186, 196, 210, 255))
md.rectangle([848, 540, 1132, 580], fill=(210, 216, 226, 255))
for wx, wy in ((900, 610), (980, 610), (1060, 610), (900, 690), (980, 690), (1060, 690)):
    md.rectangle([wx, wy, wx + 46, wy + 48], fill=(246, 214, 120, 255))
    md.rectangle([wx + 6, wy + 6, wx + 40, wy + 42], fill=(120, 168, 196, 255))
md.polygon([(1120, 700), (1240, 700), (1240, 820), (1160, 860), (1120, 820)], fill=(150, 164, 184, 255))
md.ellipse([1188, 430, 1268, 510], fill=(220, 226, 232, 255))
md.line([(1228, 510), (1228, 700)], fill=(70, 78, 96, 255), width=8)
md.arc([1160, 470, 1300, 610], 200, 340, fill=(70, 78, 96, 255), width=6)
mid.save(f"{out}/bg_mid.png")

# Near layer: stage floor, rails, crates. Upper area stays transparent.
near = Image.new("RGBA", (W, H), (0, 0, 0, 0))
nd = ImageDraw.Draw(near)
nd.polygon([(0, 760), (1920, 700), (1920, 1080), (0, 1080)], fill=(92, 64, 52, 255))
nd.polygon([(0, 730), (1920, 670), (1920, 760), (0, 800)], fill=(168, 122, 86, 255))
for x in range(0, 1920, 80):
    nd.line([(x, 760), (x + 40, 1080)], fill=(68, 46, 38, 255), width=4)
nd.line([(0, 748), (1920, 688)], fill=(236, 196, 140, 255), width=8)
nd.rectangle([80, 820, 260, 980], fill=(196, 92, 64, 255))
nd.rectangle([80, 820, 260, 848], fill=(120, 48, 40, 255))
nd.rectangle([1560, 790, 1780, 980], fill=(64, 112, 150, 255))
nd.rectangle([1560, 790, 1780, 822], fill=(30, 64, 96, 255))
nd.ellipse([420, 900, 620, 1040], fill=(58, 54, 62, 255))
nd.ellipse([1280, 880, 1460, 1040], fill=(48, 46, 58, 255))
for x in range(120, 1800, 220):
    nd.rectangle([x, 700, x + 14, 820], fill=(40, 42, 54, 255))
nd.line([(80, 700), (1840, 640)], fill=(210, 210, 220, 255), width=6)
near.save(f"{out}/bg_near.png")

# Puppet plate, 512x1024. Joints match godot/probe/probe_2d.gd.
puppet = Image.new("RGBA", (512, 1024), (0, 0, 0, 0))
pd = ImageDraw.Draw(puppet)
skin = (245, 206, 178, 255)
sleeve = (148, 36, 58, 255)
jacket = (232, 52, 68, 255)
pants = (42, 48, 86, 255)
shoe = (28, 26, 38, 255)
hair = (36, 28, 64, 255)
outline = (22, 16, 32, 255)
hip_l, knee_l, foot_l = (214, 652), (200, 828), (188, 988)
hip_r, knee_r, foot_r = (296, 652), (310, 828), (324, 988)
shoulder_l, elbow_l, hand_l = (176, 368), (116, 528), (98, 698)
shoulder_r, elbow_r, hand_r = (336, 368), (396, 528), (414, 698)
capsule(pd, hip_l, knee_l, pants, 58)
capsule(pd, knee_l, foot_l, pants, 48)
capsule(pd, hip_r, knee_r, pants, 58)
capsule(pd, knee_r, foot_r, pants, 48)
pd.ellipse([150, 960, 230, 1010], fill=shoe)
pd.ellipse([286, 960, 366, 1010], fill=shoe)
capsule(pd, shoulder_l, elbow_l, sleeve, 46)
capsule(pd, elbow_l, hand_l, skin, 36)
capsule(pd, shoulder_r, elbow_r, sleeve, 46)
capsule(pd, elbow_r, hand_r, skin, 36)
pd.rounded_rectangle([168, 330, 344, 660], radius=36, fill=jacket, outline=outline, width=10)
pd.rectangle([214, 368, 298, 452], fill=(255, 236, 220, 255))
pd.rectangle([196, 590, 316, 632], fill=(32, 28, 48, 255))
pd.rectangle([232, 286, 280, 348], fill=skin)
pd.ellipse([150, 118, 362, 250], fill=hair)
pd.ellipse([164, 150, 348, 340], fill=skin)
pd.polygon([(168, 168), (256, 78), (344, 168), (300, 150), (256, 118), (212, 150)], fill=hair)
pd.polygon([(164, 180), (210, 150), (230, 230)], fill=hair)
pd.polygon([(348, 180), (302, 150), (282, 230)], fill=hair)
pd.ellipse([196, 210, 236, 250], fill=(255, 255, 255, 255))
pd.ellipse([276, 210, 316, 250], fill=(255, 255, 255, 255))
pd.ellipse([208, 220, 228, 246], fill=(28, 32, 48, 255))
pd.ellipse([288, 220, 308, 246], fill=(28, 32, 48, 255))
pd.arc([220, 250, 292, 300], 20, 160, fill=(168, 72, 86, 255), width=6)
pd.ellipse([70, 680, 126, 736], fill=skin, outline=outline, width=6)
pd.ellipse([386, 680, 442, 736], fill=skin, outline=outline, width=6)
puppet.save(f"{out}/puppet_tex.png")

for name, size in (
    ("bg_far.png", None),
    ("bg_mid.png", None),
    ("bg_near.png", None),
    ("puppet_tex.png", (512, 1024)),
):
    img = Image.open(f"{out}/{name}")
    if img.width <= 0 or img.height <= 0:
        raise SystemExit(f"{name} is empty")
    if size is not None and img.size != size:
        raise SystemExit(f"{name} is {img.size}, expected {size}")
    print(f"asset {name} {img.width}x{img.height} mode={img.mode}")
PY

CJK_FONT="$(python3 - <<'PY'
import os
root = "/usr/share/fonts"
cands = []
if os.path.isdir(root):
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
export PROBE_CJK_FONT="$CJK_FONT"
if [[ -z "$CJK_FONT" ]]; then
  echo "warning: no Noto CJK font found under /usr/share/fonts"
else
  echo "CJK font: $CJK_FONT"
fi

rm -rf "$OUT"
mkdir -p "$OUT" "$ROOT/.cache/godot2d-probe"

echo "---- software GL ----"
xvfb-run -a -s "-screen 0 1920x1080x24" glxinfo -B > "$OUT/glxinfo.txt" 2>&1 || true
grep -E "OpenGL renderer|OpenGL version|llvmpipe" "$OUT/glxinfo.txt" || true

echo "---- install Godot ${GODOT_VERSION} ----"
CACHE="$ROOT/.cache/godot2d-probe"
STAMP="$CACHE/godot-bin-path"
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

AVI="$OUT/probe.avi"
MP4="$OUT/probe.mp4"
echo "---- Godot 2D probe ${FRAMES} frames @ ${FPS} fps ----"
start="$(date +%s.%N)"
set +e
xvfb-run -a -s "-screen 0 1920x1080x24" \
  timeout 900 "$GODOT" \
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
    >"$OUT/godot.log" 2>&1
code=$?
set -e
end="$(date +%s.%N)"
python3 -c "print('godot exit ${code}, wall', round(${end} - ${start}, 3), 's')" || echo "godot exit ${code}"
echo "---- godot log (tail) ----"
tail -n 80 "$OUT/godot.log" || true

if command -v ffmpeg >/dev/null 2>&1 && [[ -s "$AVI" ]]; then
  echo "---- ffmpeg ----"
  set +e
  ffmpeg -y -hide_banner -loglevel error \
    -i "$AVI" -an -c:v libx264 -preset veryfast -crf 30 -pix_fmt yuv420p -movflags +faststart \
    "$MP4"
  ff_code=$?
  set -e
  echo "ffmpeg exit ${ff_code}"
else
  ff_code=127
  echo "ffmpeg skipped"
fi

python3 - "$OUT" "$FRAMES" "$code" "$ff_code" <<'PY'
import json, pathlib, sys
out = pathlib.Path(sys.argv[1])
frames = int(sys.argv[2])
godot_code = int(sys.argv[3])
ff_code = int(sys.argv[4])
errors = []
result_path = out / "result.json"
avi = out / "probe.avi"
mp4 = out / "probe.mp4"
data = {}
if not result_path.is_file():
    errors.append("result.json missing")
else:
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append("result.json invalid: %s" % exc)
if not avi.is_file() or avi.stat().st_size <= 100 * 1024:
    size = avi.stat().st_size if avi.is_file() else 0
    errors.append("avi missing or <= 100KB (size=%d)" % size)
if data:
    if data.get("pixel_ok") is not True:
        errors.append("pixel_ok != true")
    if data.get("png_loaded") is not True:
        errors.append("png_loaded != true")
    if data.get("cjk_font_loaded") is not True:
        errors.append("cjk_font_loaded != true")
    if int(data.get("frames", 0)) != frames:
        errors.append("frames %s != %s" % (data.get("frames"), frames))
    print(json.dumps({
        "frames": data.get("frames"),
        "fps_target": data.get("fps_target"),
        "elapsed_sec": data.get("elapsed_sec"),
        "render_fps": data.get("render_fps"),
        "realtime_factor": data.get("realtime_factor"),
        "png_loaded": data.get("png_loaded"),
        "cjk_font_loaded": data.get("cjk_font_loaded"),
        "cjk_font_path": data.get("cjk_font_path"),
        "pixel_ok": data.get("pixel_ok"),
        "adapter": data.get("adapter"),
        "sample_frames": data.get("sample_frames"),
    }, ensure_ascii=False, indent=2))
for name in ("frame_012.png", "frame_120.png", "frame_220.png"):
    path = out / name
    if not path.is_file() or path.stat().st_size < 1000:
        errors.append("%s missing or tiny" % name)
if godot_code != 0:
    errors.append("godot exit %d" % godot_code)
if ff_code == 0:
    if not mp4.is_file() or mp4.stat().st_size < 10 * 1024:
        errors.append("mp4 missing or tiny")
elif ff_code != 127:
    errors.append("ffmpeg exit %d" % ff_code)
if errors:
    print("PROBE FAIL: " + "; ".join(errors))
    sys.exit(1)
print("PROBE OK avi=%d mp4=%d" % (avi.stat().st_size, mp4.stat().st_size if mp4.is_file() else 0))
PY
