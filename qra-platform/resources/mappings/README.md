# Versioned mappings

自动转 JSON 阶段的客户/模板映射配置放在本目录。建议使用以下标识：

```text
resources/mappings/<source-family>/<mapping-version>.json
```

每个版本至少声明适用文件特征、字段别名、目标 JSON 路径、源单位、目标单位、枚举映射和冲突优先级。历史版本只读；规则变化新增版本，不原地改变已用于快照的映射语义。

第一阶段通用配置为 `generic/generic.structured-mvp.v1.json`。它用于开发联调和标准表头，不代表任意客户资料都可无确认套用。新增客户配置后可用文件路径或配置内的 `profile_id` 传给 `python -m qra_converter convert --profile`。

第二阶段配置 `generic/generic.multisource-review.v2.json` 通过 `extends` 继承第一阶段只读配置，并示范：

- 用 `source_priorities` 声明主数据、已复核资料和补充资料的确定性建议顺序；
- 用表级 `record_key` 声明合并与去重主键；
- 用 `manual_review.confidence_threshold` 控制低置信度复核门槛；
- 扩展阀门、泄漏事件、巡线与第三方活动、土壤腐蚀环境和应急资源类别。

优先级只决定冲突预览中的建议值。不同标准化值仍会生成阻断型复核项，不得仅因来源优先级高而自动进入计算。
