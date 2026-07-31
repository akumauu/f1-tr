# PAC 车手级时间外验证（2023–2025）

- 结论：**未通过**；PAC 发布值保持 `null`：`True`。
- 完整分站 rolling-origin：60；未来 driver-event：1080。
- Pairwise direction：0.666；Brier：0.213；log score：0.611。
- Pair-gap MAE：0.366 z；相对零基线改善：12.3%。
- 分量内跨 origin 排序稳定性（仅诊断）：1.000。
- Pair-gap 80%/95% 预测区间覆盖：0.762 / 0.939。
- transfer graph 分量：5；全局连通：False。
- 冻结后确认性比赛集群：0 / 16；2026 冻结前四站不计入。
- 失败门槛：minimum_block_bootstrap_balanced_accuracy_95_low、minimum_block_bootstrap_pair_margin_spearman_95_low、minimum_brier_skill_vs_best_baseline、minimum_block_bootstrap_brier_skill_95_low、minimum_log_loss_improvement_vs_best_baseline、minimum_wis_improvement_vs_best_baseline、minimum_post_freeze_confirmatory_event_clusters、minimum_post_freeze_confirmatory_pair_events、minimum_identifiable_novel_edges、frozen_0_100_mapping_validation、complete_benchmark_suite、complete_negative_control_suite、connected_transfer_graph。

边界：验证目标只来自未来同队 pair-gap 的条件干净配速；未使用积分、完赛名次或 classification proxy。覆盖率只属于 held-out pair-gap predictive distribution，不是 pooled latent CI coverage。分层区间属于独立的 `conditional_pair_latent_z` 原型，与旧 `pooled_driver_base_z` 和旧车手卡断开；未验证冻结 0–100 映射前 PAC 恒为 null。
