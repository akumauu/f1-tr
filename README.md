# F1 TR

F1 TR 是一个 F1 赛后复盘平台，聚合 Team Radio、圈速、轮胎 stint、位置和比赛事件，并提供圈速变化、stint 趋势、公开遥测解释与科研型车辆—车手分离实验。

产品定位是“辅助理解比赛”，不是官方技术分析、车队工程决策或高精度物理模型。当前交付形态为离线数据生产、静态 JSON 和浏览器 SPA；前端不直接连接 PostgreSQL，也不依赖公网常驻 API。

## 文档入口

Agent 和开发者应先读 [文档地图与查询指南](docs/README.md)，再按任务读取对应主文档：

- [产品与技术架构](docs/technical-overview.md)
- [数据库、数据源与存储分层](docs/database.md)
- [算法与模型说明](docs/algorithm-models.md)
- [实现与验收结果](docs/implementation-results.md)
- [当前进度与下一步](docs/progress.md)
- [测试策略](docs/test-strategy.md)

`docs/work-log.md`、`research/records/research_log.md` 和带日期报告是历史证据，不是当前状态入口。

## 代码入口

```text
backend/    Go CLI、本地调试 API、PostgreSQL 连接与 migrations
tools/      OpenF1/FastF1/MultiViewer 采集、入库、规范化、导出和清理
frontend/   静态 SPA 与可发布精简数据
research/   冻结数据、模型实验、配置和结果
tests/      Python 工程合同与数据链路测试
docs/       架构、数据库、算法、结果、进度和专题说明
```

## 快速验证

```powershell
# Python
python -m unittest discover -s tests

# Go
Push-Location backend
$env:GOTOOLCHAIN="local"
$env:GOSUMDB="off"
go test ./...
Pop-Location

# 前端
Push-Location frontend
npm run build
npm test
npm run dev
Pop-Location
```

数据页面必须通过 HTTP 打开；本地入口为
`http://127.0.0.1:5173/`、`/telemetry-workbench.html` 和
`/live-review.html`。不要直接打开 `frontend/src/*.html`。

生成 Catalunya 默认静态数据：

```powershell
python tools\export_catalunya_static.py --out frontend\public\data
```

生成 2026 四站同队遥测报告和工作台数据：

```powershell
python research\run_2026_telemetry_batch.py --quiet
python tools\export_telemetry_workbench.py `
  --report research\records\telemetry_explanation_2026_australia_ferrari_v7.json `
  --report research\records\telemetry_explanation_2026_china_ferrari_v7.json `
  --report research\records\telemetry_explanation_2026_japan_ferrari_v7.json `
  --report research\records\telemetry_explanation_2026_miami_ferrari_v7.json
```

数据库、代理和多数据源命令见 [数据库文档](docs/database.md)。

## 仓库体积约定

- Git 保留源码、文档、脚本、manifest、机器可读研究结果和精简 sample。
- `data/raw/`、`data/logs/`、`.fastf1-cache/`、`.playwright-mcp/`、`MultiViewer/`、`research/cache/` 和本地冻结大文件不进入普通 Git 历史。
- TracingInsights 历史冻结实验绑定 expanded v4；需要 raw 状态与绝对时间的新遥测解释使用 expanded v5。PostgreSQL 逐点遥测表只是可丢弃缓存。
- 大体积原始包通过 Release、对象存储或 Git LFS 共享。
