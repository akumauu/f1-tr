# 本地数据库与 F1 数据采集

本文档记录本项目的本地开发数据链路：PostgreSQL、OpenF1 原始端点采集、FastF1 session 采集，以及面向前端的静态 JSON 导出。

## 遥测存储策略

- TracingInsights 逐点遥测的唯一真相源是 `data/normalized/tracinginsights/schema=tracinginsights-expanded-v4`。
- OpenF1 原始 `car_data`/`location` 保存在 `f1_api_snapshots`，需要时可重新提取。
- `telemetry_car_data` 与 `telemetry_location` 只保留空表结构，作为显式、可丢弃的临时 SQL 缓存；Go 后端和正式研究不得依赖它们。
- 禁止恢复 v1–v3 规范目录。训练缓存位于 `research/cache/`，可随时删除并从 v4 重建。

清理或复核本地大体积存储：

```powershell
# 默认只审计，不删除
python tools\cleanup_obsolete_storage.py

# 显式执行白名单文件清理和 PG 遥测瘦身
python tools\cleanup_obsolete_storage.py --apply-files --apply-postgres
```

确需为临时 SQL 分析重建 OpenF1 逐点缓存时，必须显式声明：

```powershell
python tools\extract_openf1_telemetry.py --year 2025 --materialize-postgres-cache
```

## PostgreSQL

项目默认连接串来自 `.env.example`：

```text
postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable
```

推荐本地配置：

```text
安装目录: D:\PostgreSQL\18
数据目录: D:\PostgreSQL\18\data
数据库: f1_analysis
用户: f1user
密码: f1pass2026
端口: 5432
```

PostgreSQL 18 相比 16/17 的主要吸引点是异步 I/O，对顺序扫描、bitmap heap scan、vacuum 等 I/O 密集场景更友好。对本项目这种本地分析库来说，它不是刚需，但如果新装数据库，直接使用 18 可以减少后续升级成本。

后端 API 和 ingester 启动时会自动执行 `backend/migrations/*.up.sql`：

```powershell
cd "D:\vibe-coding\F1 TR\backend"
go run ./cmd/api
```

或只跑 OpenF1 入库：

```powershell
cd "D:\vibe-coding\F1 TR\backend"
go run ./cmd/ingester -year=2026 -meetings-only
go run ./cmd/ingester -session-key=11307
```

## OpenF1 原始端点采集

通用采集脚本：

```powershell
python tools\fetch_f1_data.py `
  --source openf1 `
  --year 2026 `
  --meeting-key 1287 `
  --session-key 11307 `
  --openf1-endpoints drivers,laps,stints,team_radio,pit,position,race_control `
  --out data\raw\f1-api `
  --csv
```

输出结构：

```text
data/raw/f1-api/
  manifest.json
  openf1/
    meetings.json
    sessions.json
    sessions/{session_key}/{endpoint}.json
```

`--csv` 会为扁平记录额外写出 CSV，便于人工检查。

## OpenF1 与 FastF1 的网络调用方式

这两个数据源不是同一个 API，调用方式和当前本机的代理表现也不同：

| 数据源 | 实际地址 | 调用方式 | 当前本机测试结果 |
| --- | --- | --- | --- |
| OpenF1 | `https://api.openf1.org/v1` | 使用 HTTP GET 查询 JSON；不需要改成 POST | Python `requests`、`urllib` 和系统 `curl` 均返回 HTTP 200 和非空数据 |
| FastF1 | `https://livetiming.formula1.com/static/...` | 由 FastF1 内部使用 HTTP GET 读取 Live Timing 数据 | 经过 `127.0.0.1:7890` 系统代理时返回 HTTP 403；绕过代理后返回 HTTP 200 |

因此，OpenF1 可以按后续命令直接调用；运行 FastF1 前，应让 Live Timing 域名绕过本机代理。以下设置只影响当前 PowerShell 会话，不会修改 Windows 全局代理：

```powershell
$noProxyEntries = @($env:NO_PROXY, "livetiming.formula1.com", "api.openf1.org") `
  | Where-Object { $_ }
$env:NO_PROXY = $noProxyEntries -join ","
$env:no_proxy = $env:NO_PROXY
```

设置后，在同一个 PowerShell 窗口中运行 FastF1 采集命令。2026-07-13 的本机测试中，绕过代理后 FastF1 3.8.3 成功加载 2025 Belgian Grand Prix Race，并得到 879 条圈速记录。

如果日志出现以下内容，不应直接判断为“API 返回空数据”：

```text
Failed to load session info data
Failed to load extended driver information
```

在当前网络环境中，这些警告的底层原因是 Live Timing 请求经过代理后收到 HTTP 403。采集脚本的容错逻辑可能把失败端点记录为 0 条，因此应先设置 `NO_PROXY` 再重试。浏览器能够打开网页也不能证明 Python 走的是同一条网络路径；Python `requests`/`urllib` 会读取 Windows 当前用户的代理配置。

OpenF1 的读取接口统一使用 GET。即使服务端暂时接受 POST，也不要依赖该宽容行为。

## OpenF1 / FastF1 直接入库

如果目标是让数据直接进入 PostgreSQL，并且保留后续灵活查询能力，使用入库脚本：

```powershell
$env:DATABASE_URL="postgres://f1user:f1pass2026@localhost:5432/f1_analysis?sslmode=disable"
python tools\ingest_f1_to_postgres.py `
  --source all `
  --year 2026 `
  --meeting-key 1287 `
  --session-key 11307 `
  --fastf1-event Barcelona `
  --fastf1-session R
```

脚本会写两类数据：

- 规范化业务表：`meetings`、`sessions`、`drivers`、`laps`、`stints`、`team_radio`、`pit_stops`、`positions`、`race_control`。
- 原始快照表：`f1_api_snapshots`，保留 provider、endpoint、query、payload、record_count 和 fetched_at，便于后续补字段、排查源数据差异或重算分析。

只验证 OpenF1 最小链路：

```powershell
python tools\ingest_f1_to_postgres.py `
  --source openf1 `
  --year 2026 `
  --meeting-key 1287 `
  --session-key 11307 `
  --openf1-endpoints drivers
```

只验证 FastF1（先按上文设置 `NO_PROXY`）：

```powershell
python tools\ingest_f1_to_postgres.py `
  --source fastf1 `
  --year 2026 `
  --meeting-key 1287 `
  --session-key 11307 `
  --fastf1-event Barcelona `
  --fastf1-session R
```

FastF1 的 Live Timing 子接口仍可能发生真实的缺数据或超时。入库脚本会对 FastF1 的 `event`、`results`、`laps`、`weather`、`race_control` 分别容错：能拿到的写入业务表和快照表，拿不到的记录为 0，不会阻断 OpenF1 主链路。若当前机器启用了代理，应先排除上文所述的代理 403，再判断是否属于上游数据缺失。

## FastF1 原始采集

先安装依赖：

```powershell
python -m pip install -r requirements.txt
```

采集指定年份、分站和 session（先按上文设置 `NO_PROXY`）：

```powershell
python tools\fetch_f1_data.py `
  --source fastf1 `
  --year 2026 `
  --fastf1-event Barcelona `
  --fastf1-session R `
  --out data\raw\f1-api `
  --csv
```

FastF1 的 Live Timing 子接口偶尔会缺数据或超时。脚本会对 `event`、`results`、`laps`、`weather`、`race_control` 分别容错，能拿到多少写多少，并在 `manifest.json` 中记录每类数据的记录数。若记录数为 0 且日志出现 session/driver 加载失败，应先检查是否因系统代理收到 HTTP 403。

## OpenF1 + FastF1 静态前端导出

当前前端优先使用静态 JSON 合同。默认 Catalunya Race 导出命令：

```powershell
python tools\export_catalunya_static.py --out frontend\public\data
```

如果 FastF1 不稳定，可以先用 OpenF1 降级路径：

```powershell
python tools\export_catalunya_static.py --openf1-only --out frontend\public\data
```

导出后构建前端：

```powershell
cd frontend
npm run build
```
