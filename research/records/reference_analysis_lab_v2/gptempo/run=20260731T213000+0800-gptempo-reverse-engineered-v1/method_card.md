# GP Tempo 公开分段 Delta 方法卡

- 目标：GP Tempo / X `@f1_tempo_`
- 公开方法页：https://gp-tempo.com/about
- 参考抓取：`2026-07-31T04:23:07.090874+00:00`
- About HTML SHA-256：`21d7e52798c9b7709b8d8a6692acd0c95e0fb0d6908966f0b0c70f27554464b6`
- Bundled method script SHA-256：`58ed2cec81943cb702708c54c90711cca8b40ddea9742d7280378514ed5e5af5`
- X profile SHA-256：`967a7ab1d431a048f033f12d982ad8c03f7338cec4e223a7b0288cda8d243267`
- 真实 pilot：2025 Abu Dhabi，5 个 Qualifying 最快圈 + 5 个 Race 最快圈
- 状态：`PUBLIC_METHOD_REIMPLEMENTED_ENDPOINT_VALIDATED`

## 公开方法

1. 使用官方 timing feed via FastF1，遥测通常约 4 Hz。
2. 按三个官方 sector time 分段，把 sector 端点当成硬约束。
3. 段内把候选圈距离线性插值到参考圈距离轴。
4. 段内时间线性缩放，使首尾严格等于官方累计差。
5. 只有 sector 端点保证精确；段内曲线是估计。
6. 支持跨 session，但必须提示配方、燃油、天气和赛道演化混合。

## 本次实现

- 真实圈：10
- 原始 car-channel 样本：3192
- 采样率中位：4.167 Hz
- 有序比较：90
- sector 端点检查：270
- 最大端点误差：0.0 s
- 默认选择门：`PASS`

## 边界

产品不复制 GP Tempo 的 logo、字体、截图或 CSS。真实燃油、SOC、胎温胎压、物理磨损、
设定、损伤、动力模式和车队指令不可识别；约 4 Hz 下制动点/油门点可能偏移约一个样本。
