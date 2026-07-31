# 2026 Ferrari 四站遥测解释汇总

- 范围：`HAM - LEC`，`Ferrari`，`Race`。
- 事件暴露：4/8；严格质量合格：3/8；状态：`exploratory_only`。
- 本汇总只保留匹配遥测中的物理证据，不使用完赛名次、积分或年度排名标签。
- `OVR`、`Event Delivery` 与期望积分均为空；四场结果只可作为 2026 独立 era 的探索快照。

| 分站 | 可比圈 | 可比性 | 中位物理圈差 s | 异常 | HAM 模式 | LEC 模式 | schema | 赛段覆盖 | 响应基线覆盖 |
| --- | ---: | --- | ---: | ---: | --- | --- | --- | ---: | ---: |
| Australian Grand Prix | 20 | comparable | -0.044 | 0 | high_response_mode | save_like_mode | tracinginsights-expanded-v5 | 1.000 | 1.000 |
| Chinese Grand Prix | 16 | comparable | -0.049 | 0 | early_gain_mode | early_gain_mode | tracinginsights-expanded-v5 | 0.999 | 1.000 |
| Japanese Grand Prix | 15 | comparable | 0.380 | 0 | — | — | tracinginsights-expanded-v5 | 1.000 | 0.985 |
| Miami Grand Prix | 16 | comparable_with_balance_warning | 0.251 | 0 | early_gain_mode | early_gain_mode | tracinginsights-expanded-v5 | 1.000 | 1.000 |

## 发布边界

- 正物理圈差只表示该报告方向下目标车手在匹配圈更慢，不自动归因给车辆、车手或策略。
- 策略标签是中性操作模式代理，不能声称真实 SOC、部署功率、主动空力或 Overtake 状态。
- 单站异常触发只表示多基线与多特征族共同支持可观测响应变化，不能命名具体机械故障。
