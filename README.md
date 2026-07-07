# F1 TR

F1 TR 是一个用于 F1 赛后复盘的非严肃专业平台。它聚合全场比赛 Team Radio 原文与中文翻译、圈速数据，并基于圈速衍生计算圈速变化、轮胎衰退和关键阶段表现波动。

它的用途是帮助用户在赛后快速理解“比赛过程中发生了什么、车手和车队说了什么、圈速表现如何变化”。它不是官方技术分析工具，也不用于车队工程决策、判罚判断或高精度专业建模。

当前项目是早期“离线数据生产 + 静态前端展示”系统。当前优先数据源是 Python FastF1 + 免认证 OpenF1 HTTP API，先从 2026 Barcelona-Catalunya Race 开始生成静态 JSON；Go/PostgreSQL 链路保留为后续数据库生产路径。

开发交接与后续计划以以下文档为准：

- [开发技术统一文档](docs/technical-overview.md)
- [测试策略](docs/test-strategy.md)

当前已知状态：

- 后端架构骨架存在，P0 编译阻断已修复。
- 本机 shell 已安装 Go 1.22.2，`go test ./...` 已通过。
- `cmd/exporter` 已实现基础静态 JSON 导出能力，Go 单元测试覆盖 manifest 和 session 文件合同。
- `tools/export_catalunya_static.py` 已实现 Catalunya 默认采集器，目标为 `meeting_key=1287`、`session_key=11307`。
- 前端 sample 数据已替换为 OpenF1 Catalunya Race：22 位车手、358 圈、70 段 stint、40 条 TR 音频记录。
- `frontend` 已实现基础静态 SPA，`npm run build`、`npm test` 和本地浏览器 smoke test 已通过。
- 本机 shell 仍缺少 `docker`，PostgreSQL 端到端运行尚未验收。
- v1 先收束为 Python/Go CLI + 静态 JSON + 前端 SPA；公网不部署常驻 API 服务。
- 前端部署目标是 GitHub Pages，读取导出的 JSON 后在浏览器内完成分站/session 切换、车手筛选、圈数筛选、TR 搜索和图表交互。
- Cloudflare Workers、音频代理、音频播放、STT 音频转文字延后。
- 2026-07-07 进度：live review 前端已中文化并加入洞察卡片；MultiViewer 本地 API 采集脚本已补齐；本地仓库已瘦身，`.git` 从约 1.2GB 降到约 0.37MB，原始采集数据和运行日志只保留在本地，不进入 Git 历史。

仓库体积约定：

- Git 只保留源码、文档、构建脚本和可发布的精简 sample 数据。
- `data/raw/`、`data/logs/`、`.playwright-mcp/` 和 `MultiViewer/` 都是本地产物，默认忽略。
- 需要共享完整原始采集包时，优先使用 GitHub Release、外部对象存储或 Git LFS，避免直接提交到主分支历史。
