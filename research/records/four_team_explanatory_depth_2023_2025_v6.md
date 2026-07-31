# 2023–2025 四队 v6 可解释深度层

- 运行 ID：`four_team_explanatory_depth_2023_2025_v6-20260720T131209+0800`；四队圈级：25343；分站：69。
- 身份骨架后验：4 链 × 600；最大尺度 R-hat=1.020。
- Layer A 上下文门控（仅 2024 选特征）：基线 MAE=0.747 → 选中 ['traffic_dirty_air'] MAE=0.728；2025 锁定基线/选中=0.719/0.677。
- Layer C 圈速重建：driver-event R²=0.562，MAE=274.7 ms；模型组合序 vs 观测干净圈序平均 Spearman=0.821。
- Layer C 配速模型偏差（模型组合序≠观测干净圈序）：106 例；这是配速构念检查，不再误称赛果异常。
- Layer C 配速≠classification proxy：平均 Spearman=0.750，排名差≥2 的 driver-event=158 例；只标记待查原因，不作因果归因。

## 每站分离度与理论上限（2025）

| 分站 | 方差比 | 成对置信度 | 样本门控 | 分离度等级 | 上限第一 | 上限优势 90s ms |
| --- | ---: | ---: | --- | --- | --- | ---: |
| Abu Dhabi Grand Prix | 0.283 | 0.811 | pass | medium | McLaren | 639.3 |
| Australian Grand Prix | 1.304 | 0.699 | fail | low | McLaren | 1081.7 |
| Austrian Grand Prix | 0.861 | 0.888 | pass | high | McLaren | 1037.7 |
| Azerbaijan Grand Prix | 0.159 | 0.698 | pass | low | Red Bull Racing | 856.6 |
| Bahrain Grand Prix | 0.056 | 0.716 | pass | low | McLaren | 501.2 |
| Belgian Grand Prix | 0.382 | 0.758 | pass | medium | McLaren | 1151.2 |
| British Grand Prix | 0.247 | 0.693 | fail | low | Red Bull Racing | 857.1 |
| Canadian Grand Prix | 0.660 | 0.892 | pass | high | Mercedes | 1255.6 |
| Chinese Grand Prix | 0.102 | 0.734 | pass | low | Mercedes | 1713.8 |
| Dutch Grand Prix | 0.472 | 0.861 | pass | medium | McLaren | 1290.3 |
| Emilia Romagna Grand Prix | 0.355 | 0.822 | pass | medium | Red Bull Racing | 730.1 |
| Hungarian Grand Prix | 0.685 | 0.922 | pass | high | McLaren | 903.3 |
| Italian Grand Prix | 0.221 | 0.732 | pass | low | Ferrari | 374.7 |
| Japanese Grand Prix | 0.221 | 0.802 | pass | low | McLaren | 464.5 |
| Las Vegas Grand Prix | 0.052 | 0.666 | pass | low | McLaren | 204.2 |
| Mexico City Grand Prix | 0.388 | 0.840 | pass | medium | McLaren | 342.3 |
| Miami Grand Prix | 0.801 | 0.817 | pass | medium | Ferrari | 957.0 |
| Monaco Grand Prix | 0.844 | 0.886 | pass | high | McLaren | 538.1 |
| Qatar Grand Prix | 0.745 | 0.896 | pass | high | Red Bull Racing | 975.5 |
| Saudi Arabian Grand Prix | 0.305 | 0.731 | pass | medium | McLaren | 311.9 |
| Singapore Grand Prix | 0.315 | 0.797 | pass | medium | Ferrari | 756.9 |
| Spanish Grand Prix | 0.125 | 0.772 | pass | low | McLaren | 622.3 |
| São Paulo Grand Prix | 0.552 | 0.672 | pass | low | Red Bull Racing | 646.3 |
| United States Grand Prix | 0.069 | 0.645 | pass | low | McLaren | 394.3 |

## 使用边界

- 更明显来自更干净的上下文与显式信号/噪声，未改动 v3 先验强度。
- 理论上限是统计前沿+最快 10% 圈执行损失校正，不是物理极限；校正 bootstrap 已进入 CI，排位/正赛只融合相对优势。
- 配速模型构念与 classification proxy 赛果构念分开报告；proxy 不是 FIA 最终分类，策略/可靠/交通/事故未进入因果赛果。
- 这是 2025 已查看后的回顾性解释扩展，不冒充赛前预测。
