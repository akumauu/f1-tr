# F1 TR v3 数据与算法专业平台差距审计

- 审计运行：`20260731T164835+0800-professional-gap-audit`
- 审计性质：append-only、离线、只读复核；未修改任何 v1/v2/v3 或 Race Dossier v14-v17 产物。
- 冻结 v3：`research/records/reference_analysis_lab_v3/run=20260731T235900+0800-track-validation-v3/manifest.json`，SHA-256 `c0db603684f5dada772ca33edeb67b68d4d606254f30154f2cb20324a6b6f9ed`。
- Race Dossier v17：`research/records/race_dossier_v17/run=20260726T124625+0800-ce93134a40b8/manifest.json`，SHA-256 `9322064faee009e0e53d7ec345e9d25499f821658cdf05d497a74d233567658c`。
- 数据重算：70 events / 24 tracks / 55,248,206 Race raw points；invalid/skipped source files=0/0。
- 网络：未调用。五个目标的 URL、抓取时间、页面标识与哈希已有本地冻结证据，继续联网不会改善当前同量纲真值边界。

## 执行摘要

结论不是“v3 已追平五个平台”。57 个可审计子项中，**已证实预测准确率领先为 0**。按矩阵可重算为：领先 0、持平 22、落后 6、未知 29。持平主要是同场表值、公开阈值或端点不变量；它们不能外推完整产品持平。

最关键的发布结论是：v3 对 A-C 的大部分误差是 **F1 TR 内部代理/自一致性**，不是对 F1pace、DeltaData、FDataAnalysis 的公开逐点真值误差。DeltaData 的 11 个 track/cluster 选择还使用了同一批 outer fold 结果完成选择和报告，缺少 untouched final test；因此只能视为 selection-validation 结果。

已证实的外部数值差距集中在：F1pace Qatar 连续交通比例 MAE=2.238pp/P90=6.848pp/max=72.309pp；DeltaData 3 行 H2H MAE=0.09134pp；F1TelemetryData 控制区段 2.639pp、车队油门 1.196pp、pit lane 0.065808s。FDataAnalysis 没有同事件数值真值；GP Tempo 只有 270 个构造端点零误差，段内 MAE 必须保持 NOT_TESTED。

| 平台 | 领先 | 持平 | 落后 | 未知 | 合计 |
|---|---:|---:|---:|---:|---:|
| F1pace | 0 | 5 | 1 | 5 | 11 |
| DeltaData | 0 | 4 | 2 | 6 | 12 |
| FDataAnalysis | 0 | 3 | 0 | 7 | 10 |
| F1TelemetryData | 0 | 6 | 3 | 5 | 14 |
| GP Tempo | 0 | 4 | 0 | 6 | 10 |
| **合计** | **0** | **22** | **6** | **29** | **57** |

计数规则：每行只取一个 `relative_position`。`UNKNOWN/NOT_TESTED` 永不进入优势率；透明度、账本和 fail-close 只列为方法学证据，不被偷换成预测准确率优势。本报告不生成跨任务总复刻率。

## 已证实优势

没有已证实的预测准确率领先项（0 项）。现有公开真值只允许确认若干持平和落后，不能证明五个平台在这些任务上更差。F1 TR 的哈希链、排除账本、不可识别边界和 PAC/OVR=null 属于可审计工程能力；因为无法证明对方完整产品没有同等能力，所以不计为领先。

## 暂定/方法学优势

- 70 场、24 条赛道的内部代理覆盖，以及按赛道/事件的 coverage、split、exclusion ledger，支持定位而不是只给平均分。
- F1pace 的 Abu/Qatar 外部参考从 v3 训练排除；GP Tempo 对跨 session 混杂和直接车手/车辆因果结论 fail-close。
- FDataAnalysis 目标用可观测布局不连续代理禁止 Dutch track model，避免把布局变化误当跨年稳定。
- Race Dossier v17 保持车辆基线→车手交付→策略/进站/位置秒数→残差框架，并保持 PAC/OVR 为 null。

这些是**暂定方法学优势**：本地冻结证据只描述 F1 TR，自身透明度不能证明平台不透明，更不能证明预测更准。

## 已证实差距

1. F1pace 2025 Qatar 1,067 个外部零重拟合单元连续比例长尾明显：MAE 2.238343pp、P90 6.847727pp、max 72.308835pp；颜色读取本身 max 1.5pp，无法解释 72.3pp 最大残差。
2. DeltaData 同场 H2H 仅 3 行且是 reference-informed，MAE 0.09134pp；只有 1 行通过可比门，2 行仍是 audit_only。公开的逐圈人工复核能力尚未实现，F1 TR 以 Huber 自动降权替代。
3. F1TelemetryData 同场：控制区段 MAE/max=2.639122/6.157325pp；车队油门=1.195668/3.084442pp；pit lane=0.065808/0.363s。
4. v3 发布协议缺 untouched final test；相同 outer folds 同时决定选择和报告，不能称最终独立确认。
5. DeltaData Australian Grand Prix selected cluster 的内部 max error=11.168040s；相对发布门没有绝对质量上限。
6. D 目标 Q phase 的 chart_reference_matrix 与 model_comparison 状态冲突；E 目标端点 0 误差是构造不变量，不是段内精度。

## 因闭源/缺真值未知

- F1pace 完整交通算法、逐点配对、缺失积分和产品全量覆盖。
- DeltaData 精确 clean-air 阈值、人工标签、回归代码、赛季权重，以及是否有额外非公开燃油信息。
- FDataAnalysis 同事件逐点距离轴真值、采样/平滑/阈值和完整产品覆盖。
- F1TelemetryData Q1/Q2/Q3 数值真值及完整阈值实现；69/70 事件没有本地同场公开参考。
- GP Tempo sector 内逐点真值和除 2025 Abu Dhabi 外的冻结官方输入；69/70 事件 NOT_TESTED。
- 五个平台的完整产品功能、更新 SLA、历史覆盖、交互质量与内部验证流程。局部截图/页面不能支持总体产品排名。

真实燃油、SOC、胎温、胎压、物理磨损、设定、损伤、动力模式和车队指令均不在公开 telemetry 中；相关结论只能是条件代理。`classification_proxy` 不是 FIA final classification。

## 五个平台逐项审计

### F1pace

- 同场 race pace 表值 20/20 在 0.001s 内，最大差 0.000491s；这是表值复算持平，不是因果方法持平。
- 2s 与 33% 语义一致，Qatar 分类准确率 99.250%；但连续比例 max 72.309pp，必须同时展示尾部。
- v3 universal 24/24；内部代理 mean MAE 0.008733462 ratio，最坏 max 0.523868。这些数字不是平台 gap。

### DeltaData

- selected model=track 6/cluster 5/universal 13；这是选择折结果，不是最终测试。
- v17 输入 31,904 点，经互斥账本后 14,040 clean-air model rows；交通阈值 80% 是 F1 TR 工程选择。
- selected 内部 proxy 的 mean MAE 0.318875s；Australia MAE/P90/max=2.012158/7.680258/11.168040s 仍通过，说明绝对门缺失。
- 公开 0.032s/lap 只能称具名燃油情景，不能称真实燃油识别。

### FDataAnalysis

- v3 universal 24/24，mean MAE 0.000319779 lap fraction；目标是自身 1,200 轴重采样一致性。
- 冻结公开参考是 2026 Hungary Race/FP2，而本地 pilot 是 2025 Abu Dhabi；不同事件/会话，数值对照 NOT_TESTED。
- runtime-cache 只读重算的事件 median sampling interval 范围 0.122997-0.138000s（约 7.246-8.130Hz）；该统计尚未进入正式 v3 manifest。

### F1TelemetryData

- 持平：Q lap delta=0、race average gap MAE=0.000216s/max=0.000471s、fastest lap=0、top speed=0、top-3 sector=0、strategy top5=5/5。
- 落后：control sections、team throttle、pit lane 三项如上；采样频率与不透明阈值是明确限制。
- Q phase model_comparison 为 NOT_TESTED，但 Abu reference matrix 被错误标记为 reference-informed；当前计数按 NOT_TESTED 处理。

### GP Tempo

- 270 sector endpoints 和 90 finish checks 均 max error=0；这是官方 timing 约束实现持平。
- public point truth events=0，segment_mae=null；不得伪造段内 MAE。
- raw sample median 4.167Hz、401 点轴、linear/nearest、no smoothing；尚无采样/插值/平滑敏感性带。

## 数据、验证与产品完成度

- 原始覆盖重算：2023=22 events/17,816,224 points；2024=24/18,998,812；2025=24/18,433,170。
- v3+目标文件 hash graph 共复核 131 项，0 失败；但正式 manifest 未绑定生成器代码、库代码、依赖版本。
- v3 浏览器 acceptance 的两次运行、目标 manifest 与 20 张截图哈希已复核；首轮 CSS viewport 身份失败被保留，latest r2 为 PASS，5 个移动端 CSS width 均为 390。v2 五目标 acceptance 也已复核；F1pace v2 只有 browser_acceptance.json、没有独立 acceptance_manifest.json。
- 训练/测试事件在单个 fold 内分离；问题在于 fold 的测试结果随后又参与模型族选择，因此不是 untouched final test。
- Kish ESS 是行级权重 ESS；track fold 只有 2 个独立训练事件时，数千行 ESS 不能代表数千独立样本。
- `state.layer` 只激活方法卡，comparison/coverage 没有按层分流；当前 visual/audited 分层是标签，不是数据隔离。
- 浏览器验收保留首轮 CSS viewport 失败和 r2 通过，证明画面可用性；它没有验证 layer 数据隔离或统计语义。

## 最高风险 Top 10

1. **P-001 track/cluster publication gate**（P0）：选择偏差；最终 MAE/P90/max 不再是无偏发布证据。 验收：测试读取 split_registry，断言每个发布模型至少有 2 个未被任何选择逻辑读取的 final-test events；篡改 final-test 标签不得改变 selected_model_type；最终指标单独发布。
2. **P-002 australian-grand-prix/selected cluster/max_abs_error**（P0）：相对较差基线的改善被误称为足够准确。 验收：澳大利亚当前模型必须 FAIL/audit_only；任何 universal 也必须通过相同绝对门；测试覆盖阈值边界。
3. **P-003 v3 target vs public-platform truth**（P0）：构念效度失配；内部拟合好不等于平台复刻准确。 验收：schema 测试禁止 internal_proxy 指标出现在 same_scale_numeric_gap 或平台准确率摘要；所有 A-C 当前 v3 指标显示“内部稳定性”。
4. **P-004 generator code hash; dependency versions; cache config identity**（P1）：不可复现或陈旧缓存造成静默污染。 验收：修改任一算法常量或 config 后 cache_is_valid 必须失败；正式 manifest 的所有代码/依赖 hash 可重算一致。
5. **P-005 row-level ESS vs event clusters**（P1）：伪重复导致门槛虚高，置信度偏乐观。 验收：track fold 只有 2 个训练事件时，不论行级 ESS 多大均 FAIL；事件总权重在容差 1e-9 内相等；输出 cluster CI。
6. **P-006 HUL lap7/max_abs_error_pp**（P1）：尾部误差可能改变逐圈解释。 验收：零重拟合 final event 的 max<=15pp、P99<=15pp，且所有 >10pp 单元有可审计原因；阈值须在新 final event 前冻结。
7. **P-007 2025 Abu Dhabi/status**（P1）：NOT_TESTED 被污染，优势率/覆盖率可能虚增。 验收：2025 Abu Q phase 两张表状态一致为 NOT_TESTED；自动测试禁止 event-level boolean 覆盖 task-level truth。
8. **P-008 state.layer/renderComparison/renderCoverage**（P1）：语义混排和验证身份误导。 验收：E2E 切层后至少一个 dataset id 和表行集合改变；快照断言不再出现“完全留出/holdout passed”直到 final-test PASS。
9. **P-009 confidence intervals and fold stability**（P1）：模型选择不稳定、最坏尾部不可预期。 验收：专用模型只有在改善 90% CI 下界>0、最坏折不劣化且 final-test 过门时发布；不足则 audit_only。
10. **P-010 reference_informed flag**（P1）：参考泄漏语义不清，难以审计选择偏差。 验收：A-C 的 method_design_reference_informed=True；只有未见过的 final confirmation 可 confirmation_blind=True。

## 最值得先做的 5 项优化

1. **B-001 建立 selection-validation 与 untouched final-test 三层协议**：消除选择复用偏差；11 个 DeltaData 专用模型获得可验证确认或自动回退。 验收阈值：每个发布模型至少 2 个 final events；final 标签不参与选择；最终 MAE/P90/max 单独输出。
2. **B-002 加入绝对误差门、尾部门和事件簇不确定区间**：当前 Australia 11.168s 模型自动 FAIL；避免相对坏基线的伪发布。 验收阈值：Delta 试行 MAE<=0.25s、P90<=0.50s、max<=1.50s；改善 90% cluster-CI 下界>0；阈值在 final test 前冻结。
3. **B-003 补齐代码/配置/依赖/缓存/时间身份**：把“当前源码产生当前产物”从推断变为可验证事实。 验收阈值：所有生成代码、config、lock hash 可重算；任一算法/config 改动强制 cache miss；frozen<=started<=finished。
4. **B-004 扩充五目标同场数值真值并保留最终事件**：把 FData/GP 段内/Q phase 的 UNKNOWN/NOT_TESTED 转为可审计同量纲指标。 验收阈值：每个可量化目标至少 3 个独立事件、2 条赛道；至少 1 个事件 untouched final；URL/抓取时间/page id/hash/读图误差齐全。
5. **B-005 真正分离 visual_replication 与 audited_analysis 并修正文案**：消除证据混排和验证强度误导，用户能区分“像平台”与“可审计”。 验收阈值：切层后数据源和表行集合确实变化；internal_proxy/外部 truth 标签始终可见；无 final PASS 时不出现 holdout passed。

完整依赖顺序、工作量、代码/测试/产物/文档清单见 `improvement_roadmap.md` 和 `improvement_backlog.csv/json`。

## 机器可读证据入口

- `audit_matrix.csv/json`：57 个子项，平台计数可重算。
- `problem_register.csv/json`：18 个细粒度问题，每项含证据、根因、影响、验收与回退。
- `improvement_backlog.csv/json`：四类 15 项 backlog。
- `independent_recalculation.json`：hash graph、覆盖、selected metrics、最坏单元、源码语义和状态矛盾。
- `manifest.json`：输入/输出哈希、命令、计数、网络跳过与限制。

## 可推断与不可推断

可以推断：本地冻结事件上的同量纲误差、公开阈值/端点不变量、v3 内部协议和产品代码行为。不能推断：闭源实现细节、未冻结事件准确率、平台完整产品能力、真实燃油/SOC/胎况/设定/损伤，以及任何跨任务总体排名。
