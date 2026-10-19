"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const root = path.resolve(__dirname, "..");
const required = [
  "out/extension.js",
  "out/diagnostics.js",
  "out/hslIntellisense.js",
  "out/hslDebugAdapter.js",
  "out/stpHoverProvider.js",
  "out/traceLanguageSupport.js",
  "out/hslMethodEditor.js",
  "out/hslCore.js",
  "out/AddCheckSum.exe",
  "firmware_commands.json",
  "HSL Debugger/dist/hsl_debugger/hsl_debugger.exe",
  "HSL Debugger/dist/hsl_debugger/source-manifest.json",
];

const missing = required.filter((relative) => !fs.existsSync(path.join(root, relative)));
if (missing.length > 0) {
  console.error("Package verification failed. Missing required files:");
  for (const relative of missing) {
    console.error(`  - ${relative}`);
  }
  process.exit(1);
}

const manifestPath = path.join(
  root,
  "HSL Debugger/dist/hsl_debugger/source-manifest.json"
);
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const staleSources = [];
for (const [fileName, expectedHash] of Object.entries(manifest.files ?? {})) {
  const sourcePath = path.join(root, "HSL Debugger/hsl_runtime", fileName);
  if (!fs.existsSync(sourcePath)) {
    staleSources.push(`${fileName} (missing source)`);
    continue;
  }
  const actualHash = crypto
    .createHash("sha256")
    .update(fs.readFileSync(sourcePath))
    .digest("hex");
  if (actualHash !== expectedHash) {
    staleSources.push(fileName);
  }
}

if (staleSources.length > 0) {
  console.error("Package verification failed. The bundled simulator is stale:");
  for (const fileName of staleSources) {
    console.error(`  - ${fileName}`);
  }
  console.error("Rebuild HSL Debugger/dist/hsl_debugger and refresh source-manifest.json.");
  process.exit(1);
}

console.log(`Package verification passed (${required.length} required files).`);
