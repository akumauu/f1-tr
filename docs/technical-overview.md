# F1 TR 开发技术统一文档

本文档用于统一 F1 TR 后续开发的技术边界、当前状态、修复优先级和验收路径。它不是产品宣传稿，而是开发交接与排期基准。

状态基于 2026-06-22 的仓库实现与测试结果：当前目录包含 Go 后端、PostgreSQL migration、`docker-compose.yml`、`.env.example` 和文档；当前目录不是 git 仓库。Go 工具链已安装并通过 `go test ./...`，但当前 shell 仍缺少 `docker`，所以 PostgreSQL migration、真实 ingest、API 查询和端到端链路尚未完成验收。

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

v1 先收束为 Go + PostgreSQL 的后端数据分析系统，不再同时推进 Rust API、Cloudflare Workers、前端 Dashboard 和语音识别。

v1 必须交付：

- OpenF1 数据采集：meetings、sessions、drivers、laps、stints、team_radio、pit_stops、positions、race_control。
- PostgreSQL 持久化：原始事实表、基础唯一约束、可重复执行迁移。
- Team Radio 复盘数据：全场 TR 原文、中文翻译、意图/情绪/实体等基础标签。
- 圈速复盘数据：全场圈速、分段成绩、轮胎 stint、进站与位置变化。
- 数据库计算：圈速 delta、stint 内圈速变化、轮胎衰退率、Team Radio 与近似圈数关联。
- Go API：只读查询接口，以及可靠的 ingest / translate 触发接口。
- Team Radio 翻译链路：只处理已有英文转录 `transcript_en`，不在 v1 内实现音频转文字。
- 测试基线：编译测试、关键单元测试、静态契约测试、最小集成测试说明。

### v1 不做

- 不引入 Rust 主 API。当前代码已经选择 Gin，继续用 Go 收束路径。
- 不实现 Cloudflare Workers 缓存与音频边缘代理，只保留为部署优化方向。
- 不实现前端 Dashboard、ECharts、wavesurfer.js。
- 不实现 STT / ASR 音频转文字；`team_radio.recording_url` 只是录音 URL。
- 不做实时比赛流处理；当前按赛后批处理设计。
- 不做复杂策略模型、异常圈深度清洗或机器学习分析。
- 不输出官方级、工程级或高精度专业结论；所有分析结果只作为赛后复盘线索。

### 后续阶段

- v1.1：补后台任务状态、任务重试、数据重复约束、API 参数校验和安全限制。
- v1.2：接入外部 `transcript_en` 导入或 STT 管道。
- v2：前端 Dashboard、边缘缓存、音频代理、更多可视化和部署优化。

## 3. 当前架构

### 模块职责

- `backend/cmd/api`：启动 Gin API、连接数据库、执行迁移、注册路由。
- `backend/cmd/ingester`：CLI 数据采集入口，使用 `context.Background()`，当前比 API 触发路径更可靠。
- `backend/cmd/translator`：CLI 翻译入口，处理 `team_radio.translation_status = 'pending'` 的记录。
- `backend/internal/api`：HTTP router 与 handlers。
- `backend/internal/ingester`：OpenF1 HTTP client、并发 fetcher、PostgreSQL 写入 pipeline。
- `backend/internal/translator`：DeepSeek client、术语表、批处理 worker。
- `backend/internal/database`：PostgreSQL 连接池包装层与 migration runner。当前为适配本机 Go 1.22 验证环境，使用 `github.com/jackc/pgx v3` 的 `ConnPool` 封装出项目内 `database.Pool`。
- `backend/migrations`：事实表与分析视图。

### 数据流

OpenF1 数据流：

1. `OpenF1Client` 请求 OpenF1 JSON API。
2. `Fetcher` 按 year 或 session key 组织抓取，session 详情并发抓取多个数据域。
3. `Pipeline` 做基础字段映射、时间解析、去重或 upsert。
4. PostgreSQL 保存事实表。
5. SQL view 计算派生指标。
6. API 查询事实表或视图并返回 JSON。

Team Radio 翻译流：

1. OpenF1 `team_radio` 只提供录音 URL 与基础元数据。
2. `team_radio.transcript_en` 需要由外部流程写入；当前项目没有 STT。
3. translator worker 读取 `pending` 记录。
4. DeepSeek client 生成中文翻译、意图、情绪和结构化实体。
5. worker 写回 `transcript_zh`、`intent`、`sentiment`、`key_entities` 和 `translation_status`。

## 4. 当前进度审计

总体判断：P0 编译阻断已解除，项目已从“能读不能跑”推进到“后端可编译、关键单元测试可跑”的早期后端。下一优先级是 PostgreSQL 环境下的 migration、ingest、API 查询端到端验收。

| 维度 | 状态 | 结论 |
| --- | --- | --- |
| 架构与目录 | 基本完整 | Go 后端模块边界清楚 |
| 编译 | 已通过 | `GOTOOLCHAIN=local GOSUMDB=off go test ./...` 通过 |
| API 触发链路 | 已修复，待集成验证 | goroutine 已改用独立后台 context |
| CLI 链路 | 未端到端验证 | 需要 PostgreSQL/Docker 环境 |
| 数据库迁移 | 部分完成 | 表和视图存在，无版本表 |
| 翻译功能 | 部分完成 | 无 STT，空转风险高 |
| 测试/CI | 部分完成 | 已有 Python 契约测试与 Go 单元/编译测试；无 CI |
| 前端 | 不存在 | v1 不纳入 |

### 已完成修复

- P0-01：已补 `encoding/json`，编译错误解除。
- P0-02：已补齐 Go dependencies 和 `go.sum`。由于网络无法下载 Go 1.25/1.23 工具链，当前 go directive 收束为 `1.22.2`。
- P0-03：已修 API ingest/translate 异步触发 context，后台任务不再捕获 `c.Request.Context()`。
- P1-01：已修 DeepSeek retry body，每次重试都会重建 request body。
- P1-02：已修 API 参数类型转换，path/query 参数进入 SQL 前先转整数，非法值返回 400。
- P2-02：已修 audio proxy 的 `Content-Type` 兜底逻辑。

### 当前技术决策与临时适配

- 为在当前 Go 1.22 环境完成编译测试，数据库驱动从 `pgx/v5/pgxpool` 退回到本地可用的 `github.com/jackc/pgx v3.6.2+incompatible`，并通过项目内 `database.Pool` 封装隔离。
- 因外部网络无法下载 `github.com/gabriel-vasile/mimetype`，已加入 `third_party/mimetype` 的最小兼容实现，并通过 `replace` 指向本地模块。该实现只覆盖 `go-playground/validator` 当前编译所需的 `DetectReader` / `String()` 调用面。
- 当前测试命令使用 `GOTOOLCHAIN=local GOSUMDB=off`，原因是 `sum.golang.org` 与 Go toolchain 下载在当前网络环境超时。正式 CI 应恢复默认校验或配置可信代理。

### 仍未解决的功能风险

- P1-03：Team Radio 翻译链路没有英文转录来源。没有 `transcript_en` 时，worker 会把 `[Audio only - no transcript available]` 发给模型，产出没有业务价值。
- P1-04：`positions` 和 `race_control` 缺少足够唯一约束，重复抓取可能产生重复数据。

### P2 工程与安全风险

- P2-01：`handleAudioProxy` 直接 `http.Get(audioURL)`，存在 SSRF 风险，公开部署前必须做 URL 白名单、协议限制和内网地址过滤。
- P2-02：`handleAudioProxy` 的 Content-Type 兜底判断读取的是请求头，不是响应头，兜底逻辑不可靠。
- P2-03：migration runner 硬编码两个 `.up.sql` 文件，无版本表、无幂等校验报告、无回滚执行入口。
- P2-04：已有基础 README 导航和 Go 单元测试，但仍无 CI、无 Makefile、无 PostgreSQL 集成测试。

## 5. 数据模型与计算

### 事实表

- `meetings`：赛事周末。
- `sessions`：单个赛段，关联 `meetings`。
- `drivers`：session 内车手与车队信息，`UNIQUE (session_key, driver_number)`。
- `laps`：圈速与三段成绩，`UNIQUE (session_key, driver_number, lap_number)`。
- `stints`：轮胎 stint，`UNIQUE (session_key, driver_number, stint_number)`。
- `team_radio`：TR 元数据、录音 URL、转录与翻译结果，`UNIQUE (session_key, driver_number, date)`。
- `pit_stops`：进站数据，`UNIQUE (session_key, driver_number, lap_number)`。
- `positions`：位置变化，当前只有索引，无唯一约束。
- `race_control`：赛会控制消息，当前无唯一约束。

### 分析视图

- `v_lap_deltas`：用 `LAG()` 计算与上一圈的差值，用 `FIRST_VALUE()` 计算相对 stint 起始圈的差值。
- `v_tire_degradation`：用 `REGR_SLOPE()`、`REGR_INTERCEPT()`、`REGR_R2()` 计算 stint 级衰退指标。
- `v_radio_with_context`：把 Team Radio 关联到车手、车队，并用最近的 `laps.date_start` 估算圈数。

### 计算边界

- 当前衰退率只排除 `lap_duration IS NULL` 和 `is_pit_out_lap = FALSE`，未排除安全车、黄旗、进站前后慢圈、交通、雨胎场景。
- 当前 Team Radio 圈数关联是近似值，不是官方 lap number。
- 当前 positions API 使用采样查询，不是完整时间序列。
- 当前圈速变化、衰退率和 TR 语义分类服务于复盘和内容理解，不应用作严肃专业判断。

## 6. API 合同

基础地址默认 `http://localhost:8080`，端口来自 `API_PORT`。

| Method | Path | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/v1/meetings?year=2026` | 查询指定年份赛事会议 |
| GET | `/api/v1/meetings/:key/sessions` | 查询某个 meeting 的 sessions |
| GET | `/api/v1/sessions/:key/drivers` | 查询 session 车手 |
| GET | `/api/v1/sessions/:key/laps` | 查询 session 圈速，支持 `driver` query |
| GET | `/api/v1/sessions/:key/laps/deltas` | 查询圈速 delta，支持 `driver` query |
| GET | `/api/v1/sessions/:key/stints` | 查询轮胎 stint |
| GET | `/api/v1/sessions/:key/degradation` | 查询轮胎衰退视图 |
| GET | `/api/v1/sessions/:key/radio` | 查询 Team Radio 上下文，支持 `driver` query |
| GET | `/api/v1/sessions/:key/positions` | 查询位置变化采样结果 |
| GET | `/api/v1/sessions/:key/pit-stops` | 查询进站数据 |
| GET | `/api/v1/sessions/:key/race-control` | 查询赛会控制消息 |
| GET | `/api/v1/audio-proxy?url=...` | 代理音频 URL；当前有 SSRF 风险，公开部署前不可用 |
| POST | `/api/v1/ingest/meetings/:year` | 异步触发 meetings/sessions 抓取；P0-03 已修复，仍需 PostgreSQL 集成验证 |
| POST | `/api/v1/ingest/session/:key` | 异步触发 session 详情抓取；P0-03 已修复，仍需 PostgreSQL 集成验证 |
| POST | `/api/v1/translate/batch` | 异步触发翻译批处理；P0-03 已修复，仍需 PostgreSQL/DeepSeek mock 集成验证 |

## 7. 运行路径

### 依赖

`.env.example` 定义了 PostgreSQL、DeepSeek、API 和 OpenF1 配置。`DEEPSEEK_API_KEY` 只在翻译路径必需。

当前设计命令：

```bash
docker compose up -d postgres
cd backend
go mod tidy
go test ./...
go run ./cmd/api
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

本机状态：当前 shell 已安装 Go 1.22.2，Go 编译与单元测试已通过；当前 shell 仍没有 `docker`，PostgreSQL 端到端命令尚未验收。

## 8. 测试策略

完整测试策略见 `docs/test-strategy.md`。

当前仓库已补充 Python 静态契约测试，用于验证文档、路由、migration 和已知 blocker 的一致性。它不依赖 Go 工具链：

```bash
python3 -m unittest discover -s tests
```

Go 测试已补齐基础用例，并作为当前第一条验收线：

- `go test ./...` 已编译通过。
- `ParseTime` 已覆盖 RFC3339、RFC3339Nano、无时区格式、日期、空字符串、非法时间。
- DeepSeek retry 已测试每次重试 body 非空。
- API helper 已测试 path/query 参数转换与非法值 400。
- `jsonOrNull` 已测试空、合法、非法 JSON。
- 尚未完成：ingest/translate 后台任务集成测试、audio proxy SSRF 白名单测试、PostgreSQL migration 集成测试。

## 9. 后续开发路径

### P0：能编译，能启动（已完成编译测试）

1. 补 `encoding/json` import。
2. 补齐 `go.mod` dependencies，并生成 `go.sum`。
3. 修 API 异步触发 context：后台任务使用 `context.WithTimeout(context.Background(), ...)` 或任务队列 context，不捕获 request context。
4. 跑 `go test ./...`，至少保证编译通过。

### P1：能跑通核心链路

1. 用 Docker 或本地 PostgreSQL 跑 migration。
2. 通过 CLI ingest 抓取一个真实 session。
3. 通过 API 查询 meetings、sessions、laps、stints、degradation、radio。
4. 为 ingest/translate 后台任务补集成测试。
5. 明确 `transcript_en` 来源：外部导入优先，STT 延后。

### P2：数据正确性与安全

1. 为 `positions`、`race_control` 增加唯一约束或幂等写入策略。
2. 为 audio proxy 加白名单、协议限制、DNS/IP 内网过滤、响应大小限制。
3. 为 migration 引入版本表或迁移工具。
4. 增加 CI、Makefile、集成测试和 API 示例。

### P3：产品化扩展

1. 前端 Dashboard：圈速折线、轮胎颜色、TR 标记点、音频播放。
2. Cloudflare Workers：只在 API 稳定后做 JSON 缓存和音频边缘代理。
3. 更完整的数据清洗：安全车、黄旗、慢圈、雨胎、进站前后异常圈。

## 10. 决策记录

- 主 API 继续使用 Go Gin，不切 Rust。
- 数据计算继续下沉 PostgreSQL，不把 delta/degradation 移到 API 层。
- v1 把 CLI ingest 作为优先可验证路径；API ingest 触发已修复请求 context 问题，但开放前仍需 PostgreSQL 集成验证。
- Team Radio 翻译只接受已有英文文本；音频转文字不进入 v1。
- Cloudflare Workers 与前端 Dashboard 延后，避免在后端未跑通前扩大战线。
- 产品定位明确为赛后复盘和辅助理解平台，不按严肃专业分析工具设计。
- 当前阶段为完成本机验收，数据库依赖暂退到 pgx v3 并通过项目内 `database.Pool` 隔离，后续可在 Go 1.23+ 环境恢复 pgx v5。
