# Remotion 渲染接手说明

完整产品与运行状态见私有仓库 `/code/SoundBookCF/docs/CURSOR_HANDOFF.md`。第 6 集实片暴露的比例、落地、动作和美术问题及实施顺序见 `/code/SoundBookCF/docs/ANIMATION_REPAIR_PLAN.md`；先修舞台几何契约，再扩展动作。本仓库公开，不能存放生产密钥、私有剧本或用户数据。

`.github/workflows/render.yml` 从 R2 拉 manifest/素材，在 GitHub Actions 使用 MOSS CPU 配音、`renderer/prepare_remotion.py` 按实际音频锁定字幕和镜头时长、Remotion 生成 720p MP4，再上传 R2。最终状态写入 `renders/<job-id>/status.json`，Cloudflare Worker 分钟任务读取并同步到 D1；不依赖 runner 直连 Worker，避免 Cloudflare 拦截 GitHub Actions 出口 IP。旧 `/api/callback` 仅保留兼容。`min_duration_seconds` 是动画故事发布门槛；准备阶段测得不足时写 `qa-error.txt`，失败状态带具体原因。不要通过延长无声静帧绕过门槛。

`remotion/cartoon.tsx` 的 SVG 人物是临时远景技术底座，当前观感粗糙。2026-11-11 的第 3 集实测长 803.563 秒，技术成功，但近景日漫立绘与远景 SVG 小人风格冲突明显。actor 现可携带 `portrait_key` / `mouth_key` / `body_key`，Action 下载复用素材，`prepare_remotion.py` 映射为 Remotion 静态资源。人物近景用肖像，全身镜头用生成的全身抠像立绘和分层腿部运动；缺少全身图时仍会退回 SVG。这个全身路径尚未完成线上视觉验收，也没有可靠的手与物体接触或骨骼绑定。机器人动作在 `remotion/rover.tsx`，科普框架由旧分镜/图解路径渲染。

下一位开发者应先解决无人值守全身角色资产、关节绑定和动作质量检验，再扩大自动发布。任何新增动作必须有可复用的素材约定、失败回退和至少一段从 R2 manifest 到 MP4 的端到端测试。

2026-11-12 已增加 `renderer/quality_report.py`：正式 Action 在上传前验证视频流、时长/分辨率、舞台布局、每场镜头焦点与显式动作事件，并生成逐镜中心帧及联系表。报告与抽帧上传到 `renders/<job-id>/qa/`；技术检查失败会使任务失败，保留报告和失败 MP4。技术通过只标为 `visual_review_required`，因为当前尚无语义视觉审核器。CF 控制端将其显示为“待画面复核”，MCP 同样返回质量状态。不要把 `technical_pass_visual_review_required` 改写成 `approved`。

## 2026-11-12：整集复核与运行时缓存

第 6 集最新技术通过 Action `43244261874` 产出 518.101 秒 720p MP4（8 场、24 镜），但只通过结构化/技术 QA。联系表 `/tmp/soundbook-ep6-pixel-v2-contact.jpg` 已人工检查：场景背景已替换为低细节剧情游戏美术，并保留 48 色以内的建筑细节；角色/设备层仍存在比例与拥挤问题。结果状态必须保持 `visual_review_required`，不能标为成片批准。完整结论见 SoundBookCF 的 `docs/ANIMATION_EXECUTION_STATUS.md`。

MOSS 逐句缓存使用语言、声线和台词内容的 SHA-256 前缀作文件名，因此只改部分对白时可安全复用其他语音。GitHub Actions 使用 `actions/cache/restore` 和 `actions/cache/save`，缓存按内容版本恢复；失败时可保存已经成功合成的音频，避免重做完整 TTS。背景底图经过尺寸/色彩规整，并以 `image-rendering: pixelated` 与角色资产统一为剧情游戏像素视觉；请在更换色板/缩放策略前先对比完整联系表和动作抽帧。

控制端的科学像素故事编剧还会在图片生成前检查每场至少两个有效、带接触时点的动作事件。若模型连续生成不合格场景，生产流程会重试该场，不能通过减少每场动作数来绕开渲染器的质量门槛。

## 2026-11-11：2D 游戏式剧情渲染

`pixel_story` 分支使用 `remotion/pixel-story.tsx`。`renderer/prepare_remotion.py` 将每场背景缩至 320×180、48 色；`renderer/pixel_actor_sprites.py` 用 CPU `isnet-anime` 将 `pixel_body` 原图抠像为 36×64 角色。工作流会缓存模型。无抠像结果时移除 body_file，Remotion 退回程序角色，不阻断整集。舞台深度和承载面已进入布局契约，但仍需完整成片 QA，特别是比例、脚下背景残留、动作可读、画风连续、场景重复和字幕对比度。

## 2026-11-12：联系表复核后的修正

Action `43249555084` 完成 518.101 秒全片，24 镜技术 QA 通过，但人工联系表复核仍拒绝画面验收：背景细节过密，平滑矢量整流罩与像素背景冲突，振动台读起来像空白灰块。不要发布或标记为视觉批准。

后续 Action `43254311510` 完成同一 518.101 秒全片，124,592,043 字节。技术 QA 通过；联系表 `/tmp/soundbook-ep6-v14-contact.jpg` 已人工复核。整流罩改为分阶像素轮廓、振动台增加夹具/台面边缘/控制细节后，前景设备更贴近像素游戏风格；但背景仍偏拥挤，人物/设备比例和动作叙事尚未通过整集视觉验收。继续标记 `visual_review_required`，不得发布为已批准成片。

本轮也把控制端背景提示词限制为 3–5 个大建筑形体、禁止密集管线/网格/小灯点，并升级到 v14 重新生成全部 24 张背景；生图模型仍未稳定遵守简洁背景要求。后续要加可验证的背景复杂度/视觉语义检查和有限自动替换，避免只靠提示词。演员/设备尺度、遮挡、接触动作、连续性及音频/字幕仍需整集复核；尚无验证过且明确免费额度的视觉模型，`visual_semantic_review` 保持 `not_run`。
