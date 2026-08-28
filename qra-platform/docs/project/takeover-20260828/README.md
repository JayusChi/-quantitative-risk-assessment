# 第 0 阶段接管基线记录（2026-08-28）

## 结论

本记录冻结接管前的可运行状态。原工作区以 `main` 的
`4c0362129dcf84a2fc150e02e0a651ccb35c3a86` 为父提交，当前产品代码、合同、映射、
测试、工具和项目文档纳入新的接管基线；数据库、OCR 配置、客户输入及运行产物只做
本地备份，不进入 Git。

计划使用以下可回退引用：

- 分支：`codex/stage0-takeover-20260828`
- 标签：`stage0-takeover-baseline-v0.9.1`

引用创建后，可用 `git show stage0-takeover-baseline-v0.9.1` 查看基线，用
`git switch --detach stage0-takeover-baseline-v0.9.1` 在分离工作区复现代码。

## 1. 本地备份

备份目录（受 `.gitignore` 保护）：

```text
qra-platform/workspace/runtime/takeover-backup-20260828-164703/
```

备份创建于 2026-08-28 16:47（Asia/Shanghai），共约 279 MB。目录内包含：

| 内容 | 文件 | 核验结果 |
|---|---|---|
| 主数据库及阶段验收数据库 | `database/`，5 个 SQLite | 5 个均为 `PRAGMA integrity_check = ok` |
| OCR 配置 | `config/ocr-settings.json` | 原件备份；仍处于 Git 忽略区 |
| 客户输入 | `artifacts/inputs.zip` | 134 个 ZIP 条目，CRC 通过 |
| 正式输出 | `artifacts/outputs.zip` | 404 个 ZIP 条目，CRC 通过 |
| 工作区运行产物 | `artifacts/runtime.zip` | 397 个 ZIP 条目，CRC 通过；排除备份目录自身 |
| 根临时验收产物 | `artifacts/root-tmp.zip` | 8,667 个 ZIP 条目，CRC 通过 |
| 旧九江复核临时目录 | `artifacts/root-.tmp_jiujiang_review.zip` | 72 个 ZIP 条目，CRC 通过 |

校验文件：

- `backup-manifest.csv`：备份文件的大小和 SHA-256；
- `protected-source-checksums.csv`：5 个源 SQLite 和 OCR 配置的源文件 SHA-256；
- `metadata.json`：备份时间、父提交、数量和总字节数；
- `source-manifest.csv`：PowerShell 可枚举源文件的补充清单。根 `tmp` 含超长路径，
  因此该补充清单不作为根 `tmp` 完整性依据，完整性以 ZIP 中的 8,667 条目录和 CRC
  校验为准。

主库一致性副本为：

```text
database/state/qra.sqlite3
```

它包含 24 张业务表、合计 702 行。备份发生时默认端口 8766 未监听，之后测试只使用
测试临时数据库，没有把测试数据写入主库。

## 2. 代码和依赖状态

接管盘点时原工作区包含 36 个已跟踪修改文件和 129 个未跟踪产品文件。完整路径见
[`git-status-before-baseline.txt`](git-status-before-baseline.txt)，已跟踪差异规模见
[`git-diff-stat.txt`](git-diff-stat.txt)。新基线提交本身是所有非忽略文件的完整快照，
因此不另存二进制 patch。

运行环境：

- Windows NT 10.0.26200.0；
- PowerShell 7.6.4；
- Python 3.10.11；
- Git 2.51.0.windows.1；
- 包版本 `pipeline-qra-platform==0.9.1`；
- `pip check`：`No broken requirements found.`

完整环境记录见 [`environment.txt`](environment.txt)，精确安装版本见
[`dependencies.txt`](dependencies.txt)。`pyproject.toml` 中的直接运行依赖为：

- `jsonschema>=4.23,<5`；
- `openpyxl>=3.1,<4`；
- `Pillow>=11,<13`；
- `pdfplumber>=0.11,<1`；
- `pypdf>=5,<7`；
- `xlrd>=2.0,<3`。

## 3. 测试冻结

执行命令等价于 `scripts/test.ps1`，但显式使用项目 `.venv`：

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv\Scripts\python.exe .\tools\check_architecture.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
```

结果：

- 架构检查通过；
- 共运行 204 项测试，203 项通过，1 项按设计跳过；
- 跳过项为需要真实 OCR 提供方配置的 smoke test；
- 测试总状态 `OK (skipped=1)`，耗时 41.512 秒；
- 开始时间 2026-08-28 16:52:22 +08:00；
- 结束时间 2026-08-28 16:53:05 +08:00。

原始输出见 [`architecture-check.txt`](architecture-check.txt) 和
[`test-results.txt`](test-results.txt)。

## 4. 页面清单

管理端是 `/admin/` 下的单页应用，包含 7 个主页面：

| 页面 | 前端视图 | 用途 |
|---|---|---|
| 总览驾驶舱 | `overview` | 输入、转换、计算和风险概览 |
| 资料自动转换 | `conversions` | 上传源资料、OCR/解析、复核、确认入库 |
| 输入数据中心 | `snapshots` | 不可变输入快照、JSON 下载、发起计算 |
| 计算任务中心 | `runs` | 任务状态、动态节点、重试、报告和导出 |
| 管段风险结果 | `risk` | PLL、个人风险、上下界和管段排序 |
| 数据库只读视图 | `database` | 表概览和受控行浏览 |
| 操作审计 | `audit` | 输入、转换、计算、导出等审计事件 |

另有报告资源页面 `/runs/{run_id}/`，默认加载数据库中的
`report_dashboard.html`，其余报告资源使用 `/runs/{run_id}/{artifact_path}`。

## 5. HTTP 接口清单

管理接口遵守本机访问或 `QRA_ADMIN_TOKEN` 访问策略。

### GET

| 路径 | 用途 |
|---|---|
| `/`、`/admin` | 重定向到 `/admin/` |
| `/admin/` | 管理端单页应用 |
| `/health` | 服务、数据库、引擎和模型配置状态 |
| `/admin/api/overview` | 管理总览 |
| `/admin/api/ocr-settings` | 脱敏后的 OCR/提取配置状态 |
| `/admin/api/conversion-profiles` | 映射配置列表 |
| `/admin/api/conversions` | 转换任务列表；支持 `limit/status/cursor` |
| `/admin/api/conversions/{job_id}` | 转换任务详情和事件 |
| `/admin/api/conversions/{job_id}/sources` | 转换源文件列表 |
| `/admin/api/conversions/{job_id}/events` | 转换审计事件 |
| `/admin/api/conversions/{job_id}/model-calls` | OCR/提取模型调用记录 |
| `/admin/api/conversions/{job_id}/review-summary` | 复核汇总 |
| `/admin/api/conversions/{job_id}/candidates` | 候选字段列表和筛选 |
| `/admin/api/conversions/{job_id}/candidates/{candidate_id}` | 候选字段详情 |
| `/admin/api/conversions/{job_id}/issues` | 质量问题列表和筛选 |
| `/admin/api/conversions/{job_id}/capability` | 转换后计算能力状态 |
| `/admin/api/conversions/{job_id}/sources/{source_id}/artifacts` | 解析产物列表或按 `path` 读取产物 |
| `/admin/api/snapshots` | 输入快照列表 |
| `/admin/api/snapshots/{snapshot_id}` | 输入快照文档 |
| `/admin/api/snapshots/{snapshot_id}/input` | 下载快照 JSON |
| `/admin/api/runs` | 计算任务列表 |
| `/admin/api/runs/{run_id}` | 计算任务和节点详情 |
| `/admin/api/runs/{run_id}/segments` | 管段结果 |
| `/admin/api/runs/{run_id}/artifacts` | 计算产物列表 |
| `/admin/api/runs/{run_id}/export` | 导出计算结果 ZIP |
| `/admin/api/database` | 数据表概览 |
| `/admin/api/database/{table}` | 受控只读浏览数据表 |
| `/admin/api/audit` | 审计事件列表 |
| `/api/runs`、`/api/snapshots` | 报告端兼容列表接口 |
| `/api/runs/{run_id}` | 报告端任务详情 |
| `/api/runs/{run_id}/segments` | 报告端管段结果 |
| `/api/runs/{run_id}/artifacts` | 报告端产物列表 |
| `/runs/{run_id}/`、`/runs/{run_id}/{artifact_path}` | 数据库报告页面和静态资源 |

### POST

| 路径 | 用途 |
|---|---|
| `/admin/api/ocr-settings` | 导入或更新加密 OCR/提取配置 |
| `/admin/api/conversions` | 创建或命中去重后的转换任务 |
| `/admin/api/conversions/batch` | 批量创建 1 至 20 个转换任务 |
| `/admin/api/conversions/{job_id}/cancel` | 请求取消转换 |
| `/admin/api/conversions/{job_id}/retry` | 携带可选复核决定重试 |
| `/admin/api/conversions/{job_id}/confirm` | 确认并创建不可变快照，可选立即计算 |
| `/admin/api/snapshots/preview` | 预检上传 JSON |
| `/admin/api/snapshots/import` | 导入上传 JSON 为快照 |
| `/admin/api/runs` | 创建计算任务 |

### DELETE

| 路径 | 用途 |
|---|---|
| `/admin/api/conversions/{job_id}` | 删除允许删除的终态转换任务及关联记录 |
| `/admin/api/snapshots/{snapshot_id}` | 删除没有冲突依赖的输入快照 |

## 6. 数据库表清单

以下行数来自主库一致性副本：

| 表 | 行数 | 用途 |
|---|---:|---|
| `db_schema` | 7 | 数据库结构版本和迁移记录 |
| `input_snapshot` | 2 | 不可变输入快照主表 |
| `input_segment` | 20 | 快照中的管段 |
| `input_indicator_observation` | 100 | 工程指标观测值 |
| `input_population_receptor` | 20 | 人口/受体输入 |
| `input_raw_record` | 144 | 原始输入记录 |
| `conversion_job` | 2 | 源资料转换任务 |
| `conversion_source` | 2 | 转换源文件、ZIP 成员和安全状态 |
| `conversion_parse_artifact` | 8 | 文档解析/OCR 产物 |
| `extraction_run` | 7 | OCR/信息提取调用与执行记录 |
| `extracted_entity` | 0 | 文档提取实体 |
| `candidate_field` | 0 | 待复核候选字段 |
| `candidate_evidence_link` | 0 | 候选字段与证据位置关联 |
| `candidate_relationship` | 0 | 候选实体/字段关系 |
| `quality_issue` | 1 | 入口、提取、融合和合同质量问题 |
| `fusion_group` | 0 | 同义、重复或冲突候选融合组 |
| `fusion_group_member` | 0 | 融合组成员 |
| `input_snapshot_provenance` | 0 | 快照字段到来源证据的追溯关系 |
| `calculation_run` | 7 | 计算任务主表 |
| `calculation_node` | 77 | 动态计算节点状态 |
| `calculation_result_document` | 39 | 结构化计算结果文档 |
| `calculation_segment_result` | 70 | 管段风险结果与排序 |
| `calculation_artifact` | 155 | 报告、图表和导出资源 |
| `audit_event` | 41 | 全过程审计事件 |

## 7. 启动和验证命令

在 `D:\风险定量评估\qra-platform` 中执行。

安装或同步依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pip check
```

启动管理平台：

```powershell
.\.venv\Scripts\python.exe -m db_qra serve
.\.venv\Scripts\python.exe -m db_qra serve --port 9000
```

默认地址为 `http://127.0.0.1:8766/admin/`。初始化和查看状态：

```powershell
.\.venv\Scripts\python.exe -m db_qra init
.\.venv\Scripts\python.exe -m db_qra snapshots
.\.venv\Scripts\python.exe -m db_qra runs
```

转换和计算入口：

```powershell
.\.venv\Scripts\python.exe -m qra_converter convert --help
.\.venv\Scripts\python.exe -m qra_engine validate --help
.\.venv\Scripts\python.exe -m qra_engine run --help
.\.venv\Scripts\python.exe -m qra_engine dynamic --help
```

完整验证：

```powershell
.\scripts\test.ps1
```

可通过 `QRA_WORKSPACE_ROOT` 更换运行数据根目录，通过 `QRA_ADMIN_TOKEN` 为非本机管理
请求设置令牌。

## 8. 文件有效性分类

### 纳入 Git 的有效修改

以下目录中的原有修改和新增文件属于可解释、可测试的产品资产，纳入接管基线：

- `qra-platform/src/db_qra/`：数据库、文件入口、OCR 设置、服务和管理页面；
- `qra-platform/src/qra_converter/`：合同目录、解析、提取、归一化、融合、质量、编排、
  映射和转换服务；
- `qra-platform/src/qra_engine/`：失效频率、动态节点、风险计算和报告；
- `qra-platform/resources/contracts/`、`resources/extraction/`、`resources/mappings/`：
  版本化合同、提取提示/Schema 和客户映射；
- `qra-platform/tests/fixtures/`、`tests/unit/`、`tests/integration/`：脱敏夹具和回归测试；
- `qra-platform/tools/`：合同生成、阶段验收、真实 OCR/提取验证和演示工具；
- `qra-platform/docs/`、项目根目录的阶段说明和计划文档：架构、使用、验收和阶段记录；
- `docs/project/stage4/real-qwen-smoke.json`、`stage4-acceptance.json`：不含 API 密钥的
  正式验收记录，而非本地缓存；
- 本目录 `takeover-20260828/`：本次接管生成的环境、状态和测试证据。

上述判断由目录职责、代码引用关系和本次 204 项测试结果共同支持。基线只证明“当前可
运行和可回退”，不等价于声明所有业务算法已达到正式工程发布条件。

### 有效但不进入 Git 的本地受保护数据

以下内容不是无用临时文件，必须保留本地备份，但由于包含状态、客户资料、模型配置或
大体积产物而继续由 `.gitignore` 隔离：

- `qra-platform/workspace/inputs/`；
- `qra-platform/workspace/outputs/`；
- `qra-platform/workspace/state/qra.sqlite3`；
- `qra-platform/workspace/state/ocr-settings.json`；
- `qra-platform/workspace/runtime/`，包括本次接管备份。

### 可重建或临时文件

以下内容不进入 Git；本阶段未删除：

- `.venv/`、`*.egg-info/`：可由依赖声明重建；
- `.ruff_cache/`、`.pytest_cache/`、`__pycache__/`、`*.pyc`、`.coverage`：工具缓存；
- 根目录 `.tmp_jiujiang_review/`、`tmp/`：渲染、解析、OCR 和验收临时产物，已归档；
- `*.log`：服务和工具日志；
- `标准文档/`、`技术方案文档/`、商业软件目录及 RAR：外部参考或许可材料，不属于源码。

在业务数据保留期和验收责任人确认前，不应仅依据“Git 已忽略”删除
`workspace/inputs`、`workspace/state` 或本次接管备份。
