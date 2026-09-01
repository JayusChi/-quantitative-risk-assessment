# 路线图第四阶段：原始资料到筛查报告闭环

本目录记录“后续安排”中的路线图第四阶段/M1，不等同于仓库历史命名的 converter stage 4、stage 5，也不表示完整 QRA 已完成。

## 本阶段交付

平台现在能够从受控原始文件入口完成同一条可追溯链路：

```text
原始文件
→ 文件安全检查与解析
→ 实体、候选、证据和质量问题
→ 三栏人工复核工作台逐项决定
→ 服务端门禁
→ 不可变输入快照与本次复核 provenance
→ 自动创建并执行计算任务
→ 筛查报告
→ M1 外键链摘要与机器验收记录
```

正向闭环不接收 `review_decisions JSON`，不调用快照导入 API，也不直接写候选、证据、快照或计算结果事实表。计算任务严格引用本次确认返回的 `snapshot_id` 和输入哈希。

## 状态含义

- 工程 fixture 全链通过：`ENGINEERING_PASS / M1_PILOT_PENDING`。
- 批准的真实受控资料、真实浏览器和虚拟内部复核身份全链通过：`M1_INTERNAL_PILOT_PASS / FORMAL_BUSINESS_SIGNOFF_PENDING`。
- 当前结果仅为证据条件化筛查层级。它不是完整 11/11 QRA，不是风险接受性结论，也不是正式工程签章或监管签发。

当前受控试点的五个目标节点由版本化 manifest 固化：`data_inventory`、`indicator_coverage`、`segment_geometry`、`adaptive_evidence_qra`、`risk_matrix`。未知节点、重复节点或与试点清单不一致的节点范围会被拒绝。

## 一键验收

工程回归（仓库内脱敏原始格式 fixture，不外发）：

```powershell
.\.venv\Scripts\python.exe .\tools\run_roadmap_stage4_acceptance.py --mode engineering --json
```

真实试点必须显式提供授权、批准目录、试点 ID 和内部复核身份。默认且当前清单禁止公网 OCR/模型外发：

```powershell
.\.venv\Scripts\python.exe .\tools\run_roadmap_stage4_acceptance.py `
  --mode pilot `
  --pilot-id jiujiang-qra-screening-pilot-v1 `
  --source-root <批准的 workspace 来源目录> `
  --reviewer <内部功能复核身份> `
  --authorized `
  --record .\docs\project\roadmap-stage4\stage4-e2e-acceptance.json `
  --json
```

验收工具拒绝把 QRA JSON 或 SQLite 当作原始资料；拒绝未经授权读取真实试点；拒绝已有记录文件被旧结果覆盖；记录写入前执行绝对路径、Authorization、凭据和大段 Base64 脱敏检查。

## 关键实现与验证

- `src/db_qra/roadmap_stage4.py`：同一外键链的 M1 只读摘要、反向溯源、目标节点和报告完整性门槛。
- `src/db_qra/review_service.py`：逐项复核、最终门禁、快照复用 provenance、确认幂等和自动排队。
- `src/db_qra/engine_adapter.py`：快照/输入哈希校验与目标节点执行。
- `src/db_qra/server.py`、`src/db_qra/review_ui.py`：闭环 API、真实页面确认后立即计算及报告入口。
- `tests/integration/test_roadmap_stage4_e2e.py`：从原始 CSV 开始的生产组合入口全链和负向回归。
- `tools/run_roadmap_stage4_acceptance.py`：工程/真实试点两种验收模式。

本次真实内部试点的详细结果见 [阶段4闭环验收记录.md](./阶段4闭环验收记录.md)；机器可读事实见 [stage4-e2e-acceptance.json](./stage4-e2e-acceptance.json)。
