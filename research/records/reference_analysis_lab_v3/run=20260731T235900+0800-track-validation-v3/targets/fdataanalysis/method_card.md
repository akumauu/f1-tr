# fdataanalysis · 2023–2025 分赛道验证 method card

本卡片属于追加式 v3；冻结 v1/v2 与 Race Dossier v14–v17 只读。外层测试按整场留出；同赛道重复年份优先使用其他年份，单年或可观测轮廓不连续时只允许赛道族群候选。固定超参数不在测试折上重选，公开参考曾被看过的结果统一标记 `reference-informed`，不称 blind holdout。

## 固定语义

- 每场用最多 12 位车手各一条准确绿旗代表圈建立 1,200 区间参考轴；连续信号线性、离散信号最近邻。
- P01…Pn 是横向加速度峰值代理，不是官方弯号。族群和单赛道候选只能在训练事件选择采样/额外平滑策略。
- 跨年失败只叫可观测几何或采样不连续代理，不冒充官方布局变化。

## 发布门

- 同量纲逐项比较 MAE、P90、max、覆盖、方向或端点不变量；不同量纲不压成总复刻率。
- 候选必须在完整留出事件稳定改善，覆盖损失与尾误差均过门；否则回退。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式、车队指令、真实下压力和引擎功率不可识别。

## 冻结基线再验

```json
{
  "status": "PASS",
  "identity": {
    "path": "research/records/reference_analysis_lab_v2/fdataanalysis/run=20260731T170000+0800-fdataanalysis-reverse-engineered-v2/fdataanalysis_reverse_engineered_v2.json",
    "sha256": "305b388a0ba43861d1335d43ab1c62b9c1f51efefb6fe0075ec7c039d21f4c4e"
  },
  "rows": 1082,
  "mae_s": 0.1547279825244995,
  "p90_abs_error_s": 0.3047971966382036,
  "max_abs_error_s": 0.8365586726810221,
  "corner_proxy_role": "not_official_corner_number"
}
```
