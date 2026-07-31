# 2023 British Grand Prix Race Dossier v8

- 发布状态：`audit_only`；整场门槛：未通过。
- 全场观测圈：971；四队观测圈：416；可建模比例：17.8%。
- PAC/OVR：`null`。排位与正赛独立，不输出统一周末排名。

## 独立车辆视图

- 排位潜力代理第一：McLaren。
- 正赛长距离代理第一：Red Bull Racing。

## 发布门槛

- `McLaren:modelable_laps_below_gate`
- `McLaren:valid_stints_below_gate`
- `Red Bull Racing:valid_stints_below_gate`
- `Ferrari:modelable_laps_below_gate`
- `Ferrari:valid_stints_below_gate`
- `Mercedes:modelable_laps_below_gate`
- `Mercedes:valid_stints_below_gate`
- `modelable_lap_fraction_below_gate`

## 解释边界

- 轮胎长度、衰减和进站窗口均为公开数据代理，不是真实燃油或倍耐力内部模拟。
- 累计秒差没有 FIA 最终相邻车辆时间差支持时，不称最终名次主要因素。
- 单场保胎表现只称车手—赛车组合代理。
