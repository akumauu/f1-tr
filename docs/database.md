# F1 TR 数据库、数据源与存储分层

> 权威范围：数据从哪里来、存到哪里、表与视图如何组织、怎样复现和查询。算法含义见 [`algorithm-models.md`](algorithm-models.md)，已验证规模见 [`implementation-results.md`](implementation-results.md)，当前待办见 [`progress.md`](progress.md)。

## 1. 数据架构结论

项目同时存在三类存储，不能把它们视为同一个数据库：

1. **PostgreSQL 业务与审计库**：保存规范化赛事事实、API 原始快照、数据目录和 SQL 分析视图。
2. **本地文件数据湖**：`data/raw/` 保存不可变原始数据，`data/normalized/` 保存规范化 Parquet；大体积逐点遥测以文件数据湖为主。
3. **发布与研究产物**：`frontend/public/data/` 是精简静态发布合同；`research/artifacts/`、`research/cache/` 是可复现研究输入和缓存。

当前关键决策：TracingInsights 逐点遥测的唯一规范真相源是 `data/normalized/tracinginsights/schema=tracinginsights-expanded-v4/`。`telemetry_car_data` 与 `telemetry_location` 仅保留空表结构，作为显式、可丢弃的 OpenF1 临时 SQL 缓存；产品和正式研究不得依赖它们常驻数据。

## 2. 数据源与用途

| 数据源 | 接口/形式 | 主要内容 | 当前用途 | 注意事项 |
| --- | --- | --- | --- | --- |
| OpenF1 | `https://api.openf1.org/v1`，HTTP GET | meetings、sessions、drivers、laps、stints、team_radio、pit、position、race_control、car_data、location | 业务事实、TR、事件、原始快照；FastF1 降级 | endpoint 会超时或有意为空；必须记录状态 |
| FastF1 | Python 包 + F1 Live Timing | 圈速、stint、成绩、天气、赛会控制 | 圈速/stint 优先路径 | 本机代理可能导致 403；需绕过 `livetiming.formula1.com` |
| TracingInsights Archive | 2023–2026 Git 仓库 JSON | 单车手单圈逐点遥测、圈时和元数据 | 科研规范遥测主来源 | 2026 只代表上游当前工作树，不是完整赛季 |
| MultiViewer 本地 API | `127.0.0.1:10101-10110` REST/GraphQL | Live Timing topic、TeamRadio、车载/位置重对象 | 临场采集和复盘素材 | 依赖本地客户端和窗口状态 |
| DeepSeek | 翻译 API | 已有英文 TR 的中文翻译与标签 | `translator` 后处理 | 不负责 STT；无 `transcript_en` 时没有业务价值 |

网络约定：OpenF1 统一使用 GET。FastF1 在当前 Windows 环境运行前，将 `livetiming.formula1.com` 加入当前 PowerShell 会话的 `NO_PROXY`；浏览器可访问不能证明 Python 使用相同网络路径。

## 3. 存储分层与目录

```text
data/
  raw/                         # 原始层：本地、不可变、默认不提交 Git
    f1-api/                    # OpenF1/FastF1 原始抓取
    tracinginsights/{year}/    # 上游仓库工作树
    multiviewer-*/             # 临场快照和 DOM 兜底数据
  normalized/
    tracinginsights/
      schema=tracinginsights-expanded-v4/  # 2023–2025 冻结研究与兼容真相源
      schema=tracinginsights-expanded-v5/  # 原始状态/绝对时间保真；当前已生成 2026 Race

research/
  artifacts/data/             # 冻结实验输入，本地、可由 v4 重建
  artifacts/*manifest*.json   # 冻结身份、配置和哈希，可提交
  cache/                      # 可删除训练缓存
  records/*.json              # 机器可读实验结果

frontend/public/data/         # 精简、可提交、可发布的 session JSON
```

生命周期规则：

- 原始层尽量只追加；修正解析逻辑时生成新的规范层版本，不静默改原始文件。
- 禁止恢复已淘汰的 TracingInsights v1–v3 规范目录。v4 继续作为既有冻结实验输入；需要原始状态与绝对时间的新遥测解释优先 v5，不能用 v5 的存在静默改变旧实验身份。
- 训练缓存可随时删除并从 v4 规范层重建。
- Git 只保留源码、文档、脚本、manifest、研究摘要和精简 sample。
- 大包共享使用 Release、对象存储或 Git LFS，不直接写入主分支历史。

## 4. PostgreSQL 配置与迁移

默认开发连接串来自 `.env.example`：

```text
postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable
```

当前本地约定为 PostgreSQL 18、数据库 `f1_analysis`、端口 `5432`。连接封装位于 `backend/internal/database/postgres.go`；Go 后端当前因 Go 1.22 兼容性使用 `github.com/jackc/pgx` v3 的 `ConnPool`，由项目内 `database.Pool` 隔离。

迁移位于 `backend/migrations/`，runner 按文件名顺序执行全部 `*.up.sql`。当前共有 `001`–`012`，但尚无 migration 版本表和正式回滚入口；不要仅凭文件存在判断某个数据库实例已应用全部迁移。

| 迁移 | 作用 |
| --- | --- |
| `001_create_tables` | 建立赛事核心事实表 |
| `002_create_views` | 圈速 delta、轮胎衰退、TR 上下文视图 |
| `003_add_idempotency_indexes` | positions、race_control 幂等索引 |
| `004_create_api_snapshots` | API 原始 JSON 快照 |
| `005_add_snapshot_category` | 快照分类 |
| `006_add_snapshot_status` | 成功、真实空值、失败等状态和错误 |
| `007_create_api_audit_views` | 最新快照和 OpenF1 endpoint 审计视图 |
| `008_create_lap_enrichment` | speed trap、mini-sector 圈级增强 |
| `009_create_telemetry_tables` | OpenF1/FastF1 逐点遥测表结构 |
| `010_create_endpoint_applicability` | endpoint 对 session 类型的适用性函数 |
| `011_create_tracing_dataset_catalog` | TracingInsights 文件和导入目录 |
| `012_slim_telemetry_storage` | 将 PG 逐点遥测降级为可丢弃缓存并移除 payload GIN |

## 5. Schema 目录

### 5.1 核心赛事事实

| 对象 | 主键/唯一性 | 核心字段 | 关系与用途 |
| --- | --- | --- | --- |
| `meetings` | `meeting_key` | name、location、country、circuit、year、date_start | 赛事周末根实体 |
| `sessions` | `session_key` | `meeting_key`、name/type、start/end | 多对一关联 meeting |
| `drivers` | 自增 id；`(session_key, driver_number)` 唯一 | 姓名、缩写、车队、颜色、头像、国家 | session 内车手快照 |
| `laps` | 自增 id；`(session_key, driver_number, lap_number)` 唯一 | 圈速、三段、pit-out、起始时间 | 圈级分析核心 |
| `stints` | 自增 id；`(session_key, driver_number, stint_number)` 唯一 | compound、胎龄、起止圈 | 轮胎段 |
| `team_radio` | 自增 id；`(session_key, driver_number, date)` 唯一 | URL、英/中转录、意图、情绪、实体、状态 | TR 原始元数据和翻译结果 |
| `pit_stops` | 自增 id；`(session_key, driver_number, lap_number)` 唯一 | duration、date | 进站事实 |
| `positions` | 自增 id；唯一索引 `(session_key, driver_number, date)` | position、date | 位置采样 |
| `race_control` | 自增 id；消息组合唯一索引 | date、category、flag、message、driver、lap | 赛会控制上下文 |

只有 `sessions.meeting_key`、核心表的 `session_key` 显式使用外键；部分车手号关联依靠采集合同而非数据库外键。做删除、重建或跨 session 查询时必须显式限定 `session_key`。

### 5.2 原始快照与质量审计

| 对象 | 内容 | Agent 主要查询场景 |
| --- | --- | --- |
| `f1_api_snapshots` | provider、endpoint、查询、原始 payload、记录数、抓取时间、category、status、error | 补字段、源差异、失败/空值判断、重算 |
| `v_latest_api_snapshots` | 各查询维度最近快照 | 查最新采集状态 |
| `v_openf1_endpoint_audit` | OpenF1 endpoint 质量审计 | 查缺口和异常零记录 |
| `is_endpoint_applicable(session_name, endpoint)` | endpoint 适用性 | 避免对不适用赛段反复请求 |

`record_count = 0` 不等于请求成功且没有数据。必须同时检查 `status` 和 `error_message`；旧零记录会标记为 `legacy_zero`，需要重新验证。

### 5.3 圈级增强、遥测与目录

| 对象 | 定位 | 当前依赖规则 |
| --- | --- | --- |
| `lap_enrichment` | OpenF1 speed trap 与 mini-sector 的可查询圈级副本 | 原始 JSON 仍以快照为准 |
| `telemetry_car_data` | speed、throttle、brake、gear、rpm、DRS | 默认空；仅显式临时物化 |
| `telemetry_location` | x/y/z 位置 | 默认空；仅显式临时物化 |
| `tracing_dataset_files` | 文件 commit、分类、SHA-256、解析状态、采样数 | TracingInsights 文件级目录 |
| `tracing_dataset_imports` | 每年导入任务状态与总量 | 数据包导入审计 |

### 5.4 分析视图

| 视图 | 计算 | 主要限制 |
| --- | --- | --- |
| `v_lap_deltas` | `LAG()` 上一圈差、`FIRST_VALUE()` stint 起始差 | 依赖有效圈速和 stint 关联 |
| `v_tire_degradation` | `REGR_SLOPE/INTERCEPT/R2` stint 线性回归 | 尚未充分剔除 SC、黄旗、交通、雨胎和慢圈 |
| `v_radio_with_context` | TR 关联车手/车队，并匹配最近 `laps.date_start` | 圈数是近似值，不是官方标注 |

具体算法语义和展示边界见 [`algorithm-models.md`](algorithm-models.md)。

## 6. 数据流

### 6.1 Python 直接静态发布

`tools/export_catalunya_static.py` → FastF1 圈速/stint + OpenF1 TR/事件 → `frontend/public/data/manifest.json` 与 session 分片 → 静态 SPA。

这是当前最快交付路径，不写 PostgreSQL。

### 6.2 PostgreSQL 生产路径

OpenF1/FastF1 → `tools/ingest_f1_to_postgres.py` 或 Go `cmd/ingester` → 核心事实表 + `f1_api_snapshots` → SQL 视图 → Go `cmd/exporter` → 同一静态 JSON 合同。

该路径源码已存在，但真实数据库全链路仍需按 [`progress.md`](progress.md) 完成持续验收。

### 6.3 TracingInsights 科研路径

上游原始 JSON → `catalog_tracinginsights_dataset.py` 目录/校验 → `convert_tracinginsights_to_parquet.py` → v4/v5 规范 Parquet → 冻结实验或遥测解释模型。

v5 相对 v4 新增并审计：`drs_raw`、`drs_raw_source`、`track_status_raw`、`lap_start_time`、`sample_time`、`absolute_time_source` 及未登记上游字段。缺失值写 Arrow null，不把未知布尔伪造成 `False`，也不把缺失数值伪造成 NaN。每个分区生成 `telemetry.value-domain-audit.json`，年度 `value-domain-audit.json` 聚合 null、NaN、正负无穷、min/max、枚举频数、未知枚举、来源覆盖和 SHA-256。

2026 Race 当前本地冻结为 commit `81b90e61051a`，四个分区共 3,013,786 行、243,938,416 字节；`data/logs/parquet-acceptance-v5-2026-report.json` 已对四场全部 3,013,786 行执行状态/绝对时间语义校验并 PASS、0 errors，不再把每分区前 20,000 行抽查称为全量验收。`sample_time` 由源 double `tel.time` 计算，而落盘 `time` 为 float32；验收容差严格限定为 `0.5 × float32 ULP + 1μs`，只覆盖存储量化与 timestamp 精度，不放宽来源链或状态语义检查。

当前四场的 `drs_raw` 虽已被 v5 原值保留，但 3,013,786 行均为 0，来源均为 `tel.drs`。这证明 v5 保留了**收到的上游值**，不代表恢复了上游脚本二值化前的新规状态；主动空力、Overtake 和 SOC 仍不可直接观测。

详细数据量、commit 和校验结果见 [`tracinginsights-data-inventory.md`](tracinginsights-data-inventory.md)。

## 7. 常用命令

以下命令均从仓库根目录执行。

设置 FastF1 代理绕过：

```powershell
$noProxyEntries = @($env:NO_PROXY, "livetiming.formula1.com", "api.openf1.org") |
  Where-Object { $_ }
$env:NO_PROXY = $noProxyEntries -join ","
$env:no_proxy = $env:NO_PROXY
```

采集 OpenF1 原始数据：

```powershell
python tools\fetch_f1_data.py --source openf1 --year 2026 `
  --meeting-key 1287 --session-key 11307 `
  --openf1-endpoints drivers,laps,stints,team_radio,pit,position,race_control `
  --out data\raw\f1-api --csv
```

直接入库：

```powershell
$env:DATABASE_URL="postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable"
python tools\ingest_f1_to_postgres.py --source all --year 2026 `
  --meeting-key 1287 --session-key 11307 `
  --fastf1-event Barcelona --fastf1-session R
```

Go 入库与导出：

```powershell
Push-Location backend
go run ./cmd/ingester -session-key=11307
go run ./cmd/exporter -migrate -out ../frontend/public/data
Pop-Location
```

临时重建 PG 遥测缓存（默认不要执行）：

```powershell
python tools\extract_openf1_telemetry.py --year 2025 --materialize-postgres-cache
```

只审计或显式清理：

```powershell
python tools\cleanup_obsolete_storage.py
python tools\cleanup_obsolete_storage.py --apply-files --apply-postgres
```

构建并验收 2026 v5 Race（从仓库根目录）：

```powershell
python tools\convert_tracinginsights_to_parquet.py `
  --year 2026 --root data\raw\tracinginsights\2026 `
  --commit 81b90e61051a83149ff744bc266b55686cb2a6a3 `
  --schema-version tracinginsights-expanded-v5 --sessions Race

python tools\acceptance_check_parquet.py `
  --root data\normalized\tracinginsights\schema=tracinginsights-expanded-v5 `
  --sample-partitions 0 `
  --report data\logs\parquet-acceptance-v5-2026-report.json
```

## 7.1 Race Dossier 追加式存储与 D 盘运行边界

Race Dossier 没有新增 PostgreSQL migration。它使用规范
`tracinginsights-expanded-v4` manifest 选择 2023–2025 Race 分区，再生成圈级
聚合宇宙。当前冻结聚合身份为：

- `research/artifacts/race_dossier_v8/run=20260725T003934+0800-476316f80d23/manifest.json`
- 77,720 圈、70 场、28 位车手；
- `race_lap_universe.csv.gz` 为 3,441,796 字节，SHA-256
  `b3837398ce1a80c9f3c8eb8fc6b0ffce9f9ea1375d00761aa95d80efc0a39db3`。

上游身份清单覆盖 2023 的 22、2024 的 24、2025 的 24 个 Race 分区，共
55,248,206 条逐点遥测采样。v9–v15 复用该小型圈级聚合文件并重新校验
SHA-256，不再扫描 70 个大型 Parquet。下载完整并不等于每圈适合因果或条件
比较。v15 只硬排除物理不可读圈；其余质量、旗帜、进出站和交通行保留在
包容性候选层，以样本外可靠性软权重进入模型，并同时保留严格确认标签。
每次正式输出均使用不可覆盖的 `run=<timestamp>-<config_hash>`：

```text
research/records/race_dossier_v15/run=<id>/
research/artifacts/race_dossier_v15/run=<id>/
frontend/public/data/race-dossier/v15/run=<id>/
```

三个 staging 全部写完后才原子改名为正式 run。中止的 `.staging-*` 不是正式
产物，也不能注册为结果。旧 v1/v2 与 v8–v14 路径不得覆盖。当前正式清单为
`research/records/race_dossier_v15/run=20260725T163219+0800-58bdc41b029f/manifest.json`；
它列出 70 个报告、3 个赛季审计和 145 个输入/配置/实现身份检查。

运行缓存、Python 字节码、临时文件、Matplotlib/XDG/Joblib 缓存和 DuckDB
溢写统一位于项目内 `D:\vibe-coding\F1 TR\.runtime-cache\`。正式入口
`tools/run_race_dossier_v15_low_resource.ps1` 会拒绝非 D 盘项目，使用前台
单线程、单文件批次、768MB DuckDB 上限、批次让出 CPU 和低于正常进程优先级。
低优先级由 `kernel32.SetPriorityClass` 直接设置；项目入口不调用 `wmic`、
`Get-CimInstance`、`Get-WmiObject` 或 `Win32_Process`。2026-07-25 本机 WMI
Operational 日志记录到外部短命客户端反复枚举 `Win32_Process` 和
`Win32_PerfFormattedData_PerfProc_Process`，并出现客户端消失后的 RPC
`0x800706BA`；客户端二进制尚未识别。`tools/audit_low_resource_runtime.py`
按固定入口清单审计零 WMI、前台单进程、线程上限和 D 盘路径，当前通过；这只能
证明 Race Dossier 入口没有主动查询 WMI，不能证明外部宿主/监控已修复。不得
以禁用 Windows WMI 服务作为项目优化手段。

## 8. Agent 数据查询检查单

分析数据问题前依次确认：

1. 问题属于业务事实、API 原始快照、逐点遥测、发布样本还是研究冻结集。
2. 明确 `year / meeting_key / session_key / driver_number / lap_number` 查询范围。
3. 查 manifest、迁移和表注释，确认真相源与数据版本。
4. 对零记录同时检查 endpoint 适用性、`status`、`error_message` 和网络代理。
5. 对研究数值核对配置哈希、数据 SHA-256 与 `research/records/*.json`。
6. 查询生产库时先做只读计数和抽样，避免直接扫描数千万行或物化逐点缓存。
7. 发现 schema 或存储策略变化时同步本文件，不把实时行数硬编码为永久事实。

## 9. 已知数据库风险

- migration runner 无版本表、校验报告和正式回滚入口。
- PostgreSQL 数据库实例是否已应用 `001`–`012` 需要现场查询，不能从仓库推断。
- OpenF1 endpoint 波动会产生缺口；零记录必须区分真实空数据与失败。
- `team_radio.transcript_en` 没有项目内 STT 来源。
- 圈速和衰退视图清洗仍不足以支持工程级结论。
- 逐点数据全库扫描成本高，应优先用 Parquet 分区、预聚合、DuckDB/Polars 和语句超时。
## PAC 条件同队原型机器产物

- 配置：`research/configs/pac_validation_2023_2025_v1.json`
- 结果：`research/records/conditional_pair_prototype_validation_2023_2025_v1.json`
- 摘要：`research/records/conditional_pair_prototype_validation_2023_2025_v1.md`

结果保存 `protocol_hash`、`protocol_frozen_at`、冻结圈数据哈希、
`actual_design_audit.origin_manifests` 及组合哈希。实际 design、target、filter/group
字段由每个拟合实例导出后审计，不再以配置手填字段代替。`identification` 保存全期
`team-year` 图；逐 origin 图和编码特征清单进入 manifest。核心输出名为
`conditional_pair_latent_intervals`，字段为 `conditional_pair_latent_z`；
`legacy_pooled_driver_base_z_binding=false`、`legacy_driver_card_binding=false`，
顶层 `pac` 必须保持 `null`。

v2 新增：

- 配置：`research/configs/pac_validation_2023_2025_v2.json`
- 事件注册：`research/configs/pac_post_freeze_event_registry_v2.json`
- 产物：`research/records/conditional_pair_prototype_validation_2023_2025_v2.json`

`pair_predictions` 保留 `origin_index`、`meeting_key`、`team`、canonical edge、
识别状态、删边/自然新边类别、预测 gap、区间与计分状态。分母分别记录
attempted/eligible/scored origins 和 candidate/eligible/scored pair-events，并按
`calibration_not_ready`、`not_identifiable` 留下跳过原因。个体字段为
`individual_identification_subgate_passed`；最终资格严格等于
`model_publication_gate && driver_evidence_gate`。

## 10. Race Dossier v16 产物字段与存储边界

v16 没有新增 PostgreSQL migration，也没有把逐圈模型中间量写回数据库。新增内容只存在于追加式 Race Dossier JSON：

- `representative_tyre_age_pace_s`
- `representative_tyre_age_laps`
- `representative_tyre_age_support_laps`
- `representative_tyre_age_pace_status`
- `representative_tyre_age_pace_estimator`
- `representative_tyre_age_pace_basis`
- `representative_tyre_age_pace_fuel_sensitivity_interval_s`
- `representative_tyre_age_pace_fuel_sensitivity_basis`
- `representative_tyre_age_direct_comparison_rule`

单场真实数据验收产物位于：

```text
research/records/race_dossier_v16/
  pilot=20260725T214500+0800-stint-reference/
    manifest.json
    race_dossier.json
```

该 manifest 明确声明 `full_season_release=false`、`pilot_events=1`、`expected_full_events=70`，不能冒充正式全量运行。历史 v1/v2、v8–v15 产物没有被覆盖。

工作台导出新增 `--append` 模式：先校验现有 manifest 中每个导出文件的 SHA-256，再只写入新的 report id；若目标已存在则拒绝覆盖。当前工作台保留 70 份 v15、4 份 2026 v7，并追加 1 份 v16 pilot，共 75 份。

v16 的临时目录、Python 字节码、npm 缓存和模型缓存继续全部位于 `D:\vibe-coding\F1 TR\.runtime-cache\`。正式入口 `tools/run_race_dossier_v16_low_resource.ps1` 保持前台、串行、单线程和 768MB DuckDB 上限；静态审计确认入口不包含 WMI/CIM 查询或 `Start-Process`。

## 11. Race Dossier v17 曲线 sidecar

v17 仍不新增 PostgreSQL migration，也不把模型中间量写回数据库。它在每场
`race_dossier.json` 旁追加一个不可变的
`stint_curve_evidence.json`，只覆盖配置中的 reporting teams：

```text
research/records/race_dossier_v17/run=<id>/
  year=<year>/round=<round>/meeting=<slug>/
    race_dossier.json
    stint_curve_evidence.json
```

主报告的 `stint_curve_evidence` 保存 sidecar 的相对路径、SHA-256、字节数和覆盖
计数；run manifest 对每场重复该身份，并汇总 sidecar、逐圈点、有效范围和可比配对
数量。sidecar 自身保存 run 圈宇宙 manifest、圈宇宙 SHA-256、复用输入、排位输入和
实现文件身份，不能脱离对应报告混用。

sidecar 的主要数据层为：

- `stints[].points[]`：逐圈原始/修正配速、燃油敏感性、权重、残差、资格与上下文；
- `stints[].primary_fit`：冻结方程、支持域、网格、经验范围、删块稳定性和具名燃油
  情景；
- `stints[].condition_profile`：原始与加权的阶段、交通、旗语、进出站、质量和
  nuisance 分布；
- `pairwise_comparisons[]`：共同胎龄、两侧锚点范围、配速秒差、平衡距离和失败原因。

工作台导出器把已校验的 sidecar 原字节复制到
`frontend/public/data/telemetry-workbench/curve-evidence/`，同时在报告和工作台
manifest 中写入 source/export SHA-256。构建器只复制 manifest 明确引用且哈希正确
的 sidecar，不递归盲拷贝目录。浏览器按当前报告惰性加载单场 sidecar；不会一次
加载整个赛季逐圈数据。

运行入口为 `tools/run_race_dossier_v17_low_resource.ps1`，缓存仍全部位于项目内
`.runtime-cache`，并保留前台、串行、单线程、单文件批次和 768MB DuckDB 上限。

## 12. 参考分析复刻实验室 v1 追加式数据

F1pace pilot 不新增数据库 migration，也不回写现有 Race Dossier 表。输入复用冻结的
逐点 tracinginsights Parquet：

```text
data/normalized/tracinginsights/schema=tracinginsights-expanded-v4/
  year=2025/commit=f7a5324cae58/session=Race/
    meeting=Abu_Dhabi_Grand_Prix/telemetry.parquet
```

计算直接使用 `driver_acronym`、`lap`、`sample_index`、`time`、`speed`、`distance`、
`driver_ahead`、`distance_to_driver_ahead`、`lap_duration`、`compound`、`stint`、
`tyre_life`、`team`、`track_status`、`deleted` 和 `is_accurate`。其中
`distance_to_driver_ahead` 按米读取、`speed` 按 km/h 转换为 m/s，再以逐点时间间隔
构造交通时间权重；不能用当前 lap 级 median gap 代替比例。

追加式实验产物：

```text
research/records/reference_analysis_lab_v1/f1pace/
  run=20260730T154800+0800-f1pace-abu-dhabi-v1/
    f1pace_2025_abu_dhabi.json
    manifest.json
    validation.json
    method_card.md
    data_gap_audit.md
frontend/public/data/reference-analysis-lab/v1/
  manifest.json
  run=20260730T154800+0800-f1pace-abu-dhabi-v1/
    report.json
    manifest.json
```

`manifest.json` 保存输入文件身份、源 commit、输入行数/列数、v17 manifest SHA-256、
参考页面和 7 张参考图的 SHA-256、报告 SHA-256、覆盖账本及验证状态。实验缓存、抓取
页面、参考图和浏览器截图统一位于 `D:\vibe-coding\F1 TR\.runtime-cache\`；参考原图
不进入 `frontend/public` 产品资产。

冻结排位集 `research/artifacts/data/qualifying_2023_2025_v4.csv.gz` 只提供 2025
Abu Dhabi 的整场最佳准确推圈代理，不含 Q1/Q2/Q3 阶段字段。实验 JSON 必须保存
`q1_q2_q3_phase=NOT_AVAILABLE_IN_CURRENT_FREEZE` 和独立排位代理行，不能把它们与
Race session 合并为同阶段排名。

## 13. DeltaData 方法等价 pilot 追加式数据

DeltaData 不新增数据库 migration，也不改写 v17 sidecar。pilot 输入为：

```text
research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/
  year=2025/round=24/meeting=abu-dhabi-grand-prix/
    stint_curve_evidence.json
```

该 sidecar 保存 19 个 Stint、464 个逐圈点、`traffic_state`、`quality_ok`、
`analysis_weight`、`sample_audit`、`primary_fit.reference_pace_s`、
`primary_fit.fuel_scenarios.low/base/high` 和 `slope_s_per_tyre_lap`。DeltaData
实验层只读取这些冻结字段，按 soft-weight 计算 clean-air pace proxy 与 Kish ESS，
不读取或伪造真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令。

追加式报告与前端身份链为：

```text
research/records/reference_analysis_lab_v1/deltadata/
  run=20260730T235900+0800-deltadata-abu-dhabi-v1/
    deltadata_2025_abu_dhabi.json
    manifest.json
    method_card.md
    data_gap_audit.md
    attempts.jsonl
frontend/public/data/reference-analysis-lab/v1/
  run=20260730T235900+0800-deltadata-abu-dhabi-v1/
    report.json
    manifest.json
```

X profile HTML、帖子参考图、抓取证据和浏览器截图均只在
`D:\vibe-coding\F1 TR\.runtime-cache\reference-analysis-lab-v1\deltadata\`；原始 X
图片不进入产品资产。根 manifest 追加 `deltadata` 目标，目标 manifest 再指向报告并
校验 SHA-256，不能脱离 v17 sidecar 身份链使用。

## 13. 参考分析复刻实验室 v2 交通倒推存储

F1pace v2 不新增 PostgreSQL migration，也不修改 v1 或 Race Dossier v14–v17 产物。
逐点输入仍是冻结的 `tracinginsights-expanded-v4` Race parquet：

- 2025 Abu Dhabi：784,424 行，作为参考图校准事件；
- 2025 Qatar：720,670 行，作为零重拟合事件外验证；
- 两场只读取 `driver_acronym/lap/sample_index/time/lap_start_raw/lap_duration/
  rel_distance/distance/speed/driver_ahead/distance_to_driver_ahead/position`。

追加式身份链为：

```text
research/records/reference_analysis_lab_v2/f1pace/
  run=20260731T120000+0800-f1pace-reverse-engineered-v2/
    f1pace_traffic_reverse_engineered_v2.json
    abu_dhabi_cells.csv
    qatar_external_cells.csv
    method_card.md
    data_gap_audit.md
    reference_difference.md
    attempts.jsonl
    manifest.json
    browser_acceptance.json
frontend/public/data/reference-analysis-lab/v2/
  manifest.json
  run=20260731T120000+0800-f1pace-reverse-engineered-v2/
```

阿布扎比与卡塔尔参考图、Chrome profile、截图和临时特征均只位于
`.runtime-cache/reference-analysis-lab-v2/`；原图没有复制到前端产品资产。
报告冻结遥测、v17 manifest 和两张参考图的 SHA-256，并以独立 CSV 发布
1,156/1,067 个数值单元。`browser_acceptance.json` 是基础 manifest 之后追加的验收
旁证，不改写原 manifest；390px CDP 验收记录 `window.innerWidth=390`、
`document.scrollWidth=390` 和 1,160 个已加载阿布扎比热图单元。

## 14. DeltaData 倒推 v2 追加式数据

DeltaData v2 不新增数据库 migration，不修改 v1 或 Race Dossier v14–v17。主 pilot
继续读取 2025 Abu Dhabi `tracinginsights-expanded-v4` Race parquet，输入
784,424 行；参考数值基准另读取 2026 freeze `commit=81b90e61051a` 的 Japan Race、
Miami Sprint 和 Miami Race。2026 三场只用于算法复核，不进入 2023–2025 排名。

逐点输入字段为：

```text
driver_acronym, lap, sample_index, time, lap_start_raw, lap_duration,
rel_distance, distance, speed, driver_ahead, distance_to_driver_ahead,
position, stint, compound, tyre_life, track_status, deleted, is_accurate, team
```

四场物理交通中间表仅写入：

```text
.runtime-cache/reference-analysis-lab-v2/deltadata/traffic-features/
  2025-abu-dhabi-race.parquet
  2026-japan-race.parquet
  2026-miami-sprint.parquet
  2026-miami-race.parquet
  *.identity.json
```

每个 identity 保存源 parquet/交通表 SHA-256、行数、车手数、最大圈和
`nearest_physical_car_same_track_location_crossing_headway_le_2s` 定义。公开方法
搜索镜像、图表人工转录、图像哈希及无效参考资产审计保存在
`.runtime-cache/reference-analysis-lab-v2/deltadata/public-method-evidence.json`；
人物图和社交原图均未复制到前端。

追加式发布身份链为：

```text
research/records/reference_analysis_lab_v2/deltadata/
  run=20260731T153000+0800-deltadata-reverse-engineered-v2/
    deltadata_reverse_engineered_v2.json
    abu_driver_ranking.csv
    abu_team_ranking.csv
    abu_teammate_h2h.csv
    abu_lap_evidence.csv
    reference_benchmark.csv
    legacy_driver_difference.csv
    legacy_team_difference.csv
    model_coefficients.csv
    method_card.md
    data_gap_audit.md
    reference_difference.md
    attempts.jsonl
    manifest.json
    browser_acceptance.json
    acceptance_manifest.json
frontend/public/data/reference-analysis-lab/v2/
  manifest.json
  run=20260731T153000+0800-deltadata-reverse-engineered-v2/
```

主报告同时保存 `visual_replication` 与 `audited_analysis`，二者不复用同一排名字段。
`lap_evidence` 保留真实候选圈的交通比例、coverage、燃油情景圈时、模型预测、残差、
Huber 代表圈状态和软权重；`exclusion_ledger` 保存全部 1,156 圈的互斥处置计数。
`reference_benchmark` 明确记录 `human_model_choice_informed_by_seen_reference=true` 和
`reference_values_used_as_model_training_target=false`，避免把参考知情反推冒充盲测。

基础 manifest 冻结主报告、8 张数值 CSV、方法卡、data-gap、差异说明、attempts 以及
Abu telemetry/交通、参考图、方法证据、v17 manifest/sidecar 和 v1 report 的哈希。
浏览器验收随后以新文件 `browser_acceptance.json` 和 `acceptance_manifest.json`
追加，不重写基础 manifest。

## 15. FDataAnalysis 倒推 v2 追加式数据

FDataAnalysis v2 不新增 PostgreSQL migration，不回写 v1、Race Dossier v14–v17
或排位冻结集。主输入为真实 2025 Abu Dhabi Race
`tracinginsights-expanded-v4` telemetry，共 784,424 行；过滤后 1,082 个有效圈、
728,315 个逐点样本。逐点 feed 的中位采样间隔为 `0.130000s`，该场约 `7.692Hz`，
不得笼统写成 4Hz。

追加式产物为：

```text
research/records/reference_analysis_lab_v2/fdataanalysis/
  run=20260731T170000+0800-fdataanalysis-reverse-engineered-v2/
    fdataanalysis_reverse_engineered_v2.json
    manifest.json
    method_card.md
    data_gap_audit.md
    reference_difference.md
    attempts.jsonl
    lap_metrics.csv
    corner_lap_features.csv
    straight_lap_features.csv
    tyre_age_stints.csv
    matched_lap_pairs.csv
    browser_acceptance.json
    acceptance_manifest.json
```

`lap_metrics.csv` 为 1,082 行圈级速度/控制/时间积分/交通和资格字段；
`corner_lap_features.csv` 为 17,312 行 driver×lap×16 动态弯角代理特征；
`straight_lap_features.csv` 为 6,492 行 driver×lap×6 直道代理特征；
`tyre_age_stints.csv` 为 37 行单 Stint 鲁棒斜率审计；`matched_lap_pairs.csv`
为 34 行同队条件匹配。主报告 SHA-256 为
`305b388a0ba43861d1335d43ab1c62b9c1f51efefb6fe0075ec7c039d21f4c4e`，
基础 manifest SHA-256 为
`60c7b9bc20a994a63d4af6c33cc87b897e143a111639aeb59a7bde570a5f3063`。

参考图、X/Bluesky 页面、公开方法证据、无效图片证据和所有抓取缓存只保存在
`.runtime-cache/reference-analysis-lab-v2/fdataanalysis/` 或对应 v1 缓存中；
原图不复制到产品目录。已确认主题不符的缓存图标记为
`INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH`，禁止进入方法证据。前端只消费复制到
`frontend/public/data/reference-analysis-lab/v2/
run=20260731T170000+0800-fdataanalysis-reverse-engineered-v2/` 的带哈希 JSON。

## 16. F1TelemetryData 图表包倒推 v1 追加式数据

本目标不新增 PostgreSQL migration，不回写排位冻结集、F1pace 产物或 Race Dossier
v14–v17。主逐点输入为 2025 Abu Dhabi 的
`tracinginsights-expanded-v4` Qualifying 395,840 行与 Race 784,424 行；排位准确
推圈来自 `qualifying_2023_2025_v4.csv.gz` 的同场 88 行。外部补充固定为 OpenF1
REST v1，所有响应按精确 URL、HTTP 状态、字节数、字段、行数与 SHA-256 冻结在：

```text
.runtime-cache/reference-analysis-lab-v2/f1telemetrydata/openf1/
  source-manifest.json
  source-manifest-fastest-lap-car-data.json
  qualifying_laps.json
  qualifying_results.json
  qualifying_race_control.json
  qualifying_fastest_lap_car_data.json
  race_laps.json
  race_pit.json
  race_stints.json
```

OpenF1 补充只承担 Q 阶段身份、官方分段计时、测速点、Stint 和维修区字段；本地冻结
遥测仍是逐点主源。`lane_duration` 与 `stop_duration` 是不同字段，不得互相填补。
参考频道 HTML、X profile、13 张参考图和公开方法证据仅保存在
`.runtime-cache/reference-analysis-lab-v2/f1telemetrydata/`，原图不复制到前端产品。

追加式正式产物为：

```text
research/records/reference_analysis_lab_v2/f1telemetrydata/
  run=20260731T193000+0800-f1telemetrydata-reverse-engineered-v1/
    f1telemetrydata_reverse_engineered_v1.json
    qualifying_timings.csv
    qualifying_control_sections.csv
    qualifying_top3_traces.csv
    race_pace_laps.csv
    race_timings.csv
    tyre_strategies.csv
    pit_stops.csv
    method_card.md
    data_gap_audit.md
    reference_difference.md
    attempts.jsonl
    manifest.json
    browser_acceptance.json
    acceptance_manifest.json
```

主报告同时保存 `visual_replication` 与 `audited_analysis`，不共用因果排名字段。
基础 report SHA-256 为
`af132dd167f8da91ca30071fdcd1f5a4c9e7268ce7d4c267a93651441301aea5`，
基础 manifest SHA-256 为
`5a32585b7e5d13fb0cad481944e193c4a96747209636d4607ff23ab25527c46f`。
浏览器验收以独立文件追加，acceptance manifest SHA-256 为
`5f16e1be8ce4d45eb2a5c3f0e6c3c553bed879a92f7a5d2603b0f56d3871f635`。

## 17. GP Tempo 分段 Delta v1 追加式数据

GP Tempo v1 不新增数据库 migration，不改写 Race Dossier、排位冻结集或前四项目。
本目标使用两类冻结输入：

1. 2025 Abu Dhabi `tracinginsights-expanded-v4` Qualifying/Race 提供
   `rel_distance/x/y` 几何、session、配方、胎龄和赛道状态；
2. 冻结 OpenF1 `car_data` 与 `laps` 提供约 4 Hz speed/throttle/brake/gear/DRS、
   圈时和官方三段时间。

公开 About SPA shell、bundled JavaScript/CSS、X profile、Race 五圈 car channel
和源 manifest 全部位于：

```text
.runtime-cache/reference-analysis-lab-v2/gptempo/
  gp-tempo-about.html
  index-BAdvg-92.js
  index-H9MyqAZ_.css
  x-f1-tempo-profile.html
  race_fastest_lap_car_data.json
  source-manifest.json
```

Qualifying car channel 复用已冻结且带哈希的
`.runtime-cache/reference-analysis-lab-v2/f1telemetrydata/openf1/
qualifying_fastest_lap_car_data.json`。所有下载、脚本快照和浏览器临时证据均留在
`.runtime-cache`，不进入产品资产。

正式追加式产物为：

```text
research/records/reference_analysis_lab_v2/gptempo/
  run=20260731T213000+0800-gptempo-reverse-engineered-v1/
    gptempo_reverse_engineered_v1.json
    laps.csv
    display_traces.csv
    comparisons.csv
    sector_endpoints.csv
    track_geometry.csv
    data_gap_audit.csv
    method_card.md
    data_gap_audit.md
    reference_difference.md
    attempts.jsonl
    manifest.json
    browser_acceptance.json
    acceptance_manifest.json
```

`laps.csv` 保存十个真实圈的 session/配方/胎龄/采样身份；`display_traces.csv`
是 401 点统一距离轴的五通道展示数据；`comparisons.csv` 保存 90 个有序圈对的终点
差和条件状态；`sector_endpoints.csv` 保存 270 个官方端点的期望、实际和误差；
`track_geometry.csv` 保存 401 点本地冻结几何。主报告 SHA-256 为
`efc0f46f01c0250094acc606ba56479c6345d5c4bd278e6c2f2bd21736f31fcb`，
基础 manifest SHA-256 为
`9752518d3b571a1f59c1216fab7f20fa82ef8db0ac1013897fb538cc51463377`，
acceptance manifest SHA-256 为
`f8be61a2cbaa611f95475f0e45aa95222182d260123e1099f6c30a4b4fd3368f`。

## 18. 参考分析复刻实验室 v3 分赛道验证数据

本迭代不新增数据库 migration，不改写 Race Dossier v14–v17 或
reference-analysis-lab v1/v2。正式真相源为：

```text
research/records/reference_analysis_lab_v3/
  run=20260731T235900+0800-track-validation-v3/
    manifest.json
    universal_baseline_recheck.json
    event_catalog.csv
    observable_track_registry.csv
    track_validation_v3.json
    model_registry.json
    targets/
      f1pace/
      deltadata/
      fdataanalysis/
      f1telemetrydata/
      gptempo/
```

每个 `targets/<target>/` 至少包含：

```text
manifest.json
method_card.md
data_gap_audit.md
reference_difference.md
coverage_matrix.csv
split_registry.csv
model_comparison.csv
model_registry.json
sample_exclusion_ledger.csv
```

按目标追加的证据表包括 F1pace `event_predictions/model_coefficients/
pairwise_invariants`，DeltaData `event_predictions/model_coefficients/
degradation_direction/teammate_direction`，FDataAnalysis
`segmentation_events/strategy_selection_audit/layout_stability`，
F1TelemetryData `chart_reference_matrix`，以及 GP Tempo
`sector_endpoint_validation`。每个子 manifest 都只指向正式 run 内文件并冻结
SHA-256、bytes、rows 与 columns；最终根 manifest SHA-256 为
`c0db603684f5dada772ca33edeb67b68d4d606254f30154f2cb20324a6b6f9ed`。
主报告与合并 model registry 的 SHA-256 分别为
`d07b66f5f3750ad3299e9e00322721b5147912f2bef73443be68ec21495cf777`
和
`c04e141e42c4c5ed316cfe4122ef3725c3615a1c64db63f37b1c407324485c19`。

前端只复制主报告、合并 registry 与 public manifest：

```text
frontend/public/data/reference-analysis-lab/v3/
  manifest.json
  run=20260731T235900+0800-track-validation-v3/
    manifest.json
    track_validation_v3.json
    model_registry.json
```

根索引保留三个追加身份：`233000` 为
`FAILED_POST_PUBLICATION_IDENTITY_AUDIT`，`235500` 为
`SUPERSEDED_BY_ITERATED_VALIDATION`，`235900` 为唯一 latest PASS。
前两个正式目录不清理、不覆盖。

逐事件缓存固定在：

```text
.runtime-cache/reference-analysis-lab-v3/
  events/<event_id>/
    traffic_laps.parquet
    track_profile.npz
    segments.json
    identity.json
```

70 个 `identity.json` 分别记录源 parquet、traffic、profile 与 segments 的哈希；
缓存覆盖 2023 的 22 场、2024 的 24 场、2025 的 24 场，共读取
55,248,206 个原始逐点样本。缓存和全量逐点输入不复制进正式产品。

浏览器截图只位于：

```text
.runtime-cache/reference-analysis-lab-v3/browser-acceptance/
  run=20260731T235900+0800-track-validation-v3-r2-cdp-css390/
    <target>-desktop.png
    <target>-mobile-390.png
    browser_acceptance.json
```

五个目标的独立 acceptance manifest 与总索引位于
`research/records/reference_analysis_lab_v3_browser_acceptance/`。最终 CDP
acceptance manifest SHA-256 为
`13b8967809c44a8ac7f196350f96de82cdf0aa38c9b584ef47a5a4d7eff6a36f`。
首轮 CLI 截图虽为 390 PNG，但 CSS viewport 被钳大，已在 acceptance 索引中标为
`FAILED_CSS_VIEWPORT_IDENTITY_AUDIT` 并原样保留。
