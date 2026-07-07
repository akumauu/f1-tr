# MultiViewer API 采集说明

本文档用于银石大奖赛周末的实时数据采集。优先使用 MultiViewer 本地 API，旧的 DevTools DOM 文本采集只作为兜底。

## 数据源选择

当前推荐顺序：

1. MultiViewer 本地 REST API：`http://127.0.0.1:10101/api/v2/live-timing/state`
2. MultiViewer 本地 GraphQL API：`http://127.0.0.1:10101/api/graphql`
3. DevTools DOM 文本采集：`tools/save_multiviewer_laps_tyres.mjs` 和 `tools/save_multiviewer_ai_radio.mjs`

REST API 直接返回结构化 topic，比 `document.body.innerText` 少一层页面布局解析，理论上更不容易被时钟格式、列顺序、界面语言和页面焦点影响。

注意：`TeamRadio` topic 会被默认采集，但它是否包含实时 TR 文本、音频捕获记录，还是只包含部分元数据，需要以当场 `latest.json` 中的 `state.TeamRadio` 为准。脚本会在摘要里输出 `team_radio_count` 和 `team_radio_keys`，用于快速判断这个 topic 的实际结构。如果 `TeamRadio` 为空，再用 GraphQL 单独验证：

```powershell
node tools/save_multiviewer_api_state.mjs --graphql --topics=TeamRadio
```

## 银石关键时间

2026 英国大奖赛使用 Silverstone 当地时间，Silverstone 为 UTC+1，中国时间为 UTC+8，相差 7 小时。

| 赛段 | Silverstone 当地时间 | 中国时间 |
| --- | --- | --- |
| FP1 | 7 月 3 日 12:30 | 7 月 3 日 19:30 |
| Sprint Qualifying | 7 月 3 日 16:30 | 7 月 3 日 23:30 |
| Sprint | 7 月 4 日 12:00 | 7 月 4 日 19:00 |
| Qualifying | 7 月 4 日 16:00 | 7 月 4 日 23:00 |
| Race | 7 月 5 日 15:00 | 7 月 5 日 22:00 |

建议至少提前 10 分钟启动采集；正赛建议提前 20 分钟启动，以捕捉发车前的 session status、track status 和早期 Team Radio。

## 启动步骤

普通 API 采集不需要 DevTools 调试端口：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_multiviewer_api.ps1
```

在 MultiViewer 中打开对应赛段的 Live Timing 窗口后，运行：

```powershell
node tools/save_multiviewer_api_state.mjs --watch
```

默认输出目录：

```text
data/raw/multiviewer-api-live-timing
```

每次采集会写入：

- `multiviewer-api-*.json`：单帧结构化快照
- `latest.json`：最近一帧
- `capture-index.jsonl`：采集索引和健康摘要

## 建议参数

正赛核心数据：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --topics=core --interval-ms=5000
```

需要 GPS/车速等高频数据时再打开 heavy topic：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --topics=core --include-heavy --interval-ms=5000
```

如果想验证 GraphQL 路线：

```powershell
node tools/save_multiviewer_api_state.mjs --graphql --topics=core
```

如果 MultiViewer 的 10101 端口被占用，脚本会默认扫描 `10101-10110`。也可以手动指定：

```powershell
node tools/save_multiviewer_api_state.mjs --watch --api-base=http://127.0.0.1:10102
```

## 健康检查

采集正常时，终端会持续输出类似：

```text
[22:00:05] 保存成功：multiviewer-api-20260705_220005_123.json lap=1 drivers=20
```

重点看三件事：

- `lap` 是否随赛段推进变化。
- `drivers` 是否接近当场参赛车手数。
- `capture-index.jsonl` 是否持续追加，且没有长时间断档。

如果出现 `No data found, do you have live timing running?`，说明 MultiViewer 已启动，但 Live Timing 窗口还没有打开或还没有加载到数据。

如果出现 `无法连接 MultiViewer 本地 API`，说明本地 API 端口不可达。优先检查 MultiViewer 是否在运行，其次检查端口是否不是 `10101`。

## DOM 兜底

如果 API 临场不可用，再启动旧的 DevTools 采集：

```powershell
powershell -ExecutionPolicy Bypass -File tools/start_multiviewer_debug.ps1
node tools/save_multiviewer_laps_tyres.mjs --watch
node tools/save_multiviewer_ai_radio.mjs --watch
```

旧 DOM 采集已兼容 `1:00:00`、`59:55`、`00:00` 这几种时钟格式，但仍然受页面布局影响，优先级低于 API 采集。
