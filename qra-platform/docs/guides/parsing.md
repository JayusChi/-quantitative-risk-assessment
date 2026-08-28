# 文档解析、OCR 与版面识别

第三阶段解析层只保存“源文件中有什么、在哪里”。字段别名、业务实体、多来源冲突和 QRA 字段值仍由后续映射与复核层处理。

## 统一入口

`qra_converter.parsing.pipeline.ParsingPipeline` 按检测媒体类型选择唯一主读取器，支持：

- `text/csv`；
- `application/vnd.ms-excel`；
- `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`；
- `application/vnd.openxmlformats-officedocument.wordprocessingml.document`；
- `application/pdf`；
- `image/png` 和 `image/jpeg`。

平台任务必须传入第二阶段登记的 `detected_media_type`。直接转换命令没有入口登记信息时，才按受支持后缀推导媒体类型。`readers/tabular.py`、`documents.py` 和 `pdf.py` 是兼容导入路径，实际行为仍由同一套新读取器实现。

## 解析合同与证据

`ParsedDocument` 合同版本为 `qra.parsed-document/1.0.0`。文本块保留原文与单独的 `normalized_text`；单元格分别保存原值、显示文本、公式、缓存值和解析日期；合并单元格只保存主单元格与跨度。

坐标统一使用左上角原点。PDF 使用页面点坐标，图片使用原图像素坐标，表格文件使用 0 基网格坐标。DOCX 没有确定性渲染时 `page_count=0`，段落、表格和图片使用 OOXML 部件与结构序号，不生成伪页码。

规范化解析 JSON 不含运行耗时。`parse_sha256` 绑定源内容、解析合同、读取器版本、OCR
提供方/模型、OCR 参数和预处理版本。`metadata.parsing_provenance` 与
`preview_manifest.json.parsing_provenance` 统一保存这些版本、参数和缓存键。外部 OCR 的
原始响应哈希与提供方调用 ID 会保留在解析元数据中。

## PDF 和 OCR 路由

PDF 每页按原生字符数、有效字符比例和图像覆盖率分类为：

- `TEXT_NATIVE`：使用原生文本和原生表格；
- `MIXED`：保留原生显示层，只对图片区域调用 OCR；
- `SCAN`：对可安全解码的扫描图像调用 OCR；
- `UNREADABLE`：记录结构化失败，不生成猜测文本。

原生文本与 OCR 坐标重叠时，显示层保留原生文本，OCR 结果进入 `extraction_candidates`。OCR 未返回表格网格时，只有满足多行、多列和列中心一致规则的文本块才形成推测表格，置信度上限为 `0.6`，并产生 `PARSE.TABLE_STRUCTURE_UNCERTAIN`。

## OCR 部署配置

默认 `DisabledOcrProvider` 会返回 `PARSE.OCR_PROVIDER_NOT_CONFIGURED`，不会返回占位文本。部署提供方使用 HTTPS JSON 端口，密钥只从环境读取：

阿里云百炼工作空间可直接使用项目的一键启动脚本；完整操作见
[阿里云百炼 OCR 接入与使用](./bailian-ocr.md)。百炼适配器调用 `qwen3.5-ocr`
高精识别并消费 `words_info` 位置结果。供应商未返回逐行置信度时平台以 `0.8`
保存并明确产生复核警告；没有逐行坐标时只按整页低置信度证据保留。

原有供应商无关 JSON 端口仍可使用：

```powershell
$env:QRA_OCR_ENDPOINT = "https://ocr.example.internal/v1/recognize"
$env:QRA_OCR_API_KEY = "由密钥管理系统注入"
$env:QRA_OCR_MODEL_VERSION = "provider-model-version"
$env:QRA_OCR_MAX_RETRIES = "2"
$env:QRA_OCR_TIMEOUT_SECONDS = "120"
```

请求只包含受控预处理图像字节，不传本地路径。超时、网络连接和服务端临时错误最多指数
退避重试两次；连接拒绝不会再误报为超时。认证、不可读和响应合同错误不伪装成成功。

## 解析产物与数据库

文件产物结构为：

```text
parsed/<source_id>/
├─ parsed_document.json
├─ quality_report.json
├─ preview_manifest.json
└─ previews/...
```

平台将这些资源写入 `conversion_parse_artifact`，大图不会嵌入 `conversion_job` JSON。`conversion_source` 独立记录 `PARSED/PARSE_FAILED`、解析器版本、解析哈希、质量摘要和完成时间。解析成功不会删除或改写受保护源文件。

## 验收

确定性解析与数据库链路：

```powershell
python .\tools\run_stage3_acceptance.py --json
```

正式扫描件验收会从管理页面保存的 Windows DPAPI 加密配置加载 OCR，并对九江真实图片和
扫描 PDF 运行。验收记录只保存输入哈希、版本、请求 ID、响应哈希、坐标计数和问题码，
不保存密钥或识别正文：

```powershell
python .\tools\run_stage3_acceptance.py `
  --require-real-ocr `
  --json `
  --record .\docs\project\stage3\real-ocr-acceptance.json
```

全量回归仍使用 `scripts/test.ps1`。
