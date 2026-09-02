# M1.5 阶段 2：多格式合成原始资料包

> 记录编号：`M1.5-S2-20260901`  
> 执行日期：2026-09-01  
> 阶段状态：`S2_SYNTHETIC_SOURCE_PACK_ACCEPTED`  
> 下一阶段：允许进入阶段 3，实施真实多格式解析、抽取、标准化与证据组装

## 1. 阶段结论

阶段 2 已生成并冻结 `S00_BASELINE × D00_CLEAN` 的完整多格式合成原始资料包。标准 JSON 只用于生成 ground truth、期望快照和差异判定；验收入口是 10 份 XLSX、CSV、DOCX、PDF、PNG 原始资料及其证据位置清单。

阶段门状态为 `S2_SYNTHETIC_SOURCE_PACK_ACCEPTED`。该状态表示资料包、字段证据、参数包、黄金答案、条件变体和确定性检查均已就绪，不表示阶段 3 的抽取转换链已经完成，也不允许生成正式工程报告。

## 2. 任务完成情况

| 任务 | 状态 | 实现证据 |
|---|---|---|
| S2-01 Manifest Schema | PASS | `source-pack-manifest.schema.json` |
| S2-02 参数包 Schema | PASS | `parameter-pack.schema.json` |
| S2-03 确定性生成器 | PASS | `tools/build_synthetic_source_pack.py` |
| S2-04 从 S00 标准 JSON 读取项目事实 | PASS | 260 个项目事实合同逐字段解析，256 个事实落入资料证据，4 个容器按结构派生 |
| S2-05 台账、工况、组分、CIPS、人口、气象 | PASS | 5 个 XLSX、2 个 CSV |
| S2-06 DOCX 缺陷与维修 | PASS | 5 类原型缺陷维修明细及字段证据记录 |
| S2-07 扫描 PDF | PASS | D00 清晰扫描件与 D30 低质量扫描件 |
| S2-08 合成图片 | PASS | D00 现场示意、D40 超长图、D50 诱导文字图 |
| S2-09 全文件合成标识 | PASS | 10/10 原始资料均验证 `SYNTHETIC_TEST_ONLY` |
| S2-10 ground truth | PASS | 260 个项目事实状态、256 个证据值、6 个参数包绑定 |
| S2-11 字段到证据位置清单 | PASS | XLSX 单元格、CSV 行、DOCX 表格、PDF 页框坐标均已登记 |
| S2-12 合成参数库 | PASS | 6 个带版本参数包、68 个模型参数 |
| S2-13 expected snapshot | PASS | 标准输入快照和清单已生成 |
| S2-14 expected results/hashes | PASS | 11 个节点结果及全合同版本数值哈希；阶段 0 原始基线单独保留 |
| S2-15 D10–D70 条件变体 | PASS | 冲突、缺失、低质扫描、超长图、提示注入、重复版本、单位异常 |
| S2-16 禁止随机漂移 | PASS | 两次独立生成的业务哈希和数值哈希完全一致 |

## 3. 阶段门验收

| 验收项 | 实测结果 | 状态 |
|---|---|---|
| S00 × D00 资料齐全 | 10/10 原始资料、6/6 参数包、4/4 黄金主文件 | PASS |
| 关键项目事实可定位 | 25/25 `BLOCKING` 项目事实存在证据位置且值哈希一致 | PASS |
| 原始值与黄金答案一致 | 256/256 证据条目与 ground truth 使用同一规范值哈希 | PASS |
| 全合同数值基线 | 11/11 节点完成；数值哈希 `2d351acf...d25286` | PASS |
| 两次生成无业务漂移 | 当前业务哈希均为 `47892dc3...c85d6` | PASS |
| D10 冲突可验证 | 运行压力 8.00 MPa 与 8.35 MPa 显式冲突 | PASS |
| D20 关键资料缺失 | 移除人口和敏感受体资料并要求受影响节点阻断 | PASS |
| D30/D40 安全接收 | 文件入口均返回 `READY_FOR_PARSE`，无隔离 | PASS |
| D50 不形成业务事实 | `ground_truth_binding_count=0` | PASS |
| 现有系统无回归 | 2026-09-02 最终回归 `312 passed, 1 skipped` | PASS |

机器可读结论见 [stage2-acceptance.json](../../../../resources/synthetic/full-chain-v1/stage2/stage2-acceptance.json)。

## 4. 交付物索引

- [版本化阶段 2 目录](../../../../resources/synthetic/full-chain-v1/stage2)
- [S00 × D00 Manifest](../../../../resources/synthetic/full-chain-v1/stage2/generated/S00_BASELINE_D00_CLEAN/source-pack-manifest.json)
- [ground truth](../../../../resources/synthetic/full-chain-v1/stage2/generated/S00_BASELINE_D00_CLEAN/golden/ground-truth.json)
- [字段证据位置清单](../../../../resources/synthetic/full-chain-v1/stage2/generated/S00_BASELINE_D00_CLEAN/golden/evidence-manifest.json)
- [期望结果](../../../../resources/synthetic/full-chain-v1/stage2/generated/S00_BASELINE_D00_CLEAN/golden/expected-result.json)
- [业务内容哈希](../../../../resources/synthetic/full-chain-v1/stage2/business-content-hashes.json)
- [确定性验证记录](../../../../resources/synthetic/full-chain-v1/stage2/determinism-verification.json)
- [用户可用资料包](../../../../workspace/outputs/m1-5-stage2-source-pack-20260901/S00_BASELINE_D00_CLEAN.zip)
- [阶段 2 验收记录](阶段2验收记录.md)
- [生成与复现说明](生成与复现说明.md)
- [视觉验收记录](视觉验收记录.md)
- [测试摘要](test-results.txt)

## 5. 阶段边界

- 产品等级仍为 `DEMO_SYNTHETIC`。
- 全部资料均为人工合成，不对应任何真实管道、人员或设施。
- `formal_report_allowed=false` 保持不变。
- 阶段 3 必须从这些多格式原始资料进入解析/OCR/抽取链，禁止直接把标准 JSON 当作原始资料输入。

## S10–S40 扩展资料包（2026-09-02）

`tools/build_extended_synthetic_source_packs.py` 已为 S10、S20、S30、S40 各生成 10 份 XLSX/CSV/DOCX/PDF/PNG 原始资料、6 个参数包、256 项直接证据、黄金快照和 11 节点结果，共 40 份原始资料。四个场景均命中 `full-contract-result-hashes.json` 的全合同版本基线；机器结果见 `extended-scenarios-acceptance.json`。
