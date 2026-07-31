# FDataAnalysis 方法卡 · 2025 Abu Dhabi Race pilot

- run_id: `20260730T235950+0800-fdataanalysis-abu-dhabi-v1`
- profile: https://x.com/FDataAnalysis
- profile_sha256: `efaf66b20029af6d244ee3a5cc9038907ec41c004033efbe63819118f138ec08`
- fetched_at: `2026-07-30T16:56:17.700886+00:00`

## 原作者页面/帖子

- [RACE PACE #HungarianGP](https://x.com/FDataAnalysis/status/2081706503862378548)：pit-equated race pace、smoothed lap-by-lap race pace、box/point distribution
- [LONG RUNS DATA (FP2 #HungarianGP)](https://x.com/FDataAnalysis/status/2081027167987634407)：tyre age、lap time change、long-run distribution

## 公开方法与视觉反推

- 公开图表展示 lap time 的分布/曲线、胎龄轴、车手/车队比较和样本圈数。
- 公开图表语义覆盖速度、油门、制动、挡位/赛道位置等驾驶特征方向。
视觉反推：
- 曲线/小提琴/箱线图对逐圈遥测或选圈后的统计量做了视觉聚合。
- 弯道入口/最低/出口与直道通常依赖统一赛道距离轴，但公开页没有冻结 anchor 文件。
本 pilot 实现：
- 从真实逐点遥测过滤首圈、Stint 边界和非绿/黄旗圈，使用 0.005 rel_distance 轴。
- 每个 driver×lap×axis bin 取速度/挡位中位数，控制开关使用采样时间权重。
- 弯道是全场中位速度平滑局部极小值 proxy；直道是相邻 proxy 弯道之间的距离窗。
- 同队双车以同队观察差值报告，作为 delivery baseline，不解释成车手因果贡献。
不可识别/不发布：
- 真实下压力、引擎功率、能量/SOC、动力模式、真实制动压力和物理轮胎磨损。
- 创作者精确选圈、平滑核、弯道/直道 anchor、pit-equation 代码和车辆模型。
- Setup、损伤、车队指令、燃油负载和策略意图。

## 输入身份

- telemetry: `data/normalized/tracinginsights/schema=tracinginsights-expanded-v4/year=2025/commit=f7a5324cae58/session=Race/meeting=Abu_Dhabi_Grand_Prix/telemetry.parquet`
- telemetry_sha256: `1fb05bd10f89a492ee1797040b2b4c23cb60eea709c44df747d4e9f2e0259601`
- rows: `784424`
- v17_manifest_sha256: `9322064faee009e0e53d7ec345e9d25499f821658cdf05d497a74d233567658c`

## 边界

只重绘公开语义；不复制 X 原图、logo、品牌字体或人物照片作为产品资产。
