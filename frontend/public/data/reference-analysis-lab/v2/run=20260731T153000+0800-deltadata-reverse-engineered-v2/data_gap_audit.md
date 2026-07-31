# DeltaData data-gap audit v2

| 目标字段 | 当前字段 | 缺口 | 替代代理 | 发布状态 |
| --- | --- | --- | --- | --- |
| 逐圈 clean-air 时间比例 | 逐点绝对时间、rel_distance、distance、speed；重建 audited_traffic_ratio | 原作者阈值未公开 | 同位置物理前车 headway≤2s；交通≤20% | ALLOW_AS_NAMED_PROXY |
| 代表圈人工复核标签 | is_accurate/deleted/旗态/进出站边界 | 人工逐圈判定不可得 | Huber 自动鲁棒下权异常残差 | METHOD_EQUIVALENT_ONLY |
| 真实燃油与燃油效应 | 圈号和比赛阶段 | 真实油量不可识别 | public 0.032 与 v17 low/base/high 具名线性情景 | SCENARIO_ONLY |
| 轮胎配方与胎龄 | compound、tyre_life、stint | 胎温/胎压/物理磨损不可识别 | 观测胎龄条件项及共同支持 | ALLOW_PROXY_NOT_PHYSICAL_WEAR |
| 相近赛道阶段 | lap/max_lap | 真实赛道抓地演化不可单独识别 | lap_fraction 一次/二次条件项与阶段重叠门 | ALLOW_CONDITIONAL_PROXY |
| 故障/损伤/动力模式 | 无稳定逐圈真值 | 不可识别 | Huber 只可下权异常，不能给出原因 | NOT_IDENTIFIABLE |
| 赛季 GP 质量权重 | 样本量、Kish ESS、支持重叠、敏感性 | 原作者精确权重公式未公开 | 本 pilot 只披露质量字段，不做 2026 赛季汇总 | NOT_PUBLISHED_AS_SEASON_ORDER |
| 2025 Abu Dhabi 原作者对应数值图 | 没有有效公开参考图缓存 | 缓存第二图实际是 Ferrari 赛车照片 | 只做真实 pilot 与 v1 方法差异；不声称对图误差 | NO_EXTERNAL_NUMERIC_CLAIM |
