# Roadmap Stage 3 真实资料黄金集合同

本目录只保存 Schema 和流程说明，不保存客户原文件、OCR 正文或真实标注值。真实脱敏资料、`manifest.jsonl`、`annotations.jsonl` 与运行结果应放在本机 `workspace/golden-stage3/`；该目录默认被 Git 忽略。

每份资料使用匿名 `document_id` 和文件 SHA-256 关联。业务标注必须由 QRA 人员复核并标为 `APPROVED`；合成夹具必须设置 `is_real_business_document=false`，不能计入 20～50 份真实资料门槛。

建议本机布局：

```text
workspace/golden-stage3/
├─ manifest.jsonl
├─ annotations.jsonl
├─ results.jsonl
└─ files/                 # 受控脱敏原文件，不入 Git
```

评估命令：

```powershell
.\.venv\Scripts\python.exe .\tools\evaluate_stage3_robustness.py `
  --manifest .\workspace\golden-stage3\manifest.jsonl `
  --annotations .\workspace\golden-stage3\annotations.jsonl `
  --results .\workspace\golden-stage3\results.jsonl `
  --require-min-documents 20 --json
```

输出只包含哈希、计数、聚合指标和问题码，不包含资料名、原文、标注值或候选值。默认工程门槛来自路线图建议，最终业务门槛仍需 QRA 业务负责人和专家批准。
