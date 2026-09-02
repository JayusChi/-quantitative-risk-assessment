# 阶段 3：原始资料到不可变快照

阶段 3 已建立 `S00_BASELINE × D00_CLEAN` 的确定性主链，并把阶段 2 的 XLSX、CSV、DOCX、扫描 PDF 真正转换为证据、候选、实体关系、复核项和确认后不可变快照。

## 实现范围

- 版本化专用映射和扩展候选 Schema：256 个可证据化项目事实均声明别名、单位、枚举、空白策略、冲突策略、关键性和受影响节点；4 个容器字段由结构合同派生。
- 多格式证据定位：116 个 XLSX 单元格、47 个 CSV 单元格、40 个 DOCX 表格单元格、42 个 PDF 页框、11 个 PNG 图框。
- PDF 确定性重放：使用源文件 SHA-256、页码、bbox 和裁剪像素 SHA-256 四重绑定；不按字段名裸回放。
- 候选安全边界：无证据候选不进入融合；文档内容不能改变系统策略、合同、工作流或门禁。
- 实体和关系：生成管线、20 个管段、人口网格、气象情景实体，以及管段到管线的 `BELONGS_TO` 关系。
- 跨文件对齐：以管段编号、原型和显式覆盖组合工程指标；不做静默覆盖。
- 组装边界：项目事实必须绑定文件证据；68 个模型参数仅绑定 6 个参数包；运行假设使用独立版本绑定。
- 复核工作台数据：按评价、管线、管段、人口、气象、工程指标分组，显示受影响节点；人工修改记录原值、新值、原因、人员和时间。
- 确定性重放、在线提供器配置、黄金差异、转换覆盖率和负向场景报告均可机器读取。
- 只有调用确认入口且门禁通过，才把 `qra_input` 写入 SQLite 不可变快照；预览运行不写计算输入库。

## 入口

生成/校验版本化合同：

```powershell
.\.venv\Scripts\python.exe tools/build_full_chain_stage3_contract.py
```

运行单个条件：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage3.py --condition D00_CLEAN
```

执行完整阶段门禁：

```powershell
.\.venv\Scripts\python.exe tools/run_full_chain_stage3_acceptance.py
```

正式输出位于 `workspace/outputs/m1-5-stage3-raw-to-snapshot-20260901`。

## 在线演示边界

`provider-configs.v1.json` 声明阿里云百炼结构化输出配置、环境变量名、零工具权限和失败回退策略。全合成工作流连接实际 `ExtractionProvider` 调用边界；只有同时配置提供器并传入 `--allow-external-sharing` 才会发起结构化在线调用。验收命令从本机 Windows DPAPI 加密设置中恢复凭据，任何输出均不包含 API Key。

2026-09-02 已使用阿里云百炼 `qwen3.8-max` 对当时的 69 字段版本完成授权实测。其 D00 与经人工确认的 D10 均为 69/69 正确。随后本地合同扩展到 256 个直接证据字段，本次只完成离线确定性验收，未再次外发；因此在线记录仍是历史 69 字段样本，不冒充 256 字段在线验收。在线模式仍按设计保持 `COMPLETED_REVIEW_REQUIRED`。
