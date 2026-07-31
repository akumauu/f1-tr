# GP Tempo 参考差异说明

- 公开页面披露完整三步算法，但不提供固定 2025 Abu Dhabi 数值表，故数值参考状态为
  `NOT_APPLICABLE_PUBLIC_METHOD_PAGE_HAS_NO_FIXED_PILOT_VALUES`。
- 本次以算法不变量验收：270 个 sector 端点的最大
  绝对误差为 `0.0s`；
  90 个终点全部闭合。
- 原始样本采样率范围为 `4.149–4.167Hz`，
  中位 `4.167Hz`，与公开“约 4 Hz”一致。
- 段内曲线没有公开逐点真值，固定标记
  `ESTIMATED_LINEAR_INTERPOLATION_APPROX_4HZ`，不报告伪造的逐点 MAE。
- 默认 Qualifying 三圈同 session、同 SOFT、胎龄差不超过 2 圈；跨 session 或轮胎
  条件不满足时只显示条件警告，不改名为纯车手差。
