# 2026 Australian Grand Prix Ferrari 双车遥测解释

- 对比方向：`HAM - LEC`；正时间差表示目标车手更慢。
- 可比圈：20；状态：`comparable`。
- 数据合同：`tracinginsights-expanded-v5`；发布状态：`exploratory_2026_rules_reset_insufficient_season_exposure`。
- 通过完整异常门控的 Episode：0。

## 主要赛段来源

| 赛段 | 直接差 ms | 输入调整响应差 ms | 可比圈 | 置信 |
| --- | ---: | ---: | ---: | --- |
| T1 | 81.3 | 103.8 | 20 | medium |
| T2 | -74.6 | -15.2 | 20 | medium |
| T14 | -70.3 | -48.5 | 20 | medium |
| T3 | 63.3 | 148.0 | 20 | medium |
| T9 | 50.9 | 107.1 | 20 | medium |
| T13 | 49.5 | 0.1 | 20 | medium |
| T8 后直道 | -48.2 | -29.9 | 20 | medium |
| T11 | -44.4 | 52.3 | 20 | medium |
| T11 前直道 | -39.9 | 17.2 | 20 | medium |
| T4 | -39.8 | 22.7 | 20 | low |
| T9 前直道 | -38.4 | -8.5 | 20 | medium |
| 过渡段 X23 | -29.5 | -10.2 | 20 | medium |

## 解释边界

- 直接时间差保留车手操作、策略和未观测状态；输入调整残差也只是诊断证据，不是因果效应。
- 2026 策略只使用中性模式标签，不推断 SOC、ERS-K 实际功率、主动翼位置或 Overtake 状态。
- 当前只有四场 2026 数据，赛季确认性门槛为八场；本报告不能生成 2026 赛季 OVR。
