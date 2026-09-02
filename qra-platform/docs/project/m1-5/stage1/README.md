# M1.5 阶段 1：完整字段合同和节点覆盖矩阵

> 记录编号：`M1.5-S1-20260901`  
> 执行日期：2026-09-01  
> 阶段状态：`S1_FULL_CONTRACT_MAPPED`  
> 下一阶段：允许进入阶段 2，按缺口登记册扩展抽取 Schema 与标准化规则

## 1. 阶段结论

阶段 1 已把当前 QRA 输入 Schema、280 个字段字典字段、11 个动态节点的运行时输入以及人工复核发现的隐式输入合并为一份可重建、可校验的完整字段合同。规范主文件为：

- [`field-source-node-matrix.csv`](../../../../resources/synthetic/full-chain-v1/field-source-node-matrix.csv)

矩阵共 361 个唯一字段，包含文档要求的 14 个最小列和 10 个实现审计列。每个字段都已标明数据层、来源、单位、关键性、抽取方法、目标节点、缺失策略、冲突策略和证据要求。所有 115 个节点必需输入均有已登记来源，不存在未声明的静默零值回退。

## 2. 任务完成情况

| 任务 | 状态 | 实现证据 |
|---|---|---|
| S1-01 导出当前 `qra-input` 全部字段 | PASS | `qra-input-contract-fields.json`，覆盖 280 个字段字典字段、71 个显式 Schema 叶字段及运行时补充字段 |
| S1-02 导出 11 个动态节点输入 | PASS | `node-input-contract.json`，115 个必需输入、14 个可选输入 |
| S1-03 划分三类数据层 | PASS | 260 个 `PROJECT_FACT`、68 个 `MODEL_PARAMETER`、33 个 `RUN_ASSUMPTION` |
| S1-04 项目事实分配原始资料 | PASS | 260/260 均指向 10 类合成原始资料 |
| S1-05 模型参数分配参数包 | PASS | 68/68 均指向 6 个已登记的带版本参数包 |
| S1-06 统一标准单位 | PASS | `source_unit` 与 `target_unit` 已逐字段登记 |
| S1-07 标记关键性 | PASS | 77 `BLOCKING`、244 `IMPORTANT`、40 `OPTIONAL` |
| S1-08 标记受影响节点 | PASS | `target_nodes` 覆盖当前 11 节点目录 |
| S1-09 定义证据位置要求 | PASS | `source_location_type` 与 `evidence_required` 已登记 |
| S1-10 定义获取方式 | PASS | `deterministic`、`ocr`、`llm`、`manual`、`system` 已落到字段级 |
| S1-11 识别实现缺口 | PASS | `coverage-gap-register.json` 在阶段 1 登记 218 项；现已由 `coverage-gap-closure.json` 逐项处置并取代其开放状态 |
| S1-12 自动化覆盖检查 | PASS | 生成器 `--check` 与 5 项单元测试可捕获新增字段、节点或生成物漂移 |

## 3. 阶段门验收

| 验收项 | 实测结果 | 状态 |
|---|---|---|
| 所有节点必需输入都有来源 | 115/115 已登记来源，且均为 `BLOCKING` | PASS |
| 不存在未声明默认值 | 所有缺失策略显式登记；禁止用 0 代替缺失值 | PASS |
| 项目事实均来自原始资料 | 260/260 已分配到 10 类合成原始资料 | PASS |
| 模型参数均来自版本参数包 | 68/68 已分配到 6 个已登记且带 `-v1` 标识的参数包 | PASS |
| 运行假设显式化 | 33/33 已绑定运行假设包、项目向导或受控系统来源 | PASS |
| 新增节点或字段可被发现 | 生成器读取当前 Schema、字段字典和动态节点目录；`--check` 对漂移返回失败 | PASS |
| 现有系统无回归 | 2026-09-02 最终回归 `312 passed, 1 skipped` | PASS |

机器可读验收结论见 [`stage1-acceptance.json`](../../../../resources/synthetic/full-chain-v1/stage1-acceptance.json)。阶段门结论为 `S1_FULL_CONTRACT_MAPPED`。

## 4. 当前覆盖缺口

以下是阶段 1 验收时的历史基线：当时字段合同已经完整，但现有实现尚未完全消费这份合同，218 个字段存在至少一种实施缺口：

| 缺口类型 | 字段数 |
|---|---:|
| 仅确定性映射缺口 | 137 |
| 参数包合并尚未实现 | 40 |
| 仅复核组装缺口 | 21 |
| 映射、抽取 Schema 与复核组装均缺失 | 19 |
| 抽取 Schema 与复核组装缺失 | 1 |

2026-09-02 全合同补齐后，旧登记册状态为 `SUPERSEDED_BY_COVERAGE_CLOSURE`。新 [`coverage-gap-closure.json`](../../../../resources/synthetic/full-chain-v1/coverage-gap-closure.json) 验证原 218/218 项均已实现：153 项由直接证据或聚合映射消费、40 项由参数包合并、21 项由运行假设组装、4 项由结构派生，M1.5 阻断项为 0。口径仍保持诚实：361 个字段都有实现路径，但只有项目事实叶子进入文件证据链，参数、运行假设和结构容器不冒充 OCR/文件提取证据。

## 5. 交付物索引

- [字段来源节点矩阵](../../../../resources/synthetic/full-chain-v1/field-source-node-matrix.csv)
- [节点输入合同](../../../../resources/synthetic/full-chain-v1/node-input-contract.json)
- [QRA 输入字段全集](../../../../resources/synthetic/full-chain-v1/qra-input-contract-fields.json)
- [覆盖缺口登记册](../../../../resources/synthetic/full-chain-v1/coverage-gap-register.json)
- [机器可读验收记录](../../../../resources/synthetic/full-chain-v1/stage1-acceptance.json)
- [字段合同与覆盖口径](字段合同与覆盖口径.md)
- [阶段 1 验收记录](阶段1验收记录.md)
- [测试摘要](test-results.txt)

## 6. 重建与校验

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe .\tools\build_full_chain_stage1_contract.py
.\.venv\Scripts\python.exe .\tools\build_full_chain_stage1_contract.py --check
.\.venv\Scripts\python.exe -m unittest tests.unit.test_full_chain_stage1_contract -v
```

第一条命令从当前代码合同重建五个规范文件；第二条命令只检查，不写文件，并在字段、节点或生成物发生未同步变化时返回非零退出码。
