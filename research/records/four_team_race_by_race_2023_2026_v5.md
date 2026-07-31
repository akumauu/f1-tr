# 2023–2026 四队逐站车辆—车手分层反事实归因 v5

- 运行 ID：`four_team_race_by_race_2023_2026_v5-20260719T130139+0800`。
- 2023–2025：计划 70 站，形成正式逐站分析 69 站；缺失站保留显式空记录。
- 圈级模型与遥测键匹配率：100.00%；平均速度质量通过率：99.92%。
- 同场回顾性模型第一/最快圈第一一致率：53.6%；模型第一/分类第一一致率：62.3%。
- 只用先前分站的滚动基线共验证 64 站；最快圈第一命中率 42.2%，分类第一命中率 46.9%。
- Stint：1163/1298 可做早段→晚段留出；中位晚段 MAE=610.0 ms；中位奇偶圈差=93.6 ms。
- 2026 本地只有 4 站，确认性门槛为 5 站，因此发布状态为 `exploratory_only`。

## 这版修正了什么

1. 主验证不再依赖六站手选样本；所有赛历分站按相同规则进入或留下缺失原因。
2. 每站同时列本场模型上沿、本场最快清洁圈和本场分类，不再跨年取最快值。
3. 每个 stint 都有奇偶圈复验；样本足够时增加早段拟合→晚段留出和斜率 bootstrap 区间。
4. 赛季总结按分站等权；另列无当前站特征的时间前推基线，避免把回顾性一致率冒充预测率。
5. 2026 只继承车手先验，车辆与车手—车辆交互归零后强收缩更新。

## 可视化

- `research/artifacts/figures/race_by_race_v5/race_by_race_2023_speed_consistency.png`
- `research/artifacts/figures/race_by_race_v5/race_by_race_2024_speed_consistency.png`
- `research/artifacts/figures/race_by_race_v5/race_by_race_2025_speed_consistency.png`
- `research/artifacts/figures/race_by_race_v5/validation_top_team_match.png`
- `research/artifacts/figures/race_by_race_v5/stint_method_validation.png`
- `research/artifacts/figures/race_by_race_v5/2026_exploratory_reset_update.png`

## 使用边界

- 平均速度是描述值，赛道长度与比赛情境不同，跨站比较只使用各站四队中心化后的相对值。
- 排位与正赛只融合相对优势，不合并绝对圈时；公开数据无法证明两车具体调校、升级和损伤完全相同。
- 分类代理不含全部赛后判罚修订；策略、可靠性、交通、安全车、事故和损伤未进入因果赛果模型。
- 2026 四站 bootstrap 只描述当前点估计；图中另列历史锁定时间前推误差校准的宽预测带，仍不是完整赛季确认结论。
