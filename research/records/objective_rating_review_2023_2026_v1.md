# 综合评分系统客观性门控

- 运行 ID：`objective_rating_review_2023_2026_v1-20260731T153117+0800`
- 历史核心门控：通过
- 完整车手卡：尚不可发布
- 状态：`historical_result_accounting_passed_full_driver_card_not_ready`

## 门控结果

| 门控 | 角色 | 结果 | 实际值 | 阈值 |
| --- | --- | --- | --- | --- |
| `ability_feature_leakage` | `core_historical` | PASS | `[]` | `"no prohibited result fields"` |
| `constructor_construct_each_year` | `core_historical` | PASS | `0.8545454545454544` | `0.8` |
| `realized_driver_construct_each_year` | `core_historical` | PASS | `0.8669565217391304` | `0.8` |
| `constructor_construct_pooled` | `core_historical` | PASS | `0.8922994144139845` | `0.85` |
| `realized_driver_construct_pooled` | `core_historical` | PASS | `0.9036120162868106` | `0.85` |
| `posterior_convergence` | `core_historical` | PASS | `1.0143684377998818` | `1.05` |
| `locked_time_forward_improvement` | `core_historical` | PASS | `0.1778799559730647` | `0.1` |
| `locked_interval_calibration_95` | `core_historical` | PASS | `0.9395604395604396` | `[0.9, 0.99]` |
| `rolling_origin_depth` | `core_historical` | PASS | `44` | `20.0` |
| `pac_driver_specific_time_forward` | `ability_module` | FAIL | `{"locked_events": null, "rank_spearman": null, "interval_coverage": null}` | `{"minimum_locked_events": 20.0, "minimum_rank_spearman": 0.7, "interval_coverage": [0.9, 0.99]}` |
| `2026_confirmatory_exposure` | `future_release` | FAIL | `4` | `8.0` |

## 模块成熟度

| 模块 | 状态 |
| --- | --- |
| `PAC` | `prototype` |
| `TYM` | `prototype` |
| `EXT` | `prototype` |
| `ATK` | `missing` |
| `DEF` | `missing` |
| `AWA` | `missing` |
| `STR` | `missing` |
| `ADP` | `missing` |

## 弱识别与边界

- `driver_team_graph_disconnected:4_components`
- `unvalidated_driver_modules:ADP,ATK,AWA,DEF,EXT,PAC,STR,TYM`
- 赛果相关性只验证结果生成层，不把赛果变成长期开车能力标签。
- 转会图不连通时，孤立车队内部的车辆—车手分离依赖层级先验。
- 2026 新规车辆响应、主动空力和能量代理必须按新 era 重置。
- 当前 rolling-origin 验证的是联合圈速预测，不是池化 PAC 的车手级排序稳定性或区间覆盖。

历史结果解释层通过不等于完整车手能力卡已经完成；缺失模块必须继续按机会暴露和时间外协议验证。
