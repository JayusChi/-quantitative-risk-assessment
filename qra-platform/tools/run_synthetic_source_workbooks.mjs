import path from "node:path";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

import { buildSyntheticWorkbooks } from "./build_synthetic_source_workbooks.mjs";

const specPath = process.argv[2];
const nodeModulesRoot = process.env.QRA_WORKSPACE_NODE_MODULES;
if (!specPath || !nodeModulesRoot) {
  throw new Error(
    "Usage: QRA_WORKSPACE_NODE_MODULES=<node_modules> node " +
      "run_synthetic_source_workbooks.mjs <workbook-spec.json>",
  );
}

const resolver = createRequire(
  pathToFileURL(path.join(nodeModulesRoot, "qra-artifact-tool-resolver.cjs")),
);
const artifactToolEntry = resolver.resolve("@oai/artifact-tool");
const { SpreadsheetFile, Workbook } = await import(pathToFileURL(artifactToolEntry).href);

const results = await buildSyntheticWorkbooks(
  { SpreadsheetFile, Workbook },
  specPath,
);
process.stdout.write(`__QRA_WORKBOOK_REPORT__${JSON.stringify(results, null, 2)}\n`);
