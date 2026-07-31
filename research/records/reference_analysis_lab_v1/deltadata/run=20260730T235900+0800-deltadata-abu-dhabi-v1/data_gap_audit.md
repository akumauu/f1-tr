# DeltaData data-gap audit · 2025 Abu Dhabi Race

| 目标字段 | 仓库当前字段 | 状态 | 替代代理 | 发布规则 |
|---|---|---|---|---|
| clean_air_lap_selection | stints[].points[].traffic_state | `AVAILABLE_PROXY` | v17 条件交通状态 + analysis_weight 的 clean_air 子集 | 允许发布为 clean-air pace proxy，不称创作者一比一结果 |
| fuel_load_and_correction | stints[].primary_fit.fuel_scenarios | `AVAILABLE_PROXY` | low/base/high named fuel-correction sensitivity | 允许发布情景敏感性，不称真实油量/统计区间 |
| physical_tyre_degradation | primary_fit.slope_s_per_tyre_lap | `AVAILABLE_PROXY` | observed tyre-age slope under conditional model | 允许发布胎龄变化代理，不称物理磨损 |
| sample_size_and_effective_sample | sample_audit + analysis_weight | `AVAILABLE` | observed/fit points and Kish ESS | 允许发布并随排名披露 |
| creator_exact_selector_and_code | NOT_AVAILABLE | `NOT_IDENTIFIABLE` | method-equivalent v17 contract | 禁止声称一比一复现 |
| causal_team_driver_rank | cross-compound/global ranking not identifiable | `NOT_IDENTIFIABLE` | descriptive conditional sort + audited pair gates | 只允许描述性顺序；audited_analysis 保持 audit_only |

结论：目标创作者的精确代码、选圈、燃油和百分比归一化没有公开，不能声称一比一
复刻。本 run 只发布真实 v17 sidecar 上的方法等价结果，visual_replication 和
audited_analysis 分开；audited_analysis 保持 `audit_only`，不形成跨配方/胎龄全序。
