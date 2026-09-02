# 阶段 7：全合成端到端总验收与演示版发布

阶段 7 已把阶段 1～6 串成可重复的一条龙流程，并关闭最终门禁 `M1_5_FULL_SYNTHETIC_END_TO_END_ACCEPTED`。验收从全新数据库和原始合成资料开始，覆盖资料解析、候选值与证据、冲突决策、不可变快照、11 个计算节点、受控报告、人工确认、防篡改、服务恢复、备份恢复、独立发布目录冷启动和真实浏览器操作。

## 最终结果

- 阶段 7 场景与发布检查：19/19 通过。
- D00 基线：11/11 节点完成，数值哈希与黄金基线完全一致。
- D10：人工决策前阻断；决策后生成新决策哈希，报告证据索引记录该决策。
- D20：缺失保持显式，不补零；受影响环节跳过并输出待补数据清单。
- D30/D40：低质量扫描和超大图片走预处理、复核、可逆坐标与局部重提取路径。
- D50：提示注入只作为不可信资料处理并进入审计。
- 模型不可用：确定性工作不丢失，保留重试与人工入口，不伪造结果。
- 服务重启、备份恢复：快照、结果和报告资源保持不变。
- 报告篡改：确认被拒绝并写入审计事件。
- 真实 Chromium：普通用户一键加载、11/11、报告查看/导出/确认、桌面与手机视口均通过，控制台错误 0。
- 全程保持 `SYNTHETIC_TEST_ONLY`，`formal_report_allowed=false`。

机器可读验收记录位于 `resources/synthetic/full-chain-v1/stage7/stage7-acceptance.json`，浏览器记录位于同目录的 `stage7-browser-acceptance.json`。

## 演示版入口

发布目录：`workspace/releases/QRA全合成端到端演示版_v1`

发布 ZIP：`workspace/releases/QRA全合成端到端演示版_v1.zip`

在发布目录的 PowerShell 中执行：

```powershell
.\Install-Demo.ps1
.\Start-Demo.ps1 -OpenBrowser
```

脚本会创建隔离运行环境，幂等加载全合成演示项目、11 节点结果和受控测试报告，然后启动本地服务。用户无需编辑数据库、上传 JSON 或手工拼接中间文件。

## 验收与重建

```powershell
.\.venv\Scripts\python.exe tools\run_full_chain_stage7_acceptance.py
.\.venv\Scripts\python.exe tools\build_full_synthetic_demo_release.py
```

可重建证据位于 `workspace/outputs/m1-5-stage7-end-to-end-20260901`。发布目录中的 `release-manifest.json`、`checksums.sha256` 和根目录外层 ZIP 用于核对发布内容。

## 文档

- `用户操作手册.md`：安装、启动、演示和报告操作。
- `管理和审计手册.md`：状态、审计、防篡改与故障检查。
- `演示脚本.md`：面向演示人员的标准讲解顺序。
- `备份恢复说明.md`：数据库备份、验证和恢复。
- `版本和依赖清单.md`：版本、依赖和测试环境。
- `已知限制.md`：测试边界和未覆盖能力。
- `阶段7验收记录.md`：场景矩阵和验收结论。
- `发布清单.md`：发布物和完整性检查。

