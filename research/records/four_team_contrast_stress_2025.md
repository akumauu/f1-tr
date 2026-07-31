# 2025 六站四队归因反例压力测试

- 运行 ID：`four_team_2025_contrast_stress_v1-20260718T184954`
- 固定参数：2024 车手先验 alpha=10；车队惩罚=10；当站车手更新惩罚=100。
- 性质：按已知赛果事后挑选的解释性压力测试，不是确认性准确率。

| 类别 | 分站 | 冠军车队 | 模型 P50 最快车 | 冠军车队模型排名 | 官方最快圈代理 | 模型/代理 Spearman |
| --- | --- | --- | --- | ---: | --- | ---: |
| 观感与赛果高度一致 | Miami Grand Prix | McLaren | McLaren | 1 | McLaren | 0.80 |
| 观感与赛果高度一致 | Spanish Grand Prix | McLaren | McLaren | 1 | McLaren | 0.80 |
| 观感与赛果高度一致 | Mexico City Grand Prix | McLaren | McLaren | 1 | Mercedes | 0.40 |
| 观感与赛季印象明显错位 | Canadian Grand Prix | Mercedes | Mercedes | 1 | McLaren | 0.80 |
| 观感与赛季印象明显错位 | Japanese Grand Prix | Red Bull Racing | McLaren | 4 | McLaren | 1.00 |
| 观感与赛季印象明显错位 | Italian Grand Prix | Red Bull Racing | McLaren | 4 | McLaren | 0.80 |

## 聚合

- 模型 P50 最快车与冠军车队一致：4/6。
- 官方双车最快圈代理的最快车与冠军车队一致：2/6。
- 模型与官方最快圈代理平均 Spearman：0.767。
- 这些数字用于定位反例；不能因样本按结果挑选而外推成泛化准确率。

## 分层诊断

- Miami Grand Prix：冠军车队 P50 相对最快车 0.0 ms；同队当站中位差 NOR +0.0 ms、PIA +155.2 ms。
- Spanish Grand Prix：冠军车队 P50 相对最快车 0.0 ms；同队当站中位差 PIA +0.0 ms、NOR +87.6 ms。
- Mexico City Grand Prix：冠军车队 P50 相对最快车 0.0 ms；同队当站中位差 NOR +0.0 ms、PIA +437.8 ms。
- Canadian Grand Prix：冠军车队 P50 相对最快车 0.0 ms；同队当站中位差 RUS +0.0 ms、ANT +69.5 ms。
- Japanese Grand Prix：冠军车队 P50 相对最快车 366.6 ms；同队当站中位差 VER +0.0 ms、TSU +945.6 ms。
- Italian Grand Prix：冠军车队 P50 相对最快车 423.3 ms；同队当站中位差 VER +0.0 ms、TSU +1503.1 ms。
