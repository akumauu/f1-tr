# F1TelemetryData · 与参考图差异

## 精确或仅有显示舍入差

- Quali Lap Delta 20/20，MAE `0.000000s`，最大绝对差 `0.000000s`。
- Quali Top Speeds 20/20 完全一致，最大差 `0 km/h`。
- Top 3 sector timing 9/9 完全一致，最大差 `0.000000s`。
- Race Average Gap 20/20，MAE `0.000216s`；Race Timings 最快圈 20/20
  完全一致，最大差 `0.000000s`。
- 47/47 个本地/OpenF1 Stint 边界一致；参考图前五车手 5/5 完全一致。

## 可量化但不能声称一比一

- Lap Sections 六名公开车手的四分类 MAE `2.639122pp`，最大差 `6.157325pp`。来源是未公开阈值、
  约 3.7Hz 原始 car-channel 与仓库约 7.69Hz 扩展/插值 feed 的差异；
  未逐车手调值。
- Team Throttle Usage 十队 MAE `1.195668pp`，
  最大差 `3.084442pp`。
- Pit-lane team mean 十队 MAE `0.065808s`，
  最大差 `0.363000s`（Mercedes）。
  参考图发布时间与当前冻结 OpenF1 响应版本不同，且作者 timing-line
  定义未公开；不为贴图手调。

## 方法边界

- 以上 Race Pace/平均差是描述性全局均值，不具燃油、轮胎、交通和策略
  的因果可比性；审计视图不形成同一个排名。
- `lane_duration` 是 pit-lane transit；`stop_duration` 才是静止时间，
  当前 27 次 pit-lane passage 中只有 26 次可用，缺失值不填补。
