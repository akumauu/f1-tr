# f1pace · 2023–2025 分赛道验证 method card

本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。外层测试按整场留出；同赛道重复年份优先使用其他年份，单年或可观测轮廓不连续时只允许赛道族群候选。固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 `reference-informed`，不称 blind holdout。

## 固定语义

- 逐点前车距离不超过自身速度乘 2 秒才计入交通；一圈比例严格大于 33% 才是 traffic lap。
- universal Huber 不读取测试事件、车手身份或公开参考图；Abu Dhabi/Qatar v2 外部图只作冻结再验，不进入 v3 拟合。
- pairwise delta 仍是描述性均值差并保持反对称，不形成因果排名。

## 发布门

- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；不同量纲不压成总复刻率。
- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力和引擎功率不可识别。

## 冻结基线再验

```json
{
  "status": "PASS",
  "identity": {
    "path": "research/records/reference_analysis_lab_v2/f1pace/run=20260731T120000+0800-f1pace-reverse-engineered-v2/f1pace_traffic_reverse_engineered_v2.json",
    "sha256": "631e53996d372a710b73a30a5a83959f0d19ee3aa2c520d312c41c9c82969aff"
  },
  "external_event": "2025 Qatar",
  "external_event_zero_refit": true,
  "rows": 1067,
  "mae_pp": 2.2383428745207405,
  "p90_abs_error_pp": 6.847727146105963,
  "max_abs_error_pp": 72.30883527516667,
  "traffic_lap_accuracy": 0.9925023430178069,
  "abu_and_qatar_used_for_v3_training": false
}
```
