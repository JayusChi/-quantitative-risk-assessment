# 独立完整基准验收

`compare_external_benchmark.py` 用于 P0-01。它只接受状态为 `APPROVED`、明确独立于本引擎、带结果源文件 SHA-256 和出具机构的参考结果；合成期望值或本引擎自算结果不能作为外部基准。

参考 JSON 至少包含：

```json
{
  "benchmark_id": "批准后的案例编号",
  "profile": "aqt3046-physical",
  "approval": {"status": "APPROVED", "approved_by": "审批人/机构"},
  "source": {
    "organization": "外部结果出具机构",
    "software_or_method": "授权软件或独立表格/历史工程",
    "version": "版本",
    "independent_of_qra_engine": true,
    "result_file_sha256": "64位小写SHA-256"
  },
  "tolerances": {"relative": 0.05, "absolute": 1e-12},
  "expected": {
    "source_term_release_rate_kg_s": {
      "small_5mm": {"minimum_kg_s": 0.0, "maximum_kg_s": 0.0}
    },
    "fatal_heat_flux_distance_m": [
      {"segment_id": "SEG-001", "value": 0.0}
    ],
    "maximum_ir_per_year": 0.0,
    "fn_curve": [
      {"fatalities_at_least": 1.0, "cumulative_frequency_per_year": 0.0}
    ],
    "pipeline_pll_per_year": 0.0
  }
}
```

示例命令：

```powershell
python .\validation\compare_external_benchmark.py `
  --case .\validation\approved_case.json `
  --reference .\validation\approved_external_result.json `
  --source-result-file .\validation\external_raw_export.zip `
  --output .\validation\comparison_result.json
```

只有比较结果 `passed: true`，且外部原始文件、审批记录和哈希一并归档后，P0-01 才能记录为通过。
