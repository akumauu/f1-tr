# F1 TR 测试策略

本文档定义 F1 TR 的测试边界、优先级和验收标准。当前架构见 [`technical-overview.md`](technical-overview.md)，最近一次已验证结果见 [`implementation-results.md`](implementation-results.md)，尚未完成的测试工作见 [`progress.md`](progress.md)。测试建设分层推进：静态契约测试防止文档和代码事实脱节，Python 测试覆盖静态数据、存储清理和 v4 冻结，Go 单元测试覆盖既有后端关键行为，前端构建测试验证静态合同，PostgreSQL 真实集成测试仍需补齐。

## 1. 当前可运行测试

当前可运行 Python 标准库 `unittest` 静态契约测试：

```bash
python3 -m unittest discover -s tests
```

它验证：

- `docs/README.md` 是 Agent 文档入口，五份主文档存在且职责明确。
- `docs/technical-overview.md` 中的静态数据合同包含 manifest、session 分片和前端动态查询边界。
- `docs/database.md` 列出的核心表和分析视图与 migrations 一致。
- `docs/progress.md` 明确跟踪已知阻塞和风险。
- `tools/export_catalunya_static.py`、`cmd/exporter`、`internal/exporter` 和 `frontend` 主线文件存在，避免文档写了但实现缺位。
- Catalunya sample manifest 指向 OpenF1 `meeting_key=1287`、`session_key=11307`。
- README 指向文档地图与五份主文档。

这类测试不证明 Go 服务可运行，只证明工程事实被正确记录。

当前也可运行 Go 测试：

```bash
cd backend
GOTOOLCHAIN=local GOSUMDB=off go test ./...
```

它验证后端可编译，并覆盖 API 参数转换、JSON helper、OpenF1 时间解析、DeepSeek 重试 body、migration 文件发现顺序和 exporter manifest/session 文件合同。API 相关测试当前用于守住遗留本地调试模块质量，不代表公网部署主线。

当前 Python 测试还覆盖 `tools/export_catalunya_static.py`：

- 默认 Catalunya 目标是 `meeting_key=1287`、`session_key=11307`。
- lap delta 和 degradation 计算会跳过无效圈速。
- OpenF1 TR 音频记录会被归一化为 `translation_status=audio_only`。
- 静态 manifest 和 session 分片文件可写出。

当前也可运行前端静态构建测试：

```bash
cd frontend
npm run build
npm test
```

它验证无后端服务时可以构建静态 SPA，能从 Catalunya sample
`data/manifest.json` 找到 summary、drivers、laps、radio 等关键分片，并验证
Race Dossier 工作台的产品/赛季/分站/版本级联、稳定轮次顺序、Stint 双视图、
事件账本分类和页面阅读顺序。

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

### Exporter（基础完成）

- `manifest.json` 包含版本号、生成时间、年份、分站、session、默认入口。（已测）
- 每个 session 分片文件路径可从 manifest 解析并存在。（已测）
- 导出的 `laps.json`、`lap-deltas.json`、`stints.json`、`degradation.json`、`radio.json` 字段稳定。
- JSON 文件可被前端直接读取，不包含数据库内部连接信息或密钥。
- 大 session 按文件分片，避免前端首次加载全年数据。
- 待补：连接真实 PostgreSQL 后的最小数据导出集成测试。

### Python Catalunya 采集器（基础完成）

- 默认目标为 2026 Barcelona-Catalunya Race，OpenF1 `meeting_key=1287`、`session_key=11307`。
- FastF1 是圈速/stint 主路径；OpenF1 是 TR 和事件主路径，并作为 laps/stints 降级来源。
- OpenF1 全量 laps/stints 超时时，会按 driver_number 分片重试。
- 非正 `lap_duration` 不进入 delta、degradation 和前端最佳圈速展示。
- 待补：FastF1 依赖安装成功后的真实 session 拉取测试。

### 前端静态读取（基础完成）

- 数据页面不得用无条件 `<script type="module">` 绕过协议入口器；`file://` 下禁止
  加载应用模块或请求 JSON。真实 Chrome 必须覆盖“无服务显示 HTTP 入口”和“服务
  已就绪自动切换并完成加载”两条路径。（已测）
- 无后端服务时可以加载 manifest。（Catalunya sample 已测）
- 年份、分站、session 切换能按需加载对应 JSON。
- 车手筛选、圈数范围筛选、TR 文本搜索在浏览器内完成。（Catalunya sample 已测）
- 圈速折线、delta、stint、degradation 数据源字段对齐。（Catalunya sample 已测）
- 缺少可选数据文件时有空状态，不阻断页面。
- Race Dossier 默认与探索性遥测档案分流，赛季按新到旧、分站按正式轮次新到旧、
  同场版本按新到旧；v17 是 70 场正式逐圈证据版，v16 仍标作单场
  `summary-only` pilot。（已测）
- 必须先用完整工作台 manifest 建立轮次上下文，再逐级筛选；只剩 v16 时仍应继承
  同场 v15 的 R24。（已测）
- Stint 的主报告四队、全场、单队、单车手范围使用 AND 级联；默认比赛进程视图
  与真实可比窗口视图均有独立排序反例。（已测）
- 可比窗口必须覆盖胎龄差 2.00/2.01 圈、阶段中点差 0.200/0.201、跨配方、
  无效状态和旧版回退；每个窗口满足 max-min 冻结门，合格 Stint 不重复不遗漏，
  基准是窗口内最小代表配速，单行窗口不得显示伪差值。（已测）
- 真实 Chrome 必须在桌面、390px 移动端、横向/纵向滚动及 200% 缩放下确认
  当前窗口基准条仍可见且未裁切。（已测）
- 同配方双曲线只接受至少两个不同车队的真实可比窗口；空斜率、`null` 支持端点、
  单车队、跨配方及无共同胎龄支持必须 fail-close。（已测）
- 曲线采样点必须全部位于所有成员支持区间的交集；共同评价胎龄取参考胎龄中位数
  并夹在交集内，条件配速和衰减极值都在该共同胎龄计算。（已测）
- v17 前端必须从报告声明的 sidecar 懒加载逐圈证据并校验 schema、报告身份和
  SHA；不得在浏览器重拟合，主拟合、稳定性带和确认后曲线的每个点都必须位于
  后端发布支持内。（已测）
- 逐圈图必须同时覆盖散点、冻结主方程、删连续 3 圈块稳定性范围和可选确认后
  描述性曲线；共同胎龄图必须把观测残差 80%、稳定性 80% 与
  `low/base/high` 燃油敏感性分开，禁止命名为 CI 或 PI。（已测）
- 成对可比样本、Kish ESS 和条件分布只能在该对 Stint 的共同胎龄支持内计算；
  `warning` / `not_comparable` 的正式直接秒差必须为 `null`，任何全窗口排名只有
  在全部 pair 都为 `comparable` 时才允许。（已测）
- 车手/车队身份色必须按赛季精确解析 Formula 1 官方 `teamColourCode`；当前发布
  入口合计 41 个 `season|team` 身份必须全部命中，未知赛季、未知队和模糊名称必须
  fail-close，源码不得出现哈希、车号取模、车手手填色或 CSS 身份色回退。（已测）
- 同队车手必须同色，只能用稳定线型/标记区分；同一车手多个 Stint 及输入反序后
  样式不变。Node canvas stub 必须记录实际 `strokeStyle`、`fillStyle`、线型和线宽，
  不能只计数绘图调用；DOM 图例同时保留官方来源和规范车队 key。（已测）
- 主赛况页、Race Dossier 工作台和 Live Review 必须在真实 Chrome 中验证同一
  官方目录；车手筛选、计时表、TR 卡、曲线和图例不得各自维护颜色。（已测）
- 曲线浏览器的真实 Chrome 验收覆盖 MEDIUM/HARD 切换、桌面双栏、390px 单栏及
  画布不越界；v17 还需检查 sidecar 请求、真实 canvas 像素、方程、样本审计及
  非可比行直接秒差为空。截图写入项目 `.runtime-cache/ui-smoke/`。（已测）
- 稳定术语只能来自中央帮助词典；帮助按钮必须覆盖鼠标悬停、键盘聚焦、
  `Escape`、触屏点击、视口夹取和移动端换行，正文不得逐版本重复方法长文。（已测）
- v15 等旧合同缺少代表胎龄字段时，双曲线区必须显示可见的不可用原因，Stint 表
  继续加载；禁止按版本字符串静默隐藏或在前端补造共同胎龄曲线。（已测）
- 工作台 manifest 的每个引用必须存在于发布快照，构建后文件集合与 manifest
  精确一致，并逐份执行 JSON 解析和 `render()`；只渲染默认 v16 不算发布验收。
  当前 145/145 报告与 70/70 v17 sidecar 的构建、散列和逐份渲染已测；Git 发布
  快照仍须在提交时单独检查。
- 缺失 `.json` 必须返回 404，`fetchJSON` 必须验证 `application/json`；切换加载
  失败时恢复上一份成功报告的筛选与 scope，首次失败则隐藏全部报告正文。（已测）
- 事件账本通过 Stint 的车手—车队映射筛选；比赛因素、计时质量和完整账本可切换，
  未映射记录在全场范围内不得丢失。（已测）
- Race Dossier DOM 顺序固定为整场结论、比赛事件、Stint、条件配速、秒数拆分、
  轮胎策略、方法审计；导航锚点必须存在。（已测）
- 待补：真实大 session 数据下的加载性能、可视化密度和 GitHub Pages 路径测试。

#### v17 可比焦点遥测视觉重构（已执行）

- 静态断言：
  - 默认视图只能选择 `comparable` pair，或所有成员两两 `comparable` 的完整
    clique；`warning` / `not_comparable` 不得进入共享快慢轴或生成全序；
  - 必须存在默认 `FOCUS` 与 `FULL` 切换；焦点域外观测必须生成上下边缘方向标记
    及数量，不能静默丢点；
  - 散点半径必须固定，分析权重只改变透明度；稳定性只能绘制上下两条点线且无
    填充；燃油代理必须恰为 `L/B/H` 三个离散点，禁止连线或面积包络；
  - 浏览器不得从逐圈点重新拟合、平滑或外推，只能消费 sidecar 冻结结果。
- 交互断言：
  - 切换 `FOCUS` / `FULL` 后保持当前 pair/clique、图例和 tooltip 身份一致，
    超界标记在全量域恢复为真实点位置；
  - pair/clique 切换必须同步曲线、卡片和 tooltip；tooltip 要显示左右方向、
    共同支持、门控状态及原因，反向键不能颠倒秒差符号；
  - 无可比 pair 时显示明确审计空状态；选择 `warning` / `not_comparable` 仅查看
    门控证据，不得出现共享排名刻度或直接秒差。
- 移动端断言：
  - 390px 视口使用纵向 pair 卡片，不依赖横向宽表；触屏可打开和关闭 tooltip，
    卡片、控制器及图例不得互相遮挡；
  - `FOCUS` / `FULL`、上下边缘标记及数量在单栏中可读且不裁切，切换 pair 后
    页面不残留上一对的身份、线条或 tooltip。

本轮已执行静态合同与真实 Chrome 回归：前端 build/test 通过；默认 comparable
pair、7 个焦点选项、`FOCUS / FULL`、焦点外标记计数、6 个离散燃油情景点、
逐圈 tooltip、1440px 双栏、390px 单栏和移动 pair 卡片均通过。390px 下页面
`scrollWidth` 与视口同宽。截图使用本轮重新生成的
`.runtime-cache/ui-smoke/v17-redesign-{desktop,mobile}.png`，不复用旧画布截图。

### 遗留 API（部分完成，仅本地调试）

- `/health` 返回 200。
- 所有 path 参数先转换为整数；非法参数返回 400。
- `driver`、`year` query 参数先转换为整数；非法参数返回 400。
- ingest/translate 触发接口在返回 202 后，后台任务仍使用独立 context。
- translator 未配置 API key 时，`POST /api/v1/translate/batch` 返回 503。
- `jsonOrNull` 对空 JSON、合法 JSON、非法 JSON 行为稳定。

### Audio Proxy（延后）

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
6. 运行 exporter，验证 manifest 和 session JSON shape。
7. 使用前端或最小静态读取测试加载导出文件。

OpenF1 流程：

1. 使用 mock OpenF1 server，不直接依赖外网。
2. 模拟 meetings、sessions、session 详情响应。
3. 运行 fetcher。
4. 验证数据库写入数量和幂等性。

Python 静态采集流程：

1. 使用 mock OpenF1 client 返回 Catalunya meetings/sessions/drivers/laps/stints/radio。
2. 使用 fake FastF1 bundle 返回 laps/stints。
3. 运行 `build_export_bundle`。
4. 验证 manifest、summary、radio、lap-deltas、degradation 和 session 文件路径。
5. 使用真实 OpenF1 smoke 时，允许 endpoint warning，但必须写出静态合同。

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
6. CLI translator 用 mock DeepSeek 跑通。
7. Python Catalunya 采集器导出静态 JSON 成功。
8. exporter 导出真实 session 静态 JSON 成功。
9. 前端读取导出 JSON 并完成动态筛选、搜索、图表渲染。
10. 有真实 `transcript_en` 后再验证 DeepSeek 真实调用。

前端验收必须先执行 `npm run build`，再执行 `npm test`；`npm test` 读取 `dist`，
不得把未重建的陈旧产物当作源码通过。

## 6. 不纳入当前测试范围

- Cloudflare Workers 缓存测试。
- wavesurfer.js 音频播放测试。
- STT 准确率测试。
- 复杂比赛策略模型测试。
