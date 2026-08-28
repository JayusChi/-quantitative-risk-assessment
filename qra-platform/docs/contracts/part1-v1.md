# 第一部分输入合同 v1（第一阶段实现记录）

本记录对应《第一阶段实施说明：行业字段标准与数据合同》，与
`docs/project/stage1` 中既有的“九江真实数据转换验收”不是同一阶段编号体系。

## 当前结论

`qra.part1-input/1.0.0` 已形成 `TEST_EDITION`：合同目录、字段字典、单位、术语、
来源等级、问题码、六个 Schema、合法/非法样例和节点—字段黄金矩阵均已落地。
现有完整合成案例同时通过 JSON Schema 与
`qra_engine.validation.validate_import_contract`；最小案例仅开放数据盘点、指标覆盖、
管段几何、自适应证据 QRA 和展示矩阵等基础节点。

字段字典由构建工具读取当前工程指标目录生成，登记 17 组、246 项指标；字典同时
补充动态节点显式必需字段和评审确认的隐含预检字段。生成数量不作为测试中的固定
替代物，测试会逐项比较源指标 ID 集合。

## 使用入口

- 合同目录：`resources/contracts/part1/v1`
- 构建工具：`tools/build_part1_contract.py`（仅用于准备新版本或 TEST_EDITION）
- 加载器：`qra_converter.contract_catalog.load_contract_catalog`
- Schema 入口：`qra_converter.schema_validation.validate_qra_input`
- 节点黄金矩阵：`tests/fixtures/contracts_v1/expected-node-field-matrix.json`

平台转换任务在入队时固化合同 ID、版本、清单哈希和受控路径；执行前会重新校验。
低层转换服务保留不传合同目录的测试兼容调用，但平台 CLI 与 `db_qra` 组合根会显式
传入 v1 合同。

## 未决与发布边界

- 当前状态是 `TEST_EDITION`，不是正式工程发布；正式签批仍需业务、算法、软件三方
  完成评审。
- 第一阶段没有实现 OCR、大模型抽取、自动冲突解决或业务复核页面。
- `damage_model`、`mock_adapter_output`、`expected_aggregation`、
  `validation_expectations` 已标记为 `TEST_ONLY`，不得作为客户资料抽取目标。
- 没有修改第二部分数值公式或模型发布状态。

## 验证命令

```powershell
$env:PYTHONPATH = "$PWD\src"
python tools/check_architecture.py
python -m unittest tests.unit.converter.test_contract_catalog -v
python -m unittest tests.unit.converter.test_schema_validation -v
python -m unittest tests.unit.engine.test_part1_contract_coverage -v
python -m unittest tests.unit.engine.test_validation -v
.\scripts\test.ps1
```
