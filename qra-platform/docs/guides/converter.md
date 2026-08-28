# 多来源文件自动转 JSON

转换器提供 `CSV/XLS/XLSX/DOCX/PDF/PNG/JPEG → 统一解析与OCR路由 → 多来源合并 → 人工复核 → case.json → validate_import_contract` 链路。`db_qra.file_intake` 在该链路之前按实际签名、容器结构和 ZIP 安全规则登记或隔离文件。解析层保留原文、表格、图片和位置证据；转换层只映射、归一、关联和报告质量问题，不补模型默认值，也不执行风险计算。DOCX/PDF/OCR 或推测表格属于需复核内容，不能伪装成确定性业务字段。

## 管理页面流程

运行 `python -m db_qra serve` 后，在管理中心的“资料自动转换”页面：

1. 选择版本化映射配置和隔离策略，拖放或多选源文件，也可上传 ZIP 资料包；
2. 浏览器分别展示请求发送进度和服务端任务进度；入口检查展示检测类型、哈希、ZIP 层级、重复/版本关系和隔离原因；
3. 状态为 `BLOCKED` 时，应用符合第二阶段合同的复核决定后重试；
4. 状态为 `READY_FOR_CONFIRMATION` 时核对来源指纹、识别表格、单位换算、缺失项和可运行节点；
5. 填写确认人后创建不可变快照，可选择立即进入现有计算队列。

相同源文件名/内容哈希集合和相同映射配置哈希只创建一个转换任务。快照来源元数据保留转换任务、源文件哈希、转换器版本、映射版本及哈希、JSON 哈希、确认人和 UTC 时间。命令行和网页端均调用 `convert_sources`，黄金测试会比较两端业务 JSON 深度一致性。

平台 API 包括 `GET /admin/api/conversion-profiles`、`GET/POST /admin/api/conversions`、`GET /admin/api/conversions/{id}/sources`、`GET /admin/api/conversions/{id}/events`、`GET /admin/api/conversions/{id}/sources/{source_id}/artifacts`、`POST /admin/api/conversions/{id}/cancel|retry|confirm` 和批量创建接口。创建请求固化合同、入口规则、映射哈希、隔离策略和文件清单哈希。源文件内容保存在受保护二进制字段中，管理页数据库浏览器和元数据接口都不返回该字段；ZIP 路径穿越、盘符/UNC、符号链接、加密、重复路径、嵌套归档、层级/大小/数量/压缩比超限都会在解析队列之前隔离。

## 安装与运行

在项目根目录安装依赖后执行：

```powershell
python -m pip install -e .

python -m qra_converter convert `
  --source-dir ".\客户原始资料" `
  --profile "generic.structured-mvp.v1" `
  --output-dir ".\workspace\outputs\converted" `
  --case-id "CASE-001" `
  --project-name "项目名称"
```

首次运行如存在冲突或低置信度内容，状态为 `BLOCKED`。根据 `conversion_preview.json` 的 `manual_review.items` 制作复核决定文件，再运行：

```powershell
python -m qra_converter convert `
  --source-dir ".\客户原始资料" `
  --profile "generic.multisource-review.v2" `
  --output-dir ".\workspace\outputs\converted-reviewed" `
  --review-decisions ".\review_decisions.json"
```

`--profile` 可以是映射配置文件路径，也可以是 `resources/mappings` 下配置的 `profile_id`。`--case-id` 和 `--project-name` 均为可选；两者都没有提供且配置中也未声明时，转换器使用源目录名作为 `project_name`，并在报告中记录 `METADATA_DERIVED`。

命令返回码：

- `0`：转换层与现有输入合同均无错误，且没有阻断型待复核项，状态为 `READY_FOR_REVIEW`；
- `2`：存在阻断错误、冲突/低置信度待复核项或命令参数/文件错误，状态为 `BLOCKED`。

`READY_FOR_REVIEW` 仍要求人工确认，不代表资料完整、模型发布或正式报告获准。

## 输出文件

- `case.json`：标准 JSON 草稿；即使被阻断也保留，便于定位和修正；
- `conversion_report.json`：错误、警告、未使用列、字段级来源和值换算过程；
- `source_manifest.json`：源文件 SHA-256、读取器、工作表、非空行数、映射配置版本和哈希。
- `conversion_preview.json`：已识别/未识别表、字段来源、缺失项、冲突、单位换算、管段关联、可运行节点和继续补数清单；
- `review_audit.json`：待复核项、已应用决定、复核决定文件哈希及完整修改前后值。

输出不写生成时间。同一源文件、文件名、映射配置和转换器版本重复运行时，`case_sha256` 保持一致。

## 多来源合并规则

第二阶段按映射目标和 `record_key` 合并记录。未配置时，管段使用 `segment_id`，人口目标使用 `target_id`，其他业务记录使用 `record_id`：

- 主键及所有字段相同：输出一条记录，`source_refs` 保留全部来源；
- 主键相同、字段互补：合并为一条记录，各字段仍通过 `field_lineage` 回溯；
- 主键相同、任一字段不同：按最高来源优先级生成建议值，同时创建阻断型 `SOURCE_VALUE_CONFLICT`；
- 空主键、不同主键、越界里程等继续按转换质量规则报错，不用“最近记录”强行合并。

来源优先级配置示例：

```json
{
  "source_priorities": [
    {"file_patterns": ["*主数据*"], "sheet_patterns": [".*"], "priority": 100},
    {"file_patterns": ["*补充*"], "sheet_patterns": [".*"], "priority": 50}
  ]
}
```

优先级不是自动裁决规则；存在不同值时必须复核。

## 人工复核决定

复核决定必须引用稳定 `review_id`，并保留复核人、含时区时间和原因：

```json
{
  "schema_version": "1.0.0",
  "reviewer": "张三",
  "reviewed_at": "2026-08-25T16:00:00+08:00",
  "decisions": [
    {
      "review_id": "REV-...",
      "action": "ACCEPT_PROPOSED",
      "reason": "已与盖章检测报告逐项核对"
    }
  ]
}
```

冲突项支持 `ACCEPT_PROPOSED`、`REPLACE_VALUE`（同时给出 `value`）和 `REJECT_RECORD`；低置信度记录支持 `CONFIRM_RECORD` 和 `REJECT_RECORD`；未进入 JSON 的辅助内容可用 `ACKNOWLEDGE_NOT_IMPORTED` 或 `IGNORE_SOURCE` 确认。过期、重复、无复核人、无原因或无时区的决定会阻断转换。

## 通用映射配置

第一阶段只读配置 `generic.structured-mvp.v1` 支持以下表格：

- 管段台账（必需）；
- 管线基础信息与运行工况；
- CIPS 阴保数据；
- DCVG/ACVG 防腐层缺陷；
- 内检测缺陷；
- 人口及高后果目标；
- 维修工单。

第二阶段配置 `generic.multisource-review.v2` 继承上述规则，并增加阀门与远程切断、泄漏/失效事件、巡线与第三方活动、土壤腐蚀环境和应急资源。客户配置可用 `extends` 继承稳定父版本，再按表格 `id` 覆盖或追加规则。

通用配置只接受它明确声明的表头与单位。客户表头或单位不同，应复制为新的有版本配置，而不是修改已用于历史快照的版本。

九江真实资料验收配置为 `jiangxi-natural-gas.jiujiang.v1`。该配置示范四项面向客户台账的受控能力：

- `header_row_span`：组合连续多行表头，处理 Excel 合并式分组表头；
- `record_filters`：对标准化字段执行精确等值过滤，确定性排除同表其他管线；
- `allow_shared_source`：显式允许同一源表分别映射到管段、管线和多个原始业务类别；未声明时仍按歧义阻断；
- `strip_source_unit_suffix`：仅在源单位和允许后缀均由映射声明时解析 `273mm` 等值；压力区间等非单值表达仍不得转为 number。

阶段 1 可重复验收命令：

```powershell
python .\tools\run_stage1_acceptance.py `
  --output-root ".\workspace\runtime\stage1-real-data-acceptance-<run-id>"
```

工具会盘点九江源资料，执行待复核与已复核命令行转换、重复哈希检查、真实 HTTP 网页上传和确认、不可变快照、去重及倒置里程负向门禁。输出只用于本机内部功能验证。

映射配置的每个表格规则至少声明：

```json
{
  "id": "segments",
  "target": "segments",
  "required": true,
  "header_search_rows": 20,
  "minimum_header_matches": 3,
  "selectors": [
    {
      "file_patterns": ["*管段*"],
      "sheet_patterns": [".*"]
    }
  ],
  "fields": [
    {
      "target": "start_km",
      "aliases": ["起始里程(km)"],
      "type": "number",
      "source_unit": "km",
      "target_unit": "km",
      "required": true
    }
  ]
}
```

支持的字段类型为 `string`、`number`、`integer`、`chainage`、`enum`、`boolean`、`date` 和 `datetime`。`chainage` 可确定性解析 `10+938` 或 `JJ041G（10+938）`。支持 km/m/mm、Pa/kPa/MPa/bar、℃/K、percent/fraction 的显式换算。源单位不明确或值为压力区间等非单值表达时，转换器报告错误，不取均值或写入 0。

## 管段关联规则

业务记录先使用有效 `segment_id`，否则按 `chainage_km` 匹配。里程范围采用起点包含、终点不包含，最后一个管段包含管线终点。越界、管段重叠、里程断点、空主键和重复主键均阻断转换。

## 开发验证

```powershell
.\scripts\test.ps1
```

第一阶段脱敏黄金源资料位于 `tests/fixtures/converter_mvp`。第二阶段黄金案例位于 `tests/fixtures/converter_phase2`，覆盖重复文件内容、重复业务记录、冲突来源、来源优先级、复核审计、能力补数清单、现有输入合同和确认后业务 JSON 深度一致性。
## 第三阶段解析前置层

转换服务现在先调用统一文档解析流水线，再通过兼容适配器把可信结构转换成
`RawTable` 交给现有映射器。解析层不识别“工作压力”等业务字段，也不执行公式、
宏、PDF JavaScript 或嵌入对象。

平台任务的每个源文件会独立保存解析状态、解析器版本、`parse_sha256`、质量摘要和
受控资源。扫描文件未配置 OCR 时返回 `PARSE.OCR_PROVIDER_NOT_CONFIGURED` 并标记
`PARSE_FAILED`，不会以空字符串或占位文本继续映射。详细合同、部署配置和验收命令
见 [文档解析指南](parsing.md)。

## 第四阶段候选事实层

平台转换任务会在第三阶段解析和确定性表格映射之后运行候选事实流水线。结构化映射血缘先转换为第一阶段 `candidate-field` 合同；未被映射覆盖的可信文本块仅在已配置阿里云百炼 `ExtractionProvider` 且当前任务显式勾选外发授权时进入 `qwen3.8-max` 受约束模型提取。未配置 provider 或未授权外发不会生成模拟模型结果。

资料中的命令、链接、角色声明和工具请求始终作为不可信文本。模型没有工具权限；响应必须通过字段、实体、关系、证据、大小、深度和控制字符检查，再由确定性标准化器重新计算标准值。

第五阶段可使用以下只读接口分页读取候选层：

- `GET /admin/api/conversions/{id}/model-calls`
- `GET /admin/api/conversions/{id}/review-summary`
- `GET /admin/api/conversions/{id}/candidates?status=&field_id=&entity=&cursor=`
- `GET /admin/api/conversions/{id}/candidates/{candidate_id}`
- `GET /admin/api/conversions/{id}/issues?severity=&code=&cursor=`
- `GET /admin/api/conversions/{id}/capability`

阶段四框架验收命令：

```powershell
python .\tools\run_stage4_acceptance.py --json
```

需要同时验证真实千问时，先用桌面 CSV 对合成文本执行 `tools/test_bailian_extraction.py`，再把生成的脱敏记录传给 `run_stage4_acceptance.py --real-model-record ... --require-real-model`。真实冒烟记录不包含 API Key、接口地址或合成正文。

管理页面新建资料任务时可分别选择本任务的 OCR 和信息提取模型。选择结果与提供方一并固化到任务，参与去重指纹；任务创建后修改默认模型不会改变该任务。任务详情通过 `model-calls` 接口展示脱敏调用审计和候选数量，不提供输入正文、结构化原始输出或密钥。
