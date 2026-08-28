# 文件入口安全合成夹具

本目录只包含合成数据，不含真实项目资料。文本夹具用于人工检查和简单回归；
PNG、JPEG、PDF、OOXML 及恶意 ZIP 由 `tests/unit/test_file_intake.py` 在内存中生成，
避免提交来源不明或带主动内容的二进制文件。

- `normal/资料.csv`：正常 UTF-8 CSV；
- `versions/v1/资料.csv`、`versions/v2/资料.csv`：同名异哈希版本；
- `unsupported/readme.txt`：ZIP 未知成员样本内容；
- `damaged/damaged.xlsx`：扩展名伪装的损坏 OOXML 文本样本。

