# 三季面板 RAPM 积分榜校准 + rolling-origin 验证（§5.6 叠加层）

- run_id：`seasonal_standings_calibration_2023_2025_v1-20260719T002710`；惩罚集 `balanced_v1`
- 样本：69 场 Race、61351 清洁圈

## 转会图连通性（识别可信度）
- 连通：否；分量数 4；车手节点 28、车队节点 10
  - 分量1（6队）[跨网格锚定]：Alpine, Ferrari, Haas F1 Team, Kick Sauber, Mercedes, Williams
  - 分量2（2队）[内部转会互通但与网格孤立]：RB, Red Bull Racing
  - 分量3（1队）[孤立(无转会)]：McLaren
  - 分量4（1队）[孤立(无转会)]：Aston Martin
- 与主网格孤立的车队（车/手分解仅赛段参照锚定、内部归因弱识别）：Aston Martin, McLaren, RB, Red Bull Racing
- 弱锚定车手（区间偏宽）：ALO, NOR, PIA, STR

## Rolling-origin 逐站时间外
- 有效 origin 数 44；总体分站等权 MAE 0.9055
- 2025 锁定段逐站 MAE 0.9903

## 真实积分榜校准
- 三季合并 车队评分 vs 车队积分榜 Spearman：0.892
- 三季合并 车手实跑(车+手) vs 车手积分榜 Spearman：0.904
- 三季合并 纯车手技能 vs 车手积分榜 Spearman：0.846（预期更弱：好车手可在差车里名次低）

| 年 | 车队评分vs车队榜 | 车手实跑vs车手榜 | 纯车手vs车手榜 |
| --- | ---: | ---: | ---: |
| 2023 | 0.855 | 0.967 | 0.887 |
| 2024 | 0.891 | 0.867 | 0.838 |
| 2025 | 0.964 | 0.916 | 0.8 |

## 2026 车手先验
- 已导出：`research\artifacts\panel_2026_driver_prior.json`（三季池化车手效应，正=更快，零均值）

## 解释限制
- 叠加校准层：估计器复用 §5.6 面板 RAPM，车辆基准以 §5.6 为准；本脚本只加校准/验证/诊断/2026 先验。
- 转会网络不连通（本数据 4 分量）：主集团(Ferrari/Mercedes/Williams/Haas/Sauber/Alpine 经 HAM/SAI/HUL 桥接)、红牛家族(RB+Red Bull Racing 内部互通但与外界无桥)、Aston Martin、McLaren 各自孤立。故 Red Bull/McLaren/Aston 的车/手分解无跨网格转会锚定，只靠赛段配速参照与收缩先验约束——其绝对车辆排名可信（配速被赛段参照锚定，与积分榜 0.89–0.96 相关），但车/手内部归因弱识别。
- 积分榜含可靠性/策略/碰撞/罚时，非纯速度，Spearman 只作方向性佐证非真值。
- bootstrap 为频率派重采样区间，非贝叶斯后验；无 R-hat/ESS/PPC。
- 燃油代理（lap_fraction）；仅 Race、无排位单圈；driver_team 仅未解释组合，不作车/手百分比分摊。
