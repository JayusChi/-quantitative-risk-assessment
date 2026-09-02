# 阶段 6：受控报告智能体

阶段 6 已把不可变输入快照、不可变计算结果、节点状态、参数版本和证据索引转换为 `report-context-v1`，再通过受约束的 `report-draft-v1` 生成可读报告。大模型只可提供结构化文字，数字替换、引用、图表、禁用表述检查、水印、版本保存和发布边界均由程序确定性控制。

## 已实现范围

- 定义 `report-context-v1` 与 `report-draft-v1` JSON Schema，以及受控生成提示 `qra-controlled-report-prompt-v1`。
- 从当前项目的不可变快照和 11 个节点结果确定性提取 40 个指标、11 个证据项、11 个结果项、6 个参数绑定、缺失数据、不确定性和正式发布阻断项。
- 支持结构化报告提供器；提供器不可用或输出未通过校验时自动降级到 15 章确定性模板。
- 自由文本禁止携带数字；最终数字只由指标占位符替换，并逐项校验指标、证据、结果、不确定性和节点状态引用。
- 拒绝无来源项目事实、缺失节点完成声明、风险可接受声明、解除阻断和“正式评价通过”等禁用表述。
- 生成确定性 F-N 曲线、管段 PLL 排序和风险矩阵；相同上下文的文字可变化，但数字、图表和引用目标保持不变。
- HTML、PDF、DOCX 和 ZIP 均带“合成数据 · 仅供软件测试”水印；报告中心可查看、下载、确认和追溯历史版本。
- 人工确认只把状态推进到 `CONFIRMED_TEST_ONLY`，不会打开正式报告门；`formal_report_allowed` 始终保持 `false`。
- 保存报告版本、上下文哈希、草稿哈希、验证记录、生成模式、提供器标识和各产物 SHA-256；受控载荷由数据库触发器保护为不可变。

## 使用入口

启动服务：

```powershell
.\.venv\Scripts\python.exe -m db_qra.server
```

在 `/projects/` 加载全合成演示项目，进入项目详情后可在“报告中心”生成、查看、下载和人工确认受控测试报告。

## 验收入口

阶段门一键验收：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage6_acceptance.py
```

阶段 6 专项测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_full_chain_stage6_controlled_report.py tests/integration/test_full_chain_stage6_controlled_report.py -q
```

机器可读记录位于 `resources/synthetic/full-chain-v1/stage6/stage6-acceptance.json`；可重建数据库和报告产物位于 `workspace/outputs/m1-5-stage6-controlled-report-20260901`。

## 主要代码

| 文件 | 职责 |
|---|---|
| `src/db_qra/controlled_reporting.py` | 上下文、草稿、校验、降级、图表和 HTML/PDF/DOCX/ZIP 渲染 |
| `src/db_qra/database.py` | 报告版本、确认记录、产物哈希和不可变保护 |
| `src/db_qra/project_service.py` | 项目报告中心聚合、历史版本和下载入口 |
| `src/db_qra/project_ui.py` | 生成、查看、导出和人工确认交互 |
| `src/db_qra/server.py` | 项目级报告 API、公开 HTML 和文件导出路由 |
| `tools/run_full_chain_stage6_acceptance.py` | S6-01～S6-14 一键验收及负例校验 |

