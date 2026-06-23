# F1 TR 开发技术统一文档

本文档用于统一 F1 TR 后续开发的技术边界、当前状态、修复优先级和验收路径。它不是产品宣传稿，而是开发交接与排期基准。

状态基于 2026-06-23 的仓库实现、需求调整与测试结果：当前目录包含 Go 后端、Python Catalunya 静态采集器、PostgreSQL migration、`docker-compose.yml`、`.env.example`、文档和前端静态 SPA。Go 工具链已安装并通过 `go test ./...`；Python 单元测试、前端 `npm run build`、`npm test` 和本地浏览器 smoke test 已通过。当前 shell 仍缺少 `docker`，所以 PostgreSQL migration、真实数据库 exporter 导出和数据库端到端链路尚未完成验收；FastF1 依赖安装因 PyPI/files.pythonhosted.org 超时和本地 I/O 卡住未完成。

## 1. 产品定位

F1 TR 是一个用于 F1 赛后复盘的非严肃专业平台。它面向的是赛后内容回看、数据辅助理解和爱好者分析，不承担车队工程决策、官方技术判罚、博彩预测或严肃专业赛事分析职责。

平台核心价值：

- 汇总全场比赛 Team Radio，包括原文、中文翻译和基础语义标签。
- 汇总全场圈速数据，支持按车手、圈数、轮胎 stint 查看。
- 基于圈速数据做衍生计算，帮助观察圈速变化、轮胎衰退和关键阶段表现波动。
- 把 Team Radio 标记到比赛时间线或近似圈数上，辅助理解“车手说了什么”和“当时比赛发生了什么”。

产品表达应保持“赛后复盘”和“辅助理解”的定位。所有 delta、degradation、近似圈数和情绪/意图分类都应视为分析线索，不应包装成官方结论或高精度专业模型。

## 2. 需求边界

### v1 主线

v1 收束为“离线数据生产 + 静态发布 + 前端本地动态查询”的赛后复盘系统。当前优先路径是 Python 采集器从 FastF1 和免认证 OpenF1 HTTP API 生成静态 JSON，前端 SPA 直接读取 JSON 后在浏览器内完成筛选、搜索、排序、对比和图表交互。Go + PostgreSQL 链路保留为后续数据库生产路径。

v1 必须交付：

- 数据采集：FastF1 提供圈速/stint 主路径，OpenF1 提供 meetings、sessions、drivers、team_radio、pit_stops、positions、race_control，并在 FastF1 不可用时为 laps/stints 提供降级。
- PostgreSQL 持久化：原始事实表、基础唯一约束、可重复执行迁移。
- Team Radio 复盘数据：全场 TR 原文、中文翻译、意图/情绪/实体等基础标签。
- 圈速复盘数据：全场圈速、分段成绩、轮胎 stint、进站与位置变化。
- 数据库计算：圈速 delta、stint 内圈速变化、轮胎衰退率、Team Radio 与近似圈数关联。
- CLI：保留 Python Catalunya 静态采集器和 Go `ingester` / `translator` / `exporter`；Python 采集器直接输出前端静态 JSON，Go exporter 从 PostgreSQL 导出同一合同。
- 前端 SPA：部署到 GitHub Pages，读取静态 JSON，在浏览器内提供动态查询能力。
- Team Radio 翻译链路：只处理已有英文转录 `transcript_en`，不在 v1 内实现音频转文字。
- 测试基线：编译测试、关键单元测试、静态契约测试、最小集成测试说明。

### v1 不做

- 不提供常驻 Gin API 服务器给公网用户访问；Gin API 可作为本地调试遗留模块，后续应弱化或移除。
- 不实现 Cloudflare Workers 缓存与音频边缘代理；GitHub Pages 静态托管优先。
- 不实现音频播放作为 v1 必需能力；暂不处理音频 CORS、防盗链和代理。
- 不实现 STT / ASR 音频转文字；`team_radio.recording_url` 只是录音 URL。
- 不做实时比赛流处理；当前按赛后批处理设计。
- 不做复杂策略模型、异常圈深度清洗或机器学习分析。
- 不输出官方级、工程级或高精度专业结论；所有分析结果只作为赛后复盘线索。

### 后续阶段

- v1.1：完成 FastF1 依赖安装与真实 FastF1 拉取、用真实 PostgreSQL 数据验收 exporter、补 GitHub Pages 发布流程、强化静态 JSON schema 与前端本地索引。
- v1.2：接入外部 `transcript_en` 导入或 STT 管道。
- v2：音频播放、边缘代理、更完整可视化和更细的数据清洗。

## 3. 当前架构

### 模块职责

- `backend/cmd/api`：旧版 Gin API 入口，后续不作为公网部署主线；可保留给本地调试或逐步移除。
- `backend/cmd/ingester`：CLI 数据采集入口，使用 `context.Background()`，当前比 API 触发路径更可靠。
- `backend/cmd/translator`：CLI 翻译入口，处理 `team_radio.translation_status = 'pending'` 的记录。
- `backend/cmd/exporter`：静态 JSON 导出 CLI，查询 PostgreSQL 表/视图并导出 manifest 与 session 分片给前端。
- `backend/internal/exporter`：exporter 可测试核心，负责 manifest、文件路径、记录数和 session 数据分片编排。
- `tools/export_catalunya_static.py`：当前优先的 Python 静态采集器，默认目标为 2026 Barcelona-Catalunya Race（OpenF1 `meeting_key=1287`、`session_key=11307`），FastF1 用于圈速/stint 主路径，OpenF1 用于 TR 和事件数据。
- `requirements.txt`：Python 采集器依赖入口，当前包含 `fastf1>=3.8,<4`。
- `backend/internal/api`：HTTP router 与 handlers。
- `backend/internal/ingester`：OpenF1 HTTP client、并发 fetcher、PostgreSQL 写入 pipeline。
- `backend/internal/translator`：DeepSeek client、术语表、批处理 worker。
- `backend/internal/database`：PostgreSQL 连接池包装层与 migration runner。当前为适配本机 Go 1.22 验证环境，使用 `github.com/jackc/pgx v3` 的 `ConnPool` 封装出项目内 `database.Pool`。
- `backend/migrations`：事实表与分析视图。
- `frontend`：零外部依赖的静态 SPA，读取 `data/manifest.json` 和 session JSON 分片，在浏览器内完成筛选、搜索、表格和圈速图表展示。

### 数据流

Python 静态数据生产流：

1. `tools/export_catalunya_static.py` 默认锁定 2026 Barcelona-Catalunya Race。
2. FastF1 读取 race session 的 laps、driver results 和轮胎 stint 信息；如果 FastF1 不可用，则 OpenF1 laps/stints 作为降级来源。
3. OpenF1 读取 meetings、sessions、drivers、team_radio、pit、position、race_control。
4. 采集器生成 `manifest.json` 和 session 分片 JSON，写入 `public/data` 或 `frontend/public/data`。
5. 前端 SPA 从 GitHub Pages 读取 JSON，在浏览器内执行动态筛选和图表交互。

Go/PostgreSQL 数据生产流：

1. `OpenF1Client` 请求 OpenF1 JSON API。
2. `Fetcher` 按 year 或 session key 组织抓取，session 详情并发抓取多个数据域。
3. `Pipeline` 做基础字段映射、时间解析、去重或 upsert。
4. PostgreSQL 保存事实表。
5. SQL view 计算派生指标。
6. `cmd/exporter` 查询事实表和视图，导出静态 JSON。

Team Radio 翻译流：

1. OpenF1 `team_radio` 只提供录音 URL 与基础元数据。
2. `team_radio.transcript_en` 需要由外部流程写入；当前项目没有 STT。
3. translator worker 读取 `pending` 记录。
4. DeepSeek client 生成中文翻译、意图、情绪和结构化实体。
5. worker 写回 `transcript_zh`、`intent`、`sentiment`、`key_entities` 和 `translation_status`。

## 4. 当前进度审计

总体判断：P0 编译阻断已解除，项目已从“能读不能跑”推进到“后端可编译、Python Catalunya 采集器可生成真实静态数据、前端可展示真实 Catalunya sample”的早期离线数据产品。下一优先级是完成 FastF1 真实拉取验收、补齐 OpenF1 波动 endpoint 的重试/缓存策略、以及 PostgreSQL 环境下的 migration、ingest、translator、真实数据库 exporter 验收。

| 维度 | 状态 | 结论 |
| --- | --- | --- |
| 架构与目录 | 基本完整 | Go 后端模块边界清楚 |
| 编译 | 已通过 | `GOTOOLCHAIN=local GOSUMDB=off go test ./...` 通过 |
| API 触发链路 | 已修复，非公网主线 | goroutine 已改用独立后台 context；后续仅作本地调试 |
| CLI 链路 | 部分完成 | `ingester`、`translator`、`exporter` 可编译；真实数据库端到端仍需 PostgreSQL/Docker |
| 数据库迁移 | 部分完成 | 表、视图和幂等索引存在；runner 可按序执行 `*.up.sql`，仍无版本表 |
| 翻译功能 | 部分完成 | 无 STT，空转风险高 |
| Python 采集器 | 基础完成 | Catalunya 默认目标可输出静态 JSON；OpenF1 smoke 已拿到 22 drivers、358 laps、70 stints、40 TR |
| Exporter | 已实现基础版本 | 可导出 manifest、meeting/session 索引和 session 分片；真实数据库导出待验收 |
| 前端 | 已实现基础版本 | 静态 SPA 可读取 Catalunya sample manifest，支持 session 切换、车手/圈数筛选、TR 搜索和圈速图 |
| 测试/CI | 部分完成 | 已有 Python 契约测试、Go 单元/编译测试、前端 build/test；无 CI |

### 已完成修复

- P0-01：已补 `encoding/json`，编译错误解除。
- P0-02：已补齐 Go dependencies 和 `go.sum`。由于网络无法下载 Go 1.25/1.23 工具链，当前 go directive 收束为 `1.22.2`。
- P0-03：已修 API ingest/translate 异步触发 context，后台任务不再捕获 `c.Request.Context()`。
- P1-01：已修 DeepSeek retry body，每次重试都会重建 request body。
- P1-02：已修 API 参数类型转换，path/query 参数进入 SQL 前先转整数，非法值返回 400。
- P1-04：已新增 `positions`、`race_control` 幂等唯一索引，并让 race control 写入使用 `ON CONFLICT DO NOTHING`。
- P2-02：已修 audio proxy 的 `Content-Type` 兜底逻辑。
- P2-03：migration runner 已改为按序执行所有 `*.up.sql` 文件；版本表和回滚执行入口仍未实现。
- P2-05：已定义基础静态 JSON manifest/session 分片合同，并实现 sample 前端读取。
- P2-06：已新增 Python Catalunya 采集器，支持 OpenF1 endpoint 重试、可降级 endpoint、按车手 fallback 拉取 laps/stints、TR 音频归一化和非正圈速过滤。

### 当前技术决策与临时适配

- 为在当前 Go 1.22 环境完成编译测试，数据库驱动从 `pgx/v5/pgxpool` 退回到本地可用的 `github.com/jackc/pgx v3.6.2+incompatible`，并通过项目内 `database.Pool` 封装隔离。
- 因外部网络无法下载 `github.com/gabriel-vasile/mimetype`，已加入 `third_party/mimetype` 的最小兼容实现，并通过 `replace` 指向本地模块。该实现只覆盖 `go-playground/validator` 当前编译所需的 `DetectReader` / `String()` 调用面。
- 当前测试命令使用 `GOTOOLCHAIN=local GOSUMDB=off`，原因是 `sum.golang.org` 与 Go toolchain 下载在当前网络环境超时。正式 CI 应恢复默认校验或配置可信代理。

### 仍未解决的功能风险

- P1-03：Team Radio 翻译链路没有英文转录来源。没有 `transcript_en` 时，worker 会把 `[Audio only - no transcript available]` 发给模型，产出没有业务价值。
- 真实数据库下的 migration、ingest、translator、exporter 全链路仍未验收，原因是当前 shell 缺少 `docker` 或本地 PostgreSQL 服务。
- FastF1 依赖已写入 `requirements.txt`，但本轮临时安装到 `/tmp/f1tr-pydeps` 时 PyPI/files.pythonhosted.org 多次超时，安装进程随后卡在本地 I/O，已终止；FastF1 真实 session 拉取尚未验收。
- OpenF1 endpoint 存在波动：同一 session 的 `team_radio`、`laps`、`pit`、`race_control` 在不同尝试中会超时。采集器已支持重试和按车手 fallback，但 pit/race_control 本轮 sample 仍为空。

### P2 工程与安全风险

- P2-01：`handleAudioProxy` 直接 `http.Get(audioURL)`，存在 SSRF 风险。如果以后恢复公网音频代理，必须做 URL 白名单、协议限制和内网地址过滤；v1 暂不启用公网音频代理。
- P2-02：`handleAudioProxy` 的 Content-Type 兜底逻辑已修复，但音频代理本身不进入 v1 公网主线。
- P2-03：migration runner 已不再硬编码文件列表，但仍无版本表、无幂等校验报告、无回滚执行入口。
- P2-04：已有基础 README 导航和 Go 单元测试，但仍无 CI、无 Makefile、无 PostgreSQL 集成测试。
- P2-05：已有基础静态 JSON schema、分片策略和 manifest；仍需真实大 session 数据体积测试、缓存策略和更强前端索引。

## 5. 数据模型与计算

### 事实表

- `meetings`：赛事周末。
- `sessions`：单个赛段，关联 `meetings`。
- `drivers`：session 内车手与车队信息，`UNIQUE (session_key, driver_number)`。
- `laps`：圈速与三段成绩，`UNIQUE (session_key, driver_number, lap_number)`。
- `stints`：轮胎 stint，`UNIQUE (session_key, driver_number, stint_number)`。
- `team_radio`：TR 元数据、录音 URL、转录与翻译结果，`UNIQUE (session_key, driver_number, date)`。
- `pit_stops`：进站数据，`UNIQUE (session_key, driver_number, lap_number)`。
- `positions`：位置变化，通过 `idx_positions_unique_sample` 约束 `(session_key, driver_number, date)`。
- `race_control`：赛会控制消息，通过 `idx_race_control_unique_message` 对 session、时间、消息和可选车手/圈数做幂等约束。

### 分析视图

- `v_lap_deltas`：用 `LAG()` 计算与上一圈的差值，用 `FIRST_VALUE()` 计算相对 stint 起始圈的差值。
- `v_tire_degradation`：用 `REGR_SLOPE()`、`REGR_INTERCEPT()`、`REGR_R2()` 计算 stint 级衰退指标。
- `v_radio_with_context`：把 Team Radio 关联到车手、车队，并用最近的 `laps.date_start` 估算圈数。

### 计算边界

- 当前衰退率只排除 `lap_duration IS NULL` 和 `is_pit_out_lap = FALSE`，未排除安全车、黄旗、进站前后慢圈、交通、雨胎场景。
- 当前 Team Radio 圈数关联是近似值，不是官方 lap number。
- 当前 positions API 使用采样查询，不是完整时间序列。
- 当前圈速变化、衰退率和 TR 语义分类服务于复盘和内容理解，不应用作严肃专业判断。

## 6. 静态数据合同

v1 面向公网的主要合同不再是 HTTP API，而是 GitHub Pages 上的静态 JSON 文件。前端只读取这些文件，不直接连接 PostgreSQL，也不调用在线后端。

建议导出目录：

```text
public/data/
  manifest.json
  seasons/{year}/meetings.json
  meetings/{meeting_key}/sessions.json
  sessions/{session_key}/summary.json
  sessions/{session_key}/drivers.json
  sessions/{session_key}/laps.json
  sessions/{session_key}/lap-deltas.json
  sessions/{session_key}/stints.json
  sessions/{session_key}/degradation.json
  sessions/{session_key}/radio.json
  sessions/{session_key}/positions.json
  sessions/{session_key}/pit-stops.json
  sessions/{session_key}/race-control.json
```

`manifest.json` 至少包含：

- 数据版本号与生成时间。
- 可用年份、分站、session 列表。
- 每个 session 的 JSON 文件路径、记录数、可用数据类型。
- 前端默认展示入口，例如最新 race session。

前端动态查询边界：

- 年份、分站、session 切换：读取 manifest 和对应 JSON。
- 车手筛选：在浏览器内按 `driver_number` / `name_acronym` 过滤。
- 圈数范围筛选：在浏览器内按 `lap_number` 过滤。
- TR 搜索：在浏览器内搜索 `transcript_en`、`transcript_zh`、`intent`、`sentiment`。
- 图表对比：在浏览器内基于 laps、lap-deltas、stints、degradation 组合展示。
- 数据规模较大时按 session 分片，避免一次加载全年数据。

旧版 Gin API 合同只保留为本地调试参考，不作为公网部署目标。

## 7. 运行路径

### 依赖

`.env.example` 定义了 PostgreSQL、DeepSeek、API 和 OpenF1 配置。`DEEPSEEK_API_KEY` 只在翻译路径必需。

当前基础验证命令：

```bash
docker compose up -d postgres
cd backend
go mod tidy
go test ./...
```

从 2026 Barcelona-Catalunya Race 生成静态 JSON：

```bash
python3 -m pip install -r requirements.txt
python3 tools/export_catalunya_static.py --out frontend/public/data
cd frontend
npm run build
```

如果 FastF1 暂不可用，可先用 OpenF1 降级路径验证 TR 和部分比赛数据：

```bash
python3 tools/export_catalunya_static.py --openf1-only --out frontend/public/data
```

只抓取 meetings/sessions：

```bash
cd backend
go run ./cmd/ingester -year=2026 -meetings-only
```

抓取指定 session：

```bash
cd backend
go run ./cmd/ingester -session-key=<session_key>
```

执行翻译：

```bash
cd backend
go run ./cmd/translator -batch=10
```

导出静态数据：

```bash
cd backend
go run ./cmd/exporter -out ../public/data
# 如需导出前先执行迁移：
go run ./cmd/exporter -migrate -out ../public/data
```

前端构建和本地预览：

```bash
cd frontend
npm run build
npm test
npm run dev
# 将构建产物和 public/data 发布到 GitHub Pages
```

本机状态：当前 shell 已安装 Go 1.22.2，Go 编译与单元测试已通过；Python 3.12 可用，Python 采集器单元测试通过；Node/npm 可用，Catalunya sample 数据构建、构建合同检查和本地浏览器 smoke test 已通过；当前 shell 仍没有 `docker`，PostgreSQL 端到端命令和真实数据库 exporter 导出尚未验收；FastF1 依赖安装未完成。

## 8. 测试策略

完整测试策略见 `docs/test-strategy.md`。

当前仓库已补充 Python 静态契约测试，用于验证文档、静态数据合同、migration 和已知 blocker 的一致性。它不依赖 Go 工具链：

```bash
python3 -m unittest discover -s tests
```

Go 测试已补齐基础用例，并作为当前第一条验收线：

- `go test ./...` 已编译通过。
- `ParseTime` 已覆盖 RFC3339、RFC3339Nano、无时区格式、日期、空字符串、非法时间。
- DeepSeek retry 已测试每次重试 body 非空。
- API helper 已测试 path/query 参数转换与非法值 400。
- `jsonOrNull` 已测试空、合法、非法 JSON。
- Exporter 已测试 manifest、默认 session、session 文件清单和 migration 文件发现顺序。
- Python Catalunya 采集器已测试默认目标、lap delta、degradation、TR audio 归一化和静态文件写出。
- 前端已通过 `npm run build`、`npm test` 和 Playwright smoke test，覆盖 Catalunya manifest 加载、40 条 TR、355 条有效圈速图表、canvas 非空和控制台 0 warning/error。
- 尚未完成：FastF1 真实 session 拉取、真实 PostgreSQL migration 集成测试、真实数据库 exporter 导出和真实大 session 前端性能测试。

## 9. 后续开发路径

### P0：能编译，能启动（已完成编译测试）

1. 补 `encoding/json` import。
2. 补齐 `go.mod` dependencies，并生成 `go.sum`。
3. 修 API 异步触发 context：后台任务使用 `context.WithTimeout(context.Background(), ...)` 或任务队列 context，不捕获 request context。
4. 跑 `go test ./...`，至少保证编译通过。

### P1：能跑通离线生产链路

1. 用 Docker 或本地 PostgreSQL 跑 migration。
2. 通过 CLI ingest 抓取一个真实 session。
3. 通过 CLI translator 处理已有 `transcript_en`。
4. 新增 `cmd/exporter`，导出 manifest 和 session JSON。（代码已完成，真实数据库导出待验收）
5. Python Catalunya 采集器输出静态 JSON。（OpenF1 降级路径已验收，FastF1 主路径待依赖安装后验收）
6. 前端读取静态 JSON，完成 session 切换、车手筛选、圈数筛选、TR 搜索、图表展示。（Catalunya sample 已验收）
7. 明确 `transcript_en` 来源：外部导入优先，STT 延后。

### P2：数据正确性与安全

1. 为 exporter 增加更严格 JSON schema 测试和真实数据库文件完整性测试。
2. 为 migration 引入版本表或迁移工具。
3. 为前端增加真实大 session 性能测试和 GitHub Pages 发布脚本。
4. 增加 CI、Makefile 和 PostgreSQL 集成测试。

### P3：产品化扩展

1. 前端 Dashboard：圈速折线、轮胎颜色、TR 标记点、TR 原文/翻译阅读面板。
2. 音频播放：如果后续确实需要，再处理音频 CORS、防盗链或代理。
3. 更完整的数据清洗：安全车、黄旗、慢圈、雨胎、进站前后异常圈。

## 10. 决策记录

- 公网访问不部署常驻 API，主线改为 Go CLI 导出静态 JSON + 前端 SPA。
- 数据计算继续下沉 PostgreSQL，不把 delta/degradation 移到 API 层。
- v1 把 CLI ingest / translator / exporter 作为唯一数据生产路径；网页端不触发在线采集或在线翻译。
- 当前优先从 Python FastF1 + OpenF1 直接生成静态 JSON，绕过数据库以先交付前端可读数据；Go/PostgreSQL 路径保留用于后续结构化生产。
- 默认实现从 OpenF1 `meeting_key=1287`、`session_key=11307` 的 2026 Barcelona-Catalunya Race 开始。
- Team Radio 翻译只接受已有英文文本；音频转文字不进入 v1。
- 前端进入 v1 主线，但只读静态 JSON；Cloudflare Workers 和音频代理延后。
- 产品定位明确为赛后复盘和辅助理解平台，不按严肃专业分析工具设计。
- 当前阶段为完成本机验收，数据库依赖暂退到 pgx v3 并通过项目内 `database.Pool` 隔离，后续可在 Go 1.23+ 环境恢复 pgx v5。
