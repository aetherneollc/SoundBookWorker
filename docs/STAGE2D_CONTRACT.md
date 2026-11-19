# Godot 2D 人偶舞台契约（v1）

故事片的 Godot 2D 渲染路径靠三份 JSON 衔接：

| 文件 | 谁写 | 谁读 | 内容 |
| --- | --- | --- | --- |
| `stage.json` | `renderer/director.py` 的 `build_stage()` | `godot/player/main.gd` | 一集的逐帧舞台调度 |
| `rig.json` | `renderer/rig_mesh.py` | `godot/player/puppet.gd`、`face.gd` | 一个角色的蒙皮网格和脸部锚点 |
| `godot/motions/*.json` | 手写，`renderer/test_motions.py` 校验 | `godot/player/motion.gd` | 所有角色共用的动作片段 |

通用约定：

- 时间一律是整数帧，区间 `[from, to)` 左闭右开。
- 顶层的 `from` 是全片绝对帧；`shots[]` 里 `beats`、`cuts` 的 `from`/`to` 相对所在镜头的起点。
- 横向位置 `x` 是画面宽度的比例（0 最左，1 最右）；`ground_y` 是画面高度的比例。
- 素材路径相对 `stage.json` 所在目录，也可以写绝对路径或 `res://`。
- 朝向、侧倾都用屏幕空间：`+` 表示朝画面右侧。立绘只有正面，不会镜像。

## 1. stage.json

```json
{
  "version": 1, "width": 1280, "height": 720, "fps": 24, "total_frames": 720,
  "intro": { "frames": 68, "episode_number": 1, "title": "...", "subtitle": "..." },
  "cta": { "from": 660, "frames": 60, "title": "...", "subtitle": "..." },
  "rigs": { "linlan": { "rig_json": "media/rigs/linlan/rig.json", "stature_scale": 0.93 } },
  "shots": [{
    "id": "s1_sh1", "scene_number": 1, "from": 68, "frames": 180,
    "set": {
      "plate": "media/loc1-plate.png", "far": "media/loc1-far.png", "near": "media/loc1-near.png",
      "ground_y": 0.86, "walk_scroll_speed": 0.0,
      "prop": { "kind": "console_desk", "x": 0.5, "depth": "mid", "image": "" }
    },
    "actors": [
      { "id": "linlan", "name": "林岚", "x": 0.34, "depth": "mid", "facing": "right",
        "lean": 0.25, "base_clip": "idle", "emotion": "neutral" }
    ],
    "beats": [
      { "from": 6, "to": 78, "actor": "linlan", "clip": "talk_explain", "emotion": "serious",
        "speaking": true, "look_at": "chen", "move_x": [0.34, 0.34] }
    ],
    "cuts": [
      { "from": 0, "to": 48, "framing": "wide", "focus_actor": "", "camera_move": "push_in" }
    ]
  }],
  "speech": [{ "actor": "linlan", "from": 74, "to": 146, "cues": [[0, "X"], [3, "D"], [7, "B"]] }],
  "captions": [{ "from": 74, "to": 146, "text": "林岚：先看月面传回的数据。" }],
  "audio": [{ "file": "media/voice_1.wav", "start_ms": 3083, "volume": 1.0, "loop": false }]
}
```

### 顶层

| 字段 | 说明 |
| --- | --- |
| `version` | 固定 `1`，其他值播放器拒绝。 |
| `width`、`height` | 取自 manifest。播放器只接受 1280×720，其他尺寸直接报错退出。 |
| `fps` | 帧率，默认 24。 |
| `total_frames` | 全片帧数，等于最后一个镜头的结尾和 `cta.from + cta.frames` 中较大的一个。 |
| `intro` | 片头卡，占用 `[0, frames)`。`frames` 为 0 表示没有片头；这个对象始终存在。 |
| `cta` | 片尾卡，占用 `[from, from + frames)`。`frames` 为 0 表示没有片尾；这个对象始终存在。 |
| `rigs` | 角色 id → `{rig_json, stature_scale}`。`stature_scale` 是身高倍数（1.0 为标准）。 |
| `shots` | 舞台镜头，按时间排列，互不重叠。没有镜头覆盖的帧，播放器出空画面。 |
| `speech` | 口型轨，每句台词一条。 |
| `captions` | 字幕，原样来自上游。 |
| `audio` | 音轨清单。播放器不读它，留给混音步骤。`start_ms` 是全片毫秒，`volume` 范围 0–4。 |

### shots[]

| 字段 | 说明 |
| --- | --- |
| `id` | `s{场号}_sh{镜号}`。长距离走路会把一个镜头拆成两个，后半段的 id 加后缀 `b`（见 1.4）。 |
| `scene_number` | 场号。 |
| `from`、`frames` | 绝对起始帧和时长。 |
| `set` | 布景，见下。 |
| `actors` | 本镜在场的角色和初始状态。每个 `id` 都在 `rigs` 里。 |
| `beats` | 表演段。 |
| `cuts` | 镜内切镜。 |

**`set`**

| 字段 | 说明 |
| --- | --- |
| `plate`、`far`、`near` | 中景板、远景层、前景遮挡层，没有就是空串。只有 `plate` 时它被当作远景层显示。 |
| `ground_y` | 脚底线，默认 0.86。有场景标定时取 `plate_metrics.mid_y`。 |
| `walk_scroll_speed` | 布景横移速度（像素/秒）。**整个镜头从第 0 帧起一直滚动**，远、中、近三层分别乘 0.25、0.65、1.15。正值表示布景向左移，即角色朝右走。 |
| `prop` | `kind` 取 `console_desk`、`telemetry_screen`、`workbench`，由播放器程序绘制；`image` 非空时改贴这张图。`kind` 和 `image` 都为空表示没有道具。`depth` 取 `near`/`mid`/`far`。 |

**`actors[]`**

| 字段 | 说明 |
| --- | --- |
| `id`、`name` | 角色 id（ASCII）和显示名。 |
| `x` | 没有表演段时的站位。 |
| `depth` | `near`/`mid`/`far`，缩放分别为 1.12、1.0、0.86。 |
| `facing` | `left` 或 `right`。只影响头部微转和中景的视线留白。 |
| `lean` | 身体侧倾，范围 -1 到 1。站在同伴左边为 `+0.25`，右边为 `-0.25`，独自在场为 0。 |
| `base_clip` | 没有表演段时循环播放的动作，取 `idle` 或 `listen`。 |
| `emotion` | 没有表演段时的表情。 |

**`beats[]`**：同一角色的表演段互不重叠。

| 字段 | 说明 |
| --- | --- |
| `from`、`to` | 镜头内的帧区间。 |
| `actor` | 在场角色的 id。 |
| `clip` | 动作片段名，见 1.1。**每个表演段都从片段的第 0 秒重新播放**，换片段时有 0.25 秒过渡。 |
| `emotion` | `neutral`、`happy`、`serious`、`angry`、`sad`、`surprised`。 |
| `speaking` | 这一段是否在说话。播放器把它和 `speech` 轨取“或”。 |
| `look_at` | 看向的在场角色 id，空串表示按 `facing`。 |
| `move_x` | `[起点, 终点]`，在本段内线性移动。不移动时两个值相同。 |

某一帧没有表演段覆盖时，角色回到 `actors[]` 里的 `x`、`base_clip`、`emotion`。所以角色一旦挪过位，导演会用表演段把这个镜头剩下的时间全部铺满，站位才不会跳回去。

**`cuts[]`**：首尾相接，正好铺满 `[0, frames)`。

| 字段 | 说明 |
| --- | --- |
| `framing` | `wide`（缩放 1.0，看全场）、`two_shot`（1.18，对准 `actors` 的前两人）、`medium`（1.42，对准胸口）、`close`（1.85，对准头部）。 |
| `focus_actor` | `medium`、`close` 对准的角色；`wide`、`two_shot` 为空串。 |
| `camera_move` | `push_in`（缓推 7%）、`slow_pan_left`、`slow_pan_right`（横移 70 像素）、`static`。运动在这一刀的时长内走完。 |

**`speech[]`**：`actor` 是角色 id，`from`/`to` 是绝对帧。`cues` 是 `[相对帧, 口型]`，相对帧从 0 起严格递增且小于 `to - from`，一个口型保持到下一条为止。口型取 `X`（闭嘴）和 `A`–`F`。

### 1.1 动作词表

`idle`、`talk_calm`、`talk_emphatic`、`talk_explain`、`listen`、`nod`、`point`、`wave`、`react`、`walk_medium`、`step_shift`，与 `godot/motions/` 下的 11 个文件一一对应。

manifest 里旧的 `CARTOON_ACTIONS` 按下表映射（`director.map_action`）：

| 旧动作 | 片段 |
| --- | --- |
| `idle` | `idle` |
| `listen` | `listen`（同时作为 `base_clip`） |
| `talk` | `talk_calm`（实际手势按台词再选） |
| `point`、`wave`、`react` | 同名片段 |
| `work` | `talk_explain`，表示手上在忙。M2 的 `operate` 片段做好后替换。 |
| `console` | `point`，表示伸手去够控制台或屏幕。 |
| `walk`、`carry` | 按位移：超过 0.14 用 `walk_medium`，0.03 到 0.14 用 `step_shift`，不超过 0.03 不动。 |
| 其他 | 已经是片段名就原样使用，否则 `idle`。 |

### 1.2 build_stage 的输入

```python
build_stage(manifest, timeline_shots, speech_tracks, captions, rigs_map, audio_tracks,
            intro_info=None, cta_info=None) -> dict
```

这个函数不读文件、不拷素材，传进来的路径原样写出。

| 参数 | 形状 |
| --- | --- |
| `manifest` | Worker 的 v1 manifest，读 `width`、`height`、`fps`、`episode_number`、`episode_title`。 |
| `timeline_shots` | 按播放顺序的 manifest 镜头。每个带绝对起始帧 `from`（缺省时从片头之后顺排）、帧数 `frames`（或 `duration`，单位也是帧）、`scene_number`、`story_motion`，可选 `plate` 和 `plate_metrics`。`plate` 可以是拷贝后的 `{src, far, near}`，也可以是 manifest 原始的 `{plate_file, far_file, near_file}`，放在镜头上或 `story_motion` 里都行。 |
| `speech_tracks` | 每句台词一项：`{speaker, from, to, cues}`，可带 `text`、`emotion`。`cues` 可以是 `voice_cues.speech_cues` 的原始输出（`{start, end, value}`，秒，相对台词开头）、绝对帧的 `{from, value}`，或已经规范好的 `[相对帧, 口型]`。也接受 `prepare_remotion` 那种每个口型一项的平铺列表（`{speaker, from, to, value}`），同一说话人相邻的口型会并成一句。 |
| `captions` | `{from, to, text}`，绝对帧。 |
| `rigs_map` | 角色 id 或角色名 → `{rig_json, stature_scale?, name?, id?}`，也可以直接是 `rig.json` 的路径。 |
| `audio_tracks` | `{file, start_ms, volume, loop}`；也接受 `src` 和以帧计的 `from`。 |
| `intro_info`、`cta_info` | `{frames, title, subtitle}`；片头可带 `episode_number`，片尾可带 `from`（缺省接在最后一个镜头后面）。 |

`story_motion` 里读这些字段：`actors[]` 的 `name`、`character_id`、`action`、`from_x`、`to_x`、`facing`、`depth`、`stature_scale`，以及 `framing`、`camera`、`set_prop`、`plate`。

角色 id 的取法：`rigs_map` 条目的 `id`，其次 `rigs_map` 的键，其次 `character_id`，其次角色名；都不是 ASCII 时用 `actor_N`。同一个角色全片 id 不变。

口型映射：Rhubarb 的 `A`–`F`、`X` 原样保留，`G` 记作 `B`，`H` 记作 `C`。0 到 1 的音量值按 0.13、0.35、0.68 三档分成 `X`、`B`、`C`、`D`。

### 1.3 兜底

- `rigs_map` 里找不到的角色不上台，并打一条警告。这样 `actors[]` 里的每个 id 都有 rig。
- 旁白、阿拓，或者不在这个镜头里的角色说话时：字幕和音轨照常保留，在场角色保持 `base_clip`，不产生说话表演段。说话人全片都没上过台时，也不产生 `speech` 条目。
- 镜头没有 `story_motion` 或没有角色时：`actors`、`beats` 为空，只有一刀 `wide`。

### 1.4 导演规则

**走路。** 立绘只有正面，全景里走不了远路。

- 位移超过 0.14 的 `walk`：单独拆成一个舞台镜头，只有一刀 `medium` 对准走路的人，动作是 `walk_medium`，`walk_scroll_speed` 为 ±38。时长取 2 个步行循环（位移超过 0.24 时取 3 个）。原镜头剩下的部分成为下一个舞台镜头（id 加 `b`），角色已经站在终点，布景不再滚动。原镜头剩余不足 1.5 秒时不拆，整个镜头都用来走路。之所以要拆，是因为布景滚动对整个镜头生效。
- 位移在 0.03 到 0.14 之间：在原景别里播一次 `step_shift`（2 秒），带 `move_x`。
- 位移不超过 0.03：不动。

**切镜。** 每一刀不短于 28 帧（24 fps 下；其他帧率按比例换算）。

1. 开场：镜头不短于 4.5 秒，并且剧本景别是全景，或者它是这一场的第一个镜头且有两人以上在场时，先给 1.8 到 2.2 秒的建立镜头。第一句台词正好落在这个窗口里就在那一帧切，否则在 2.0 秒切。两人镜头只有在一场的第一个镜头且剧本景别是全景时用 `wide`，其余用 `two_shot`；一人或三人以上一律 `wide`。
2. 一场的第一个镜头有两人以上但不足 4.5 秒时，整个镜头只用一刀全景或双人景。
3. 台词：说话人换了，就在新说话人开口的那一帧切到他的 `medium`。情绪是 `angry`、`surprised`，或者是不超过 1.5 秒的感叹句时用 `close`。同一个人连续说话不重复切，也不会从 `close` 退回 `medium`。
4. 最短时长保护：上一刀还不满 28 帧时，新的一刀顺延；顺延后这个人已经快说完，就不切。
5. 长镜头：对准一个人的 `medium` 或 `close` 超过 4.5 秒时打断一次。场上有听者时，插 1.5 秒听者的反应镜头（听者同时点头）再切回；否则在 `medium` 和 `close` 之间换景别。两种办法全片交替使用，一刀仍然过长就继续打断。
6. 一句台词之后场上三秒以上没人说话（比如接旁白）时，多人镜头在这句结束 0.5 秒后退回全景或双人景。
7. `camera_move` 按 `push_in`、`slow_pan_right`、`static`、`slow_pan_left` 轮换，相邻两刀不重复。`close` 只用 `push_in` 和 `static`。`story_motion.camera` 里写了推、摇、固定时，用来定这个镜头第一刀的运动。

**说话人。** 手势按顺序匹配第一条：

| 条件 | 片段 |
| --- | --- |
| 镜头动作是 `wave`，或台词是问候、告别 | `wave` |
| 镜头动作是 `react`，或 2 秒以内的惊讶句 | `react` |
| 镜头动作是 `point`、`console`，或台词有“你看”“这里”等指示词 | `point` |
| 情绪是 `angry`、`surprised`，或感叹句 | `talk_emphatic` |
| 镜头动作是 `work`，或问句、3.5 秒以上的长句、带“因为”“所以”等解释词 | `talk_explain` |
| 其他 | `talk_calm` |

镜头动作只影响这个角色在本镜头的第一句。同一个角色相邻两句选到同一个片段时换一个：`talk_calm` 和 `talk_explain` 互换，`point`、`wave`、`react` 改用说话片段；只有情绪是 `angry`、`surprised` 时才允许连续两句 `talk_emphatic`。`point`、`wave`、`react` 只播一个循环，台词还没完就分别接 `talk_explain`、`talk_calm`、`talk_emphatic`。说话人看向上一个说话的人，没有就看向下一个说话的人或最近的人。

台词没有 `emotion` 或者写的是自由文本（如“质疑”）时，按关键词归到六种表情之一，归不了就是 `neutral`。

**听者。** 别人说话时，在场的其他角色播 `listen` 并 `look_at` 说话人，两句之间一秒以内的停顿里保持不变。被说话人看着的那个听者会点一次头（`nod`，1 秒）：台词不短于 2.5 秒时点在句尾，否则点在句后不短于 0.6 秒的停顿里。问句不点头，同一个人两次点头至少隔 2 秒。

不说话的角色如果镜头动作是 `point`、`wave`、`react`、`console`，开场播一次对应片段；`work` 则整个镜头播 `talk_explain`。有人说话时，这些动作让位给 `listen`。

`director.validate_stage(stage)` 按本节检查一份 `stage.json`，返回问题列表，空列表表示可以播放。

## 2. rig.json

`renderer/rig_mesh.py` 从一张正面立绘生成，和 `texture.png` 放在同一个目录。坐标都是贴图像素，原点在左上角，y 向下。

```json
{
  "version": 1,
  "texture": "texture.png",
  "texture_size": [1536, 2048],
  "feet_y": 1987.0,
  "body_height": 1902.0,
  "center_x": 771.5,
  "bones": [
    { "name": "pelvis", "parent": "root", "head": [771.5, 1010.2], "tail": [770.9, 905.4],
      "length": 104.8, "rest_angle": -1.5765 }
  ],
  "groups": [
    { "name": "torso", "z_index": 1,
      "vertices": [[640.0, 520.0], [664.0, 520.0], [652.0, 541.0]],
      "uvs": [[640.0, 520.0], [664.0, 520.0], [652.0, 541.0]],
      "triangles": [[0, 1, 2]],
      "weights": { "pelvis": [0.0, 0.0, 0.2], "torso": [1.0, 1.0, 0.8] } }
  ],
  "face_anchors": {
    "head_box": [600, 90, 340, 380], "face_box": [650, 190, 240, 250],
    "eye_left": { "x": 690, "y": 290, "w": 60, "h": 34 },
    "eye_right": { "x": 800, "y": 290, "w": 60, "h": 34 },
    "brow_left": { "x": 684, "y": 262, "w": 66, "h": 14 },
    "brow_right": { "x": 800, "y": 262, "w": 66, "h": 14 },
    "mouth": { "x": 742, "y": 380, "w": 56, "h": 20 },
    "skin_rgb": [244, 212, 190], "ink_rgb": [52, 40, 44],
    "method": "rtmlib_refined", "confidence": 0.86
  }
}
```

| 字段 | 说明 |
| --- | --- |
| `texture`、`texture_size` | 抠底并放大后的立绘，路径相对 `rig.json`；`[宽, 高]`。 |
| `feet_y` | 前景最低一行的 y。它和 `center_x` 一起是人偶的原点（两脚中间）。 |
| `body_height` | 前景从头顶到脚底的像素高度。动作里的位移都以它为单位。 |
| `center_x` | 两髋中点的 x。 |
| `bones` | 17 根骨骼，父骨在前：`root`、`pelvis`、`torso`、`neck`、`head`，左右各 `upper_arm`、`lower_arm`、`hand`、`upper_leg`、`lower_leg`、`foot`。后缀 `_l`、`_r` 指画面的左、右。`head`、`tail` 是骨骼两端，`rest_angle` 是从 `head` 指向 `tail` 的弧度。`root` 不参与变形。 |
| `groups` | 绘制组 `legs`、`torso`、`head`、`arm_l`、`arm_r`，`z_index` 依次为 0 到 4，所以手臂抬起时画在躯干上面。每组各自一套顶点：`uvs` 与 `vertices` 相同（像素 UV），`triangles` 是组内顶点下标，`weights` 是骨骼名 → 每个顶点的权重，只列用到的骨骼，每个顶点的权重和为 1。 |
| `face_anchors` | 找不到脸时为 `null`，人偶照常动作但没有口型和眨眼。`*_left` 指画面左侧。框是 `{x, y, w, h}`，`head_box`、`face_box` 是 `[x, y, w, h]`。`skin_rgb` 用来盖住原嘴，`ink_rgb` 用来画线。`method` 取 `rtmlib_refined` 或 `skin_blob`。 |

## 3. motions/*.json

一个文件一个片段，文件名就是片段名。骨架对所有角色通用。

```json
{
  "name": "nod", "loop": false, "duration": 1.0, "fps": 24, "foot_lock": true,
  "tags": ["gesture", "agree", "standing"],
  "keyframes": [
    { "t": 0.0, "root_offset": [0.0, 0.0], "bones": { "head": 0.0, "neck": 0.0 } },
    { "t": 0.35, "root_offset": [0.0, 0.002], "bones": { "head": 9.0, "neck": 3.0 } },
    { "t": 1.0, "root_offset": [0.0, 0.0], "bones": { "head": 0.0, "neck": 0.0 } }
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `name` | 片段名，等于文件名。 |
| `loop` | `true` 时按 `duration` 循环，首尾两个关键帧必须相同。`false` 时播完停在最后一帧。 |
| `duration` | 秒，等于最后一个关键帧的 `t`。 |
| `fps` | 制作时参考的帧率，采样与它无关。 |
| `foot_lock` | `true` 时双脚钉在地上，腿由播放器重新解算；`false` 用于走路和挪步。 |
| `tags` | 说明用的标签，至少一个。 |
| `ease` | 可选，`smooth`（默认）、`cosine`、`linear`，关键帧之间的插值方式。 |
| `keyframes[].t` | 秒，从 0 开始严格递增。 |
| `keyframes[].root_offset` | `[dx, dy]`，身高的比例，x 向右、y 向下，绝对值不超过 0.05。 |
| `keyframes[].bones` | 骨骼名 → 相对静止姿势的转角，单位度，顺时针为正。某根骨骼在某个关键帧里没写时，在它自己前后两个关键帧之间插值。 |

转角上限（度，正负对称）：`pelvis`、`torso` 14，`neck`、`head` 16，`upper_arm` 52，`lower_arm` 88，`hand` 35，`upper_leg` 35，`lower_leg` 60，`foot` 20。`root` 不转。`renderer/test_motions.py` 按这组上限检查每个片段；播放器另有一组略宽的硬限幅。片段名不存在时播放器改播 `idle`。

首批片段：

| 片段 | 时长（秒） | 循环 | 钉脚 |
| --- | --- | --- | --- |
| `idle` | 3.2 | 是 | 是 |
| `listen` | 3.0 | 是 | 是 |
| `talk_calm` | 2.4 | 是 | 是 |
| `talk_emphatic` | 2.4 | 是 | 是 |
| `talk_explain` | 2.8 | 是 | 是 |
| `nod` | 1.0 | 否 | 是 |
| `point` | 2.4 | 是 | 是 |
| `wave` | 1.6 | 是 | 是 |
| `react` | 1.2 | 否 | 是 |
| `walk_medium` | 1.2 | 是 | 否 |
| `step_shift` | 2.0 | 是 | 否 |

`director.py` 的 `CLIP_SECONDS` 记着同一组时长，用来决定手势、点头、走路各占多少帧。改片段时长时两边一起改，`renderer/test_director.py` 会比对。
