# F1 车辆—车手分离研究日志

## 2026-07-17：首轮确认性实验

### 研究设计

- 训练：2024 Race + Sprint。
- 锁定验证：2025 Race + Sprint。
- 目标：`100 × log(lap_duration / session_median)`，负值表示更快。
- 清洗：干胎、非进出站、非 SC/VSC/红黄旗明显影响圈、稳健 MAD 异常过滤。
- 超参数：只使用 2024、按分站 GroupKFold 选择。
- 主运行：`f1_driver_car_2024_train_2025_validate_v1-20260717T000905`。

### 数据冻结

| 项目 | 数量 |
| --- | ---: |
| 原始有效圈 | 57,306 |
| 清洗后总圈 | 45,483 |
| 2024 训练圈 | 23,223 |
| 2025 验证圈 | 22,260 |
| 2024 训练赛段 | 29 |
| 2025 验证赛段 | 30 |

2024 São Paulo Race 因全湿地/非干胎条件没有进入本轮干地模型。2024 Qualifying 与 Sprint Qualifying 当前没有规范化 `laps` 数据，未参与本轮研究。

### 首轮结果

| 模型 | 2024 CV MAE | 2025 MAE | 2025 R² | 决策 |
| --- | ---: | ---: | ---: | --- |
| zero_relative_pace | — | 1.11842 | 0.00000 | 最低基线 |
| context_ridge | 0.94171 | 0.91930 | 0.24646 | 保留为上下文基线 |
| driver_only_ridge | 0.73070 | 0.83814 | 0.33814 | 保留为消融 |
| team_only_ridge | 0.73866 | 0.82728 | 0.36791 | 保留为消融 |
| driver_team_ridge | 0.72979 | 0.79059 | 0.40526 | 首轮最佳，但存在车队别名问题 |

配对分站 bootstrap：

- 联合模型 vs 上下文：MAE delta = -0.12871，95% CI [-0.15782, -0.10158]；
- 联合模型 vs 仅车手：-0.04755，95% CI [-0.09713, -0.01627]；
- 联合模型 vs 仅车队：-0.03669，95% CI [-0.04373, -0.02969]；
- 仅车队 vs 仅车手：-0.01086，95% CI [-0.06046, 0.02259]，不能据此声称车辆一定比车手更重要。

### Sprint 否定结果

联合模型在 2025 Sprint 的 MAE 为 0.99187，R² = -0.50038；相对零基线的 MAE delta = +0.22226，95% CI [0.10621, 0.32700]。当前 Sprint 模型判定不合格，原因可能包括：

- 2024 只有 6 场 Sprint、2,074 个清洁训练圈；
- Sprint 比赛状态、轮胎策略与 Race 不同；
- 赛段内中位数归一化使零基线很强；
- 当前上下文没有发车位、实时交通和短赛管理状态。

决策：产品端不发布 Sprint 车手/车辆评分；后续积累多赛季 Sprint 数据后独立建模。

## 2026-07-17：阶段 3 探索性模型

查看首轮 2025 结果后提出，因此以下 2025 指标不再属于完全盲测。

| 模型 | 2024 CV MAE | 2025 MAE | 结论 |
| --- | ---: | ---: | --- |
| session_head_ridge | 0.73015 | 0.79090 | 未超过 0.79059，否定 |
| team_track_ridge | 0.72978 | 0.79625 | 时间外变差，否定 |
| bayesian_driver_team | — | 0.81751 | 点预测较弱，保留为不确定性原型 |

Bayesian Ridge 的 80% 预测区间实际覆盖 82.74%，95% 区间覆盖 92.32%。95% 尾部仍低估，因此个体可信区间暂不进入产品；下一步需使用 Student-t/分站异方差模型。

## 2026-07-17：车队名称敏感性

2025 冻结集中 13.32% 圈的车队名称未在 2024 原样出现：`Alpine F1 Team`、`RB F1 Team`、`Racing Bulls`、`Red Bull`、`Sauber`。这些是同一车队的来源别名，不应当作新车队。

名称归一化后：

- team 未见率从 13.32% 降到 0；
- 联合模型 MAE 从 0.79059 变为 0.79733；
- canonical - original MAE = +0.00674，95% CI [0.00111, 0.01277]。

解释：错误别名恰好把部分 2024 车队效应收缩为零，获得了偶然的预测收益。科研与生产必须优先实体正确性，不能为了更低 MAE 保留错误身份。后续有效基准采用名称归一化后的 0.79733；0.79059 只保留为首轮诊断结果。

## 2026-07-17：跨赛季衰减

- 使用 2023→2024 选择车手/车队效应保留比例；
- 2023 清洁训练圈 19,277，2024 调参圈 23,223；
- 2023 GroupKFold 选择 alpha = 100；
- 最优车手衰减 = 1.00，车队衰减 = 1.00；
- 2024→2025 名称归一化验证 MAE = 0.79733；
- 相对首轮原始名称联合模型 delta = +0.00674，95% CI [0.00104, 0.01238]。

结论：简单的统一乘法衰减无法解决车队年度变化。下一模型应显式估计 `team × season` 动态状态和规则断点，而不是继续搜索一个全局衰减系数。

## 2026-07-19：三季面板 RAPM 真实积分榜校准 + rolling-origin 验证层

> 定位：`run_seasonal_standings_calibration.py` 是 §5.6 三赛季面板 RAPM 的**校准/验证叠加层，不重造估计器**（复用 `seasonal_attribution.fit_seasonal_model`）。补齐 §5.6 缺的：真实积分榜校准、rolling-origin、转会图连通性诊断、2026 车手先验。积分榜只读缓存、不入拟合。

### 设计
- 复用三季冻结 CSV（2023/24/25 Race，61,351 清洁圈，69 场）+ `balanced_v1` 惩罚集拟合全场面板。
- 抓 Jolpica 三季车手/车队积分榜（缓存 `research/cache/external/jolpica_*Standings.json`）。
- 转会二部图（车手在规范车队 ≥5 圈连边）连通性/割点/弱锚定诊断。
- rolling-origin：全 70 场按赛历 train≤r 预测 r+1；2025 锁定不参与选参。
- bootstrap 40× 分层重采样给车/手 z 区间。

### 结果
- **积分榜校准（核心，用户要的"数据支撑"）：** 三季合并 车队评分 vs 制造商榜 Spearman **0.892**、车手实跑(车+手) vs 车手榜 **0.904**、纯车手技能 vs 车手榜 **0.846**（更弱，符合"好车手可在差车里名次低"）；2025 车队 **0.964**、2023 车手实跑 **0.967**。方向性外部效度强。
- **rolling-origin：** 44 origin 总体分站等权 MAE 0.905，2025 锁定段 0.990（前瞻预测，误差高于内样属应有）。
- **关键否定发现——转会网络不连通（4 分量）：** 主集团(Ferrari/Mercedes/Williams/Haas/Sauber/Alpine 经 HAM/SAI/HUL 桥接)、红牛家族(RB+Red Bull Racing 内部互通但与网格无桥)、Aston Martin、McLaren 各自孤立。**Red Bull/McLaren/Aston 的车/手内部分解无跨网格转会锚定**，只靠赛段配速参照+收缩；绝对车辆排名可信（与积分榜 0.89–0.96 相关），车/手归因弱识别。这修正了此前"VER 开第 4 车夺冠"的过强表述：车辆绝对排名稳，车/手拆分在红牛孤立分量里弱识别。
- **标准化分解（正=更快，替换有偏队友差）：** 2025 车辆 McLaren +680 > Mercedes +268 > Ferrari +185 > Red Bull +177ms；纯车手 VER +611 ≫ LEC +331 > NOR +285 > … > TSU −409ms。结论"红牛=开得好、McLaren=车好"成立但标注弱识别。不输出百分比分摊。
- **2026 车手先验：** `research/artifacts/panel_2026_driver_prior.json`，池化三季车手效应（VER 0.640 顶、NOR 0.396、LEC 0.375…TSU/SAR/MAG 垫底）。

### 与 Codex/§5.6 的区别
§5.6/§5.7 是估计器本身（面板 ridge / 贝叶斯复验），只用本地圈速代理、一次时间前切。本层不重写估计器，新增**真实官方积分榜**校准、rolling-origin、连通性诊断、2026 先验——正是用户要的"用最终赛季评分作参考"且区别于 Codex。

## 2026-07-18：机理级车辆能力包络与车手发挥归因（从属机理组件，单站四强）

> 定位：本实验是 `docs/four-team-car-driver-attribution-model.md` 主规范下的**从属弯段机理组件**（`run_car_driver_envelope.py`），提供"差在哪个环节"的 Δt 分解；其**车辆上限估计已被 5.4/5.5 全场联合模型取代**（见文末分歧说明）。不作为独立车辆基准来源。

首个跳出"纯圈速回归"、直接使用采样级遥测的实验。目标不是再出一个排行榜，而是把最终圈时差**机理级地拆到弯段和物理通道**。

### 研究设计

- 样本：2025 西班牙站 Race，四大车队 8 车（McLaren NOR/PIA、Red Bull Racing VER/TSU、Ferrari HAM/LEC、Mercedes RUS/ANT）。干地、高下压力参照赛道。
- 直接读单站 parquet（duckdb），距离域重采样到 1000 点网格，30 个 mini-sector，弯型标注。
- 清洗：`is_accurate`、非 deleted、干胎、全绿旗、圈号≥3、圈时 74–95s、逐车手 MAD；脏空气圈按 `distance_to_driver_ahead<120m` 标记（169 圈）。
- Δt 恒等式：`dt=ds/v`，逐段可加、精确重构总圈时差（单测校验 Σ 分段=总差，容差 1e-9）。
- 上限两法：前沿 P90（池化两车手 sector 时间取 P10）+ 理论最优圈拼接（逐车手 P10 再取 min）；敏感性=两者差。
- 发挥%：车手自身低油最优（P10，与上限同口径抵消燃油）落在同队车包络 [下限 P60→上限 P10] 的位置。
- 不确定性：逐圈 block bootstrap（n=400）给上限圈时、上限差、车占% 的 80%/95% CI。

### 结果（点估计，run `f1_car_driver_envelope_spain_2025_v1-20260718T181127`）

- **车辆上限（前沿 P90 圈时）：** McLaren 76.95 < Red Bull 77.77 < Ferrari 78.01 < Mercedes 78.40；方向与 2025 公认竞争力一致。积分圈时中位 80.39s，与实际中位 80.66s 吻合。
- **车手发挥%（贴上限=100）：** VER 107 vs TSU 79；RUS 117 vs ANT 63（新秀）；LEC 99 vs HAM 87；NOR 102 vs PIA 95。队内强弱方向与真实叙事一致；>100% 表示该车手定义了车辆展示上限、队友拖低池化前沿（即前沿低估风险的直接体现）。
- **成绩归因（相对 McLaren）：** Mercedes 车占 62%（80%CI [57,79]）、Ferrari 57%（[54,74]）、Red Bull **车占 47% / 手占 53%**（80%CI [36,59]）。即 RB 车辆上限比 McLaren 慢 0.81s，但 VER 实跑反快 0.09s——出现"人发挥补偿甚至超过车辆劣势"的情形；但该 47/50 结论 CI 跨越 50%，只能称**倾向性而非确证**。

### 否定/限制结论

- 燃油/胎龄未显式修正：上限与发挥%用低油同口径抵消大部分燃油，但中位实跑与跨队发挥差仍含各队比赛燃油/策略差异。
- 单站=赛道特定，不能外推季度车辆评级；直道尾速差含设定与能量部署，不等同车手弯中能力。
- 前沿仅部分识别上限：队友一强一弱时（Merc、RB）池化前沿低估或误并。**本站把 Red Bull 车判为第 2（仅落后 0.81s），但 4 队同队前沿把 VER 的超常发挥误并入 RB 车辆上限。**
- **与全场联合模型分歧（关键）：** 并行主线 `run_four_team_baseline.py`（Hungary，全场 20 车联合识别）把 VER 技能剥离后，Red Bull 车实为四队最弱（落后约 1.44s）；5.5 六站压力测试在日本、意大利站也一致给出 McLaren 最快、Red Bull 第四。**故本组件的车辆上限被全场联合结果取代**；但两法在车手发挥排序上高度一致（VER 顶格、TSU/HAM/ANT 垫底），互为交叉印证。本组件保留价值在弯段机理定位。
- 归因 CI 较宽，RB 的车/手主导之争不可确证；产品端不发布确定性"车手真值/车辆真值"。

### 下一步

本组件后续只在弯段机理层深化（显式燃油修正 → 官方 sector 对齐 → 把弯段 Δt 作为特征喂给 5.4 全场联合模型，补其"差在哪个环节"）；车辆基准识别、跨赛道归一、转会锚定与 2026 迁移以 `four-team-car-driver-attribution-model.md` 主规范为准。

## 失败与跳过记录

`compound_join_data_audit_v0` 连续失败三次：

1. 多表直接连接导致中间结果爆炸并超时；
2. 聚合成功但 Decimal JSON 序列化失败；
3. 二级快照查询出现歧义字段。

已按协议标记 `skipped_after_3_failures`，改用各表先聚合的单用途查询。其他正式模型均在第一次尝试成功，没有触发跳过。

## 当前决策

1. **Race 当前候选：** 名称归一化的 `driver_team_ridge`，2025 Race MAE 约 0.78553；其系数仅作为条件关联。
2. **Sprint：** 当前所有模型拒绝，不发布评分。
3. **赛道交互：** 当前独立 `team × circuit` one-hot 过拟合，不采用；后续改为低秩赛道嵌入。
4. **不确定性：** Bayesian Ridge 可作为原型，但需重尾与异方差校准。
5. **确认集：** 任何看过本日志后提出的新结构，最终必须使用冻结的 2026 已完成场次确认。
6. **机理归因原型：** 遥测级车辆能力包络/车手发挥/跨队归因已跑通单站四强；结论为描述性倾向，归因 CI 宽、燃油未显式修正，暂不产品化。

## 产物索引

- 数据 manifest：`research/artifacts/data_manifest.json`
- 首轮结果：`research/records/latest_results.json`
- 首轮摘要：`research/records/latest_summary.md`
- 配对检验：`research/records/model_comparisons.json`
- 阶段 3：`research/records/phase3_results.json`
- 名称敏感性：`research/records/team_alias_sensitivity.json`
- 动态衰减：`research/records/dynamic_decay_results.json`
- 机理归因（车辆包络/发挥/归因）：`research/records/car_driver_envelope_results.json`、`car_driver_envelope_summary.md`、`research/artifacts/car_driver_envelope_manifest.json`
- 完整逐次日志：`research/records/experiment_log.jsonl`
