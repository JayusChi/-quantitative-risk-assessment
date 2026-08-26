# Pipeline QRA Platform

天然气输送管道定量风险评估平台，提供源资料自动转换、人工复核、不可变输入快照、动态 QRA 计算、风险结果展示、报告导出和全过程审计。

## 快速启动

项目已经建立 `.venv` 虚拟环境。在 PowerShell 中进入项目目录：

```powershell
cd "D:\风险定量评估\qra-platform"
```

推荐直接使用虚拟环境中的 Python 启动，不依赖 PowerShell 激活状态：

```powershell
.\.venv\Scripts\python.exe -m db_qra serve
```

服务默认使用端口 **8766**。启动成功后访问：

[http://127.0.0.1:8766/admin/](http://127.0.0.1:8766/admin/)

终端窗口需要保持运行。需要停止服务时，在该窗口按 `Ctrl+C`。

也可以先激活虚拟环境，再使用较短的启动命令：

```powershell
.\.venv\Scripts\Activate.ps1
python -m db_qra serve
```

如果 PowerShell 禁止执行 `Activate.ps1`，直接使用前一种 `.\.venv\Scripts\python.exe` 命令即可，不需要修改系统执行策略。

## 首次安装或更新依赖

虚拟环境刚创建、依赖发生变化或拉取新版本代码后，在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

确认依赖没有冲突：

```powershell
.\.venv\Scripts\python.exe -m pip check
```

依赖应安装在 `.venv` 中，避免与系统 Python 中的 Gradio、Pillow 等软件包互相影响。

## 管理页面使用流程

### 源资料自动转换

1. 打开“资料自动转换”。
2. 选择映射配置，上传 CSV、XLS、XLSX、DOCX、PDF 或 ZIP 资料包。
3. 后台任务完成后，查看识别结果、来源哈希、缺失字段、冲突、单位换算和可运行节点。
4. 如果任务状态为“已阻断”，根据待复核项准备复核决定并重试。
5. 状态达到“待确认”后，由复核人确认并生成不可变输入快照。
6. 可以在确认时直接创建计算任务，也可以稍后从“输入数据中心”发起计算。

相同源文件集合和相同映射版本会自动去重。解析失败、人工复核未完成或输入合同预检失败时，不会写入正式快照。

### 直接上传 JSON

原有 JSON 导入功能继续可用：

1. 点击“上传JSON数据”。
2. 选择符合平台输入合同的 JSON 文件。
3. 预检通过后选择“仅保存”或“保存并计算”。

### 计算与报告

- “计算任务”显示排队、执行、完成或失败状态，以及每个动态计算节点的结果。
- “管段风险结果”展示 PLL、个人风险、筛查上下界和管段排序。
- 已完成任务可以打开 HTML 报告或导出 ZIP。
- “操作审计”记录转换、确认、快照、计算和导出事件。

## 常用命令

以下命令均在 `D:\风险定量评估\qra-platform` 目录执行。

启动管理平台，默认监听 `127.0.0.1:8766`：

```powershell
.\.venv\Scripts\python.exe -m db_qra serve
```

临时使用其他端口：

```powershell
.\.venv\Scripts\python.exe -m db_qra serve --port 9000
```

初始化数据库：

```powershell
.\.venv\Scripts\python.exe -m db_qra init
```

列出输入快照和计算任务：

```powershell
.\.venv\Scripts\python.exe -m db_qra snapshots
.\.venv\Scripts\python.exe -m db_qra runs
```

直接导入一份标准 JSON：

```powershell
.\.venv\Scripts\python.exe -m db_qra import-data `
  --input ".\workspace\inputs\虚拟输入_6类最小实用数据_10管段.json" `
  --name "测试输入"
```

按最近输入快照计算：

```powershell
.\.venv\Scripts\python.exe -m db_qra calculate
```

命令行转换源资料：

```powershell
.\.venv\Scripts\python.exe -m qra_converter convert `
  --source-dir ".\tests\fixtures\converter_mvp" `
  --profile "generic.structured-mvp.v1" `
  --output-dir ".\workspace\outputs\converter-mvp" `
  --case-id "CONVERTER-MVP-001"
```

运行完整测试：

```powershell
.\scripts\test.ps1
```

如果没有激活虚拟环境，而测试脚本调用了系统 Python，可以先激活 `.venv`，或者执行：

```powershell
$env:PATH = "$PWD\.venv\Scripts;$env:PATH"
.\scripts\test.ps1
```

## 数据位置

默认运行数据位于项目的 `workspace` 目录：

```text
workspace/
├─ inputs/          示例和待导入输入
├─ outputs/         命令行转换或计算输出
├─ runtime/         转换与计算临时目录
└─ state/
   └─ qra.sqlite3  平台 SQLite 数据库
```

历史快照和已经产生计算记录的数据不能随意覆盖或删除。需要修改业务数据时，应重新转换或导入，形成新的输入快照。

如需把运行数据放到其他磁盘，可在启动前配置：

```powershell
$env:QRA_WORKSPACE_ROOT = "D:\qra-runtime"
.\.venv\Scripts\python.exe -m db_qra serve
```

## 项目结构

```text
qra-platform/
├─ src/
│  ├─ qra_converter/   文件读取、映射、合并、复核和 JSON 组装
│  ├─ db_qra/          转换任务、SQLite、管理页面和计算任务编排
│  └─ qra_engine/      输入校验、动态计算节点、风险模型和报告
├─ resources/
│  ├─ mappings/        版本化数据映射配置
│  └─ templates/       受控填报模板
├─ tests/              单元、集成和黄金案例测试
├─ docs/               架构、开发、计算及转换说明
├─ scripts/            测试和辅助命令
└─ workspace/          本地业务数据和运行产物
```

## 默认端口和访问控制

- 默认管理地址：`http://127.0.0.1:8766/admin/`
- 默认只监听本机地址，不接受其他电脑直接访问。
- 管理 API 在未配置令牌时只允许本机写操作。
- 对外部署可以配置 `QRA_ADMIN_TOKEN`，但正式企业部署仍应增加 HTTPS、反向代理和统一身份权限系统。

设置管理令牌示例：

```powershell
$env:QRA_ADMIN_TOKEN = "请替换为足够长的随机令牌"
.\.venv\Scripts\python.exe -m db_qra serve
```

## 常见问题

### 端口被占用

检查 8766 端口：

```powershell
Get-NetTCPConnection -LocalPort 8766 -ErrorAction SilentlyContinue
```

可以停止原来的服务，或者临时换端口：

```powershell
.\.venv\Scripts\python.exe -m db_qra serve --port 9000
```

### 页面仍然是旧版本

先在原服务窗口按 `Ctrl+C`，重新执行启动命令，然后在浏览器中刷新页面。

### Python 依赖冲突

确认实际使用的是项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
.\.venv\Scripts\python.exe -m pip check
```

输出的 Python 路径应位于 `qra-platform\.venv\Scripts\python.exe`。

## 延伸文档

- [平台架构](qra-platform/docs/architecture.md)
- [开发指南](qra-platform/docs/development.md)
- [文件自动转 JSON](qra-platform/docs/guides/converter.md)
- [数据库与管理页面](qra-platform/docs/guides/database.md)
- [第三阶段实施计划](qra-platform/docs/plans/auto-json.md)
