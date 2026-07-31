# DeltaData v2 数值差异说明

## 对公开 2026 Mercedes H2H 图

| 事件 | 公开 ANT-RUS | v2 ANT-RUS | 误差 pp | 审计状态 |
| --- | ---: | ---: | ---: | --- |
| JAPAN RACE | -0.131% | -0.242% | -0.111 | AUDIT_ONLY |
| MIAMI SPRINT | -0.270% | -0.178% | +0.092 | AUDIT_ONLY |
| MIAMI RACE | -0.572% | -0.643% | -0.071 | COMPARABLE |

- 3 场 MAE: `0.091340 pp`
- RMSE: `0.092794 pp`
- 最大绝对误差: `0.111173 pp`
- 方向准确率: `100.0%`

这些事件值在模型家族选择时已被看到，因此是参考知情的反推基准，不是盲测或零重拟合外部验证。

## 对本仓库 DeltaData v1

- 共享 8 名车手顺序完全一致: `True`
- 共享 4 支车队顺序完全一致: `True`
- 车手 centered-gap MAE: `0.154270 s`
- 车队 centered-gap MAE: `0.063910 s`

主要差异来源：

- v1 只消费 v17 sidecar 的 4 支 reporting teams；v2 使用原始全场 20 车手。
- v1 主情景为 0.051724 s/圈；v2 视觉主口径冻结为公开 0.032 s/圈。
- v1 使用 v17 cross-fit/analysis_weight 与 Stint 参考胎龄；v2 使用逐点物理交通、80% clean-air 和全场 Huber 条件标准化。
- v2 对极少 clean-air 支持的车手 fail-close，不用 sidecar 条件模型补齐视觉排名。
- 两版绝对参考点不同，因此只比较相对最快者的 centered gap，不比较绝对秒数。
