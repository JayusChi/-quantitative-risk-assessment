import fs from "node:fs/promises";
import path from "node:path";

function columnName(index) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function safeTableName(prefix, value, index) {
  const normalized = String(value)
    .normalize("NFKD")
    .replace(/[^A-Za-z0-9_]/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  return `${prefix}_${normalized || "Section"}_${index}`.slice(0, 240);
}

function styleTitle(range) {
  range.format = {
    fill: "#17365D",
    font: { bold: true, color: "#FFFFFF", size: 17 },
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 36,
  };
}

function styleWarning(range) {
  range.format = {
    fill: "#FFF2CC",
    font: { bold: true, color: "#7F6000" },
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 28,
  };
}

function styleHeader(range) {
  range.format = {
    fill: "#4472C4",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 28,
  };
}

function setColumns(sheet, lastRow, widths) {
  widths.forEach((width, index) => {
    const column = columnName(index);
    sheet.getRange(`${column}1:${column}${lastRow}`).format.columnWidth = width;
  });
}

function addAboutSheet(workbook, item) {
  const sheet = workbook.worksheets.add("说明");
  sheet.showGridLines = false;
  sheet.mergeCells("A1:H1");
  sheet.getRange("A1").values = [[item.title]];
  styleTitle(sheet.getRange("A1:H1"));
  sheet.mergeCells("A2:H2");
  sheet.getRange("A2").values = [["SYNTHETIC_TEST_ONLY｜合成测试数据｜严禁用于真实工程评价或正式报告"]];
  styleWarning(sheet.getRange("A2:H2"));
  sheet.getRange("A4:B9").values = [
    ["字段", "内容"],
    ["场景", `${item.scenario_id || "S00_BASELINE"} × D00_CLEAN`],
    ["案例编号", item.case_id],
    ["生成器版本", item.generator_version],
    ["基准日期", item.as_of],
    ["用途边界", "软件开发、算法验证、回归测试和培训演示"],
  ];
  styleHeader(sheet.getRange("A4:B4"));
  sheet.getRange("A5:A9").format = {
    fill: "#D9EAF7",
    font: { bold: true, color: "#17365D" },
  };
  sheet.getRange("A4:B9").format.borders = {
    preset: "all",
    style: "thin",
    color: "#B4C6E7",
  };
  sheet.getRange("A5:B9").format.wrapText = true;
  sheet.getRange("A1:A12").format.columnWidth = 22;
  sheet.getRange("B1:B12").format.columnWidth = 72;
  sheet.freezePanes.freezeRows(2);
  return sheet;
}

function addDataSheet(workbook, item, prefix) {
  const sheet = workbook.worksheets.add("数据");
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(2);
  let row = 1;
  let maximumColumns = 1;
  item.sections.forEach((section, sectionIndex) => {
    const columns = section.headers.length;
    maximumColumns = Math.max(maximumColumns, columns);
    const lastColumn = columnName(columns - 1);
    sheet.mergeCells(`A${row}:${lastColumn}${row}`);
    sheet.getRange(`A${row}`).values = [[section.title]];
    sheet.getRange(`A${row}:${lastColumn}${row}`).format = {
      fill: "#D9EAF7",
      font: { bold: true, color: "#17365D", size: 13 },
      verticalAlignment: "center",
      rowHeight: 27,
    };
    const headerRow = row + 1;
    sheet.getRange(`A${headerRow}:${lastColumn}${headerRow}`).values = [section.headers];
    styleHeader(sheet.getRange(`A${headerRow}:${lastColumn}${headerRow}`));
    if (section.rows.length > 0) {
      const firstDataRow = headerRow + 1;
      const lastDataRow = firstDataRow + section.rows.length - 1;
      sheet.getRange(`A${firstDataRow}:${lastColumn}${lastDataRow}`).values = section.rows;
      sheet.getRange(`A${firstDataRow}:${lastColumn}${lastDataRow}`).format = {
        verticalAlignment: "top",
        wrapText: true,
      };
      const table = sheet.tables.add(
        `A${headerRow}:${lastColumn}${lastDataRow}`,
        true,
        safeTableName(prefix, section.title, sectionIndex + 1),
      );
      table.style = "TableStyleMedium2";
      table.showHeaders = true;
      table.showFilterButton = true;
      table.showBandedRows = true;
      if (section.number_formats) {
        for (const [columnIndexText, numberFormat] of Object.entries(section.number_formats)) {
          const columnIndex = Number(columnIndexText);
          const column = columnName(columnIndex);
          sheet.getRange(`${column}${firstDataRow}:${column}${lastDataRow}`).format.numberFormat =
            numberFormat;
        }
      }
      row = lastDataRow + 3;
    } else {
      row = headerRow + 3;
    }
  });
  const widths = item.data_column_widths || Array(maximumColumns).fill(18);
  setColumns(sheet, Math.max(row, 8), widths);
  return sheet;
}

function addEvidenceSheet(workbook, item, prefix) {
  const sheet = workbook.worksheets.add("字段证据");
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(4);
  sheet.mergeCells("A1:I1");
  sheet.getRange("A1").values = [["字段证据索引"]];
  styleTitle(sheet.getRange("A1:I1"));
  sheet.mergeCells("A2:I2");
  sheet.getRange("A2").values = [["SYNTHETIC_TEST_ONLY｜value_json 与 ground-truth.json 使用同一规范序列化值"]];
  styleWarning(sheet.getRange("A2:I2"));
  const headers = [
    "field_id",
    "target_path",
    "business_name",
    "criticality",
    "unit",
    "value_json",
    "value_sha256",
    "quality",
    "as_of",
  ];
  sheet.getRange("A4:I4").values = [headers];
  styleHeader(sheet.getRange("A4:I4"));
  if (item.evidence_rows.length > 0) {
    const lastRow = 4 + item.evidence_rows.length;
    sheet.getRange(`A5:I${lastRow}`).values = item.evidence_rows;
    sheet.getRange(`A5:I${lastRow}`).format = {
      verticalAlignment: "top",
      wrapText: true,
    };
    const table = sheet.tables.add(
      `A4:I${lastRow}`,
      true,
      safeTableName(prefix, "Evidence", 1),
    );
    table.style = "TableStyleMedium2";
    table.showHeaders = true;
    table.showFilterButton = true;
    table.showBandedRows = true;
    sheet.getRange(`D5:D${lastRow}`).conditionalFormats.add("containsText", {
      text: "BLOCKING",
      format: { fill: "#FECACA", font: { bold: true, color: "#991B1B" } },
    });
    setColumns(sheet, lastRow, [22, 42, 24, 14, 12, 58, 32, 12, 14]);
  }
  return sheet;
}

export async function buildSyntheticWorkbooks(api, specPath) {
  const { SpreadsheetFile, Workbook } = api;
  const spec = JSON.parse(await fs.readFile(specPath, "utf8"));
  await fs.mkdir(spec.qa_output_root, { recursive: true });
  const results = [];

  for (let index = 0; index < spec.workbooks.length; index += 1) {
    const item = spec.workbooks[index];
    const workbook = Workbook.create();
    const prefix = `S2W${index + 1}`;
    addAboutSheet(workbook, item);
    addDataSheet(workbook, item, prefix);
    addEvidenceSheet(workbook, item, prefix);

    const inspection = await workbook.inspect({
      kind: "workbook,sheet,table",
      maxChars: 3500,
      tableMaxRows: 5,
      tableMaxCols: 8,
    });
    const errors = await workbook.inspect({
      kind: "match",
      searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
      options: { useRegex: true, maxResults: 100 },
      summary: "stage2 workbook error scan",
    });
    if (!errors.ndjson.includes("Cell search matched 0 entries.")) {
      throw new Error(
        `Workbook ${item.output_path} contains formula errors: ${errors.ndjson}`,
      );
    }
    for (const sheetName of ["说明", "数据", "字段证据"]) {
      const sheet = workbook.worksheets.getItem(sheetName);
      const used = sheet.getUsedRange();
      const values = used.values;
      const rowCount = Math.max(1, values.length);
      const columnCount = Math.max(1, ...values.map((row) => row.length));
      const previewRowCount = Math.min(rowCount, 30);
      const lastColumn = columnName(Math.min(columnCount, 12) - 1);
      const preview = await workbook.render({
        sheetName,
        range: `A1:${lastColumn}${previewRowCount}`,
        scale: sheetName === "说明" ? 1.2 : 0.85,
        format: "png",
      });
      const previewName = `${path.parse(item.output_path).name}-${sheetName}.png`;
      await fs.writeFile(
        path.join(spec.qa_output_root, previewName),
        new Uint8Array(await preview.arrayBuffer()),
      );
    }

    await fs.mkdir(path.dirname(item.output_path), { recursive: true });
    const output = await SpreadsheetFile.exportXlsx(workbook);
    await output.save(item.output_path);
    await fs.rm(`${item.output_path}.inspect.ndjson`, { force: true });
    results.push({
      output_path: item.output_path,
      inspection_records: inspection.recordCount,
      inspection_completed: inspection.recordCount > 0,
      error_scan: errors.ndjson,
    });
  }

  return results;
}
