# SoundBook renderer

Public GitHub Actions repository that turns a finished SoundBook manifest into a 720p MP4. It holds no story-generation, LLM, or image-generation runtime and no application credentials. The Cloudflare control plane (SoundBookCF) dispatches `.github/workflows/render.yml` with a unique `job_id` and a private R2 `manifest_key`; the runner downloads the already-generated media, synthesizes speech with MOSS on CPU, renders, and uploads `renders/<job_id>/output.mp4`.

Two renderers live here. The manifest picks one:

| `manifest.renderer` | Used for | Pipeline |
| --- | --- | --- |
| `"godot2d"` | Animated stories. **This is the story pipeline.** | `renderer/prepare_stage.py` → Godot 4.7.2 `godot/player` → `renderer/mux.py` |
| absent (or `"remotion"`) | Science explainers and the science-framework serial. Also the **legacy** story paper puppets. | `renderer/prepare_remotion.py` → Remotion (`remotion/`) |

The workflow reads the field in its `Select renderer` step and runs one branch or the other. Speech synthesis and lip-sync tooling are shared.

## Godot 2D story pipeline

Characters are skinned 2D mesh puppets built from one front-facing A-pose master per character. Nothing is drawn per shot: the manifest says who is on stage and what they do, and the director turns that into frame-accurate cuts and performance.

1. **Mesh rigs.** `renderer/rig_mesh.py` mattes the master, fits a 17-bone skeleton, triangulates five draw groups, and writes `rig.json` plus `texture.png`. `renderer/face_anchors.py` locates eyes, brows, and mouth for lip-sync, blinking, and expressions. Rigs are cached by file hash under `media/rigs/` (`SOUNDBOOK_RIG_CACHE`).
2. **Stage.** `renderer/prepare_stage.py manifest.json stage.json` locks the timeline to the measured voice lines (`renderer/timeline_common.py`, shared with Remotion), derives mouth cues (Rhubarb when `RHUBARB_BIN` is set, audio energy otherwise), and calls `renderer/director.py`. The director plans establishing shots, shot/reverse-shot cuts, listener cutaways, gestures, nods, stances, walks, and the rover's path, then validates the result against the contract.
3. **Render.** `godot/player/main.tscn` plays `stage.json` one logical frame per tick under `--write-movie`. In CI, `scripts/render_godot2d.sh` runs it on `xvfb` with Mesa llvmpipe; no GPU is needed.
4. **Mux.** `renderer/mux.py` mixes the stage's audio tracks and encodes H.264/AAC.

`docs/STAGE2D_CONTRACT.md` is the reference for `stage.json`, `rig.json`, `robot_rig.json`, the motion clip format, and every director rule.

### Motion library

`godot/motions/` holds 19 clips shared by all characters:

- Base: `idle`, `listen`, `nod`
- Speech: `talk_calm`, `talk_emphatic`, `talk_explain`
- Gestures: `point`, `wave`, `react`, `surprised`
- Stances: `operate`, `carry`, `cross_arms`, `angry`, `sad`, `sit`, `stand`
- Movement: `walk_medium`, `step_shift`

The control plane's action words map onto them (`work` and `console` become `operate`; `walk` becomes a waist-up `walk_medium` or an in-place `step_shift` depending on distance). A stance given as a shot action is held while the actor listens and through the actor's first line. `point` and `talk_explain` are posed toward whoever the actor addresses. `sit` and `stand` lower the whole figure and need a desk or similar occluder in front.

### AT-1 rover

`godot/player/rover.gd` (`Rover2D`) plays the rover from a `robot_rig.json` built by `renderer/rig_robot.py`: hull, wheels that roll by actual ground travel, a two-link IK arm, and a signal lamp. It stands 0.53 of a character's height. The control plane exports only `rover: {action, speaker}`, so the director parks it clear of the cast, rolls it in from the screen edge on its first `drive`, keeps its position and facing across cuts, and holds a wide shot while it is the subject.

### Manifest (v1, `godot2d`)

Keys ending in `_key` are R2 objects. The workflow downloads each one and adds the matching `_file`; MOSS adds `file` to every voice track.

```json
{
  "version": 1,
  "renderer": "godot2d",
  "language": "zh",
  "episode_number": 1,
  "episode_title": "扭矩异常的清晨",
  "tts_engine": "moss",
  "width": 1280, "height": 720, "fps": 24,
  "timeline_mode": "voice_locked",
  "character_rigs": [
    {"character_id": "linlan", "name": "林岚", "master_key": "library/characters/linlan/master.png"}
  ],
  "robot_rig": {"master_key": "library/robot/at1/master.png"},
  "shots": [{
    "key": "library/locations/loc1/plate.png", "duration": 4.5, "scene_number": 1, "camera": "全景缓慢推近",
    "plate": {"plate_key": "library/locations/loc1/plate.png", "far_key": "library/locations/loc1/far.png",
              "near_key": "library/locations/loc1/near.png"},
    "story_motion": {
      "framing": "wide", "camera": "全景缓慢推近", "focus": "actor",
      "actors": [{"name": "林岚", "character_id": "linlan", "action": "point",
                  "from_x": 0.32, "to_x": 0.32, "facing": "right", "depth": "mid"}]
    },
    "rover": {"action": "drive", "speaker": "阿拓 AT-1"}
  }],
  "audio_tracks": [
    {"scene_number": 1, "dialogue_number": 1, "speaker": "林岚", "caption_text": "你看主屏幕。",
     "tts_text": "你看主屏幕。", "tts_voice": "Xiaoyu", "start_ms": 0, "volume": 1}
  ],
  "cta_card": {"name": "月面工坊", "tagline": "下集预告"}
}
```

The stage is 1280×720 only. `robot_rig` may also carry `wheel_hints` and `native_facing` when wheel detection needs help, or point at a prebuilt `rig_json`.

### Run it locally

```bash
python renderer/prepare_stage.py manifest.json stage.json --rig-cache media/rigs
bash scripts/render_godot2d.sh stage.json output.mp4      # Linux: installs Godot 4.7.2, renders, muxes
```

On a desktop with Godot 4.7.2 installed (Windows shown), run Godot yourself and then mux. The player reads its inputs from the environment:

```powershell
$env:STAGE_JSON = "D:/path/stage.json"
$env:STAGE_CJK_FONT = "C:/Windows/Fonts/msyh.ttc"          # any font with CJK glyphs
$env:STAGE_SAMPLE_FRAMES = "40,100,203"                    # optional PNG stills
$env:STAGE_SAMPLE_DIR = "D:/path/samples"
$env:STAGE_RESULT_JSON = "D:/path/render_result.json"      # optional timing report
godot_console.exe --path godot --rendering-method gl_compatibility --rendering-driver opengl3 `
  --audio-driver Dummy --resolution 1280x720 --fixed-fps 24 --disable-vsync `
  --quit-after <total_frames + 180> --write-movie D:/path/raw.avi res://player/main.tscn
python renderer/mux.py D:/path/raw.avi D:/path/stage.json D:/path/output.mp4
```

Local manifests use `*_file` paths directly. `media/` and `public/media/` are ignored by git: generated audio, video, rigs, and images stay out of this public repository.

## Remotion

Remotion (`remotion/`, `npm run render`) renders everything whose manifest has no `renderer: "godot2d"`.

- **Science explainers and the science-framework serial (current).** Camera moves over generated stills, animated diagrams, fact cards, title and end cards, captions, and synchronized audio. This path is unchanged and is not being replaced.
- **Story paper puppets (legacy).** The cut-out puppets rigged by `renderer/rig_characters.py`, the SVG and sprite rovers in `remotion/rover.tsx` and `remotion/robot.tsx`, `remotion/story.tsx`, `remotion/cartoon.tsx`, and the pixel-story stage. They still render for existing `cartoon` stories, but new story work goes to Godot 2D and these files only receive fixes.

`python renderer/prepare_remotion.py manifest.json remotion-props.json` followed by `npm run render` runs this path locally. A minimal manifest:

```json
{
  "version": 1,
  "width": 1280, "height": 720, "fps": 24,
  "shots": [{"key": "projects/demo/shot-01.png", "duration": 5, "scene_number": 1}],
  "audio_tracks": [{"key": "projects/demo/line-01.wav", "start_ms": 0, "volume": 1.0}],
  "subtitle": {"key": "projects/demo/subtitles.srt"}
}
```

`audio` is still accepted as a single full-length track.

## Tests

```bash
python -m unittest -v renderer/test_rig_mesh.py renderer/test_face_anchors.py renderer/test_motions.py \
  renderer/test_director.py renderer/test_prepare_stage.py renderer/test_mux.py \
  renderer/test_rover_stage.py renderer/test_side_walk.py renderer/test_voice_cues.py
```

`godot/tools/face_preview.gd` renders face close-up contact sheets (every mouth shape against every brow emotion) for any `rig.json`; the header of the script lists its environment variables.

`.github/workflows/probe-godot2d.yml` records a short skinned-mesh probe on the software renderer for pushes to `feat/godot2d` that touch `godot/`, and on manual dispatch.

## Workflow inputs and secrets

`render.yml` takes `job_id`, `manifest_key`, and `qa_mode`. With `qa_mode=true` the MP4 is uploaded as a workflow artifact and no studio episode is touched; use it to accept animation, voice, and timing changes before production does.

Repository Actions secrets: `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT` (`https://<account-id>.r2.cloudflarestorage.com`), `R2_BUCKET`, `CALLBACK_URL` (the Worker URL ending in `/api/callback`), and `CALLBACK_TOKEN` (matching the Cloudflare Worker secret). Keep the bucket private and grant the key access to this bucket only.
