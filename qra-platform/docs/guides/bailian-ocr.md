# 阿里云百炼 OCR 与千问信息提取接入

本项目已经接入工作空间专属的 DashScope 接口。管理页面可从业务空间已开通的四个模型中选择：
`qwen3.5-ocr`、`qwen3.8-max`、`qwen3.7-max` 和 `qwen3.7-max-2026-06-08`。
`qwen3.5-ocr` 使用高精识别任务并保存逐行文字和坐标，是文档 OCR 的推荐默认值；三个 Max
模型使用受控的多模态抄录提示词，主要用于对比复杂页面识别效果，通常只保留整页文本证据。
所有模型都会保存供应商请求编号和原始响应哈希。

第四阶段信息提取通过同一业务空间的 OpenAI 兼容地址调用 `qwen3.8-max`，用于资料分类、实体、字段和关系候选。OCR 与信息提取复用同一个 API Key，但分别保存模型版本、调用哈希和状态。

API Key 不得复制到源码、配置样例、日志或 Git。管理页面首次导入领导提供的 CSV 后，平台使用
Windows 当前用户 DPAPI 加密密钥，并保存到 `workspace/state/ocr-settings.json`；该文件已被 Git
忽略。页面和状态接口只返回服务商、模型与脱敏状态，不返回 API Key。

## 第一次：在管理页面导入配置

打开 PowerShell：

```powershell
cd D:\风险定量评估\qra-platform
.\.venv\Scripts\python.exe -m db_qra serve
```

然后只需配置一次：

1. 打开 `http://127.0.0.1:8766/admin/`；
2. 进入“资料自动转换”，单击“模型设置”；
3. 选择领导提供的业务空间 API Key CSV；
4. “用于OCR的模型”保留 `qwen3.5-ocr`，“用于QRA信息提取的模型”选择 `qwen3.8-max`；
5. 单击“保存设置并立即启用”；
6. 页面同时显示 OCR 和信息提取模型已启用即配置完成。

同一台电脑、同一个 Windows 用户下，后续启动会自动解密并加载。更换 Windows 用户、迁移电脑或
管理员轮换 API Key 后，需要在该页面重新导入 CSV。

已经保存过 CSV 后，切换默认模型只需再次打开“模型设置”，选择模型并保存，不需要重新选择 CSV。
模型切换会立即写入本机加密配置，作为之后新任务的默认选择。

## 日常：启动平台并上传图片

以后不再需要 CSV，也不再需要 `start-bailian.ps1`，只运行原来的命令：

```powershell
cd D:\风险定量评估\qra-platform
.\.venv\Scripts\python.exe -m db_qra serve
```

保持这个 PowerShell 窗口运行，然后：

1. 打开 `http://127.0.0.1:8766/admin/`；
2. 进入“资料自动转换”，新建资料任务；
3. 选择映射配置、OCR 模型和信息提取模型，填写项目名称并选择资料；如允许本次资料发送到业务空间进行智能提取，显式勾选外发授权；
4. 等待服务端进度结束，打开任务“详情”；
5. 在“解析与 OCR 产物”中单击“查看解析/OCR JSON”，检查 `pages[].text_blocks`
   和 `tables`。

任务创建时会固化当时选择的两个模型，之后修改全局默认值不会改变历史任务。在任务列表的
“映射/模型”列可直接查看选择结果；打开“详情”后，“模型调用记录”会显示 OCR、资料分类、
实体提取、字段提取和关系提取的模型、状态、供应商请求编号、重试/结构修复次数和可用 Token
计数。该视图不返回 API Key、接口地址、资料正文、提示词正文或模型原始响应。

单张文档图片只完成 OCR 时，任务最终可能显示 `BLOCKED`：这通常表示识别出的通用文档还没有
对应到 QRA 业务字段或需要人工复核，并不代表 OCR 失败。以源文件状态 `已解析`、OCR JSON 中
存在 `text_blocks`、且 `metadata.ocr.provider_id` 为 `aliyun-bailian-dashscope` 作为 OCR 成功依据。

未勾选外发授权时，平台仍可完成 OCR、解析和确定性映射，但不会把 OCR/正文块发送给 `qwen3.8-max`。外发授权只对当前任务有效，模型仍无工具、文件系统、数据库或计算提交权限。

按 `Ctrl+C` 停止服务。不要把 CSV 移入项目目录，也不要把 API Key 粘贴到聊天、截图或提交记录。

## 第三阶段正式验收

管理页面保存配置后，使用正式验收入口。它会识别九江真实图片和扫描 PDF，并生成不含密钥、
端点或识别正文的审计记录：

```powershell
python .\tools\run_stage3_acceptance.py `
  --require-real-ocr `
  --json `
  --record .\docs\project\stage3\real-ocr-acceptance.json
```

临时连通性工具 `tools/test_bailian_ocr.py` 默认也只输出识别文本哈希和计数。只有人工诊断时
显式使用 `--show-text` 才会在终端显示最多 500 字识别内容，不得将该输出提交到 Git。

## 可选：用旧脚本做临时连通性测试

`scripts/start-bailian.ps1` 仍保留给部署人员做临时诊断。它不会替代页面中的持久化配置，日常启动
不需要运行它。若管理员要求验证新 Key，可使用 `-TestOnly`，验证后再到页面重新导入该 CSV。

## 手动环境变量（仅部署人员）

一键脚本等价于在服务进程中配置：

```powershell
$env:QRA_OCR_PROVIDER = "aliyun-bailian"
$env:QRA_ALIYUN_DASHSCOPE_URL = "https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/api/v1"
$env:QRA_ALIYUN_API_KEY = "由密钥管理系统注入"
$env:QRA_OCR_MODEL_VERSION = "qwen3.5-ocr"
$env:QRA_OCR_TIMEOUT_SECONDS = "120"
$env:QRA_OCR_MAX_RETRIES = "2"
$env:QRA_EXTRACTION_PROVIDER = "aliyun-bailian"
$env:QRA_ALIYUN_OPENAI_BASE_URL = "https://<WorkspaceId>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
$env:QRA_EXTRACTION_MODEL_VERSION = "qwen3.8-max"
$env:QRA_EXTRACTION_TIMEOUT_SECONDS = "120"
$env:QRA_EXTRACTION_MAX_RETRIES = "2"
$env:QRA_EXTRACTION_MAX_CONCURRENCY = "2"
```

通用 `QRA_OCR_ENDPOINT` 配置仍保留，用于兼容平台原有的供应商无关 JSON OCR 合同。
