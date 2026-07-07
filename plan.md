# F1 TR 采集计划

本文档记录当前比赛周末的数据采集方案，优先保证实时数据连续、可追溯，并保留降级路径。

## 任务清单与开发进度

### 已完成

- 静态复盘前端已实现中文化界面、洞察卡片、车手筛选、圈数筛选、Team Radio 搜索和多视图图表。
- `frontend/public/data/live-review/timeline.json` 已更新为当前可展示的 live review 时间线数据。
- MultiViewer 本地 API 采集脚本已新增，默认采集核心 Live Timing topic，并保存快照、`latest.json` 和采集索引。
- MultiViewer API 采集说明已整理到 `docs/multiviewer-api-capture.md`。
- 本地仓库瘦身策略已确定：原始采集数据、运行日志、Playwright 临时截图和下载版 MultiViewer 不进入 Git。
- `.gitignore` 已补充 `data/raw/`、`data/logs/`、`.playwright-mcp/` 和 `MultiViewer/` 等本地产物规则。
- 本地 Git 检查点引用和不可达对象已清理，`.git` 从约 1.2GB 降到约 0.37MB。
- GitHub `origin/main` 已推送到提交 `0bca725`。

### 进行中

- GitHub Pages 发布检查：推送后继续确认 Pages 是否读取 `frontend/dist` 或配置的发布产物。

### 后续建议

- 把临场原始数据长期放在本地、网盘或对象存储，仓库只保留可复现脚本、文档和精选 sample 数据。
- 如果需要共享大体积赛段原始包，优先使用 GitHub Release、外部存储或 Git LFS，不要直接提交到主仓库历史。
- 后续采集完成后，用脚本从 `data/raw/**` 生成精简 JSON，再复制到 `frontend/public/data/**` 作为可发布数据。

## 当前推荐方法

主路径使用 MultiViewer 本地 API 采集结构化 Live Timing 数据，不再优先依赖页面文字解析。

启动 MultiViewer：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_multiviewer_api.ps1
```

打开目标赛段的 Live Timing 窗口后，运行：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --topics=core --interval-ms=5000
```

默认输出目录：

```text
data/raw/multiviewer-api-live-timing
```

输出文件：

- `multiviewer-api-*.json`：每次采集的完整结构化快照。
- `latest.json`：最近一帧快照。
- `capture-index.jsonl`：采集索引、摘要和健康指标。

## API 路径说明

REST 路径是默认方案：

```text
http://127.0.0.1:10101/api/v2/live-timing/state
http://127.0.0.1:10101/api/v2/live-timing/state/{topics}
```

GraphQL 路径用于验证或补充：

```text
http://127.0.0.1:10101/api/graphql
```

单独验证 TeamRadio topic：

```powershell
node tools/save_multiviewer_api_state.mjs --graphql --topics=TeamRadio
```

## 默认采集 Topic

`core` 当前包含：

```text
SessionInfo
SessionStatus
LapCount
DriverList
TimingData
TimingAppData
TimingStats
LapSeries
PitLaneTimeCollection
PitStopTimeCollection
RaceControlMessages
TeamRadio
TrackStatus
WeatherData
WeatherDataSeries
TopThree
```

高频大对象默认不采集，避免磁盘占用过快增长。需要时再加：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --topics=core --include-heavy
```

`--include-heavy` 会额外采集：

```text
CarData
Position
DriverTracker
```

## Live TR 状态

当前脚本已经默认请求 `TeamRadio` topic，并保存原始返回内容。但 `TeamRadio` 是否等同于 live TR / AI radio transcription，需要以当场返回结构为准。

采集摘要会输出：

```text
team_radio_count
team_radio_keys
```

判断方式：

- 如果 `team_radio_count > 0`，并且 `team_radio_keys` 出现 `Captures`、`Entries`、`Messages` 等字段，说明本地 API 当前有 TeamRadio 数据。
- 如果 `TeamRadio` 为空或字段不包含文本/转录内容，需要用 GraphQL 单独验证 `TeamRadio`。
- 如果 REST 和 GraphQL 都没有可用 live TR 文本，则保留旧 AI Radio DOM 采集作为兜底。

## 中断风险与处理

上一次采集的主要风险是网络波动、MultiViewer 停更、电脑休眠或端口不可达。

比赛前检查：

- 电脑插电运行。
- 关闭系统睡眠和自动休眠。
- MultiViewer 保持 Live Timing 窗口打开。
- 终端持续输出保存成功。
- `capture-index.jsonl` 持续追加。
- `lap` 随赛段推进变化。
- `drivers` 接近当场参赛车手数。

如果 `lap` 长时间不动，优先检查 MultiViewer 窗口是否还在更新。

如果出现无法连接本地 API，优先检查 MultiViewer 是否仍在运行；脚本默认会扫描 `10101-10110`，也可以手动指定：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --api-base=http://127.0.0.1:10102
```

## DOM 兜底方案

如果 MultiViewer API 临场不可用，启用 DevTools DOM 文本采集：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_multiviewer_debug.ps1
node tools/save_multiviewer_laps_tyres.mjs --watch
node tools/save_multiviewer_ai_radio.mjs --watch
```

DOM 路径已兼容 `1:00:00`、`59:55`、`00:00` 等时钟格式，但仍受页面布局、窗口状态和界面文本影响，优先级低于 API 采集。

## 银石执行建议

正赛建议提前 20 分钟启动采集；Sprint、排位和练习赛至少提前 10 分钟启动。

比赛日建议命令：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_multiviewer_api.ps1
node tools/save_multiviewer_api_state.mjs --watch --topics=core --interval-ms=5000
```

赛中重点观察终端摘要：

```text
lap
drivers
team_radio_count
team_radio_keys
```
