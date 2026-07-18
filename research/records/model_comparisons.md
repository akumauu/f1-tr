# 首轮模型配对分站 Bootstrap

- 运行 ID：`locked_model_pairwise_bootstrap-20260717T001532`
- 模型和 alpha 完全复用首轮锁定结果，没有重新调参。
- delta = first MAE - second MAE，负数表示 first 更好。

| 比较 | 观测 delta | 95% CI | first 更优概率 |
| --- | ---: | ---: | ---: |
| driver_team_ridge_vs_zero_relative_pace | -0.32782 | [-0.41977, -0.20906] | 1.000 |
| driver_team_ridge_vs_context_ridge | -0.12871 | [-0.15782, -0.10158] | 1.000 |
| driver_team_ridge_vs_driver_only_ridge | -0.04755 | [-0.09713, -0.01627] | 1.000 |
| driver_team_ridge_vs_team_only_ridge | -0.03669 | [-0.04373, -0.02969] | 1.000 |
| team_only_ridge_vs_driver_only_ridge | -0.01086 | [-0.06046, 0.02259] | 0.646 |

## Race 与 Sprint 分层

### Race

| 比较 | 观测 delta | 95% CI | first 更优概率 |
| --- | ---: | ---: | ---: |
| driver_team_ridge_vs_zero_relative_pace | -0.36261 | [-0.45235, -0.24051] | 1.000 |
| driver_team_ridge_vs_context_ridge | -0.13215 | [-0.16284, -0.10306] | 1.000 |
| driver_team_ridge_vs_driver_only_ridge | -0.04929 | [-0.10280, -0.01558] | 1.000 |
| driver_team_ridge_vs_team_only_ridge | -0.03839 | [-0.04481, -0.03212] | 1.000 |
| team_only_ridge_vs_driver_only_ridge | -0.01090 | [-0.06289, 0.02452] | 0.629 |

### Sprint

| 比较 | 观测 delta | 95% CI | first 更优概率 |
| --- | ---: | ---: | ---: |
| driver_team_ridge_vs_zero_relative_pace | 0.22226 | [0.10621, 0.32700] | 0.000 |
| driver_team_ridge_vs_context_ridge | -0.07431 | [-0.11966, -0.03952] | 1.000 |
| driver_team_ridge_vs_driver_only_ridge | -0.02008 | [-0.04082, -0.00469] | 0.995 |
| driver_team_ridge_vs_team_only_ridge | -0.00978 | [-0.02383, 0.00534] | 0.898 |
| team_only_ridge_vs_driver_only_ridge | -0.01030 | [-0.04434, 0.01872] | 0.744 |
