# F1pace 2025 Abu Dhabi Race pace method card

## 来源与身份

- 原作者页面：https://f1pace.com/p/2025-abu-dhabi-gp-race-pace/
- 图表包：Race pace Top 10 / Bottom 10 / All drivers、Summarized race pace、Laps in traffic、Race pace delta
- 页面抓取时间：2026-07-30T15:54:59.773614+00:00
- 页面 SHA-256：`5912ecc8879b08c4dcd4f4fa0b2d676c99f187e3e0f1a0d9b786a578b69a7f1e`
- 真实数据：`data\normalized\tracinginsights\schema=tracinginsights-expanded-v4\year=2025\commit=f7a5324cae58\session=Race\meeting=Abu_Dhabi_Grand_Prix\telemetry.parquet`，SHA-256 `1fb05bd10f89a492ee1797040b2b4c23cb60eea709c44df747d4e9f2e0259601`，784,424 个逐点样本
- 复刻身份：`reference-analysis-f1pace-v1`；本实验输出与 Race Dossier v17 分离

## 公开口径与边界

公开方法明确移除首圈、进/出站圈和 SC/VSC 圈，保留绿/黄旗与湿胎圈；按车手均值排序，Q1/Q3 作为分布四分位，Stint 只决定横向 jitter；交通按逐圈超过 33% 时间处于前车 2 秒内判断，包含回退车。逐点交通实现使用 `distance_to_driver_ahead`、速度换算的 2 秒距离阈值和逐点时间权重，绝不使用圈级 median gap。

视觉上重绘中性网格、四分位背景、逐圈点、Stint jitter、交通热图和反对称均值差矩阵；不复制原始图片、logo 或品牌字体。页面署名保留 F1pace by F1bythenumbers，并遵守 CC BY-NC-ND 边界。

无法从页面公开代码确认的选圈细节不被声称为一比一复现。真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令和全局均值差的因果车辆/车手贡献均不可识别。

## 真实 pilot 验收

- 圈宇宙：1156 圈；F1pace 口径有效圈：1082 圈；逐点样本：784,424。
- 排除账本：首圈 20；进/出站边界并集 54；非绿/黄旗 0；标记为 deleted 但仍保留的有效圈 38。
- 公开均值比对：20/20 名车手，最大绝对差 0.000491s，容差 0.001s。
- pairwise 反对称：`True`；交通逐点时间加权：`True`。
- 排位：`整场最佳准确推圈代理；阶段不可识别`；不把当前冻结集冒充 Q1/Q2/Q3。

## 两种视图

- `visual_replication`：忠实展示公开页面的全场均值、Q1/Q3、逐圈点、交通比例和 pairwise 均值 delta。
- `audited_analysis`：固定为 `audit_only`，逐车手披露停站、配方、胎龄、交通构成和原因；不从全局均值形成因果全序。
