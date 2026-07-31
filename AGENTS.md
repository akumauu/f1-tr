# F1 TR Agent 工作入口

在分析、修改或回答本仓库问题前，先阅读 [`docs/README.md`](docs/README.md)。该文件是文档路由表，会说明每类问题的权威文档、对应代码和需要继续读取的证据。

执行规则：

1. 先按 `docs/README.md` 的“任务路由”只读取与任务有关的主文档，不要默认加载全部历史日志或研究记录。
2. 当前事实冲突时，采用“代码/迁移/配置/机器可读产物 > 对应主题主文档 > 专题操作文档 > 历史日志”的优先级，并同步修正文档。
3. 数据库结构变更同步更新 `docs/database.md`；算法、特征或实验协议变更同步更新 `docs/algorithm-models.md`；验收结果同步更新 `docs/implementation-results.md`；任务状态同步更新 `docs/progress.md`。
4. 不把 `docs/work-log.md`、`research/records/research_log.md` 或带日期的报告当作当前状态入口；它们只用于追溯。
5. 文档、代码注释和交接说明使用中文；路径、命令、表名、字段名和符号保持源码原样。

