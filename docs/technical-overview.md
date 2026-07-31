# F1 TR 产品与技术架构总览

> 本文只负责产品边界、系统架构、数据流和对外静态合同。Agent 应先阅读 [`README.md`](README.md) 的文档地图；数据库、算法、实现结果和进度分别由对应主文档维护。

## 1. 产品定位

F1 TR 是用于 F1 赛后复盘的非严肃专业平台。它聚合 Team Radio、圈速、轮胎 stint、位置和比赛事件，帮助用户理解“发生了什么、当时说了什么、表现如何变化”。

所有 delta、degradation、TR 近似圈数、情绪/意图和车辆—车手评分均是辅助分析线索，不是官方判罚、车队工程决策、博彩预测或物理真值。

## 2. 需求边界

### v1 主线

v1 是“离线数据生产 + 静态发布 + 前端浏览器内查询”：

- Python FastF1 + OpenF1 直接生成静态 JSON，是当前优先交付路径。
- Go `ingester` / `translator` / `exporter` + PostgreSQL 是结构化生产路径。
- 前端 SPA 部署到 GitHub Pages，只读取静态 JSON，在浏览器内筛选、搜索、排序和绘图。
- Team Radio 翻译只处理已有 `transcript_en`；音频转文字不属于当前链路。
- 算法分为产品复盘派生指标和独立科研实验，前端不得把条件关联包装成确定性能力排名。

### v1 不做

- 不提供常驻 Gin API 服务器给公网用户访问；遗留 Gin API 只用于本地调试。
- 不实现 Cloudflare Workers、在线缓存或音频边缘代理。
- 不把音频播放作为必需能力。
- 不实现 STT / ASR 音频转文字。
- 不做实时比赛流式决策、官方级策略分析或高精度车辆动力学建模。

### 后续方向

- 完成 PostgreSQL 生产路径的持续集成验收。
- 扩展多分站静态数据、GitHub Pages 发布和大 session 性能。
- 在冻结的 v4 数据上推进动态层级模型与 2026 OOD 验证。
- 用 v5 公开遥测生成可审计的同队双车、单 stint、Episode 与中性 2026 操作模式报告；评分缺失模块保持空值。
- 如产品确有需求，再独立评估 TR 文本导入、STT 和音频代理。

当前是否完成、阻塞和优先级只看 [`progress.md`](progress.md)。

## 3. 系统结构

```text
OpenF1 / FastF1 / MultiViewer / TracingInsights
                    │
          ┌─────────┴─────────┐
          │                   │
 Python 工具链          Go + PostgreSQL
 原始抓取/规范化/导出    入库/翻译/SQL 视图/导出
          │                   │
          └─────────┬─────────┘
                    │
       manifest + session 静态 JSON
                    │
          GitHub Pages 静态 SPA

TracingInsights v4 Parquet → 历史冻结集 → 人车分离/历史 PAC
TracingInsights v5 Parquet → 距离域/TrackPhase/Episode → 同队报告/2026 中性模式
                                                           │
                                         telemetry-workbench 静态 JSON
```

### 模块职责

| 模块 | 职责 | 当前定位 |
| --- | --- | --- |
| `tools/export_catalunya_static.py` | FastF1/OpenF1 → 静态 JSON | 当前最短产品数据路径 |
| `tools/fetch_f1_data.py` | 通用原始采集 | 数据湖入口 |
| `tools/ingest_f1_to_postgres.py` | Python 直接入库并留原始快照 | 数据库生产工具 |
| `backend/cmd/ingester` | Go OpenF1 入库 CLI | 结构化生产路径 |
| `backend/cmd/translator` | 处理已有英文 TR | 不含 STT |
| `backend/cmd/exporter` | PostgreSQL → 静态 JSON | 与 Python 输出同一前端合同 |
| `backend/cmd/api` | Gin HTTP 入口 | 遗留本地调试，不公网部署 |
| `backend/migrations` | 事实表、审计、分析视图和数据目录 | schema 权威来源 |
| `frontend` | 静态 SPA | 公网产品入口 |
| `frontend/src/telemetry-workbench.*` | 赛段差、异常证据、模式、机会与评分边界 | 静态科研解释工作台 |
| `research` | 数据冻结、基线、遥测解释、评分门控和稳健性分析 | 独立科研链路 |

详细代码路由见 [`README.md`](README.md)。

## 4. 数据流

### 4.1 Python 静态流

1. FastF1 提供圈速、成绩和 stint 主路径。
2. OpenF1 提供赛事、车手、TR、进站、位置和赛会控制，并为 laps/stints 降级。
3. Python 导出 manifest、meeting/session 索引和 session 分片。
4. 前端按 manifest 加载数据，在浏览器内完成交互。

### 4.2 Go/PostgreSQL 流

1. ingester 或 Python 入库工具抓取 OpenF1/FastF1。
2. 业务事实进入规范表，原始响应进入 `f1_api_snapshots`。
3. SQL 视图计算圈速差、stint 趋势和 TR 近似上下文。
4. translator 只处理已有英文转录。
5. exporter 生成与 Python 路径一致的静态文件。

表、视图、数据分层和操作命令见 [`database.md`](database.md)。

### 4.3 科研流

1. TracingInsights 原始 JSON 建目录和校验。
2. 既有历史实验转为 expanded v4；需要原始状态/绝对时间的 2026 遥测解释转为 expanded v5，并通过值域审计。
3. 从 v4 建立带哈希的训练/验证冻结集；v5 单站先建全场 Episode，再筛选同队可比圈。
4. 历史链运行人车分离与 PAC；单站链运行距离域相位、逐段双差、三基线异常和中性策略模式。
5. 客观性门控决定 PAC、Event Delivery、OVR 哪些字段可发布；不够成熟的模块写 `null`。
6. 机器可读结果写入 `research/records/*.json`，再导出到 `frontend/public/data/telemetry-workbench/`；Markdown 只作摘要。

模型协议见 [`algorithm-models.md`](algorithm-models.md)。

## 5. 静态数据合同

公网主合同是静态 JSON，不是 HTTP API。建议/当前目录：

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
  telemetry-workbench/manifest.json
  telemetry-workbench/reports/{report_id}.json
```

`manifest.json` 至少包含版本、生成时间、年份、分站、session、默认入口、文件路径、记录数和可用数据类型。

### 前端动态查询边界

- 年份、分站、session：按 manifest 和索引文件切换。
- 车手与圈数：在浏览器内按 `driver_number`、缩写、`lap_number` 过滤。
- TR：搜索英/中转录、意图和情绪；缺文字时显示音频元数据/空状态。
- 图表：组合 laps、lap-deltas、stints、degradation。
- 大数据按 session 和数据域分片，前端不直接连接 PostgreSQL。

### 车手与车队官方颜色合同

车手和车队身份色只允许来自 `frontend/src/official-team-colours.js`。目录按
`赛季 + 规范车队身份` 保存 Formula 1 官方成绩页的 `teamColourCode` 及来源 URL；
当前冻结覆盖 2023–2026。报告 JSON 不重复保存这份稳定目录。

- 同队所有车手必须使用完全相同的官方车队色；车手之间只用线型、标记和文字区分；
- 解析只接受该赛季的精确车队名或已登记别名，不做子串猜测，不把车手和赛季直接
  绑定，避免季中换队时沿用旧队颜色；
- 未知赛季、未知车队或身份冲突必须 fail-close，禁止哈希色、按车号取模色、手工
  近似色、强调色或灰色回退；
- 轮胎配方、旗语、状态和告警色不属于车手/车队身份色，可继续使用各自语义色；
- Live Review 数据只发布 `year`、`team` 和 `team_key`，浏览器仍由同一目录解析
  颜色，避免生成器与主页面出现两套色值。

### 前端解释文案与提示合同

工作台执行“稳定含义只写一次”的展示合同，禁止为了兼容不同报告版本，把同一段
方法定义、代理边界或免责声明复制进每份版本 JSON、每一行表格或每个卡片：

- 版本报告只保存数值、状态、门控结果和该场特有结论；稳定术语统一维护在
  `frontend/src/telemetry-help.js`，比较阈值从前端冻结常量注入，不另抄一份数值；
- 页面正文常驻当前筛选范围、样本量、置顶比较基准、最快/最慢点估计、发布门、
  不可用原因及 `classification_proxy（非 FIA 分类）` 等决策必要信息；
- 计算方法、术语定义、燃油情景、事件重叠、有效权重和进站周期代理等说明，使用
  真实帮助按钮按需显示；鼠标悬停、键盘聚焦和触屏点击都必须可达，`Escape` 可关闭，
  不得只依赖原生 `title`；
- 提示框不得承载唯一的错误、发布状态或禁止声称边界。旧合同缺字段时必须显示简短
  不可用状态与原因，不能静默隐藏，也不能在浏览器端伪造新合同字段；
- 报告加载失败必须返回真实 HTTP/内容类型错误，并回退到上一份成功报告；筛选器、
  scope 与正文不得出现“新选择 + 旧数据”的混合状态。

### Stint 曲线证据加载与展示

Race Dossier v17 的主报告只携带 sidecar 身份和覆盖摘要。工作台选中报告后才读取
`stint_curve_evidence.frontend_path`，并校验 sidecar schema 与 `report_id`；
失败时只降级曲线区域，不得把另一场或上一版本的逐圈数据留在当前页面。

前端不运行回归。主图直接消费后端发布的逐圈散点、分析权重、冻结主回归网格、
删连续三圈稳定性带和可选的确认后描述性形状；副图在后端发布的共同评价胎龄上
并列展示点估计、加权经验中间 80%、删块稳定性 80% 和 low/base/high 燃油敏感性
包络。三种范围的名称与统计角色不得互换。

直接快慢结论只消费 `pairwise_comparisons.status=comparable`。平衡警告和不可比
配对继续显示散点、条件分布与失败原因，但不能进入最快/最慢排名。v16 及更早报告
没有 sidecar 时保留 summary-only 线性投影降级，并明确说明它不是逐圈曲线；禁止
根据旧字段在浏览器补造散点、区间或可比性结论。

旧版 Gin API 合同只保留为本地调试参考，不作为公网部署目标。

## 6. 运行入口

从仓库根目录：

```powershell
# Python 静态导出
python tools\export_catalunya_static.py --out frontend\public\data

# 前端
Push-Location frontend
npm run build
npm test
npm run dev
Pop-Location

# Go 测试
Push-Location backend
$env:GOTOOLCHAIN="local"
$env:GOSUMDB="off"
go test ./...
Pop-Location

# Python 测试
python -m unittest discover -s tests

# 公开遥测解释与静态工作台
python research\run_2026_telemetry_batch.py --quiet
python tools\export_telemetry_workbench.py --report research\records\telemetry_explanation_2026_australia_ferrari_v7.json
```

三个数据页面只支持 HTTP/HTTPS 静态托管，不把 `file://` 当发布协议。页面统一先经
`frontend/src/data-page-entry.js` 判断协议：正常托管时再加载应用模块；直接打开
源码文件时先探测默认本地服务，服务可用则切换到同名 HTTP 页面，否则显示本地入口
和启动命令，不再发起必然失败的 JSON `fetch`。

环境、数据库、采集和科研命令分别见 [`database.md`](database.md)、[`test-strategy.md`](test-strategy.md) 和 [`../research/README.md`](../research/README.md)。

## 7. 不变量与关键决策

- 公网前端不持有数据库连接或密钥。
- 静态数据按 session 分片，不一次加载全年逐圈/遥测数据。
- 原始大数据不进入普通 Git 历史。
- TracingInsights v4 是既有历史冻结实验真相源；v5 是需要 raw 状态与绝对时间的新遥测解释真相源；两者不能静默互换。PostgreSQL 遥测表仍是可丢弃缓存。
- `transcript_en` 的来源必须可追溯；translator 不伪装成 STT。
- 所有研究结论必须标注数据版本、验证年份、不确定性和适用范围。
- 同队差不等于车辆故障，策略指纹不等于真实 SOC/主动空力状态；只有通过匹配平衡、FDR、持续性和多证据门控的观测差异才可进入异常列表。
