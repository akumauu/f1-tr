# f1telemetrydata · 2023–2025 分赛道验证 method card

本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。外层测试按整场留出；同赛道重复年份优先使用其他年份，单年或可观测轮廓不连续时只允许赛道族群候选。固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 `reference-informed`，不称 blind holdout。

## 固定语义

- braking/lift/full/partial 互斥优先级、99% 全油门阈值、`lane_duration` 与 `stop_duration` 区别保持不变。
- 只有 2025 Abu Dhabi 有冻结同场公开图和 OpenF1 Q 阶段补充；其余排位只允许整场最佳准确推圈代理，逐任务 `NOT_TESTED`。
- 单个 reference-informed 事件不足以选择 cluster/track 模型。

## 发布门

- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；不同量纲不压成总复刻率。
- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力和引擎功率不可识别。

## 冻结基线再验

```json
{
  "status": "PASS_REFERENCE_INFORMED_NOT_BLIND",
  "identity": {
    "path": "research/records/reference_analysis_lab_v2/f1telemetrydata/run=20260731T193000+0800-f1telemetrydata-reverse-engineered-v1/f1telemetrydata_reverse_engineered_v1.json",
    "sha256": "af132dd167f8da91ca30071fdcd1f5a4c9e7268ce7d4c267a93651441301aea5"
  },
  "qualifying": {
    "lap_delta_mae_s": 0.0,
    "lap_sections_mae_pp": 2.639122,
    "lap_sections_max_error_pp": 6.157325,
    "team_throttle_mae_pp": 1.195668
  },
  "race": {
    "average_gap_mae_s": 0.000216,
    "fastest_lap_mae_s": 0.0,
    "pit_lane_team_mean_mae_s": 0.065808,
    "pit_lane_team_mean_max_error_s": 0.363
  },
  "same_event_public_reference_events": 1
}
```
