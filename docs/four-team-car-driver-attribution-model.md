# 四大车队车辆基准、车手提取与人车归因模型规范

> 文档状态：**主规范；M0/M1、静态分层贝叶斯复验、M2 单场/stint 描述层、v5 全量逐站和 v6 可解释深度层已实现，动态 M2、完整 M3–M5 尚未完成**。本文定义研究问题、识别边界、模型结构、数据协议、输出合同和验收标准；已跑出的数值只维护在 [`implementation-results.md`](implementation-results.md) 和机器结果中，数据真相源见 [`database.md`](database.md)。

**初始登记日期：** 2026-07-18  
**首期报告对象：** McLaren、Red Bull、Ferrari、Mercedes  
**识别数据范围：** 原则上使用全场车手和车队建立连接网络，只将首期结论限制为四大车队  
**旧规训练域：** 2023–2025  
**新规迁移域：** 2026，并显式区分赛季内规则断点

## 0. 当前实施快照

截至 2026-07-20 已完成可复现基线、逐站报告与回顾性解释扩展：

- 配置：`research/configs/four_team_2025_baseline_v1.json`；运行入口：`research/run_four_team_baseline.py`。
- 基准选择只查看冻结数据覆盖，不查看模型结果：按“四队双车完整 → 最大化最少车手有效圈 × (1-CV)”审计 2025 Race，锁定 `Hungarian Grand Prix`。
- 2024 Race 全场数据学习车手 ridge 先验；2025 匈牙利站由全场 10 队、20 位车手联合更新单站车辆状态，只发布四队结果。
- 单站车手固定效应与人车交互不可分，因此使用差异化 ridge：车辆状态较弱收缩，车手当站更新较强收缩；后者只能解释为“当站车手/组合代理”，不能发布为稳定适配结论。
- 车辆包络使用上下文标准化后、固定参考车手分布的 P10/P50/P90；区间来自车手 → stint → 圈的 300 次分层 bootstrap，不是假装成贝叶斯后验。
- 已输出车辆基准、车手提取和有符号 `V_pace` 组件；策略、可靠性和积分组件明确为未建模，而不是填零。
- OpenF1 圈速代理和 FIA Doc 44 最终分类只作为外部方向性核验，均不进入拟合或调参。

三赛季 M1 基线：

- 配置：`research/configs/four_team_2023_2025_seasonal_v2.json`；入口：`research/run_four_team_seasonal_attribution.py`；全流程离线。
- 2023–2025 计划 70 场 Race，61,351 个本地干地有效圈覆盖其中 69 场；2024 São Paulo 为湿地主头排除。全场 10 队参与识别，只发布四队。
- `team-year / team-circuit / driver / driver-year / driver-team` 分块收缩；赛后描述头加入 `team-event / driver-event`。每个分站—车手等权，并用 Huber IRLS 限制极端圈和极端队友组合杠杆。
- 2024 时间外分站等权 MAE 选择惩罚；候选落入距最优 0.01 的实用等价带时按预注册顺序选中央先验。2025 后 19 站是锁定测试，不参与选参。
- 2025 `joint_robust` MAE 为 0.8105，优于 context 的 0.8881；用 2024 时间外残差校准后，80%/95% 覆盖为 76.2%/94.2%，中间区间仍偏窄。
- 40 次分站簇 bootstrap 与惩罚敏感性只稳定支持 2023 Red Bull 第一/McLaren 第四、2024 Red Bull 第四、2025 McLaren 第一；其余中间顺序或 2024 领先者不得发布为高置信精确全序。
- 不以队友差归一化，不输出车辆/车手百分比分摊。本地 Live Timing 分类代理只作构念检查，不含赛后 DSQ、罚时和 FIA 最终修订。
- 2026 只迁移车手基础能力、残差尺度和适配协议；车辆、赛道响应、人车交互、能量和空气动力状态全部重置。本地 4 场 Race 尚未达到 5 站适配门槛。

三赛季静态贝叶斯反事实复验：

- 配置：`research/configs/four_team_2023_2025_bayesian_v3.json`；入口：`research/run_four_team_bayesian_attribution.py`；输入仍是上述本地冻结圈速，`network_calls=[]`。
- 先由无身份上下文头去除轮胎、赛道和圈次阶段，再用全部清洗后圈构造 1,324 个车手—分站 Huber 聚合观测；没有把赛果、积分或队友差作为标签。
- `team-year / team-circuit / team-event`、`driver / driver-year`、`driver-team` 在同一个分层高斯模型中联合抽样，而不是两步先算车手再机械相减。
- 纯车辆输出固定 `driver + driver-year + driver-team=0`；纯车手输出固定为四队等权参考赛车且 `driver-team=0`；适配后验单列。主运行 4 链、每链 600 个留存样本，最大 R-hat=1.014。
- 2025 聚合层 MAE 从零模型 0.7174 降为 0.5816；原始 80%/95% 区间覆盖仅 70.9%/86.8%，只用 2024 时间外误差校准后为 80.2%/94.0%。概率输出必须带校准状态。
- 该模型明确暴露而未消除弱识别：VER 纯车手优势在两种合理交互先验下为 +0.418 至 +0.643 z；删除不同 Red Bull 车手会使 2025 纯车辆优势漂移 -0.303 至 +0.198 z。因此不能发布脱离先验敏感性的唯一真值。

单场—stint—圈级 v4 描述扩展：

- 配置：`research/configs/four_team_2023_2025_event_stint_v4.json`；入口：`research/run_four_team_event_stint_attribution.py`；排位冻结入口：`research/build_qualifying_frozen_data.py`。
- v3 后验继续负责长期车辆、车手和适配身份；v4 保留 69 场、25,343 个四队 Race 圈的可加预测/残差/执行损失，汇总出 1,298 个 stint 和每场四队车辆/车手特征。
- 规范 Parquet 冻结出 7,401 个 2023–2025 Qualifying accurate 干地推圈；四队占 3,469 圈、覆盖 67 场。Qualifying 与 Race 分别拟合无身份上下文头，只融合四队 session 内相对上沿，不融合绝对圈时。
- 主车辆上沿采用车手等权的双车中位条件分位数；最佳单车手上沿单列为理论上沿代理。每场使用 240 次车手内 bootstrap，并在 27 组分位数/会话权重边界网格下报告名次范围。
- 公开数据不能验证 setup、升级、损伤和动力模式完全相同；周末融合只标为 `parc_ferme_weekend_family_proxy_not_observed_setup_identity`。分类代理只检查干净圈配速与实际名次是否相容，不进入拟合或因果归因。

全量逐站 v5 与可解释深度 v6：

- v5 把 2023–2025 的 69 个可用分站和 2024 São Paulo 显式缺失统一为逐站合同；2026 只作规则重置后的四站探索。
- v6 不改 v3 身份先验强度，只允许 2024 时间外门控的身份无关 nuisance 改善上下文；traffic 缺失必须披露，不能冒充 clean-air。
- 每站“分明”必须由队间/队内方差比、后验成对置信度和逐队样本门控共同支持；成对记录必须同时保存原始比较方向与后验多数快慢方向。
- 配速模型构念与赛果代理构念必须分表：前者不能称为名次异常，后者只使用不回流训练的 classification proxy 且不作原因归因。
- 理论上限只在 Q/R 各自 session 内估计相对优势；最快圈执行校正的抽样不确定性必须进入最终 CI，仍不得称为物理极限或完全同调教。

`research/run_car_driver_envelope.py` 的 Spain 逐点遥测包络探索负责 mini-sector 机理描述。它只使用四队同队前沿，未实现全场识别网络，因此不能替代三赛季 M1，也不能视为 M3 已验收；后续应把 M1 的人车先验与该组件的赛段 Δt 分解连接起来。

## 1. 任务目标

本模型不以再次展示圈速、delta 或原始遥测曲线为主要目标，而是建立一个可以回答以下问题的统计研究体系：

1. 在排除车手个体能力差异后，四大车队车辆自身的标准化基准性能、性能上沿和可用窗口分别是多少？
2. McLaren、Red Bull、Ferrari、Mercedes 的车辆差距有多少，具体发生在重刹、低速、中速、高速、牵引、直线或轮胎维持的哪些环节？
3. 在排除车辆基准差异后，各车手把所驾驶车辆发挥到性能包络的什么位置？
4. 最终圈速、完赛结果和积分优势中，车辆、车手、人车适配、策略、可靠性和事件分别贡献多少？
5. 2023–2025 学到的车手能力与控制策略中，哪些可以迁移到 2026；2026 新车、新动力和规则变化需要重置哪些状态？
6. 2026 新规下，一位车手的旧规优势是否仍然成立，还是人车匹配、能量管理和车辆可驾驭性重新定义了优势？

首期模型不是 VER/PER、NOR/PIA 或任何单一队友对比模型。具体队友组合是模型的证据和验证案例，不能作为车辆基准的定义来源。

## 2. 核心研究原则

### 2.1 两次“排除”通过联合模型实现

产品输出可以先固定车手比较车辆，再固定车辆比较车手；模型估计不能先计算一个确定的车手分数后机械相减。车手、车辆和人车交互必须在同一概率模型中联合估计，否则第一阶段误差会被错误转移到第二阶段。

联合估计完成后生成两个标准化反事实：

- **车辆基准视角：** 将车手能力固定为参考车手分布，比较四辆车。
- **车手提取视角：** 将车辆固定为参考车辆或实际所驾车辆的性能包络，比较车手发挥。

### 2.2 输出限制四队，识别不限制四队

首期只发布四大车队结果，但训练数据原则上保留全场：

- 队友关系提供近似同车比较；
- 车手转会连接不同车队；
- 中游和后排车队帮助固定全场相对尺度；
- 新秀、替补和赛季中换人需要全场层级先验；
- 如果仅保留四队，部分车手—车队二部图会弱连接或断开，车辆与车手归因区间会明显扩大。

全场非四队实体只作为识别和正则化辅助，不在首期排行榜和产品输出中展开。

### 2.3 不预设“特调”“一号车手”或纯物理原因

模型不得将媒体叙事或冠军身份写入先验。以下说法都不能预先假定：

- 某车专门为某位车手设计或调校；
- 某位车手的最快圈等于车辆上限；
- 同队两辆车在规格、损伤和设定上绝对相同；
- 队友差距全部属于车手能力或全部属于人车适配。

公开数据不可见的调校、升级规格、损伤、工程团队差异和策略指令，应进入人车组合效应、赛段随机效应或未解释项，并扩大不确定区间。

### 2.4 “车辆上限”是统计性能前沿

本文所称车辆上限，是在给定公开观测、参考车手分布和模型假设下的条件性能前沿，不是空气动力学、动力单元或轮胎的物理工程极限。

如果车手持续快于模型此前估计的上沿，应更新车辆上沿后验，不能表述为车手突破真实物理上限。

## 3. 研究对象与场景拆分

### 3.1 首期四大车队

模型配置中应使用稳定实体 ID 和版本化别名表，展示名称为：

- McLaren
- Red Bull
- Ferrari
- Mercedes

源数据中的车队名称变化、赞助商前后缀和大小写不得形成新类别。车队实体归一化必须先于模型冻结，并提供别名敏感性实验。

### 3.2 必须分开的能力场景

车辆和车手都不能只输出一个总分。至少分开：

1. 排位低油推圈；
2. 正赛干净空气推圈；
3. 正赛轮胎管理状态；
4. 跟车或脏空气状态；
5. 湿地状态；
6. 不同轮胎年龄区间；
7. 2026 能量受限、回收和部署状态。

Race、Sprint、Qualifying 和 Sprint Qualifying 不共享未经验证的同一模型头。排位模型必须单独定义清洗、场上演化和尝试阶段。

### 3.3 标准赛道篮子

跨车队总评不直接按当年赛历出现频率加权。建立版本化标准赛道篮子，按赛段类型汇总：

- 重刹区；
- 低速弯；
- 中速弯；
- 高速弯；
- 出弯牵引区；
- 长直道；
- 快速方向切换区。

同时保留三种报告口径：

- **标准篮子：** 各赛段类型按预注册权重汇总，用于四队公平横向比较；
- **赛季篮子：** 按实际赛历和有效样本汇总，用于解释当季结果；
- **单赛道：** 报告具体分站的车辆和车手后验。

## 4. 数据协议

### 4.1 真相源

- 逐点遥测：`data/normalized/tracinginsights/schema=tracinginsights-expanded-v4/`。
- 冻结身份：`research/artifacts/*manifest*.json` 中的配置哈希、文件集合和 SHA-256。
- 圈时、sector、stint、compound、天气、赛会控制、位置和间隔：FastF1/OpenF1 冻结快照或由其生成的规范产物。
- 关键赛果核验：FIA Event & Timing 文档；只用于核验，不把人工网页内容作为训练时动态依赖。

### 4.2 分析单位

按阶段逐步推进：

1. **圈级原型：** 先验证车辆、车手、动态状态和执行损失能否被稳定识别。
2. **sector 级：** 将差距定位到官方分段，检查模型方向。
3. **赛段级主模型：** 将遥测按统一赛道相位切分为重刹、弯中、牵引、直道等功能区段。

赛段样本必须保留：

`year / meeting / session / driver / team / lap / stint / segment / timestamp range / source commit / rule regime`

### 4.3 可用观测与限制

主要观测包括：

- 圈时、sector time、圈序、session 阶段；
- speed、throttle、brake 状态、gear、rpm、DRS/可观测空气动力状态；
- x/y/z、distance、相对距离和前车距离；
- compound、tyre age、stint、pit；
- position、interval、track status、race control、weather；
- 车手、车队、赛季、分站和规则阶段。

公开数据不能可靠提供：

- 制动压力、方向盘角度和完整赛车线；
- 真实燃油质量、SOC、能量部署命令和动力模式；
- 胎压、胎温、磨损量和热循环；
- 车辆设定、升级规格、损伤和零件状态；
- 完整车队指令与车手主观目标。

因此所有工程维度只能命名为可观测性能代理，例如“高速响应代理”“牵引响应”“潜在能量可用性”，不能命名为真实下压力、马力或电池电量。

## 5. 模型结构

### 5.1 观测模型

对样本 (i) 的标准化对数时间：

\[
z_i=100\log\left(\frac{t_i}{t^{ref}_{session,segment}}\right)
\]

首期透明模型定义为：

\[
\begin{aligned}
z_i = &\ \alpha_{session,phase,segment}
- C_{team,round,k,m}
- D_{driver,round,k,m} \\
&- I_{driver,team,season,k,m}
+ f_{context}(tyre,weather,traffic,track\ evolution) \\
&+ f_{rule}(regime,energy,aero)
+ u^{execution}_i
+ \epsilon_i
\end{aligned}
\]

其中：

- (k) 为赛段类型；
- (m) 为排位、正赛推圈、管理、跟车、湿地等场景；
- (C) 为车辆基准能力，数值越大表示对缩短时间的贡献越大；
- (D) 为跨车辆共享的车手能力；
- (I) 为强收缩的人车组合效应；
- (u^{execution}_i\ge 0) 为未兑现当前可达水平产生的执行损失；
- \(\epsilon_i\) 使用 skewed-t、Student-t 或污染混合分布容纳未标记异常。

### 5.2 动态层

车辆允许比车手更快的分站间变化：

\[
C_{team,r}=C_{team,r-1}+\eta^{car}_{team,r}
\]

\[
D_{driver,r}=D_{driver,r-1}+\eta^{driver}_{driver,r}
\]

并设置：

- 车辆过程噪声大于成熟车手过程噪声；
- 新秀、替补和换队车手使用更宽层级先验；
- 升级或规则断点允许车辆状态跳变；
- 人车交互允许随适应时间衰减或稳定，但必须由时间外验证选择结构。

### 5.3 执行损失与性能包络

首期优先比较两种前沿实现：

1. 随机前沿：(u^{execution}\) 使用 half-normal 或 half-Student-t；
2. 分层条件分位数：估计 P10/P50/P90 的条件表现。

车辆输出定义：

- **Car Ceiling：** 将执行损失置于接近零或采用条件上沿分位数后的后验；
- **Usable Baseline：** 参考车手分布在高质量有效状态下的中位表现；
- **Performance Floor：** 有效推圈中较差但非事故/非赛会中断状态的条件下沿；
- **Drivability Width：** 从上沿到常态或下沿的性能窗口，以及时间对操作偏差的敏感度。

“下沿”不使用观察到的最慢圈定义，避免把事故、交通和管理状态当作车辆下限。

### 5.4 车辆基准

车辆基准通过对参考车手分布和标准赛道篮子边际化得到：

\[
B^{car}_{team,r,m}
=E_{d\sim D_{ref},\ k\sim W_{standard}}
[C_{team,r,k,m}+I_{d,team,r,k,m}]
\]

参考车手不是某个真实车手，首版采用全场车手能力的零均值层级分布。人车交互默认在参考分布中均值为零，并单独报告其方差和敏感性。

车辆差距至少提供：

- 相对最快车的百分比；
- 标准 90 秒圈的毫秒差；
- 真实赛道的每圈毫秒差；
- 各赛段类型贡献；
- 80%/95% 区间；
- 分站动态曲线。

### 5.5 车手能力与提取率

车手基础能力通过对参考车辆篮子边际化得到：

\[
B^{driver}_{d,r,m}
=E_{c\sim C_{ref},\ k\sim W_{standard}}
[D_{d,r,k,m}+I_{d,c,r,k,m}]
\]

车手发挥不使用“实际圈速 / 理论最快圈”作为物理百分比。主要输出：

- **Ceiling Extraction：** 最佳高质量样本相对当前车辆包络的位置；
- **Typical Extraction：** 正常状态下执行损失的后验中位数；
- **Extraction Percentile：** 在相同车辆和上下文下，相对参考车手执行分布的分位数；
- **Consistency：** 控制上下文后的正常残差尺度；
- **Negative Tail：** 显著低于常态发挥的概率和累计损失；
- **Segment Extraction：** 各赛段类型分别接近车辆上沿的程度；
- **Adaptation Rate：** 换队、新车、升级或规则切换后的收敛速度。

### 5.6 人车适配

首期使用强收缩组合效应：

\[
I_{driver,team,season,k,m}\sim N(0,\tau_I^2)
\]

只有同时满足以下条件才发布稳定适配结论：

- 在多个分站或赛季重复出现；
- 不由单次事故、DNF、车队换人或数据缺失驱动；
- 留一分站、留一赛道和转会验证中仍有预测价值；
- 对清洗、车队别名、轮胎和交通处理稳定；
- 后验区间能与零效应区分。

若不满足，只报告“未解释的人车组合效应”，不得解释为调校偏向或车手适应问题。

## 6. 四队差距的机制分解

四队横向比较至少覆盖以下可观测维度：

| 维度 | 数据代理 | 允许结论 | 禁止直接结论 |
| --- | --- | --- | --- |
| 重刹 | 制动开始、速度变化、制动距离、最低速位置 | 重刹时间/速度响应差 | 真实制动压力或制动材料优劣 |
| 低速弯 | 最低速度、弯中时间、重新给油位置 | 低速性能差 | 具体悬架或差速器设定 |
| 中高速弯 | 曲率条件下速度包络 | 中高速响应差 | 真实下压力数值 |
| 牵引 | 最低速后油门和加速度 | 出弯牵引响应差 | 真实轮胎抓地或扭矩映射 |
| 直线 | 入直道速度、挡位、油门、高速加速度 | 直线效率/动力响应代理 | 真实马力或阻力系数 |
| 轮胎维持 | 初始配速条件下的后续变化 | 配速—维持权衡 | 纯轮胎物理衰退 |
| 跟车 | 前车距离、闭合率、DRS、条件时间损失 | 脏空气/交通敏感度 | 真实尾流场 |
| 可驾驭性 | 操作偏差对应的时间损失、双车分布 | 性能窗口宽窄 | 车手主观驾驶感受 |

如果模型包含非加性交互，机制贡献使用模型内反事实或带后验区间的 Shapley/Owen 分解；SHAP 只可用于预测解释，不能命名为因果贡献。

## 7. 最终成绩归因

### 7.1 三种价值函数必须分开

至少定义：

- (V_{pace})：标准化圈速或赛段时间优势；
- (V_{result})：完赛时间、名次或领奖台概率；
- (V_{points})：积分期望。

积分和名次层必须额外建模策略、可靠性、事故和赛会事件，不能把所有结果差异强行分给车辆与车手。

### 7.2 归因组件

首选报告六项有符号贡献：

1. 车辆基础性能；
2. 车手基础能力与当场发挥；
3. 人车适配；
4. 策略与运营；
5. 可靠性与事故；
6. 未解释与随机事件。

### 7.3 两方比例的 Shapley 定义

当产品需要“车辆/车手占比”时，必须注明价值函数和参考基准。设 (C_0,D_0) 为参考车辆与参考车手：

\[
\phi_C=\frac12\{V(C,D_0)-V(C_0,D_0)+V(C,D)-V(C_0,D)\}
\]

\[
\phi_D=\frac12\{V(C_0,D)-V(C_0,D_0)+V(C,D)-V(C,D_0)\}
\]

人车交互由 Shapley 对称分配，或在主报告中保持独立。比例仅针对“相对参考基准的已解释性能增益”，不能表述为 F1 永久的人车物理比例。

允许出现负贡献、超过 100% 的单项贡献或总增益接近零导致比例不稳定。此时优先展示毫秒、积分期望和有符号贡献，不强制生成 0–100 饼图。

## 8. 2023–2025 到 2026 的迁移协议

### 8.1 旧规训练域负责学习

- 车手基础能力和条件控制策略先验；
- 车手稳定性、失误尾部和适应速度先验；
- 赛道赛段表示和标准篮子；
- 测量误差、缺失和上下文修正；
- 队友/转会识别网络；
- 从赛季初少量样本更新新车状态的方法。

### 8.2 2026 必须重置或弱迁移

- 四队车辆基准和动态状态；
- 动力单元与潜在能量状态；
- 主动空气动力/低阻状态效应；
- 轮胎—车辆响应；
- 跟车和尾流敏感度；
- 人车交互。

车手先验可以迁移，但允许比旧规赛季更大的初始偏移和适应过程噪声。

### 8.3 规则阶段

初始配置至少包含：

- `old_regulation_2023_2025`
- `new_regulation_2026_initial`
- `new_regulation_2026_post_miami_revision`

2026 赛季内能量管理规则修订不得只用普通 round 随机效应吸收，应显式标注断点。规则事实以 FIA 当前发布的法规和修订公告为准。

### 8.4 验证时间线

1. 2023 训练，模拟少量 2024 适配后预测 2024 后续分站；
2. 2023–2024 训练，模拟少量 2025 适配后预测 2025 后续分站；
3. 用上述伪新赛季实验选择先验强度、冻结层和在线更新速度；
4. 冻结 2023–2025 旧规模型；
5. 将截至预注册日期已查看的 2026 数据定义为适配/开发集；初始登记以 2026-07-18 为边界；
6. 将边界之后尚未发生或尚未查看的分站作为前瞻性确认集；
7. 2026 初始规则与赛季内修订后的比赛分别报告。

已经查看的 2025 和 2026 数据不能重新声明为完全未见验证集。所有结构调整必须记录首次查看日期和下一确认窗口。

## 9. 基线、消融与评价

### 9.1 最低基线

- 同队有效圈中位差；
- 仅赛道/上下文模型；
- 车队固定效应 ridge；
- 车手+车队 ridge；
- 动态 `team × season` 层级模型；
- 无执行前沿的普通分布模型。

### 9.2 必做消融

- 移除人车交互；
- 移除动态车辆状态；
- 移除执行损失前沿；
- 移除赛段类型；
- 移除轮胎、交通、天气和规则阶段；
- 仅使用四队训练，对比全场识别网络；
- 不同参考车手和标准赛道篮子权重。

### 9.3 评价指标

**预测质量：** MAE、RMSE、CRPS、对数预测密度、PIT。  
**概率校准：** 50%/80%/95% 区间覆盖率与宽度。  
**车辆识别：** 下一分站四队差距、队友差预测、分站状态更新稳定性。  
**迁移质量：** 适配前 N 站后对后续分站的预测和区间覆盖。  
**归因稳定性：** 清洗、别名、参考篮子、留一分站、留一赛道和留一转会敏感性。  
**模型诊断：** R-hat、ESS、PPC、残差结构、参数可识别性和后验相关。  

复杂模型只有在时间外预测、概率校准或归因稳定性上至少一项明确改善且没有破坏其他门槛时才采用。

## 10. 输出合同

### 10.1 四队车辆基准

每个 `team × round × scenario` 至少输出：

- `car_ceiling_posterior`
- `usable_baseline_posterior`
- `performance_floor_posterior`
- `drivability_width`
- `relative_pace_pct`
- `standard_90s_lap_delta_ms`
- `segment_type_deltas_ms`
- `development_state`
- `data_coverage`
- `confidence_flags`

### 10.2 车手提取

每个 `driver × round × scenario` 至少输出：

- `ceiling_extraction_percentile`
- `typical_execution_loss_ms`
- `extraction_percentile`
- `consistency_scale`
- `negative_tail_probability`
- `segment_extraction`
- `adaptation_state`
- `sample_size`
- `confidence_flags`

### 10.3 价值归因

每个分站和赛季累计至少输出：

- `vehicle_contribution`
- `driver_contribution`
- `interaction_contribution`
- `strategy_operations_contribution`
- `reliability_incident_contribution`
- `unexplained_contribution`
- `value_function`
- `reference_definition`
- `posterior_interval`

机器可读 JSON 是结果真相源；Markdown 只生成摘要。任何前端百分比必须能追溯到价值函数、参考基准、模型版本、数据截止日期和置信区间。

## 11. 已实现入口与后续产物

当前已实现：

```text
research/configs/four_team_2025_baseline_v1.json
research/configs/four_team_2025_contrast_stress_v1.json
research/configs/four_team_2023_2025_seasonal_v2.json
research/configs/four_team_2023_2025_bayesian_v3.json
research/configs/four_team_2023_2025_event_stint_v4.json
research/configs/four_team_explanatory_depth_v6.json
research/build_qualifying_frozen_data.py
research/run_four_team_baseline.py
research/run_four_team_contrast_stress.py
research/run_four_team_seasonal_attribution.py
research/run_four_team_bayesian_attribution.py
research/run_four_team_event_stint_attribution.py
research/run_four_team_race_by_race.py
research/run_four_team_explanatory_depth.py
research/modeling/entities.py
research/modeling/hierarchical.py
research/modeling/evaluation.py
research/modeling/seasonal_attribution.py
research/modeling/bayesian_counterfactual.py
research/modeling/event_stint_attribution.py
research/modeling/explanatory_depth.py
research/records/four_team_car_baseline_2025.json
research/records/four_team_driver_extraction_2025.json
research/records/four_team_value_attribution_2025.json
research/records/four_team_external_validation_2025_hungary.json
research/records/four_team_attribution_hungary_2025.json
research/records/four_team_contrast_stress_2025.json
research/records/four_team_seasonal_attribution_2023_2025.json
research/records/four_team_bayesian_attribution_2023_2025.json
research/records/four_team_event_stint_attribution_2023_2025.json
research/records/four_team_race_by_race_2023_2026_v5.json
research/records/four_team_explanatory_depth_2023_2025_v6.json
tests/test_four_team_attribution.py
tests/test_four_team_contrast_stress.py
tests/test_four_team_seasonal_attribution.py
tests/test_four_team_bayesian_attribution.py
tests/test_four_team_event_stint_attribution.py
tests/test_four_team_race_by_race.py
tests/test_four_team_explanatory_depth.py
```

仍计划补齐：

```text
research/configs/2026_regulation_transfer_v1.json
research/modeling/splits.py
research/modeling/segments.py
research/modeling/context.py
research/modeling/attribution.py
research/records/regulation_transfer_2026.json
```

单站原型中三个带 `posterior` 名称的车辆输出字段只是为兼容第 10 节长期合同而保留；其内部 `method` 明确标记 `bootstrap_not_bayesian_posterior`。只有 v3 机器结果中的 `counterfactual_ratings` 来自真正 Gibbs 后验，但其原始时间外区间仍欠覆盖，发布时不得省略校准状态和识别敏感性。

补充的六站压力测试沿用同一参数，并故意纳入冠军与全年赛车印象相反的 Japan/Italy。该实验只回答“单站原型是否机械复述赛果、反例如何分层解释”，现已降级为诊断材料。M1 v2、静态后验 v3、单场/stint v4、全量逐站 v5 与可解释深度 v6 已完成；它们仍不满足动态随机前沿、M3 统一赛段主模型、`V_result` 因果归因或 2026 最终确认门槛。

## 12. 实施顺序

### M0：预注册与数据审计

1. 固定四队实体别名、全场识别范围和车手转会图；
2. 建立 2023–2025 Race/Qualifying 分开冻结；
3. 定义标准赛道篮子、规则阶段和数据截止日期；
4. 审计每个 `team × driver × session × segment type` 覆盖；
5. 创建配置哈希、数据 manifest 和失败日志。

### M1：圈级透明联合模型

1. 已复用现有 ridge 作为最低基线；
2. 已实现静态分层车辆、车手和强收缩人车交互；动态状态仍待完成；
3. 已生成四队标准车辆基准和参考车辆下的车手能力，并单列适配后验；
4. 已完成 2023→2024、2024→2025 rolling-origin 聚合层回测和区间校准审计。

### M2：执行前沿与性能包络

1. 已实现条件分位数描述层；随机前沿和动态状态仍待比较；
2. 已按分站输出 Car Ceiling、Usable Baseline、Performance Floor 和最佳单车手上沿敏感性；
3. 已输出每站车手执行损失、稳定性与每个 stint 的残差/超额衰退；v5 对不少于 8 圈的 stint 增加早段拟合→晚段留出、奇偶圈复验和 200 次斜率 bootstrap；
4. 已运行 27 组分位数/会话权重网格并报告排名边界；仍需加入留一车手、交通和损伤状态敏感性后才能验收完整 M2；
5. v5 已将 2023–2025 全赛历规范化为逐站同构合同：69 站正式分析、1 站显式缺失；每站并列模型上沿、本场最快圈、本场分类、平均速度和稳定性，赛季只做分站等权汇总。

### M3：赛段级车辆差距

1. 生成统一赛道相位和赛段类型；
2. 对齐官方 sector 与逐点遥测；
3. 分解四队在重刹、低速、中速、高速、牵引和直线的差距；
4. 仅在时间外提升和稳定性通过后加入更复杂的功能表示。

### M4：结果价值归因

1. 先完成 `V_pace`；
2. 再加入策略、可靠性和事故，形成 `V_result`；
3. 最后形成 `V_points`；
4. 使用有符号贡献和 Shapley 结果，验证不同参考基准敏感性。

### M5：2026 跨规则迁移

1. 冻结旧规车手先验、赛道表示和测量模型；
2. 重置 2026 车辆、能量、空气动力和交互层；
3. 按规则阶段与分站在线更新；当前 v5 已用本地四站完成探索性零状态强收缩更新，并用历史锁定时间前推误差形成宽预测带；
4. 四站尚未达到 5 站确认门槛；达到门槛后冻结适配窗口，对登记日期之后的比赛做前瞻性确认。

## 13. 完成定义

本任务只有同时满足以下条件才可称为完成：

1. 数据、配置、代码和结果均有可追溯版本与哈希；
2. 四队车辆基准不是由单一车手最快圈直接定义；
3. 全场识别网络与四队输出限制被分别实现并有消融；
4. 2023→2024、2024→2025 rolling-origin 回测通过预注册门槛；
5. 车辆、车手、人车交互和执行损失有可识别性诊断；
6. 四队差距同时输出百分比、标准 90 秒圈毫秒和赛段分解；
7. 车手发挥输出后验分位数和执行损失，不声称物理性能百分比；
8. 结果归因明确价值函数、参考基准和非车辆/车手组件；
9. 2026 车辆状态重置，并显式处理赛季内规则断点；
10. 所有排名和贡献带区间、覆盖、样本量和敏感性标记；
11. 机器可读结果、研究日志、[`algorithm-models.md`](algorithm-models.md)、[`implementation-results.md`](implementation-results.md) 和 [`progress.md`](progress.md) 按职责同步。

## 14. 新任务启动检查单

新 Agent 开始建模前按顺序执行：

1. 阅读 [`docs/README.md`](README.md)、本文、[`algorithm-models.md`](algorithm-models.md) 和 [`database.md`](database.md)；
2. 核对 `research/artifacts/data_manifest.json`、v4 schema 和当前结果文件；
3. 创建预注册配置，不直接修改旧实验配置；
4. 明确当前任务处于 M0–M5 哪个里程碑；
5. 先实现最小基线和时间切分测试，再实现复杂模型；
6. 不使用已查看的 2025/2026 结果选择阈值后仍称确认性验证；
7. 每个方法遵守三次失败协议并写入独立实验日志；
8. 只在验收通过后更新 `implementation-results.md`，开始/阻塞/完成时更新 `progress.md`。

## 15. 当前限制与非目标

- 首期不发布全场完整车手排行榜；
- 首期不以单一队友争议作为模型目标；
- 首期不恢复真实车辆工程参数、SOC、燃油、胎温、设定或空气动力数值；
- 首期不把 SHAP、回归系数或媒体解释直接称为因果贡献；
- 首期不强制输出始终相加为 100% 的车辆/车手比例；
- 首期不把 2026 已查看比赛重新包装为完全未见测试集；
- 若公开数据无法区分车辆、车手和组合效应，应把宽区间或不可识别作为正式研究结论。

## 16. 外部规则依据

- FIA 2026 规则总入口：<https://www.fia.com/regulation/sporting-code>
- FIA 2026 规则技术概览：<https://www.fia.com/news/new-era-competition-fia-showcases-future-focused-formula-1-regulations-2026-and-beyond>
- FIA 2026 赛季内修订公告：<https://www.fia.com/news/refinements-2026-fia-formula-1-regulations-agreed-all-stakeholders>

规则页面可能继续修订。实现任务必须将实际采用的 FIA 文档 issue、发布日期和规则阶段写入配置或 manifest，不能只依赖本文链接。
