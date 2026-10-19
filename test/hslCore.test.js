"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const {
  hasMethodMain,
  isSupportedLaunchTarget,
  maskCommentsAndStrings,
  resolveHslIncludeTarget,
} = require("../out/hslCore.js");

test("method-main detection ignores comments and string literals", () => {
  assert.equal(hasMethodMain('// method main() { }'), false);
  assert.equal(hasMethodMain('variable x = "method main()";'), false);
  assert.equal(hasMethodMain('namespace _Method {\nmethod main()\n{\n}\n}'), true);
});

test("masking preserves offsets and newlines", () => {
  const source = 'x(); /* comment\ncontinued */ y("text");';
  const masked = maskCommentsAndStrings(source);
  assert.equal(masked.length, source.length);
  assert.equal(masked.split("\n").length, source.split("\n").length);
  assert.match(masked, /x\(\);/);
  assert.match(masked, /y\(      \);/);
});

test("simulation rejects binary and library implementation targets", () => {
  assert.equal(isSupportedLaunchTarget("Method.hsl", true), true);
  assert.equal(isSupportedLaunchTarget("Method.sub", true), true);
  assert.equal(isSupportedLaunchTarget("Method.med", true), false);
  assert.equal(isSupportedLaunchTarget("Library.hs_", true), false);
  assert.equal(isSupportedLaunchTarget("Method.med", false), true);
});

test("include resolution prefers the including file before Hamilton roots", () => {
  const local = "C:\\work\\method\\Shared.hsl";
  const library = "C:\\Hamilton\\Library\\Shared.hsl";
  const existing = new Set([local.toLowerCase(), library.toLowerCase()]);
  const resolved = resolveHslIncludeTarget(
    "Shared.hsl",
    ["C:\\Hamilton\\Library"],
    "C:\\work\\method\\Main.hsl",
    (candidate) => existing.has(candidate.toLowerCase())
  );
  assert.equal(resolved, local);
});

test("clean-machine defaults do not require Hamilton or external runtimes", () => {
  const packageJson = require("../package.json");
  assert.equal(
    packageJson.contributes.configuration.properties["hsl.updateChecksumOnSave"].default,
    false
  );
  assert.equal(packageJson.dependencies, undefined);
  assert.equal(
    fs.existsSync(
      path.join(__dirname, "..", "HSL Debugger", "dist", "hsl_debugger", "hsl_debugger.exe")
    ),
    true
  );
});
