# F1 TR 实现与验收结果

> 本文只记录已经实现并有证据支持的事实，不记录计划。快照日期：**2026-07-23**。任务状态与下一步见 [`progress.md`](progress.md)。数值如与机器可读产物冲突，以产物为准。

## 1. 当前可交付形态

项目已经形成“离线数据生产 + 静态 JSON + 浏览器 SPA”的早期可运行闭环：

- Python 可从 FastF1/OpenF1 生成 Catalunya 默认 session 的静态数据；OpenF1-only 降级路径可用。
- Go `ingester`、`translator`、`exporter` 和遗留本地 API 可编译，exporter 已实现静态合同。
- 前端无需在线后端即可读取 manifest/session 分片，完成筛选、搜索、表格和图表交互。
- PostgreSQL schema、分析视图、快照审计和 TracingInsights 目录迁移已实现到 `012`。
- TracingInsights 2023–2025 保持 expanded v4 科研冻结层；2026 四场 Race 已并行建立保留原始状态与绝对时间的 expanded v5。
- 车辆—车手分离首轮基线、探索模型和稳健性实验已有可复现结果。
- 车辆—车手 v4 已保留到 69 场、1,298 个 stint 和 25,343 个四队 Race 圈，并接入独立 Qualifying 条件前沿。

## 2. 软件实现结果

### 2.1 数据与后端

| 能力 | 结果 | 证据 |
| --- | --- | --- |
| OpenF1/FastF1 通用抓取 | 支持原始 JSON、可选 CSV、endpoint 容错 | `tools/fetch_f1_data.py` |
| Python 直接静态导出 | 默认 `meeting_key=1287`、`session_key=11307`，支持 OpenF1 降级 | `tools/export_catalunya_static.py` |
| Python PostgreSQL 入库 | 可写规范业务表和 `f1_api_snapshots` | `tools/ingest_f1_to_postgres.py` |
| Go CLI | ingester、translator、exporter 入口存在并通过编译测试 | `backend/cmd/` |
| 数据库迁移 | `001`–`012`，覆盖业务表、分析视图、审计、遥测目录和瘦身策略 | `backend/migrations/` |
| 静态 exporter | manifest、meeting/session 索引和 session 分片合同已实现 | `backend/internal/exporter/` |
| TracingInsights 规范化 | 原始目录、catalog、expanded v4/v5 Parquet、值域审计和验收工具齐备 | `tools/catalog_*`、`convert_*`、`acceptance_*` |

### 2.2 前端

前端为零第三方运行时依赖的静态 SPA，当前已实现：

- manifest 驱动的分站/session 数据加载；
- 车手筛选、圈数范围筛选和 Team Radio 文本搜索；
- 圈速、delta、stint、degradation 数据组合展示；
- 中文化 live review 页面和洞察卡片；
- `npm run build`、`npm test` 的本地构建合同。

### 2.3 MultiViewer 临场采集

- `save_multiviewer_api_state.mjs` 可采集 REST/GraphQL Live Timing topic、写快照、`latest.json` 和索引。
- `core` topic 包含 session、圈数、车手、TimingData、轮胎、进站、赛会控制、TeamRadio、天气等；CarData/Position/DriverTracker 需显式 `--include-heavy`。
- DOM 圈数/轮胎和 AI Radio 脚本作为 API 不可用时的低优先级兜底。

## 3. 当前发布样本

`frontend/public/data/manifest.json` 当前默认指向 2026 Barcelona Grand Prix Race：

| 项目 | 已验证数量/值 |
| --- | ---: |
| `meeting_key` | 1287 |
| `session_key` | 11307 |
| 车手 | 22 |
| 原始圈记录 | 358 |
| 前端有效圈速图表记录 | 355 |
| stint | 70 |
| Team Radio 录音记录 | 40 |

该目录是精简演示数据，不代表本地完整数据库，也不证明所有可选 endpoint 在每次抓取都非空。

## 4. TracingInsights 数据结果

详细来源 commit、文件级校验和分区验收见 [`tracinginsights-data-inventory.md`](tracinginsights-data-inventory.md)。当前文档化验收结果：

| 年份 | 原始遥测文件 | 有效 | 无效 | v4 行数 | v4 体积 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023 | 59,787 | 59,786 | 1 | 60,221,664 | 3.37 GB |
| 2024 | 67,616 | 67,616 | 0 | 68,814,885 | 3.84 GB |
| 2025 | 69,459 | 69,459 | 0 | 69,847,468 | 3.91 GB |
| 2026 | 21,428 | 21,428 | 0 | 28,081,700 | 1.32 GB |
| 合计 | 218,290 | 218,289 | 1 | 226,965,717 | 约 12.4 GB |

v4 迁移后的主研究冻结：2024 训练 23,165 圈，2025 验证 22,245 圈。对应 SHA-256 记录在 `research/records/v4_migration_summary.md` 和 manifest 中。

## 5. 算法与模型结果

### 5.1 v4 首轮确认性基线

机器可读来源：`research/records/latest_results.json`。目标为赛段内相对圈速，2024 训练、2025 时间外验证。

| 模型 | 2024 CV MAE | 2025 MAE | 2025 RMSE | 2025 R² | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| `zero_relative_pace` | — | 1.09883 | 1.39641 | -0.00016 | 最低基线 |
| `context_ridge` | 0.93582 | 0.90608 | 1.21418 | 0.24384 | 保留上下文基线 |
| `driver_only_ridge` | 0.72588 | 0.82296 | 1.13461 | 0.33971 | 保留消融 |
| `team_only_ridge` | 0.73372 | 0.84321 | 1.15572 | 0.31491 | 保留消融 |
| `driver_team_ridge` | 0.72561 | 0.80562 | 1.11872 | 0.35807 | v4 简单联合基线 |

v4 相比旧 PostgreSQL 口径没有改变“联合模型优于简单基线”的总体方向，但 `team_only` 和 `driver_team` 的最优 alpha 与误差发生变化，因此后续以 v4 数值为准，不再引用旧 PG 数值作为当前结果。

### 5.2 旧 PG 首轮稳健性证据

旧口径配对分站 bootstrap 显示 `driver_team_ridge` 相对 `context_ridge`、`driver_only_ridge` 和 `team_only_ridge` 均有负 MAE delta；但该结果属于旧 PG 数据冻结，只用于证明首轮方向，不覆盖 v4 最新指标。证据：`research/records/model_comparisons.json`。

Sprint 分层中联合模型相对零基线更差；当前不发布 Sprint 能力评分。

### 5.3 阶段 3 探索性结果

| 模型 | 2025 MAE | 2025 R² | 结论 |
| --- | ---: | ---: | --- |
| `session_head_ridge` | 0.79090 | 0.40507 | 未超过当时旧 PG 联合基线 0.79059 |
| `team_track_ridge` | 0.79625 | 0.39812 | 时间外变差 |
| `bayesian_driver_team` | 0.81751 | 0.35325 | 点预测较弱；95% 覆盖率 92.32% |

这些探索结果基于验收前旧 PG 口径，不能直接与 v4 最新表格混排排名；应在 v4 上重跑后再更新当前结论。

### 5.4 敏感性与动态结果

- 旧 PG 口径中，统一 2025 跨来源车队别名后，联合模型 MAE 从 0.79059 变为 0.79733；原始结果部分受“未见车队=零效应”偶然收益影响。实体正确性优先于更好看的误差。
- 跨赛季统一乘法衰减的最优车手/车队保留率均为 1.00；`dynamic_decay` 与 `full_carryover` 指标相同，没有增益。
- 更合理的下一模型结构是显式 `team × season` 动态层级，而不是统一乘法衰减。

### 5.5 四队最均衡单站 M0/M1 原型

机器可读来源：`research/records/four_team_attribution_hungary_2025.json`；配置：`research/configs/four_team_2025_baseline_v1.json`。这是已经查看 2025 数据后的探索性开发结果，不是确认性验证。

覆盖审计在 2025 Race 中选择匈牙利站：四队 8 位车手共 519 个清洗后有效圈，单车手 64–66 圈，圈数 CV 为 0.0099。模型识别使用当站全场 10 队、20 位车手；2024 Race 历史先验使用 21,047 圈。圈级拟合内样本 MAE 为 0.59807 `target_log_pct`，只作实现诊断，不是时间外性能。

| 车辆顺序 | 相对最快车 | 95% bootstrap 区间 | 匈牙利参考圈差 | 90 秒标准圈差 |
| --- | ---: | ---: | ---: | ---: |
| McLaren | 0.0000% | [0.0000, 0.0000] | 0.0 ms | 0.0 ms |
| Mercedes | 0.5167% | [0.2549, 0.9230] | 426.1 ms | 466.2 ms |
| Ferrari | 0.9974% | [0.3632, 1.5900] | 824.6 ms | 902.2 ms |
| Red Bull Racing | 1.4417% | [1.1940, 1.6242] | 1194.5 ms | 1306.9 ms |

预注册的 9 组车辆/车手收缩强度组合都得到 `McLaren > Mercedes > Ferrari > Red Bull Racing`。外部核验未参与拟合或选参：

- OpenF1 `session_key=9928` 共读取 1,369 圈；双车“最快四分之一有效圈中位数”的车队顺序与模型完全一致，Spearman 为 1.000，两两车队差值 MAE 为 155.0 ms。
- FIA Doc 44 官方最快圈双车均值的车队顺序也完全一致，Spearman 为 1.000，两两差值 MAE 为 281.5 ms。
- 同队车手提取方向与 FIA 单一最快圈只匹配 2/4。该负面结果与宽 bootstrap 区间共同表明：车辆层有初步表面效度，车手提取和人车交互尚未通过外部验证，不能发布为稳定排名。

限制：区间是 300 次车手 → stint → 圈分层 bootstrap，不是贝叶斯后验；单站车手更新与人车交互共线；尚无赛段分解、策略、可靠性、`V_result`、`V_points` 或 2026 迁移。

### 5.6 六站赛果/观感反例压力测试

机器可读来源：`research/records/four_team_contrast_stress_2025.json`；配置：`research/configs/four_team_2025_contrast_stress_v1.json`。运行 ID 为 `four_team_2025_contrast_stress_v1-20260718T184954`。六站按已知结果事后挑选，固定匈牙利原型参数，不做目标站调参。

| 分站 | 预设观察组 | 冠军车队 | 模型 P50 最快车 | 冠军车队模型排名 | 官方双车最快圈代理 | 模型/代理 Spearman |
| --- | --- | --- | --- | ---: | --- | ---: |
| Miami | 高度一致 | McLaren | McLaren | 1 | McLaren | 0.80 |
| Spain | 高度一致 | McLaren | McLaren | 1 | McLaren | 0.80 |
| Mexico City | 高度一致 | McLaren | McLaren | 1 | Mercedes | 0.40 |
| Canada | 反直觉 | Mercedes | Mercedes | 1 | McLaren | 0.80 |
| Japan | 反直觉 | Red Bull Racing | McLaren | 4 | McLaren | 1.00 |
| Italy | 反直觉 | Red Bull Racing | McLaren | 4 | McLaren | 0.80 |

聚合上，模型最快车与冠军一致 4/6，官方最快圈代理与冠军一致 2/6，模型与代理的平均 Spearman 为 0.767。日本和意大利是最重要的反例：模型及外部代理都不把冠军车队视作最快车，模型中 Verstappen 相对 Tsunoda 的同车标准化当站中位圈差分别约 945.6 ms 和 1503.1 ms。这支持“车辆均值、车手当站发挥和最终结果必须分层阅读”，但不证明上述差值是纯车手能力；交通、策略、赛车状态和位置仍未建模。

六场 3×3 收缩敏感性网格各自均为 9/9 同一车队效应顺序。Jolpica 分类同时核对配置中的冠军和领奖台；OpenF1 在本次执行返回 HTTP 401，FastF1 底层计时下载失败，因此按降级协议使用 Jolpica 官方结果/最快圈。该降级和代理局限已写入配置与结果。

该产物已经降级为诊断材料：它只有同队中位差与车辆 P50/最快圈代理两个主要对比量，且按赛果事后选站，不能称为算法模型。尤其不再用 VER–PER/TSU 的极端队友差归一化或判断“人主导”。

### 5.7 2023–2025 三赛季鲁棒分层归因 v2

机器可读来源：`research/records/four_team_seasonal_attribution_2023_2025.json`；阅读摘要：`research/records/four_team_seasonal_attribution_2023_2025.md`；配置：`research/configs/four_team_2023_2025_seasonal_v2.json`。运行 ID 为 `four_team_seasonal_attribution_2023_2025_v2-20260718T222427+0800`，`network_calls=[]`。

本地冻结数据覆盖 2023–2025 共 61,351 个清洗后 Race 圈、10 支车队；22/24/24 场计划 Race 中有 69 场进入干地主头，2024 São Paulo 因湿地无干地有效圈排除。所有圈速、实体和单站分类参考均来自本地文件。

参数只看 2024 时间外误差：2023 + 2024 前 5 站拟合，后续 18 个有干地有效圈的分站选参（São Paulo 湿地排除）。最优分站等权 MAE 为 0.8340；`balanced_v1`、`driver_flexible`、`interaction_tight`、`all_tight` 均落在预注册 0.01 实用等价带内，因此按预注册顺序选择 `balanced_v1`，没有用 2025 赛果打破并列。

2025 锁定测试使用 2023–2024 + 2025 前 5 站拟合，验证其余 19 站：

| 模型 | MAE | 分站等权 MAE | 相对 context MAE | 80% 覆盖 | 95% 覆盖 |
| --- | ---: | ---: | ---: | ---: | ---: |
| zero | 1.1481 | 1.1107 | +0.2600 | 81.3% | 95.7% |
| context | 0.8881 | 0.9418 | 0.0000 | 82.1% | 95.1% |
| team_only | 0.8200 | 0.8779 | -0.0681 | 75.9% | 94.3% |
| joint_robust | 0.8105 | 0.8707 | -0.0776 | 76.2% | 94.2% |

区间使用 2024 时间外残差做 split-conformal 校准。95% 覆盖接近目标，80% 覆盖仍偏低，说明分布跨赛季有变化；在 2026 不得只发布窄区间，至少保留 95% 口径并重新校准。

40 次年份内分站簇 bootstrap 的车辆赛季点估计如下；正的配速优势表示相对当年四队均值更快，毫秒为 90 秒标准圈相对最快车：

| 年份 | 模型车辆顺序 | 相对最快车差 | 本地赛季分类代理顺序 | Spearman |
| ---: | --- | --- | --- | ---: |
| 2023 | Red Bull > Mercedes > Ferrari > McLaren | 0 / 252 / 309 / 591 ms | Red Bull > Mercedes > Ferrari > McLaren | 1.00 |
| 2024 | McLaren > Mercedes > Ferrari > Red Bull | 0 / 132 / 134 / 296 ms | McLaren > Ferrari > Red Bull > Mercedes | 0.40 |
| 2025 | McLaren > Red Bull > Mercedes > Ferrari | 0 / 476 / 483 / 485 ms | McLaren > Mercedes > Red Bull > Ferrari | 0.80 |

稳定结论只有 2023 Red Bull 第一/McLaren 第四、2024 Red Bull 第四和 2025 McLaren 第一。2023 Mercedes/Ferrari、2024 领先者及 Mercedes/Ferrari、2025 第 2–4 在合理惩罚或 bootstrap 下区间重叠、部分换序，不能发布为高置信精确排名。69 场同场赛后构念检查中，模型车辆状态与当场四队最佳分类一致 53/69（76.8%），平均 Spearman 为 0.733；`team_event` 使用同场圈速拟合，这不是赛前预测命中率。

Red Bull 队友极端差不再作为归一尺度。2025 原始上下文调整后队友跨度为 1.274 z，模型分配到车手层的能力跨度为 1.132 z；Red Bull 当年 18.2% 圈级观测受到 Huber 降权。模型不输出车辆/车手百分比分摊，只输出有符号 z、90 秒标准圈毫秒、组合项和区间。

2026 迁移合同已经写入结果：只迁移车手基础能力、稳定性/残差尺度、清洗和在线适配协议；`team-year`、`team-event`、赛道响应、人车交互、能量与空气动力状态全部重置。本地 2026 当前只有 4 场 Race，未达到预注册的 5 站适配门槛，因此本次没有提前发布 2026 车辆排名。

### 5.8 2023–2025 分层贝叶斯反事实复验 v3

机器可读来源：`research/records/four_team_bayesian_attribution_2023_2025.json`；阅读摘要：`research/records/four_team_bayesian_attribution_2023_2025.md`；配置：`research/configs/four_team_2023_2025_bayesian_v3.json`。运行 ID 为 `four_team_bayesian_counterfactual_2023_2025_v3-20260718T233835+0800`，`network_calls=[]`。

该运行复用 61,351 个本地 Race 有效圈和 69 场干地比赛，但将训练单位改为 1,324 个 `driver × event` Huber 稳健聚合。上下文头不含车队或车手身份；联合后验分开估计 `team-year / team-circuit / team-event`、`driver / driver-year` 和 `driver-team`。主后验为 4 链、每链 600 个留存样本，层级尺度最大 R-hat=1.014；两组交互先验敏感性最大 R-hat=1.036，删车手影响审计最大 R-hat=1.026。

时间外复验在车手—分站聚合层完成，不能与 v2 的逐圈 MAE 直接横比：

| 目标年 | 角色 | 零模型 MAE | 贝叶斯 MAE | 分站等权 MAE | 原始 80%/95% 覆盖 | 2024 校准后覆盖 |
| ---: | --- | ---: | ---: | ---: | --- | --- |
| 2024 | 调整复验 | 0.7669 | 0.5149 | 0.5252 | 69.5% / 86.2% | 不适用 |
| 2025 | 锁定复验 | 0.7174 | 0.5816 | 0.6032 | 70.9% / 86.8% | 80.2% / 94.0% |

原始贝叶斯区间明显欠覆盖，不能宣称概率模型已经校准；2025 校准只使用 2024 时间外预测的非一致性分数，没有用 2025 调参。

固定零参考车手后的纯车辆后验如下。优势为相对当年四队均值的有符号 z；`P1` 为四队最快后验概率：

| 年份 | 后验车辆顺序 | 领先者优势 95% 区间 | P1 | 本地分类代理 Spearman |
| ---: | --- | --- | ---: | ---: |
| 2023 | Red Bull > Mercedes > Ferrari > McLaren | RB +0.257 `[-0.023, +0.513]` | 0.746 | 1.00 |
| 2024 | Ferrari > McLaren > Mercedes > Red Bull | Ferrari +0.129 `[-0.112, +0.381]` | 0.451 | 0.60 |
| 2025 | McLaren > Mercedes > Ferrari > Red Bull | McLaren +0.337 `[+0.049, +0.634]` | 0.911 | 0.80 |

这不是与 v2 完全复制：共同稳定方向是 2023 Red Bull/McLaren 两端、2024 Red Bull 后段和 2025 McLaren 第一；2024 Ferrari/McLaren 无法分开。2025 v2 把 Red Bull/Mercedes/Ferrari 点估计排为第 2–4，贝叶斯排为 Mercedes/Ferrari/Red Bull；这些中间位在 v2 已被标为非稳定组，不能拿单一顺序声称冲突或一致。

固定为四队等权参考赛车并令 `driver-team=0` 后，2025 纯车手圈速贡献如下：

| 车手 | 优势 z | 95% 后验区间 | P(四队车手池最快) |
| --- | ---: | --- | ---: |
| VER | +0.565 | `[+0.252, +0.858]` | 0.872 |
| NOR | +0.185 | `[-0.153, +0.516]` | 0.056 |
| PIA | +0.149 | `[-0.192, +0.481]` | 0.044 |
| LEC | +0.138 | `[-0.188, +0.439]` | 0.023 |
| RUS | +0.003 | `[-0.317, +0.322]` | 0.005 |
| HAM | -0.033 | `[-0.304, +0.243]` | 0.000 |
| ANT | -0.175 | `[-0.507, +0.155]` | 0.000 |
| LAW | -0.408 | `[-0.760, -0.082]` | 0.000 |
| TSU | -0.424 | `[-0.705, -0.141]` | 0.000 |

车手结果必须与识别审计一起读：`driver-team` 先验标准差由 0.06 放宽到 0.24 时，VER 纯车手优势从 +0.643 降为 +0.418 z。移除 2025 VER 后 Red Bull 纯车辆优势由 -0.247 漂移到 -0.550 z；移除 LAW/TSU 后漂移到 -0.049 z。也就是说，模型不再用极端队友差定义全场尺度，但 Red Bull 车辆/车手拆分仍显著依赖不均衡车手网络；宽区间和敏感性是正式结论，不是待隐藏的瑕疵。

### 5.9 单场—stint—圈级解释与周末性能前沿 v4

机器可读来源：`research/records/four_team_event_stint_attribution_2023_2025.json`；阅读摘要：`research/records/four_team_event_stint_attribution_2023_2025.md`；配置：`research/configs/four_team_2023_2025_event_stint_v4.json`。正式运行 ID 为 `four_team_event_stint_attribution_2023_2025_v4-20260719T103318+0800`，`network_calls=[]`。

该运行没有用 2025 再选择 v3 身份参数，而是复用 v3 的静态分层结构，增加可追溯的描述层：

| 项目 | 已验证规模/结果 |
| --- | ---: |
| 全场 Race 身份输入 | 61,351 圈、1,324 个车手—分站观测、69 场 |
| 四队圈级可加解释 | 25,343 圈 |
| 四队 stint 特征 | 1,298 个 |
| 单场车辆/车手报告 | 69 场；每场均保留四队车辆行 |
| Qualifying accurate 干地推圈冻结 | 全场 7,401 圈、67 场；四队 3,469 圈 |
| 正式身份后验 | 4 链 × 600 留存样本；最大尺度 R-hat=1.024 |
| 车辆前沿不确定性 | 每场 240 次车手内 bootstrap |
| 参数边界 | 每场 27 组 Race/Qualifying 分位数与会话权重组合 |

每个圈保存 `context + car + driver + interaction + residual` 的可加解释、模型期望圈时、实际毫秒残差、车手—分站内残差分位数和相对条件前沿的执行损失。每个 stint 保存实跑/期望 P50、执行损失、MAD 稳定性以及控制全场上下文后的超额衰退斜率。圈级文件 `research/artifacts/data/four_team_lap_explanations_2023_2025_v4.csv.gz` 共 25,343 行，SHA-256 为 `92b04b3744cfadb23c2c16a3b0d090a0d8d87481f2ec824688f473d6a1e4db66`；其机器身份见 `research/artifacts/four_team_lap_explanations_2023_2025_v4_manifest.json`。

周末车辆上沿只融合 Qualifying/Race 各自 session 内的相对优势，主权重为 0.65/0.35；主口径使用车手等权双车中位前沿，最佳单车手前沿单列为理论上沿代理。27 组参数网格中，276 个“分站 × 四队”车辆名次只有 102 个（37.0%）在全部参数下固定；因此输出正式保留 `rank_min/rank_max/p_rank_1`，不能只展示默认参数下的一条精确全序。

本地分类代理仅作同场结果相容性检查：276 个车辆—分站中 231 个（83.7%）的周末上沿排名与四队分类代理相差不超过 1 位；535 个车手—分站中 448 个（83.7%）的干净圈配速排名与分类代理相差不超过 1 位。这是回顾性同场解释，不是赛前命中率；其余偏差只标为“成绩优于/低于干净圈配速”，尚未归因给策略、交通、SC/VSC、可靠性、事故或罚则。

排位冻结 manifest 为 `research/artifacts/four_team_qualifying_2023_2025_manifest.json`。公开数据没有 setup、升级规格、损伤和动力模式字段，因此本实现只声明 parc fermé 周末家族代理，不声称 Qualifying 与 Race 是可验证的“完全同调教”。

### 5.10 全量逐站标准化、stint 方法验证与 2026 探索更新 v5

机器真相源：`research/records/four_team_race_by_race_2023_2026_v5.json`；总摘要：`research/records/four_team_race_by_race_2023_2026_v5.md`；逐年报告：`research/records/four_team_race_by_race_{2023,2024,2025,2026}_v5.md`；配置：`research/configs/four_team_race_by_race_v5.json`。正式运行 ID 为 `four_team_race_by_race_2023_2026_v5-20260719T130139+0800`，`network_calls=[]`。

| 项目 | 已验证结果 |
| --- | ---: |
| 2023–2025 计划/可用分站 | 70 / 69；2024 São Paulo 显式缺失 |
| 历史四队模型圈 | 25,343 |
| 遥测逐圈键匹配 | 25,343 / 25,343（100%） |
| 平均速度质量通过 | 25,323 / 25,343（99.92%） |
| Stint 总数 | 1,298 |
| 可做早段→晚段留出 | 1,163；晚段 MAE 中位数 610.0 ms |
| 可做奇偶圈复验 | 1,163；奇偶圈差中位数 93.6 ms |
| 斜率符号 bootstrap 稳定 | 71.45% 的被评估 stint |

逐圈平均速度按 `3.6 × (max(distance)-min(distance)) / lap_duration` 计算，采样速度均值只作质量审计。遥测缓存 33,609 行，覆盖 2023–2026 共 74 个本地 Race 分区；SHA-256 为 `70dbda870e3f9fa544a807ee8de5d335a242534275aece968368a73d6575418b`，身份见 `research/artifacts/four_team_telemetry_lap_metrics_2023_2026_v5_manifest.json`。

v5 对所有可用分站同时给出本场模型周末上沿、本场四队最快清洁圈、本场四队分类，以及全场冠军/最快圈代理。69 站回顾性构念检查中，模型第一与本场最快圈第一一致率为 53.62%，与分类第一一致率为 62.32%；平均秩相关分别为 0.351 和 0.510。这些数值使用本场数据，不能称为预测准确率。

另一个只使用先前分站状态的指数滚动基线验证了 64 站：最快圈第一命中率 42.19%，分类第一命中率 46.88%，平均秩相关分别为 0.187 和 0.272。该透明基线与既有 v2/v3 预注册时间前推指标分开保存，且在当前站观测更新之前产生预测。

赛季汇总改用分站等权平均。默认周末上沿平均名次的点估计顺序为：2023 Red Bull Racing（2.14）领先；2024 McLaren（1.96）领先；2025 McLaren（1.75）领先。它们仍需与每站参数网格、前沿区间和最快圈/分类差异一起读，不能把全年点估计当车辆物理真值。

2026 本地 Race/Qualifying 均只有 Australian、Chinese、Japanese、Miami 四站。按规则重置合同，车手后验可迁移，新车手使用宽零先验；车辆与人车交互归零，使用 5 个先验等效分站强收缩。四站收缩点估计为 Mercedes、Ferrari、McLaren、Red Bull Racing，但 2025 锁定时间前推校准得到的 80% 预测带平均宽度为 1.741 z，四队预测带全部重叠；且 4 站低于 5 站确认门槛，因此机器状态为 `exploratory_only`，不能发布确定性 2026 排名。

已生成六张图：2023/2024/2025 逐站平均速度相对值与模型残差稳定性、同场/时间前推分离验证、stint 方法验证、2026 重置更新。路径统一位于 `research/artifacts/figures/race_by_race_v5/`。

### 5.11 可解释深度层 v6

机器真相源：`research/records/four_team_explanatory_depth_2023_2025_v6.json`；总摘要及逐年报告：`research/records/four_team_explanatory_depth_2023_2025_v6.md`、`research/records/four_team_explanatory_depth_{2023,2024,2025}_v6.md`；配置：`research/configs/four_team_explanatory_depth_v6.json`。正式运行 ID 为 `four_team_explanatory_depth_2023_2025_v6-20260720T131209+0800`，`network_calls=[]`。

| 验收项 | 已验证结果 |
| --- | ---: |
| traffic 可用圈 | 58,089 / 61,351（94.683%）；缺失单独留痕，不判为 dirty-air |
| 2024 时间外上下文 MAE | 0.7474 → 0.7279；仅保留 `traffic_dirty_air` |
| 2025 锁定上下文 MAE | 0.7185 → 0.6765；未参与选特征 |
| driver-event 重建 | 535 条；R²=0.5617；MAE=274.7 ms |
| 配速模型构念 | 模型组合序 vs 观测干净配速序 Spearman=0.8210；排名差≥2 共 106 例 |
| 赛果代理构念 | 观测干净配速序 vs v5 classification proxy Spearman=0.7498；排名差≥2 共 158 例 |
| 后验诊断 | 4 链 × 600；最大尺度 R-hat=1.020 |
| 逐站报告 | 69 场；2023/2024/2025 三份同构 v6 报告 |

Layer B 不再只看后验置信度：分离等级取队间/队内方差比等级与成对排序置信度等级的较低者，并要求每个在场车队至少 12 个干净圈。Hungarian 为 0.685/0.922、Qatar 为 0.745/0.896，均为 high；United States 为 0.069/0.645、Las Vegas 为 0.052/0.666，均为 low。2024 Canada、2025 Australia/Britain 因至少一队样本不足而强制 low。所有成对输出已校正方向，机器审计未发现 `p_faster_than_slower < 0.5` 或快慢同队记录。

Layer C 明确拆成两张表：106 例“模型组合序≠观测干净配速序”只评价配速模型，不再称为名次异常；158 例“观测干净配速序≠classification proxy”才是赛果代理差异。classification proxy 来自冻结 v5 产物，只作回顾性构念参照，不进入训练、上下文特征选择或后验抽样，也不等同于 FIA 最终分类。

Layer D 使用最快 10% 圈尾部离散度构造 half-normal 形状执行损失校正，并以 0.30 z 硬封顶；前沿与校正分别按车手内 bootstrap，最终 CI 已包含两部分不确定性。Q/R 只融合 session 内相对优势。2025 点估计中 McLaren 为 14/24 站理论上限第一，Canada 为 Mercedes、Azerbaijan 为 Red Bull Racing；不少分站 CI 较宽或跨零，不能把点估计第一解释为确定排名或物理极限。

### 5.12 v7 公开遥测解释、同队双车与评分门控

数据层已把 2026 四场 Race 从原始 commit `81b90e61051a` 转为 expanded v5：4 个分区、3,013,786 行、243,938,416 字节；2026-07-23 重跑后全部 3,013,786 行状态/绝对时间语义验收 PASS、0 errors。v5 保留 raw 状态与绝对时间来源，但年度审计确认 `drs_raw` 仍只有 0，来源均为上游 `tel.drs`，因此不能恢复上游已丢失的新规状态。

客观性机器真相源为 `research/records/objective_rating_review_2023_2026_v1.json`：

| 门控 | 结果 |
| --- | ---: |
| 制造商结果构念，每年最小 Spearman | 0.8545（阈值 0.80） |
| 车+手实跑结果构念，每年最小 Spearman | 0.8670（阈值 0.80） |
| pooled 制造商 / 实跑车手 Spearman | 0.8923 / 0.9036 |
| 最大尺度 R-hat | 1.0144（阈值 1.05） |
| 2025 锁定 MAE 相对改进 | 17.79%（阈值 10%） |
| 校准 95% 区间覆盖 | 93.96% |
| rolling-origin 数 | 44 |
| 2026 事件暴露 | 4/8，FAIL，只允许探索 |
| PAC 独立验证 | FAIL：可识别 novel edge 仅 3/5，基线/块 bootstrap/负对照/冻结映射与 post-freeze 窗口未闭合 |

历史结果记账核心全部通过，但它验证的是联合圈速/结果记账，不足以单独支持 PAC。旧 `pooled_driver_base_z` 因 `driver`、`driver_year`、`driver_team` 身份列共线及跨分量不可识别，已退出发布路径。新 `conditional_pair_laplacian_v1` 只验证 held-out 同队 pair-gap 的预测分布；其分量条件化 latent 区间不作真实 latent coverage 声称。`research/records/driver_rating_cards_2023_2025_v1.json` 仍覆盖 28 名唯一车手，但 PAC 对 28 人全部保持 `null`。TYM/EXT/ATK/DEF/AWA/STR/ADP、Event Delivery 与 OVR 也均为 `null`；当前没有发布任何综合能力分。

2026 Ferrari 四站机器真相源为 `research/records/telemetry_explanation_2026_ferrari_four_event_v7.json`，正方向统一为 `HAM - LEC`：

| 分站 | 可比圈 | 匹配状态 | 中位物理差 s/可比圈 | 异常触发 | HAM / LEC 主导中性模式 |
| --- | ---: | --- | ---: | ---: | --- |
| Australia | 20 | comparable | -0.044 | 0 | high_response / save_like |
| China | 16 | comparable | -0.049 | 0 | early_gain / early_gain |
| Japan | 15 | comparable | +0.380 | 0 | insufficient / insufficient |
| Miami | 16 | comparable_with_balance_warning | +0.251 | 0 | early_gain / early_gain |

迈阿密胎龄平均绝对差达到卡钳的 81.25%，因此所有赛段置信降为 low，且异常发布门控关闭；严格质量合格事件只有 3/8。其余三场匹配平衡通过。四场扫描没有任何信号通过“匹配平衡 + 三基线 + 直接时间/运动学两族 + 输入调整响应通道 + BH 筛查 + 连续三次”完整门控；结论是**未发现足够证据确认持续单车响应异常**，不是“确认车辆正常”。四场各自 80% 物理圈差区间全部跨零，因此跨站方向结论与汇总点估计保持 `null`；不再用四个点估计的中位数制造 HAM/LEC 长期方向。

四份单站 JSON 已导出到 `frontend/public/data/telemetry-workbench/`。工作台可显示逐段直接差、输入调整响应残差、异常审计、中性模式、Episode 机会、全空值能力评分和禁止声称；前端 build/test 已通过。

### 5.13 Race Dossier v8–v15 真实数据结果（2026-07-25）

当前正式主产物：

- `research/records/race_dossier_v15/run=20260725T163219+0800-58bdc41b029f/manifest.json`
- manifest SHA-256：
  `9ed8d286bc72e0947107b63b79ba588bbb0e445ded0dd54861ba0071f7efa517`
- 通用追加式审计 145/145 项身份检查通过，配置快照与重算覆盖一致，SHA/合同
  错误均为 0；v15 专项审计的 5/5 实现身份检查也通过；
- 70 份逐场 Dossier、3 份赛季审计、77,720 圈聚合输入；
- 70/70 事件账本闭合；
- 77,606 圈进入物理可读候选层，占 99.8533%；OOF 可靠性权重质量
  58,380.11（候选层的 75.2263%），Kish 有效样本量 65,437.76；
- v14 严格集合的 20,562 圈只作独立确认，占候选层 26.4954%；
- 3,113/3,946 个全场 Stint 通过包容性自身门，2,688 个累计合理基线秒差
  可识别；543 个 Stint 同时通过严格确认；
- 严格确认层中 289 个曲线通过较晚段确认，208 个确认段无增益，46 个确认圈
  不足；3,403 个未通过严格确认的 Stint 不运行曲线候选赛；
- 66 场形成可用历史进站周期代理，共 1,625 个有效 pit-in/pit-out 周期；
- 主要因素声明数为 0，非空 PAC/OVR 数为 0。

当前发布分布为 60 场完整 Dossier、3 场部分车队模块、7 场纯审计；单队模块
通过场数为 Red Bull Racing 70、Mercedes 70、McLaren 69、Ferrari 67。部分
模块通过只允许对应车队范围内的条件结论，不等于完整四队车辆全序。

“全季遥测已下载”和“严格可确认”现在明确分层：v15 不再让 20,562 个严格圈
决定主模型，而是让 77,606 个物理可读圈共同训练；非绿旗、交通、进出站和
质量异常只要物理可读就保留并连续降权。103 圈有无效/缺失圈时，74 圈距离
严重不完整，原因可重叠，实际硬排除共 114 圈。软权重是观测可靠性质量，
不是事件概率、真实燃油量或“清洗后剩余圈数”。

63 场通过交叉拟合模型门；OOF MAE 改善的跨场中位数为 0.3606 秒，模型 OOF
MAE 中位数 0.8454 秒，对照基线中位数 1.2285 秒。4 场因预测门失败：2023
Australia、2023 São Paulo、2024 Japan、2024 Monaco；3 场因调整饱和度门
失败：2023 Netherlands、2023 Mexico City、2024 São Paulo。7 场均完整保留
在审计中，发布为 `audit_only`，且不进入之后分站的策略历史池。

公开燃油算法没有被忽略：当前线性剩余质量敏感性低/中/高包络在全季给出
0–2.9615 秒的基准修正范围；赛道演化和交通代理仍必须分别通过留出门。真实
起步油量、逐圈耗油、能量回收、胎温胎压与磨损没有公开标签，因此结果明确
为敏感性代理，不称实测燃油修正或车队内部模拟。

失败演进按追加式证据保留：

- v8 `run=20260725T003934+0800-476316f80d23`：70/70 audit-only；
  Stint 门错误依赖跨车队基线，0 个有效 Stint。
- v9 `run=20260725T103758+0800-6b91c3a67cab`：数值门槛不变，
  拆开 Stint 与累计基线识别，得到 475 个有效 Stint；整场仍 0/70。
- v10 `run=20260725T104301+0800-774be200a686`：继续保持门槛，
  允许账本闭合场次中的单个有效 Stint 进入严格历史池，赛前包络恢复。
- v11：加入线性燃油敏感性、跨车手留出赛道演化、gap-band 交通代理和
  全场/单队/同队车手对模块门；不降低 v10 数值门槛。
- v12：有效 Stint 上比较常数、线性、对数、二次和局部线性 Kalman 曲线。
- v13：改成较早段选模、较晚段独立确认，并加入完整进站周期损失代理。
- v14：只跳过 3,403 个已知物理门失败 Stint 的昂贵曲线赛；v13 的科学字段
  与 543 个有效 Stint 结果保持一致，历史产物未覆盖。
- v15：主层改为全量物理可读圈、确定性两折 OOF 稳健条件模型和非零软权重；
  v14 严格层只作独立确认。历史 v1/v2 与 v8–v14 均未覆盖或美化。

工作台 `telemetry-workbench-v2` 现由 v15 manifest 明确列举 70 份报告，并
保留 4 份 2026 Ferrari v7 报告，共 74 份。漏斗分开显示观测圈、物理可读
候选、OOF 有效权重质量、严格确认圈、有效 Stint 和严格确认 Stint；四队只
定义主报告对象，不定义训练样本。audit-only 车队顺序仍只作描述展示，
PAC/OVR=null。

2026-07-25 的可见内容修订不再复用旧遥测卡片作为 Race Dossier 主界面：顶部
直接给出排位潜力、正赛同配方/近胎龄/近阶段条件配速、80% 排名区间及
`classification_proxy` 兑现差异；燃油、轮胎、损伤、ERS/SOC 分成独立状态卡；
全场每个 Stint 可按车手筛选并显示初速、稳态、MAD、燃油敏感性衰减区间、
末段掉速、累计合理基线秒差、严格复核和曲线状态；事件账本显示具体圈段及
主模型/严格层处理动作；车辆共同基线、当场兑现、策略位置和残差按秒展示。
轮胎区同时列本场观测包络、只用此前分站的赛前包络、停站窗口和可识别成本
分量，并明确这些分量不是策略总时间排名。`npm run build` 与 `npm test` 已在
512MB Node 堆限制下通过；后者使用构建后 JS 真实渲染阿布扎比 v15 报告，
断言 BOR Stint、燃油场景、轮胎窗口、事件动作、秒数分解和成绩差异均非空。

资源边界：v15 从 3.4MB 圈宇宙前台串行运行；NumPy/BLAS 与 DuckDB 均为
单线程、单文件批次、768MB DuckDB 上限，Node 构建限制 512MB。项目数据与
缓存没有写入 C 盘。项目低资源入口用直接 Win32 API 调低优先级，不调用 WMI；
固定入口静态审计已通过。本机 WMI 高频枚举的外部客户端二进制仍待系统侧
识别，不能把项目侧零调用误报为系统问题已经解决。

## 6. 测试与验收快照

截至本文快照，仓库记录的已通过项：

| 验收 | 结果 | 覆盖 |
| --- | --- | --- |
| Python 标准库测试 | 已通过 | 项目合同、Catalunya 导出、存储清理、TracingInsights catalog、v4 冻结 |
| 四队归因定向测试 | 4/4 通过 | 分解可加性、效应中心化、车手提取定义、均衡选站和外部排序指标 |
| 六站压力测试定向测试 | 3/3 通过 | 官方圈时解析、领奖台去重顺序、车辆包络排序与毫秒换算 |
| 分层贝叶斯归因定向测试 | 4/4 通过 | 全圈 Huber 聚合、合成车辆/车手方向恢复、未见层级零均值预测、R-hat 计算 |
| 单场/stint v4 定向测试 | 4/4 通过 | 参数边界、车手等权前沿 bootstrap、stint 执行/超额衰退和名次相容分档 |
| 全量逐站 v5 定向测试 | 4/4 通过 | 描述/预测边界、共同实体秩相关、stint 晚段真留出与 bootstrap、当前站更新前滚动预测 |
| 可解释深度 v6 定向测试 | 9/9 通过 | 配置守卫、traffic 门控、配速/赛果构念隔离、half-normal 封顶与 CI、成对方向、双证据和样本门控 |
| Race Dossier/工作台定向合同 | 已通过（2026-07-25） | v8–v16 配置冻结、包容性反例、OOF 稳定性、D 盘路径、零 WMI 入口审计、v15 manifest、v16 pilot、工作台级联导航与双 Stint 排序 |
| Python `pytest tests -q` | 219/219 通过（2026-07-25） | 单线程；覆盖既有 v4–v7、PAC v1/v2、Race Dossier v8–v16、工作台可见内容与导出合同 |
| Go `go test ./...` | 已通过 | 编译、API helper、时间解析、DeepSeek 重试、迁移发现、exporter 合同 |
| 前端 `npm run build` | 已通过 | 静态 SPA 构建 |
| 前端 `npm test` | 已通过 | manifest、关键 session 分片及阿布扎比 v15 构建后 JS 最小 DOM/canvas 渲染合同 |
| 浏览器 smoke | 真实 Chrome 交互通过（2026-07-25） | 产品/赛季/分站/版本级联、v15/v16 切换、Stint 车队/车手/视图联动、事件账本分类；页面无脚本异常 |
| FastF1 实测 | 已记录通过 | 2025 Belgian GP Race，879 圈；需绕过代理 |

这些是最近记录的验收快照，不代表每次环境都自动持续通过。Agent 修改实现后必须重新运行相关测试，再更新本节日期或结论。

## 7. 尚不能宣称的能力

- 不能宣称公网常驻 API、在线数据库查询或 Cloudflare Workers 已交付。
- 不能宣称项目内已有音频转文字；只有录音 URL 时不等于有 TR 文本。
- 不能宣称真实 PostgreSQL ingest → translator → exporter 全链路已在当前环境持续验收。
- 不能把圈速趋势称为纯轮胎物理衰退。
- 不能发布确定性“车手真值”“车辆真值”或 Sprint 评分。
- 阶段 3 旧 PG 结果尚未在 v4 规范层完整复跑。
- 不能把匈牙利单站探索结果外推为 2025 全赛季车辆排名；车手提取外部方向只匹配 2/4，尚不具备发布条件。
- 不能把六站事后反例压力测试的 4/6 当作泛化准确率；它只证明模型在日本和意大利没有机械复述冠军，并提供可审计的分层解释。
- 不能把三赛季 v2 的 53/69 同场构念匹配当作赛前准确率；2024/2025 中间集团在先验敏感性下会换序，当前只发布区间和稳定组别。
- 不能把 v3 条件反事实称为因果真值；长期单队车手的 `driver` 与 `driver-team` 部分共线，2025 原始贝叶斯 95% 时间外覆盖只有 86.8%。
- 不能把 v4 周末统计上沿称为车辆物理极限或可验证的完全同调教结果；其 27 组网格只有 37.0% 的单场车辆名次完全固定，且分类代理一致性是同场描述，不是赛前预测。
- 不能把本地分类代理的积分数称为 FIA 官方积分；赛后 DSQ、罚时和最终修订未进入代理。
- 不能把 v6 的 106 例配速模型偏差称为“名次≠配速”；真正的 proxy 赛果差异是另一组 158 例，两者不能合并。
- 不能从 158 例配速—classification proxy 差异直接判断策略、可靠性、事故、交通或处罚原因；当前只完成异常定位，没有完成 `V_result` 因果归因。
- 不能把 v6 理论上限点估计第一当确定排名；half-normal 形状校正只处理最快圈执行缺口，不会消除 Qualifying 稀疏样本与 setup 不可观测造成的宽区间。
## 2026-07-23：PAC 车手级协议第二轮

首次成功运行的协议哈希为
`0f606deef5761f69e2308f03d4eae6d87ed4bfe88d082137469178986d7dea85`。
回顾性窗口含 60 个比赛集群、10 队、512 个完成 OOS 校准的 pair-event。
其中 repeated-pair 为 497，novel edge 仅 15；只有 3 个可由其他历史队友路径识别，
12 个为 `not_identifiable`，未达到预注册的 5 个可识别新边门槛。

开发性指标为：pair balanced accuracy `0.6639`，pair-margin Spearman `0.4299`，
Brier `0.2129`（零差基线 `0.25`），log score `0.6111`，pair-gap MAE 相对零差改善
`12.30%`，80%/95% pair-gap predictive coverage 为 `0.7617/0.9395`。这些数字只支持
“重复同队组合具有一定条件预测信号”，不能证明跨队友迁移或全局 PAC。

发布门控明确失败：比赛块 bootstrap、最佳基线 Brier/log/WIS、完整基线与负对照尚未
测试；可识别 novel edge 不足；全期 `team-year` 图仍有 5 个分量；冻结 0–100 映射
未验证；冻结后确认比赛集群/pair-event 均为 0。最终状态为
`conditional_pair_prototype_keep_null`，PAC 未通过且没有写回旧车手卡。

## 2026-07-23：PAC v2 方法审计

v2 协议哈希为
`ce5247472afc6dfce4380a900eb64fc16818e6e02dcb6e45c5616abff447f624`，
真实冻结时间为 `2026-07-23T20:47:01.5319120+08:00`。四个 2026 报告产物均早于
该时间并登记为开发数据；post-freeze 确认性事件/pair 数为 `0/0`。

真实进入 v2 的分母为：60 attempted origins、22 eligible/scored origins、
540 candidate pairs、87 eligible/scored pairs、22 个计分比赛集群、5 队；
453 个 pair 因实际 incidence 图或删边后断连而不可识别。transfer 子集含 87 个
pair-event，但只覆盖 22 个事件和 5 条唯一 canonical edge，不能按重复行数宣称
跨队友验证充分。发布图有 5 个分量。

开发性指标为 balanced accuracy `0.5991`、Brier `0.2123`、log score `0.5993`、
pair-margin Spearman `0.5081`；滚动经验区间 80%/95% 覆盖仅
`0.3678/0.6207`。严格 split conformal、full-pipeline purge、有效电阻、
事件等权 bootstrap CI 与冻结基线 paired LCB 均为 `NOT_TESTED`。v2 明确失败，
`PAC=null`、`OVR=null`。

最终验证：`python -m pytest tests -q` 为 `144 passed`。

## 2026-07-25：Race Dossier v16 单一 Stint 代表配速验收

本轮修正了工作台把一个 Stint 拆成“初速 / 稳态 / MAD / 末段”多个主圈速的问题。v16 现在每个车手 Stint 只发布一个燃油与可观测条件修正后的胎龄代表配速，另列一个 `s/胎龄圈` 斜率；旧字段仅保留为兼容审计证据。

合成最小反例：

- 设真实胎龄掉速为 `+0.10 s/胎龄圈`；
- 叠加燃油递减后，原始圈速斜率错误变为 `-0.20 s/胎龄圈`；
- v16 修正后恢复 `+0.10 s/胎龄圈`；
- 同一车手两个 Stint 的真实基准差设为 `0.5s`，两个单一代表配速准确保留 `0.5s` 差值；
- 参考胎龄始终位于 Stint 样本支持内，不进行外推。

2025 Abu Dhabi GP、BOR 单场真实数据 pilot：

| Stint | 配方 | 圈段 | 参考胎龄 | 单一代表配速 | 燃油情景区间 | 修正后胎龄斜率 | 原始净斜率 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | MEDIUM | L1–15 | 8 | 87.056s | 85.849–88.091s | +0.0642 | -0.0143 |
| 2 | HARD | L16–58 | 22 | 88.191s | 87.684–88.625s | +0.0440 | -0.0197 |

两条代表配速因配方和参考胎龄不同，不能彼此直接判断快慢。pilot 覆盖 20 名车手、1,156 个观测车手圈；包容性主模型状态为 `accepted_crossfit_proxy`。BOR 两个 Stint 均满足：每个 Stint 一行、代表配速非空、参考胎龄位于支持区间、胎龄斜率非空、燃油敏感性区间非空。`PAC=null`、`OVR=null` 未改变。

验收命令与结果：

- `pytest tests/test_race_dossier.py tests/test_race_dossier_run.py tests/test_telemetry_workbench_export.py -q`：56/56 通过；
- 最终 `pytest tests -q`：205/205 通过，耗时 59.55 秒；
- `npm run build`：通过；
- `npm test`：通过，构建后最小 DOM/canvas 渲染实际断言 `BOR`、`87.056s`、`@ 胎龄 8.0 圈` 和燃油条件代理文本；
- v16 静态运行审计：D 盘项目路径、前台串行、五类数值线程上限为 1、无 WMI/CIM 查询、无后台 `Start-Process`。

当前发布范围仍是单场 pilot。虽然本轮全量 `pytest tests -q` 已通过，但尚未运行 70 场 v16；因此不得把 pilot 称为全量新版本结论。

工作台展示层随后完成两项不改变模型数值的增量验收：

- 比赛选择器按年份、事件日期/轮次从新到旧排列；2023–2025 从追加式报告
  `source_path` 的 `round=` 读取轮次，同场 v16 从 v15 继承 R24。四份 2026 v7
  旧报告没有正式 round，只按冻结四站配置的相对事件序排列并显示“四站序”；
- Stint 先按软胎、中性胎、硬胎、半雨胎、全雨胎分组，各组按当前实际展示的
  单一代表配速由快到慢排列；无代表配速的旧版行以明确标注的
  `stable_pace_s` 回退排序，缺失配速排在组末；
- 前端最小反例覆盖跨年份、R24/R01、同场 v16/v15、2026 四站相对顺序，
  以及 SOFT/MEDIUM/HARD 分组、低圈时优先和缺失值末置；真实 v16 报告渲染
  同时断言三种干胎标题与“代表配速由快到慢”提示。
- 低资源串行复验：`npm run build`、`npm test` 均通过；随后
  `python -m pytest tests -q` 为 205/205 通过，耗时 67.76 秒。临时目录和
  缓存位于项目 `.runtime-cache`，数值线程上限为 1。

上述仅改善浏览顺序。不同参考胎龄或不同赛道条件的 Stint 即使在同一配方组内，
仍不能仅凭表格先后顺序形成直接性能结论。

## 2026-07-25：Race Dossier 工作台导航与阅读顺序验收

本轮只调整展示、筛选和稳定排序，不修改 Race Dossier 模型、发布门或任何报告
数值。`telemetry-workbench-v2` 的 75 份条目现按四级级联导航浏览：

1. 产品：`Race Dossier` 与 2026 探索性遥测档案分流，默认进入主产品；
2. 赛季：在当前产品内按年份新到旧；
3. 分站：同一比赛只出现一次，按正式轮次新到旧；v16 继续从完整 manifest
   的同场 v15 继承 R24，2026 v7 继续明确显示冻结“四站序”；
4. 版本：同场 v16/v15 收束为版本选择，v16 标明“单场试点，非全量发布”，
   v15 标明“70 场正式发布版”。

筛选先基于完整 manifest 建立轮次上下文，再逐级收窄，避免单独筛出 v16 后丢失
R24。快速连续切换增加请求序号保护，较早请求不能覆盖较晚选择。

比赛内阅读顺序固定为“整场结论 → 比赛事件 → Stint → 条件配速 → 秒数拆分 →
轮胎策略 → 数据审计”。事件账本默认聚焦四队主报告对象和比赛因素，将交通、
进出站、未解释残差与计时质量分组；完整账本、仅质量和全场车队仍可切换，圈次
合计继续标成可重叠暴露。Stint 默认聚焦四队并按车队—车手—Stint 比赛进程
排列；另保留“同配方分组、代表配速快到慢”的比较视图，以及全场、单队和单车手
级联筛选。轮胎包络统一使用软/中/硬/半雨/全雨顺序，停站候选只按类型与首个窗口
稳定排列，不冒充总比赛时间推荐。

验收证据：

- `npm run build`：通过；
- `npm test`：通过，覆盖四级筛选、R24 继承、同场版本折叠、主报告/全场 Stint、
  双排序、事件分类和页面 DOM 阅读顺序；
- `python -m pytest tests/test_telemetry_workbench_export.py -q`：7/7 通过；
- `python -m pytest tests -q`：219/219 通过，51.23 秒；
- 真实 Chrome 交互 smoke：产品切换、v15/v16、Ferrari→LEC、Stint 双视图和
  事件质量筛选均通过，页面脚本异常为 0；
- 桌面与 390px 移动视口截图保存在项目
  `.runtime-cache/ui-smoke/race-dossier-navigation-playwright-*.png`。

限制：前端 manifest 仍缺少显式 `round`、`report_version`、`is_pilot` 和
`default_for_event`；当前路径/同场继承逻辑已有反例测试守卫，但后续应由 exporter
直接发布这些排序元数据，移除前端对 `id` 与 `source_path` 的推断。

## 2026-07-25：Stint 真实可比窗口与燃油外部一致性审计

工作台原“同配方快到慢”视图仍会把参考胎龄和比赛阶段明显不同的 Stint 放在
同一张排序表，置顶整个配方的最小点估计会制造隐性的直接比较。本轮把该视图改成
冻结门驱动的不重叠窗口：

- 只接纳 `valid` 且具有 v16 合格代表配速状态、有限参考胎龄和比赛阶段的 Stint；
- 先按配方隔离，再按阶段中点、参考胎龄、代表配速稳定 first-fit；
- 新成员加入后，窗口内最大—最小参考胎龄必须不超过 2 圈，阶段中点比例跨度
  必须不超过 0.20；因此窗口内任意两行都满足冻结门；
- 每个 Stint 只出现一次；窗口内点估计最小者作为“显示值基准”常驻表格顶部，
  多行窗口才显示相对基准差值；单行窗口明确标成没有直接比较对象；
- 无效、旧版回退或缺失代表胎龄配速的行只进入审计区，不生成比较基准。

2025 Abu Dhabi GP v16 主四队的 19 个 Stint 被拆成 11 个窗口：
SOFT/MEDIUM/HARD 分别为 1/4/6 个，其中 4 个窗口含直接对照，7 个为单行窗口。
例如 MEDIUM 首个窗口的置顶基准是 LEC #1（86.664s，参考胎龄 8 圈），NOR #1
和 RUS #1 分别显示相对它的窗口内差值；HARD 全局最小显示值 NOR #3 因找不到
同时满足胎龄和阶段门的对象，只能作为单行显示，不能冒充全硬胎比较基准。

验收：

- `npm run build`、`npm test` 通过；
- `python -m pytest tests/test_telemetry_workbench_export.py -q`：7/7 通过；
- 最终 `python -m pytest tests -q`：219/219 通过，耗时 63.91 秒；
- 前端反例覆盖胎龄差 2.00/2.01、阶段差 0.200/0.201、跨配方、无效状态、
  旧版回退、窗口 max-min、不重叠和窗口内最小点估计；
- 真实报告断言 1/4/6 个窗口及 11 个锚点；
- Chrome 桌面、390px 移动端和 200% 缩放 smoke 通过；纵向及横向滚动后当前
  窗口基准仍可见，截图为
  `.runtime-cache/ui-smoke/stint-comparison-baseline-{desktop,mobile}.png`。

燃油代理随后按统一量纲与三个公开分析口径对照。项目冻结场景仍为
`90kg×0.02s/kg`、`100kg×0.03s/kg`、`110kg×0.04s/kg`，低/中/高是成对情景，
不是真实油量区间。以 Abu Dhabi 的观测最大圈号 `N=58` 换算，燃油变轻的逐圈
效应为 0.031034/0.051724/0.075862 s/圈，L1 扣除值为
1.768966/2.948276/4.324138s，L58 为 0。

- [TracingInsights FAQ](https://tracinginsights.com/faqs/) 使用 100kg、
  0.03s/kg 和同样的线性剩余燃油公式，和项目 base 情景在公式及参数上完全一致；
  来源自身也明确称其为近似，实际车队油量和消耗会变化。
- [f1metrics 的 2017 Barcelona 长距离分析](https://f1metrics.wordpress.com/2017/03/14/2017-preseason-analysis/)
  从已知 race simulation 的同配方、不同油量 Stint 估出 0.054s/每圈燃油。
  项目 Abu Dhabi base 为 0.051724s/圈，低 0.002276s/圈，即低 4.21%；
  low/high 分别低 42.53% 和高 40.49%。
- [f1pace 的 2025 Hungarian GP 分析](https://f1pace.com/p/2025-hungarian-gp-what-really-happened-to-charles-leclerc-the-story-the-raw-lap-times-don-t-tell/)
  采用 0.03s/圈的直线修正并明确称为行业近似。项目 Abu Dhabi low 为
  0.031034s/圈，只高 0.001034s/圈（3.45%）；base 则高 72.41%。

这些来源的年份、赛道、技术规则和量纲不同，不能平均后回调冻结参数。当前可支持的
结论只是：公开口径大致落在项目 low–base 之间，base 与 TracingInsights 完全同式，
也与 f1metrics 的 Barcelona 长距离估计接近；high 应继续解释为保守压力情景，
不是外部实测上置信界。真实起步油量、逐车耗油、SC/VSC、lift-and-coast、余油及
赛道特异质量敏感性仍不可识别。

另发现配置名 `linear_by_scheduled_race_fraction` 与实现口径并不完全一致：
实现分母取圈宇宙最大 `lap_number`，不是独立赛历计划圈数。既有 v15/v16 记录和
哈希保持不改；若要修名或改用计划圈数，必须新建冻结版本并覆盖缩短比赛、红旗和
异常圈宇宙反例。

## 2026-07-26：同配方跨车队衰减 / 配速双曲线

Race Dossier 的 Stint 区现已增加独立曲线浏览器。用户先选轮胎配方，再选该配方的
跨车队真实可比窗口；界面并排显示：

1. 在共同评价胎龄归零的相对衰减曲线；
2. 同一共同胎龄下的 Stint 条件配速曲线。

两图复用既有“同配方、参考胎龄跨度不超过 2 圈、比赛阶段跨度不超过 0.20”的
窗口，并额外要求至少两个不同车队、斜率可识别及所有 Stint 胎龄支持区间交集非空。
绘图域只取支持区间交集，禁止外推。颜色由 Formula 1 官方赛季车队目录解析，
同队双车共用官方色并用不同线型区分；图例保留车队、车手、Stint、斜率、共同
胎龄配速和各自支持区间。

每个窗口固定显示四张数值卡：衰减点估计最慢/最快、共同胎龄条件配速点估计
最快/最慢。衰减燃油区间重叠时直接显示“排序未确认”；关于物理磨损、车手保胎
能力和赛车绝对排序的稳定边界由中央帮助提示按需显示，不再在每张卡和每份版本
报告中重复。

2025 Abu Dhabi v16 主四队共有 4 个跨队窗口：MEDIUM 与 HARD 各 2 个，SOFT
没有跨队窗口。默认选择成员最多的 MEDIUM 后段窗口：

- 3 个车队、4 辆车，成员为 LEC #3、HAM #3、ANT #2、TSU #2；
- 共同胎龄支持为 4–22 圈，共同评价胎龄为 13.5 圈；
- 衰减斜率点估计最小为 HAM #3（`+0.0807s/胎龄圈`），最大为 LEC #3
  （`+0.1291s/胎龄圈`）；
- 共同胎龄条件配速点估计最快为 LEC #3（`86.826s`），最慢为 TSU #2
  （`88.106s`）；
- 最慢/最快衰减对象的燃油敏感性区间均与其他对象重叠，因此没有发布确认排序。

实现没有改写 research v16 pilot 或前端报告 JSON；两份仍保持原 SHA-256
`34437e9ec75350ec637224ca60ae1d675cbc5afbf75b863c4cf0cf9b1f8c1950`。当前曲线
由 v16 汇总字段线性重建，不含逐圈散点，也不冒充 `tyre_curve_model_audit` 中
quadratic/log/Kalman 的已选曲线。

验收：

- `npm run build` → `npm test`：通过；新增反例覆盖空斜率、空支持端点、单车队、
  跨配方、无共同支持、稳定窗口 key、共同区间采样和真实 Abu Dhabi 极值；
- Node canvas 合同确认两张图均产生折线操作、可访问名称和可读数值图例；
- Chrome 桌面与 390px 移动端 smoke 通过，MEDIUM/HARD 切换、桌面双栏、移动端
  单栏和画布边界均通过；
- 截图保存在
  `.runtime-cache/ui-smoke/stint-curve-explorer-{desktop,mobile}.png`。

## 2026-07-26：工作台解释单一来源与加载失败闭环

工作台已落实“稳定含义只写一次”的前端合同：

- `frontend/src/telemetry-help.js` 集中维护术语、方法和代理边界；报告 JSON 未被
  改写，也没有逐版本复制解释段落；
- 正文只保留当前筛选、样本数、发布门、比较基准、极值、不可以及不可用原因；
  燃油情景、共同支持、有效权重、事件重叠和进站周期等定义改由帮助按钮显示；
- 提示支持鼠标悬停、键盘聚焦、`Escape` 关闭和触屏点击，使用共享
  `role="tooltip"` 节点并限制在视口内；
- v15 的 70 场报告仍缺少 v16 的代表胎龄配速和支持区间，因此不伪造双曲线；
  但曲线区不再静默消失，而是显示简短不可用原因，Stint 表继续正常加载。不可用
  状态同时隐藏筛选器、画布和旧图像，避免残留上一份 v16 曲线。

加载链也改为 fail-close：

- 静态服务器仅对无扩展名页面路由使用 SPA fallback；缺失 JSON/JS/CSS 返回真实
  404 和 `nosniff`；
- `fetchJSON` 同时检查 HTTP 状态、`application/json` 内容类型和 JSON 解析；
- 已有成功报告时，后续加载失败会恢复上一份报告的筛选、scope 和正文；首次加载
  失败则隐藏全部报告正文，只保留错误状态；
- 事件账本表头去掉硬编码的“v15”，v15 下拉改为“70 场批次（逐场发布门）”，
  不再把 7 场 `audit_only` 和 3 场 partial 误写成全部正式可发布。

当前验收证据：

- `npm run build`、`npm test` 通过；构建测试逐份解析并渲染 manifest 的 75/75
  份报告，其中 v15 不可用状态 70/70、旧遥测档案 4/4；
- 真实 Chrome 中 75/75 报告通过，浏览器异常和失败响应为 0；
- 缺失 JSON 返回 404，切换失败回滚和首次失败空壳均通过反例；
- 帮助提示的 hover/focus/`Escape`、v15 可见降级、桌面/390px 曲线布局通过；
  截图为 `.runtime-cache/ui-smoke/help-tooltip-desktop.png`、
  `.runtime-cache/ui-smoke/v15-curve-unavailable.png` 及既有双曲线截图。

发布限制：当前工作树的 70 份 v15 与 1 份 v16 工作台 JSON 尚未进入当前 Git
发布快照；本地 public/dist 完整不等于 GitHub Pages 已发布。正式发布仍必须把
manifest 引用的 71 份文件与本轮前端变更一并提交，并在提交快照中重跑构建。

## 2026-07-26：车手 / 车队官方颜色统一

前端三条身份色链路已合并为 `frontend/src/official-team-colours.js`。2023、2024、
2025、2026 分别冻结 10、10、10、11 支车队，色值逐季取自 Formula 1 官方成绩页
HTML 中的 `teamColourCode`；名称别名也按赛季隔离。未知赛季、未知车队或身份
冲突直接失败，不再生成替代色。

本轮删除了：

- 主赛况页按车号从五色 palette 取模的回退；
- 工作台十队手写近似色与未知车队 HSL hash；
- Live Review 按车手 TLA 硬编码颜色及灰色回退；
- 主页面 CSS 中 `var(--team, ...)` 的静默身份色回退。

同队车手现在严格共用同一官方色。Race Dossier 曲线按规范车手身份稳定分配虚实
线，同一车手多个 Stint 与输入反序后保持同一线型。Live Review 的生成数据改为
只保存 22 位车手的 `team` / `team_key`，浏览器再由同一目录解析；因此筛选器、
1,254 条计时行和 TR 卡不再各自携带颜色。

验收：

- `npm run build`、`npm test` 通过；构建合同覆盖官方 41 个发布
  `season|team` 身份、未知身份 fail-close、75/75 工作台报告、同队同色、稳定线型
  以及 Canvas 实际颜色/线型记录；
- 真实 Chrome 同时通过主赛况页 11 队/22 车手、工作台 2025 Ferrari/Mercedes/
  Red Bull 曲线、Live Review 22 车手及计时/TR 渲染路径；浏览器异常和失败响应为 0；
- 截图保存在 `.runtime-cache/ui-smoke/official-team-colours-{main,workbench,live-review}.png`。

本轮没有把稳定颜色说明复制进每份 Race Dossier 或遥测报告 JSON；版本报告仍只
保存赛事证据。官方来源页为 Formula 1 的
`/en/results/{2023,2024,2025,2026}/team`。

## 2026-07-26：数据页面本地入口修复

主赛况、Race Dossier 工作台和 Live Review 已统一通过
`frontend/src/data-page-entry.js` 加载。HTTP/HTTPS 下正常注入页面模块；
`file://` 下不加载模块、不请求 JSON：默认本地服务已运行时自动切换到同名 HTTP
页面，未运行时显示可点击入口和 `npm run dev` 命令。

真实 Chrome 已覆盖当前用户使用的 `frontend/src/telemetry-workbench.html`
直接打开场景：无服务时不出现 `Failed to fetch`，有服务时最终进入
`http://127.0.0.1:5173/telemetry-workbench.html` 且状态为“已加载”。三个 HTTP
页面均无浏览器异常和失败响应；工作台 manifest 的 75/75 份报告继续全部渲染。

## 2026-07-26：Race Dossier v17 逐圈配速证据正式验收

旧 v16 图不是平均速度，也不是逐圈数据。它只从每段 Stint 的代表配速、参考胎龄
和线性斜率重建
`p(x) = p* + beta * (x - x*)`，因此图形必然是一条直线，而且两个画布重复表达
同一组汇总点估计。v17 改为发布后端冻结的逐圈 sidecar，浏览器只负责显示：

- 条件调整后的逐圈散点、逐圈是否进入主拟合及排除原因；
- 加权 Theil–Sen 主拟合方程和仅在输入支持内发布的预测网格；
- 观测残差中间 80%、删除真实连续 3 圈块后的稳定性中间 80%，以及
  `low/base/high` 三组燃油代理敏感性范围；这些都不是置信区间或新圈预测区间；
- 仅在预先冻结的确认门通过后发布非线性描述性曲线，且不替代主拟合；
- 在每一对 Stint 的共同胎龄支持内重算样本、Kish ESS、交通、比赛阶段、非绿旗、
  进出站、低质量圈和 nuisance 调整平衡；只有 `comparable` 才发布直接秒差。

正式运行：

```text
run_id: 20260726T124625+0800-ce93134a40b8
manifest: research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/manifest.json
manifest_sha256: 9322064faee009e0e53d7ec345e9d25499f821658cdf05d497a74d233567658c
```

机器产物覆盖 2023–2025 共 70 场：60 场 `complete_race`、3 场
`partial_team_coverage`、7 场 `audit_only`；70/70 份逐圈 sidecar 均在 manifest
中有路径、SHA-256 和报告身份链。四支报告车队共发布 31,904 个逐圈点，其中
31,871 个进入主拟合；1,225 个 Stint 有范围支持，1,224 个得到删块稳定性估计。
6,298 个成对比较中只有 368 个 `comparable`，205 个为平衡警告，5,725 个
`not_comparable`。这个低通过率是数据条件的审计结果，不通过放宽门槛美化。

2025 Abu Dhabi MEDIUM 的工作台窗口发布 98/98 个主拟合圈和 4 条 Stint 证据。
HAM–ANT、HAM–TSU、ANT–TSU 三对通过；LEC 与三人的交通/条件分布距离均未通过，
因此三行直接秒差显示为 `—`，页面不再从条件点估计生成“LEC 最快”的全序结论。

工作台 manifest 现有 145 份追加式报告，其中 70 份 v17 报告带独立
`curve-evidence` sidecar。构建只复制 manifest 明确引用的报告和 sidecar，并逐项
校验散列；v17 默认进入“逐圈发布证据 · 浏览器零拟合”，v16 保留为明确的
`summary-only` 历史回退。

正式发布审计已验证 70 份报告、70 份 sidecar、3 份赛季汇总、artifact、lap
universe、实现身份和前端导出身份，并检查无支持外推、非可比直接秒差为空等语义
不变量。真实 Chrome 桌面 1440×1000 与移动端 390×844 smoke 通过：sidecar
懒加载、散点/两类范围画布、方程、样本审计、成对门控、桌面双栏和移动单栏均
可见，浏览器异常及失败响应为 0。截图位于
`.runtime-cache/ui-smoke/stint-curve-evidence-v17-{desktop,mobile}.png`。
视觉复核还发现 canonical `pair_key` 的字符串顺序不保证等于 sidecar 的
`left_stint_key` / `right_stint_key` 方向；前端现以显式左右键定向标签、样本和
`left_minus_right_pace_s`，身份不闭合时 fail-close。反向键 fixture 与真实
Abu Dhabi `ANT ↔ HAM +0.324s` 浏览器断言均已加入，避免符号与标签颠倒。
最终回归为 `python -m pytest tests -q` 231/231 通过；前端
`npm run build`、`npm test` 通过，构建合同确认 145 份工作台报告、141 份
Race Dossier、4 份旧遥测档案、70 份 v17 报告和 70 份 sidecar。
`git diff --check` 退出码为 0（仅有工作区 LF→CRLF 提示，无空白错误）。

不可识别边界保持不变：公开遥测没有真实燃油量、SOC、轮胎状态/磨损、设定、损伤、
动力模式和车队指令。v17 只能给出可观测条件下的配速代理与敏感性范围，不能把
区间解释成真实物理因果贡献，也不能把 `classification_proxy` 称为 FIA 最终分类。

### 可比焦点遥测视觉重构（专项验收通过）

本轮在既有 v17 sidecar 和后端冻结结果上重构展示合同，不改模型或重新生成数值：

- 默认只展示一对 `comparable` Stint，或成员之间每一对都为 `comparable` 的完整
  clique；`warning` / `not_comparable` 只保留审计信息，不进入共享快慢轴或全序；
- 纵轴默认使用 `FOCUS`，由入拟合点及已发布线/范围确定核心配速域，并提供
  `FULL` 全量视图；超出焦点域的点以图边缘方向标记和数量保留，不静默裁掉；
- 散点使用固定点大小，仅以透明度表达分析权重；删块稳定性范围改为上下两条点线，
  不再使用填充带；燃油代理只画 `L/B/H` 三个离散点，不连接成线或包络；
- 移动端将成对比较改为 pair 卡片，并与桌面端共同提供可触发的 tooltip，显示身份、
  方向、共同支持与门控原因；
- 浏览器继续保持零拟合、零平滑，只消费 sidecar 中已冻结的点、方程、范围和门控。

专项验收已执行：`npm run build` 与 `npm test` 通过；真实 Chrome 在 1440px
桌面与 390px 移动视口中确认默认 comparable pair、7 个焦点选项、
`FOCUS → FULL → FOCUS` 状态切换、2 个焦点外圈边缘标记、每条两车比较共
6 个离散燃油情景点，以及逐圈 tooltip 的已发布字段。移动端
`document.scrollWidth = innerWidth = 390`，pair 审计无需横向宽表；桌面图例
只显示当前两条焦点曲线，完整四条 Stint 与六对门控仍留在折叠审计区。新截图为
`.runtime-cache/ui-smoke/v17-redesign-{desktop,mobile}.png`。

## 2026-07-30 参考分析复刻实验室 v1：F1pace pilot 验收

### 实现路径

- 新增 `research/reference_analysis_lab/f1pace_pilot.py`、运行器和
  `tests/test_reference_analysis_lab.py`；输入为真实 2025 Abu Dhabi Race 逐点 Parquet，
  不使用合成圈时；
- 新增独立静态页 `frontend/src/reference-analysis-lab.html` 及其 CSS/JS，并从根索引
  manifest 跟随目标 manifest，再校验报告 SHA-256；入口已挂入现有本地导航；
- 新增 F1pace method card、data-gap audit、validation、报告和前端 manifest，均采用
  新 run 身份，未覆盖 v14–v17 产物；
- `visual_replication` 提供 Top 10、Bottom 10、All drivers、summarized race pace、
  逐点交通热图和 pairwise mean delta；`audited_analysis` 只提供审计画像与不可比原因，
  不形成第二个全序排名。

### 数值与账本验收

2025 Abu Dhabi Race 覆盖 20/20 车手。圈宇宙 1,156，公开口径有效配速圈 1,082；首圈
排除 20、Stint 边界排除 54、非绿/黄旗排除 0、`deleted` 标记保留 38。逐点正赛样本
784,424，有效配速圈关联样本 728,315。F1pace 公开均值 20/20 通过，最大绝对差
`0.000491s`（容差 `0.001s`）；pairwise 190 对反向矩阵通过。交通热图共 1,156 个
driver×lap 单元，比例来自逐点距离和速度/时间权重，阈值为 2 秒与 33%，包含回退车。

### 自动化与浏览器验收

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 5 passed
cd frontend
npm run build                                             # passed
npm test                                                   # Frontend build contract OK
```

真实本地 HTTP URL 为 `http://127.0.0.1:5173/reference-analysis-lab.html`。Chrome
1440×1000 桌面首屏与 390×844 移动端均显示 `PASS · 哈希已校验`，移动断点的标题、导航、
筛选器和四张指标卡无横向溢出；截图证据为：

```text
D:\vibe-coding\F1 TR\.runtime-cache\reference-analysis-lab-v1\f1pace\ui-smoke\
  reference-analysis-lab-f1pace-desktop-final-v2.png
  reference-analysis-lab-f1pace-mobile-final-v2.png
```

与 F1pace 参考图的差异：本产品重绘了相同的语义（均值/Q1/Q3、Stint 分离、交通标记、
pairwise 符号和分布轮廓），但使用 F1 TR 中性颜色与系统字体，不复制 logo、品牌字体或
原图；参考原图仍只存于 `.runtime-cache` 并有哈希。参考页面未公开精确代码和选圈实现，
因此 method card 将这些部分标为视觉反推/不可识别；公开均值差异来自 feed 版本与字段
清洗口径，保留有效圈集合、差值和容差，不手工调值。

Qualifying 继续独立显示“整场最佳准确推圈代理/阶段不可识别”。真实燃油、SOC、胎温
胎压、物理磨损、设定、损伤、动力模式、车队指令和全局均值的因果贡献均为不可识别；
`audited_analysis` 因停站、配方、胎龄、交通和比赛阶段未满足 v17 可比门控而保持
`audit_only`。

## 2026-07-30 参考分析复刻实验室 v1：DeltaData 方法等价 pilot 验收

### 实现与来源

- 新增 `research/reference_analysis_lab/deltadata_pilot.py` 和
  `research/run_reference_analysis_lab_deltadata_v1.py`；输入为真实 v17 Abu Dhabi
  `stint_curve_evidence.json`，没有合成点；
- X profile `https://x.com/DeltaData_` 以 HTTP 200 缓存，profile HTML、两个公开帖子的
  URL/图片 SHA-256 和抓取时间写入 method card；web 文本抽取为空但缓存 HTML 暴露公开
  profile/current post 证据，因此精确一比一算法标记 `SKIPPED_OPAQUE_METHOD`，目标仍以
  `METHOD_EQUIVALENT_ONLY` 交付；
- 新增独立页面 `frontend/src/delta-data-pilot.html`、CSS/JS，根 manifest 追加
  `deltadata` target，页面可切换 `visual_replication` / `audited_analysis`。

### 数值与审计账本

2025 Abu Dhabi v17 sidecar 覆盖 19 个 Stint、464 个真实观察点、421 个质量合格拟合点、
167 个 clean-air 点，clean-air Kish ESS sum 为 165.743。车队/车手表随行披露 Stint、
配方、有效点、clean-air 点、参考配速和敏感性；low/base/high 是命名燃油修正情景，胎龄
斜率单位为秒/胎龄圈。72 对 Stint 中 4 对 `comparable`、2 对
`audit_only_balance_warning`、66 对 `not_comparable`；审计视图不形成全序，未通过门控
的 direct delta 保持 `null`。

### 自动化与浏览器验收

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 8 passed
cd frontend
npm run build                                             # passed
npm test                                                   # Frontend build contract OK
```

真实本地 HTTP URL 为 `http://127.0.0.1:5173/delta-data-pilot.html`。Chrome
1440×1000 visual、390×844 visual 和 1440×1000 `?view=audit` 均显示
`PASS · 哈希已校验`；审计截图确认 `not_comparable` direct delta 显示为 `—`，没有把
后端 `null` 误显示为零。截图证据为：

```text
D:\vibe-coding\F1 TR\.runtime-cache\reference-analysis-lab-v1\deltadata\ui-smoke\
  delta-data-pilot-desktop-v2.png
  delta-data-pilot-mobile.png
  delta-data-pilot-audit-desktop-v2.png
```

差异与边界：参考图语义（clear-air/H2H/样本披露）已重绘，但未复制 X 原图、logo、品牌
字体或人物照片；精确选圈、燃油和归一化方法没有公开，不能声称一比一；跨配方、胎龄和
阶段的视觉排序只是条件代理，`audited_analysis` 继续 `audit_only`。

## 2026-07-31 F1pace 交通算法倒推 v2 验收

### 结果

- 阿布扎比校准表：1,156 个真实 driver×lap 单元；按整名车手五折交叉验证平均
  MAE `3.641pp`，最差折 `4.342pp`，平均 `>33%` 判定准确率 `98.097%`。
- 卡塔尔事件外表：1,067 个真实单元，模型未重拟合；视觉模型 MAE `2.238pp`、
  RMSE `5.723pp`、P90 `6.848pp`，阈值判定准确率 `99.250%`。
- 卡塔尔纯物理 2 秒 crossing companion：MAE `2.498pp`、RMSE `6.959pp`，
  阈值判定准确率 `98.594%`。它不使用参考图，继续标为 `audit_only`。
- 参考图颜色反解经 22 个明文标签校准，读图器 MAE `0.473pp`、最大误差约
  `1.50pp`；该误差与模型误差分开披露。
- 真实数据点：Abu Dhabi 784,424、Qatar 720,670；合成点为 0。

发布状态为 `METHOD_EQUIVALENT_EXTERNALLY_VALIDATED`，不是一比一源码复现。
全套 method card、data-gap audit、逐格数值 CSV、排除账本、差异说明和参数化模型位于：

```text
research/records/reference_analysis_lab_v2/f1pace/
  run=20260731T120000+0800-f1pace-reverse-engineered-v2/
```

### 自动化与浏览器验收

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 16 passed
cd frontend
npm run build                                             # passed
npm test                                                   # Frontend build contract OK
```

本地入口为 `http://127.0.0.1:5174/f1pace-reverse-engineered-v2.html`。Chrome
1440×1800 桌面截图和 CDP 真实 390×1800 移动截图均通过；移动端实测
`window.innerWidth=390`、`document.scrollWidth=390`，页面没有横向溢出，driver×lap
热图保留独立横向滚动。证据位于
`.runtime-cache/reference-analysis-lab-v2/f1pace-ui-smoke/`，对应哈希和视觉结论写入
追加式 `browser_acceptance.json`。

剩余长尾主要来自首圈、进出站、退赛末圈、遥测距离积分和未知原作者异常规则；公开图
只有颜色代理且页面声明该交通功能仍在测试。因此最大单元误差不作为“真值错误”单独归因，
也不会为了贴图手工修改阈值。

## 2026-07-31 DeltaData clean-air pace 倒推 v2 验收

### 实现与公开证据

- 新增 `research/reference_analysis_lab/deltadata_reverse_engineered_v2.py` 和
  `research/run_reference_analysis_lab_deltadata_v2.py`，主 pilot 使用真实
  2025 Abu Dhabi Race 784,424 个逐点样本；
- 公开方法证据冻结 `@DeltaData_` X 身份、第三方搜索镜像快照和
  “Last 11 H2H Mercedes Clear Air Race Pace”参考图 SHA-256
  `f7ee8009377f99617a71ddb02e6e010bddcb418d2d038e194b7c1062fb0961d3`；
- 镜像公开文本确认 2026 方法使用 1,021 圈逐圈复核、`0.032 s/圈`、代表性
  clean-air 圈、同配方/相近阶段优先以及样本量/质量加权。镜像不是作者权威档案，
  精确阈值、人工标签、回归代码和权重公式仍为 `SKIPPED_OPAQUE_METHOD`；
- 缓存中名为 `team-representative-race-laps.jpg` 的第二张图实际是 Ferrari 赛车照片，
  已标记 `INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH`，没有作为方法证据。

算法实现为逐点同位置物理交通、`traffic_ratio<=0.20`、公开 `0.032 s/圈` 情景和
`HuberRegressor(epsilon=1.5, alpha=0.01)` 条件标准化。Huber 自动下权只叫
“人工复核的方法等价替代”，不声称恢复作者逐圈标签。视觉层在公共条件分布上排序；
审计层独立门控同配方、共同胎龄、阶段、交通、样本量和 Kish ESS。

### 公开图、v1 与当前 pilot 的具体差距

三场可核对的 2026 Mercedes H2H 结果为：

| 事件 | 公开 ANT−RUS | v2 ANT−RUS | v2−公开 | 审计状态 |
| --- | ---: | ---: | ---: | --- |
| Japan Race | -0.131% | -0.242173% | -0.111173pp | `AUDIT_ONLY` |
| Miami Sprint | -0.270% | -0.178253% | +0.091747pp | `AUDIT_ONLY` |
| Miami Race | -0.572% | -0.643100% | -0.071100pp | `COMPARABLE` |

合计 MAE `0.091340pp`、RMSE `0.092794pp`、最大绝对误差 `0.111173pp`、偏差
`-0.030175pp`，方向 3/3 一致。Japan 的 Russell 只有 2 个 Huber 代表圈，Miami
Sprint 只有 5 个，因此视觉数值保留但审计 fail-close；这也是没有为了贴图放宽门槛的
直接反例。其余 8 行因本地没有对应 2026 freeze，保持 `NOT_TESTED`。

对本仓库 v1 的同场比较只看相对最快者 centered gap，因为两版绝对参考点不同。共享
8 名车手顺序均为 `NOR/LEC/VER/PIA/HAM/RUS/ANT/TSU`，共享 4 支车队顺序均为
`McLaren/Ferrari/Red Bull Racing/Mercedes`；顺序完全一致。v2 相对差距较 v1
压缩：车手 centered-gap MAE `0.154270s`、最大 `0.273087s`，车队 MAE
`0.063910s`、最大 `0.113528s`。来源是：

- v1 只消费 v17 sidecar 四队和 cross-fit `analysis_weight`，v2 使用全场原始遥测；
- v1 主燃油情景为 `0.051724 s/圈`，v2 视觉主口径为公开 `0.032 s/圈`；
- v1 在 Stint 参考胎龄上拟合，v2 用逐点物理交通、80% clean-air 和全场 Huber；
- v2 对不足三圈的 HUL 显式不排名，不用模型外推补齐。

Abu Dhabi 圈宇宙 1,156，互斥账本为 411 个 clean-air 候选、38 个 deleted、20 个
首圈、54 个进出站边界和 633 个交通超阈值圈；Huber 接受 320 个代表圈、下权 89 个，
代表圈 Kish ESS `318.864`。20 名车手全部披露，19 名进入模型；10 支车队中 Kick
Sauber 因 HUL 样本不足不形成双车排名。10 个同队配对只有 4 对通过审计门，不形成
全场审计总序。

### 自动化与浏览器验收

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 21 passed
cd frontend
npm run build                                             # passed
npm test                                                   # Frontend build contract OK
```

本地入口为
`http://127.0.0.1:5174/deltadata-reverse-engineered-v2.html`。Chrome/CDP 验收：

- 1440px visual：12 指标卡、3 基准卡、10 车队行、10 H2H 行，运行时错误 0；
- 390px visual：`innerWidth=390`、`document.scrollWidth=390`，无页面横向溢出；
- 1440px `?view=audit`：visual panel 隐藏、audit panel 可见、10 个门控行，失败
  配对的 audited delta 为 `—`。

截图位于 `.runtime-cache/reference-analysis-lab-v2/deltadata-ui-smoke/`：

```text
deltadata-v2-desktop.png
deltadata-v2-mobile-390.png
deltadata-v2-desktop-audit.png
```

浏览器证据 SHA-256 写入追加式 `browser_acceptance.json`，其 SHA-256 为
`bcc7f1ea4d6948b613e955d5e654522ffaa680372e70fac07d56549a15f61557`。
页面使用 F1 TR 中性视觉，不复制参考图、人物、logo 或品牌字体。

发布状态为 `METHOD_EQUIVALENT_REFERENCE_BENCHMARKED`，不是盲测或一比一复现。
真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车手管理意图和车队指令
仍不可识别。

## 2026-07-31 FDataAnalysis 赛道特征倒推 v2 验收

- 新增 `research/reference_analysis_lab/fdataanalysis_reverse_engineered_v2.py`、
  `research/run_reference_analysis_lab_fdataanalysis_v2.py` 和独立静态前端。
  主 pilot 仅使用真实 2025 Abu Dhabi Race：784,424 个原始逐点样本、728,315 个
  有效样本、1,082 个统一距离轴圈，合成样本为 0。
- 1,000 区间动态分段得到 16 个弯角代理和 6 个直道代理；800/1,000/1,200
  三档敏感性均为 16/16 匹配，最大峰位漂移 `0.001 lap`。v1 的 88 行
  “出口速度低于其报告最低速度”反例在 v2 降为 0。
- 速度积分圈时 MAE `0.154728s`、P90 `0.304797s`，门控通过。视觉 top15 与
  审计逐圈 P99 中位数的车手级 MAE `22.040524 km/h`；样本与时间权重全油门比例
  MAE `0.308727pp`，差异均已作为统计口径差公开。
- 同队条件匹配共 34 对圈；10 个车队组合中 Alpine 与 Racing Bulls 两对通过，
  其余 `audit_only`。正反向最大反对称误差 0，不生成车辆总分或因果全序。
- 参考图为 2026 Hungarian Race/FP2，与 2025 Abu Dhabi Race pilot 不同事件和
  session，数值验收 fail-close 为
  `NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION`，没有伪造跨事件 MAE。
- 定向测试 `python -m pytest tests/test_reference_analysis_lab.py -q` 为
  `25 passed`；`npm run build` 与 `npm test` 通过。首次前端合同测试发现缺少显式
  跨事件状态字面量，补入 fail-close 身份检查后通过。
- 本地入口为
  `http://127.0.0.1:5174/fdataanalysis-reverse-engineered-v2.html`。Chrome/CDP
  桌面 visual/audit 与 390px visual 均通过；移动端初验曾因 hero 长状态 flex 项
  把页面撑至 507px，修复后 `innerWidth=390`、document/body `scrollWidth=390`，
  运行时异常 0。

截图与浏览器证据位于
`.runtime-cache/reference-analysis-lab-v2/fdataanalysis-ui-smoke/`：
`fdata-v2-desktop-visual.png`、`fdata-v2-desktop-audit.png`、
`fdata-v2-mobile-390.png` 和 `browser_acceptance.json`。浏览器验收 SHA-256 为
`6adfa8f6977e68a2b01fe3447ff5825a3b1129089ed2db50ef9b4b01c5e3f899`，
追加式 acceptance manifest SHA-256 为
`edd49066013b30ef76c424326e607ec63ee064e16214db4487efa2898ea2f88d`。

## 2026-07-31 F1TelemetryData 同场图表包倒推 v1 验收

- 新增 `research/reference_analysis_lab/f1telemetrydata_reverse_engineered_v1.py`、
  `research/run_reference_analysis_lab_f1telemetrydata_v1.py` 与独立静态页面。
- 同场公开证据冻结 2025 Abu Dhabi 帖子 3015–3034 的 13 张图；产品只重画语义，
  不复制参考图、logo 或品牌字体。X profile 与 Telegram 频道身份、抓取时间和哈希
  均进入 method card。
- 真实输入为排位 395,840 个逐点样本、正赛 784,424 个逐点样本、88 个准确排位推圈
  和 1,156 圈正赛 timing 宇宙；合成样本为 0。OpenF1 补充按 URL 与 SHA-256 冻结，
  20 位车手最快排位圈 car channel 共 6,319 个约 3.7 Hz 样本。
- Q 阶段身份覆盖 88/88：Q1 42、Q2 28、Q3 18。排位 Lap Delta 20/20 MAE
  `0.000000s`，Top Speeds 20/20 MAE `0.000 km/h`，前三名 sector time 最大误差
  `0.000000s`。
- 控制分段未逐车手调参：前六名 Lap Sections MAE `2.639122pp`、最大
  `6.157325pp`；10 队 Throttle Usage MAE `1.195668pp`、最大
  `3.083765pp`。这是当前最大图表语义差距，来源是创作者阈值、采样、插值和 feed
  版本未公开。
- 正赛 Average Gap 20/20 MAE `0.000216s`、最大 `0.000471s`；最快圈 20/20
  完全一致；Stint 边界 47/47 一致，公开可见 Top 5 为 5/5。
- Pit 图使用 `lane_duration`，当前冻结 OpenF1 对公开图车队均值 MAE
  `0.065808s`、最大 `0.363s`（Mercedes）；27 次通行的 `stop_duration` 覆盖
  26 次，缺失的一次未插补。
- `visual_replication` 发布 13 类图表语义；`audited_analysis` 把全局正赛均值保持
  `audit_only`，并固定
  `direct_global_race_pace_causal_order_allowed=false`。Track Dominance 只称共同
  距离累计时间代理，官方几何 sector anchor 为
  `NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR`。

验收命令：

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 30 passed
cd frontend
npm run build                                               # passed
npm test                                                    # Frontend build contract OK
```

本地入口为
`http://127.0.0.1:5174/f1telemetrydata-reverse-engineered-v1.html`。Chrome/CDP
实测桌面 visual 1425×8017、桌面 audit 1425×3782、移动 visual 390×9574；
移动端 `innerWidth=document/body scrollWidth=390`，运行时错误为 0。截图位于
`.runtime-cache/reference-analysis-lab-v2/f1telemetrydata-ui-smoke/`。浏览器验收
SHA-256 为
`33f0f28fe230a52f477f4909f76981de67d06478e7bb293ec43d59687ca21def`，
acceptance manifest SHA-256 为
`5f16e1be8ce4d45eb2a5c3f0e6c3c553bed879a92f7a5d2603b0f56d3871f635`。

## 2026-07-31 GP Tempo 分段 Delta 公开方法复刻 v1 验收

- 新增 `research/reference_analysis_lab/gptempo_reverse_engineered_v1.py`、
  `research/run_reference_analysis_lab_gptempo_v1.py` 与独立交互页面。
- 公开 About 首次抓取只有 763-byte SPA shell；第二次实质不同尝试冻结 bundled
  JavaScript/CSS，确认官方 sector 硬约束、段内距离线性插值、段内时间线性缩放及
  约 4 Hz 限制。两次尝试均写入 `attempts.jsonl`，无
  `SKIPPED_AFTER_3_ATTEMPTS`。
- 真实 pilot 为 2025 Abu Dhabi 五个 Qualifying 最快圈和五个 Race 最快圈，共
  3,192 个原始 car-channel 样本；单圈采样率 `4.149–4.167Hz`，中位
  `4.167Hz`，合成圈为 0。
- 10 圈全部两两有向比较，形成 90 个 Delta 和 270 个 sector 端点；最大端点误差
  `0.000000000000s`，90 个终点全部严格等于官方总圈时差，三段和对圈时最大误差为 0。
- 默认 Qualifying 选择 VER/NOR/PIA，同 SOFT、胎龄均为 2 圈；Race 切换默认选择
  PIA/HUL，同 MEDIUM、胎龄 3/4 圈，条件门通过。跨 session 默认 Q VER 对 Race
  LEC，页面强制显示
  `WARNING_CROSS_SESSION_COMBINED_CONDITIONS`、不同配方和胎龄支持警告。
- 多车手 speed/throttle/brake/gear/DRS、Delta 和赛道位置共享距离游标；真实
  Chrome 指针事件使赛道点 `cx` 从 `24.5540` 移到 `149.7624`，联动验收通过。
- 公开页没有固定 Abu Dhabi 逐点数值表，因此参考差异状态为
  `NOT_APPLICABLE_PUBLIC_METHOD_PAGE_HAS_NO_FIXED_PILOT_VALUES`。不报告虚构的段内
  MAE；段内只标记 `ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ`，端点误差单独精确
  验收。

验收命令：

```text
python -m pytest tests/test_reference_analysis_lab.py -q   # 36 passed
cd frontend
npm run build                                               # passed
npm test                                                    # Frontend build contract OK
```

本地入口为
`http://127.0.0.1:5174/gptempo-reverse-engineered-v1.html`。Chrome/CDP 实测
桌面 visual 1425×3596、桌面 audit 1425×2972、移动 visual 390×5123；
移动端 `innerWidth=document/body scrollWidth=390`，运行时错误为 0。截图位于
`.runtime-cache/reference-analysis-lab-v2/gptempo-ui-smoke/`。浏览器验收
SHA-256 为
`5dd4c3faabca126405abba4ccea668f62d395c741040b062c0ca23f9560298b7`，
acceptance manifest SHA-256 为
`f8be61a2cbaa611f95475f0e45aa95222182d260123e1099f6c30a4b4fd3368f`。

## 2026-07-31 参考分析复刻实验室五项目全仓验收

F1pace、DeltaData、FDataAnalysis、F1TelemetryData、GP Tempo 已按固定顺序全部
完成；没有目标触发 `SKIPPED_AFTER_3_ATTEMPTS`。最终全仓
`python -m pytest tests -q` 为 `267 passed`，前端 `npm run build` 通过，
`npm test` 返回 `Frontend build contract OK`。五项目均使用真实 2025 Abu Dhabi
数据作为 pilot，合成样本数为 0，并分别保留 `visual_replication` 与
`audited_analysis`；无法由公开数据识别的燃油、SOC、胎温胎压、物理磨损、设定、
损伤和动力模式未被补造。

## 2026-07-31 参考分析复刻实验室 v3 分赛道验证验收

### 冻结基线与数据覆盖

- Race Dossier v17 真相源
  `run=20260726T124625+0800-ce93134a40b8` 经独立审计通过：70 场、70 sidecar、
  31,904 点、31,871 拟合点、1,225 个支持范围、6,298 个配对。
- reference-analysis-lab v2 索引与五份报告哈希全部匹配；四份既有 acceptance
  manifest 通过，F1pace v2 的历史浏览器 acceptance 通过。候选比较前全仓基线为
  Python `267 passed`，前端 build/test 通过。
- 三年 raw Race parquet 全部可用：2023 22 场 17,816,224 点，2024 24 场
  18,998,812 点，2025 24 场 18,433,170 点；合计 70 场、24 条赛道、
  55,248,206 点，合成样本为 0。

### 五目标结果

- **A F1pace**：70,511/77,720 圈进入模型，70 场 pairwise 反对称误差为 0；
  2025 Abu Dhabi/Qatar 从所有训练折排除。24/24 回退 universal，避免把细微均值
  改善误报为赛道专用模型。
- **B DeltaData**：31,904 个 v17 点中 14,040 个 clean-air 点进入模型；
  6 个 track、5 个 cluster 通过全部门，13 个回退 universal。专用模型留出 MAE
  改善 `3.16%–60.90%`，但 P90、max、方向和覆盖仍逐列披露。
- **C FDataAnalysis**：70 场各自建轴，profile 缺失率 0，起终点误差为 0；
  universal MAE `0.000238–0.000382 lap fraction`。24/24 回退 universal；
  Dutch 的 2023–2025 轮廓触发布局/采样不连续代理，track 候选禁止。
- **D F1TelemetryData**：7 个图表任务各有 universal/cluster/track 三行；
  只有 2025 Abu Dhabi 的 6 类冻结数值为 reference-informed PASS。Q 阶段数值与
  其余事件明确 `NOT_TESTED`，空值没有写成 0。
- **E GP Tempo**：2025 Abu Dhabi 270 个官方端点最大误差 0；其余 69 场缺官方
  sector/car channel，70 场均缺公开逐点真值，因此不发布虚构 segment MAE。

### 迭代与失败证据

1. `20260731T233000+0800-track-validation-v3` 的目标子 manifest 仍指向 staging；
   发布后身份审计失败，正式目录保留，索引标为
   `FAILED_POST_PUBLICATION_IDENTITY_AUDIT`。
2. `20260731T235500+0800-track-validation-v3` 修复路径身份并通过审计；后续结构审计
   发现 D 的 Q 阶段三模型表缺 universal 显式空行，保留并标为
   `SUPERSEDED_BY_ITERATED_VALIDATION`。
3. 最终 `20260731T235900+0800-track-validation-v3` 补齐 D 的 7×3 表和各目标
   显式缺失率；独立审计逐文件验证 SHA/bytes/rows、训练测试互斥、赛道/族群折、
   发布门、账本、端点与 `NOT_TESTED` 空值，结果 PASS。
4. 首轮 Chrome CLI 移动 PNG 虽为 390px，CSS viewport 实际被钳大并发生裁切；
   acceptance 保留并标失败。CDP r2 强制真实 CSS `innerWidth=390`，修复 hidden
   控件后 10 个 viewport case 全部通过。

### 前端与浏览器

独立入口为
`http://127.0.0.1:5187/track-validation-v3.html`。页面从 v3 public index 解析
唯一 latest，浏览器内校验 public manifest、report 与 model registry SHA；提供
目标、按 2025 赛历排序的赛道、图表任务和证据层筛选，并保持
`visual_replication` 与 `audited_analysis` 分离。

五个目标各生成一张 1440×1100 桌面截图和一张 390×1400 移动截图。CDP 验收确认
10/10 case 的 CSS viewport 精确、document/body 无横向溢出、run 身份可见、
任务控件语义正确、runtime exception 0、console error 0、network failure 0。
截图位于 `.runtime-cache/reference-analysis-lab-v3/browser-acceptance/
run=20260731T235900+0800-track-validation-v3-r2-cdp-css390/`；acceptance manifest
SHA-256 为
`13b8967809c44a8ac7f196350f96de82cdf0aa38c9b584ef47a5a4d7eff6a36f`。

定向验收为：

```text
python tools/audit_reference_analysis_lab_v3.py
# PASS：70 events、24 tracks、5 targets、10 browser screenshots

python -m pytest \
  tests/test_reference_analysis_lab_track_validation_v3.py \
  tests/test_reference_analysis_lab_track_validation_v3_release.py -q
# 10 passed

cd frontend
npm run build
npm test
# Frontend build contract OK
```

最终全仓回归 `python -m pytest tests -q` 为
`277 passed in 74.21s`；相对冻结基线新增 10 项 v3 协议、反例与正式发布审计测试。
