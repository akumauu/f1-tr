# 2026-07-18 本地存储瘦身报告

## 执行结论

本轮清理共回收 `58,922,233,761` 字节：

- 文件系统回收 `2,486,792,097` 字节（约 2.32 GiB）。
- PostgreSQL 回收 `56,435,441,664` 字节（约 52.56 GiB）。
- `f1_analysis` 数据库从 `58,152,040,127` 字节降至 `1,716,598,463` 字节。

逐点遥测已从 PostgreSQL 常驻数据调整为显式可重建缓存；正式研究与产品均不再依赖这两张表。

## 删除内容

| 内容 | 删除前字节数 | 处置 |
| --- | ---: | --- |
| `schema=tracinginsights-expanded-v1` | 682,326,257 | 删除 |
| `schema=tracinginsights-expanded-v2` | 501,780,679 | 删除 |
| `schema=tracinginsights-expanded-v3` | 1,301,886,248 | 删除 |
| 三套验收/烟雾训练缓存 | 793,763 | 删除，可从 v4 重建 |
| `_db_check_tmp.py`、`_inventory_tmp.py` | 5,150 | 删除 |

清理后 `data/normalized/tracinginsights/` 只保留
`schema=tracinginsights-expanded-v4`，其验收 manifest 和 Parquet 未被修改。

## PostgreSQL 处置

消费者审计结果：

- Go 后端和前端没有读取 `telemetry_car_data` 或 `telemetry_location`。
- 唯一读者是离线 `audit_2025_tier_data.py`，现已改为按存储策略和快照覆盖审计。
- 两张表没有被其他表通过外键引用。

执行前精确行数：

| 表 | 行数 | 总体积 |
| --- | ---: | ---: |
| `telemetry_car_data` | 55,046,108 | 28,926,509,056 B |
| `telemetry_location` | 54,347,570 | 26,592,477,184 B |

两表已原子 `TRUNCATE`，清理后均为 0 行、24,576 B，空表 schema 保留。

OpenF1 恢复来源仍保存在 `f1_api_snapshots`：

- `car_data`：1,911 个成功快照，54,530,953 条源记录。
- `location`：1,913 个成功快照，55,188,291 条源记录。

没有查询消费者的 `idx_f1_api_snapshots_payload_gin` 已删除，额外回收
916,258,816 字节；快照 JSON 本身保留。

## 防回流措施

- TracingInsights PG 导入必须显式传入 `--allow-deprecated-postgres-telemetry`。
- OpenF1 PG 物化必须显式传入 `--materialize-postgres-cache`。
- `finish_telemetry_import.ps1` 默认拒绝执行，只有传入 `-MaterializePostgresCache` 才允许重建。
- migration 012 会删除未使用的 payload GIN 索引，并给两张表标记“可丢弃重建缓存”。
- 可通过 `tools/cleanup_obsolete_storage.py` dry-run 或显式复跑白名单清理。

机器审计原始报告位于忽略目录 `data/logs/storage-cleanup-report.json`。
