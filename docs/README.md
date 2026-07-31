# F1 TR 文档地图与 Agent 查询指南

> 这是仓库文档的唯一导航入口。Agent 开始分析前应先读本文件，再按任务路由读取最少必要文档。本文不承载详细业务结论，负责回答“信息在哪、以谁为准、修改后要同步哪里”。

## 1. 五份主文档

| 文档 | 唯一职责 | 典型问题 | 不应放入 |
| --- | --- | --- | --- |
| [`technical-overview.md`](technical-overview.md) | 产品边界、系统架构、数据流、静态合同 | 系统做什么、模块如何协作、前端读取什么 | 任务进度、实验指标、完整数据库字典 |
| [`database.md`](database.md) | 数据源、存储分层、PostgreSQL、迁移、表/视图、数据复现 | 数据在哪、表怎么关联、如何采集/入库/清理 | 模型优劣结论、开发排期 |
| [`algorithm-models.md`](algorithm-models.md) | 产品派生算法、科研模型、特征、协议、评价和解释边界 | delta/衰退怎么算、当前研究哪些模型、如何复现实验 | 软件构建是否通过、待办状态 |
| [`implementation-results.md`](implementation-results.md) | 已经实现且有证据验证的结果快照 | 现在真正能运行什么、样本规模、测试和模型结果 | 计划、愿望、未验收能力 |
| [`progress.md`](progress.md) | 当前进度、阻塞、优先级、下一步和完成定义 | 现在做到哪、接下来做什么、什么被阻塞 | 长篇原理、历史流水账 |

职责判定口诀：

- “是什么、如何连接”写入技术总览。
- “数据在哪里、怎么存取”写入数据库文档。
- “怎么算、为什么这样算”写入算法模型文档。
- “已经证明了什么”写入实现结果。
- “还要做什么”写入当前进度。

## 2. Agent 任务路由

| 任务意图 / 关键词 | 首读 | 必要时继续读 | 代码或产物入口 |
| --- | --- | --- | --- |
| 项目定位、v1 边界、整体架构、数据流 | `technical-overview.md` | `implementation-results.md` | `README.md`、`backend/cmd/`、`frontend/src/` |
| PostgreSQL、表、字段、索引、迁移、SQL 视图 | `database.md` | `local-data-pipeline.md`（仅命令补充） | `backend/migrations/`、`backend/internal/database/` |
| OpenF1、FastF1、TracingInsights、Parquet、快照 | `database.md` | `tracinginsights-data-inventory.md` | `tools/fetch_*`、`tools/ingest_*`、`tools/convert_*` |
| 圈速 delta、轮胎衰退、TR 近似圈数 | `algorithm-models.md` | `database.md` | `002_create_views.up.sql`、`tools/export_catalunya_static.py` |
| 车手—车辆分离、ridge、Bayesian、bootstrap、动态衰减 | `algorithm-models.md` | `research/README.md`、科研报告 | `research/run_*.py`、`research/configs/` |
| 四队车辆基准、全量逐站、单场/stint 留出、周末上沿、赛季总结、2026 跨规则迁移 | `four-team-car-driver-attribution-model.md` | `algorithm-models.md`、`database.md` | `research/configs/`、`research/run_four_team_race_by_race.py`、`research/records/*.json` |
| 同队双车异常、弯道/直道响应、Episode、2026 策略指纹、车手评分卡 | `algorithm-models.md`（5.13） | `database.md`、`implementation-results.md` | `research/run_telemetry_explanation.py`、`research/run_2026_telemetry_batch.py`、`research/modeling/{telemetry_explanation,episode_engine,strategy_fingerprint,performance_scoring}.py` |
| 实验数值、样本量、测试是否通过、前端已经支持什么 | `implementation-results.md` | 对应机器可读结果 | `research/records/*.json`、`frontend/public/data/manifest.json` |
| 当前任务、阻塞、优先级、下一步 | `progress.md` | `implementation-results.md` | `git status`、当前测试输出 |
| MultiViewer 临场采集 | `multiviewer-api-capture.md` | `database.md` | `tools/save_multiviewer_*.mjs` |
| 测试命令、测试分层、验收顺序 | `test-strategy.md` | `implementation-results.md` | `tests/`、Go `*_test.go`、`frontend/scripts/check-build.js` |
| 历史原因、某次改动经过 | `work-log.md` | 对应 Git 提交 | `git log`、`git blame` |
| 存储瘦身历史 | `storage-cleanup-20260718.md` | `database.md` | `tools/cleanup_obsolete_storage.py` |

### Race Dossier 任务路由

完整比赛覆盖、事件账本、Stint、赛前轮胎包络、车辆—车手秒数闭合和赛季审计，
先读 `algorithm-models.md` 的 5.14 与“Race Dossier v17 逐圈散点、冻结回归与
条件平衡”，再读 `implementation-results.md` 的 v17 正式运行验收与
`database.md` 的 7.1、11；当前状态以 `progress.md` 的
“2026-07-26 Race Dossier v17 当前状态”为准。机器真相源是
`research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/manifest.json`，
不得从旧日志或年终积分榜反向推断模型状态。

### 推荐读取顺序

1. 本文档：确定信息归属和权威来源。
2. 一份主题主文档：建立当前上下文。
3. 主题文档点名的代码、迁移、配置或机器可读产物：验证当前实现。
4. 仅当需要解释历史决策时，再读日志和带日期报告。

不要一开始递归读取 `data/raw/`、`research/artifacts/data/`、`.fastf1-cache/` 或全部实验日志。这些目录体积大，且多数任务只需 manifest、schema、配置和摘要。

## 3. 权威性与冲突处理

从高到低采用以下优先级：

1. 当前源码、SQL migration、配置文件、自动化测试、机器可读 JSON manifest/结果。
2. 本目录五份主文档中负责该主题的文档。
3. 专题操作文档，例如 MultiViewer、测试策略、TracingInsights 清单。
4. `work-log.md`、`research/records/research_log.md`、带日期的清理报告。
5. Git 历史和旧产物副本。

如果高优先级来源与主文档不一致，Agent 应以实现为准完成分析，并在同一变更中更新对应主文档。数据库实时行数会变化，引用时必须注明查询时间；研究冻结集的行数和哈希则应以 manifest 为准。

## 4. 数据与结果的“当前”定义

- 文档快照日期：**2026-07-19**。
- “已实现”表示源码存在；“已验证”表示有当前测试、构建、manifest 或结果文件证据；两者不能混用。
- `frontend/public/data/` 是可提交、可发布的精简样本，不代表完整本地数据库。
- `data/raw/` 是可再处理的原始本地产物，默认不进 Git。
- `data/normalized/tracinginsights/schema=tracinginsights-expanded-v4/` 是 TracingInsights 逐点遥测的规范真相源。
- `research/artifacts/data/` 与 `research/cache/` 是可重建的本地研究输入/缓存；研究冻结身份由 manifest 和 SHA-256 确认。
- `research/records/*.json` 是实验结果的机器可读真相源；同名 Markdown 是便于阅读的摘要。

## 5. 常用代码地图

| 区域 | 入口 | 主要责任 |
| --- | --- | --- |
| Go CLI | `backend/cmd/{ingester,translator,exporter}/` | 入库、翻译、静态导出 |
| 遗留本地 API | `backend/cmd/api/`、`backend/internal/api/` | 本地调试；不是公网主线 |
| 数据库 | `backend/migrations/`、`backend/internal/database/` | schema、视图、连接和迁移执行 |
| Python 数据工具 | `tools/` | 抓取、入库、规范化、导出、清理和验收 |
| 前端 | `frontend/src/`、`frontend/public/data/` | 静态 SPA 与发布数据 |
| 科研 | `research/run_*.py`、`research/configs/` | 冻结数据、基线、探索模型与比较 |
| 测试 | `tests/`、Go `*_test.go`、`frontend/scripts/` | Python、Go、前端合同与构建验证 |

## 6. 文档维护协议

任何 Agent 完成变更时，按下表同步；没有对应影响时不要机械修改日期。

| 变更 | 必须同步 |
| --- | --- |
| 新增/修改 migration、表、视图、索引、存储层 | `database.md`，必要时 `technical-overview.md` |
| 修改 delta、degradation、特征、清洗、模型或实验切分 | `algorithm-models.md`，并更新研究配置/记录 |
| 新能力通过测试或实验产生新结论 | `implementation-results.md`，保留证据路径和验证日期 |
| 开始、完成、阻塞或调整优先级 | `progress.md` |
| 新增专题文档或文档改名 | 本文件的主文档表和任务路由 |
| 改变产品边界、部署方式或主数据流 | `technical-overview.md`、根 `README.md` |

写作约束：

- 用“事实 / 限制 / 待办”明确区分已验证内容与计划。
- 数值附数据口径、日期和证据路径；不要复制一份无人维护的最新行数。
- 命令必须注明工作目录或使用仓库根目录相对路径。
- 主文档之间用链接引用，不重复粘贴长段落。
- 历史记录追加，不回写成当前状态；当前状态只维护在 `progress.md`。

## 7. 专题与历史文档

| 文档 | 类型 | 使用说明 |
| --- | --- | --- |
| [`local-data-pipeline.md`](local-data-pipeline.md) | 兼容入口 | 已收束为数据库文档的命令索引 |
| [`multiviewer-api-capture.md`](multiviewer-api-capture.md) | 专题操作 | 临场 MultiViewer API/DOM 采集手册 |
| [`test-strategy.md`](test-strategy.md) | 专题规范 | 测试分层、清单和验收顺序 |
| [`tracinginsights-data-inventory.md`](tracinginsights-data-inventory.md) | 数据证据 | 上游 commit、文件量、规范层验收 |
| [`research-report-driver-car-modeling.md`](research-report-driver-car-modeling.md) | 深度研究设计 | 完整科研背景、候选算法与长期路线，不是当前结果入口 |
| [`four-team-car-driver-attribution-model.md`](four-team-car-driver-attribution-model.md) | 建模主规范 | 四队车辆基准、全量逐站、性能包络、车手提取、stint 留出、结果归因和 2026 跨规则迁移的执行合同；M0/M1、静态贝叶斯、M2 条件分位数与 v5 全量报告层已实现 |
| [`work-log.md`](work-log.md) | 历史日志 | 开发过程追溯，不代表当前进度 |
| [`storage-cleanup-20260718.md`](storage-cleanup-20260718.md) | 历史报告 | 2026-07-18 存储处置证据 |
| [`../research/README.md`](../research/README.md) | 实验运行手册 | 研究协议、命令、输出目录 |
| [`../research/records/research_log.md`](../research/records/research_log.md) | 研究日志 | 实验演进、失败与决策追溯 |
