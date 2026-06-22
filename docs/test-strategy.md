# F1 TR 测试策略

本文档定义 F1 TR 的测试边界、优先级和验收标准。当前仓库已有 Go 1.22.2 工具链，P0 编译阻断已解除；测试建设分两层推进：静态契约测试防止文档和代码事实脱节，Go 单元测试覆盖关键行为，PostgreSQL 集成测试留到 Docker/数据库环境可用后执行。

## 1. 当前可运行测试

当前可运行 Python 标准库 `unittest` 静态契约测试：

```bash
python3 -m unittest discover -s tests
```

它验证：

- `docs/technical-overview.md` 中的 API 合同与 `backend/internal/api/router.go` 一致。
- 文档列出的表和视图与 migrations 一致。
- 已知 P0/P1/P2 blocker 在文档中有明确编号，避免后续交接把严重问题写轻。
- README 指向统一技术文档。

这类测试不证明 Go 服务可运行，只证明工程事实被正确记录。

当前也可运行 Go 测试：

```bash
cd backend
GOTOOLCHAIN=local GOSUMDB=off go test ./...
```

它验证后端可编译，并覆盖 API 参数转换、JSON helper、OpenF1 时间解析、DeepSeek 重试 body。

## 2. P0 Go 测试基线

P0 修复完成后，第一条验收命令是：

```bash
cd backend
go test ./...
```

通过标准：

- 所有 package 可编译。
- `go.mod` 和 `go.sum` 完整。
- 没有未使用 import。
- 没有缺失依赖。

## 3. 单元测试清单

### Config（待补）

- `Load` 在环境变量缺失时使用默认值。
- `Load` 在环境变量存在时优先使用环境值。
- `GetBatchSize` 对空值、非法值、负数、0 使用默认值 `10`。

### Ingester（部分完成）

- `ParseTime` 覆盖 RFC3339、RFC3339Nano、无时区格式、日期格式、空字符串、非法字符串。
- OpenF1 client 请求参数正确拼接。
- OpenF1 client 429/5xx/网络错误会重试。
- OpenF1 client 非 200 响应返回包含状态码的错误。
- pipeline 对 meetings、sessions、drivers、laps、stints、team_radio、pit_stops 的 upsert SQL 行为可重复执行。

### Translator（部分完成）

- DeepSeek request body 包含 system prompt、术语表、driver acronym、`response_format=json_object`。
- DeepSeek 200 响应能解析 `transcript_zh`、`intent`、`sentiment`、`key_entities`。
- DeepSeek 429/5xx 重试时每次请求 body 都非空。
- DeepSeek 返回非 JSON content 时返回清晰错误。
- worker 对空 `transcript_en` 的行为被明确测试；后续如果引入 STT，这个测试必须更新。

### API（部分完成）

- `/health` 返回 200。
- 所有 path 参数先转换为整数；非法参数返回 400。
- `driver`、`year` query 参数先转换为整数；非法参数返回 400。
- ingest/translate 触发接口在返回 202 后，后台任务仍使用独立 context。
- translator 未配置 API key 时，`POST /api/v1/translate/batch` 返回 503。
- `jsonOrNull` 对空 JSON、合法 JSON、非法 JSON 行为稳定。

### Audio Proxy

- 缺少 `url` 返回 400。
- 非 `https` 或非白名单域名返回 400。
- 内网地址、loopback、link-local、metadata IP 被拒绝。
- 上游无 Content-Type 时响应兜底为 `audio/mpeg`。
- 上游非 200 响应透传或转换策略明确。

## 4. 集成测试清单

集成测试需要 PostgreSQL，可用 Docker 或本地数据库运行。

基础流程：

1. 启动干净 PostgreSQL。
2. 执行 migrations。
3. 验证所有表和视图存在。
4. 插入最小 meetings/sessions/drivers/laps/stints/team_radio 数据。
5. 查询 `v_lap_deltas`、`v_tire_degradation`、`v_radio_with_context`。
6. 启动 API server，调用查询接口并验证 JSON shape。

OpenF1 流程：

1. 使用 mock OpenF1 server，不直接依赖外网。
2. 模拟 meetings、sessions、session 详情响应。
3. 运行 fetcher。
4. 验证数据库写入数量和幂等性。

Translator 流程：

1. 使用 mock DeepSeek server。
2. 插入含 `transcript_en` 的 pending radio。
3. 运行 worker。
4. 验证状态从 `pending` 到 `processing` 到 `done`。
5. 验证错误路径会写入 `error`。

## 5. 验收顺序

每个阶段只在前一阶段通过后继续：

1. 静态契约测试通过。
2. `go test ./...` 编译通过。
3. 单元测试覆盖 P0/P1 修复点。
4. PostgreSQL migration 集成测试通过。
5. CLI ingest 跑通一个真实 session。
6. API 查询真实 session 数据成功。
7. 翻译链路用 mock DeepSeek 跑通。
8. 有真实 `transcript_en` 后再验证 DeepSeek 真实调用。

## 6. 不纳入当前测试范围

- 前端图表渲染测试。
- Cloudflare Workers 缓存测试。
- wavesurfer.js 音频播放测试。
- STT 准确率测试。
- 复杂比赛策略模型测试。
