# F1TelemetryData · data-gap audit

| 图表 | 所需字段 | 当前仓库 | 缺口 | 补充/代理 | 发布 |
|---|---|---|---|---|---|
| Quali Lap Compare / Lap Delta | fastest accurate lap, speed, throttle, distance | qualifying_v4 + expanded-v4 telemetry | current global freeze has no Q phase | frozen OpenF1 race_control/session_result | `ALLOW_WITH_EXTERNAL_PHASE_IDENTITY` |
| Track Dominance | common distance axis, cumulative time, track x/y | expanded-v4 telemetry | official geometric sector anchor absent | sector-end distance estimated from official sector time | `ALLOW_OBSERVED_TIME_PROXY_OFFICIAL_ANCHOR_NOT_TESTED` |
| Top Speeds / Throttle Usage / Lap Sections | raw car-channel speed/throttle/brake | expanded-v4 telemetry (~7.69Hz) | creator thresholds and source feed version opaque | frozen OpenF1 fastest-lap car data (~3.7Hz) | `ALLOW_METHOD_EQUIVALENT_WITH_REFERENCE_ERROR` |
| Quali Timings / Race Timings | sector durations and timing-line speeds | not retained in current telemetry freeze | sector timing and speed traps | frozen OpenF1 laps | `ALLOW_FROZEN_SUPPLEMENT` |
| Race Pace / Average Gap / Lap Times | lap duration, stint boundary, status | expanded-v4 telemetry + F1pace audited selection | fuel, traffic causality and tyre state | none; descriptive visual plus audit_only companion | `ALLOW_DESCRIPTIVE_NO_CAUSAL_GLOBAL_RANK` |
| Tyre Strategies | compound, stint, lap boundaries, tyre age at start | expanded-v4 + frozen OpenF1 stints | physical wear and tyre-set identity | fresh/used proxy from tyre_age_at_start | `ALLOW_OBSERVED_STRATEGY_NOT_PHYSICAL_WEAR` |
| Pit Times | pit-lane transit and stationary stop duration | not retained in current freeze | one stationary stop missing in supplement | frozen OpenF1 pit lane_duration/stop_duration | `ALLOW_LANE_DURATION; STOP_DURATION_PARTIAL_NO_IMPUTATION` |
| Downforce Map | aerodynamic load/drag and setup | not identifiable | true downforce, drag, setup and power mode | none | `NOT_IDENTIFIABLE` |
