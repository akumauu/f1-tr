# F1 车辆—车手分离实验

本目录执行“2024 年训练、2025 年锁定验证”的第一轮科研实验。2025 年验证数据不得参与模型选择、超参数选择、清洗阈值调整或特征筛选。

## 当前研究边界

- 第一阶段主动限定为 `Race` 与 `Sprint`。v4 规范层已经包含 `Qualifying` 与 `Sprint Qualifying`；排位模型因目标、清洗和分段结构不同，仍应另立实验协议，不能直接混入首轮比赛圈速模型。
- 第一阶段估计的是干地、非进出站、非明显赛会中断状态下的**回顾性相对比赛圈速**。
- 每个赛段内部使用中位圈速做参照。这适合车手—车辆相对分解，不等同于赛前绝对圈速预测。
- 2025 年只做一次锁定验证。若根据 2025 结果修改模型，必须生成新的实验版本，并把 2025 降级为开发集，另选 2026 已完成场次作为验证集。

## 阶段安排

1. **阶段 0：数据冻结。** 从验收通过的 `tracinginsights-expanded-v4` Parquet 提取 2024/2025 Race+Sprint，使用 `is_accurate`、绿旗、干胎和稳健异常值规则清洗，生成带 schema、来源 commit、配置哈希和文件哈希的数据 manifest。PostgreSQL 路径只保留用于复现旧基线。
2. **阶段 1：简单基线。** 零预测、仅上下文 ridge、仅车手 ridge、仅车队 ridge。
3. **阶段 2：车手—车队分解。** 同时加入车手和车队的正则化模型，观察其相对验证收益与系数稳定性。
4. **阶段 3：动态/层级模型。** 在不读取 2025 调参的前提下，引入比赛轮次动态效应、车手—车辆交互和概率区间。
5. **阶段 4：排位与多视图模型。** 回填 2024 排位数据后独立执行，不与第一阶段混用。

首轮 v1 查看 2025 结果后，所有新增结构都标记为“阶段 3 探索性”。这些模型仍以 2024 GroupKFold 选择参数、以 2025 计算外样本指标，但 2025 已不再是完全未见的确认性验证集；最终确认应转向冻结的 2026 已完成场次。

## 三次失败协议

每个方法最多执行三次：

- 每次失败均写入 `records/experiment_log.jsonl`，包含方法、尝试序号、错误类型和错误摘要；
- 允许的重试只包括实现错误、资源限制或数值求解器替换，不允许查看 2025 验证结果后调整模型；
- 第三次仍失败时写入 `skipped_after_3_failures`，停止该方法并进入下一个模型；
- 数据提取失败时可启动下一种、预先定义的降级提取方法，但必须使用新的 `method` 名称。

## 运行

```powershell
python research\run_experiments.py
```

只重建 v4 圈级冻结，不运行模型：

```powershell
python research\build_v4_frozen_data.py --archive-existing
```

只重用已经冻结的数据：

```powershell
python research\run_experiments.py --reuse-data
```

仅复现验收前 PostgreSQL 数据口径：

```powershell
python research\run_experiments.py --legacy-postgres
```

阶段 3 探索性模型：

```powershell
python research\run_phase3.py
```

首轮模型的配对分站 bootstrap：

```powershell
python research\analyze_model_comparisons.py
```

主要输出：

```text
research/artifacts/data/          # 本地冻结 CSV，不进入 Git
research/artifacts/data_manifest.json
research/records/experiment_log.jsonl
research/records/latest_results.json
research/records/latest_summary.md
```

## 结果解释

- 目标值单位为 `100 × log(lap_time / session_median)`，负数更快。
- 车手/车队 `pace_advantage_pct` 已反号并做零均值中心化，正数表示相对更快。
- ridge 系数是正则化条件关联，不是物理因果真值。
- 新秀或新车队在 2024 未出现时使用零效应先验，并单独报告未见类别比例。
