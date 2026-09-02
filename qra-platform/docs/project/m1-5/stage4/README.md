# 阶段 4：不可变快照到 11/11 计算

阶段 4 已把阶段 3 经人工确认并写入 SQLite 的 `S00_BASELINE × D00_CLEAN` 不可变快照接入现有动态计算引擎，完成 11 个计算节点、数据库任务持久化、直接 JSON 对照、黄金基线比较、守恒校验、确定性复跑和反向谱系。

## 实现范围

- 计算任务只接受数据库快照 ID 和与快照正文一致的 SHA-256；快照正文不在阶段 4 修改。
- 6 个系统参数包的业务哈希、运行假设 `run-assumption:S00_BASELINE-v1` 及引擎/适配器版本与计算任务绑定。
- 快照适配到现有 `dynamic-case-v1` 输入，不引入第二套计算公式或旁路结果。
- 计算前生成能力计划，`S00 × D00` 的 11 个节点全部为 `RUNNABLE`。
- 自动创建数据库计算任务，执行并持久化节点结果、风险结果和产物；结果可从运行 ID 反查快照、参数包、运行假设、模型版本与阶段 3 转换记录。
- 以相同快照分别执行“数据库原始资料链”和“直接 JSON”路径；两条路径的 11 个节点原始 JSON 完全一致。
- 与阶段 2 黄金结果按引擎数值合同比较：浮点取 12 位有效数字、结果记录规范排序、排除非数值审计字段；原始表示尾差仍在差异报告中保留。
- 校验管段/机理/孔径频率守恒、Poisson 概率公式、气象概率归一、组分归一、场景分支守恒、PLL 管段求和和 F-N 曲线单调性。
- D20 删除 `population_cells` 后不补零、不写新快照，`human_qra` 明确跳过并生成补数清单；其余可运行节点继续执行。
- 合成数据、暂定验证模型和缺失生产批准参数使正式报告门始终保持关闭。

## 入口

运行阶段 4 主链：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage4.py
```

执行完整阶段门禁：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage4_acceptance.py
```

默认读取阶段 3 输出：

- `workspace/outputs/m1-5-stage3-raw-to-snapshot-20260901/D00_CLEAN/snapshot.json`
- `workspace/outputs/m1-5-stage3-raw-to-snapshot-20260901/stage3-snapshots.sqlite3`

阶段 4 正式输出位于 `workspace/outputs/m1-5-stage4-full-calculation-20260901`。

## 主要产物

| 产物 | 内容 |
|---|---|
| `calculation-job-binding.json` | 快照、6 个参数包、运行假设、任务和引擎版本绑定 |
| `result-diff-report.json` | 两条执行路径及黄金节点的逐节点原始/数值哈希差异 |
| `conservation-report.json` | 频率、概率、场景分支、组分、气象、PLL 和 F-N 守恒 |
| `deterministic-rerun-record.json` | 同一快照两次执行的数值哈希 |
| `reverse-provenance-record.json` | 11 个节点到快照、参数包、假设、模型和转换记录的反向谱系 |
| `standard-formula-path.json` | 节点标准引用、频率概率公式和模型轨迹 |
| `D20-missing-data-report.json` | 阻断节点、缺失字段和补数清单 |
| `source-chain-db-run/` | 数据库快照计算路径的完整引擎产物 |
| `direct-json-run/` | 直接 JSON 复跑路径的完整引擎产物 |

机器验收记录为 `resources/synthetic/full-chain-v1/stage4/stage4-acceptance.json`。
