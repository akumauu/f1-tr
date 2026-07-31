# FDataAnalysis 算法倒推 v2 方法卡

- run_id: `20260731T170000+0800-fdataanalysis-reverse-engineered-v2`
- status: `METHOD_EQUIVALENT_INTERNALLY_VALIDATED`
- original profile: https://x.com/FDataAnalysis
- captured_at: `2026-07-31T16:30:00+08:00`
- telemetry_sha256: `1fb05bd10f89a492ee1797040b2b4c23cb60eea709c44df747d4e9f2e0259601`
- v17_manifest_sha256: `9322064faee009e0e53d7ec345e9d25499f821658cdf05d497a74d233567658c`

## 可公开确认的方法

- 把速度、油门、制动、挡位等通道画在统一赛道距离轴上。
- 比较弯角入口、最低和出口速度，以及刹车与再加速位置。
- 公开分析曾使用直道 150–250 km/h 区间速度曲线斜率讨论加速。
- 公开 Top Speeds 图曾同时披露最大速度和最高 15 个速度样本均值。
- 长跑图按胎龄展示圈速变化、逐圈点、分布、标准差和 Push Laps 样本量。
- 车辆强弱总结由多场遥测中反复观察到的速度与控制特征归纳而来。

## 倒推或方法等价实现

- 每圈线性插值到 1,000 个距离区间；离散控制通道使用最近邻。
- 弯角代理由全场中位横向加速度绝对峰值识别，边界由横向载荷与制动证据动态确定。
- 全油门视觉口径保留样本比例；审计伴随改用逐点时间权重。
- 最高速度视觉口径使用最高 15 个逐点速度均值；审计伴随使用逐圈 P99 中位数。
- 胎龄变化在单一 Stint/配方内使用 Theil–Sen，并报告具名燃油情景。
- 同队双车使用一对一最小代价匹配，只接受同配方、胎龄差≤2、阶段差≤0.12、交通差≤0.15。

## 视觉反推与不可识别

- 原作者未公开精确平滑核、Push Lap 标签、弯角 anchor、跨场权重或车辆总评分公式。
- 1,000 区间、横向加速度阈值和匹配门限是可解释的工程化实现，不声称一比一源码复现。
- 参考图是 2026 匈牙利，而真实 pilot 是 2025 Abu Dhabi Race；不能把两者做数值 MAE。

- 不可识别：真实下压力、机械抓地、引擎功率、阻力系数、SOC、动力模式和能量部署。
- 不可识别：真实燃油量、胎温、胎压、物理磨损、设定、损伤和车队指令。
- 不可识别：公开图精确选圈、人工复核、平滑/进站等价代码和赛季聚合权重。

## 参考资产

- 2026 Hungarian Grand Prix Race Pace: `c2ce1c32e9fcdc9a64fd1001f5115242cc77e3aab8642e808614fb7738cb45e0` · `REFERENCE_ONLY_DIFFERENT_EVENT_SESSION` · https://x.com/FDataAnalysis/status/2081706503862378548
- 2026 Hungarian Grand Prix FP2 Long Runs: `519573a2ca23aa102c97116871fd065d2a1eb3e3bc4b3a5d533b0f62cb479f3a` · `REFERENCE_ONLY_DIFFERENT_EVENT_SESSION` · https://x.com/FDataAnalysis/status/2081027167987634407
- 无效资产：`aa499fc95e55a6d42607d200416a8db60381b2738b3e057db05edfdaf6ad7e6a` · `INVALID_REFERENCE_ASSET_SUBJECT_MISMATCH`

## 边界

只重实现公开逻辑与布局语义；不复制原图、logo、品牌字体或人物照片。
