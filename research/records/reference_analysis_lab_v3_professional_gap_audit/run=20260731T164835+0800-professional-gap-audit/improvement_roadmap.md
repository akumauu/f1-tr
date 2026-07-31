# F1 TR v3 专业平台差距改进路线图

本路线图不修改冻结 v3；所有改进进入后续 append-only run。依赖顺序优先于类别顺序。

## 依赖主链

1. B-001 冻结三层验证协议。
2. B-002 在 selection-validation 预注册绝对门和不确定区间，并在 untouched final test 确认。
3. B-003 补齐代码、缓存、依赖和时间身份。
4. B-004 扩充同场公开真值，同时永久保留至少一个 final event。
5. B-005 在产品层真正分离 visual_replication 与 audited_analysis。

## 当前冻结数据即可完成

### B-003 补齐代码/配置/依赖/缓存/时间身份

- 优先级/顺序：P1 / 1
- 依赖：无
- 工作量：M（2-4 人日）
- 验收阈值：所有生成代码、config、lock hash 可重算；任一算法/config 改动强制 cache miss；frozen<=started<=finished。
- 可验证收益：把“当前源码产生当前产物”从推断变为可验证事实。
- 代码：manifest writer、cache_is_valid、run clock identity。
- 测试：hash graph、cachebuster、future-time rejection tests。
- 产物：provenance.json、cache identity v4、environment-lock.txt。
- 文档：docs/implementation-results.md、docs/progress.md。
- 回退：身份不完整时全量重算并标 PROVENANCE_INCOMPLETE。

### B-015 完整产品能力清单与推断边界

- 优先级/顺序：P2 / 1
- 依赖：无
- 工作量：S（1-2 人日）
- 验收阈值：每个平台能力均标 public_reference/local_freeze/inference；未冻结项 UNKNOWN；不生成总复刻率。
- 可验证收益：防止把本审计 57 个子项误当完整产品排名。
- 代码：仅追加 inventory generator。
- 测试：UNKNOWN exclusion、scope label tests。
- 产物：capability_inventory.csv/json。
- 文档：本审计报告附录。
- 回退：证据不足则不补全能力清单。

### B-006 修复 D 目标 Q phase 状态与任务级 registry

- 优先级/顺序：P1 / 2
- 依赖：B-003
- 工作量：S（1-2 人日）
- 验收阈值：2025 Abu Q phase 两表均 NOT_TESTED；registry 具有 24×7 个 task identities；team throttle max=3.084442pp。
- 可验证收益：防止 NOT_TESTED 被计作已参考，恢复任务级尾部可见性。
- 代码：v4 generator task truth matrix；不回写 v3。
- 测试：event×task truth、registry cardinality、metric completeness tests。
- 产物：chart_reference_matrix_v4.csv、task_model_registry.json。
- 文档：docs/implementation-results.md。
- 回退：v3 审计层继续覆盖矛盾并从计数排除。

### B-007 发布正式采样频率、缺失率与处理链

- 优先级/顺序：P1 / 2
- 依赖：B-003
- 工作量：S（1-2 人日）
- 验收阈值：70/70 event 有 source/resampled Hz、missing、interpolation、smoothing；与缓存重算误差<1e-9。
- 可验证收益：控制占比与段内曲线差异可被追踪到处理身份。
- 代码：coverage schema v4 和 sample identity collector。
- 测试：70-event sampling identity test。
- 产物：sampling_registry.csv。
- 文档：docs/database.md（若 schema 变更）、docs/algorithm-models.md。
- 回退：缺采样身份的任务 NOT_TESTED。

### B-013 拆分 reference-informed 身份

- 优先级/顺序：P1 / 2
- 依赖：B-003
- 工作量：S（1-2 人日）
- 验收阈值：四字段身份完整；A-C design_reference_informed=True；只有未见 final 可 confirmation_blind=True。
- 可验证收益：可追溯设计参考、拟合参考与真正盲测的差别。
- 代码：registry schema v4。
- 测试：semantic identity tests。
- 产物：reference_use_registry.json。
- 文档：algorithm-models。
- 回退：统一标 REFERENCE_INFORMED_NOT_BLIND。

## 需新增公开数据冻结

### B-004 扩充五目标同场数值真值并保留最终事件

- 优先级/顺序：P1 / 3
- 依赖：B-001,B-003
- 工作量：L（5-10 人日，受公开数据可得性约束）
- 验收阈值：每个可量化目标至少 3 个独立事件、2 条赛道；至少 1 个事件 untouched final；URL/抓取时间/page id/hash/读图误差齐全。
- 可验证收益：把 FData/GP 段内/Q phase 的 UNKNOWN/NOT_TESTED 转为可审计同量纲指标。
- 代码：只新增 reference freezer、transcription schema 和 reader uncertainty 模块。
- 测试：hash/许可/坐标标定、零重拟合 final-event 测试。
- 产物：reference_inventory.json、frozen truth tables、attempts.jsonl。
- 文档：method cards、data-gap audit、docs/algorithm-models.md。
- 回退：三次实质不同尝试仍无真值则保持 UNKNOWN/NOT_TESTED。

### B-010 获取 FData 同场距离轴和 GP Tempo 段内逐点真值

- 优先级/顺序：P1 / 3
- 依赖：B-003
- 工作量：L（5-8 人日，可能不可得）
- 验收阈值：FData/GP 各>=3 事件；逐点真值带轴标定/读取误差；1 个 final event。
- 可验证收益：首次允许报告真正的逐点 MAE/P90/max，而非端点或自一致性。
- 代码：reference freezer/axis calibrator。
- 测试：same-event identity、axis monotonicity、reader uncertainty tests。
- 产物：point_truth.csv、calibration.json。
- 文档：method cards。
- 回退：不可得时永远 UNKNOWN/NOT_TESTED，不估算 MAE。

### B-014 扩充 F1TelemetryData Q 阶段与多事件图表真值

- 优先级/顺序：P1 / 3
- 依赖：B-003,B-006
- 工作量：M/L（4-8 人日）
- 验收阈值：至少 3 事件、Q1/Q2/Q3 各有独立数值真值；1 final event；控制/油门/维修区任务均含 max。
- 可验证收益：验证 2.639pp/1.196pp 误差是否跨事件稳定，消除 Q phase NOT_TESTED。
- 代码：reference freezer/task transcriber。
- 测试：phase identity、same-event、final isolation tests。
- 产物：q_phase_truth.csv、multi_event_chart_truth.json。
- 文档：method card。
- 回退：无逐阶段真值则保持 NOT_TESTED。

## 算法改进

### B-001 建立 selection-validation 与 untouched final-test 三层协议

- 优先级/顺序：P0 / 1
- 依赖：无
- 工作量：L（5-8 人日）
- 验收阈值：每个发布模型至少 2 个 final events；final 标签不参与选择；最终 MAE/P90/max 单独输出。
- 可验证收益：消除选择复用偏差；11 个 DeltaData 专用模型获得可验证确认或自动回退。
- 代码：research/run_reference_analysis_lab_track_validation_v4.py 或新增 protocol 模块；不回写 v3。
- 测试：新增 split disjointness、final-label invariance、selection perturbation tests。
- 产物：split_registry_v4.csv、selection_registry.json、final_confirmation.json。
- 文档：docs/algorithm-models.md、docs/implementation-results.md、docs/progress.md。
- 回退：未完成前专用模型 audit_only，保留 universal。

### B-002 加入绝对误差门、尾部门和事件簇不确定区间

- 优先级/顺序：P0 / 2
- 依赖：B-001
- 工作量：M（3-5 人日）
- 验收阈值：Delta 试行 MAE<=0.25s、P90<=0.50s、max<=1.50s；改善 90% cluster-CI 下界>0；阈值在 final test 前冻结。
- 可验证收益：当前 Australia 11.168s 模型自动 FAIL；避免相对坏基线的伪发布。
- 代码：candidate_publication_decision v4、event-cluster bootstrap。
- 测试：绝对门边界、Australia 回归、CI 可重复性测试。
- 产物：absolute_gate_registry.json、uncertainty.csv、tail_ledger.csv。
- 文档：docs/algorithm-models.md、docs/implementation-results.md。
- 回退：门失败则 audit_only 且回退 universal/no-model。

### B-008 改为事件级支持门与事件簇 bootstrap

- 优先级/顺序：P1 / 2
- 依赖：B-001
- 工作量：M（3-5 人日）
- 验收阈值：track fold 独立训练事件<3 时 FAIL；事件总权重相等；发布 event-level ESS 与 90% CI。
- 可验证收益：阻止行级伪重复把 2 个事件包装成数千 ESS。
- 代码：weighting/ESS/cluster bootstrap 模块。
- 测试：event weight equality、low-cluster failure tests。
- 产物：support_registry.json、uncertainty.csv。
- 文档：docs/algorithm-models.md。
- 回退：支持不足回退 cluster/universal。

### B-009 F1pace 连续比例尾部专门校准

- 优先级/顺序：P1 / 3
- 依赖：B-001,B-004
- 工作量：M（2-4 人日）
- 验收阈值：预注册 final event max<=15pp、P99<=15pp；所有 >10pp 单元有原因账本。
- 可验证收益：在不牺牲 33% 分类的前提下压低 72.3pp 极端误差。
- 代码：tail calibration/abstention，禁止对 final event 调参。
- 测试：HUL/HAM/COL 回归、final zero-refit tests。
- 产物：tail_ledger.csv、reader_uncertainty.json。
- 文档：method card、implementation results。
- 回退：尾部失败时只发布分类，不发布连续比例。

## 可视化/产品改进

### B-005 真正分离 visual_replication 与 audited_analysis 并修正文案

- 优先级/顺序：P1 / 4
- 依赖：B-001,B-003
- 工作量：M（3-5 人日）
- 验收阈值：切层后数据源和表行集合确实变化；internal_proxy/外部 truth 标签始终可见；无 final PASS 时不出现 holdout passed。
- 可验证收益：消除证据混排和验证强度误导，用户能区分“像平台”与“可审计”。
- 代码：frontend/src/track-validation-v4.js、数据 schema；不修改冻结 v3 页面。
- 测试：E2E layer isolation、文案禁词、任务状态渲染测试。
- 产物：两层独立 public JSON、browser acceptance manifest。
- 文档：用户指南、docs/implementation-results.md。
- 回退：无法分层时移除 layer 控件，只显示 audited_analysis。

### B-011 目标身份、代理身份和来源角色显式化

- 优先级/顺序：P1 / 4
- 依赖：B-003
- 工作量：S（1-2 人日）
- 验收阈值：每个指标显示 public_reference/local_frozen/internal_proxy/official/unofficial；internal_proxy 不进入平台 gap。
- 可验证收益：避免用户把 OpenF1、图片转录和 FIA 官方数据混为一谈。
- 代码：frontend badges + schema。
- 测试：role rendering/forbidden inference tests。
- 产物：source_role_registry.json。
- 文档：用户指南。
- 回退：无法确定来源角色则显示 UNKNOWN SOURCE。

### B-012 任务级最坏尾部、排除账本和数据缺口卡

- 优先级/顺序：P2 / 5
- 依赖：B-006,B-007
- 工作量：M（2-3 人日）
- 验收阈值：每任务显示 MAE/P90/max、最坏事件/圈、排除率、Kish/event ESS；A/B 不再显示 {}。
- 可验证收益：从平均值导向尾部和支持证据，快速发现 Australia/HUL 等失败。
- 代码：workbench components。
- 测试：worst-tail sort、ledger closure、empty-card tests。
- 产物：browser acceptance screenshots。
- 文档：用户指南、implementation results。
- 回退：字段缺失时显示 NOT_AVAILABLE 而不是空对象。
