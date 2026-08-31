# 第五阶段数据库迁移说明

目标版本：`2.0.0`

迁移由 `QraDatabase.initialize()` 在同一数据库连接内幂等执行，不要求手工运行SQL。它只新增复核表、
索引和不可变触发器，并保留旧转换、候选、来源、快照、计算和审计数据。

## 升级

升级前按现有运维流程备份 `workspace/state/qra.sqlite3`，停止旧服务，然后在新代码下执行：

```powershell
$env:PYTHONPATH = 'src'
python -m db_qra init
python -m db_qra serve
```

初始化可重复运行。旧 `conversion_source` 结构迁移、已有列补齐和第五阶段表创建继续使用现有兼容逻辑，
不重建用户业务快照。

## 新增对象

- 表：`review_session`、`review_decision`、`review_gate_run`、`reextraction_request`、
  `input_snapshot_review_provenance`；
- 唯一约束：同一转换任务最多一个 `OPEN/IN_REVIEW/READY_TO_CONFIRM` 会话；
- 索引：会话任务/状态、决定会话/字段组、门禁会话/revision、重提取任务/状态、快照复核来源；
- 触发器：输入快照业务列不可更新、复核决定不可更新/删除、门禁运行不可更新/删除。

## 验证

```powershell
$env:PYTHONPATH = 'src'
python -m unittest `
  tests.integration.test_review_workbench.ReviewWorkbenchIntegrationTests.test_migration_creates_review_tables_and_immutable_triggers `
  tests.integration.test_file_intake_phase2.FileIntakePhase2IntegrationTest.test_legacy_conversion_source_schema_is_migrated_without_rebuild -v
```

验收同时验证旧数据库兼容和历史数据链未破坏。数据库浏览器可只读查看新增表；复核业务写入必须经过
服务接口。

## 回退原则

不要删除新增表或用旧程序写入已升级数据库。若必须回退应用版本，应停止服务并恢复升级前的完整数据库
备份。已由第五阶段确认的新快照和来源记录应先保留或导出，不能通过删除表模拟降级。
