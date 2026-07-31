# gptempo · data-gap audit

- coverage rows：70。
- 状态分布：`{"status": {"NOT_TESTED_MISSING_FROZEN_OFFICIAL_SECTOR_INPUT": 69, "ENDPOINT_VALIDATED": 1}}`。
- 缺失不会被补成零；没有公开同场真值时保持 `NOT_TESTED`。
- 网络、闭源或补充源没有在 v3 重新抓取；沿用的冻结补充均由 v2 URL、抓取时间与 SHA-256 身份链约束。
- 本地原始逐点读入只用于生成 `.runtime-cache` 中间表；正式报告只发布精简数值和输入哈希。
