# QRA 平台架构

## 1. 架构目标

平台采用“模块化单体 + 端口适配器”结构。计算公式、源文件转换和持久化服务在同一个工程中开发和测试，但通过独立 Python 包形成硬边界。这样既保留当前单机部署的简单性，又能在以后把转换任务或计算任务拆成独立服务时保持接口稳定。

本次调整遵循三条原则：

1. 已验证的数值公式不因目录治理而改写；
2. 自动识别文件不得直接写数据库或修改计算模型；
3. 所有进入计算引擎的数据都必须经过“转换、人工确认、输入合同校验、不可变快照”链路。

## 2. 上下文与依赖方向

```text
┌──────────────── qra_converter ────────────────┐
│ readers -> mapping -> matching -> assembly    │
│                  -> quality report/provenance │
└──────────────────────┬────────────────────────┘
                       │ ConversionResult / 标准 JSON 草稿
                       v
┌─────────────────── db_qra ────────────────────┐
│ 人工确认、输入合同适配、快照、审计、任务、API │
└──────────────────────┬────────────────────────┘
                       │ 已确认的不可变 dict
                       v
┌───────────────── qra_engine ──────────────────┐
│ 校验 -> 动态 DAG -> 公式计算 -> 风险聚合/报告 │
└───────────────────────────────────────────────┘
```

允许的依赖：

- `qra_engine` 只依赖 Python 标准库和自身版本化模型规格；
- `qra_converter` 不依赖计算引擎和数据库，通过稳定合同输出草稿；
- `db_qra` 作为组合根，可以调用转换器和计算引擎；
- `tools/check_architecture.py` 会在测试前检查上述规则并禁止源码修改 `sys.path`。

### 第一部分合同资源的组合规则

第一部分输入标准存放在 `resources/contracts/part1/<version>`，不是任一源码包的
隐式全局变量。`qra_converter` 只接收由组合根显式传入的合同目录，负责清单路径、
版本和逐文件 SHA-256 校验；它不得导入 `qra_engine`。`db_qra` 和平台 CLI 是组合根，
在创建转换任务时选择合同目录，并把 `contract_id`、`contract_version` 和清单哈希
固化到任务。任务执行前再次比对三项身份，目录内容发生变化时必须失败。

JSON Schema 负责结构、类型、枚举和早期质量问题；
`qra_engine.validation.validate_import_contract` 仍是第二部分跨字段语义校验的最终
权威。动态节点依赖通过黄金矩阵和资源文件测试比对，不允许为复用节点目录而让
`qra_converter` 反向依赖 `qra_engine`。

## 3. 包职责

### `qra_engine`

计算核心继续保持无数据库依赖。现有模块按职责分为四组：

- 数学与领域模型：`aqt3046`、`gbt34346`、`frequency`、`event_tree`、`risk`；
- 应用调度：`engine`、`dynamic`、`automation`；
- 合同与模型登记：`validation`、`indicators`、`model_registry`、`model_specs`；
- 输出适配：`reporting`、`audit`。

这些文件当前保留原导入路径，避免目录迁移改变已经验证的计算行为。以后拆分大文件时，应先固定公共 API 和黄金结果，再做仅结构性的迁移。

### `qra_converter`

第二阶段在 `contracts.py` 和 `ports.py` 的稳定边界上扩展了多来源转换与人工复核链路：

- `SourceReader` 只做源文件的忠实提取；
- `MappingProvider` 负责版本化字段语义和单位映射；
- `ConversionResult` 同时携带 JSON 草稿、来源、映射版本和全部质量问题；
- `ERROR` 阻断确认，`WARNING` 允许进入人工复核，`INFO` 只记录转换说明；
- `readers` 支持 CSV、XLS、XLSX，并对 DOCX/PDF 提供必须复核的辅助提取；
- `mapping` 负责版本化表格选择、表头扫描、类型和单位归一；
- `review` 负责业务主键去重、来源优先级、字段冲突、低置信度门禁和人工决定审计；
- `matching` 使用管段 ID 和半开里程区间关联记录；
- `assembly`、`validation` 和 `reporting` 分别负责 JSON 草稿、转换质量检查和三份确定性输出。

当前包内结构为：

```text
qra_converter/
├─ parsing/       统一解析合同、媒体注册、流水线、坐标、质量与缓存
├─ ocr/           提供方端口、禁用/夹具/HTTPS适配器和重试边界
├─ image_processing/  受控解码、EXIF转正、质量检测与可逆预处理
├─ readers/       XLS/XLSX/CSV 与需复核的 DOCX/PDF 辅助提取
├─ mapping/       表头别名、字段、枚举和单位规则
├─ matching/      管段、里程与空间关联
├─ assembly/      标准 JSON 组装
├─ validation/    转换层质量检查
├─ review.py      多来源合并、复核门禁与审计
└─ reporting/     来源清单、转换预览、冲突和复核报告
```

禁止读取器直接拼装最终 JSON，也禁止映射代码调用计算公式。

第三阶段后，`readers` 的主输出为版本化 `ParsedDocument`。旧 `RawTable` 只由
`parsing.compatibility` 生成，作为第四阶段候选字段流水线完成前的兼容边界。
读取器注册按第二阶段检测媒体类型选择唯一主读取器；PDF 只在页级路由内部组合
原生文本与 OCR。解析原文、规范化文本和映射后的业务字段不得互相覆盖。

### `db_qra`

这是平台应用与基础设施适配层，负责 SQLite、不可变输入快照、计算任务、Admin/API 和报告资源。`paths.py` 是唯一知道本地 `workspace` 布局的源码模块；部署时通过 `QRA_WORKSPACE_ROOT` 切换数据盘或挂载点。

`file_intake.py` 是平台资料入口安全边界，负责文件签名/MIME 一致性、ZIP 安全展开、成员级哈希、隔离、重复/版本关系和不可变清单哈希。`conversion_adapter.py` 是平台转换组合根：只从受控映射目录选择配置，只把 `READY_FOR_PARSE` 源文件写入任务隔离临时目录，调用与命令行相同的 `convert_sources`，并把预览、报告、来源清单和审计结果交给数据库。HTTP 处理器只负责请求体限制、Base64 解码、后台线程调度和统一错误响应，不复制入口或转换规则。

第五阶段把人工确认拆成四个确定性应用模块：`review_service` 管理会话、追加式决定、乐观锁、证据和
原子确认；`review_assembly` 依据字段合同归一手工值并组装业务JSON；`review_gate` 组合Schema、引擎输入
合同和动态节点能力；`review_ui` 提供无前端构建链的三栏业务页面。候选仍由 `qra_converter` 产生且只读，
复核服务不能改写候选或提取事实。旧确认HTTP入口也进入同一复核门禁，不存在普通页面旁路。

## 4. 数据生命周期

1. 源文件进入 `workspace/inputs`；
2. 转换器生成 `ConversionResult`，保留文件哈希、位置和映射版本；
   在映射前，每个 `READY_FOR_PARSE` 文件独立生成 `ParsedDocument`、质量报告和
   预览资源，并立即写入 `PARSED/PARSE_FAILED`；
3. `db_qra` 创建与候选集合哈希绑定的复核会话，人工查看证据并追加决定；
4. 复核门禁重新组装JSON，执行Schema、`qra_engine.validation.validate_import_contract` 和目标节点检查；
5. 门禁通过后在单一事务中以内容哈希写入或复用不可变快照，并保存完整复核来源；
6. 计算任务只读取指定快照，并保存引擎版本、节点状态、结果哈希和报告资源；
7. 历史转换和计算结果不原地覆盖，新版本形成新快照。

转换任务状态为 `QUEUED → RUNNING → READY_FOR_CONFIRMATION/BLOCKED → CONFIRMED`；执行异常使用
`FAILED`，未确认任务可协作式进入 `CANCELLED`。复核会话独立使用
`OPEN → IN_REVIEW → READY_TO_CONFIRM → CONFIRMED`，候选集合变化进入 `STALE`。文件状态独立记录
`VALIDATED/QUARANTINED/DUPLICATE/READY_FOR_PARSE/PARSE_FAILED/PARSED`。只有至少一个
`READY_FOR_PARSE` 且符合隔离策略的任务可以排队；确认必须重新通过服务端门禁，并在同一事务中创建或
复用快照。服务重启会完成待生效取消，并把其余 `QUEUED/RUNNING` 任务恢复为 `QUEUED`。

## 5. 工程规则

- 业务数据与生成物只进入 `workspace`，脱敏黄金样本进入 `tests/fixtures`；
- 模型规格和映射配置都必须带版本，不在 Python 代码中散落客户字段别名；
- 新功能至少包含单元测试；跨包流程必须包含集成或合同测试；
- 公式变更必须保留黄金结果对比和数值哈希；
- CLI、网页和未来任务队列都调用同一应用服务，不各自复制业务流程；
- 当前为私有模块化单体，不提前引入消息队列、微服务或分布式事务。

## 6. 自动转换阶段验收边界

第二阶段的转换草稿只有在没有 `ERROR` 且没有阻断型待复核项时才标记为 `READY_FOR_REVIEW`。来源优先级只生成建议值；冲突和低置信度记录必须应用带复核人、时区时间和原因的决定，审计记录不得写入或覆盖源事实字段。转换成功不代表计算模型达到正式发布状态，转换器也不得设置 `formal_report_allowed` 或模型发布状态。
