# 第四阶段：大模型智能提取、标准化与多来源融合

状态：`TEST_EDITION_ACCEPTED`

实施日期：2026-08-28（Asia/Shanghai）

第四阶段测试版已按《第四阶段实施说明》实现，并完成确定性门禁、带标签黄金指标和真实千问合成冒烟。第三阶段 `ParsedDocument`、解析哈希和稳定证据对象保持只读；现有结构化映射继续作为最高优先级路径，模型候选不能覆盖确定性结果。

## 已实现范围

- 显式状态路径：`PARSED → CLASSIFYING → CLASSIFIED → EXTRACTING_ENTITIES → ENTITIES_READY → EXTRACTING_FIELDS → CANDIDATES_READY → NORMALIZING → NORMALIZED → FUSING → FUSION_READY → QUALITY_CHECKING → READY_FOR_REVIEW/BLOCKED`；每步输入/输出哈希、版本、时间和重试数写入 `extraction_run`，相同输入不得固化不同输出。
- 厂商无关 `ExtractionProvider`、阿里云百炼 OpenAI 兼容 `qwen3.8-max` 适配器、有界重试、最大响应限制、一次结构修复、fixture provider 和不含密钥的调用审计。
- OCR 使用 `qwen3.5-ocr`，信息提取使用 `qwen3.8-max`，复用桌面 CSV 中同一个业务空间 API Key；项目内只保存 Windows DPAPI 密文。需要 OCR 的文件按所选 OCR 模型识别；每个转换任务必须另行显式授权正文/OCR 文本进入第四阶段信息提取，未授权时不会调用信息提取模型。
- 新建任务可以分别覆盖默认 OCR/提取模型；提供方和模型版本随任务固化并参与去重。任务详情展示不含正文和密钥的 OCR、分类、实体、字段、关系调用记录，包括状态、请求编号、重试、结构修复和 Token 计数。
- 版本化提示模板和清单哈希；资料块始终作为不可信数据传递，模型没有工具、文件、网络、数据库或工作流控制权。
- 字段、实体、关系和证据白名单；未知字段、伪造证据、无证据候选、未知关系端点、控制字符、过深或过大输出被拒绝。
- 映射血缘到第一阶段候选合同的确定性转换，以及数值、显式单位、绝压边界、桩号、日期精度、坐标系和枚举标准化。
- 显式业务 ID、批准别名、管线加半开桩号区间等身份规则；完全重复、近似重复、互补、冲突和身份歧义分组。来源等级只产生建议，不产生确认。
- 候选层证据、低置信度、基本物理关系、完整性和节点可用性检查；能力计划明确不把默认值计作项目事实。
- 独立持久化 `extracted_entity`、`candidate_field`、`candidate_evidence_link`、`candidate_relationship`、`quality_issue`、`fusion_group` 及成员表。
- 第五阶段只读接口：
  - `GET /admin/api/conversions/{id}/review-summary`
  - `GET /admin/api/conversions/{id}/model-calls`
  - `GET /admin/api/conversions/{id}/candidates`
  - `GET /admin/api/conversions/{id}/candidates/{candidate_id}`
  - `GET /admin/api/conversions/{id}/issues`
  - `GET /admin/api/conversions/{id}/capability`

## 验收

执行命令：

```powershell
$env:PYTHONPATH='src'
python tools/test_bailian_extraction.py `
  --config-csv "<桌面CSV路径>" `
  --record docs/project/stage4/real-qwen-smoke.json `
  --json
python tools/run_stage4_acceptance.py `
  --real-model-record docs/project/stage4/real-qwen-smoke.json `
  --require-real-model `
  --record docs/project/stage4/stage4-acceptance.json `
  --json
```

结果：21 项阶段四专项测试全部通过，项目全量回归共执行 204 项（203 项通过、1 项按运行条件跳过）。22 个黄金场景覆盖结构化表格、原生文档、扫描件、复杂表格和提供方失败；7 个带标签候选的 fixture 精确率、召回率和证据绑定率均为 `1.0`。真实 `qwen3.8-max` 合成冒烟完成分类、实体、字段和关系四次调用，生成有证据字段候选且无调用失败。机器可读记录见 `stage4-acceptance.json` 和 `real-qwen-smoke.json`。

黄金指标仍使用确定性 fixture provider，避免 CI 受实时模型漂移影响；真实千问只用不含客户数据的合成文本验证接入和合同。真实客户资料的字段准确率校准、资料外发审批和生产批准仍需按真实数据验收执行，不能由本次合成冒烟替代。未配置 provider 或未授权外发时，平台保留确定性映射候选，对未覆盖资料降级为待复核而不生成伪候选。

## 向第五阶段移交

移交对象为候选、实体、关系、证据、融合组、质量问题、能力计划和全部模型/提示词/规则/合同版本。本阶段没有候选确认写接口；建议候选不等于人工确认，候选集合哈希变化会使旧决定失效。
