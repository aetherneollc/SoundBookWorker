# Remotion 渲染接手说明

完整产品与运行状态见私有仓库 `/code/SoundBookCF/docs/CURSOR_HANDOFF.md`。本仓库公开，不能存放生产密钥、私有剧本或用户数据。

`.github/workflows/render.yml` 从 R2 拉 manifest/素材，在 GitHub Actions 使用 MOSS CPU 配音、`renderer/prepare_remotion.py` 按实际音频锁定字幕和镜头时长、Remotion 生成 720p MP4，再上传 R2 并回调 Cloudflare Worker。`min_duration_seconds` 是动画故事发布门槛；准备阶段测得不足时写 `qa-error.txt`，失败回调带具体原因。不要通过延长无声静帧绕过门槛。

`remotion/cartoon.tsx` 的 SVG 人物是临时远景技术底座，当前观感粗糙。actor 可携带 `portrait_key` / `mouth_key`，Action 自动下载并在 `prepare_remotion.py` 转为本地静态资源；人物近景优先使用系列角色库立绘。此机制只改进近景，不能代替全身分层和自然动作。机器人动作在 `remotion/rover.tsx`，科普框架由旧分镜/图解路径渲染。

下一位开发者应先解决无人值守全身角色资产、关节绑定和动作质量检验，再扩大自动发布。任何新增动作必须有可复用的素材约定、失败回退和至少一段从 R2 manifest 到 MP4 的端到端测试。
