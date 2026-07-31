# F1 TR 当前进度与下一步

> 本文是任务状态的唯一入口，更新日期：**2026-07-31**。已经验证的事实见 [`implementation-results.md`](implementation-results.md)；架构、数据库和算法细节不在本文重复。

## 1. 总体状态

项目处于“Race Dossier v17 已完成 2023–2025 共 70 场逐圈证据正式发布，四队
v6 与同队双车 v7 已有客观基础；PAC、完整评分、全场反事实积分与真实数据库生产
验收仍未成立”的阶段。当前严格判断是：**整场复盘与可审计配速证据已成立，但
不可比窗口和不可识别变量不补数，完整评分仍未成立**。

| 工作流 | 状态 | 当前判断 |
| --- | --- | --- |
| 静态前端产品 | Race Dossier v17 本地正式验收完成 | 四级导航、逐圈散点、冻结拟合、配速范围、成对可比门及 2023–2026 官方车队颜色目录已验收；GitHub Pages 发布配置仍需确认 |
| Python 数据生产 | 基础完成 | OpenF1/FastF1 工具齐备，需继续做多分站稳定性与重试验证 |
| Go/PostgreSQL 生产 | 部分完成 | 代码、迁移和 exporter 已有；真实全链路持续验收未完成 |
| MultiViewer 临场采集 | 基础完成 | API 主路径和 DOM 兜底已有，TeamRadio 文本可用性需按赛段验证 |
| TracingInsights 数据层 | v4 + 2026 Race v5 完成 | v5 四场 3,013,786 行通过 raw 状态、绝对时间和值域审计；历史冻结实验继续绑定 v4 |
| 车辆—车手研究 | v6 历史解释 + v7 遥测/评分合同完成 | 历史结果记账门控通过；PAC 车手级时间前推未通过，全部能力、Event Delivery、OVR 和期望积分为空；2026 原始 4/8、严格质量 3/8 |
| 测试与 CI | 本地基线完成 | Python `pytest tests -q` 最近基线为 267/267（2026-07-31）；前端 build/test、v17 曲线、三页面官方颜色 smoke 与五项目参考复刻浏览器验收已通过；缺 CI 和 PostgreSQL 集成测试 |

## 2. 已完成

### 产品与前端

- 静态 SPA、中文 live review、洞察卡片、车手/圈数筛选、TR 搜索和多视图图表。
- Catalunya 默认静态 sample 和 manifest/session 分片合同。
- 前端本地 build/test 与浏览器 smoke 记录。
- 遥测解释工作台已接入四份 2026 Ferrari 单站报告，展示赛段差、异常证据、策略指纹、Episode 与评分边界。
- Race Dossier v17 已发布同配方跨车队的逐圈散点、冻结稳健主拟合和三类配速
  范围；直接秒差只在成对共同胎龄支持与条件平衡门通过时显示，全窗口不满足门控
  时不形成最快/最慢全序。
- 主赛况页、工作台和 Live Review 已共用 Formula 1 官方赛季车队颜色目录；同队
  车手同色并用线型/标记区分，未知身份不再生成替代色。

### 数据工程

- OpenF1/FastF1 原始抓取、PostgreSQL 入库和 Python 静态导出工具。
- PostgreSQL `001`–`012` migration、API 快照状态审计、圈级增强和数据目录。
- MultiViewer 本地 API 结构化采集和 DOM 兜底。
- TracingInsights 2023–2026 原始目录、expanded v4 Parquet、文件/数据体验收。
- 2026 四场 Race expanded v5：原始状态、绝对时间和值域审计；3,013,786 行语义全检 PASS、0 errors。
- PostgreSQL 逐点遥测瘦身，明确 Parquet 真相源和可丢弃缓存。

### 科研

- 2024 训练 / 2025 锁定验证的数据冻结与 v4 迁移复核。
- 零、上下文、仅车手、仅车队、车手+车队 ridge 基线。
- 配对分站 bootstrap、Race/Sprint 分层、车队别名和跨赛季衰减敏感性。
- session head、team×track 和 Bayesian Ridge 探索实验及否定结论。
- 四队样本均衡审计、Hungary 2025 全场识别圈级原型、300 次分层 bootstrap、`V_pace` 有符号组件和 OpenF1/FIA 外部方向性核验。
- 2023–2025 共 70 场覆盖审计、69 场干地有效 Race 的全场分块 ridge、分站—车手等权、Huber IRLS、2024 实用等价带选参与 2025 锁定时间外测试。
- 40 次年份内整站 bootstrap、5 组惩罚敏感性、本地 Live Timing 单站/赛季分类构念检查和 2026 迁移/重置合同；全流程离线。
- 静态分层贝叶斯 v3：车辆、车手和 `driver-team` 在同一后验中联合估计；4 链 Gibbs、R-hat、2024→2025 时间外覆盖、交互先验敏感性与 Red Bull 留一车手影响审计均已落盘。
- 单场—stint—圈级 v4：冻结 7,401 个 Qualifying accurate 推圈，生成 69 场四队车辆/车手报告、25,343 圈可加解释、1,298 个 stint 特征、240 次单场 bootstrap 和 27 组参数边界网格；全流程离线。
- 全量逐站 v5：70 站赛历逐站同构报告（69 站可用、1 站显式缺失），遥测平均速度 100% 键匹配/99.92% 质量通过，1,163 个 stint 完成早段→晚段留出与奇偶圈复验；同场描述、滚动前推和既有锁定时间前推分开报告。
- 2026 四站规则重置探索：仅迁移车手先验，车辆/交互归零并强收缩；使用历史时间前推误差宽预测带，因未过 5 站门槛标记 `exploratory_only`。
- 可解释深度层 v6 优化：`traffic_dirty_air` 使 2024 时间外 MAE 0.747→0.728，`compound_tyre_slope` 退化被拒；分离度改为方差比、成对后验和逐队 12 圈样本三重门控，Hungarian/Qatar high、US/Las Vegas low；driver-event 重建 R²=0.562。106 例明确为配速模型偏差，另有 158 例配速—classification proxy 差异（Spearman=0.750）；half-normal 形状校正 bootstrap 已进入理论上限 CI。三份逐年报告已生成；R-hat=1.020，定向测试 9/9、全量 `pytest tests` 72/72。
- v7 距离域同队解释：冻结 TrackPhase、官方弯角切段、全局最优一对一可比圈、输入调整响应残差、三基线/BH/持续性异常门控、全场 Episode、StintDossier 和 2026 中性策略指纹均已实现；当前测试结果见实现验收文档。
- 2026 Ferrari 四站实跑：可比圈 20/16/15/16；迈阿密因匹配平衡降级；四场异常触发均为 0，且四站 80% 圈差区间全部跨零。跨站方向、OVR、Event Delivery 和期望积分均为空。
- 28 名历史车手候选卡与客观性门控已生成；历史结果记账核心通过，但 PAC 缺车手级时间前推排序与区间验证，PAC 和完整车手卡均不可发布。

## 3. 进行中

| 事项 | 当前产出 | 完成条件 |
| --- | --- | --- |
| GitHub Pages 发布核验 | 前端构建产物已可生成 | 明确 workflow/发布目录，线上路径加载 manifest 和分片通过 |
| v4 模型统一 | v4 首轮基线已完成 | 在 v4 上重跑必要探索/稳健性，消除旧 PG 与 v4 混用 |
| 数据稳定性 | 单分站和历史采集已验证 | 多分站记录 endpoint 成功率、重试、缓存和缺口原因 |
| 文档单一事实源 | 已建立五份主文档和 Agent 路由 | 后续变更持续按维护协议同步 |
| 完整车手能力与价值账本 | PAC/TYM/EXT prototype；其他模块和 OVR 为空 | 先补齐 PAC 的 novel-edge 迁移、proper-score 基线、比赛块 bootstrap、负对照、图/0–100 映射与冻结后确认窗口，再分模块验证并建立全场位置/积分反事实模拟；所有模块通过前不发布 OVR |
| 四队人车归因深化 | v6 已加入 traffic，v7 已有同队 TrackPhase 与 Episode | 将 SC/VSC/天气/赛会控制/进站策略接入 Episode；用预注册全场赛道篮子训练模块；达到 8 场后首次冻结 2026 确认窗口 |

### 已登记、尚未完成

| 事项 | 规范入口 | 下一动作 | 完成条件 |
| --- | --- | --- | --- |
| 四队车辆基准、性能包络、车手提取与价值归因 | `docs/four-team-car-driver-attribution-model.md` | 用 v4 的单场/stint 合同承接 2026，同时继承 v3 车手先验并重置车辆/交互状态；第 5 场后只做一次预注册适配评估 | 按规范完成动态 M2、M3–M5、结果价值层与 2026 前瞻性确认 |

## 4. 阻塞与风险

### P1：影响业务闭环质量

- **TR 英文转录来源缺失**：项目不含 STT；没有 `transcript_en` 时 translator 只能处理无价值占位内容。完成条件是接入可追溯的外部英文转录导入，或明确继续只展示音频元数据。
- **PostgreSQL 全链路未形成自动验收**：需要干净实例执行 migration → ingest → 视图 → exporter，并校验静态合同。
- **OpenF1/FastF1 上游波动**：需要按 endpoint 记录真实空值、失败、重试和代理问题，避免把 0 条误判为无数据。

### P2：工程与安全

- migration 无版本表、checksum/状态审计和正式回滚入口。
- **P2-01 音频代理 SSRF**：遗留音频代理直接请求外部 URL，若恢复公网必须增加协议、域名和内网地址白名单；v1 不启用。
- 缺 CI、PostgreSQL 集成测试、真实大 session 性能测试和 GitHub Pages 路径测试。
- 圈速衰退清洗未充分控制 SC/VSC、黄旗、交通、湿地和进站前后慢圈。

### 研究风险

- 车手与车辆高度共线，公开数据不能保证唯一归因。
- 2025 已作为锁定回顾测试；新增结构只能算探索性，最终确认需使用冻结的 2026 OOD 窗口。
- Sprint 当前表现不支持发布评分。
- Bayesian 区间覆盖不足，不发布个体可信区间。
- Hungary 车辆顺序对两个外部代理一致，但同队车手方向只匹配 2/4；单站交互不可识别，当前车手结果不发布为排名。
- 六站事后压力测试中 Japan/Italy 出现“冠军 Red Bull、模型及官方最快圈代理均判 McLaren 更快”的有价值反例；它支持分离赛果与车辆均值，但不能替代前瞻性验证。
- 六站产物只有队友中位差和最快车代理，且事后选站，已降级为诊断材料，不再作为算法模型或 2026 参数依据。
- 三赛季 v2 的 2025 锁定集 95% split-conformal 覆盖为 94.2%，但 80% 覆盖只有 76.2%；2026 必须重新校准，不能发布过窄区间。
- v3 原始贝叶斯 2025 时间外 80%/95% 覆盖仅 70.9%/86.8%；2024 非一致性校准后为 80.2%/94.0%。动态分站冲击尚未充分建模，2026 不得省略经验校准层。
- Red Bull 拆分仍弱识别：移除 2025 VER 或 LAW/TSU 会使纯车辆优势分别漂移 -0.303/+0.198 z；VER 纯车手优势随交互先验从 +0.643 变为 +0.418 z。不得发布无敏感性标记的唯一车手/赛车真值。
- v4 的 276 个单场车辆名次只有 102 个（37.0%）在 27 组分位数/会话权重下完全固定；默认周末上沿排名必须与 `rank_min/rank_max` 和最佳单车手上沿敏感性一起读。
- v4 的分类代理相容率是同场回顾描述；未建模策略、交通、SC/VSC、损伤、可靠性、事故和罚则前，不能用名次偏差判定“车手超神/失常”或“车辆被高估/低估”。
- v5 的同场模型第一/最快圈第一仅 53.6%，而无当前站特征滚动基线的最快圈第一命中率为 42.2%；两者定义不同，不得合并或挑较高者宣传。
- stint 前半段斜率外推到后半段的中位 MAE 为 610 ms，说明线性超额衰退只能作为局部诊断，不能充当稳定的轮胎物理预测器。
- Qualifying/Race setup 身份不可观测；只能融合 session 内相对优势，不得融合绝对圈时或宣称完全同调教。
- 2024 Red Bull 第四与 2025 McLaren 第一较稳定，但 2023 中间组、2024 领先者/中间组、2025 第 2–4 在合理惩罚下部分换序；当前不支持精确全序。
- 本地分类代理不含赛后 DSQ、罚时和 FIA 最终修订；只用于构念检查，不作为官方积分或训练标签。
- 本地 2026 目前只有 4 场 Race/Qualifying，尚未达到 5 站确认门槛；四队历史误差校准 80% 预测带全部重叠。
- v7 更严格的综合评分门槛为 8 场；当前原始报告为 4/8，严格匹配质量合格仅 3/8，不能把四站遥测模式或同队物理差聚合成 2026 能力排名。
- 旧 `pooled_driver_base_z` 路线因身份列共线与跨分量不可识别已退出 PAC 发布路径；真实数据只能检验 held-out 同队 pair-gap 的预测分布，不能声称 latent PAC 覆盖率。当前 `conditional_pair_laplacian_v1` 只有 3 个可识别 novel edge，且基线、块 bootstrap、负对照、冻结映射和 post-freeze 确认窗口未闭合，因此 PAC 必须保持 `null`。
- v5 已保留 `drs_raw`，但四场值域仍全为 0 且来源是上游 `tel.drs`；主动空力、Overtake、ERS-K 功率和 SOC 仍不可直接识别。
- 单站逐段中置信差异不等于异常；目前四场没有任何信号通过完整持续性门控。未触发也不能证明车辆不存在问题。
- 2026 规则变化可能造成结构断点，必须单独 OOD 报告。

## 5. 推荐执行顺序

### P0：守住现有闭环

1. 将 Python、Go、前端测试接入 CI，确保文档路由和静态合同不漂移。
2. 完成 GitHub Pages workflow 与线上静态路径 smoke test。
3. 每次数据更新保留 manifest、生成时间、来源状态与记录数。

### P1：完成数据库生产验收

1. 启动干净 PostgreSQL 18 测试库，执行 `001`–`012`。
2. 使用 mock OpenF1 做稳定的幂等 ingest 集成测试，再对一个真实 session 做 smoke。
3. 验证 `v_lap_deltas`、`v_tire_degradation`、`v_radio_with_context`。
4. 从真实数据库运行 exporter，前端读取导出结果并完成筛选/图表验收。
5. 为 migration 引入版本表或成熟迁移工具。

### P1：统一科研 v4 口径

1. 在 v4 冻结数据上重跑配对 bootstrap、别名敏感性和必要的阶段 3 模型。
2. 把旧 PG 结果留作迁移证据，不再当当前排行榜。
3. 以三赛季 v2 为线性挑战基线，不再用 2025 调整惩罚；后续复杂模型必须在 2026 OOD 上胜出。
4. 本地 2026 达到第 5 场后冻结首次适配窗口，车辆、能量、空气动力和交互全部重置并单独报告规则断点。

### P1：启动四队人车归因模型

1. 保留 Hungary 单站结果为开发基线，不再用其结果选择结构或阈值。
2. 保持 `balanced_v1` 和 0.01 实用等价选参规则冻结；2026 不得按赛果选择其他惩罚。
3. v4 已保留单场、stint 和圈级残差；下一步为交通、旗帜、损伤/规格缺失建立原因码，并比较条件分位数与一侧随机前沿。
4. 把 Spain 逐点遥测包络扩展到预注册赛道篮子并加入正式 M3；将排位/Race 上沿下钻到 sector/mini-sector，先保持 `V_pace`，不急于计算积分占比。
5. 旧 v5 路线的第 5 场只允许方法监控；综合评分的确认窗口坚持 8 场门槛。届时继承旧规车手先验，重置车辆、能量、主动空气动力、跟车和交互状态，显式区分规则阶段。
6. 先完成 TYM、EXT、ATK、DEF、AWA、STR、ADP 各自的全场机会合同与 rolling-origin 门控；全部通过后再由冻结情境篮子模拟 OVR。
7. 建立全场反事实比赛模拟器，把物理秒数映射为位置概率、P10/P6 与期望积分；在此之前 RaceValue 只显示真实秒数，不显示伪造积分值。

### P2：数据与产品质量

1. 增加 SC/VSC、旗帜、湿地、交通、进出站慢圈的清洗与敏感性。
2. 补真实大 session 的前端加载、可视化密度和缓存测试。
3. 决定 TR 路线：外部文字导入优先；STT 作为独立后续项目评估。

## 2026-07-25 Race Dossier 主产品迭代

状态：**v15 包容性稳健模型与 70 场正式运行完成；未通过场次继续
fail-closed，PAC/OVR 仍为 null**。

已完成：

- 完整比赛作为首要单元；定义覆盖、有效 Stint、账本闭合与整场发布门。
- Qualifying/Race 独立；车辆共同基线、车手兑现、策略位置代理和残差按秒闭合。
- 事件账本单列非绿旗、进出站、质量、交通和未知慢圈；70 场闭合。
- v15 输出 70 份逐场 Dossier、3 份赛季审计；发布分布为 60 场完整、3 场
  部分车队模块、7 场纯审计。
- v11 增加燃油/赛道演化/交通数据代理与模块门，v12 增加曲线候选赛，v13
  增加有序独立确认与进站周期损失，v14 优化执行范围；v15 以全量物理可读圈
  + OOF 软权重为主层，严格干净圈改为独立确认层。
- 77,720 个观测圈中 77,606 个进入候选层；有效权重质量 58,380.11，Kish
  有效样本量 65,437.76；3,113 个 Stint 通过主层，543 个再通过严格确认。
- v1/v2 与 v8–v14 失败证据原样保留；PAC/OVR 继续为 null。
- 低资源入口固定 D 盘项目缓存、前台单线程、768MB DuckDB 和低进程优先级。
- 工作台已切换到 70 份 v15 + 4 份 v7，并显示全量候选、有效权重和严格确认
  三层；Race Dossier 专用视图现展示整场结论、排位/正赛/成绩差异、燃油与
  车辆状态、逐 Stint、事件圈段、轮胎窗口及车—手—策略秒数分解。定向合同
  测试 50/50、全量 pytest 198/198、前端 build/test 及真实报告 Node 渲染
  smoke 均通过。
- v15 通用与专项追加式审计均通过：145/145 清单身份、5/5 实现身份，覆盖、
  配置快照、SHA 和合同错误均为 0。
- 固定入口低资源审计通过：无 WMI/CIM 查询、直接 Win32 优先级、前台单进程、
  五类数值线程为 1、缓存和输出均位于 D 盘项目内。

仍未达到：

- 7/70 场没有通过包容性模型门：4 场 OOF 预测不胜过回退基线或绝对误差过高，
  3 场 nuisance 调整饱和度过高；这些场次只能审计，不能进入之后的策略历史。
- 99.85% 候选覆盖不等于 99.85% 因果识别。软权重只能缓解严格筛选偏差，
  不能补造未观测损伤、车队指令、能量模式和真实交通反事实；20,562 个严格圈
  仍是确认层，只有 543 个 Stint 达到严格确认。
- 缺少 FIA 最终相邻车辆计时差、完整赛道位置反事实和已冻结积分概率映射，
  因此期望积分/年度排名区间仍为 null。
- 已有公开线性燃油质量敏感性包络和比赛内条件模型，但真实燃油、胎温胎压、
  磨损、损伤、动力模式和车队指令不可识别；不能把模拟潜变量称为实测真值。
- 单场保胎只能称车手—赛车组合代理；尚无跨多个 Stint/分站且稳定的同队
  证据把它进一步归因给车手。
- 本机 WMI Operational 日志显示外部短命客户端反复枚举进程与性能类；项目
  入口无 WMI 查询并已有合同守卫，但客户端二进制尚未识别，系统卡顿不能宣称
  已解决。
- 工作台真实 Chrome 交互 smoke 已覆盖产品、赛季、分站、版本、Stint 与事件
  筛选并通过；GitHub Pages 线上路径 smoke 仍未执行。

后续若继续研究，必须新建预注册版本或新增独立测量来源；不得针对 7 个失败场
事后降低阈值，也不得用年终积分榜反向调参。

## 6. 完成定义

任务不能只以“文件已创建”为完成：

- 数据库任务：migration 可重复执行、最小数据可入库、视图可查询、测试可自动复现。
- 数据采集任务：manifest 记录来源、范围、成功/失败/空值和记录数。
- 前端任务：构建通过、静态路径正确、空数据有降级、浏览器无阻断错误。
- 模型任务：冻结数据与配置、时间外指标、基线/消融、失败日志和解释限制齐全。
- 文档任务：更新对应唯一主文档，链接可达，不在多个文件维护相同“当前状态”。

## 7. 状态更新模板

后续 Agent 更新本文件时使用以下格式，保持条目短且可验收：

```text
- 事项：
  - 状态：未开始 / 进行中 / 阻塞 / 已完成
  - 最近证据：命令、测试、产物或提交
  - 阻塞：没有则写“无”
  - 下一动作：一个可执行动作
  - 完成条件：可验证标准
```
## 2026-07-23 PAC 第二轮状态

- 已完成：严格过去可见的完整分站 rolling-origin、每 origin `team-year` transfer
  graph、同队 held-out pair-gap proper scoring、过去 origin OOS residual conformal
  覆盖、Laplacian 单身份估计、event→driver→stint bootstrap、实际 design manifest
  与哈希、别名回归和共线识别测试。
- 已隔离：新 `conditional_pair_laplacian_v1` 不复用旧 `JOINT_BLOCKS`，
  不绑定 `pooled_driver_base_z`、旧 PAC 或 0–100 车手卡。
- 未通过：novel-edge 识别样本、比赛块 bootstrap、完整基线/负对照、WIS、惩罚与
  leave-bridge 的完整阈值证明、全局图连通、冻结尺度映射、冻结后 16 场/120 pair
  确认窗口。
- 发布结论：`PAC=null`；系统仍是“客观基础已成立，但完整评分尚未成立”。

## 2026-07-23 PAC 第三轮状态

- v1 配置、哈希和失败产物保留，标记 `superseded_for_method_audit`；
  v1 午夜冻结字段记为 `timestamp_not_immutably_attested`。
- v2 已完成实际 pair-incidence 发布图、断连 gap=null、canonical edge、
  完整分母与跳过原因、统一 nominal/bootstrap reducer、实际 design manifest
  和可审计 post-freeze 注册表。
- v2 未完成严格 simultaneous split conformal、full-pipeline purge、事件等权
  transfer CI/paired baseline LCB、有效电阻、完整基线与冻结 0–100 映射；
  均保持 `NOT_TESTED/FAIL`。
- objective、driver cards、2026 四站报告和工作台已重跑；PAC/OVR 仍为 null。
- 全量测试：`144 passed`。

## 2026-07-25 Race Dossier v16 当前状态

- 状态：**单一 Stint 胎龄代表配速已实现并通过合成反例与一场真实数据 pilot；70 场正式 v16 尚未运行。**
- 已完成：
  - 每个车手 Stint 主表只显示一个代表圈速，不再并列初速、稳态和末段圈速；
  - 代表圈速先做公开燃油质量敏感性修正和可观测比赛条件修正，再用加权 Theil–Sen 投影到本 Stint 加权中位胎龄；
  - 参考胎龄禁止超出实际支持区间；同配方直接比较还要求参考胎龄差不超过 2 圈及赛道条件相近；
  - 低/高燃油情景输出代表配速区间，但明确不是真实油量、胎温、胎压、磨损量或车队内部模拟；
  - 2025 Abu Dhabi、BOR 两个 Stint pilot 已通过，修正后的胎龄斜率由原始负值恢复为正值；
  - 工作台保留 70 份 v15 和 4 份 v7，只追加 1 份 v16 pilot；追加导出会校验 SHA 并拒绝覆盖；
  - 比赛选择器已按比赛时间新到旧排列；2023–2025 使用报告轮次，同场版本按
    v16→v15，缺少正式 round 的 2026 v7 只显示冻结“四站序”；
  - Stint 比较视图已按软/中/硬/半雨/全雨配方隔离，并进一步按冻结的
    参考胎龄跨度 ≤2 圈、阶段中点跨度 ≤0.20 划为不重叠窗口；窗口内点估计
    最小者常驻置顶，多行窗口才显示相对差值，单行窗口明确不可直接比较；
  - 工作台已拆成“产品 → 赛季 → 分站 → 版本”四级导航；Race Dossier 与旧
    遥测档案不再混排，同场版本折叠到版本选择，pilot/正式发布边界始终可见；
  - 页面顺序调整为“整场结论 → 比赛事件 → Stint → 条件配速 → 秒数拆分 →
    轮胎策略 → 数据审计”；事件账本与 Stint 均默认聚焦四队，并可切到全场；
  - Stint 新增比赛进程默认视图；原同配方浏览已收紧为显式“真实可比窗口”，
    无效状态、旧版回退和缺失 v16 代表配速的行 fail-close 到审计区；
  - 燃油代理已完成公开口径外部一致性审计：base 与 TracingInsights 的
    100kg×0.03s/kg 线性公式相同；Abu Dhabi base 逐圈效应比 f1metrics
    Barcelona 的 0.054s/圈低 4.21%，low 比 f1pace 2025 使用的 0.03s/圈
    高 3.45%；不据此回调冻结参数；
  - v16 低资源入口静态审计通过：D 盘缓存、前台串行、单线程、无 WMI/CIM、无后台进程。
- 已验证：
  - Race Dossier / runner / workbench exporter 定向回归：56/56；
  - 最新全量 `pytest tests -q`：219/219，63.91 秒；
  - 前端 `npm run build` 与 `npm test`：通过；覆盖四级导航、同场版本顺序、
    主报告/全场范围、Stint 双视图、2.00/2.01 圈与 0.200/0.201 阶段门、
    窗口不重叠、置顶基准、事件分类和 DOM 阅读顺序；
  - 真实 Chrome 交互 smoke：产品/版本切换、Ferrari→LEC、Stint 视图和事件
    质量筛选通过；新增桌面、390px 移动端、横向/纵向滚动及 200% 缩放置顶
    基准检查通过，页面脚本异常为 0；截图位于 `.runtime-cache/ui-smoke/`；
  - 单场真实 pilot：20 名车手、1,156 圈，BOR 两个 Stint 的单一代表配速、参考胎龄、斜率和燃油区间全部非空；
  - `PAC=null`、`OVR=null`。
- 尚未完成：
  - 70 场 v16 追加式正式运行、manifest 覆盖/SHA/发布审计；
  - 前端当前复制 concordance-v1 冻结阈值；exporter 尚未把
    `stint_comparison_contract` 显式写进报告，后续需消除前后端阈值漂移风险；
  - 真实燃油、真实胎态、损伤、SOC、动力模式和车队指令仍不可识别。
- 下一动作：由 exporter 显式发布比较门合同，并保持单线程低资源前台模式决定
  是否启动耗时较长的 70 场 v16 正式运行。不得用 Abu Dhabi pilot 或本轮外部
  博主口径事后回调模型阈值。

## 2026-07-25 成绩—表现一致性审计层 v1 状态（含跨 Stint 自动比较表实现）

- 事项：Dossier 赛后一致性审计 + 跨 Stint 可比分组（concordance-v1）
  - 状态：进行中（实现完成，验证被执行环境阻塞）
  - 最近证据：`research/modeling/dossier_concordance.py`（含 `build_stint_comparison_table`：同配方、参考胎龄差 ≤2 圈、阶段中点差 ≤0.2 的自动配对表，即上节"下一动作"要求的比较表）、`research/run_dossier_concordance_v1.py`、`research/configs/dossier_concordance_2023_2025_v1.json`、`tests/test_dossier_concordance.py`；协议阈值预注册并写入 `algorithm-models.md` 对应小节
  - 阻塞：本会话执行后端（权限分类器）持续不可用，Bash 无法运行 pytest 与 runner；只读与文件写入不受影响
  - 下一动作：执行 `python -m pytest tests -q`（含新增 `tests/test_dossier_concordance.py`）；通过后对 v15 正式 run 运行 `python research/run_dossier_concordance_v1.py`，并启动 70 场 v16 正式运行，完成后用 `--manifest` 指向 v16 run 重跑一致性与比较表
  - 完成条件：全量 pytest 通过；v15 70 场一致性赛季汇总落盘；清洁场 `unexplained_inversion` 清单形成并开始迭代；v16 正式 run 后跨 Stint 比较表非空且 manifest/SHA 审计通过

## 2026-07-26 工作台解释与加载状态

- 已完成：稳定方法说明收敛到 `frontend/src/telemetry-help.js`；正文保留决策必要
  状态，鼠标、键盘和触屏按需显示帮助，不改写各版本报告 JSON。
- 已完成：v15 70 场的双曲线合同不足改为可见降级，Stint 表保留，筛选器、画布和
  上一份 v16 图像不会残留；只有 2025 Abu Dhabi v16 pilot 具备可绘制字段。
- 已完成：缺失静态资产返回真实 404；前端校验 JSON 内容类型；切换失败回滚上一份
  成功报告，首次失败隐藏正文；事件账本和 v15 批次标签去除版本误导。
- 已验证：前端 build/test 通过；manifest 75/75 份 JSON 全部解析并渲染；真实
  Chrome 75/75、hover/focus/`Escape`、v15 降级、404 和回滚反例通过。
- 发布阻塞：manifest 当前引用的 70 份 v15 与 1 份 v16 JSON 尚未进入当前 Git
  发布快照。提交并从该提交重建 Pages 前，只能声称本地 public/dist 验收通过。

## 2026-07-26 官方车队颜色状态

- 已完成：按 Formula 1 官方 `teamColourCode` 冻结 2023–2026 四套车队目录，
  覆盖当前发布入口 41 个 `season|team` 身份。
- 已完成：删除主页面车号取模色、工作台手写/HSL 色和 Live Review 车手手填色；
  同队车手只用线型、标记和文字区分。
- 已验证：前端 build/test 通过；真实 Chrome 覆盖主赛况页、工作台、Live Review，
  11 队/22 车手、计时表、TR 卡和曲线图例均命中官方目录，浏览器错误为 0。
- 边界：颜色目录是按赛季冻结的官方标识色，不是逐站特殊涂装重建；新增赛季或
  新车队必须先登记官方来源，否则前端 fail-close。

## 2026-07-26 数据页面本地入口状态

- 已完成：主赛况、工作台和 Live Review 统一使用协议入口器；HTTP/HTTPS 才加载
  应用模块，`file://` 不再执行必然失败的 JSON 请求。
- 已完成：默认本地服务可用时，从源码文件自动切换到同名 HTTP 页面；服务未运行
  时显示可点击入口和启动命令，不再暴露裸 `Failed to fetch`。
- 已验证：构建合同、当前 `src/telemetry-workbench.html` 文件入口、三个 HTTP
  页面及工作台 75/75 报告真实 Chrome 回归通过，浏览器错误与失败响应均为 0。

## 2026-07-26 Race Dossier v17 当前状态

- 状态：**70 场逐圈证据正式运行、追加式发布和工作台接入已完成；不可比窗口继续
  fail-close，不把条件点估计包装成全序排名。**
- 已完成：
  - 将 v16 的“代表配速 + 斜率”直线汇总明确降级为 `summary-only` 历史回退；
  - v17 sidecar 发布逐圈散点、后端冻结 Theil–Sen 方程/预测网格、观测残差
    中间 80%、删除连续 3 圈块的稳定性中间 80%、具名燃油敏感性范围及确认后
    描述性曲线；
  - 所有预测只在对应输入支持内发布；浏览器不重新拟合、不外推；
  - 成对比较在两段 Stint 的共同胎龄支持内重算样本、Kish ESS 和可观测条件平衡，
    `warning` / `not_comparable` 的正式直接秒差保持 `null`；
  - 低资源 runner、manifest/SHA 身份链、追加式 exporter 和独立正式发布审计器
    已落地。
- 正式证据：
  - `run=20260726T124625+0800-ce93134a40b8`；
  - 70 场 = 60 `complete_race` + 3 `partial_team_coverage` + 7 `audit_only`；
  - 31,904 个四队逐圈点，31,871 个进入主拟合；
  - 1,225 个有范围支持的 Stint，1,224 个完成删块稳定性估计；
  - 6,298 对比较 = 368 `comparable` + 205 warning + 5,725
    `not_comparable`；
  - 工作台现有 145 份报告，其中 v17 70 份、curve sidecar 70 份。
- 已验证：
  - v17 正式发布审计通过，70 份报告/sidecar、3 份赛季汇总、artifact、圈宇宙、
    实现与前端身份链一致；
  - 前端 build/test 通过，145/145 报告和 70/70 sidecar 逐项校验并渲染；
  - 完整 `pytest` 231/231 通过，`git diff --check` 无空白错误；
  - 真实 Chrome 桌面与 390px 移动端通过，2025 Abu Dhabi MEDIUM 的 98 圈散点、
    4 条方程、三类范围和 6 对门控记录可见；LEC 三对不可比且直接秒差为 `—`。
- 边界：
  - 观测/稳定性/燃油范围都不是 CI 或新圈 PI；
  - 真实燃油、SOC、轮胎状态/磨损、设定、损伤、动力模式和车队指令仍不可识别；
  - 本地正式产物与 `frontend/dist` 验收通过不等于 GitHub Pages 已发布，仍需在
    用户决定的提交快照中纳入 v17 代码、manifest、70 份报告和 70 份 sidecar。

### 可比焦点遥测视觉重构

- 状态：**视觉合同、实现、专项自动化与真实浏览器验收均已完成。**
- 展示范围：默认聚焦 `comparable` pair 或完整可比 clique；`warning` /
  `not_comparable` 保留审计卡片，但不形成共享排名轴或全序。
- 图形口径：纵轴提供默认 `FOCUS` 与可切换 `FULL`；焦点域外点在上下边缘保留
  方向标记和数量；散点大小固定，权重仅映射为透明度；稳定性以两条点线边界显示、
  不填充，燃油仅显示 `L/B/H` 三个离散点。
- 交互口径：桌面与移动端共用 tooltip；移动端使用 pair 卡片承载身份、方向、
  共同支持和门控原因。浏览器只显示后端冻结结果，不重新拟合或平滑。
- 已验证：前端 build/test 通过；真实 Chrome 默认选中 Abu Dhabi MEDIUM
  `ANT ↔ HAM` comparable pair，7 个焦点可切换，`FOCUS` 有 2 个焦点外圈边缘
  标记、`FULL` 为 0，副图恰有 6 个 L/B/H 离散情景点，逐圈 tooltip 能显示
  发布圈号、调整圈时、主拟合、残差、权重和上下文标记。
- 响应式：1440px 保持 3:2 双栏；390px 单栏且页面宽度与视口同为 390px，
  pairwise 审计转换为 `data-label` 卡片，顶部栏不再覆盖图表。
- 证据截图：`.runtime-cache/ui-smoke/v17-redesign-{desktop,mobile}.png`。

## 2026-07-30 参考分析复刻实验室 v1 当前状态

- 状态：**第 1 项 F1pace 已完成真实 pilot、自动化验收和本地 HTTP 桌面/移动端验收；
  第 2 项 DeltaData 待开始。**
- F1pace 产物：2025 Abu Dhabi Race，20/20 车手，1,156 圈宇宙、1,082 个有效配速圈、
  784,424 个逐点样本；公开均值 20/20 通过，最大差 `0.000491s`；交通比例由逐点
  `distance_to_driver_ahead` 与速度/时间权重计算；pairwise 矩阵 190 对反对称通过。
- 双视图：`visual_replication` 已发布 Top/Bottom/All、summarized race pace、交通
  热图和 pairwise；`audited_analysis` 保持 `audit_only`，不形成因果全序。排位单独展示
  整场最佳准确推圈代理，Q1/Q2/Q3 阶段不可识别。
- 独立入口：`http://127.0.0.1:5173/reference-analysis-lab.html`；静态页面已接入
  根索引和工作台导航，根 manifest → 目标 manifest → 报告 SHA-256 链通过。
- 验收：`python -m pytest tests/test_reference_analysis_lab.py -q` 为 5 passed；
  `frontend/npm run build`、`frontend/npm test` 通过；Chrome 1440×1000 与 390×844
  截图位于 `.runtime-cache/reference-analysis-lab-v1/ui-smoke/`。
- 边界：参考图只做语义/布局核对，不进入产品资产；精确闭源选圈不声称一比一复现；
  真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令仍不可识别。

## 2026-07-30 参考分析复刻实验室 v1 当前状态（F1pace + DeltaData）

- 状态：**第 1 项 F1pace 与第 2 项 DeltaData 均已完成真实 Abu Dhabi pilot、自动化
  验收和本地 HTTP 桌面/移动端验收；第 3 项 FDataAnalysis 待开始。**
- F1pace：20/20 车手，1,156 圈宇宙、1,082 个有效配速圈、784,424 个逐点样本，公开
  均值最大差 `0.000491s`；逐点交通比例、Top/Bottom/All、summarized、pairwise 和
  排位代理均已隔离发布，`audited_analysis` 为 `audit_only`。
- DeltaData：真实 v17 sidecar 19 Stints/464 观察点/421 质量拟合点/167 clean-air 点；
  4 对直接可比、2 对 `audit_only_balance_warning`、66 对不可比；合成点为 0。目标
  状态 `METHOD_EQUIVALENT_ONLY`，精确一比一方法保持 `SKIPPED_OPAQUE_METHOD`。
- 入口：`http://127.0.0.1:5173/reference-analysis-lab.html`（F1pace）和
  `http://127.0.0.1:5173/delta-data-pilot.html`（DeltaData）；根 manifest → 目标
  manifest → 报告 SHA-256 链通过，均已挂入本地导航。
- 验收：`python -m pytest tests/test_reference_analysis_lab.py -q` 为 8 passed；
  `frontend/npm run build`、`frontend/npm test` 通过；Chrome 1440×1000 与 390×844
  截图均位于 `.runtime-cache/reference-analysis-lab-v1/` 下对应目标目录。
- 边界：社交图表未混入 Race Dossier 真相层；真实燃油、SOC、胎温胎压、物理磨损、设定、
  损伤、动力模式和车队指令仍不可识别；视觉排序不等于 PAC/OVR 或因果排名。

## 2026-07-31 参考分析复刻实验室 v2 当前状态

- 顺序状态：**第 1 项 F1pace 倒推 v2 已完成；第 2 项 DeltaData v2 正在实施；
  第 3 项 FDataAnalysis v1 已有真实 pilot，待按倒推协议升级；第 4/5 项尚未开始。**
- F1pace v2：用阿布扎比 1,156 个颜色代理单元拟合可解释 Huber 校准器，并在卡塔尔
  1,067 个真实单元零重拟合外验。外验 MAE `2.238pp`、P90 `6.848pp`、
  `>33%` 判定准确率 `99.250%`；纯物理 companion MAE `2.498pp`。
- 双视图：`visual_replication` 为事件外验证的方法等价复刻；
  `audited_analysis` 为不读取参考图的同位置 crossing headway 2 秒模型，保持
  `audit_only`，不形成因果排名。一比一状态为 `SKIPPED_OPAQUE_METHOD`。
- 产物：`research/records/reference_analysis_lab_v2/f1pace/
  run=20260731T120000+0800-f1pace-reverse-engineered-v2/`；前端入口
  `http://127.0.0.1:5174/f1pace-reverse-engineered-v2.html`。
- 验收：定向 Python `16 passed`；`npm run build` 与 `npm test` 通过；1440px 桌面和
  CDP 真实 390px 移动验收通过，移动页面宽度与视口均为 390px。
- 边界：参考图未作为产品资产；读图误差独立披露；首圈、进出站和退赛长尾没有被静默
  删除。真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令仍不可识别。

## 2026-07-31 参考分析复刻实验室 v2 当前状态（F1pace + DeltaData）

- 顺序状态：**第 1 项 F1pace v2 与第 2 项 DeltaData v2 已完成；下一目标为
  第 3 项 FDataAnalysis v2。第 4 项 F1TelemetryData、第 5 项 GP Tempo 尚未开始。**
- DeltaData 主 pilot：2025 Abu Dhabi Race 784,424 个逐点样本、1,156 圈宇宙、
  411 个 clean-air 候选、320 个 Huber 代表圈；20 名车手均披露，19 名进入模型，
  HUL 因不足三圈显式不排名。
- 公开图差距：Japan Race、Miami Sprint、Miami Race 三场 ANT−RUS 的 MAE
  `0.091340pp`、最大误差 `0.111173pp`、方向 3/3 一致。该基准在反推时已经可见，
  明确标记 `reference-informed`，不是盲测。
- 与 v1：共享 8 名车手和 4 支车队顺序均完全一致；车手 centered-gap MAE
  `0.154270s`、车队 `0.063910s`。差异来自全场覆盖、逐点物理交通、
  `0.032` 对 `0.051724 s/圈` 燃油情景以及 Huber 代表圈。
- 双视图：`visual_replication` 发布方法等价顺序；`audited_analysis` 只允许 10 个
  同队配对中的 4 个可比子集，其他 audited delta 保持 `null`，不形成全场总序。
- 产物：`research/records/reference_analysis_lab_v2/deltadata/
  run=20260731T153000+0800-deltadata-reverse-engineered-v2/`；入口
  `http://127.0.0.1:5174/deltadata-reverse-engineered-v2.html`。
- 验收：定向 Python `21 passed`；`npm run build`、`npm test` 通过；Chrome 1440px
  visual/audit 与真实 390px visual 通过，390px 页面宽度等于视口，运行时错误为 0。
- 边界：精确作者阈值、人工选圈、回归和赛季权重仍为
  `SKIPPED_OPAQUE_METHOD`；真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、
  动力模式、车手管理意图和车队指令仍不可识别。

## 2026-07-31 参考分析复刻实验室 v2 当前状态（前三项目）

- 顺序状态：**第 1 项 F1pace、第 2 项 DeltaData、第 3 项 FDataAnalysis v2
  均已完成；下一目标为第 4 项 F1TelemetryData，第 5 项 GP Tempo 尚未开始。**
- FDataAnalysis：真实 2025 Abu Dhabi Race 784,424 个原始逐点样本、1,082 个有效圈；
  统一到 1,000 区间距离轴，生成 16 个动态弯角代理和 6 个直道代理。圈时积分
  MAE `0.154728s`、P90 `0.304797s`，分段敏感性通过。
- 忠实视图保留公开 top15 最高速、样本全油门比例和原始胎龄 OLS；审计视图改用
  逐圈 P99、时间权重、单 Stint Theil–Sen、具名燃油情景和同队一对一条件匹配。
  10 个同队组合仅 2 个可比，其余 `audit_only`，不形成车辆总分。
- 参考数值状态为 `NOT_COMPARABLE_DIFFERENT_EVENT_AND_SESSION`；发布状态为
  `METHOD_EQUIVALENT_INTERNALLY_VALIDATED`，精确闭源分段/选圈仍不声称一比一复现。
- 产物：`research/records/reference_analysis_lab_v2/fdataanalysis/
  run=20260731T170000+0800-fdataanalysis-reverse-engineered-v2/`；入口
  `http://127.0.0.1:5174/fdataanalysis-reverse-engineered-v2.html`。
- 验收：定向 Python `25 passed`；`npm run build`、`npm test` 通过；Chrome
  1440px visual/audit 与真实 390px visual 通过，运行时错误为 0。

## 2026-07-31 参考分析复刻实验室 v2 当前状态（前四项目）

- 顺序状态：**第 1 项 F1pace、第 2 项 DeltaData、第 3 项 FDataAnalysis、
  第 4 项 F1TelemetryData 均已完成；下一目标为第 5 项 GP Tempo。**
- F1TelemetryData：真实 2025 Abu Dhabi 排位 395,840 个逐点样本、正赛 784,424
  个逐点样本；13 类公开图表语义已复刻，88/88 排位准确推圈获冻结 Q1/Q2/Q3 身份。
- 同场数值：排位 Lap Delta、Top Speeds、前三名 sector time 与正赛最快圈为零误差；
  正赛 Average Gap MAE `0.000216s`；Stint 47/47 一致；Pit 车队均值 MAE
  `0.065808s`。控制分段 MAE `2.639122pp`、车队油门 MAE `1.195668pp`，均明确
  归因于不公开阈值、采样和 feed 版本，未逐车手调参。
- 双视图：`visual_replication` 保留图表包描述语义；`audited_analysis` 将正赛全局
  均值保持 `audit_only`，不形成车辆因果全序。官方几何 sector anchor 未测试，
  真实燃油、SOC、胎温胎压、物理磨损、设定、损伤、动力模式和车队指令仍不可识别。
- 产物：`research/records/reference_analysis_lab_v2/f1telemetrydata/
  run=20260731T193000+0800-f1telemetrydata-reverse-engineered-v1/`；入口
  `http://127.0.0.1:5174/f1telemetrydata-reverse-engineered-v1.html`。
- 验收：定向 Python `30 passed`；`npm run build`、`npm test` 通过；Chrome/CDP
  桌面 visual/audit 与真实 390px visual 通过，移动端页面宽度严格为 390px，
  运行时错误为 0。

## 2026-07-31 参考分析复刻实验室 v2 当前状态（五项目完成）

- 顺序状态：**F1pace、DeltaData、FDataAnalysis、F1TelemetryData、GP Tempo
  五项目已按固定顺序全部完成，没有目标因三次失败而跳过。**
- GP Tempo：真实 5 个 Qualifying 圈 + 5 个 Race 圈、3,192 个约 4.17 Hz 原始
  car-channel 样本；90 个有序圈对、270 个官方 sector 端点，最大端点误差为 0。
- 交互：多车手 speed/throttle/brake/gear/DRS、Delta 与赛道位置共享游标；Q 默认
  三圈和 Race 默认 PIA/HUL 均通过条件门；跨 session 强制显示组合条件警告。
- 误差边界：公开方法没有固定同场逐点数值表，只验收公开算法不变量；约 4 Hz 的段内
  曲线为估计，不伪造逐点 MAE。跨 session 不解释为纯车手或车辆差。
- 产物：`research/records/reference_analysis_lab_v2/gptempo/
  run=20260731T213000+0800-gptempo-reverse-engineered-v1/`；入口
  `http://127.0.0.1:5174/gptempo-reverse-engineered-v1.html`。
- 验收：定向 Python `36 passed`；`npm run build`、`npm test` 通过；Chrome/CDP
  桌面 visual/audit、Race 默认条件、跨 session 警告、赛道游标联动与 390px 移动端
  全部通过，运行时错误为 0；最终全仓 `python -m pytest tests -q` 为
  `267 passed`。

## 2026-07-31 参考分析复刻实验室 v3 分赛道验证当前状态

- 状态：**A–E 五目标已完成 2023–2025 的分赛道验证、发布、独立审计、前端接入和
  真实浏览器验收。** 最终 latest run 为
  `20260731T235900+0800-track-validation-v3`，覆盖 70 场、24 条赛道。
- 冻结基线：Race Dossier v17 与 reference-analysis-lab v2 五报告哈希重验通过；
  候选比较前 universal baseline 已冻结。2025 Abu Dhabi/Qatar 不进入 F1pace
  任何训练折。
- 模型注册：F1pace `universal 24`；DeltaData `track 6 / cluster 5 /
  universal 13`；FDataAnalysis、F1TelemetryData、GP Tempo 均
  `universal 24`。回退是验证结论，不是失败隐藏。
- 数据与边界：A–C 有三年留出误差、ESS、条件支持、缺失率和排除账本；D 仅一个
  reference-informed 同场事件，E 仅一个官方端点事件，缺口均保持
  `NOT_TESTED/null`。排位与正赛独立；真实燃油、SOC、胎温胎压、物理磨损、设定、
  损伤、动力模式、车队指令、真实下压力和引擎功率仍不可识别。
- 追加链：`233000` 因子 manifest staging 路径失败；`235500` 因 D 的 Q 阶段
  universal 空行缺失被迭代取代；两者均保留。最终 `235900` 独立审计 PASS。
- 产品：新增 `track-validation-v3.html` 及独立 CSS/JS，挂入实验室、五个既有项目和
  首页导航；本地入口使用 `npm run build`、`npm run dev` 与 HTTP URL，不要求打开
  `frontend/src`。
- 浏览器：五目标各有 1440px 桌面与真实 CSS 390px 移动截图。最终 CDP r2
  `innerWidth=390`、无页面横向溢出、runtime/console/network error 均为 0；
  首轮 CLI viewport 身份失败保留在 acceptance 索引。
- 最终验收：独立 auditor PASS，新增定向测试 `10 passed`；全仓
  `python -m pytest tests -q` 为 `277 passed in 74.21s`，`npm run build` 与
  `npm test` 通过。
