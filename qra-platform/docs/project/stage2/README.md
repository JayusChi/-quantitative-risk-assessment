# 阶段 2 真实数据试算与差异整改

> 记录编号：M2-20260826  
> 执行日期：2026-08-26  
> 技术状态：`PASSED_INTERNAL_STAGE2_FUNCTION_VALIDATION`  
> G2 计算门：`PASSED_SCREENING_EXECUTION`  
> M2 里程碑：`HOLD_FOR_DATA_COMPLETION_AND_BUSINESS_REVIEW`

## 1. 执行结论

九江支线 `GDBZYQ-JJ-1` 已使用阶段 1 不可变快照 `SNAP-b1cfcc71-3f9e-4c67-8b03-05de5396a798` 完成当前能力试算、差异整改和两次真实 HTTP 管理链路复跑。两次运行均完成，动态状态均为 `PARTIAL`，数值结果哈希一致为 `b125e2787bef9c9a0728fe253796577e2cbf095524ca6c67cccc0b829367cd09`。

11 个动态节点中，5 个完成、6 个因明确输入缺口跳过、0 个运行失败。已生成数据盘点、指标覆盖、单段几何、证据条件化 PLL 筛查、风险矩阵、SVG 图表、HTML 报告、CSV、JSON 和 ZIP。结果层级固定为 `EVIDENCE_CONDITIONED_SCREENING_ESTIMATE`，正式接受性判断未放行。

当前只有 1 个全线管段和 0 个空间人口单元，因此“排名第 1、全线贡献 100%”不具有区段比较意义。PLL 使用模型人口密度、压力、温度、壁厚和物性先验，只验证平台能够形成受控筛查结果；IR 与项目 F-N 明确为不可计算。不得把本次矩阵“中高”展示色带、PLL 或完全破裂主导场景解释为现场真实风险等级、真实高风险位置、完整 QRA 或正式工程结论。

## 2. 关键证据

| 项目 | 结果 |
|---|---|
| 平台/引擎 | `pipeline-qra-platform 0.9.1` / `qra-engine 0.6.1` |
| 动态输出合同 | v1.2.0 |
| 输入哈希 | `0da428e547120043b9a90a13608272f96573940f302999ee5cd297d7dbae965e` |
| 计算任务 | `RUN-20260826T030630Z-74abb224`、`RUN-20260826T030631Z-0f112700` |
| 数值结果哈希 | 两次均为 `b125e2787bef9c9a0728fe253796577e2cbf095524ca6c67cccc0b829367cd09` |
| 节点状态 | 5 `COMPLETED`、6 `SKIPPED_MISSING_INPUT`、0 `FAILED_ISOLATED` |
| 结果层级 | `EVIDENCE_CONDITIONED_SCREENING_ESTIMATE` |
| PLL 筛查值 | `5.372309046830368E-4` 人/年；`4.9116008839187854E-5` 人/(km·年) |
| 筛查范围 | `5.372309046830368E-5`～`5.372309046830368E-3` 人/年；模型不确定性带，不是统计置信区间 |
| IR / 项目 F-N | 均不可计算；缺空间受体和人口分布，未以 0 代替缺失 |
| 软件差异整改 | 2 个 P1 已关闭并增加回归；开放 P0/P1 软件缺陷为 0 |

## 3. 交付物索引

- [能力报告与动态执行清单](能力报告与动态执行清单.md)
- [风险结果、业务解释与异常排查](风险结果业务解释与异常排查.md)
- [数据补充清单](数据补充清单.md)
- [缺陷整改与回归记录](缺陷整改与回归记录.md)
- [需求、问题、决定与风险状态更新](需求问题与风险状态更新.md)
- [阶段 2 验收记录](阶段2验收记录.md)
- [阶段 2 可重复验收工具](../../../tools/run_stage2_acceptance.py)
- [九江阶段 2 回归测试](../../../tests/integration/test_stage2_jiujiang_trial.py)

包含真实结果的 SQLite、HTML、SVG、CSV、JSON 和 ZIP 保存在本机受控目录 `workspace/runtime/stage2-real-data-trial-final/`，由 `.gitignore` 隔离。主报告入口为 `run-a-artifacts/report_dashboard.html`，结构化总表为 `stage2-acceptance-summary.json`。

## 4. 阶段门结论与后续入口

- G2 的“输入合同、任务可复现、节点状态清晰、只输出筛查层级”已满足，记为 `PASSED_SCREENING_EXECUTION`；
- M2 仍为 `HOLD_FOR_DATA_COMPLETION_AND_BUSINESS_REVIEW`：缺少可比较的权威内部分段、空间人口和业务人员现场一致性复核；
- 正式 G1 仍为 `DEFERRED_NOT_IN_CURRENT_FUNCTION_VALIDATION_SCOPE`，本次试算不改变阶段 1 数据签批状态；
- 下一步优先补人口/受体和权威空间分段，再补生产频率、工况、物性、气象和点火参数；取得新数据时创建新快照和新计算任务，不覆盖本次证据。

重复执行（目标目录必须不存在）：

```powershell
.\.venv\Scripts\python.exe .\tools\run_stage2_acceptance.py
```
