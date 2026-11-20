# 渲染接手说明

本仓库公开，不能存放生产密钥、私有剧本或用户数据，也不提交生成的音频、视频、立绘和 rig（`media/`、`public/media/` 已在 `.gitignore`）。

这份文档分两部分：前面是现在的状态，后面“旧版（Legacy）”是 Remotion 故事剪纸偶和像素故事时期的记录，只作历史参考。

## 现状（2026-11-20，分支 `feat/godot2d`）

### 两条渲染路径

`.github/workflows/render.yml` 从 R2 拉 manifest 和素材，用 MOSS 在 CPU 上配音，然后按 `manifest.renderer` 分支（`Select renderer` 步骤）：

| `manifest.renderer` | 内容 | 流程 |
| --- | --- | --- |
| `"godot2d"` | 动画故事，**故事片的主流程** | `renderer/prepare_stage.py` → `scripts/render_godot2d.sh`（Godot 4.7.2，xvfb + llvmpipe 软件渲染）→ `renderer/mux.py` |
| 没有这个字段 | 科普片、科学框架连续剧；以及**旧版**故事剪纸偶 | `renderer/prepare_remotion.py` → `npm run render`（Remotion） |

控制端（SoundBookCF）里故事的渲染风格选 `godot2d` 时才会写出 `renderer: "godot2d"`。风格是 `cartoon` 的老故事不带这个字段，仍走旧版 Remotion 剪纸偶；科普片（`explainer`）固定是 `science`，一直走 Remotion，这条路径没有变，也不打算替换。

工作流跑完后把成片传到 `renders/<job_id>/output.mp4`，并向 `CALLBACK_URL`（控制端的 `/api/callback`）回报 `running`、`succeeded`、`failed`。`qa_mode=true` 时不回报、不上传 R2，成片作为工作流产物保留 7 天。

### Godot 2D 网格人偶

三份 JSON 的字段、导演规则和兜底都写在 `docs/STAGE2D_CONTRACT.md`，改代码前先读它。流程是：

1. `renderer/rig_mesh.py`：一张正面 A-pose 立绘 → 抠底、17 根骨骼、5 个绘制组的蒙皮网格（`rig.json` + `texture.png`）。`renderer/face_anchors.py` 找出眼、眉、嘴的位置，口型、眨眼和表情都靠它。rig 按文件哈希缓存在 `media/rigs/`。
2. `renderer/prepare_stage.py`：按实测配音时长锁定镜头和字幕（`renderer/timeline_common.py`，和 Remotion 共用），生成口型（有 `RHUBARB_BIN` 用 Rhubarb，否则用音量），再交给 `renderer/director.py` 排出切镜、手势、听者反应、站姿、走路和阿拓的站位，最后用 `validate_stage` 校验，不合契约就不出片。
3. `godot/player/`：`main.gd` 逐帧播放 `stage.json`，`puppet.gd` 蒙皮和钉脚，`motion.gd` 采样动作，`face.gd` 画嘴、眼、眉，`camera_director.gd` 取景，`stage.gd` 三层视差布景，`captions.gd`、`title_card.gd` 字幕和片头片尾卡，`rover.gd` 阿拓。
4. `renderer/mux.py`：把 `stage.json` 的音轨混到无声 AVI 上，输出 H.264/AAC。

### 19 个动作

`godot/motions/*.json`，所有角色共用，`renderer/test_motions.py` 校验：

| 类别 | 片段 |
| --- | --- |
| 基础 | `idle`、`listen`、`nod` |
| 说话 | `talk_calm`、`talk_emphatic`、`talk_explain` |
| 手势 | `point`、`wave`、`react`、`surprised` |
| 站姿 | `operate`、`carry`、`cross_arms`、`angry`、`sad`、`sit`、`stand` |
| 移动 | `walk_medium`、`step_shift` |

控制端的动作词表（`SoundBookCF/src/cartoon_stage.ts` 的 `CARTOON_ACTIONS`）经 `director.map_action` 映射到这些片段：`work`、`console` 是 `operate`，`walk` 按位移变成中景 `walk_medium` 或原地 `step_shift`。

### 阿拓 Rover2D

`godot/player/rover.gd` 读 `renderer/rig_robot.py` 生成的 `robot_rig.json`：车身、按实际位移滚动的车轮、两段 IK 机械臂、信号灯。高度是人物的 0.53 倍。控制端只导出 `rover: {action, speaker}`，站位由导演补：停在远离人物的一侧，第一次 `drive` 从画面边缘驶入，切镜时位置和朝向不变，阿拓是主体时镜头保持全景。规则见契约 1 节的 `rover` 和 1.4 节第 8 条。

### M4 端到端验收

用控制端 `godot2d` 导出格式手写了一集 manifest（三场、11 句对白、林岚和陈工两个网格人偶、阿拓、两个场景、片头和片尾卡），在本机走完 `prepare_stage.py` → Godot `--write-movie` → `mux.py`。素材和成片都在 `media/m4_e2e_episode/`，不进仓库。

| 项目 | 结果 |
| --- | --- |
| 成片 | `episode_e2e.mp4`，1280×720，24 fps，H.264 High + AAC 48 kHz 立体声 |
| 时长 | 55.667 秒，1336 帧（`stage.json` 是 1335 帧，Godot 录像在结尾多录一帧） |
| 文件大小 | 21,048,307 字节 |
| 渲染速度 | 26.99 帧/秒（Windows，RTX 5060 Laptop，OpenGL 兼容模式，含 AVI 编码） |
| 音画 | 11 句对白的能量都落在各自字幕区间内，句间静音；片尾卡无声 |

配音是按字合成的测试音（不是 MOSS），机器人立绘是用已发布的车身和车轮切片拼回去的，所以 manifest 的 `robot_rig` 多带了 `wheel_hints` 和 `native_facing`。CI 的 xvfb + llvmpipe 路径这一轮没有重跑，软件渲染的帧率要另测。

抽了 10 帧人工看过（片头卡、双人全景、正反打、口型、`surprised`/`operate`/`cross_arms`/`angry`、中景走路、挥手、阿拓驶入、机械臂作业、片尾卡），加上从 MP4 里另抽的 9 帧，没有发现切头、字幕出框、遮挡或音画错位。

### 这一轮修掉的缺陷

都是端到端出片后在画面上看出来的：

- **没有口型、眨眼和表情。** `rig_mesh.py` 只按 `renderer.face_anchors` 这个名字导入人脸检测，而工作流是 `python renderer/prepare_stage.py` 直接跑脚本，这个包名不存在，于是所有 rig 的 `face_anchors` 都是 `null`，而且没有任何提示。现在两种名字都试，失败会打印；缓存盐值升到 `godot2d-mesh-v2`，旧的无脸缓存不会被复用。
- **阿拓只有玩具大小。** 原来按 1024 画布的宽度缩放，画布大半是透明边。现在按实际画出的高度缩放。同时修了：车轮被车身盖住半截、三轮车多补一个悬空的程序轮、机械臂肩部悬在车外、行驶和待机时机械臂也追着作业目标；有车身图时不再叠画程序桅杆和轮腿，并加了接地阴影。
- **阿拓 `drive` 时原地不动。** 控制端不导出站位，导演原来把起点和终点都填成同一个默认值。
- **阿拓说话或作业时镜头切到站着不动的人。** 双人景和中景会把阿拓裁出画面。
- **M2 的站姿和表情动作在对白里从不出现。** 听别人说话时一律被 `listen` 盖掉，自己说话时只认 `operate`。
- **`operate`、`carry`、`cross_arms`、`angry`、`sit` 在播放器里和 `idle` 一个样。** 播放器的前臂限位把向内折的前臂顶了回去，Python 预览没有这道限位所以没发现。这五个片段现在写了 `"arm_guard": false`。
- **`point` 和 `talk_explain` 总是朝画面左边伸手。** 站在左边的人会背对着对象比划。现在按对象所在一侧镜像姿势。

### 已知限制

- `sit`、`stand` 不钉脚，人物会下沉约一成身高，没有桌椅遮挡时脚在地面线以下。控制端的词表里有这两个词，编剧可能会用到。
- 不同立绘的手臂张开角度不一样（林岚约 40°，陈工约 15°），放开前臂限位的五个片段在两人身上的样子不同：陈工的前臂会交叉得更深。
- 嘴部只有画上去的口型，没有牙齿和舌头的细分；表情主要靠眉毛，幅度小。
- 立绘只有正面，远距离走路只能拍中景（`docs/SIDE_WALK_EXPERIMENT.md` 记录了侧面走路的试验，结论是不接入）。
- Godot 录像比 `stage.json` 多一帧结尾帧，`mux.py` 按录像帧数定长，所以成片比舞台时间长 1/24 秒。
- `renderer/quality_report.py` 只认 Remotion 的 props，Godot 2D 成片目前没有自动质检，靠 `STAGE_SAMPLE_FRAMES` 抽帧人工看。

### 本机出片和测试

命令见 `README.md` 的 “Run it locally”。本机用的是 WinGet 装的 Godot 4.7.2（`%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine*\...\*_console.exe`），字体用 `C:/Windows/Fonts/msyh.ttc`。

```bash
python -m unittest -v renderer/test_rig_mesh.py renderer/test_face_anchors.py renderer/test_motions.py \
  renderer/test_director.py renderer/test_prepare_stage.py renderer/test_mux.py \
  renderer/test_rover_stage.py renderer/test_side_walk.py
```

## 旧版（Legacy）：Remotion 故事剪纸偶与像素故事

以下是 2026-11-11 到 11-12 的记录，对象是 Remotion 的故事路径：`remotion/story.tsx`、`remotion/puppet.tsx`、`remotion/cartoon.tsx`、`remotion/pixel-story.tsx`、`remotion/robot.tsx`、`remotion/rover.tsx`，以及 `renderer/rig_characters.py`、`renderer/pixel_actor_sprites.py`、`renderer/layer_shots.py`。这些文件还在，`cartoon` 风格的老故事仍用它们出片，但不再增加新能力，新的故事工作都在 Godot 2D 上做。科普片用的 Remotion 图解路径不属于旧版。

读下面的记录时注意几处和现在的代码对不上：

- 文中说最终状态写到 `renders/<job-id>/status.json`、`/api/callback` 只作兼容。现在的 `render.yml` 没有写 `status.json` 的步骤，状态只通过 `/api/callback` 回报。
- 文中说正式 Action 会跑 `renderer/quality_report.py` 并把报告传到 `renders/<job-id>/qa/`。脚本和它的测试还在，但现在的 `render.yml` 没有调用它，也没有 `min_duration_seconds` / `qa-error.txt` 这道门槛。
- 文中提到的 `SoundBookCF/docs/CURSOR_HANDOFF.md`、`ANIMATION_REPAIR_PLAN.md`、`ANIMATION_EXECUTION_STATUS.md` 不在 SoundBookCF 现在的 `docs/` 里，那里只有 `ANIMATION_PIPELINE.md` 和 `MCP.md`。

### 原始记录

完整产品与运行状态见私有仓库 `/code/SoundBookCF/docs/CURSOR_HANDOFF.md`。第 6 集实片暴露的比例、落地、动作和美术问题及实施顺序见 `/code/SoundBookCF/docs/ANIMATION_REPAIR_PLAN.md`；先修舞台几何契约，再扩展动作。

`.github/workflows/render.yml` 从 R2 拉 manifest/素材，在 GitHub Actions 使用 MOSS CPU 配音、`renderer/prepare_remotion.py` 按实际音频锁定字幕和镜头时长、Remotion 生成 720p MP4，再上传 R2。最终状态写入 `renders/<job-id>/status.json`，Cloudflare Worker 分钟任务读取并同步到 D1；不依赖 runner 直连 Worker，避免 Cloudflare 拦截 GitHub Actions 出口 IP。旧 `/api/callback` 仅保留兼容。`min_duration_seconds` 是动画故事发布门槛；准备阶段测得不足时写 `qa-error.txt`，失败状态带具体原因。不要通过延长无声静帧绕过门槛。

`remotion/cartoon.tsx` 的 SVG 人物是临时远景技术底座，当前观感粗糙。2026-11-11 的第 3 集实测长 803.563 秒，技术成功，但近景日漫立绘与远景 SVG 小人风格冲突明显。actor 现可携带 `portrait_key` / `mouth_key` / `body_key`，Action 下载复用素材，`prepare_remotion.py` 映射为 Remotion 静态资源。人物近景用肖像，全身镜头用生成的全身抠像立绘和分层腿部运动；缺少全身图时仍会退回 SVG。这个全身路径尚未完成线上视觉验收，也没有可靠的手与物体接触或骨骼绑定。机器人动作在 `remotion/rover.tsx`，科普框架由旧分镜/图解路径渲染。

下一位开发者应先解决无人值守全身角色资产、关节绑定和动作质量检验，再扩大自动发布。任何新增动作必须有可复用的素材约定、失败回退和至少一段从 R2 manifest 到 MP4 的端到端测试。

2026-11-12 已增加 `renderer/quality_report.py`：正式 Action 在上传前验证视频流、时长/分辨率、舞台布局、每场镜头焦点与显式动作事件，并生成逐镜中心帧及联系表。报告与抽帧上传到 `renders/<job-id>/qa/`；技术检查失败会使任务失败，保留报告和失败 MP4。技术通过只标为 `visual_review_required`，因为当前尚无语义视觉审核器。CF 控制端将其显示为“待画面复核”，MCP 同样返回质量状态。不要把 `technical_pass_visual_review_required` 改写成 `approved`。

### 2026-11-12：整集复核与运行时缓存

第 6 集最新技术通过 Action `43244261874` 产出 518.101 秒 720p MP4（8 场、24 镜），但只通过结构化/技术 QA。联系表 `/tmp/soundbook-ep6-pixel-v2-contact.jpg` 已人工检查：场景背景已替换为低细节剧情游戏美术，并保留 48 色以内的建筑细节；角色/设备层仍存在比例与拥挤问题。结果状态必须保持 `visual_review_required`，不能标为成片批准。完整结论见 SoundBookCF 的 `docs/ANIMATION_EXECUTION_STATUS.md`。

MOSS 逐句缓存使用语言、声线和台词内容的 SHA-256 前缀作文件名，因此只改部分对白时可安全复用其他语音。GitHub Actions 使用 `actions/cache/restore` 和 `actions/cache/save`，缓存按内容版本恢复；失败时可保存已经成功合成的音频，避免重做完整 TTS。背景底图经过尺寸/色彩规整，并以 `image-rendering: pixelated` 与角色资产统一为剧情游戏像素视觉；请在更换色板/缩放策略前先对比完整联系表和动作抽帧。

控制端的科学像素故事编剧还会在图片生成前检查每场至少两个有效、带接触时点的动作事件。若模型连续生成不合格场景，生产流程会重试该场，不能通过减少每场动作数来绕开渲染器的质量门槛。

### 2026-11-11：2D 游戏式剧情渲染

`pixel_story` 分支使用 `remotion/pixel-story.tsx`。`renderer/prepare_remotion.py` 将每场背景缩至 320×180、48 色；`renderer/pixel_actor_sprites.py` 用 CPU `isnet-anime` 将 `pixel_body` 原图抠像为 36×64 角色。工作流会缓存模型。无抠像结果时移除 body_file，Remotion 退回程序角色，不阻断整集。舞台深度和承载面已进入布局契约，但仍需完整成片 QA，特别是比例、脚下背景残留、动作可读、画风连续、场景重复和字幕对比度。

### 2026-11-12：联系表复核后的修正

Action `43249555084` 完成 518.101 秒全片，24 镜技术 QA 通过，但人工联系表复核仍拒绝画面验收：背景细节过密，平滑矢量整流罩与像素背景冲突，振动台读起来像空白灰块。不要发布或标记为视觉批准。

后续 Action `43254311510` 完成同一 518.101 秒全片，124,592,043 字节。技术 QA 通过；联系表 `/tmp/soundbook-ep6-v14-contact.jpg` 已人工复核。整流罩改为分阶像素轮廓、振动台增加夹具/台面边缘/控制细节后，前景设备更贴近像素游戏风格；但背景仍偏拥挤，人物/设备比例和动作叙事尚未通过整集视觉验收。继续标记 `visual_review_required`，不得发布为已批准成片。

本轮也把控制端背景提示词限制为 3–5 个大建筑形体、禁止密集管线/网格/小灯点，并升级到 v14 重新生成全部 24 张背景；生图模型仍未稳定遵守简洁背景要求。后续要加可验证的背景复杂度/视觉语义检查和有限自动替换，避免只靠提示词。演员/设备尺度、遮挡、接触动作、连续性及音频/字幕仍需整集复核；尚无验证过且明确免费额度的视觉模型，`visual_semantic_review` 保持 `not_run`。

修正地点解析后的 Action `43264011872`、render `b22bfbc6-999b-46a2-9945-17c8cc7d1ffd` 完成 518.101 秒全片，技术 QA 通过。联系表 `/tmp/soundbook-ep6-v16-contact.jpg` 证明模型仍在室内背景中画出城市和天空，因此新修复不再依赖室内生图提示词：`pixel-story.tsx` 对室内场景忽略 AI 背景，`game/scene.tsx` 以程序化封闭墙面、地板、梁柱和远景架绘制像素舞台；室外继续用 AI 背景。本版尚未全片复渲，需以新的联系表复核。

首次程序舞台 Action `43267534964`、render `4e9cbeb9-0cc7-464b-8d35-91c63707fb42` 全片技术检查通过。联系表 `/tmp/soundbook-ep6-v17-contact.jpg` 表明，仍有几镜因旧版 `cartoon.world.room` 字段不准确而套回 AI 城市背景。新提交强制所有识别到真空舱/装配/振动台/整流罩的 setpiece 走室内像素舞台，并固定为室内房间。需要在该最新提交上再跑完整片，检查 24 镜都不混入室外背景。比例、可读动作和声音同步仍未完整验收。

修正后的 Action `43271437318`、render `16489fbe-a4bd-416f-a833-c4c107612e65` 完整技术通过，联系表 `/tmp/soundbook-ep6-v18-contact.jpg` 已逐镜检查：工程场景不再混入城市/天空背景，风格统一；程序化墙面较朴素，动作叙事和完整音频/字幕仍需验收。状态继续保持 `visual_review_required`。SoundBookWorker 提交 `a5e5c7e`，SoundBookCF 当前场景解析提交 `5aef198`；项目中还有后续文档提交。测试：TS 类型检查、8 项 renderer/QA Python 测试通过。

2026-11-12 后续 QA 改进：`quality_report.py` 为每个结构化动作保存动作前/接触点/动作后三帧，生成 `action-contact.jpg`，并计算避开字幕区域的首尾帧平均变化量。它提供可复核证据，不会把“有像素变化”直接判为动作语义合格。控制端新增 `/api/render-jobs/:id/quality/action-contact` 读取动作联系表；下一次 Action 要确认上传、报告字段和路由都正常。

### 2026-11-12：动作事件绑定的实片发现

第 6 集 Action `43279662954` 首次上传了 24 组动作前/接触/后帧，7/24 镜低变化。提交 `4cd5d1a` 将 reach/press/secure/inspect 接到备用骨骼手臂，但整集 Action `43283930114` 复测仍有 7/24 低变化；因此不能把“动作事件有送到 React”视为画面动作验收。

从生产 manifest 查到真正漏项：事件 `subject=character,target=rover` 被误认为机器人动作而排除了人物；另一个分支只给机器人抠像素材做上下浮动，未执行伸手/采样动作。Worker 提交 `6ae45d1` 已按主体归属角色动作，在抠像机器人图层上加带接触时点的机械臂并驱动程序机器人手臂。整集 Action `43287204831` 技术成功，但 QA 仍报告 7/24 低变化；进一步发现该 QA 只对比动作开始和结束帧，而两个时点都处于待机姿势，导致真实伸出/收回动作被误判为无运动。

Worker 提交 `4e1529e` 已修正 QA：分别比较开始→接触与接触→结束，并取较大差异作为“可见变化”信号；同时让动作宽景以角色/机器人为中心适度推近，并让事件驱动的机器人动作覆盖旧的静态信号动作。新整集 Action `43290150423`、render `660c25e5-ca7c-4861-b00c-2a7dd2d50506` 正在跑。要对照新 QA 字段 `before_contact_difference` / `contact_after_difference` 和动作联系表确认变化；像素差异只作筛查，不能替代人工确认动作是否触碰到目标。

Action `43290150423` 完成后，低变化从原 QA 的 7/24（错误首尾比较）修正为 6/24（分段比较）。放大联系表显示个别人物仍离目标太远，手臂伸出没有形成明确接触。Worker 提交 `6a77c3b` 按 `action_event.target` 与舞台位置计算角色靠近点：人物在动作中先走到设备/机器人旁，再伸手；宽景动作镜头也把操作者和目标一起收入构图。

最新验证 Action `43293543630`、render `dde14545-b1dd-4245-9296-bfdb991ff2b2` 正在运行。完成后重点放大之前低变化镜头，并核对动作前/接触/动作后三帧、角色是否接近目标、机械臂是否伸出。若仍不能辨认接触，继续改舞台和目标动画；不要用像素门槛下调来宣称过关。

最新验证 Action `43293543630` 已结束，技术通过，24 镜/8 场，518.101 秒 720p。新的接触点前后 QA 将低变化标记降至 3/24；联系表放大后确认，剩余三镜里的机器人机械臂确实在伸出/调整，但在整画面中的面积较小，仍应看完整段判断动作能否读懂。角色先走近目标再操作后，之前低变化的人物镜头动作清晰度改善。最终状态继续 `visual_review_required`；语义审核、音画字幕同步/尾句听审和完整 8 场逐镜复核仍未通过。控制端工作室页为 `https://soundbook-studio.pages.dev/#/story/41bb60b2-3464-4e46-8d22-b2ef721493f1/episode/6cbb5ee9-4da3-44d0-8e18-90f350f5ff3f`。

### 2026-11-12：音频与字幕边界质检

`prepare_remotion.py` 现在会把每条对白音轨的场次、句号、帧长写入渲染 props，并给相应字幕及口型节奏写入同一索引。`quality_report.py` 校验每句字幕是否覆盖实际对白音频、口型提示是否存在且处于该句音频范围、对白/字幕/口型提示是否越过片尾；缺失字幕或节奏提示会令 QA 失败。这个门禁只验证时间边界，不评价 TTS 音色自然度、角色差异或字幕翻译质量。验证：renderer Python 单测 12 项通过，TypeScript 类型检查通过。后续 Action 仍需验证真实长片数据和报告上传。
