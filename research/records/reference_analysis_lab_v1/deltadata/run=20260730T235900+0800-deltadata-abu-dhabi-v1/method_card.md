# DeltaData 方法卡 · 2025 Abu Dhabi Race pilot

- run_id: `20260730T235900+0800-deltadata-abu-dhabi-v1`
- target_status: `METHOD_EQUIVALENT_ONLY`
- profile: https://x.com/DeltaData_
- profile_sha256: `f02d08aa445e9f2578272dba7a98de03357b2a65786cf2fe6bed1c3e2213748a`
- fetched_at: `2026-07-30T16:27:43.276722+00:00`

## 原作者页面/帖子

- [LAST 11 H2H MERCEDES CLEAR AIR RACE PACE](https://x.com/DeltaData_/status/2082785101931311292)：clear-air race pace、head-to-head、lower percent is faster
- [Representative race laps analysed by team](https://x.com/DeltaData_/status/2082828055677108520)：representative race laps、team sample disclosure

## 公开方法、视觉反推和不可识别项

公开方法：
- 公开帖子展示 clear-air race pace、H2H 百分比和代表圈样本量语义。
- 公开材料没有给出完整选圈、燃油修正、轮胎拟合或不确定性代码。
视觉反推：
- 排名图使用赛季/比赛标签、车手或车队对象和样本量披露。
- 百分比/排序是描述性结果，不能从图片反推因果模型。
方法等价实现：
- 复用 v17 已冻结的 OOF/cross-fit reliability soft weight。
- 使用 v17 low/base/high 具名燃油修正敏感性，而不是声称真实燃油量。
- 按 Stint 记录清洁空气代理、交通分布、共同胎龄支持和 Kish ESS。
不可识别/不发布：
- DeltaData 的精确代码、选圈阈值、燃油模型、胎衰模型和百分比归一化方式。
- 真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令。
- 不同配方、胎龄、比赛阶段和停站下的无条件车手/车队因果排名。

## 输入身份

- v17 sidecar: `research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/year=2025/round=24/meeting=abu-dhabi-grand-prix/stint_curve_evidence.json`
- sidecar_sha256: `313280fbc0982e3d2724275b03a1c19b7ed5a0f1e316509d819fd4124ab47f52`
- v17 manifest_sha256: `9322064faee009e0e53d7ec345e9d25499f821658cdf05d497a74d233567658c`
- reference images: 2 张，均在缓存中冻结哈希

## 边界

参考图只用于语义/布局核对，未复制 logo、品牌字体或原图作为产品资产。
