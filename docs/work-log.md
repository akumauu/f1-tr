# F1 TR 工作留痕

> 本文是追加式历史日志，不是当前状态入口。当前进度见 [`progress.md`](progress.md)，已验证实现见 [`implementation-results.md`](implementation-results.md)，文档查询路由见 [`README.md`](README.md)。

## 2026-06-22 16:18 CST

### Step 1：环境与目标复核

- 已读取 `pua` skill，按“先查后问、端到端验证”的方式执行。
- 已复核 `docs/technical-overview.md`，当前开发顺序从 P0 开始：修编译、补依赖、修 API 异步触发 context。
- 已确认当前运行环境：
  - OS：Ubuntu 24.04.4 LTS on WSL2。
  - Python：3.12.3 可用。
  - `go`：当前 shell 不可用。
  - `docker`：当前 shell 不可用。
  - 当时未识别到 git 仓库；本轮 `git status` 可用，当前工作区按 git worktree 处理。
- 当前验收策略：先完成可在本机验证的 Go/Python 测试；Docker/PostgreSQL 端到端测试若环境仍不可用，必须记录为未验收项，不能冒充完成。

### Step 2：P0/P1 代码修复

- 已修复 P0-01：`handlers.go` 补入 `encoding/json`。
- 已修复 P0-03：API ingest/translate 异步触发改为 `context.WithTimeout(context.Background(), ...)`，不再捕获 `c.Request.Context()`。
- 已处理 P1-02：API path/query 参数进入 SQL 前先做 `strconv.Atoi`，非法参数返回 400。
- 已处理 P1-01：DeepSeek retry 每次 attempt 都重建 `http.Request` 和 body reader，避免重试发送空 body。
- 已处理 P2-02：audio proxy 的 `Content-Type` 兜底改为读取上游响应头后再设置响应头。
- 当时待验证：Step 2 完成时 shell 仍无 `go`，需要补 Go 工具链后运行 `gofmt`、`go mod tidy`、`go test ./...`。该项已在后续步骤完成。

### Step 3：Go 工具链处理

- 已通过 apt 安装 Go：`go version go1.22.2 linux/amd64`。
- `go mod tidy` 首次执行时尝试下载 `go1.25.2` toolchain，但 `proxy.golang.org` 连接超时。
- 为保证当前环境可验证，已将 `backend/go.mod` 的 go directive 从 `1.25.2` 收束到本机可用的 `1.22.2`。
- 决策理由：当前项目没有依赖 Go 1.25 专属语法或标准库能力；先保证 P0 编译测试可闭环。

### Step 4：依赖收束与数据库驱动适配

- `github.com/jackc/pgx/v5` 可用缓存版本要求 Go 1.23+，但 Go 1.23/1.24 apt 安装均因 `archive.ubuntu.com` 超时失败。
- GitHub direct、`proxy.golang.org`、`goproxy.cn` 均出现超时，无法稳定下载缺失的 pgx v5 兼容版本。
- 为避免写假数据库 stub，已退回本地已有真实驱动 `github.com/jackc/pgx v3.6.2+incompatible`。
- 已新增项目内 `database.Pool` 包装层，让 API、ingester、translator 不再直接依赖具体 pgx pool 类型。
- 因 `github.com/gabriel-vasile/mimetype` 下载超时，已新增 `third_party/mimetype` 最小兼容模块，并通过 `replace` 指向本地。该模块只满足当前 validator 编译所需调用面。
- 已成功执行：`GOTOOLCHAIN=local GOSUMDB=off go mod tidy`。
- 已成功执行：`gofmt` 覆盖所有改动 Go 文件。

### Step 5：测试补充与阶段验证

- 已新增 `backend/internal/ingester/openf1_client_test.go`：覆盖 `ParseTime`。
- 已新增 `backend/internal/api/handlers_test.go`：覆盖 API 参数转换和 `jsonOrNull`。
- 已新增 `backend/internal/translator/deepseek_client_test.go`：覆盖 DeepSeek retry 时请求 body 非空。
- 已通过 Python 契约测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`。
- 已通过 Go 测试：`GOTOOLCHAIN=local GOSUMDB=off go test ./...`。
- 未完成验收：当前 shell 无 `docker`，PostgreSQL migration、真实 ingest、API 查询端到端测试尚未执行。

### Step 6：最终可运行测试复核

- 已通过 Python 契约测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`，结果 `Ran 5 tests ... OK`。
- 已通过后端 Go 全量测试且禁用缓存：`GOTOOLCHAIN=local GOSUMDB=off go test -count=1 ./...`。
- 已通过本地兼容模块测试：`go test -count=1 ./...` in `third_party/mimetype`。
- 已再次确认 `docker` 不可用，因此本轮不能声明 PostgreSQL/Docker 端到端验收完成。
- 阶段结论：P0 编译与关键单元测试可验收；数据库端到端验收仍需 Docker 或本地 PostgreSQL 环境。

## 2026-06-23 16:00 CST

### Step 7：需求边界收束与文档更新

- 已根据最新产品决策调整技术主线：公网不部署常驻 Gin API，不引入 Cloudflare Workers，不把音频播放作为 v1 必需能力。
- 已明确 v1 架构改为“官方数据离线获取 -> PostgreSQL 持久化与计算 -> Go exporter 导出静态 JSON -> GitHub Pages 前端 SPA 展示”。
- 已明确动态能力边界：动态查询发生在浏览器内，前端读取静态 JSON 后完成年份/分站/session 切换、车手筛选、圈数筛选、TR 搜索和图表交互。
- 已更新 `README.md`，把项目描述从“Go 后端/API 闭环”改为“离线数据生产 + 静态前端展示”。
- 已更新 `docs/technical-overview.md`，新增静态数据合同、exporter 主线、前端动态查询边界和 GitHub Pages 发布方向。
- 已更新 `docs/test-strategy.md`，把后续验收从 API 查询改为 exporter JSON、前端静态读取和浏览器内动态查询。
- 已更新 `tests/test_project_contract.py`，静态契约测试不再要求公网 API 列表，改为校验静态数据合同。
- 已通过 Python 契约测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`，结果 `Ran 5 tests ... OK`。
- 已通过后端 Go 全量测试且禁用缓存：`GOTOOLCHAIN=local GOSUMDB=off go test -count=1 ./...`。
- 已通过本地兼容模块测试：`go test -count=1 ./...` in `third_party/mimetype`。
- 未完成验收：当前 shell 无 `docker`，PostgreSQL migration、真实 ingest、`cmd/exporter`、前端构建和静态发布端到端测试尚未执行。

## 2026-06-23 14:28 CST

### Step 8：按静态发布主线实现 exporter

- 已新增 `backend/cmd/exporter`，默认导出到 `../public/data`，支持 `-version` 和可选 `-migrate`。
- 已新增 `backend/internal/exporter`，把数据库读取和静态文件编排拆开：
  - `PostgresStore` 查询 meetings、sessions、drivers、laps、stints、pit_stops、positions、race_control 和三个分析视图。
  - `Exporter` 生成 `manifest.json`、年份 meeting 索引、meeting session 索引和 session 分片文件。
  - manifest 包含版本、生成时间、年份、分站、session、默认 session、文件路径和记录数。
- 已新增 exporter 单元测试，覆盖 manifest 写入、session 分片文件清单和默认 race session 选择。

### Step 9：修复幂等抓取与 migration runner

- 已新增 `backend/migrations/003_add_idempotency_indexes.up.sql` / `.down.sql`：
  - 建唯一索引前会先用 `ROW_NUMBER()` 清理历史重复行，避免已有重复数据导致迁移失败。
  - `positions` 增加 `(session_key, driver_number, date)` 唯一索引。
  - `race_control` 增加基于 session、时间、消息、可选车手和圈数的唯一表达式索引。
- 已将 migration runner 从硬编码两个 `.up.sql` 改为按文件名排序执行所有 `*.up.sql`。
- 已新增 `backend/internal/database/postgres_test.go`，验证 migration 文件发现顺序。
- 已将 race control 写入改为 `ON CONFLICT DO NOTHING`，不再靠吞插入错误处理重复消息。

### Step 10：实现前端静态 SPA

- 已新增 `frontend`：
  - 零外部依赖构建脚本：`npm run build`。
  - 构建合同检查：`npm test`。
  - 本地静态服务器：`npm run dev`。
  - `frontend/src` 实现静态 SPA，读取 `data/manifest.json` 和 session 分片。
  - `frontend/public/data` 提供 sample 数据，用于无数据库环境下验证页面。
- 前端已实现：
  - 年份、分站、session 选择。
  - 车手筛选、圈数范围筛选、TR 文本搜索。
  - 圈速 canvas 图、指标卡、Team Radio、stint、lap、pit stop、race control 表格。
  - 缺少可选文件时记录 partial 状态，不阻断页面渲染。

### Step 11：验证结果

- 已通过 Python 契约测试：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests`。
- 已通过 Go 全量测试：`GOTOOLCHAIN=local GOSUMDB=off go test -count=1 ./...`。
- 已通过前端构建：`npm run build` in `frontend`。
- 已通过前端构建合同检查：`npm test` in `frontend`。
- 已用 Playwright 打开 `http://localhost:5173` 验证：
  - 页面标题为 `F1 TR Review`。
  - sample manifest 和 session 分片加载成功，状态为 `Ready`。
  - TR 搜索 `brake` 后 Team Radio 计数从 3 变为 1。
  - canvas 非空：检测到非白像素。
  - 390px 移动端下 `body.scrollWidth === window.innerWidth`，未出现横向溢出。
  - 控制台 warning/error 为 0。

### Step 12：已记录但跳过的阻塞项

- 当前 shell 仍无 `docker`，所以 PostgreSQL migration、真实 OpenF1 ingest、真实 translator、真实数据库 exporter 导出和完整端到端链路仍未验收。
- 后台方式启动前端 dev server 会被当前执行环境回收；改用前台会话运行 `node scripts/serve.js 5173` 后验证通过。
- `go doc github.com/jackc/pgx.Rows` 曾长时间无输出，已停止依赖该辅助命令，改由 `go test ./...` 验证真实 pgx 调用面。

## 2026-06-23 17:04 CST

### Step 13：切换优先数据源到 FastF1 + OpenF1

- 已确认最近一次 Catalunya 相关 F1 赛事为 2026 Barcelona-Catalunya Race：
  - OpenF1 `meeting_key=1287`。
  - OpenF1 Race `session_key=11307`。
  - Race 时间为 `2026-06-14T13:00:00+00:00` 到 `2026-06-14T15:00:00+00:00`。
- 已新增 `requirements.txt`，声明 `fastf1>=3.8,<4`。
- 已新增 `tools/export_catalunya_static.py`：
  - 默认从 2026 Barcelona-Catalunya Race 开始导出静态 JSON。
  - FastF1 作为 laps/stints 主路径。
  - OpenF1 作为 meetings/sessions/drivers/team_radio/pit/position/race_control 主路径。
  - FastF1 不可用时，可通过 `--openf1-only` 使用 OpenF1 laps/stints 降级路径。
  - OpenF1 全量 laps/stints 超时时，会按 driver_number 分片重试。
  - OpenF1 drivers 超时时，会从 TR URL 和事件数据中推导 driver_number/name_acronym 占位信息。
  - Team Radio 当前只有 audio URL，导出为 `translation_status=audio_only`。

### Step 14：生成 Catalunya sample 数据并验证前端

- 已用 OpenF1 降级路径生成并替换 `frontend/public/data` sample：
  - 22 位车手。
  - 358 条 lap 原始记录。
  - 355 条有效 lap delta 记录。
  - 70 条 stint 记录。
  - 18 条 degradation 记录。
  - 40 条 Race Team Radio 音频记录。
  - 230 条 position 采样记录。
- 已发现 OpenF1 存在 `lap_duration=0` 异常圈，导致前端 Best Lap 显示 `0.000s`。
- 已修复：
  - Python delta/degradation 计算跳过非正 `lap_duration`。
  - 前端 Best Lap 和 chart 跳过非正 `lap_duration`。
  - `formatLapTime` 对非正值显示 `--`。
- 已通过 Playwright 验证：
  - 页面显示 `Barcelona Grand Prix / Race`。
  - 状态为 `Ready`。
  - Best Lap 显示 `1:20.232`，车手 `NOR`。
  - chart 显示 `355 laps`。
  - Team Radio 显示 `40 messages`。
  - 控制台 warning/error 为 0。

### Step 15：本轮新增阻塞记录

- 尝试将 FastF1 安装到 `/tmp/f1tr-pydeps` 时，PyPI/files.pythonhosted.org 多次连接超时；后续安装进程卡在本地 I/O，已终止。因此 FastF1 真实 session 拉取尚未验收。
- OpenF1 endpoint 有波动：
  - `team_radio?session_key=11307` 单独重试第 2 次成功，返回 40 条。
  - 全量 `laps` 多次超时，按 driver_number fallback 后成功拿到 358 条 lap 原始记录。
  - `pit` 和 `race_control` 在生成 sample 的本轮超时，因此当前 sample 中这两类记录为 0；脚本保留重试与降级能力，API 稳定后可重新生成。

## 2026-07-07 20:20 CST

### Step 16：仓库瘦身、进度同步与 GitHub 上传准备

- 已排查仓库体积来源：
  - `.git` 约 1.2GB，主要来自历史中的松散对象和曾提交过的原始采集数据。
  - `MultiViewer` 约 399MB，为本地下载工具，已在 `.gitignore` 中排除。
  - `data` 约 196MB，主要为临场采集原始快照、日志和分车手拆分结果，不适合进入 Git。
- 已确认当前 Git 历史中包含过 `data/raw/**` 大对象；仅删除工作区文件不能降低远端仓库大小，必须重写历史并执行垃圾回收。
- 已补充 `.gitignore`，明确忽略：
  - `data/raw/`
  - `data/logs/`
  - `.playwright-mcp/`
  - `MultiViewer/`
- 已新增 `docs/multiviewer-api-capture.md`，记录 MultiViewer REST/GraphQL 采集路径、银石关键时间、启动命令和健康检查。
- 已更新 `plan.md` 的任务清单与开发进度，加入仓库瘦身、GitHub 上传和后续大文件管理策略。
- 本轮准备提交的功能进展包括：
  - 前端 live review 中文化与洞察卡片。
  - `timeline.json` 静态时间线数据更新。
  - MultiViewer API 采集脚本与启动脚本。
  - Live Timing 文本解析兼容两段式倒计时。
- GitHub 推送结果见 Step 17。

### Step 17：本地仓库瘦身结果

- 已提交本轮源码与文档改动：`Prepare live review capture and slim repo`。
- 已确认 `HEAD` 历史中的最大对象为前端精简 sample 数据，`data/raw/**`、`data/logs/**`、`MultiViewer/**` 和 `.playwright-mcp/**` 不在主分支源码历史中。
- 已删除本地 `refs/codex/turn-diffs/checkpoints/**` 检查点引用，它们是 `.git` 体积异常增大的主要来源。
- 已执行 `git reflog expire --expire=now --expire-unreachable=now --all` 和 `git gc --prune=now --aggressive`。
- 瘦身结果：
  - `.git` 从约 1.2GB 降到约 0.37MB。
  - `git count-objects -vH` 显示 loose object 为 0，pack 大小约 346KB。
  - 本地工作区只剩被忽略的 `.env`、`data/`、`MultiViewer/`、`frontend/dist/` 和缓存目录。
- 已通过验证：
  - `npm run build` in `frontend`。
  - `npm test` in `frontend`。
  - `python -m unittest discover -s tests`。
- 已推送到 GitHub：
  - 远端：`https://github.com/akumauu/f1-tr.git`
  - 分支：`main`
  - 提交：`0bca725`
- 后续待办：确认 GitHub Pages 发布配置和线上页面加载结果。

## 2026-07-19 17:30 CST

### Step 18：v6 可解释深度层（更强上下文 / 分离度 / 毫秒重建 / 理论上限）

- 目标：让"每场/每 stint 特征值更明显"由更干净的上下文与显式信号/噪声**赚取**，并客观验证车辆+车手分解能否解释实际圈速与名次；复用 v3 Gibbs 骨架，不改 v2–v5 对外行为。
- 新增：`research/modeling/explanatory_depth.py`、`research/run_four_team_explanatory_depth.py`、`research/configs/four_team_explanatory_depth_v6.json`、`tests/test_four_team_explanatory_depth.py`。
- Layer A：从扩展 v4 遥测 parquet 逐圈提取 `distance_to_driver_ahead` 作 dirty-air 代理（全场，`(year,meeting,driver,lap)` 键 merge，94.7% 圈匹配），缓存到 `four_team_traffic_lap_metrics_2023_2025_v6.csv.gz`。上下文特征只按 2024 时间外 `driver×event` MAE 选：`traffic_dirty_air` 保留（2024 0.747→0.728、2025 锁定 0.719→0.677），`compound_tyre_slope` 退化被拒并写入 `context_gate.feature_trace`。
- Layer B：队间/队内方差比 + 复用 Gibbs `team_event` 后验成对排序置信度定级；Hungarian 0.922 / Qatar 0.896 判 high，US 0.645 / Las Vegas 0.666 判 low。修复过程中发现并修正 rank 方向反转（`<`→`>`）。
- Layer C：driver-event 加性毫秒分解 + 重建 R²=0.562、MAE≈274.7 ms、名次构念 Spearman 0.821、106 例"名次≠配速"异常。
- Layer D：只看最快 10% 圈的 half-normal 执行损失校正（硬上限封顶，修复排位异质慢圈把上限抬到 z≈3 的初版缺陷），Q/R 分开再 0.65/0.35 融合成带 CI 的理论上限。
- 验证：`--quick` 冒烟通过；全量 4 链×600、最大尺度 R-hat=1.020；`pytest tests/test_four_team_explanatory_depth.py` 7/7、全量 `pytest tests` 70/70，无回归。产物为 `research/records/four_team_explanatory_depth_2023_2025_v6.{json,md}` 与 traffic manifest。
- 边界：所有"更明显"来自上下文剥离与显式分离度，未改 v3 先验；名次构念只判断配速能否解释名次，策略/可靠性/交通/事故未进入因果赛果；2025 已查看，标记为回顾性解释扩展，不冒充赛前预测。
