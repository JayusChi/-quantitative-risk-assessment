# 第三阶段：文档解析、OCR 与版面识别

第三阶段已于 2026-08-28 正式验收通过并关闭。平台已建立从 `READY_FOR_PARSE`
源文件到版本化 `ParsedDocument`、质量报告、预览清单和数据库解析资源的完整链路。

已完成：

- CSV、XLS、XLSX、DOCX、PDF、PNG、JPG/JPEG 按检测媒体类型进入统一合同；
- CSV 物理行、Excel 公式/缓存/合并/隐藏结构、DOCX OOXML 位置、PDF/图片坐标均可追溯；
- PDF 逐页区分原生、混合、扫描和不可读内容；
- OCR 禁用、夹具和 HTTPS 部署适配器，以及超时/限流/认证/非法响应降级；
- 图像 EXIF 转正、灰度/对比度预处理、可逆坐标矩阵和清晰度/曝光/分辨率问题；
- 解析哈希、版本化缓存、跨页表格关系候选和低置信度门禁；
- 每个源文件独立 `PARSED/PARSE_FAILED`，并固化质量摘要和解析资源；
- `ParsedDocument → RawTable` 兼容层保持旧转换黄金结果与复核 ID 稳定。
- 九江真实 JPG 和两页扫描 PDF 已使用 `aliyun-bailian-dashscope/qwen3.5-ocr`
  完成正式验收，OCR 块页码/坐标绑定率为 100%；
- 全量架构检查通过，182 项测试通过、1 项旧环境变量型冒烟按设计跳过。

复验命令：

```powershell
python .\tools\run_stage3_acceptance.py `
  --require-real-ocr `
  --json `
  --record .\docs\project\stage3\real-ocr-acceptance.json
```

详见 [解析开发与部署指南](../../guides/parsing.md)、[阶段3验收记录](阶段3验收记录.md)
和 [脱敏真实 OCR 验收记录](real-ocr-acceptance.json)。第四阶段可以正式开始。
