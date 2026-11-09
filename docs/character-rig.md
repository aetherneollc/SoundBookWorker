# Character performance prototype

The production renderer accepts a per-shot `character_layers` object in the R2 manifest. It is a **layered asset contract**, not a promise that a single image can be bound automatically. The repository's existing anime segmentation still produces only a whole-character body cutout when no explicit rig is supplied.

## Sprite contract

All sprite images must be transparent, have the same pixel dimensions as the shot, and use the same coordinate system. Upload them to R2 and put the object keys in a shot:

```json
{
  "character_layers": {
    "background_key": "characters/robot/scene-bg.jpg",
    "body_key": "characters/robot/body.png",
    "head_key": "characters/robot/head.png",
    "arm_left_key": "characters/robot/arm-left.png",
    "arm_right_key": "characters/robot/arm-right.png",
    "subject_x": 0.65,
    "subject_y": 0.75,
    "head_pivot_x": 0.64,
    "head_pivot_y": 0.31,
    "arm_left_pivot_x": 0.56,
    "arm_left_pivot_y": 0.34,
    "arm_right_pivot_x": 0.73,
    "arm_right_pivot_y": 0.34,
    "character": "机器人",
    "action": "wave",
    "mouth_mode": "led",
    "mouth_x": 0.64,
    "mouth_y": 0.22,
    "mouth_width": 0.03
  }
}
```

Coordinates are fractions of the full frame, not of a cropped character image. `action` currently supports `idle`, `talk`, and `wave`; omitted action behaves as `idle`. `mouth_mode: "led"` is for a robot's illuminated speaking indicator. For a human character, supply transparent full-frame viseme sprites instead:

```json
"mouth_keys": {"X": "characters/alex/mouth-closed.png", "B": "characters/alex/mouth-small.png", "D": "characters/alex/mouth-open.png"}
```

The manifest's `audio_tracks` must contain `scene_number` and `speaker`; `character` must match that speaker exactly. The timeline is locked to measured audio duration. When a rig has mouth assets, GitHub Actions installs Rhubarb Lip Sync 1.14.0 and generates mouth cues from each spoken line. Its language-independent phonetic mode handles non-English speech less precisely than English; if it fails, amplitude-driven open/close cues are used. Music and effects must be separate tracks because they corrupt lip detection.

The `technical_overlay` shot field can be `false` for story footage without the explainer-style scan line and markers.

## Asset quality gate

The free Kolors API generated a usable robot concept quickly, but an image prompt asking for a front-facing 2D A-pose returned a three-dimensional three-quarter view. This is why the current prototype uses manually checked head/arm masks. It should not be enabled as unattended automatic rigging until there is a reliable asset generator plus checks for pose, occlusion, seams, and all required parts. A failed rig should fall back to the original image, as the existing segmentation pipeline does.

Rive, Live2D, and Spine can animate pre-rigged artwork, but none automatically creates hidden pixels, correct joints, and visemes from a single composed image. More complex movement needs a dedicated asset pass or separate generated action footage.
