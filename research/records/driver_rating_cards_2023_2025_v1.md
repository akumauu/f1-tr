# 2023–2025 全场历史车手评分卡

- 运行 ID：`driver_rating_cards_2023_2025_v1-20260731T153107+0800`
- 发布范围：``
- 车手数：**28**
- 赛事 / 清洁圈：**69 / 61351**
- 冻结总体标准差：`0.22839362345273181`（ddof=0）
- PAC 候选公式：`clip(75 + 10z, 0, 100)`；不按样本量二次收缩。
- PAC 当前值与区间均为 `null`：尚缺车手级时间前推排序稳定性与池化区间覆盖。

## 车手卡

| 排名 | 车手 | PAC | z | CI | 年份 | 识别标志 |
| ---: | --- | --- | --- | --- | --- | --- |
| — | `ALB` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `ALO` | `null` | `null` | `null` | 2023/2024/2025 | `driver_teammate_network_weakly_anchored, component_not_cross_grid_transfer_anchored, component_not_internally_transfer_anchored` |
| — | `ANT` | `null` | `null` | `null` | 2025 | `transfer_graph_anchor_available` |
| — | `BEA` | `null` | `null` | `null` | 2024/2025 | `transfer_graph_anchor_available` |
| — | `BOR` | `null` | `null` | `null` | 2025 | `transfer_graph_anchor_available` |
| — | `BOT` | `null` | `null` | `null` | 2023/2024 | `transfer_graph_anchor_available` |
| — | `COL` | `null` | `null` | `null` | 2024/2025 | `transfer_graph_anchor_available` |
| — | `DEV` | `null` | `null` | `null` | 2023 | `component_not_cross_grid_transfer_anchored` |
| — | `DOO` | `null` | `null` | `null` | 2024/2025 | `transfer_graph_anchor_available` |
| — | `GAS` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `HAD` | `null` | `null` | `null` | 2025 | `component_not_cross_grid_transfer_anchored` |
| — | `HAM` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `HUL` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `LAW` | `null` | `null` | `null` | 2023/2024/2025 | `component_not_cross_grid_transfer_anchored` |
| — | `LEC` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `MAG` | `null` | `null` | `null` | 2023/2024 | `transfer_graph_anchor_available` |
| — | `NOR` | `null` | `null` | `null` | 2023/2024/2025 | `driver_teammate_network_weakly_anchored, component_not_cross_grid_transfer_anchored, component_not_internally_transfer_anchored` |
| — | `OCO` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `PER` | `null` | `null` | `null` | 2023/2024 | `component_not_cross_grid_transfer_anchored` |
| — | `PIA` | `null` | `null` | `null` | 2023/2024/2025 | `driver_teammate_network_weakly_anchored, component_not_cross_grid_transfer_anchored, component_not_internally_transfer_anchored` |
| — | `RIC` | `null` | `null` | `null` | 2023/2024 | `component_not_cross_grid_transfer_anchored` |
| — | `RUS` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `SAI` | `null` | `null` | `null` | 2023/2024/2025 | `transfer_graph_anchor_available` |
| — | `SAR` | `null` | `null` | `null` | 2023/2024 | `transfer_graph_anchor_available` |
| — | `STR` | `null` | `null` | `null` | 2023/2024/2025 | `driver_teammate_network_weakly_anchored, component_not_cross_grid_transfer_anchored, component_not_internally_transfer_anchored` |
| — | `TSU` | `null` | `null` | `null` | 2023/2024/2025 | `component_not_cross_grid_transfer_anchored` |
| — | `VER` | `null` | `null` | `null` | 2023/2024/2025 | `component_not_cross_grid_transfer_anchored` |
| — | `ZHO` | `null` | `null` | `null` | 2023/2024 | `transfer_graph_anchor_available` |

## 模块成熟度与空值合同

| 指标 | 成熟度 / 状态 | 当前值 |
| --- | --- | --- |
| `PAC` | `prototype` | `null` |
| `TYM` | `prototype` | `null` |
| `EXT` | `prototype` | `null` |
| `ATK` | `missing` | `null` |
| `DEF` | `missing` | `null` |
| `AWA` | `missing` | `null` |
| `STR` | `missing` | `null` |
| `ADP` | `missing` | `null` |
| `EventDelivery` | `not_estimated` | `null` |
| `OVR` | `blocked_until_all_ability_modules_validated` | `null` |

## 2026 发布门槛

- 已观测 / 最低分站：**4 / 8**
- 门控：FAIL
- 当前模式：`exploratory_only`
- 说明：不足时仍可生成逐场探索报告，但不得发布确认性赛季评分。

## 解释边界

- `driver_team_graph_disconnected:4_components`
- `unvalidated_driver_modules:ADP,ATK,AWA,DEF,EXT,PAC,STR,TYM`
- `pac_driver_specific_time_forward_gate_not_passed`
- `pooled_pac_interval_not_exported_ci_null`
- `annual_driver_intervals_not_reused_for_pooled_pac`
- `sample_count_not_used_for_second_stage_shrinkage`
- 积分、完赛位置、冠军身份等赛果字段不得进入能力评分。
- `sample_count_total` 只说明证据覆盖，不改变本层 PAC。
- 当前结果是池化长期配速能力卡，不是单场发挥、轮胎管理或轮对轮能力卡。
