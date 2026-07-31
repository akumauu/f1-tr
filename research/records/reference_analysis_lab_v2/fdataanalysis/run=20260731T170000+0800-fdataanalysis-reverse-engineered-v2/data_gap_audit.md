# FDataAnalysis v2 data-gap audit

| 目标字段 | 当前字段 | 缺口 | 替代代理 | 发布规则 |
|---|---|---|---|---|
| speed/throttle/brake/gear/DRS/rpm | 逐点公开遥测 | 约 4 Hz 且为公开 feed | 统一距离轴插值并披露圈时积分误差 | `ALLOW_OBSERVED_FEATURES` |
| corner_entry_min_exit_and_brake_position | rel_distance/speed/brake/acc_y | 无冻结官方弯角 anchor | 横向加速度峰值动态窗口 P01…Pn | `ALLOW_NAMED_PROXY_NOT_OFFICIAL_CORNERS` |
| top_speed | 逐点 speed | 最高 15 样本受采样密度影响 | 视觉 top15 + 审计逐圈 P99 中位数 | `ALLOW_WITH_FORMULA_DISCLOSURE` |
| full_throttle_usage | 逐点 throttle/time | 样本占比不等于时间占比 | 视觉样本占比 + 审计时间权重 | `ALLOW_BOTH_VIEWS` |
| tyre_age_speed_and_lap_time_change | tyre_life/stint/compound/lap_duration/speed | 燃油、赛道演化和物理胎态不可分离 | 单 Stint Theil–Sen + low/base/high 具名燃油情景 | `CONDITIONAL_PROXY_ONLY` |
| vehicle_strengths | 输出速度与控制特征 | 输入物理量和设定不可识别 | 单特征排序；禁止总分和物理命名 | `OBSERVED_PROFILE_ONLY_NO_CAUSAL_TOTAL_ORDER` |

结论：允许发布观测速度与控制特征；物理车辆强弱和真实轮胎磨损不可识别，
所有跨车比较必须保留 visual_replication 与 audited_analysis 两套口径。
