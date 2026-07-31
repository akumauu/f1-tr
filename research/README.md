# F1 车辆—车手分离实验

本目录执行“2024 年训练、2025 年锁定验证”的第一轮科研实验。2025 年验证数据不得参与模型选择、超参数选择、清洗阈值调整或特征筛选。

## 当前研究边界

- 第一阶段主动限定为 `Race` 与 `Sprint`。v4 规范层已经包含 `Qualifying` 与 `Sprint Qualifying`；排位模型因目标、清洗和分段结构不同，仍应另立实验协议，不能直接混入首轮比赛圈速模型。
- 第一阶段估计的是干地、非进出站、非明显赛会中断状态下的**回顾性相对比赛圈速**。
- 每个赛段内部使用中位圈速做参照。这适合车手—车辆相对分解，不等同于赛前绝对圈速预测。
- 2025 年只做一次锁定验证。若根据 2025 结果修改模型，必须生成新的实验版本，并把 2025 降级为开发集，另选 2026 已完成场次作为验证集。

## 阶段安排

1. **阶段 0：数据冻结。** 从验收通过的 `tracinginsights-expanded-v4` Parquet 提取 2024/2025 Race+Sprint，使用 `is_accurate`、绿旗、干胎和稳健异常值规则清洗，生成带 schema、来源 commit、配置哈希和文件哈希的数据 manifest。PostgreSQL 路径只保留用于复现旧基线。
2. **阶段 1：简单基线。** 零预测、仅上下文 ridge、仅车手 ridge、仅车队 ridge。
3. **阶段 2：车手—车队分解。** 同时加入车手和车队的正则化模型，观察其相对验证收益与系数稳定性。
4. **阶段 3：动态/层级模型。** 在不读取 2025 调参的前提下，引入比赛轮次动态效应、车手—车辆交互和概率区间。
5. **阶段 4：排位与多视图模型。** 回填 2024 排位数据后独立执行，不与第一阶段混用。

首轮 v1 查看 2025 结果后，所有新增结构都标记为“阶段 3 探索性”。这些模型仍以 2024 GroupKFold 选择参数、以 2025 计算外样本指标，但 2025 已不再是完全未见的确认性验证集；最终确认应转向冻结的 2026 已完成场次。

## 三次失败协议

每个方法最多执行三次：

- 每次失败均写入 `records/experiment_log.jsonl`，包含方法、尝试序号、错误类型和错误摘要；
- 允许的重试只包括实现错误、资源限制或数值求解器替换，不允许查看 2025 验证结果后调整模型；
- 第三次仍失败时写入 `skipped_after_3_failures`，停止该方法并进入下一个模型；
- 数据提取失败时可启动下一种、预先定义的降级提取方法，但必须使用新的 `method` 名称。

## 运行

```powershell
python research\run_experiments.py
```

只重建 v4 圈级冻结，不运行模型：

```powershell
python research\build_v4_frozen_data.py --archive-existing
```

只重用已经冻结的数据：

```powershell
python research\run_experiments.py --reuse-data
```

仅复现验收前 PostgreSQL 数据口径：

```powershell
python research\run_experiments.py --legacy-postgres
```

阶段 3 探索性模型：

```powershell
python research\run_phase3.py
```

首轮模型的配对分站 bootstrap：

```powershell
python research\analyze_model_comparisons.py
```

四队最均衡单站 M0/M1 圈级原型：

```powershell
python research\run_four_team_baseline.py
```

该命令先对 2025 Race 冻结集做覆盖审计，再锁定配置中的 Hungary 基准；拟合保留全场，只输出 McLaren、Red Bull Racing、Ferrari、Mercedes。默认执行 300 次分层 bootstrap，并从 `research/cache/external/` 复用或抓取 OpenF1 独立快照。需要重抓时使用 `--refresh-external`。

固定参数的六站赛果/观感反例压力测试：

```powershell
python research\run_four_team_contrast_stress.py
```

该命令不重新选择收缩参数，比较 Miami、Spain、Mexico City、Canada、Japan、Italy；Jolpica 最终分类用于核对冠军/领奖台，双车官方最快圈均值只作方向性外部代理。六站按已知结果事后挑选，不能将汇总命中数解释为确认性准确率。

该六站产物已经降级为诊断材料，不是独立算法模型；队友差和最快圈代理不得用于定义全场尺度或判断“人主导”。

2023–2025 全赛季鲁棒分层归因 v2（默认且强制离线）：

```powershell
python research\run_four_team_seasonal_attribution.py
```

该命令校验三个冻结圈级文件 SHA-256，审计 70 场计划 Race/69 场干地有效覆盖，用 2024 时间外误差和 0.01 实用等价带选择分块惩罚，再在 2025 后 19 站锁定测试。40 次年份内整站 bootstrap 输出赛季车辆/车手区间；本地 `laptimes.json` 分类代理只作单站和赛季构念检查，不参与拟合或选参。入口不包含 OpenF1、FastF1、Jolpica 或其他网络调用。

2023–2025 分层贝叶斯反事实复验 v3（默认且强制离线）：

```powershell
python research\run_four_team_bayesian_attribution.py --quiet
```

该命令复用 v2 冻结数据，先用无车队/车手身份的上下文头构造 1,324 个 `driver × event` Huber 聚合观测，再用多链共轭 Gibbs 联合抽样车辆、车手和 `driver-team`。它输出标准车手下的纯车辆、标准赛车下的纯车手、人车适配、R-hat、2024/2025 时间外聚合层指标、2024→2025 区间校准、交互先验敏感性和 Red Bull 留一车手影响审计。`--quick` 只作开发烟雾测试，不写正式结果。

2023–2025 单场—stint—圈级解释与周末性能前沿 v4（默认且强制离线）：

```powershell
python research\build_qualifying_frozen_data.py
python research\run_four_team_event_stint_attribution.py --quiet
```

第一条命令从验收通过的规范 Parquet 冻结 Qualifying accurate 干地推圈；第二条复用 v3 身份骨架，生成 69 场四队车辆/车手特征、1,298 个 stint 和 25,343 个四队 Race 圈的可加解释。每场车辆前沿有 240 次车手内 bootstrap，并执行 27 组 Race/Qualifying 分位数与会话权重边界网格。`--quick` 使用短链完成全合同烟雾测试，不写正式结果；正式结果属于已查看 2025 后的回顾性描述扩展。

全量逐站标准化 v5（默认且强制离线）：

```powershell
python research\run_four_team_race_by_race.py --quiet
```

该命令以 v4 圈级解释为身份骨架，对 2023–2025 全 70 站赛历逐站输出同构车辆、车手和 stint 表；2024 São Paulo 无统一清洁圈时保留显式缺失。每站只比较本场模型上沿、本场最快清洁圈与本场分类，并从逐点遥测计算距离/圈时平均速度。样本足够的 stint 执行早段→晚段留出、奇偶圈复验和斜率 bootstrap；赛季按分站等权汇总。2026 只做四站探索性重置更新，车辆/交互归零且使用历史时间前推误差宽预测带。首次运行会构建遥测缓存；需要强制重建时增加 `--rebuild-telemetry`。

六站手选压力测试只保留为历史诊断附录，不进入 v5 主验证、赛季总结或 2026 参数。

2023–2025 可解释深度层 v6（默认且强制离线）：

```powershell
python research\run_four_team_explanatory_depth.py --quiet
```

该命令复用 v3 Gibbs 身份骨架与 v4 圈级明细，从扩展遥测 parquet 提取 `distance_to_driver_ahead`。候选上下文特征只按 2024 时间外 MAE 选择，2025 只锁定报告；分离度同时门控队间/队内方差比、后验成对置信度和逐队最小圈数。driver-event 毫秒重建分别输出“模型组合序 vs 观测干净配速序”和“观测干净配速序 vs v5 classification proxy”，不得把前者误写成赛果异常。理论上限将最快 10% 圈的 half-normal 形状执行校正 bootstrap 纳入 CI；Q/R 只融合相对优势。`--quick` 使用短链且不写正式产物；重建 traffic 缓存时增加 `--rebuild-traffic`。

主要 v6 输出：

```text
research/records/four_team_explanatory_depth_2023_2025_v6.json
research/records/four_team_explanatory_depth_2023_2025_v6.md
research/records/four_team_explanatory_depth_{2023,2024,2025}_v6.md
research/artifacts/four_team_traffic_lap_metrics_2023_2025_v6_manifest.json
```

主要输出：

```text
research/artifacts/data/          # 本地冻结 CSV，不进入 Git
research/artifacts/data_manifest.json
research/records/experiment_log.jsonl
research/records/latest_results.json
research/records/latest_summary.md
research/records/four_team_attribution_hungary_2025.json
research/records/four_team_attribution_hungary_2025.md
research/records/four_team_external_validation_2025_hungary.json
research/records/four_team_contrast_stress_2025.json
research/records/four_team_contrast_stress_2025.md
research/records/four_team_seasonal_attribution_2023_2025.json
research/records/four_team_seasonal_attribution_2023_2025.md
research/records/four_team_bayesian_attribution_2023_2025.json
research/records/four_team_bayesian_attribution_2023_2025.md
research/artifacts/four_team_qualifying_2023_2025_manifest.json
research/records/four_team_event_stint_attribution_2023_2025.json
research/records/four_team_event_stint_attribution_2023_2025.md
research/artifacts/four_team_lap_explanations_2023_2025_v4_manifest.json
research/records/four_team_race_by_race_2023_2026_v5.json
research/records/four_team_race_by_race_2023_2026_v5.md
research/records/four_team_race_by_race_{2023,2024,2025,2026}_v5.md
research/artifacts/four_team_telemetry_lap_metrics_2023_2026_v5_manifest.json
research/artifacts/figures/race_by_race_v5/
research/records/four_team_explanatory_depth_2023_2025_v6.json
research/records/four_team_explanatory_depth_2023_2025_v6.md
research/records/four_team_explanatory_depth_{2023,2024,2025}_v6.md
research/artifacts/four_team_traffic_lap_metrics_2023_2025_v6_manifest.json
```

### 公开遥测解释、四站 2026 快照与评分门控

以下命令全部离线；2026 单站优先读取 expanded v5，缺失时才回退 v4：

```powershell
# 单站 Ferrari HAM-LEC 双车解释
python research\run_telemetry_explanation.py --quiet

# 四站一次运行；长任务也可逐站运行后只重建汇总
python research\run_2026_telemetry_batch.py --event-id australia --quiet
python research\run_2026_telemetry_batch.py --event-id china --quiet
python research\run_2026_telemetry_batch.py --event-id japan --quiet
python research\run_2026_telemetry_batch.py --event-id miami --quiet
python research\run_2026_telemetry_batch.py --aggregate-existing --quiet

# 28 名历史 PAC 卡和客观性门控
python research\run_driver_rating_cards.py --quiet
python research\run_objective_rating_review.py --quiet
```

单站报告的正差方向固定为 `target_driver - reference_driver`，不等于纯车手能力差。四场汇总少于 8 场确认门槛，`OVR`、`Event Delivery` 与期望积分必须为空。

### Race Dossier v10：低资源前台运行

正式入口只在 D 盘项目内运行，并把所有缓存放入 `.runtime-cache`：

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_race_dossier_v10_low_resource.ps1
```

该入口不启动后台任务，固定 NumPy/BLAS 与 DuckDB 单线程、每批一个文件、
DuckDB 768MB、低于正常进程优先级。当前 v10 复用已校验的 77,720 圈聚合
输入，不重新扫描大型原始遥测。

当前正式结果：

```text
research/records/race_dossier_v10/run=20260725T104301+0800-774be200a686/
research/artifacts/race_dossier_v10/run=20260725T104301+0800-774be200a686/
frontend/public/data/race-dossier/v10/run=20260725T104301+0800-774be200a686/
```

70 场均输出完整审计 Dossier，但 0 场通过四队联合整场发布门。475 个 Stint
通过自身门槛，556 个 Stint 的累计基线秒差可识别，69 场生成非空严格历史
轮胎包络，其中 66 场至少一个配方达到历史样本门。所有轮胎、衰减、进站窗口
与时间损益均为公开数据代理；PAC、OVR、期望积分与期望年度排名保持 null。

### Race Dossier v17：逐圈曲线证据正式运行

从仓库根目录执行单线程、D 盘缓存的正式入口：

```powershell
powershell -ExecutionPolicy Bypass -File tools\run_race_dossier_v17_low_resource.ps1
```

runner 会为每场报告同时生成 `stint-curve-evidence-v17` sidecar。正式运行完成后，
先审计源产物，再追加到工作台；不要手工复制或覆盖既有报告：

```powershell
python tools\audit_race_dossier_v17_release.py `
  --manifest "research\records\race_dossier_v17\run=20260726T124625+0800-ce93134a40b8\manifest.json"

python tools\export_telemetry_workbench.py `
  --race-dossier-manifest "research\records\race_dossier_v17\run=20260726T124625+0800-ce93134a40b8\manifest.json" `
  --append
```

当前正式结果：

```text
research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/
research/artifacts/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/
frontend/public/data/race-dossier/v17/run=20260726T124625+0800-ce93134a40b8/
```

70 场覆盖、报告/sidecar/赛季汇总、输入与实现散列以及前端身份链必须全部通过
`audit_race_dossier_v17_release.py` 才算正式完成。当前发布分布为 60 场
`complete_race`、3 场 `partial_team_coverage`、7 场 `audit_only`；6,298 对
共同胎龄比较中只有 368 对可直接比较。观测残差、删块稳定性和燃油情景均为不同
用途的敏感性范围，不是置信区间或新圈预测区间。

## 结果解释

- 目标值单位为 `100 × log(lap_time / session_median)`，负数更快。
- 车手/车队 `pace_advantage_pct` 已反号并做零均值中心化，正数表示相对更快。
- ridge 系数是正则化条件关联，不是物理因果真值。
- 新秀或新车队在 2024 未出现时使用零效应先验，并单独报告未见类别比例。
- 四队单站模型中的 `posterior` 字段当前包裹的是 bootstrap 区间；字段内部会明确标记 `not_bayesian_posterior`。
- 单站车手当站更新与人车交互不可分；`interaction_contribution` 只是强收缩代理，不能解释为稳定适配或调校偏向。
- 三赛季 v2 的配速优势是分块 ridge 条件关联；只有通过 bootstrap 和惩罚敏感性的组别结论可发布，不把 2024/2025 中间集团点估计当唯一全序。
- 2025 split-conformal 的 80% 覆盖仍偏低；面向 2026 优先使用 95% 区间并在新规则数据上重新校准。
- v3 的 `counterfactual_ratings` 是真正分层贝叶斯后验，但仍是观测数据的条件反事实，不是因果试验；长期单队车手的纯能力与人车适配部分共线。
- v3 原始 2025 95% 时间外覆盖只有 86.8%，使用 2024 非一致性分数校准后为 94.0%；发布时必须同时标明“原始/校准”。
- 稳定性头保留全部清洗后圈做 Huber 聚合；车辆上限/排位极限应另建尾部分位模型，不能从当前稳健平均配速后验直接推出。
- v4 已把车辆上限另立为条件分位数描述层：双车中位前沿是主口径，最佳单车手前沿只作理论上沿代理；bootstrap 区间不是贝叶斯后验。
- Qualifying 与 Race 只融合 session 内相对车辆优势，不融合绝对圈时；公开数据不能证明 setup、规格、损伤和动力模式完全一致。
- v4 的分类一致性只回答实际名次是否与同场干净圈配速大致相容；在 `V_result` 未完成前，偏差原因保持未解释。
- v5 的同场模型第一/本场最快圈第一一致率仍是回顾性构念检查；只有 `rolling_prior_events_only` 与既有 v2/v3 时间前推字段可以用于预测讨论。
- v5 的 2026 四站 bootstrap 区间不等于未来预测区间；必须同时读取历史时间前推误差校准的宽预测带，并保留 `exploratory_only` 状态。
- v6 的 106 例是配速模型序与观测配速序偏差；配速与 classification proxy 的 158 例差异另列，且不等同于 FIA 最终分类或已完成原因归因。
- v6 理论上限的执行校正与前沿采样都进入 CI；点估计第一只有与区间、分离度和样本门控一起读才有意义。
