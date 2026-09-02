# M1.5 阶段 0：冻结基线和完成定义

> 记录编号：`M1.5-S0-20260901`  
> 执行日期：2026-09-01  
> 阶段状态：`S0_BASELINE_FROZEN`  
> 下一阶段：允许进入阶段 1

## 1. 阶段结论

M1.5“全合成原始资料到完整 QRA 测试报告闭环”的实施边界、完成定义和回退基线已经冻结。阶段 0 的十项任务均已完成：

| 任务 | 状态 | 证据 |
|---|---|---|
| S0-01 保存 Git、依赖和架构基线 | PASS | [基线版本记录](基线版本记录.md)、[Git 状态](git-status-before-baseline.txt)、[依赖清单](dependencies.txt)、[架构检查](architecture-check.txt) |
| S0-02 保存 254 项测试结果 | PASS | [测试结果记录](测试结果记录.md)、[测试摘要](test-results.txt) |
| S0-03 记录 5 个数值哈希 | PASS | [合成结果哈希记录](合成结果哈希记录.md)、[机器可读哈希](synthetic-result-hashes.json) |
| S0-04 记录数据库 Schema | PASS | [数据库 Schema 记录](database-schema.json) |
| S0-05 目标限定为 `DEMO_SYNTHETIC` | PASS | [范围和非范围说明](范围和非范围说明.md) |
| S0-06 首链固定为 `S00_BASELINE × D00_CLEAN` | PASS | [范围和非范围说明](范围和非范围说明.md) |
| S0-07 固定 11 个目标节点 | PASS | [范围和非范围说明](范围和非范围说明.md) |
| S0-08 禁止用标准 JSON 绕过原始资料链 | PASS | [范围和非范围说明](范围和非范围说明.md)、[决策记录](决策记录.md) |
| S0-09 所有演示报告阻断正式签发 | PASS | [合成结果哈希记录](合成结果哈希记录.md)、[决策记录](决策记录.md) |
| S0-10 建立决策和风险登记册 | PASS | [决策记录](决策记录.md)、[风险登记册](风险登记册.md) |

机器可读的总验收结论见 [stage0-acceptance.json](stage0-acceptance.json)。

## 2. 阶段门验收

| 验收项 | 实测结果 | 状态 |
|---|---|---|
| 新目录或新环境可以启动当前系统 | 新建隔离虚拟环境完成依赖导入、11 节点目录读取、数据库 `2.1.0` 初始化及三个 CLI 冒烟 | PASS |
| 现有 254 项测试无回归失败 | `Ran 254 tests in 114.947s`；0 failure、0 error、1 项在线 OCR 条件跳过 | PASS |
| 5 个现有合成场景仍为 PASS | 5/5 场景均完成 11/11；0 skipped、0 failed；响应关系检查全部通过 | PASS |
| “演示可用不等于正式 QRA 可用”无歧义 | 产品级别、禁止用途、签发门禁和最终状态已写入范围、DoD 和决策记录 | PASS |

阶段门结论：`S0_BASELINE_FROZEN`。该状态只允许开始阶段 1，不表示 M1.5 已完成，也不表示任何真实工程数据、生产模型参数或正式报告已获批准。

## 3. 冻结边界摘要

- 唯一产品等级：`DEMO_SYNTHETIC`。
- 第一条验收链：`S00_BASELINE × D00_CLEAN`。
- 目标计算节点：固定 11 个，详见范围说明。
- 标准 JSON 只作为黄金答案和差异判定依据，不是原始资料入口。
- 正式验收必须经过多格式原始资料、解析/OCR、提取、标准化、复核、门禁和不可变快照。
- 所有演示输入、结果和报告必须保持 `formal_report_allowed=false`。
- M1.5 最终状态只能在完整 DoD 全部满足后设为 `M1_5_FULL_SYNTHETIC_END_TO_END_ACCEPTED`。

## 4. 基线与运行证据

- 代码基线：Git 提交 `1ebdc65c6169f6c5a999c80bfa9b7c74f3b1a99f`。
- 回退标签：`roadmap-stage4-internal-pilot-20260901`，当前指向该提交。
- 运行证据：`workspace/outputs/m1-5-stage0-baseline-20260901/`。
- 新环境冒烟：`workspace/runtime/m1-5-stage0-smoke-env-20260901/`。

`workspace` 下的运行产物受 `.gitignore` 隔离，可由记录中的命令重建，不属于代码基线。

## 5. 交付物索引

- [基线版本记录](基线版本记录.md)
- [测试结果记录](测试结果记录.md)
- [合成结果哈希记录](合成结果哈希记录.md)
- [范围和非范围说明](范围和非范围说明.md)
- [M1.5 完成定义](M1.5完成定义.md)
- [决策记录](决策记录.md)
- [风险登记册](风险登记册.md)
- [阶段 0 机器可读验收记录](stage0-acceptance.json)
- [基线输入文件 SHA-256](baseline-input-files.sha256)

