# Roadmap Stage 3：OCR 与大模型真实资料鲁棒性加固

本目录记录外层路线图的第三阶段加固工作。它与历史 `docs/project/stage3/` 的“文档解析、OCR 与版面识别”阶段相互独立；历史阶段的 `CLOSED/PASS` 记录不在本次范围内，也未被改写。

2026-09-02 已使用当前可用真实资料启动第一轮本地内部基线，结果见
[`现有资料首轮基线-20260902.md`](./现有资料首轮基线-20260902.md)。该记录明确区分
AI 草案、正式业务批准和无冲突样本时的不可评估状态。

## 工程结论

P0 的根因已消除：平台不再把允许上传的原文件直接等同于一次模型请求。18 MiB 原始文件入口与单次外发请求预算分层配置，图片和扫描 PDF 在本地完成逐页解码、缩放、编码选择和确定性切片；最终发送前，以供应商适配器实际使用的同一 JSON 序列化函数复核 data URI 和 HTTP body 字节数。

工程链路包括：

- 供应商无关的 OCR 请求策略与安全环境变量；
- PNG/JPEG 编码阶梯、最大像素缩放、长图/超限图切片；
- `tile → processed image → original image → PDF page` 坐标链和重叠区合并；
- PDF 逐页处理、正文与表格逻辑任务、表格推断回退；
- 413/明确体积错误、限流、超时、截断和单切片失败的有界降级；
- 信息提取最终请求体预算、长证据稳定拆分、字段/块批次递归二分；
- `model_call_audit` 尝试级持久化审计和历史查询兼容；
- 文件、页面、字段重提取后台执行、结果版本化与旧复核会话失效；
- 真实资料黄金集 Schema、脱敏评估工具和确定性验收入口。

部分 OCR 成功或 OCR 成功而字段提取失败时，解析文档、质量报告、预览资源和模型审计先被固化，成功内容仍可进入复核。无证据候选、空白转零、未知单位/日期/坐标系猜测仍由既有合同和评估门禁拒绝。

## 边界和未完成业务依赖

仓库内不提交真实资料或真实标注。2026-08-31 已在 Git 忽略的 `workspace/golden-stage3/` 中用 20 份获授权标准资料建立并运行草案：14 份扫描页、6 份原生页，25 次真实模型调用、0 失败。标准编号草案的证据绑定率、精确率、召回率均为 100%，但标签状态仍为 `DRAFT`，不能在 QRA 业务负责人批准前宣称业务通过。仓库内 2 份合成 JSONL 仍只验证评估器合同。

真实百炼 OCR 冒烟需要管理员显式提供外发授权、受控密钥配置和脱敏输入文件。默认工程验收不发起网络请求，也不会以模拟结果替代真实冒烟。2026-08-31 的受控在线冒烟已经 PASS；脱敏记录见本目录的 `real-ocr-acceptance.json`。

## 验收入口

无密钥的确定性 P0 验收：

```powershell
.\.venv\Scripts\python.exe .\tools\run_roadmap_stage3_acceptance.py --json
```

显式授权后的真实百炼冒烟：

```powershell
.\.venv\Scripts\python.exe .\tools\run_roadmap_stage3_acceptance.py `
  --require-real-ocr `
  --real-input <脱敏文件路径> `
  --json `
  --record .\docs\project\roadmap-stage3\real-ocr-acceptance.json
```

真实黄金集评估：

```powershell
.\.venv\Scripts\python.exe .\tools\evaluate_stage3_robustness.py `
  --manifest .\workspace\golden-stage3\manifest.jsonl `
  --annotations .\workspace\golden-stage3\annotations.jsonl `
  --results .\workspace\golden-stage3\results.jsonl `
  --require-min-documents 20 --json
```

从已获授权的标准文档生成并运行 20 份本地草案（源文件名和路径不会写入结果）：

```powershell
python .\tools\run_stage3_standard_golden.py `
  --source-root <已获授权资料目录> `
  --config-csv <本机百炼业务空间CSV> `
  --output-root .\workspace\golden-stage3 `
  --limit 20 `
  --authorized `
  --json
```

该命令只生成 `DRAFT` 标签；不得在没有业务负责人明确批准的情况下机械改成 `APPROVED`。

## 需要业务批准的门槛

当前默认值只是路线图建议：证据绑定率 100%、精确率不低于 95%、召回率不低于 90%、冲突识别率 100%，且无证据候选、空白转零、未经证据猜测和提示注入改变工作流均为 0。QRA 业务负责人和专家仍需批准这些阈值、关键字段范围、不同资料类别的抽样比例、冲突判定口径以及允许进入正式计算的部分成功边界。
