# TracingInsights 公开遥测数据整理说明

## 1. 文档目的

本文档单独登记 `TracingInsights-Archive` 2023—2026 数据包的来源、下载状态、目录规则、数据库分类、质量校验、已知缺失和复现命令。原始数据不因当前模型是否使用而删减；科研训练集由后续冻结步骤从完整数据湖中派生。

## 2. 来源与本地位置

| 年份 | 上游仓库 | 本地目录 | Git 提交 | 下载状态 |
| --- | --- | --- | --- | --- |
| 2023 | `https://github.com/TracingInsights-Archive/2023.git` | `data/raw/tracinginsights/2023` | `e4b7468f2cdc1a2f86ba94a1eb8f1477719ea582` | 已完整同步当前工作树 |
| 2024 | `https://github.com/TracingInsights-Archive/2024.git` | `data/raw/tracinginsights/2024` | `869496da863e00e4b060e98d52b9826d0ac4005c` | 已完整同步当前工作树 |
| 2025 | `https://github.com/TracingInsights-Archive/2025.git` | `data/raw/tracinginsights/2025` | `f7a5324cae589ecdbfbf15a114b784dec180b1f6` | 已完整同步当前工作树 |
| 2026 | `https://github.com/TracingInsights-Archive/2026.git` | `data/raw/tracinginsights/2026` | `81b90e61051a83149ff744bc266b55686cb2a6a3` | 已完整同步上游当前工作树；不等于完整赛季 |

原始目录结构保持上游形式：

```text
年份仓库/
  比赛周末/
    赛段/
      车手缩写/
        laptimes.json
        <圈号>_tel.json
      drivers.json
      weather.json
      rcm.json
      session_laptimes.json（部分年份/赛段存在）
```

## 3. 统一分类

数据库表 `tracing_dataset_files` 对完整工作树中的每个非 Git 文件建立一条目录记录：

| category | 含义 |
| --- | --- |
| `telemetry_lap` | 单车手单圈遥测 `*_tel.json` |
| `driver_laptimes` | 单车手赛段圈时索引 `laptimes.json` |
| `session_metadata` | 车手、天气、赛会控制、赛段圈时等元数据 |
| `cache` | 上游仓库附带的缓存文件 |
| `repository_support` | 脚本、Notebook、说明、许可证和其他支持文件 |

赛段名称统一为：

- `Practice 1`
- `Practice 2`
- `Practice 3`
- `Qualifying`
- `Sprint Qualifying`（2023 `Sprint Shootout` 映射到此名称）
- `Sprint`
- `Race`

## 4. 完整性与内容校验

文件级目录入库执行以下检查：

1. 所有文件计算 SHA-256；
2. 遥测、圈时和赛段元数据 JSON 必须可解析；
3. 单圈遥测必须包含 `time/speed/throttle/brake/gear/rpm/drs/x/y/z`；
4. 遥测数组必须与 `time` 等长；
5. `time` 必须非空且单调递增；
6. 登记采样数、字段列表与遥测持续时间；
7. 无法通过的文件保留原文，状态记为 `invalid`，不得静默删除。

每次目录导入汇总写入 `tracing_dataset_imports`，包括来源提交、文件数、遥测文件数、有效数、无效数、总字节数和完成状态。

## 5. 三层数据链路

唯一允许的数据流向是：

```text
raw JSON（不可变）
  → Parquet（规范分析层、算法读取的唯一真相）
    → NPZ/Arrow（按实验派生的训练缓存，可丢弃、可重建）
```

### 5.1 原始层：不可变 JSON

原始 Git 工作树保持上游字节不变，不重命名、不修补、不因当前模型不用而删除。PostgreSQL 的 `tracing_dataset_files` 只登记路径、哈希、分类和验证结果，不是遥测主仓。

### 5.2 规范层：展开行 Parquet

`tools/convert_tracinginsights_to_parquet.py` 将单圈 JSON 展开为逐采样点行：

- 连续数值统一为 `float32`；
- 文件内按 `(driver_number, lap, time)` 排序；
- 使用 Zstandard 压缩、字典编码、列统计和行组；
- 按 schema、年份、来源提交、赛段和比赛分区；
- 每个分区记录来源文件数、来源清单哈希、行数、Parquet SHA-256；
- 已存在的 commit 分区拒绝覆盖，修订 schema 必须产生新版本目录。

主仓采用展开行，便于 DuckDB、Polars 和 SQL 按车手、圈号、时间进行谓词下推；“一圈一个 list 样本”的视图查询时生成，不另立主数据。

旧脚本 `tools/ingest_tracinginsights_telemetry.py` 仍可将少量选定赛段导入 PostgreSQL 的 `telemetry_car_data` / `telemetry_location`，但它只是兼容用途。两表分别保存车辆信号和位置、并重复保留 `raw_payload`，不适合作为全量规范层。

### 5.3 训练层：可丢弃缓存

`tools/build_training_cache.py` 从规范 Parquet 按实验筛选圈，并在等距 distance 网格或固定 time 网格上重采样、对齐，输出 `.npz`：

- `X`：`[圈, 网格点, 特征]` 的 `float32` 张量；
- `mask`：有效性掩码；
- `groups`：年份、比赛、赛段、车手、圈号；
- `manifest.json`：完整筛选配置、配置哈希、形状、跳过原因和文件哈希。

缓存位于 `research/cache/<experiment>/<config_hash>/`，可随实验删除。任何缓存都必须可从 Parquet 重建，禁止回写原始层或规范层。

## 6. 复现命令

完整同步后，逐年登记：

```powershell
python tools\catalog_tracinginsights_dataset.py `
  --year 2024 `
  --root "data\raw\tracinginsights\2024"
```

生成规范 Parquet：

```powershell
python tools\convert_tracinginsights_to_parquet.py `
  --year 2024 `
  --root "data\raw\tracinginsights\2024"
```

查询规范层：

```sql
SELECT driver_number, lap, min(time), max(time), count(*) AS samples
FROM read_parquet('data/normalized/tracinginsights/**/*.parquet', hive_partitioning=false)
WHERE year = 2024 AND session = 'Race' AND driver_acronym = 'VER'
  AND lap BETWEEN 30 AND 40
GROUP BY driver_number, lap
ORDER BY driver_number, lap;
```

派生训练缓存：

```powershell
python tools\build_training_cache.py `
  --parquet-root "data\normalized\tracinginsights\schema=tracinginsights-expanded-v4\year=2024" `
  --experiment "race-distance-v1" `
  --meeting "Bahrain Grand Prix" `
  --session "Race" `
  --drivers "1,11" `
  --laps "30,31,32,33,34,35,36,37,38,39,40" `
  --grid distance `
  --points 1024
```

按赛段展开示例：

```powershell
python tools\ingest_tracinginsights_telemetry.py `
  --year 2024 `
  --root "data\raw\tracinginsights\2024" `
  --sessions "Qualifying,Sprint Qualifying,Sprint,Race"
```

目录查询示例：

```sql
SELECT year, session_name, category, validation_status,
       COUNT(*) AS files,
       SUM(file_size_bytes) AS bytes,
       SUM(sample_count) AS samples
FROM tracing_dataset_files
GROUP BY year, session_name, category, validation_status
ORDER BY year, session_name, category, validation_status;
```

## 7. 最终下载与入库登记

| 年份 | 全部文件 | 遥测文件 | 有效遥测 | 无效遥测 | 原始字节数 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023 | 62,547 | 59,787 | 59,786 | 1 | 10,911,212,521 |
| 2024 | 70,729 | 67,616 | 67,616 | 0 | 12,506,824,489 |
| 2025 | 106,589 | 69,459 | 69,459 | 0 | 14,779,828,683 |
| 2026 | 22,191 | 21,428 | 21,428 | 0 | 4,743,844,382 |

2023 唯一无效文件为 `São Paulo Grand Prix/Sprint Shootout/SAR/6_tel.json`：`acc_y`、`acc_z` 与主时间轴数组长度不一致。原始 JSON 原样保留，规范层跳过该圈并在 manifest 的 `skipped_files` 中登记。

2024 的 Git 稀疏转完整工作树方法连续失败三次（TLS EOF、挂起、低速超时）后按规则停止，改用 GitHub codeload 归档；归档 SHA-256 为 `870325FB59598949415C3A022403D305E8EA77808E755F1907722B14B26DE2CE`。解包后以对应提交树为准完成逐文件目录和哈希校验。2025 的第一次过滤 Git 包在 2.814 GB 时中断且 `index-pack` 报 `early EOF`，失败产物隔离后第二次 Git 下载成功。

2026 的“完整”仅指本地与上游提交 `81b90e6...` 的当前公开工作树一致；该仓库当前只含测试和四个比赛周末，因此后续赛季内容属于上游尚未发布，不属于本地漏下载。

规范层固定使用 `schema=tracinginsights-expanded-v4`。开发中产生的 v1—v3 目录没有最终 manifest，不是唯一真相，也不得用于正式实验。任何下载或解析方法连续失败三次后，登记失败原因并切换下一种可行方法。

## 8. 规范层最终验收（2026-07-17）

### 8.1 清单自报总量（来自各年 manifest.json `totals`）

| 年份 | 分区 | 源文件 | 无效源文件 | 行数 | Parquet 体积 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2023 | 112 | 59,787 | 1 | 60,221,664 | 3.37 GB |
| 2024 | 123 | 67,616 | 0 | 68,814,885 | 3.84 GB |
| 2025 | 123 | 69,459 | 0 | 69,847,468 | 3.91 GB |
| 2026 | 29 | 21,428 | 0 | 28,081,700 | 1.32 GB |
| 合计 | 387 | 218,290 | 1 | 226,965,717 | ≈12.4 GB |

对比：原始 JSON 约 42.9 GB，规范层压缩比约 3.5:1；此前 PostgreSQL 采样级双表方案同等数据需数十 GB。

### 8.2 已完成的结构核验（目录/清单级）

- manifest 分区条目 387 个 ↔ 磁盘 `telemetry.parquet` 387 个，逐年计数一致（112/123/123/29）；
- 每年恰好一个 `commit=` 目录，与第 2 节登记的上游提交一致；
- 赛段覆盖完整：常规周末 P1/P2/P3/Qualifying/Race，冲刺周末含 Sprint / Sprint_Qualifying，季前测试以 `Pre-Season_Testing*` 入仓；2026 为进行中赛季（4 个比赛周末 + 3 次季前测试）；
- 2023 唯一无效源文件（`São Paulo GP/Sprint Shootout/SAR/6_tel.json`，acc_y/acc_z 数组长度不一致）已隔离并在 manifest `skipped_files` 登记原因，原始 JSON 未改动；
- 2023/2024 manifest 分别含 103/114 个 `resumed` 分区（断点续转时经 schema 与源文件清单校验后复用）；
- 训练缓存链路已有合规产物：`research/cache/canonical-v4-smoke/f546b46e5829d039/`（源自 v4 2024 Bahrain Race，shape [4,256,12]，manifest 含配置哈希与 SHA-256）。

### 8.3 数据体级核验（已通过）

`tools/acceptance_check_parquet.py` 执行以下独立核验并输出 `data/logs/parquet-acceptance-report.json`：

1. 逐分区实际行数/字节数与 manifest 对账（读 Parquet 元数据）；
2. 每年抽样 3 个分区：Parquet SHA-256 复算、schema 与参考完全一致、连续量 float32、`sort_order` 元数据、`(driver_number, lap, time)` 实际排序校验；
3. DuckDB 跨季查询：年×赛段覆盖矩阵、正赛速度跨季对比（谓词下推）、排位赛尾速抽查。

2026-07-17 20:18（Asia/Shanghai）已执行以下命令：

```powershell
python tools\acceptance_check_parquet.py
python -m pytest tests\ -q
python tools\build_training_cache.py `
  --parquet-root "data\normalized\tracinginsights\schema=tracinginsights-expanded-v4\year=2024" `
  --experiment "acceptance-20260717" `
  --meeting "Bahrain Grand Prix" --session "Race" `
  --drivers "1,11" --laps "30,31,32,33,34,35,36,37,38,39,40" `
  --grid distance --points 1024
```

实测结果：

- `acceptance_check_parquet.py` 终局为 **PASS**：387 个分区、226,965,717 行、12.44 GB 与 manifest 全部一致；按固定随机种子每年抽 3 个、合计 12 个分区，其 SHA-256、统一 schema、连续量 float32、`sort_order` 元数据及 `(driver_number, lap, time)` 实际排序均通过；DuckDB 跨季覆盖、正赛速度和排位赛尾速查询成功。机器可读报告见 `data/logs/parquet-acceptance-report.json`，其中 `errors=[]`。
- `python -m pytest tests\ -q`：**18 passed in 2.58s**。
- 训练缓存构建成功：`research/cache/acceptance-20260717/ba7619f0826bff49/`，`config_hash=ba7619f0826bff49`，shape 为 `[22, 1024, 12]`，写入 22 组、跳过 0 组，`sequences.npz` SHA-256 为 `ecaa12a85c45eb0158b9f919433384bc170127bc8395d3c924f4777506bcb4a5`。

最终结论：**结构层 PASS，数据体层 PASS，测试与训练缓存链路 PASS；规范层最终验收完成。**
