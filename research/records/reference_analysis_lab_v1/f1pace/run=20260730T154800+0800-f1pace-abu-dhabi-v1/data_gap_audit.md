# F1pace data-gap audit

| 目标字段 | 当前字段 | 状态 | 替代代理 | 发布边界 |
| --- | --- | --- | --- | --- |
| race_lap_time | lap_duration | AVAILABLE | — | allowed_descriptive |
| green_yellow_flag_lap | track_status | AVAILABLE | — | allowed_descriptive |
| pit_in_out_lap | stint transition | AVAILABLE_PROXY | stint 切换前后两圈边界代理 | allowed_with_proxy_label |
| point_distance_to_driver_ahead | distance_to_driver_ahead + speed + time | AVAILABLE | 逐点时间加权，不使用 lap median gap | allowed_descriptive |
| qualifying_q1_q2_q3_phase | phase label absent in qualifying_2023_2025_v4 | NOT_AVAILABLE_IN_CURRENT_FREEZE | 整场最佳准确推圈代理 | proxy_only |
| true_fuel_or_tyre_state | none | NOT_IDENTIFIABLE | 不发布物理量；后续目标可用具名情景 | not_allowed_as_observed_fact |
