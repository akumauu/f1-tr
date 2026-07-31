# gptempo · 2023–2025 分赛道验证 method card

本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。外层测试按整场留出；同赛道重复年份优先使用其他年份，单年或可观测轮廓不连续时只允许赛道族群候选。固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 `reference-informed`，不称 blind holdout。

## 固定语义

- 三个官方 sector 端点必须严格闭合；连续信号线性、离散信号最近邻，段内时间按官方 sector 仿射缩放。
- 只有 2025 Abu Dhabi 冻结了所需官方 sector/car channel。公开页没有逐点真值，因此段内 MAE 保持 `null/NOT_TESTED`。
- 单事件只能保留 universal 公开方法，不能发布赛道专用模型。

## 发布门

- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；不同量纲不压成总复刻率。
- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力和引擎功率不可识别。

## 冻结基线再验

```json
{
  "status": "PASS",
  "identity": {
    "path": "research/records/reference_analysis_lab_v2/gptempo/run=20260731T213000+0800-gptempo-reverse-engineered-v1/gptempo_reverse_engineered_v1.json",
    "sha256": "efc0f46f01c0250094acc606ba56479c6345d5c4bd278e6c2f2bd21736f31fcb"
  },
  "sector_endpoint_checks": 270,
  "max_abs_sector_endpoint_error_s": 0.0,
  "public_point_truth_available": false,
  "segment_mae": null,
  "segment_mae_status": "NOT_TESTED_NO_PUBLIC_POINT_TRUTH"
}
```
