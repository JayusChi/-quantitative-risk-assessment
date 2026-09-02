# 阶段 5：统一项目向导和普通用户体验

阶段 5 已把资料接收、自动转换、人工复核、快照确认、11 节点计算和报告查看收敛为一个项目级普通用户入口。默认首页为 `/projects/`；数据库、JSON 导入和底层任务信息保留在高级管理与审计入口，不再成为普通业务流程的前置知识。

## 已实现范围

- 项目列表展示名称、数据类型、业务状态、数据完整度、待处理问题、计算进度和最新报告，并支持新建、演示加载、进入和归档。
- “加载全合成演示项目”幂等创建 `S00_BASELINE × D00_CLEAN` 项目，绑定阶段 3 不可变快照、6 个参数包以及阶段 4 的 11/11 计算结果。
- 项目详情按“上传资料 → 自动转换 → 数据复核 → 确认数据 → 风险计算 → 查看报告”展示六步向导和下一步操作。
- 普通上传入口只接收 CSV、XLS、XLSX、DOCX、PDF、PNG、JPG/JPEG 和 ZIP 文件，不要求粘贴 JSON；页面说明文件安全、失败策略、重复、版本和隔离状态。
- 三栏复核工作台增加关键程度筛选、字段影响、数据层、参数包说明、距确认的剩余阻断项和返回项目入口。
- 计算区展示 11 个节点、节点状态、失败或跳过原因、补数项、数据快照哈希、引擎版本和 6 个参数包版本。
- 报告中心展示草稿状态、完整性、数字一致性、引用绑定、人工确认状态，并提供页内 HTML、独立 HTML 和 ZIP 入口。PDF/DOCX 按项目路线图留给阶段 6 的受控报告输出，因此当前以禁用且带说明的入口呈现。
- 失败项目提供“重试/继续处理”，不会丢失已确认快照；合成项目全程显示醒目标识并关闭正式报告判定。
- 高级审计折叠区可追溯项目、转换、复核、快照、计算任务、输入/结果哈希和引擎版本。
- 桌面和 390px 窄屏均完成真实浏览器检查；支持跳转链接、键盘焦点、对话框关闭、字段级错误提示和无横向溢出布局。

## 使用入口

启动服务：

```powershell
.\.venv\Scripts\python.exe -m db_qra.server
```

打开普通用户项目工作台：

```text
http://127.0.0.1:8765/projects/
```

高级管理中心仍位于 `/admin/`。

## 验收入口

阶段门一键验收：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage5_acceptance.py
```

专项测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/integration/test_full_chain_stage5_user_journey.py tests/unit/test_full_chain_stage5_user_journey.py -q
```

机器可读验收记录位于 `resources/synthetic/full-chain-v1/stage5/stage5-acceptance.json`，可重建数据库和运行产物位于 `workspace/outputs/m1-5-stage5-user-journey-20260901`。

## 主要代码

| 文件 | 职责 |
|---|---|
| `src/db_qra/project_service.py` | 项目聚合、业务状态、六步向导、演示加载和审计视图 |
| `src/db_qra/project_ui.py` | 普通用户项目列表、详情、上传、计算与报告中心 |
| `src/db_qra/database.py` | `business_project` 持久化合同和项目生命周期关联 |
| `src/db_qra/server.py` | 项目页面与项目级 API、继续/重试流程 |
| `src/db_qra/review_ui.py` | 三栏复核的项目化导航和筛选增强 |
| `tools/run_full_chain_stage5_acceptance.py` | S5-01～S5-10 与端到端返回导航验收 |

