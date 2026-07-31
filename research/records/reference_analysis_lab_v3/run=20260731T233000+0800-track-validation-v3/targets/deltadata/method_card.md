# deltadata · 2023–2025 分赛道验证 method card

本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。外层测试按整场留出；同赛道重复年份优先使用其他年份，单年或可观测轮廓不连续时只允许赛道族群候选。固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 `reference-informed`，不称 blind holdout。

## 固定语义

- clean air 操作定义保持逐点 2 秒交通比例不高于 20%；公开燃油情景固定 0.032 秒/圈。
- 模型预测 v17 条件修正量，只使用 raw 圈时、胎龄、阶段、交通、配方、位置和质量等可观测输入；v17 nuisance 分量不回灌为特征。
- 衰减方向与同队同配方 H2H 分开验收，不从不可比车队生成全序。

## 发布门

- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；不同量纲不压成总复刻率。
- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力和引擎功率不可识别。

## 冻结基线再验

```json
{
  "status": "PASS_REFERENCE_INFORMED_NOT_BLIND",
  "identity": {
    "path": "research/records/reference_analysis_lab_v2/deltadata/run=20260731T153000+0800-deltadata-reverse-engineered-v2/deltadata_reverse_engineered_v2.json",
    "sha256": "888ec7315050b2e98f5342a6c91d4f62ffdf741f1163d9f5d3f53777222c4f22"
  },
  "rows": 3,
  "mae_pp": 0.09133999999999999,
  "p90_abs_error_pp": 0.10728779999999999,
  "max_abs_error_pp": 0.111173,
  "direction_accuracy": 1.0,
  "reference_informed": true
}
```
