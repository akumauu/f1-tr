# F1 TR 算法与模型说明

> 权威范围：产品派生指标、科研模型、实验协议、评价方法和解释边界。实际数值与已验证结论见 [`implementation-results.md`](implementation-results.md)，数据表和存储见 [`database.md`](database.md)。

## 1. 两条算法线必须分开

| 算法线 | 目的 | 输入 | 输出 | 严谨性定位 |
| --- | --- | --- | --- | --- |
| 产品复盘派生算法 | 帮助用户理解某场比赛发生了什么 | 圈速、stint、TR、位置和赛会控制 | delta、衰退、TR 近似圈数、洞察卡片 | 内容辅助线索，不是官方工程结论 |
| 车辆—车手科研模型 | 尝试分离车辆性能与车手能力 | 冻结的跨赛季圈级/遥测数据和上下文 | 时间外误差、正则化效应、不确定性、稳健性结论 | 科研实验；条件关联不等于因果真值 |

修改任一条线时，不要把另一条线的指标或验收标准混入。

## 2. 产品复盘派生算法

### 2.1 圈速差

- `delta_to_previous_lap`：同一车手按圈序，用当前 `lap_duration` 减上一有效圈。
- `delta_to_stint_start`：当前圈速减同一 stint 起始有效圈的圈速。
- PostgreSQL 实现位于 `v_lap_deltas`，使用 `LAG()` 和 `FIRST_VALUE()`。
- Python 静态导出器有等价计算，非正圈速不进入派生结果。

解释：正值代表比参照圈更慢，负值代表更快。它反映观测差异，不自动归因于轮胎、燃油、交通或驾驶。

### 2.2 轮胎衰退

- 分析单位：单车手单 stint。
- 当前 SQL 核心：对 `lap_duration ~ lap_number` 使用 `REGR_SLOPE()`、`REGR_INTERCEPT()`、`REGR_R2()`。
- 斜率为正通常表示圈速随圈数变慢，但不能直接解释为纯轮胎物理衰退。
- 当前仅基础排除空圈速和 pit-out lap；Python 路径还会排除非正圈速。

发布限制：未充分控制 Safety Car/VSC、黄旗、交通、燃油变化、进站前慢圈、湿地和轮胎管理，因此只能叫“stint 内圈速趋势”或“衰退线索”。

### 2.3 Team Radio 上下文关联

- `v_radio_with_context` 通过 TR 时间与该车手最近的 `laps.date_start` 估算圈数。
- `driver_number`、session 和时间是关联关键；不能跨 session 只按车手号关联。
- 结果必须标注“近似圈数”，不能当作官方 lap number。
- OpenF1 Team Radio 通常只有录音 URL；翻译模型只处理外部已写入的 `transcript_en`。

### 2.4 翻译与语义标签

输入：英文转录、车手缩写和术语表。输出：`transcript_zh`、`intent`、`sentiment`、`key_entities`。DeepSeek 请求使用结构化 JSON 输出。

限制：项目当前不含 STT/ASR。`transcript_en` 为空时不应生成有业务价值的翻译；音频 URL 本身不是文字证据。意图和情绪是模型标签，前端应允许回看原文。

## 3. 科研问题与目标

当前研究问题是：在公开数据可识别的范围内，将观测比赛圈速拆分为车队/车辆、车手、赛道、轮胎、交通、比赛状态和噪声等成分。

必须区分：

- **潜在速度**：在尽量可比的干地、绿旗、非进出站圈下估计相对节奏。
- **实际比赛价值**：包括交通处理、轮胎管理、可靠性和策略结果，不能与潜在速度混成一个标签。

不把积分、完赛名次、单圈最快圈或遥测风格直接称为纯车手能力。这些量受到车辆、策略、DNF 和比赛情境共同影响。

## 4. 当前确认性实验协议

首轮协议位于 `research/configs/2024_train_2025_validate.json`：

- 研究范围：Race + Sprint；排位需另立协议。
- 训练：2024；锁定验证：2025。
- 数据来源：验收通过的 TracingInsights expanded v4 Parquet 冻结集。
- 目标：`100 × log(lap_time / session_median)`；负数更快。
- 清洗：准确圈、绿旗、干胎、非进出站及稳健异常值规则。
- 切分：按赛段分组，禁止同场相邻圈跨训练/验证泄漏。
- 超参数：只用 2024 GroupKFold 选择；2025 不得参与特征、阈值或模型选择。
- 看过 2025 后新增的结构一律标记“阶段 3 探索性”，最终确认转向冻结的 2026 已完成场次。

冻结身份以 `research/artifacts/data_manifest.json`、`dynamic_tuning_data_manifest.json` 中的 schema、配置哈希和文件 SHA-256 为准。

## 5. 已实现模型

### 5.1 首轮基线与分解

| 模型 | 结构 | 用途 |
| --- | --- | --- |
| `zero_relative_pace` | 始终预测赛段相对值 0 | 最低基线 |
| `context_ridge` | 仅上下文特征 + ridge | 判断上下文本身的解释力 |
| `driver_only_ridge` | 上下文 + 车手效应 | 车手消融 |
| `team_only_ridge` | 上下文 + 车队效应 | 车队消融 |
| `driver_team_ridge` | 上下文 + 车手 + 车队效应 | 当前简单联合分解基线 |

车手/车队 `pace_advantage_pct` 是正则化系数反号并做零均值中心化的展示量，正数表示相对更快。它是条件关联，不是物理因果贡献。

### 5.2 探索性阶段 3

| 模型 | 假设 | 当前定位 |
| --- | --- | --- |
| `session_head_ridge` | Race/Sprint 使用独立模型头 | 时间外未改善，否定当前实现 |
| `team_track_ridge` | 加入 `team × circuit` one-hot | 时间外变差，否定当前实现 |
| `bayesian_driver_team` | Bayesian Ridge 提供预测区间原型 | 点预测和覆盖率不足，不发布个体可信区间 |
| `dynamic_decay` | 车手/车队跨赛季效应乘法衰减 | 最优保留率为 1.0，未带来增益 |

### 5.3 稳健性分析

- **配对分站 bootstrap**：以分站为抽样单位比较模型 MAE，避免把相邻圈误当独立样本。
- **Race/Sprint 分层**：检查短赛与正赛结构是否不同。
- **车队别名敏感性**：统一跨来源同一车队名称，测量未见类别和指标变化。
- **v4 迁移复核**：用 Parquet 规范层重跑旧 PostgreSQL 基线，确认数据迁移没有改变总体结论方向。

### 5.4 四队单站归因 M0/M1 原型

`research/run_four_team_baseline.py` 实现了受限的圈级分层近似：

1. 从 2025 Race 冻结集按四队 8 位车手覆盖完整性、最少有效圈和圈数 CV 自动选出最均衡分站，不使用比赛结果或模型输出选站。
2. 用 2024 Race 全场数据的 `driver_team_ridge` 学习车手先验；目标站仍保留全场车队识别，只限制四队报告。
3. 在目标站联合拟合轮胎、比赛进程上下文、零均值车辆状态和同队零均值的强收缩车手当站更新。车辆惩罚为 10，车手当站更新惩罚为 100；另运行预注册 3×3 收缩网格，只报告稳定性，不据 2025 结果选参。
4. 固定参考车手后，用上下文标准化车辆圈的 P10/P50/P90 近似 Car Ceiling、Usable Baseline 和 Performance Floor；车手提取视角固定车辆但保留历史车手能力、当站更新和执行残差。
5. `V_pace` 只输出有符号车辆、历史车手、当站组合代理和未解释项；策略、可靠性、结果和积分层保持未建模。

单站里车手当站效应与人车交互完全共线，差异化 ridge 只是可审计识别约束，不是唯一真值。300 次车手 → stint → 圈 bootstrap 只提供采样稳定性区间，不满足 R-hat、ESS、PPC 或后验覆盖要求。

### 5.5 六站反例压力测试

`research/run_four_team_contrast_stress.py` 固定 5.4 的 2024 车手先验和 `team=10 / driver_event=100` 收缩参数，不针对目标站重新选参。测试按已知结果事后选取三场“观感与赛果高度一致”和三场“冠军相对赛季赛车印象反常”的比赛，因此只用于解释、寻找反例和检查模型是否机械复述冠军，不计算确认性准确率。

每站仍用全场识别、四队报告；车辆上限/基线/下限分别取标准化车辆圈 P10/P50/P90。外部比较改用 Jolpica Ergast-compatible API 的官方最终分类和双车最快圈均值。该代理未参与拟合，但单一最快圈高度受策略影响，且与研究上游可能共享官方计时源，只能检查方向。

六站中模型 P50 最快车与冠军车队一致 4/6；日本和意大利均判定 McLaren 最快、Red Bull Racing 第四，但冠军为 Verstappen。两场的官方双车最快圈均值也把 McLaren 排第一，说明模型没有简单复述冠军；与此同时 Verstappen 相对 Tsunoda 的同车标准化当站中位圈差约为 946 ms 和 1503 ms，支持“冠军主要由单车发挥/赛道位置等非车队均值因素解释”的假说，但该差不能解释为纯车手能力。

**降级结论：**该产物本质上是“同队中位差 + 车辆 P50/最快圈代理”的事后诊断，不是独立算法模型。队友差不能作为全场归一尺度，尤其不能用 VER–PER/TSU 的极端差定义“人主导”；六站汇总仅保留为 5.4 单站原型的反例材料。

### 5.6 三赛季鲁棒分层归因 v2

`research/run_four_team_seasonal_attribution.py` 使用本地冻结的 2023–2025 Race 有效圈实现跨赛季量化基线，配置为 `research/configs/four_team_2023_2025_seasonal_v2.json`。计划赛历共 70 场，其中 69 场有干地有效圈；2024 São Paulo 为湿地主头排除。模型不读取网络，也不把赛果、积分或队友差作为训练标签。

模型将 `team-year`、`team-circuit`、`driver`、`driver-year`、`driver-team` 分块收缩；赛后描述头再加入较弱收缩的 `team-event` 和强收缩的 `driver-event`。每个 `meeting × driver` 拥有相同总权重，再用 Huber IRLS 限制极端圈的杠杆。因此 VER–队友差只影响对应车手层与残差，不定义全场尺度，也不再生成车辆/车手百分比分摊。

验证协议：

1. 2023 + 2024 前 5 站拟合，2024 后续分站只用于选择惩罚；5 个候选若分站等权 MAE 距最优不超过 0.01，则按预注册顺序选择中央先验，最终为 `balanced_v1`。
2. 2023–2024 + 2025 前 5 站拟合，2025 后续分站作为锁定测试，不参与选参。
3. 2025 预测区间只用 2024 时间外残差做 split-conformal 校准；40 次按年份内整站重采样的 bootstrap 衡量赛季评分稳定性。
4. 本地 `laptimes.json` 赛道内分类/积分代理只作外部构念检查，不含赛后 DSQ、罚时和 FIA 最终修订，不能称为官方积分。

稳定性只支持 2023 Red Bull 第一、McLaren 第四，2024 Red Bull 第四，以及 2025 McLaren 第一；其余中间顺序或 2024 领先者在部分合理惩罚下会换位。该版本负责“跨赛季能否量化分开”；5.8 的逐点遥测组件负责“差距发生在哪个赛段”。二者不是重复实现。当前 v2 仍是分块 ridge 近似，不是规范 5.1 的完整概率状态空间模型。

### 5.7 分层贝叶斯反事实复验 v3

`research/run_four_team_bayesian_attribution.py` 在同一批本地冻结圈速上对 v2 做方法复验，配置为 `research/configs/four_team_2023_2025_bayesian_v3.json`。它不是旧的 `BayesianRidge` 预测原型：先用不含车队/车手身份的上下文头去除轮胎、赛道和圈次阶段，再将全部清洗后圈按 `driver × event` 做 Huber 聚合，形成 1,324 个观测；随后联合估计：

\[
y_g = \alpha + C_{team,year} + C_{team,circuit} + C_{team,event}
      + D_{driver} + D_{driver,year} + I_{driver,team} + \epsilon_g
\]

每个块使用零均值正态层级先验，块尺度和观测噪声方差使用逆伽马超先验，由 4 条共轭 Gibbs 链抽样。正式运行每链保留 600 个样本，主层级尺度最大 R-hat 为 1.014。三个输出严格分开：

- **纯车辆反事实：** `driver + driver_year + driver_team` 固定为零参考，比较 `team_year + team_circuit + team_event`；
- **纯车手反事实：** 赛车固定为四队等权参考且 `driver_team=0`，比较 `driver + driver_year`；
- **人车适配：** `driver_team` 单列，不并入纯车手评分。

2025 后 19 站的车手—分站聚合层时间外 MAE 从零模型 0.7174 降到 0.5816；原始贝叶斯 80%/95% 覆盖仅 70.9%/86.8%，说明概率模型仍漏掉分站冲击。只用 2024 时间外非一致性分数校准后，2025 覆盖为 80.2%/94.0%；面向 2026 必须保留该校准层。

结果不是“高度一致”的包装：2023 的 Red Bull 方向和 2025 的 McLaren 第一与 v2/本地分类代理一致；2024 贝叶斯给 Ferrari/McLaren 几乎并列，无法确认领先者；2025 贝叶斯将 Red Bull 放在四队末位，而 v2 的第 2–4 本就属于区间重叠的非稳定中间组。2025 车手后验中 VER 第一概率为 0.872，但 NOR/PIA/LEC 等人的 95% 区间大幅重叠。

识别边界同样被量化：把 `driver_team` 先验标准差从 0.06 放宽到 0.24 时，VER 的纯车手优势从 +0.643 z 降到 +0.418 z；移除 2025 VER 后 Red Bull 纯车辆优势漂移 -0.303 z，移除 LAW/TSU 后漂移 +0.198 z。因此贝叶斯没有“解决”长期单队车手与人车适配的共线性，只把先验依赖和队友结构污染显式化。该 v3 是静态分层后验复验，仍不是跨分站动态状态空间模型。

### 5.8 弯段级机理归因组件（从属，`car_driver_envelope`）

`research/run_car_driver_envelope.py` 是 5.4 主线下的**从属机理组件**，不作为独立的车辆基准来源。它直接用采样级遥测（distance/speed/throttle/brake）在距离域做 Δt 恒等式分解（`dt=ds/v`，逐段可加、精确重构总圈时差，单测校验 Σ 分段=总差），把最终圈时差落到 mini-sector 与弯型（重刹/低速/中速/高速/直道），提供 5.4 联合模型缺少的"差在哪个环节、差多少毫秒"这一层。当前样本为 2025 西班牙站四队 8 车；上限用同队前沿 P90 + 理论最优圈拼接两法，发挥%用车手自身低油最优对齐同队车包络，附逐圈 block bootstrap 的 80%/95% CI。

定位与限制（重要）：其"车辆上限"仅用四队同队前沿，**当一名车手异常出色时会把该车手技能误并入车辆上限**（Spain 样本把 Red Bull 车判为第 2）。5.6 的三赛季全场联合模型承担车辆/车手跨赛季识别，本组件只承担弯段机理定位；由于 2024/2025 中间集团在合理惩罚下仍会换序，任何一边都不能被称为唯一车辆真值。产物：`research/records/car_driver_envelope_results.json`、`car_driver_envelope_summary.md`、`research/artifacts/car_driver_envelope_manifest.json`。

### 5.9 真实积分榜校准 + rolling-origin 验证层（`seasonal_standings_calibration`）

`research/run_seasonal_standings_calibration.py` 是 5.6 三赛季面板 RAPM 的**校准/验证叠加层，不重造估计器**（复用 `modeling.seasonal_attribution.fit_seasonal_model`）。补齐 5.6 缺的四块：

1. **真实积分榜校准（核心增量）：** 抓 Jolpica 三季车手/车队积分榜（只读缓存、不入拟合），把模型车队评分 vs 车队积分榜、车手实跑(车+手) vs 车手积分榜做 Spearman。当前结果：三季合并 车队评分 vs 制造商榜 **0.892**、车手实跑 vs 车手榜 **0.904**、纯车手技能 vs 车手榜 **0.846**（预期更弱：好车手可在差车里名次低）；2025 车队 0.964。
2. **rolling-origin 逐站时间外：** 全 70 场按赛历排序，train≤r 预测 r+1；44 个 origin 总体分站等权 MAE 0.905，2025 锁定段 0.990（纯前瞻预测，误差高于内样是应有的）。
3. **转会二部图连通性诊断：** 关键否定发现——转会网络**不连通（4 分量）**：主集团(Ferrari/Mercedes/Williams/Haas/Sauber/Alpine 经 HAM/SAI/HUL 桥接)、红牛家族(RB+Red Bull Racing 内部互通但与网格无桥)、Aston Martin、McLaren 各自孤立。故 **Red Bull/McLaren/Aston 的车/手内部分解无跨网格转会锚定**，只靠赛段配速参照+收缩先验；其**绝对车辆排名可信**（配速被赛段参照锚定、与积分榜 0.89–0.96 相关），但车/手归因弱识别。
4. **2026 车手先验交接：** 冻结三季池化 `driver` 效应为 `research/artifacts/panel_2026_driver_prior.json`（正=更快，零均值），供 2026 锚定车手、只放开新车状态。

标准化车/手分解替换有偏的队友差判据：每(车手,赛季)输出有符号毫秒的 `car_contribution` 与 `driver_contribution`（均正=更快）、bootstrap 区间、连通性标记，**不输出百分比分摊**（沿用 5.6 `no_share_ratio`）。示例（2025，标注弱识别）：车辆 McLaren +680 > Mercedes +268 > Ferrari +185 > Red Bull +177ms；纯车手 VER +611 ≫ LEC +331 > NOR +285 > … > TSU −409ms——即"红牛=开得好(VER 全场第一、同车 TSU 垫底)、McLaren=车好"，但红牛孤立分量使其车/手内部归因仅弱识别。产物：`research/records/seasonal_standings_calibration_results.json`、`_summary.md`、`research/artifacts/panel_2026_driver_prior.json`、`research/cache/external/jolpica_*Standings.json`。

### 5.10 单场—stint—圈级解释与周末性能前沿 v4

`research/run_four_team_event_stint_attribution.py` 不改写 v3 的确认口径，而是把 v3 的车辆、车手和 `driver-team` 后验作为身份骨架，新增回顾性的单场—stint—圈级解释层。配置为 `research/configs/four_team_2023_2025_event_stint_v4.json`；2025 已被查看，因此该层明确标记为 `retrospective_descriptive_extension_after_2025_review`，不能把同场拟合解释称为赛前预测。

每个 Race 圈保留以下可加关系：

\[
z_{lap}=\hat z_{context}+\hat z_{car}+\hat z_{driver}+\hat z_{interaction}+r_{lap}
\]

其中上下文头不含车队/车手身份；`r_lap` 同时以 z、实际毫秒和车手—分站内分位数保存。车手—分站的 P10 残差作为数据条件前沿，`max(0, r_lap-r_{P10})` 作为相对该场可达状态的执行损失。stint 层汇总实跑/期望 P50、执行损失、MAD 稳定性，以及控制全场轮胎曲线后的超额残差斜率；后者只能称为 `degradation_residual_ms_per_tyre_lap`，不能称为纯轮胎衰退。

车辆周末上沿分三层：

1. Race：车手及适配中和后的 P10；
2. Qualifying：单独冻结 accurate 干地推圈、单独拟合无身份上下文头，再用 Race 学到的车手/适配先验中和；
3. Weekend：只融合两种 session 内的四队**相对优势**，默认排位/正赛权重 0.65/0.35，不融合绝对圈时。

车手等权的双车中位前沿是主口径；最佳单车手前沿另列为“理论上沿代理”敏感性，避免一位车手或样本数更多的一辆车直接定义车辆真值。每站还运行 `3 × 3 × 3 = 27` 组 Race 分位数、Qualifying 分位数和 session 权重网格，并报告每支车的名次范围和第一概率。区间来自车手内重采样的 240 次分层 bootstrap，不是新的贝叶斯后验；纯车辆/纯车手区间仍来自 v3 Gibbs 后验。

排位与正赛的 setup、升级规格、损伤和动力模式在公开数据中不可见；`parc_ferme_weekend_family_proxy_not_observed_setup_identity` 是融合边界，不是“完全同调教”的事实声明。赛果只读取本地 Live Timing 分类代理，输出 `compatible / better / worse than clean pace` 三档一致性；在策略、交通、SC/VSC、可靠性、事故和罚则进入 `V_result` 前，不把名次偏差自动归给车辆或车手。

### 5.11 全量逐站标准化、stint 留出验证与 2026 重置更新 v5

`research/run_four_team_race_by_race.py` 将 v4 的 69 场有效描述扩成 2023–2025 全部 70 站赛历合同：有数据的 69 站按完全相同的特征、边界和报告格式输出；2024 São Paulo 因统一干地、准确、绿旗清洁规则后无合格样本而保留显式空记录，不插值、不借用其他分站。配置为 `research/configs/four_team_race_by_race_v5.json`，机器真相源为 `research/records/four_team_race_by_race_2023_2026_v5.json`。

每站必须并列而不混用三类排名：

1. `weekend_ceiling_rank`：同场 Qualifying/Race 相对前沿按 0.65/0.35 融合；
2. `same_event_fastest_clean_lap_rank_four_team`：只取本场正式清洁圈，不再使用全年最快值；
3. `classification_proxy_rank_four_team`：只取本场本地分类代理，同时单列全场冠军和全场最快圈代理。

第 1、2 项共享本场圈速信息，因此二者的一致率只属于回顾性构念检查。v5 另设不读取当前分站特征的指数滚动排名基线，并继续保留 v2/v3 的 2024 调参与 2025 锁定时间前推指标；同场描述和时间前推不得合并成一个“准确率”。

逐圈平均速度固定为：

\[
v_{avg}=3.6\frac{d_{max}-d_{min}}{t_{lap}}
\]

其中距离单位为米、圈时单位为秒。采样点速度均值只作质量审计，避免不等频采样造成均值偏差。正式圈还要求距离覆盖率、采样数和两种速度口径相对差通过配置门槛。绝对平均速度只描述本场；跨站赛季图使用每站四队中心化后的 `average_speed_advantage_kph`。

对不少于 8 个清洁圈的 stint，v5 增加三种可复验量：

- 前半段拟合 `model_residual_ms ~ tyre_age`，对后半段计算真正留出的 MAE 与偏差；
- 奇偶圈分别估计中位残差，报告两者绝对差；
- 逐圈 bootstrap 200 次，输出超额残差斜率的 80% 区间、正斜率概率和符号稳定性。

该斜率仍是控制全场轮胎年龄与燃油阶段后的**超额残差斜率**，换胎、交通、损伤和策略可造成跨 stint 差异，不能称为纯轮胎物理衰退。赛季总结以分站为等权单位，不以总圈数加权。

2026 探索层只使用本地已有四站：继承 2025 车手后验，新车手使用零均值宽先验；车辆状态与 `driver-team` 交互在规则断点归零，车辆点估计以 5 个先验等效分站强收缩。四站 bootstrap 区间只描述当前估计稳定性；对外展示另使用 2025 锁定时间前推的校准 80% 平均宽度 1.741 z 形成预测带。四站少于 5 站确认门槛，故只能标记 `exploratory_only`，不得发布确定性 2026 排名。

### 5.12 可解释深度层：更强上下文、信号/噪声分离度、加性毫秒重建与理论上限 v6

`research/run_four_team_explanatory_depth.py` 是 v4/v5 的**严格超集**，复用 v3 Gibbs 身份骨架，只新增回顾性解释层，不改动 v2–v5 任一模块的对外行为。配置为 `research/configs/four_team_explanatory_depth_v6.json`，机器真相源为 `research/records/four_team_explanatory_depth_2023_2025_v6.json`；标记为 `retrospective_explanatory_extension_after_2025_review`，全流程离线。目标是让"每场特征值更明显"**由更干净的上下文与显式信号/噪声赚取**，而不是靠降低收缩制造伪差异。四个层：

- **Layer A 更强上下文头（`context_head`）：** 在身份无关的上下文头里加入候选 nuisance（`compound_tyre_slope`、`traffic_dirty_air`）。traffic 代理来自扩展 v4 遥测 parquet 的 `distance_to_driver_ahead` 逐圈中位数（复用 `run_car_driver_envelope.py` 的 dirty-air 模式，120 m 阈值，全场 94.7% 圈匹配），按 `(year, meeting, driver, lap)` 键 merge，不改冻结步骤。特征**只按 2024 时间外 `driver×event` MAE 选择**（2025 只锁定报告）：`traffic_dirty_air` 使 2024 MAE 从 0.747 降到 0.728、2025 锁定从 0.719 降到 0.677 被保留；`compound_tyre_slope` 使 2024 MAE 升到 0.753 被拒绝并写入 `context_gate.feature_trace` 审计。更干净的残差 `driver_neutral_vehicle_z` 让车/手信号在**先验强度不变**下更锐。
- **Layer B 信号/噪声分离度（`separation`）：** 每站队间/队内方差比（`separation_index`）+ 复用 Gibbs `team_event` 后验的成对排序置信度与整体顺序正确概率。最终等级取“方差比等级”和“成对置信度等级”的较低者；任一车队少于 12 个干净圈时强制 low。真正分明的站突出（Hungarian 方差比/置信度 0.685/0.922、Qatar 0.745/0.896 判 high），混乱站被诚实标 low（US 0.069/0.645、Las Vegas 0.052/0.666）。成对输出同时保存原始比较方向和后验多数方向，避免 `p<0.5` 时快慢标签反向。
- **Layer C 加性毫秒重建 + 双构念检查（`reconstruction`）：** 把每 driver-event 的可加成分转有符号毫秒（车/手/交互/上下文/执行/未解释），driver-event 圈速重建 R²=0.562、MAE=274.7 ms。模型组合序 vs 观测干净配速序平均 Spearman=0.821；两者排名差至少 2 位的 106 例是**配速模型偏差**，不是赛果异常。另用 v5 本地 `classification_proxy` 做不回流训练的赛果构念检查：观测干净配速序 vs proxy 平均 Spearman=0.750，排名差至少 2 位共 158 例。proxy 不含 FIA 赛后最终修订；这些差异只生成待查清单，不能自动归因给策略、可靠性、交通、事故或处罚。
- **Layer D 半正态校正的理论上限（`frontier`）：** 对驾驶中和后的车辆前沿加**只看最快 10% 圈**估计的 half-normal 形状执行损失校正（0.30 z 硬上限），Q/R 分开再按 0.65/0.35 融合。前沿和执行校正均按车手内重采样，校正不确定性已进入最终 80/95% CI；使用分站稳定种子，避免各站复用相同伪随机序列。输出仍是统计前沿，非物理极限；排位/正赛只融合相对优势，不合并绝对圈时，保留 parc-fermé 周末族 setup 不可观测 caveat。2025 点估计上限第一为 McLaren 14/24 站；Canada 为 Mercedes、Azerbaijan 为 Red Bull，但必须连同宽 CI 阅读。

边界：所有"更明显"来自上下文剥离与显式分离度，机器结果留存 v3 身份配置 SHA-256 与未变先验强度字段；2025 已查看，因此仍是回顾性解释扩展。4 链 × 600 样本最大尺度 R-hat=1.020。已生成 2023/2024/2025 三份逐站 v6 表；定向测试 9/9、全量 `pytest tests` 72/72。

### 5.13 公开遥测解释、同队车辆响应与评分合同 v7

`research/run_telemetry_explanation.py` 把逐点公开遥测转为单场可审计报告；`research/run_2026_telemetry_batch.py` 负责 2026 四场独立 era 批处理。它们不在圈速残差上继续堆一个总分，而是严格拆分五类对象：`Car Potential`、`Car Condition`、`Driver Ability`、`Event Delivery` 与 `Strategy/Ops`。当前只对公开数据足以识别的部分给数值，其余字段保持 `null`。

距离域与赛段合同：

1. 每圈按固定 5 m 网格重采样，区段时间严格使用 `dt = ds / v`；不向原覆盖范围外外推。
2. `TrackPhaseMap` 只由非目标车队的稳定参考圈冻结；速度、制动和油门决定 `corner/straight/transition`，官方 `corners.json` 的相邻弯角中点只负责切开连续组合弯，不改变相位标签。
3. 一对一可比圈先精确阻断 session、compound、绿旗、交通与 stint 阶段，再对胎龄、比赛进程和圈次窗口使用声明卡钳。声明字段缺失时失败关闭；双车分离池使用最大匹配数优先、总归一化卡钳距离全局最小的分配，不再用逐边贪心。报告同时输出匹配后协变量相对卡钳的平均绝对差；超过 0.65 降级，超过 0.90 或精确阻断不一致时返回 `not_comparable`。
4. `external_condition_matched_gap` 保留驾驶输入、策略和未观测状态；`input_adjusted_response_gap` 使用非目标车队逐段 ridge 基线，再控制入段速度、油门、制动、挡位与 RPM。后者仍是事件内诊断残差，不是因果效应。

单车异常必须同时满足：自身滚动基线、匹配队友和全场赛段中位数方向一致；直接时间族与独立运动学响应族同时支持，输入调整时间响应作为额外必需通道而不冒充第二个独立特征族；逐圈多赛段先做 Benjamini–Hochberg 筛查；同方向至少连续三次可比事件；匹配平衡门控通过。当前 p 值依赖事件内稳健尺度与正态近似，因此 FDR 只是一层保守筛查，不是确认性故障检验。输出名称固定为 `single_car_observed_response_divergence`，禁止命名底板、动力单元、电池或主动空力故障。

`episode_engine.py` 从全场数据反向构造进攻/防守机会、发车/重启窗口、stint 阶段和负尾候选。没有机会不记零能力；发车/重启没有二元结果时 `observed_successes=null`。`StintDossier` 只汇总阶段、干净圈、交通和 episode 暴露，不从单个 stint 反推长期能力。

2026 策略指纹只用逐圈直道早/晚段速度、高速平台、全油门、RPM 与派生响应信号，输出 `early_gain_mode`、`late_gain_mode`、`balanced_mode`、`save_like_mode`、`high_response_mode`。聚类标签和双车分布差只表示公开信号形状；真实 SOC、ERS-K 功率、主动翼位置和 Overtake/Boost 状态均不可观测。

评分分三层且不得混写：

- 历史 `PAC` 候选：从 2023–2025 模型中每位唯一车手只取一次 `-driver_base_z`，冻结 28 人总体分布，候选映射为 `clip(75 + 10z, 0, 100)`。`driver_base_z` 已经层级收缩，`sample_count` 只作覆盖审计，禁止二次收缩。现有 rolling-origin 只验证联合圈速预测，尚未验证池化 PAC 的车手级排序稳定性；同时没有 pooled `driver_base_z` 区间，因此 PAC 的数值与 `ci` 均保持 `null`。
- `Event Delivery`：2026 尚无全场、对称且时间前推验证过的参考分布，数值保持 `null`；单场只展示匹配物理秒数、stint 与 episode 暴露。
- `OVR`：PAC/TYM/EXT 为 prototype，ATK/DEF/AWA/STR/ADP 未完成，因此 OVR 必须为 `null`，不能平均现有模块；通用 OVR 构造器也会拒绝任何零暴露或 `insufficient` 组件，不能用先验均值 75 代替缺失能力。

客观性门控由 `research/run_objective_rating_review.py` 冻结：积分、完赛名次、冠军身份和 classification proxy 不得进入能力输入；2023/2024/2025 按整场时间前推，2025 锁定段不再选特征；真实积分榜只作车辆/实跑组合的外部构念检查。历史结果记账核心已通过，但 PAC 车手级时间前推门控与完整车手卡均未通过。2026 原始事件暴露为 4/8；按 v7 严格匹配质量只有 3 场合格，均只允许探索。

### 5.14 完整比赛 Race Dossier 与赛季审计 v8–v16

本产品线把首要分析单元从跨全场统一评分改为 `complete_race`。逐圈、局部
TrackPhase 和可比圈只作为证据，不得替代整场覆盖门。配置与入口分别为：

- 冻结失败证据：`research/configs/race_dossier_2023_2025_v8.json`；
- Stint/基线识别解耦反例：`research/configs/race_dossier_2023_2025_v9.json`；
- 历史轮胎池解耦：`research/configs/race_dossier_2023_2025_v10.json`；
- 科学数据代理与模块门：v11；曲线候选赛：v12；有序选择/确认与进站周期：
  v13；
- v14 低资源执行优化；
- 当前 70 场正式实现：`research/configs/race_dossier_2023_2025_v15.json`、
  `research/run_race_dossiers_v15.py`；
- 单场代表配速 pilot：`research/configs/race_dossier_2023_2025_v16.json`、
  `research/run_race_dossiers_v16.py`，尚不是 70 场正式发布。

#### 分析顺序

1. 建立完整 Race 圈宇宙并审计车手圈连续性、全场圈数、四队覆盖和账本闭合。
   输入保留全体参赛车队；`reporting_teams` 只定义主报告与发布门，不得在全场
   基线、交通或赛道演化估计前过滤中游/后排车队。
2. 先把非绿旗、进出站边界、质量缺失、交通/脏空气和未解释慢圈分开记账。
3. Qualifying 与 Race 独立估计；不产生统一周末排名。
4. v11 先用公开公式形成线性剩余燃油质量敏感性区间，再以跨车手留出验证
   决定是否接纳公共赛道演化和 gap-band 交通代理；它们都不是真实油量、
   物理抓地力或因果气动损失。v15 仍输出低/中/高燃油敏感性，不把中值伪装
   为已知的起步油量。
5. v15 主层先纳入所有物理可读圈：只对无效圈时、严重圈距不完整或逐点样本
   极少做硬排除。车队、车手、配方、胎龄、比赛阶段、交通状态、非绿旗、
   进出站边界、质量标记和遥测覆盖进入同一个比赛内加性条件模型。确定性
   两折交叉拟合产生 OOF 预测与残差；Huber 残差权重和测量质量先验只连续
   降权，不事后删除仍可读的异常圈。每场必须相对训练折车手中位数回退基线
   不劣化、达到绝对 OOF MAE 门，并通过调整饱和度门，否则整场为
   `audit_only`，且不能污染后续赛前策略历史池。
6. Stint 主层以加权中位数、加权 MAD、加权 Theil–Sen 和 Kish 有效样本量
   估计初速、稳定期、波动、胎龄斜率、末段掉速与累计秒差。v14 的严格
   clean-air 集合保留为独立确认层；只有严格层也通过的 Stint 才运行常数、
   线性、对数、二次和局部线性 Kalman 曲线候选赛。较早段选模、较晚段确认，
   不能用同一滚动验证结果既选模又声称确认。v16 的主表不再把初速、稳态和
   末段各自当作多个“Stint 圈速”，而是先做燃油与可观测条件修正，再用加权
   Theil–Sen 投影到本 Stint 支持区间内的加权中位胎龄，每个 Stint 只给一个
   代表配速；胎龄斜率、波动和末段状态仍作为解释字段单列。
7. 累计秒差另要求同配方、相近胎龄、相近比赛阶段的跨车队合理基线；
   基线不足时为 `not_identifiable`，不能反向判定 Stint 无效。
8. 赛前轮胎包络只接纳目标分站之前、事件账本闭合、主模型通过且自身为
   `valid` 的 Stint。
   配方长度、衰减区间、一停/两停组合与进站圈均为公开数据代理，不是真实
   燃油量、胎温胎压、磨损量或倍耐力/车队内部模拟。
9. v13 的进站损失是 pit-in/pit-out 圈相对两侧局部配速的完整周期代理，
   混合限速区、停车、路径和出站暖胎；已识别的衰减与进站组件仍不足以
   识别最优策略。
10. 车辆—车手拆分使用“车辆共同基线秒数 + 车手兑现偏差秒数 +
   策略/赛道位置代理秒数 + 未解释残差秒数”，要求加性闭合，不输出贡献百分比。

交叉拟合与稳健权重借鉴公开的
[Double/Debiased Machine Learning](https://arxiv.org/abs/1608.00060)、
[样本选择 DML](https://arxiv.org/abs/2012.00745) 和
[稳健均值估计](https://projecteuclid.org/journals/electronic-journal-of-statistics/volume-15/issue-2/Robust-and-efficient-mean-estimation--an-approach-based-on/10.1214/21-EJS1925.pdf)
思想，但这里是比赛内预测代理，不是对相关论文因果估计量的逐字复现。公开的
[F1 轮胎—燃油状态空间模型](https://arxiv.org/abs/2512.00640)可作为后续候选；
在没有真实燃油、胎温、磨损或车队标签时，其潜变量仍只能作带假设的数据代理，
不能改名为实测物理状态。

#### 发布门

v11 起把完整比赛上下文、单队、同队车手对和四队全场分别门控。只有四队
全部通过时才允许完整四队结论；至少两队通过时可发布明确列出车队范围的
`partial_team_modules`，未通过模块仍为审计。任何模块都必须满足自身车手数、
条件可分析圈、有效 Stint 和基线圈要求；v15 还要求全场包容性模型通过样本外
预测与调整合理性门。失败不会删除圈或 Stint，只关闭结论发布。

v8 首次真实运行暴露了“Stint 有效性依赖跨车队基线”的错误耦合，70 场全部
关闭；v9 保持所有数值门槛不变，只拆开两道识别门，得到 475 个有效 Stint，
但整场仍 0/70；v10 再把有效历史 Stint 的准入与整场四队联合发布门解耦，
69 场获得非空赛前轮胎包络，其中 66 场至少一个配方达到历史样本门。v8/v9
产物均保留，不覆盖、不回写、不用降低门槛美化结果。

v11 增加燃油、赛道演化、交通代理和模块发布门；v12 增加 Stint 曲线候选赛；
v13 加入有序独立确认和进站周期损失；v14 只把昂贵曲线候选赛限制到已经通过
物理门的 Stint，不改变 v13 科学阈值或有效 Stint 结果。正式 v14 在 70 场中
得到 1 场完整四队发布、15 场部分车队模块发布、54 场纯审计；543/3,946 个
全场 Stint 通过自身门，289 个曲线通过较晚段确认。其余 Stint 原样保留为
`audit_only`，不是数据文件丢失。

v15 修正“严格子集先决定主模型”的选择偏差风险。正式 70 场中，77,720 个
观测圈有 77,606 个进入包容性候选层（99.8533%）；OOF 软权重质量为
58,380.11，Kish 有效样本量为 65,437.76。20,562 个严格圈仅作独立确认，
不再代表主样本。63 场包容性模型通过；最终发布为 60 场完整 Dossier、3 场
部分模块和 7 场纯审计。全场 3,113 个 Stint 通过包容性自身门，其中 543 个
也通过严格确认；轮胎曲线仍只有 289 个通过较晚段确认。4 场因 OOF 预测门
失败、3 场因调整饱和度门失败，均保留完整审计数据且不得进入后续策略历史。

v16 目前只完成 2025 Abu Dhabi 单场真实数据 pilot。工作台的 Stint 展示先按
`SOFT / MEDIUM / HARD / INTERMEDIATE / WET` 配方分组，再在每个配方内按表中
实际显示的代表配速从快到慢排列；旧 v15 行缺少代表配速时只退回并明确标注
旧版中段中位数。该排序是浏览索引，不是新的估计量或胜负结论；直接比较仍须
满足同配方、参考胎龄接近和赛道条件相近。比赛下拉按事件时间新到旧排列：
优先用事件日期或 `round`，同一比赛继承已知轮次；四份没有正式轮次字段的
2026 v7 旧报告只复用冻结四站配置的相对事件序，并显示“四站序”，不冒充
FIA 正式赛历轮次。

PAC、OVR、期望积分和期望年度排名继续为 null；`classification_proxy` 只作
赛后审计，不进入拟合。v1/v2 与 v8–v14 历史失败产物保持追加式，不得覆盖。

## 6. 候选主模型与采用门槛

长期推荐不是立即堆叠黑箱，而是逐步建立动态贝叶斯多视图模型：

1. robust hierarchical lap model：车手、车队、赛道、轮胎、天气和情境的层级分解。
2. 动态状态层：车手/车辆随赛季和规则周期变化。
3. sticky HMM/HSMM：把推进、管理、交通、异常等隐驾驶状态与持续时间建模。
4. 赛道低秩嵌入和人车适配：只在时间外验证显示稳定增益后加入。
5. 原因特异可靠性/删失模型：处理 DNF 非随机和幸存者偏差。

下一阶段主规范见 [`four-team-car-driver-attribution-model.md`](four-team-car-driver-attribution-model.md)。首期只发布 McLaren、Red Bull、Ferrari、Mercedes 的车辆基准、性能包络、车手提取和价值归因，但识别数据保留全场。当前 5.4 的 M0/M1 单站原型、5.6 的三赛季鲁棒 ridge 基线、5.7 的静态分层贝叶斯反事实复验、5.10 的单场/stint 描述层、5.11 的全量逐站与 stint 留出验证、5.12 的可解释深度层（更强上下文/分离度/毫秒重建/理论上限）已经实现；动态概率前沿、统一赛段主模型、`V_result`、`V_points` 与 2026 OOD 确认仍未完成。

该规范将当前宽泛长期路线收束为五个实施里程碑：动态联合人车模型、执行前沿、赛段级四队差距、结果价值归因、2026 跨规则迁移。2023–2025 负责学习车手先验、赛道表示和适应协议；2026 车辆、能量、主动空气动力、跟车和人车交互状态必须重置或弱迁移，并显式处理赛季内规则断点。

候选组件及定位：

| 方法 | 推荐定位 | 主要风险 |
| --- | --- | --- |
| 时间衰减 RAPM/ridge | 强解释基线 | 线性、共线和标签敏感 |
| Rank-Ordered Logit / Plackett–Luce | 历史名次模型 | 丢失圈速幅度 |
| Elo/Glicko/TrueSkill | 展示型动态评分基线 | 默认混合人车贡献 |
| CatBoost/LightGBM | 纯预测挑战模型 | 泄漏、黑箱、SHAP 非因果 |
| BART/稀疏 GP | 非线性轮胎/天气组件 | 计算和动态结构成本 |
| 动态贝叶斯状态空间 | 目标主模型 | 识别、诊断和计算成本最高 |
| 功能 PCA + HDBSCAN | 驾驶风格画像 | 风格不等于速度 |
| 时序图神经网络 | 后期挑战模型 | 样本图小、校准困难 |
| DiD / synthetic control | 转会事件稳健性 | 事件少、平行趋势弱 |

采用复杂模型前必须满足：按比赛/时间外验证优于简单基线、预测区间校准、残差/PPC/收敛诊断通过、合理清洗下排序稳定、2026 OOD 单独报告。

## 7. 评价指标

- 点预测：MAE、RMSE、R²。
- 模型比较：按分站配对 bootstrap 的 MAE delta 与 95% CI。
- 概率预测：CRPS、对数预测密度、80%/95% 区间覆盖率和区间宽度。
- 稳健性：Race/Sprint 分层、别名、清洗、DNF、交通、燃油先验、跨赛季和规则断点敏感性。
- 工程质量：数据覆盖、缺口原因码、冻结哈希、无时间泄漏和可复现命令。

## 8. 运行与产物

```powershell
# 主实验
python research\run_experiments.py --reuse-data

# 阶段 3 探索
python research\run_phase3.py

# 模型配对比较、别名、动态衰减
python research\analyze_model_comparisons.py
python research\run_team_alias_sensitivity.py
python research\run_dynamic_decay.py

# 四队最均衡单站 M0/M1 原型（会复用或抓取 OpenF1 外部快照）
python research\run_four_team_baseline.py

# 六站赛果/观感反例压力测试（固定 M0/M1 参数）
python research\run_four_team_contrast_stress.py

# 2023–2025 全赛季鲁棒归因；完全离线
python research\run_four_team_seasonal_attribution.py

# 2023–2025 分层贝叶斯反事实复验；完全离线
python research\run_four_team_bayesian_attribution.py --quiet

# 冻结排位 accurate 推圈，并生成单场—stint—圈级 v4；完全离线
python research\build_qualifying_frozen_data.py
python research\run_four_team_event_stint_attribution.py --quiet

# 全量逐站 v5、stint 留出验证、赛季等权总结与 2026 探索性重置；完全离线
python research\run_four_team_race_by_race.py --quiet

# 可解释深度层 v6：更强上下文门控、分离度、毫秒重建、理论上限；完全离线
python research\run_four_team_explanatory_depth.py --quiet

# v7：单站同队遥测解释、四站 2026 批处理、历史全场车手卡与客观性门控
python research\run_telemetry_explanation.py --quiet
python research\run_2026_telemetry_batch.py --quiet
python research\run_driver_rating_cards.py --quiet
python research\run_objective_rating_review.py --quiet
```

| 产物 | 作用 |
| --- | --- |
| `research/artifacts/data_manifest.json` | 主冻结集身份 |
| `research/records/latest_results.json` | 首轮结果真相源 |
| `research/records/model_comparisons.json` | bootstrap 比较 |
| `research/records/phase3_results.json` | 探索模型结果 |
| `research/records/team_alias_sensitivity.json` | 实体归一化敏感性 |
| `research/records/dynamic_decay_results.json` | 跨赛季衰减 |
| `research/records/experiment_log.jsonl` | 失败、重试和跳过审计 |
| `research/records/four_team_attribution_hungary_2025.json` | 四队单站归因组合真相源 |
| `research/records/four_team_external_validation_2025_hungary.json` | OpenF1/FIA 外部方向性核验 |
| `research/records/four_team_bayesian_attribution_2023_2025.json` | 分层贝叶斯车辆/车手/适配后验、时间外覆盖和敏感性真相源 |
| `research/artifacts/four_team_qualifying_2023_2025_manifest.json` | 2023–2025 排位 accurate 推圈冻结身份、清洗审计和 SHA-256 |
| `research/records/four_team_event_stint_attribution_2023_2025.json` | 69 场车辆/车手特征、周末上沿、参数边界、分类相容性和 1,298 个 stint 的机器真相源 |
| `research/artifacts/four_team_lap_explanations_2023_2025_v4_manifest.json` | 25,343 个四队 Race 圈的可加解释文件身份与字段合同 |
| `research/records/four_team_race_by_race_2023_2026_v5.json` | 70 站赛历合同、69 站逐站结论、stint 留出验证、赛季等权总结、同场/时间前推分离验证和 2026 探索性更新 |
| `research/artifacts/four_team_telemetry_lap_metrics_2023_2026_v5_manifest.json` | 2023–2026 四队逐圈平均速度/稳定性遥测身份与 SHA-256 |
| `research/records/four_team_contrast_stress_2025.json` | 历史六站诊断附录；不进入 v5 主验证或 2026 参数 |
| `research/records/four_team_seasonal_attribution_2023_2025.json` | 三赛季时间外验证、车辆/车手评分、bootstrap、惩罚敏感性与本地赛果构念检查 |
| `research/records/four_team_explanatory_depth_2023_2025_v6.json` | v6 可解释深度：上下文门控 feature_trace、每站分离度、driver-event 毫秒重建 R²/名次异常、半正态校正理论上限 |
| `research/artifacts/four_team_traffic_lap_metrics_2023_2025_v6_manifest.json` | 逐圈 gap-to-ahead/position traffic 代理身份与 SHA-256 |
| `research/records/telemetry_explanation_2026_{australia,china,japan,miami}_ferrari_v7.json` | 四场同队可比圈、逐段双差、三基线异常、Episode、StintDossier 与中性策略指纹 |
| `research/records/telemetry_explanation_2026_ferrari_four_event_v7.json` | 四场探索汇总、8 场发布门槛及空值评分合同 |
| `research/records/driver_rating_cards_2023_2025_v1.json` | 28 名车手冻结 PAC 与其他模块显式空值合同 |
| `research/records/objective_rating_review_2023_2026_v1.json` | 历史构念、时间前推、区间校准、泄漏和 2026 暴露门控 |

## 9. Agent 修改检查单

1. 先确定改的是产品派生算法还是科研模型。
2. 检查数据真相源、单位、分组键、清洗和时间切分。
3. 不读取验证集来选择模型或阈值；看过验证结果后的调整必须新建实验版本。
4. 每种实验方法最多三次失败，记录到 `experiment_log.jsonl`；第三次后跳过。
5. 新模型同时提供最低基线、消融、时间外指标和解释限制。
6. 更新配置、机器可读结果、研究日志与本文；验证通过后再更新 `implementation-results.md`。
## PAC v1 条件同队原型（2026-07-23）

PAC 的第二轮验证已与联合圈级 MAE、积分、完赛名次及 `classification_proxy` 解耦。冻结协议
`research/configs/pac_validation_2023_2025_v1.json` 使用完整分站 rolling-origin：每个 origin
只用过去分站拟合无车手身份的 context head，并只对下一站同一 `team-event` 的
held-out pair-gap 做概率评分。transfer graph 在每个 origin 重新按
`driver ↔ team-year` 设计列构建，禁止未来转会反向锚定过去。

当前估计器版本为 `conditional_pair_laplacian_v1`：在历史同队 pair-margin incidence
matrix 上求 ridge Laplacian 单一车手身份量，不复用旧 `JOINT_BLOCKS` 的
`driver + driver_year + driver_team` 拆分。旧拆分对单队车手存在
`driver == driver_team` 完全共线，单年车手还存在 `driver == driver_year`；因此本原型
的 `conditional_pair_latent_z`、分层 bootstrap 区间与旧 `pooled_driver_base_z`、
旧 PAC 卡完全断开。

概率与 80%/95% 覆盖只由此前 origin 的 OOS pair-gap residual 经验 conformal 分布生成，
不能称为 pooled latent CI coverage。未来跨队圈速不作为 rank 标签；跨 origin 排序只在
过去图连通分量内作非门控诊断。分层 event→driver→stint bootstrap 复用同一 Laplacian
点估计器，但其区间仍只作分量条件化敏感性描述。

协议在 2025 全历史和 2026 前四站已可见后冻结，故 2023–2025 结果只能称
retrospective development evidence，前四站也只属开发期。正式确认要求冻结后至少
16 个比赛集群和 120 个 pair-event；8 场只算 interim。比赛块 bootstrap、最佳基线
（rolling climatology/EWMA/独立 BT）、WIS、奇偶圈伪配对、标签置换、reference
平移不变和半合成覆盖未完成时均为 `NOT_TESTED/FAIL`，不得默认通过。

### v1 方法审计状态与 v2

v1 配置及 `conditional_pair_prototype_validation_2023_2025_v1.*` 原样保留，
状态为 `superseded_for_method_audit`；其中 `protocol_frozen_at=00:00` 只是占位，
必须解释为 `timestamp_not_immutably_attested`。

v2 的发布识别图改为真正进入估计的 qualified pair-incidence 图，
`driver↔team-year` 只作上下文诊断。点估计和 event→driver→stint→lap bootstrap
共享 `driver_event_context_residual_median_v2` reducer，导出点值是 nominal
full score，不是 bootstrap median；区间明确条件于已拟合 context heads。

v2 当前区间只是 `event_clustered_rolling_empirical_predictive_interval`。由于不同
origin 使用不同模型，它不是标准 split conformal；严格的 event-cluster simultaneous
split conformal 为 `NOT_TESTED`。当前删边只在 pair estimator 层删除全部直接共现
team-event，未重拟 context head，因此准确命名为
`pair_estimator_direct_edge_purge`，full-pipeline purge 仍为 `NOT_TESTED`。

## 2026-07-25：Race Dossier v16 单一 Stint 胎龄代表配速

v16 不再把“初段圈速、稳态圈速、末段掉速”并列为每个 Stint 的主产品。它们可继续作为内部审计量，但工作台主表每个车手 Stint 只发布一个代表圈速和一个胎龄斜率。

配置入口是 `research/configs/race_dossier_2023_2025_v16.json`，解析时继承冻结的 v15 配置，只增加本节协议。真实全量运行前已经先通过合成燃油漂移反例；禁止根据真实比赛结果回调阈值。

对 Stint 中第 \(i\) 个有效圈，令：

- \(x_i\)：公开遥测中的轮胎使用圈数代理 `tyre_age`；
- \(y_i\)：先应用公开燃油质量敏感性模型，再扣除赛道阶段、交通、进出站边界和事件质量条件扰动后的圈速；
- \(w_i\)：v15 样本外残差 Huber 权重与观测质量权重的乘积。

胎龄斜率采用加权 Theil–Sen：

\[
\hat{\beta}
=
\operatorname{weighted\ median}_{i<j}
\left(
\frac{y_j-y_i}{x_j-x_i};
w_iw_j
\right)
\]

参考胎龄和单一代表配速为：

\[
x^\star=\operatorname{weighted\ median}(x_i;w_i)
\]

\[
p^\star
=
\operatorname{weighted\ median}
\left(
y_i-\hat{\beta}(x_i-x^\star);
w_i
\right)
\]

`x*` 必须位于本 Stint 实际胎龄支持区间内，v16 明确禁止外推。若胎龄跨度不足以识别斜率，只能输出 `level_only_tyre_age_slope_not_identified`，不能伪造衰减。低/高燃油情景在相同 `x*` 上重新投影，但保持非燃油扰动修正不变，因此只是燃油假设敏感性区间，不是真实油量重建。

直接比较门槛冻结为：

- 相同轮胎配方；
- 两个 Stint 的参考胎龄差不超过 2 圈；
- 赛道阶段和可观测交通条件相近；
- 两个 Stint 自身发布门均通过。

不同配方或明显不同胎龄的两个 `p*` 不能直接用于快慢归因。胎温、胎压、物理磨损量、损伤、SOC、动力模式和车队指令仍不可识别。

2025 阿布扎比单场真实数据 pilot 的 BOR 反例显示：MEDIUM Stint 的原始净斜率为 `-0.0143 s/胎龄圈`，燃油与条件修正后为 `+0.0642`；HARD Stint 从 `-0.0197` 修正为 `+0.0440`。这证明燃油变轻会掩盖轮胎掉速，也证明不能直接用原始逐圈线性趋势解释轮胎。该 pilot 只验证字段与方向，不构成 2023–2025 全量 v16 发布。

### 燃油代理外部一致性审计（不改变冻结参数）

v15/v16 低/中/高情景仍为成对的
`(90kg,0.02s/kg)`、`(100kg,0.03s/kg)`、`(110kg,0.04s/kg)`。
对圈号 \(l\) 和圈宇宙最大观测圈号 \(N\)，实现实际使用：

\[
C_q(l)=M_{0,q}k_q(1-l/N),\qquad
t^{corrected}_q(l)=t^{raw}(l)-C_q(l)
\]

因此 Abu Dhabi `N=58` 的低/中/高逐圈燃油效应是
`0.031034/0.051724/0.075862 s/圈`。公开来源中，TracingInsights 的
100kg、0.03s/kg、线性剩余质量公式与 base 完全相同；f1metrics 从 2017
Barcelona 已知 race simulation 估计 `0.054s/每圈燃油`，比本场 base 高
`0.002276s/圈`（4.21%）；f1pace 的 2025 Hungarian GP 模型使用
`0.03s/圈` 行业近似，与本场 low 相差 `0.001034s/圈`（low 高 3.45%）。
来源与计算详见 `docs/implementation-results.md`。

这只是外部 concordance，不是带真实燃油标签的校准。赛道、年份、技术规则、
初始油量、SC/VSC、lift-and-coast 和余油口径均不同，禁止把三个值平均后事后
回调参数；当前只把 low–base 视为覆盖常见公开口径、high 视为压力情景。
配置中的 `linear_by_scheduled_race_fraction` 名称也必须谨慎：当前实现分母是
最大观测 `lap_number`，不是独立读取的计划圈数。既有冻结记录不改；若更名或
更换分母，必须新建版本并补缩短比赛、红旗及异常圈宇宙测试。

## 2026-07-25：Dossier 赛后一致性审计与跨 Stint 可比分组 v1

目的：把"没有意外时，比赛成绩应当与配速表现一致"从口头判断变成逐场可量化验收，并把 v16 的单一代表配速自动组织成允许直接比较的配对表。该层只消费已发布 Dossier 与同源冻结圈宇宙，不重新拟合模型、不修改任何发布门；classification 只作赛后构念审计，不进入拟合。

入口与实现：`research/run_dossier_concordance_v1.py`、
`research/configs/dossier_concordance_2023_2025_v1.json`、
`research/modeling/dossier_concordance.py`、
`tests/test_dossier_concordance.py`。

协议（全部预注册；看过真实结果后修改任何阈值必须新建 concordance-v2）：

1. 成绩代理：同 run 冻结圈宇宙的末圈 `position`，先按完成圈数降序、再按各自末圈位置升序排名；完成圈数占全场 ≥90% 记为完赛代理。不含赛后罚时、DSQ 与 FIA 最终修订，禁止称为 FIA 最终分类。
2. 表现侧 = 车辆共同基线 + 车手兑现偏差（秒）；上下文侧 = 策略/赛道位置代理 + 未解释残差。二者直接取自 Dossier 已发布的加性分解，覆盖范围随分解只含 reporting 车队车手，不是全场车手榜。
3. 车手成对倒挂解释级联（顺序固定）：任一方未完赛代理 → 上下文代理差 ≥ 配速差（容差 0）→ 配速差 ≤ 0.05 s/圈 × 全场圈数的噪声地板 → 其余记为 `unexplained_inversion`。
4. 清洁场判据：非绿旗圈占比 ≤5%、退赛代理 ≤2 人、未解释慢圈占比 ≤2%。
5. 验收规则：清洁场必须 0 个 `unexplained_inversion`；未达标场次进入迭代清单，只能通过改进模型或新增可识别解释来消除，禁止调阈值清零。非清洁场只输出描述性审计，不参与"无意外应匹配"的验收。
6. 跨 Stint 可比分组沿用 v16 冻结门：同配方、参考胎龄差 ≤2 圈、阶段中点差 ≤0.2（等于两个冻结基线 `lap_fraction_bin`）、双方自身门通过；输出配对 Δp\*、Δβ̂ 与低/高燃油情景符号稳定性。不同配方或胎龄差过大的组合一律不比，不做外推。
7. 输出为追加式 records（逐场 JSON/MD、赛季汇总、manifest，全部含输入 SHA-256 身份），禁止覆盖；对 v15 记录自动降级为 `not_available_schema_before_v16` 的比较表状态。

工作台的“真实可比窗口”只实现上述第 6 条冻结门，不另造阈值。对每个配方，
合格 Stint 按阶段中点、参考胎龄、代表配速稳定排序，再 first-fit 到加入后仍满足
组内 `max(age)-min(age)≤2` 且 `max(phase)-min(phase)≤0.2` 的第一个窗口。
这种 max-min 约束保证窗口内任意两行都满足门；每个 Stint 只进入一个窗口。
窗口形成后才以其中最小代表配速作为置顶显示值基准，并只在多行窗口展示相对差值。
单行窗口明确为“无直接比较对象”；无效状态、v15 旧版回退或缺失 v16 代表配速
的行 fail-close 到审计区，不得成为基准。

状态：协议实现与合成反例已纳入全量 `pytest`，最新为 219/219 通过；工作台已在
2025 Abu Dhabi v16 单场 pilot 上得到 SOFT/MEDIUM/HARD 为 1/4/6 个不重叠窗口，
并通过真实浏览器置顶检查。70 场 v16 正式运行及其追加式 concordance 产物仍未
完成，因此本节的真实窗口验证范围仍限单场 pilot。

## 2026-07-26：同配方跨车队双曲线展示协议

工作台在 v16 真实可比窗口之上增加“轮胎衰减 / Stint 条件配速”双曲线。该层只消费
`stint_dossiers` 已发布的 \(p^\star\)、\(x^\star\)、\(\hat{\beta}\) 和胎龄支持区间，
不读取逐圈原始点，也不在浏览器重新拟合模型。除上一节窗口门外，曲线还必须满足：

- 同一窗口至少包含两个不同车队；
- `representative_tyre_age_pace_status=identified_conditional_proxy`；
- \(\hat{\beta}\) 有限且胎龄支持区间有效；
- 所有入图 Stint 存在非空的共同胎龄支持区间。

对 Stint \(s\) 的支持区间 \([a_s,b_s]\)，共同绘图区间和评价胎龄定义为：

\[
a=\max_s a_s,\qquad b=\min_s b_s
\]

\[
x_c=\operatorname{clip}
\left(
\operatorname{median}_s(x_s^\star),
a,
b
\right)
\]

若 \(b\le a\)，整个窗口 fail-close，不允许通过外推补线。两张图使用同一个
\([a,b]\) 和 \(x_c\)：

\[
\hat p_s(x)=p_s^\star+\hat{\beta}_s(x-x_s^\star)
\]

\[
\Delta\hat p_s(x)=\hat{\beta}_s(x-x_c)
\]

其中 \(\hat p_s(x)\) 是同油量和可观测条件下的线性代表配速代理；
\(\Delta\hat p_s(x)\) 在共同评价胎龄归零，只用于比较斜率。条件配速最快/最慢分别
取 \(x_c\) 上的最小/最大 \(\hat p_s(x_c)\)，衰减最慢/最快分别取最小/最大
\(\hat{\beta}_s\)。所有极值必须标成“点估计”，不能解释为赛车绝对快慢、物理磨损
或车手保胎能力。

燃油敏感性数组当前只保存已排序后的配速区间和斜率区间，没有保存 low/base/high
情景的成对曲线参数，因此前端不拼接燃油 ribbon。只有极端对象的斜率燃油区间与
所有对手分离时，才可写“燃油情景排序分离”；存在重叠时必须显示“排序未确认”。
真实燃油、胎温胎压、磨损量、损伤和动力状态继续保留为不可识别。

当前发布 JSON 不含 `pace_adjusted_lap_duration_s`、`analysis_weight`、逐圈资格状态
或 quadratic/log/Kalman 预测点，所以本功能只能称为“样本支持内的加权 Theil–Sen
线性投影”，不能称原始逐圈数据曲线或已选非线性模型曲线。若后续需要逐圈散点和
确认模型曲线，必须在新版本追加带 SHA-256 身份的 curve sidecar；不得覆盖既有
v16 pilot。

## 2026-07-26：Race Dossier v17 逐圈散点、冻结回归与条件平衡

v17 以追加式 `stint_curve_evidence` sidecar 落实上一节的升级要求，不覆盖 v16。
旧图中的直线不是平均速度，也不是逐圈观测曲线；它只是由 v16 的
\(p^\star,x^\star,\hat\beta\) 在浏览器内采样得到的线性投影。v17 改为由后端一次性
发布逐圈散点和冻结拟合，浏览器不得重新估计系数。

每个配置内 reporting team 的 Stint sidecar 发布：

- 原始圈时、条件修正圈时、low/base/high 燃油修正敏感性、分析权重、样本外残差、
  拟合残差、资格状态、排除原因、交通/旗语/进出站/质量状态；
- 与 v16 完全相同的加权 Theil–Sen 居中方程、实际胎龄支持域及预测网格；
- 加权经验残差中间 80%，投影到共同评价胎龄后称
  `weighted_empirical_central_80_s`，不是新圈预测区间；
- 逐次删除连续三圈后重拟合得到的中间 80% 稳定性带，不是置信区间；
- low/base/high 三种具名燃油修正敏感性拟合及其包络，不是统计区间或真实油量；
- 只有既有 ordered selection/confirmation 已通过时，才发布确认后全段描述性
  quadratic/log/Kalman 形状；它不回写样本外确认误差，也不取代主线性代理。

置信区间与新圈预测区间的校准协议仍未冻结，字段必须保持 `NOT_TESTED`。真实燃油、
胎温胎压、物理磨损、天气、赛道温度、损伤、SOC、动力模式和车队指令仍不可识别。

### 可观测条件平衡门

v16 的同配方、参考胎龄差和比赛阶段门不足以支持直接排名。v17 对每一对 Stint
同时比较原始圈分布与分析权重分布，并取更差的差异。冻结门为：

- 同场、同 session、同配方，双方 Stint 自身有效；
- 参考胎龄差不超过 2 圈，阶段中位数差不超过 0.20；
- 共同胎龄支持至少 5 圈，较短支持域的重叠率至少 50%，双方 Kish ESS 至少 8；
- 可观测上下文覆盖至少 80%；
- 交通状态分布的 total variation distance 不超过 0.35；
- 非绿旗、进出站边界、低质量圈占比差分别不超过 0.20；
- nuisance 调整中位数差除以同场同配方稳健 MAD；MAD 下限为 0.10 秒。

把各差异除以各自卡钳，并令 \(D\) 为最大标准化差异。只有 \(D\le0.65\) 的配对
进入近条件直接比较；\(0.65<D\le0.90\) 只作平衡警告审计；\(D>0.90\) 为
`not_comparable`。共同评价胎龄取双方参考胎龄均值并夹在共同支持域内，后端直接
发布双方点估计、秒差、经验范围、删块稳定性范围和燃油敏感性范围。

因此工作台分成两层：A 层只对 `status=comparable` 的配对做直接快慢比较；
B 层保留全部散点、条件模型和不可比原因，用于解释“数据为什么不能直接比较”，
不得把模型外推重新包装成 A 层排名。该平衡门只约束已观测代理，不等于识别真实
天气、赛道温度、策略意图或赛车状态。

## 2026-07-30：参考分析复刻实验室 v1 · F1pace Race pace pilot

本实验室是 Race Dossier 之外的隔离参考层，首个真实 pilot 为 2025 Abu Dhabi Race，
运行身份为 `20260730T154800+0800-f1pace-abu-dhabi-v1`。它保留两个不可混合的
视图：`visual_replication` 忠实实现 F1pace 的公开口径，用于语义、布局和数值复核；
`audited_analysis` 复用 F1 TR 的覆盖、Stint、事件和可比性边界，当前只发布
`audit_only`，不把全局均值包装成车辆/车手因果排名。

### F1pace 公开口径与可复核实现

- 删除首圈、进站圈和出站圈；保留绿旗/黄旗有效圈，湿胎圈不因胎面而删除；按车手
  均值排序，并展示 Q1/Q3、均值、逐圈点、Stint 横向 jitter 和停站次数；Stint 内
  的横向位置不编码第二个数值轴；
- 交通逐圈单元从逐点 `distance_to_driver_ahead` 与速度/时间权重计算，而不是用
  lap-level median gap 代替比例。每个点的间隔权重取相邻采样时间差，最后一个点
  延伸到该圈 `lap_duration`；前车距离不超过 `speed_kph / 3.6 * 2` 才计入 2 秒内，
  一圈比例严格大于 `1/3` 才标记 `in_traffic`，回退车也保留；
- pairwise 矩阵采用左车手均值减底部车手均值，负值表示左车手更快；矩阵只作描述性
  复刻，所有反向单元必须满足反对称性；
- `qualifying_analysis` 与正赛完全独立。当前冻结排位集没有 Q1/Q2/Q3 阶段标签，
  只展示“整场最佳准确推圈代理”，并标记阶段不可识别。

### F1pace v1 计算函数与门控

`research/reference_analysis_lab/f1pace_pilot.py` 固化圈宇宙、Stint 边界、时间权重、
交通比例、pace 汇总、pairwise 矩阵和审计伴随结果。公开复刻的资格集合为
`lap > 1 AND green_or_yellow AND NOT pit_boundary_lap`；`deleted` 与
`is_accurate` 作为标记/审计字段保留，不能为贴近参考均值而手工调整。交通分析的
圈表保留首圈和进出站边界，因此交通图与 Race pace 汇总的圈集合有意不同。

2025 Abu Dhabi pilot 的圈账本为 1,156 圈宇宙、1,082 个有效配速圈、首圈排除 20、
Stint 边界排除 54、非绿/黄旗排除 0、`deleted` 标记保留 38；逐点正赛样本
784,424 个，有效配速圈关联样本 728,315 个。20 名车手全部覆盖，公开均值逐项复核
20/20 通过，最大绝对差 `0.000491s`，容差 `0.001s`；不采用手工调值。pairwise
矩阵 190 对，反对称性通过。

审计伴随只发布每名车手的有效圈、停站、Stint、配方、胎龄、交通时间占比和 pair
失败原因；不同停站次数、配方、胎龄、交通和比赛阶段导致全局均值不具因果可比性，
所以不存在 `audited_analysis` 全序。真实燃油、SOC、胎温胎压、物理磨损、设定、
损伤、动力模式和车队指令仍不可识别；燃油、交通和赛道演化不得写成无条件因果结论。

独立产物位于 `research/records/reference_analysis_lab_v1/f1pace/`，前端仅消费带
SHA-256 的 JSON/manifest；参考原图只作为语义和布局核对证据，不作为产品资产。原作者
页面与许可证边界记录在同一 run 的 `method_card.md`，方法不闭源部分不声称一比一复现。

## 2026-07-30：参考分析复刻实验室 v1 · DeltaData 方法等价 pilot

DeltaData 目标的 X profile HTML 在本地缓存中返回 HTTP 200，profile SHA-256、两个公开
帖子和参考图哈希均已冻结；公开可见语义包括 clear-air race pace、H2H 百分比、代表圈
样本披露和车队/车手对象。精确选圈、燃油修正、胎衰拟合、百分比归一化和代码没有公开，
因此目标状态为 `METHOD_EQUIVALENT_ONLY`，一比一方法状态为 `SKIPPED_OPAQUE_METHOD`，
不是把闭源部分猜成已复现。

真实 pilot 复用 v17 Abu Dhabi `stint_curve_evidence.json` 的 19 个 Stint、464 个
sidecar 点。clean-air pace proxy 从 `traffic_state=clean_air` 且 `quality_ok` 的
真实点中计算，权重使用 v17 已冻结的 OOF/cross-fit reliability `analysis_weight`；
每个 Stint 另报观察点、质量拟合点、clean-air 点和 Kish ESS。燃油展示使用 v17
`primary_fit.fuel_scenarios.low/base/high`；胎衰展示使用对应 `slope_s_per_tyre_lap`。
这些均是条件敏感性/观察代理，不是真实油量或物理磨损。

视觉视图按车队/车手给出描述性条件顺序，但行内必须披露 Stint、配方、样本量、clean-air
占比和 ESS，不能跨配方/胎龄/比赛阶段改名为因果排名。审计视图复用 v17 门：同场同
session 同配方、参考胎龄差不超过 2、共同支持至少 5 圈、双方 Kish ESS 至少 8、上下文
覆盖至少 80%、交通 TVD 不超过 0.35；只有 `status=comparable` 的成对 direct delta
可以发布，其余保留 `null` 与门控原因。Abu Dhabi 19 Stint 共 72 对：4 对可比、2 对
`audit_only_balance_warning`、66 对 `not_comparable`，审计视图不形成全序。

本目标的产物、参考哈希、方法边界和单次网络/闭源处理证据位于
`research/records/reference_analysis_lab_v1/deltadata/run=20260730T235900+0800-deltadata-abu-dhabi-v1/`；
前端页面为 `delta-data-pilot.html`。真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、
动力模式和车队指令仍不可识别。

## 2026-07-31：F1pace 交通热图倒推 v2

v1 的 `distance_to_driver_ahead / self_speed` 是冻结 feed 的逐点实现，但无法覆盖
公开口径明确包含的回退车，也没有逐格外部验收。v2 采用追加式身份
`20260731T120000+0800-f1pace-reverse-engineered-v2`，不覆盖 v1，并把交通算法拆成
两个不可混合的视图：

- `audited_analysis`：把所有车手统一到 `lap_start_raw + time` 的绝对时间轴，以
  `lap - 1 + rel_distance` 建立单调累计赛道相位；每个目标样本寻找圆周方向最近的
  物理前车，再在该车累计相位轴上反插值其上一次通过目标车当前位置的时刻。
  `crossing_headway <= 2s` 的逐点区间进入交通时间，分母始终是整圈全部可观测时间；
  无法重建的区间不被重新归一化，而是同时进入 `missing` 账本。该结果不使用参考图。
- `visual_replication`：只使用三族有物理语义的比例——冻结 feed 的
  `distance/self_speed`、上述同位置 crossing headway、圆周最近前车弧长/ahead speed；
  每族固定 1.5/1.75/2.0/2.25/2.5 秒五个阈值，连同 `position`、`missing`、
  `lap_frac` 共 18 个特征。模型固定为 `StandardScaler + HuberRegressor`
  (`epsilon=1.5, alpha=0.1`)，不使用树模型、车手身份、图像坐标或事件标签。

参考图数值不是原作者导出。v2 从 22 个图内明文百分比标签校准单元背景绿色通道，
读图器自身 MAE `0.473pp`、最大误差约 `1.50pp`；`>90%` 文本删失不被冒充为精确
原值。阿布扎比 1,156 单元只用于拟合；交叉验证按整名车手 `GroupKFold`，避免同一
车手相邻圈泄漏。卡塔尔 1,067 个真实单元作为事件外验证，模型零重拟合、零阈值调整：
MAE `2.238pp`、RMSE `5.723pp`、P90 `6.848pp`，`>33%` 交通圈判定准确率
`99.250%`。纯物理 crossing 模型的卡塔尔 MAE 为 `2.498pp`；稳健校准的主要收益是
压低尾部误差和错误阈值判定，而不是逐格强贴参考图。

原作者精确前车定义、异常圈规则、采样权重和源码未公开，因此发布状态为
`METHOD_EQUIVALENT_EXTERNALLY_VALIDATED`，一比一状态仍为
`SKIPPED_OPAQUE_METHOD`。首圈、进出站、事故/退赛末圈和速度积分相位误差会形成
长尾；参考图本身也不能作为无误差真值。交通比例只作描述性观测，不识别燃油、轮胎、
车辆或车手因果效果。

## 2026-07-31：DeltaData clean-air pace 倒推 v2

追加式运行 `20260731T153000+0800-deltadata-reverse-engineered-v2` 把 v1 的
v17-sidecar 展示升级为逐点交通、公开燃油口径和自动代表圈模型。公开方法证据来自
`@DeltaData_` 原始 X 身份和第三方搜索镜像快照：可确认 2026 方法帖披露
`0.032 s/圈`、逐圈人工复核、只用代表性 clean-air 圈、优先同配方与相近赛道阶段，
并按样本量和质量调整赛季权重；精确交通阈值、人工标签、回归代码和权重公式仍未公开。

### clean-air 候选与代表圈

逐点交通复用 F1pace v2 的纯物理 companion。对目标车在绝对时间 \(t\) 和累计赛道
相位 \(p\) 上寻找圆周方向最近前车，并反插值前车上一次通过目标车当前位置的时间；
一圈的时间权重交通比例为：

\[
R_i =
\frac{\sum_j \Delta t_{ij}\mathbf{1}(h_{ij}\le2\text{s})}
{\sum_j \Delta t_{ij}} .
\]

本实验把 `R_i <= 0.20` 冻结为“至少 80% 可观测时间处于 clean air”的操作定义。
这是对公开语义的可解释反推，不是原作者公开阈值。首圈、进出站边界、非绿/黄旗、
`deleted`、不准确圈、缺失配方、不可重建交通和超过阈值分别进入互斥排除账本。
不能使用 lap-level median gap 代替逐点时间比例。

对具名燃油折损 \(\rho\)，圈时先变换为：

\[
y_i=t_i+\rho(l_i-1).
\]

视觉主情景固定 \(\rho=0.032\text{s/圈}\)。另保留 v17
`0.031034/0.051724/0.075862 s/圈` 三个压力情景；它们都不是真实油量重建。
条件矩阵包含车手、配方、胎龄及二次项、`lap_fraction` 及二次项、配方×胎龄项。
以 `clean_air_share² × telemetry_coverage` 为非负样本权重，拟合
`HuberRegressor(epsilon=1.5, alpha=0.01)`：

\[
\min_{\beta,c}\sum_i w_i L_{1.5}
\left(\frac{y_i-c-x_i^\top\beta}{\sigma}\right)
+0.01\lVert\beta\rVert_2^2 .
\]

`HuberRegressor.outliers_` 仅标记自动下权异常圈；它是对不可获得人工逐圈复核的
方法等价替代，不能称为恢复了作者的人工标签或故障原因。每名车手至少 3 个 clean-air
候选才进入视觉模型；低于门槛者显式保留为 `INSUFFICIENT_CLEAN_AIR_SAMPLE`。

### 公共条件投影、H2H 与审计门

视觉层把每名车手投影到全场 Huber 代表圈的同一条件分布，按软权重平均预测圈时；
车队值为两名有效车手等权均值。H2H 百分比定义为：

\[
\Delta_{A,B}^{\%} =
100\frac{\hat t_A-\hat t_B}{\hat t_B},
\]

因此负数表示左侧车手更快。该顺序仅用于 `visual_replication` 方法效果复核。

`audited_analysis` 另行 fail-close。双方至少 6 个代表圈、Kish ESS 至少 6、存在
共同配方、共同胎龄跨度至少 5 圈、比赛阶段范围重叠率至少 50%、加权交通均值差不超过
0.10，才发布 `audited_left_minus_right_pct`；否则该字段为 `null` 并保留门控原因。
无论通过多少同队子集，都不从这些配对生成全场总序。

### 数值反推证据

公开 Mercedes H2H 图的 11 行由人工数字转录并冻结图像哈希。本地 2026 freeze 只覆盖
其中 Japan Race、Miami Sprint、Miami Race。模型给出的 ANT−RUS 分别为
`-0.242173%/-0.178253%/-0.643100%`，参考值为
`-0.131%/-0.270%/-0.572%`；三场 MAE `0.091340pp`、RMSE `0.092794pp`、
最大绝对误差 `0.111173pp`，方向 3/3 一致。由于这些参考值在模型家族选择时已经被
看到，该结果只能称 `reference-informed reverse-engineering benchmark`，不能称盲测、
零重拟合外部验证或一比一复现。

2025 Abu Dhabi pilot 使用 784,424 个真实逐点样本，形成 1,156 圈宇宙、411 个
clean-air 候选和 320 个 Huber 代表圈；20 名车手中 19 名进入模型，HUL 因不足三圈
显式不排名。10 个同队配对中仅 Alpine、McLaren、Racing Bulls、Red Bull Racing
四对通过审计门，其余 `audit_only`。真实燃油、SOC、胎温胎压、物理磨损、设定、
损伤、动力模式、车手管理意图和车队指令仍不可识别。

## 2026-07-31：FDataAnalysis 赛道特征算法倒推 v2

追加式运行 `20260731T170000+0800-fdataanalysis-reverse-engineered-v2` 使用真实
2025 Abu Dhabi Race 逐点遥测，不覆盖 v1，也不把社交图表混入 Race Dossier
真相层。公开材料能够确认速度区间、弯角入口/最低/出口、制动位置、直道加速、
最高速、全油门比例、胎龄曲线和同队双车画像等图表语义；作者的精确分段锚点、
选圈、平滑、聚合和异常规则未公开，因此状态为
`METHOD_EQUIVALENT_INTERNALLY_VALIDATED`，不是一比一代码复现。

### 统一距离轴与动态弯角代理

每个有效圈先按 `rel_distance` 去重并单调化，再线性插值到 1,000 个等距区间
（1,001 个端点）；连续信号使用线性插值，`gear`、`DRS` 等离散信号使用最近邻。
以所有有效圈的中位数横向加速度绝对值构造赛道模板，先用
`sigma=0.003 lap` 的高斯核平滑，再检测高度至少 `6 m/s²`、prominence 至少
`8 m/s²`、间距至少 `0.01 lap` 的峰。边界由峰两侧横向加速度阈值和制动证据动态
扩展，编号 `P01…Pn` 只是冻结算法生成的弯角代理，不冒充官方弯号。800/1,000/1,200
区间的敏感性运行均检测 16 个峰，16/16 在 `0.015 lap` 内匹配，最大漂移
`0.001 lap`。

每个 driver×lap×proxy 发布入口速度、窗口最低速度、出口速度、持续制动起点、
油门恢复点和区间积分时间。直道只在相邻弯角代理间且多数距离为全油门时发布，
并计算最高速、P95、全油门距离比例、DRS 距离比例和公开图线索中的
150–250 km/h 每 100 m 加速斜率。圈时重建使用

\[
\hat t=\sum_k \frac{\Delta s_k}{\max(v_k,1)/3.6}.
\]

1,082 圈的重建 MAE 为 `0.154728s`，P90 绝对误差为 `0.304797s`，通过
`MAE<=0.25s AND P90<=0.50s` 门。

### 忠实统计与审计 companion

- 视觉最高速按公开线索取车手全部原始速度样本最高 15 个的均值；审计 companion
  取逐圈 P99 后再取中位数。两者车手级 MAE 为 `22.040524 km/h`，来源是采样密度
  和统计定义，不是数据损坏。
- 视觉全油门比例为 `throttle>=99` 的样本占比；审计 companion 按相邻采样时间
  加权。两者车手级 MAE 为 `0.308727pp`。
- 视觉胎龄曲线保留原始逐圈 OLS；审计曲线只在单 Stint 内使用 Theil–Sen，并要求
  交通比例不高于 20%、准确且未删除、至少 6 圈、Kish ESS 至少 6、胎龄跨度至少
  5 圈。圈时先分别应用公开 `.032s/圈` 与 v17 low/base/high 具名燃油情景。
  所有斜率都是“在该条件代理下随胎龄变化”，不是物理磨损率。
- 同队双车通过匈牙利一对一匹配：同配方、胎龄差不超过 2 圈、比赛阶段差不超过
  `0.12`、交通比例差不超过 `0.15`；匹配后仍要求至少 6 对和 ESS 至少 6。
  10 对车队组合中只有 Alpine 24 对、Racing Bulls 10 对通过，反向结果严格取负，
  最大反对称误差为 0。其余只发布 `audit_only`，不形成车辆总分或全序。

参考图来自 2026 Hungarian Race/FP2，而 pilot 是 2025 Abu Dhabi Race，故数值状态
固定为 `NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION`；不以跨事件差异计算 MAE，也不
据此调参。观测到的速度、油门、制动和挡位特征不能直接命名为真实下压力、引擎功率、
阻力、设定、损伤、SOC、动力模式或物理轮胎磨损。

## 2026-07-31：F1TelemetryData 同场图表包倒推 v1

追加式运行 `20260731T193000+0800-f1telemetrydata-reverse-engineered-v1`
使用真实 2025 Abu Dhabi Qualifying/Race，复刻公开频道帖子 3015–3034 中 13 类图表
语义。公开材料能确认图名、同场结果和视觉定义，但未公开精确代码、控制阈值、平滑、
插值、选圈和 feed 版本，因此发布状态为
`METHOD_EQUIVALENT_REFERENCE_BENCHMARKED`，一比一状态保持
`SKIPPED_OPAQUE_METHOD`。

### 排位阶段身份与统一距离轴

当前仓库排位冻结集仍没有 Q1/Q2/Q3 标签。本实验不改写原冻结集，而是冻结 OpenF1
`race_control`，对每个阶段取 `GREEN LIGHT - PIT EXIT OPEN` 到
`CHEQUERED FLAG` 的 UTC 时间窗。准确推圈的起点落入窗口，或在绿灯前起步但跨过绿灯，
才获得阶段标签；窗口外返回 `null`，不以最近阶段补齐。Abu Dhabi 88 个准确推圈全部
识别：Q1 42、Q2 28、Q3 18。

前三名最快圈按 `rel_distance` 去重并线性插值到 500 个等距区间。速度、油门、累计用时
共享距离轴。公开的 sector time 可用于反推“时间端点在本地轨迹上的距离分数”，但该
端点不是官方赛道几何坐标，因此固定标记
`NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR`。Track Dominance 定义为共同距离 \(s\)
处累计用时最小的车手：

\[
d(s)=\arg\min_i t_i(s).
\]

它只描述这三条观测圈在当前距离轴上的累计领先者，不命名为真实车辆、空气动力或动力
单元优势。

### 控制输入互斥分类

原始 OpenF1 car channel 冻结覆盖 20 位车手最快圈、6,319 个样本，文档采样率约
3.7 Hz。视觉复刻层对每个样本采用互斥优先级：

1. `brake > 0` 为 braking；
2. 无刹车且 `throttle <= 0` 为 lift；
3. 无刹车且 `throttle >= 99` 为 full throttle；
4. 其余有效油门样本为 partial throttle。

视觉百分比为样本等权；审计 companion 另给相邻时间区间加权值。四类分母严格相同，
同一样本不能重复计数。与公开前六名 Lap Sections 数值相比，24 个单元 MAE 为
`2.639122pp`、最大绝对误差 `6.157325pp`；10 队 Throttle Usage MAE 为
`1.195668pp`、最大 `3.083765pp`。差距来自未公开阈值、采样和 feed 版本，不做
逐车手手工调参。

### 正赛、维修区与发布边界

Race Pace/Average Gap 复用 F1pace 的同场公开选圈身份：首圈、进站/出站边界排除，
保留公开口径允许的删除标记，得到 1,082 个有效圈。全局均值只在
`visual_replication` 中形成描述排序；不同停站次数、配方、胎龄和交通构成使
`audited_analysis.race.status=audit_only`，不形成因果全序。

Tyre Strategies 的本地与 OpenF1 边界为 47/47 一致。Pit Times 严格区分
`lane_duration`（维修区通行时间）与 `stop_duration`（静止停车时间）；27 次通行中
静止时间覆盖 26 次，缺失值不插补。公开图与当前冻结 OpenF1 的车队维修区均值
MAE 为 `0.065808s`、最大 `0.363s`。真实燃油、SOC、胎温胎压、物理磨损、设定、
损伤、动力模式、车队指令、真实下压力和引擎功率仍不可识别。

## 2026-07-31：GP Tempo 官方分段 Delta 公开方法复刻 v1

追加式运行 `20260731T213000+0800-gptempo-reverse-engineered-v1` 实现 GP Tempo
About 页明确披露的三步 Delta 方法。与前四项目不同，本目标不需要从视觉猜测核心算法：
公开页确认数据经 FastF1 来自官方 timing feed、遥测通常约 4 Hz、三段官方 sector
是硬约束、段内距离线性插值、段内时间线性缩放，且只有 sector 端点保证精确。产品状态
因此为 `PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED`，视觉样式仍由 F1 TR 独立
实现，不复制原站 CSS、logo、字体或截图。

### 原始距离代理与分段对齐

公开 car channel 没有直接距离时，先对相邻速度样本做梯形积分：

\[
d_j=d_{j-1}+
\frac{v_{j-1}+v_j}{2\cdot 3.6}(t_j-t_{j-1}),
\qquad r_j=\frac{d_j}{d_{\mathrm{finish}}}.
\]

连续的 speed/throttle 线性插值；离散的 brake/gear/DRS 使用最近邻。每圈三个官方
sector time 记为 \(q_{i1},q_{i2},q_{i3}\)，累计端点为
\(Q_{ik}=\sum_{h\le k}q_{ih}\)。在参考圈 sector \(k\) 的距离点 \(r\) 上先计算
段内比例

\[
u=\frac{r-R^{\mathrm{ref}}_{k-1}}
        {R^{\mathrm{ref}}_k-R^{\mathrm{ref}}_{k-1}},
\]

再把候选圈查询距离映射为

\[
r^{\mathrm{cand}}=
R^{\mathrm{cand}}_{k-1}
+u(R^{\mathrm{cand}}_k-R^{\mathrm{cand}}_{k-1}).
\]

候选与参考原始累计时间分别在上述距离上线性插值。每条圈在每个 sector 内做仿射时间
缩放：

\[
\tilde t_i(r)=Q_{i,k-1}
+\frac{t_i(r)-t_i(R_{i,k-1})}
       {t_i(R_{i,k})-t_i(R_{i,k-1})}
 (Q_{i,k}-Q_{i,k-1}).
\]

最终
\(\Delta(r)=\tilde t_{\mathrm{cand}}(r)-\tilde t_{\mathrm{ref}}(r)\)。
因此 sector 端点必然满足
\(\Delta(R^{\mathrm{ref}}_k)=Q_{\mathrm{cand},k}-Q_{\mathrm{ref},k}\)。
若原始段内时间跨度退化为零，使用首尾线性坡道并记录退化分支，不能除零或静默外推。

### 真实 pilot、条件门与误差身份

真实 pilot 使用 2025 Abu Dhabi 五个最快 Qualifying 圈与五个最快 Race 圈，共
3,192 个原始 car-channel 样本；每圈中位采样率范围 `4.149–4.167Hz`，总体中位
`4.167Hz`。10 圈形成 90 个有序圈对和 270 个 sector 端点，最大绝对端点误差
`0.000000000000s`，90 个终点全部等于官方总圈时差。

默认选择只允许同 session、绿旗、同配方且胎龄差不超过 2 圈；Qualifying 默认
VER/NOR/PIA 三个 SOFT 胎龄 2 圈，Race 默认 PIA/HUL 两个 MEDIUM 胎龄 3/4 圈。
跨 session 仍可交互，但固定显示
`WARNING_CROSS_SESSION_COMBINED_CONDITIONS`，因为轮胎、燃油、天气、赛道演化和
session 目标不可分离。公开方法页没有固定 Abu Dhabi 逐点数值表，所以不伪造段内
MAE；段内状态固定为 `ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ`，只有端点误差
可作精确验收。

## 2026-07-31：参考分析复刻实验室 v3 · 2023–2025 分赛道验证

追加式最终运行
`20260731T235900+0800-track-validation-v3` 把五个单站 pilot 扩展到
2023–2025 的 70 场、24 条赛道。该运行不修改 Race Dossier v14–v17，也不改写
reference-analysis-lab v1/v2；候选模型比较开始前，先按冻结报告哈希重算五个
universal baseline。`visual_replication` 继续只承担公开语义和同场数值再验，
`audited_analysis` 承担无泄漏分赛道验证，两层不共用因果排名字段。

### 外层划分、赛道族群与固定发布门

每个测试事件都从训练事件中完全移除。重复举办赛道的 track 候选只使用同赛道其他
年份；cluster 候选只使用同一可观测族群的其他事件；universal 候选使用其余事件。
China 与 Emilia-Romagna 只有两年覆盖，track 候选 fail-close；可观测轮廓不连续时
也直接禁止 track 候选。F1pace 另把 2025 Abu Dhabi 与 Qatar 从所有训练折全局
排除，避免已看公开参考事件经其他测试折回流。

赛道族群只使用冻结遥测可观察量：

- 中位速度 `<185 / 185–205 / >=205 km/h` 形成 low/medium/high speed band；
- 弯角代理密度 `<2.5 / >=2.5 per km` 形成 sparse/dense；
- 族群名形如 `speed_high__corner_dense`，不是实际下压力、阻力或引擎功率。

Huber 任务按事件等权，单事件训练上限为 F1pace 120 行、DeltaData 100 行，并报告
训练行数、事件数和 Kish ESS。候选只有同时满足下列固定门才可发布：

1. 至少 2 个测试事件；track 至少 3 个年份，cluster 每折至少 6 个训练事件；
2. 相对 universal 的 MAE 至少改善 2%；
3. P90 最多恶化 2%，max 最多恶化 5%；
4. 留出折 MAE 改善比例至少 `2/3`；
5. 覆盖损失与方向一致率损失均不超过 1 个百分点；
6. 最小训练 Kish ESS 至少 20。

任一门失败即依次回退 cluster 或 universal。不同任务的秒、比例、百分点和距离比例
不合成“总复刻率”；PAC、OVR 与跨任务总分保持 `null`。

### A · F1pace

固定语义仍是逐点时间权重的 2 秒车距与严格 `>1/3` traffic lap。模型目标为
`traffic_ratio_2_00s`，特征不包含该目标；使用邻近固定阈值、覆盖、位置和可观测
上下文。70 个 universal 留出折训练 67–68 场、8,040–8,160 行，2025 Abu
Dhabi/Qatar 永不进入训练。70,511/77,720 圈进入模型，赛道级 traffic target
缺失率为 0，排除率为 `0.646%–21.516%`，Kish ESS 合计 70,499。

24 条赛道的 universal MAE 范围为
`0.004595–0.010879 traffic-ratio fraction`，平均 `0.008733`；最坏 P90
`0.033065`、最坏单点误差 `0.523868`。cluster/track 均未同时通过改善和尾部门，
所以 24/24 回退 universal。70 场 pairwise mean delta 的反向值严格取负，
最大反对称误差为 0；它仍是描述性均值差，不是可比 Stint 因果排名。

### B · DeltaData

clean-air 固定为 2 秒交通比例不高于 20%，公开燃油情景固定
`0.032 s/lap`。目标是 v17 `adjusted_pace_s - raw_lap_duration_s`，特征不读取
调整目标、燃油修正目标或 nuisance 总量。31,904 个 v17 点中 14,040 个进入
clean-air 模型；首圈 551、pit boundary 1,977、非绿旗 2,145、低质量 1,820、
交通高于 20% 11,371 个被互斥记账。赛道级字段缺失率为 0，条件排除率为
`42.357%–72.408%`，Kish ESS 合计 `13,707.598`。

最终发布 6 个 track、5 个 cluster、13 个 universal：

| 发布层 | 赛道 |
| --- | --- |
| track | Austria、Bahrain、Belgium、Mexico City、Singapore、Spain |
| cluster | Abu Dhabi、Australia、Netherlands、Las Vegas、Miami |
| universal | 其余 13 条 |

11 个专用模型的留出 MAE 相对改善范围为 `3.16%–60.90%`；例如 Abu Dhabi
cluster 从 `0.080384s` 降到 `0.070286s`，Spain track 从 `0.138392s`
降到 `0.076033s`。个别 max 可在门内轻微变差，因此页面同时显示 MAE、P90、
max、方向、覆盖与 ESS，不以单一均值掩盖尾部。衰减方向只在单 Stint 内检查；
同队方向只在同队同配方支持内检查，不允许不可比车队形成全序。

### C · FDataAnalysis

每场用 12 个代表圈独立构建 0–1 距离轴。连续通道线性插值、离散通道最近邻；
800/1,000 轴密度和平滑候选只在训练事件按“先最坏峰位、再 P90”选择，测试事件
不参与选择。70 场 track profile 事件缺失率为 0。universal 峰位 MAE 范围
`0.000238–0.000382 lap fraction`，最坏 P90 与 max 均为 `0.000833`，
所有起终点误差为 0。

24/24 保留 universal：cluster/track 没有获得稳定的折级改善。Dutch 2023 对
2024/2025 的弯角匹配比例为 `0.5000/0.4375`、速度轮廓相关为
`0.3348/0.3282`，因此标记
`OBSERVABLE_LAYOUT_DISCONTINUITY_PROXY` 并禁止 track 模型；该名称不是官方布局
变更判定。

### D · F1TelemetryData

七类任务分别保留 universal/cluster/track 三行，共 21 行。固定的 braking/lift/
full/partial 互斥优先级、99% 全油门阈值、采样口径、`lane_duration` 与
`stop_duration` 区分均不为追数而改变。只有 2025 Abu Dhabi 有冻结同场公开图：
已冻结的 universal 数值仍为 Lap Delta MAE `0s`、控制分段 `2.639122pp`、
车队油门 `1.195668pp`、Average Gap `0.000216s`、最快圈 `0s`、pit lane
`0.065808s`。

其余 69 场没有同场公开图，不以跨事件差异伪造误差。Q1/Q2/Q3 虽有单站 OpenF1
阶段补充，但没有跨事件冻结数值真值；其 universal/cluster/track 均明确
`NOT_TESTED`，MAE/max 保持空值。24/24 registry 保留 universal，且
reference-informed 不称 blind holdout。

### E · GP Tempo

2025 Abu Dhabi 保持 401 点、连续线性/离散最近邻与官方 sector 内仿射时间缩放；
270 个官方 sector 端点最大误差严格为 `0s`。其余 69 场缺冻结官方 sector times
与 car channel；全部 70 场缺公开逐点真值。因此 cluster/track 不测试，
segment MAE 始终为 `null/NOT_TESTED`，24/24 registry 保留 universal。真实燃油、
SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力、引擎功率
与赛道演化因果效果仍不可识别。
