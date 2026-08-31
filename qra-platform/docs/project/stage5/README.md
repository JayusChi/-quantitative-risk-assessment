# 第五阶段：人工复核工作台与不可变输入快照

状态：`REVIEW_WORKBENCH_ACCEPTED`

实施日期：2026-08-31（Asia/Shanghai）

第五阶段实现了候选字段到不可变输入快照的普通业务闭环。管理页面不再要求用户粘贴
`review_decisions JSON`；候选、证据、冲突和质量问题仍复用第四阶段事实表。

## 数据库迁移

数据库 `schema_version` 从 `1.8.0` 增量升级为 `2.0.0`，新增：

- `review_session`：会话状态、乐观锁revision、候选/来源哈希、目标节点和确认快照；
- `review_decision`：五类追加式决定、人工原始/归一值、原因和替代链；
- `review_gate_run`：每次门禁的输入哈希、计数、节点能力、组装哈希和完整结果；
- `reextraction_request`：字段/来源/证据级重新提取队列及替换运行；
- `input_snapshot_review_provenance`：相同快照可重复确认的完整复核来源链。

迁移保留旧表和 `input_snapshot_provenance`。触发器保护快照业务列、复核决定和门禁运行不可更新或删除；
部分唯一索引保证同一转换任务最多一个可编辑会话。

## 实现模块

- `db_qra.review_service`：会话、字段组、五类决定、revision冲突、证据、重提取、门禁持久化、原子确认和审计；
- `db_qra.review_assembly`：手工值归一、业务目标路径映射及确定性JSON组装；
- `db_qra.review_gate`：Schema、引擎输入合同、动态节点能力及正式报告边界；
- `db_qra.review_ui`：原生HTML/CSS/JavaScript三栏式响应式工作台；
- `db_qra.server`：工作台页面和写/读API，旧普通确认路径统一经过复核服务；
- `db_qra.admin_ui`：任务行和详情中的“打开复核工作台/创建复核新版本”。

API、状态机、门禁规则、证据格式和操作说明见
[`docs/guides/review-workbench.md`](../../guides/review-workbench.md)。

## 审计和追溯

已覆盖会话创建/恢复/过期、五类决定、重新提取请求/完成、门禁开始/阻断/通过、确认开始/成功/失败。
事件保存actor、任务/会话/字段组、旧新决定、revision及候选/决定哈希，不保存密钥或完整敏感正文。

快照元数据可沿以下链路追溯：

```text
input_snapshot
  → input_snapshot_review_provenance
  → review_session / review_gate_run / review_decision
  → candidate_field / candidate_evidence_link
  → conversion_source / extraction_run
```

## 确定性端到端验收

`tests.integration.test_review_workbench` 使用临时SQLite数据库和临时运行目录，不调用真实云端模型。
案例覆盖两个管段、20个以上字段组、唯一候选、低置信度、冲突、缺失关键字段、手工单位换算、合法不适用、
表格上下文、图片/PDF坐标高亮、revision 409、候选变化过期、门禁、事务故障注入、不可变快照、计算节点、
报告资源和反向审计链。

验收命令：

```powershell
$env:PYTHONPATH = 'src'
python tools/run_stage5_acceptance.py `
  --record docs/project/stage5/stage5-acceptance.json `
  --json
```

全量回归命令：

```powershell
.\scripts\test.ps1
```

机器可读结果见 `stage5-acceptance.json`。测试版仍使用本机受信管理模式；企业统一认证、电子签名、
多人实时协同和重新提取执行队列属于后续生产化范围。

最终验收结果：第五阶段专项 16/16 通过；项目全量回归执行 230 项，229 项通过、1 项因未配置真实OCR
按运行条件跳过；架构检查与修改文件Ruff检查通过。另以真实浏览器完成三栏、表格证据、决定保存、门禁、
确认快照和720px移动视图操作，浏览器控制台无错误。
