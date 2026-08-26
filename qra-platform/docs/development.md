# 开发指南

## 环境

- Python 3.10 或更高版本；
- 计算引擎只使用标准库；转换器读取 XLSX/XLS 分别使用 `openpyxl` 和 `xlrd`，PDF 辅助提取使用 `pdfplumber`/`pypdf`，DOCX 原生表格使用 OOXML 忠实读取；
- 源码采用 `src` 布局，开发时可以安装为可编辑包，或在 PowerShell 中设置 `PYTHONPATH`。

不安装包直接运行：

```powershell
$env:PYTHONPATH = "$PWD\src"
python -m qra_engine catalog
python -m db_qra --help
python -m qra_converter --help
```

## 一键验证

```powershell
.\scripts\test.ps1
```

该脚本依次执行：

1. 包依赖方向和 `sys.path` 拼接检查；
2. 计算引擎单元与黄金回归；
3. 转换层合同测试；
4. 数据库/API 端到端测试。

## 增加文件读取器

1. 在 `src/qra_converter/readers` 新建读取器；
2. 实现 `SourceReader`，只返回源忠实记录，不做客户字段推断；
3. 在 `tests/unit/converter` 增加格式、空值、日期和异常文件测试；
4. 脱敏源文件放入 `tests/fixtures`，不得引用 `workspace` 中的客户资料作为黄金样本；
5. 在版本化映射配置中处理表头和单位，不在读取器里写客户特例。

DOCX/PDF/OCR 或文本启发式读取必须设置 `requires_review=True` 和置信度，不能伪装成结构化自动映射。新增合并规则时同时覆盖同值重复、互补字段、冲突来源、优先级和过期复核决定测试。

## 增加计算能力

计算公式或动态节点的变更必须同时更新模型登记、公式状态文档和对应测试。数值行为变更应显式升级引擎或模型版本，不能借重构改变既有结果。

## 运行数据位置

默认使用项目内 `workspace`。部署或需要把数据放到其他磁盘时：

```powershell
$env:QRA_WORKSPACE_ROOT = "D:\qra-runtime"
python -m db_qra serve
```

源码不应再自行推导“相邻计算引擎目录”或插入 `sys.path`。
