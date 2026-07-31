# 2023–2025 四队单场—stint—圈级反事实解释 v4

- 运行 ID：`four_team_event_stint_attribution_2023_2025_v4-20260719T103318+0800`
- Race 清洗圈：61351；四队圈级解释：25343；stint：1298；分站：69。
- Qualifying accurate 推圈：3469；覆盖 67 场。
- 身份骨架后验：4 链 × 600；最大尺度 R-hat=1.024。
- 周末车辆上沿只融合排位/正赛的相对优势，不融合绝对圈时；公开数据无法验证两辆车的具体 setup、升级和损伤完全相同。

## 2025 每站周末车辆统计上沿

| 分站 | 上沿第一 | 第二 | 第三 | 第四 | 分类代理一致性 |
| --- | --- | --- | --- | --- | --- |
| Abu Dhabi Grand Prix | McLaren | Mercedes | Red Bull Racing | Ferrari | 3/4 配速相容 |
| Australian Grand Prix | McLaren | Red Bull Racing | Mercedes | Ferrari | 4/4 配速相容 |
| Austrian Grand Prix | McLaren | Ferrari | Mercedes | Red Bull Racing | 4/4 配速相容 |
| Azerbaijan Grand Prix | Red Bull Racing | Mercedes | McLaren | Ferrari | 4/4 配速相容 |
| Bahrain Grand Prix | McLaren | Mercedes | Ferrari | Red Bull Racing | 4/4 配速相容 |
| Belgian Grand Prix | McLaren | Ferrari | Red Bull Racing | Mercedes | 4/4 配速相容 |
| British Grand Prix | McLaren | Red Bull Racing | Mercedes | Ferrari | 3/4 配速相容 |
| Canadian Grand Prix | Mercedes | Red Bull Racing | McLaren | Ferrari | 4/4 配速相容 |
| Chinese Grand Prix | Mercedes | Red Bull Racing | McLaren | Ferrari | 2/4 配速相容 |
| Dutch Grand Prix | McLaren | Mercedes | Red Bull Racing | Ferrari | 4/4 配速相容 |
| Emilia Romagna Grand Prix | McLaren | Ferrari | Red Bull Racing | Mercedes | 4/4 配速相容 |
| Hungarian Grand Prix | McLaren | Mercedes | Ferrari | Red Bull Racing | 4/4 配速相容 |
| Italian Grand Prix | Ferrari | Mercedes | Red Bull Racing | McLaren | 1/4 配速相容 |
| Japanese Grand Prix | McLaren | Mercedes | Red Bull Racing | Ferrari | 3/4 配速相容 |
| Las Vegas Grand Prix | Mercedes | McLaren | Ferrari | Red Bull Racing | 4/4 配速相容 |
| Mexico City Grand Prix | Mercedes | Ferrari | McLaren | Red Bull Racing | 2/4 配速相容 |
| Miami Grand Prix | McLaren | Ferrari | Mercedes | Red Bull Racing | 3/4 配速相容 |
| Monaco Grand Prix | McLaren | Ferrari | Red Bull Racing | Mercedes | 4/4 配速相容 |
| Qatar Grand Prix | McLaren | Red Bull Racing | Mercedes | Ferrari | 4/4 配速相容 |
| Saudi Arabian Grand Prix | McLaren | Red Bull Racing | Mercedes | Ferrari | 3/4 配速相容 |
| Singapore Grand Prix | Mercedes | Ferrari | McLaren | Red Bull Racing | 3/4 配速相容 |
| Spanish Grand Prix | McLaren | Mercedes | Ferrari | Red Bull Racing | 4/4 配速相容 |
| São Paulo Grand Prix | Mercedes | McLaren | Ferrari | Red Bull Racing | 4/4 配速相容 |
| United States Grand Prix | Ferrari | Red Bull Racing | Mercedes | McLaren | 2/4 配速相容 |

## 输出如何使用

- `events[].cars`：每站纯车辆后验、Race/Qualifying 条件前沿、周末上沿排名、区间和分类代理一致性。
- `events[].drivers`：每站纯车手后验、实际干净圈排名、模型组合排名、执行损失和赛果相容性。
- `stints[]`：每个车手每个 stint 的实跑/期望圈速、执行损失、稳定性和相对上下文的超额衰退斜率。
- 圈级可追溯文件：`research/artifacts/data/four_team_lap_explanations_2023_2025_v4.csv.gz`；其 SHA-256 为 `92b04b3744cfadb23c2c16a3b0d090a0d8d87481f2ec824688f473d6a1e4db66`。
- 这是 2025 已查看后的回顾性描述扩展，不把同场解释性能冒充赛前预测准确率。
