# F1TelemetryData 图表包 · method card

- 运行：`20260731T193000+0800-f1telemetrydata-reverse-engineered-v1`
- 作者身份：`https://x.com/F1TelemetryData`
- 公开频道：`https://t.me/s/f1telemetrydata`
- 同场 pilot：2025 Abu Dhabi Qualifying + Race。
- 状态：`METHOD_EQUIVALENT_REFERENCE_BENCHMARKED`；精确代码、阈值、
  异常处理与平滑未公开，故一比一状态为 `SKIPPED_OPAQUE_METHOD`。

## 参考图身份

| 图表 | 帖子 | SHA-256 | 发布时间 |
|---|---|---|---|
| `quali-lap-compare` | https://t.me/F1TelemetryData/3015 | `7f8738461453846c9d92f4625422c6f2efe8cc799446469609b75a85f700b56c` | 2025-12-06T15:30:56+00:00 |
| `quali-lap-delta` | https://t.me/F1TelemetryData/3016 | `b2f09bdd459cd72658aea3fab10fbbe6ab502991fbff34ba16790ded411e1654` | 2025-12-06T15:30:58+00:00 |
| `quali-track-dominance` | https://t.me/F1TelemetryData/3017 | `1c13efd52828716b24cb3da00142c63e73a999e0656e13bbdee5c6ce1fd8f12c` | 2025-12-06T15:30:59+00:00 |
| `quali-top-speeds` | https://t.me/F1TelemetryData/3019 | `c49f5aa28ec3c576581f9f2f1c3f661b5bc5be2db2cb7cbbc45523bee94602e1` | 2025-12-06T15:31:01+00:00 |
| `quali-throttle-usage` | https://t.me/F1TelemetryData/3021 | `d4d1797f3c238a819200a482398d8a81315dbab366bcb9c94c6d9d42140e533a` | 2025-12-06T15:31:04+00:00 |
| `quali-lap-sections` | https://t.me/F1TelemetryData/3022 | `cd97a4d04f27ae7af1ecc212113176930b910e73cab6912d0f804d880fabb6c3` | 2025-12-06T15:31:05+00:00 |
| `quali-timings` | https://t.me/F1TelemetryData/3023 | `7f1966c6b39d99d1238bac11f2d2b0beb5edee6d23cc29072d769e92507cf58c` | 2025-12-06T15:31:06+00:00 |
| `race-pace` | https://t.me/F1TelemetryData/3026 | `d758215aa47f4ec5ae24482dd13361751df29cd151f671a8ab7d28306f348a18` | 2025-12-07T15:25:56+00:00 |
| `tyre-strategies` | https://t.me/F1TelemetryData/3027 | `a456eb1e7b12e04174eb8452065cd52ca652fec03b9ea3f622488e54e6427186` | 2025-12-07T15:25:57+00:00 |
| `average-gap` | https://t.me/F1TelemetryData/3028 | `cf7e9a61dc6bd19a041c2d53e2849eeb36735fc2514235d838ce97262e9f82a2` | 2025-12-07T15:25:58+00:00 |
| `pit-times` | https://t.me/F1TelemetryData/3031 | `12d837dfe3964acf578582dcc40a0599313b63ae43cab747d56dde62019bd651` | 2025-12-07T15:26:02+00:00 |
| `race-timings` | https://t.me/F1TelemetryData/3033 | `7024b1bef890aafcd5da6c0df315f4ca7ba9c973821e3ebe9d96ea280fd69f57` | 2025-12-07T15:26:05+00:00 |
| `lap-times` | https://t.me/F1TelemetryData/3034 | `f0c16c7ba41b2c9ace9ff59b8f6315befe4975675ada25426683bf7be4948055` | 2025-12-07T15:26:06+00:00 |

参考图只用于语义、人工数值转录和差异核对；原图、logo、人物照和
品牌字体不进入产品资产。

## 公开可确认的方法

- 排位包按每名车手整个 session 最快圈展示 lap delta、速度/油门、
  sector timing、timing-line speed 和最快圈最高速。
- Race Pace 为圈时分布；Average Gap 是同一圈集合的均值相对最快均值。
- Tyre Strategies 按 Stint 的配方、圈边界和新/旧胎线型展示。
- Pit Times 的坐标明确写作 `Time in Pitlane`，与 stationary stop time
  不是同一个字段。

## 反推实现

- Q1/Q2/Q3 用冻结 OpenF1 race-control 绿灯/方格旗时间窗识别；
  88/88 个准确推圈均获得阶段标签。该补充只服务本 pilot，不回写全局
  qualifying_v4。
- Track Dominance 在统一距离轴上取累计用时最小车手；这只是观察到的
  时间领先，不是车辆物理 dominance。
- Lap Sections 使用约 3.7Hz car-channel：`brake>0`；无制动且
  `throttle<=0` 为 lift；无制动且 `throttle>=99` 为 full；其余 partial。
- Race Pace 复用已外部逐项验证的圈宇宙：删除首圈、Stint 切换两侧圈和
  非绿/黄旗圈，按算术均值排序。
- Sector 和 speed trap、pit-lane/stop duration、tyre_age_at_start
  来自冻结 OpenF1 v1 响应，每个 URL、响应和 manifest 均有 SHA-256。

## 不可识别与 NOT_TESTED

- 官方几何 sector distance anchor 未冻结；页面上的距离位置是由官方
  sector time 与本地参考圈插值得到，状态为
  `NOT_TESTED_OFFICIAL_GEOMETRIC_ANCHOR`。
- Downforce Map 所需真实下压力、阻力、设定和动力模式不可识别，状态
  `NOT_IDENTIFIABLE`。
- 真实燃油、SOC、胎温胎压、物理磨损、损伤、车手管理和车队指令
  仍不可识别。
