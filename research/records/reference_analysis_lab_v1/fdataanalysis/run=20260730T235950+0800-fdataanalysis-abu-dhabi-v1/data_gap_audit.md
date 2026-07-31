# FDataAnalysis data-gap audit · 2025 Abu Dhabi Race

| 目标字段 | 仓库当前字段 | 状态 | 替代代理 | 发布规则 |
|---|---|---|---|---|
| speed_throttle_brake_gear_drs | telemetry.speed/throttle/brake/gear/drs | `AVAILABLE` | distance-axis median + time-weighted control shares | 允许发布观测特征 |
| corner_entry_min_exit_and_brake_position | rel_distance + speed + brake | `AVAILABLE_PROXY` | speed local-minimum corner proxies and distance windows | 允许发布 proxy，必须标明 anchor 非官方 |
| straight_speed_and_full_throttle | rel_distance + speed + throttle + drs | `AVAILABLE_PROXY` | between-proxy-corner distance windows | 允许发布观测特征 |
| tyre_age_effect | tyre_life + lap_duration + speed | `AVAILABLE_PROXY` | lap-time/speed slope per tyre-age lap | 允许发布条件变化，不称物理磨损 |
| official_corner_anchor_and_vehicle_physics | NOT_AVAILABLE_IN_FREEZE | `NOT_IDENTIFIABLE` | named distance-axis proxy only | 禁止命名真实下压力/功率/物理轮胎状态 |

结论：速度/控制字段可从真实逐点遥测发布；弯道/直道 anchor 是距离轴 proxy，
胎龄变化是条件观察代理。真实下压力、引擎功率、物理胎损和未公开创作者代码
不可识别，audited_analysis 保持 audit_only。
