import * as vscode from "vscode";
import { BUILTIN_FUNCTIONS, ELEMENT_FUNCTIONS } from "./builtins";
import { getHslIndexService } from "./hslIntellisense";
import { execFile } from "child_process";
import * as path from "path";
import * as fs from "fs";

// -- Non-ASCII auto-replacement map -------------------------------------------
// Characters with known ASCII equivalents are replaced on save.
// Anything else non-ASCII is stripped entirely.
const NON_ASCII_REPLACEMENTS: Record<string, string> = {
  "\u2014": "-",   // em dash
  "\u2013": "-",   // en dash
  "\u00B5": "u",   // micro sign (mu)
  "\u03BC": "u",   // Greek small letter mu
  "\u2192": "->",  // rightwards arrow
  "\u2190": "<-",  // leftwards arrow
  "\u2194": "<->", // left right arrow
  "\u21D2": "=>",  // rightwards double arrow
  "\u21D0": "<=",  // leftwards double arrow
  "\u2018": "'",   // left single quote
  "\u2019": "'",   // right single quote
  "\u201C": "\"",  // left double quote
  "\u201D": "\"",  // right double quote
  "\u00A0": " ",   // non-breaking space
  "\u2026": "...", // horizontal ellipsis
  "\u00D7": "x",   // multiplication sign
  "\u00F7": "/",   // division sign
  "\u2264": "<=",  // less-than or equal to
  "\u2265": ">=",  // greater-than or equal to
  "\u2260": "!=",  // not equal to
};

/**
 * Replace known non-ASCII characters with their ASCII equivalents and
 * strip any remaining non-ASCII characters. Returns undefined if no
 * changes were needed.
 */
function sanitizeNonAscii(text: string): string | undefined {
  // Match any character outside printable ASCII (0x20-0x7E), tab, CR, LF
  const nonAsciiPattern = /[^\x09\x0A\x0D\x20-\x7E]/g;
  if (!nonAsciiPattern.test(text)) {
    return undefined; // nothing to do
  }

  // Replace all mapped characters, then strip anything else non-ASCII.
  let result = text;
  for (const [char, replacement] of Object.entries(NON_ASCII_REPLACEMENTS)) {
    result = result.split(char).join(replacement);
  }
  // Strip any remaining non-ASCII characters
  result = result.replace(/[^\x09\x0A\x0D\x20-\x7E]/g, "");
  return result;
}

const pendingChecksumUpdates = new Map<string, Promise<void>>();

function checksumKey(filePath: string): string {
  return path.normalize(filePath).toLowerCase();
}

/** Wait for a checksum update triggered by the most recent save, if any. */
export async function waitForPendingHslChecksum(filePath: string): Promise<void> {
  await pendingChecksumUpdates.get(checksumKey(filePath));
}

function startChecksumUpdate(
  context: vscode.ExtensionContext,
  filePath: string
): void {
  const addCheckSumExe = path.join(context.extensionPath, "out", "AddCheckSum.exe");
  if (!fs.existsSync(addCheckSumExe)) {
    vscode.window.showWarningMessage(
      `HSL checksum update skipped: AddCheckSum.exe was not packaged at ${addCheckSumExe}`
    );
    return;
  }

  const key = checksumKey(filePath);
  const update = new Promise<void>((resolve) => {
    execFile(addCheckSumExe, [filePath], { windowsHide: true }, (error) => {
      if (error) {
        vscode.window.showWarningMessage(`HSL checksum update failed: ${error.message}`);
      }
      resolve();
    });
  });
  pendingChecksumUpdates.set(key, update);
  void update.finally(() => {
    if (pendingChecksumUpdates.get(key) === update) {
      pendingChecksumUpdates.delete(key);
    }
  });
}

/**
 * Creates and returns a DiagnosticCollection that validates HSL syntax.
 * Currently checks for:
 *   - `=+` which should be `= +` (assign positive) or `= ++` (assign pre-increment)
 *   - `=-` which should be `= -` (assign negative) or `= --` (assign pre-decrement)
 *   - Non-ASCII characters (em dashes, en dashes, smart quotes, etc.)
 *   - Variable declarations that are not at the top of their code block
 *   - `continue` keyword usage (not supported in HSL)
 *   - Array element access used directly in `+` expressions (must assign to temp variable first)
 *   - Namespace-qualified variable access via `::` (only functions support `::`)
 */
export function createHslDiagnostics(
  context: vscode.ExtensionContext
): vscode.DiagnosticCollection {
  const diagnosticCollection =
    vscode.languages.createDiagnosticCollection("hsl");

  const refreshTimers = new Map<string, ReturnType<typeof setTimeout>>();

  const scheduleRefresh = (document: vscode.TextDocument, immediate = false): void => {
    const key = document.uri.toString();
    const existing = refreshTimers.get(key);
    if (existing) {
      clearTimeout(existing);
    }
    const expectedVersion = document.version;
    const timer = setTimeout(() => {
      refreshTimers.delete(key);
      void refreshDiagnostics(document, diagnosticCollection, expectedVersion).catch((error: unknown) => {
        const message = error instanceof Error ? error.message : String(error);
        console.error(`HSL diagnostics failed for ${document.uri.toString()}: ${message}`);
      });
    }, immediate ? 0 : 200);
    refreshTimers.set(key, timer);
  };

  // Run diagnostics on the active editor when the extension activates
  if (vscode.window.activeTextEditor?.document.languageId === "hsl") {
    scheduleRefresh(vscode.window.activeTextEditor.document, true);
  }

  // Re-run when a document is opened or its content changes
  context.subscriptions.push(
    vscode.workspace.onWillSaveTextDocument((event) => {
      const doc = event.document;
      if (
        doc.languageId !== "hsl" ||
        !vscode.workspace.getConfiguration("hsl", doc.uri).get<boolean>("autoSanitizeNonAscii", false)
      ) {
        return;
      }
      const cleaned = sanitizeNonAscii(doc.getText());
      if (cleaned === undefined) {
        return;
      }
      const fullRange = new vscode.Range(
        doc.positionAt(0),
        doc.positionAt(doc.getText().length)
      );
      event.waitUntil(Promise.resolve([vscode.TextEdit.replace(fullRange, cleaned)]));
    }),
    vscode.workspace.onDidSaveTextDocument((doc) => {
      if (
        doc.languageId === "hsl" &&
        doc.uri.scheme === "file" &&
        vscode.workspace.getConfiguration("hsl", doc.uri).get<boolean>("updateChecksumOnSave", false)
      ) {
        startChecksumUpdate(context, doc.fileName);
      }
    }),
    vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.document.languageId === "hsl") {
        scheduleRefresh(e.document);
      }
    }),
    vscode.workspace.onDidOpenTextDocument((doc) => {
      if (doc.languageId === "hsl") {
        scheduleRefresh(doc, true);
      }
    }),
    vscode.workspace.onDidCloseTextDocument((doc) => {
      const key = doc.uri.toString();
      const timer = refreshTimers.get(key);
      if (timer) {
        clearTimeout(timer);
        refreshTimers.delete(key);
      }
      diagnosticCollection.delete(doc.uri);
    }),
    { dispose: () => refreshTimers.forEach((timer) => clearTimeout(timer)) },
    diagnosticCollection
  );

  return diagnosticCollection;
}

/**
 * Analyse the full document and publish diagnostics.
 */
async function refreshDiagnostics(
  document: vscode.TextDocument,
  collection: vscode.DiagnosticCollection,
  expectedVersion: number
): Promise<void> {
  const diagnostics: vscode.Diagnostic[] = [];

  // Patterns: match `=+` or `=-` that are NOT part of `+=`, `-=`, `=++`, `=--`, `==`, `!=`, `<=`, `>=`
  //   (?<![+\-!=<>])   -- `=` must NOT be preceded by another operator character
  //   =                -- the literal `=`
  //   \+(?!\+|=)       -- a `+` NOT followed by another `+` or `=`  (avoids `=++` and `+=`)
  //   -(?!-|=)         -- a `-` NOT followed by another `-` or `=`  (avoids `=--` and `-=`)
  const equalsPlusPattern = /(?<![+\-!=<>])=\+(?!\+|=)/g;
  const equalsMinusPattern = /(?<![+\-!=<>])=-(?!-|=)/g;

  const fullText = document.getText();

  // Build a set of ranges that are inside strings or comments so we can skip them
  const ignoredRanges = getIgnoredRanges(fullText);

  for (let lineIndex = 0; lineIndex < document.lineCount; lineIndex++) {
    const line = document.lineAt(lineIndex);
    const lineText = line.text;
    const lineOffset = document.offsetAt(line.range.start);

    let match: RegExpExecArray | null;

    // Check for =+
    equalsPlusPattern.lastIndex = 0;
    while ((match = equalsPlusPattern.exec(lineText)) !== null) {
      const absoluteOffset = lineOffset + match.index;
      if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
        continue;
      }
      const range = new vscode.Range(
        lineIndex,
        match.index,
        lineIndex,
        match.index + match[0].length
      );
      const diag = new vscode.Diagnostic(
        range,
        "'=+' is not valid HSL. Did you mean '= +' (assign positive value) or '= ++' (assign pre-increment)? Note: HSL does not have compound assignment operators like '+='.",
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "invalid-equals-plus";
      diagnostics.push(diag);
    }

    // Check for =-
    equalsMinusPattern.lastIndex = 0;
    while ((match = equalsMinusPattern.exec(lineText)) !== null) {
      const absoluteOffset = lineOffset + match.index;
      if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
        continue;
      }
      const range = new vscode.Range(
        lineIndex,
        match.index,
        lineIndex,
        match.index + match[0].length
      );
      const diag = new vscode.Diagnostic(
        range,
        "'=-' is not valid HSL. Did you mean '= -' (assign negative value) or '= --' (assign pre-decrement)? Note: HSL does not have compound assignment operators like '-='.",
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "invalid-equals-minus";
      diagnostics.push(diag);
    }
  }

  // Check for non-ASCII characters (em dashes, en dashes, smart quotes, etc.)
  checkNonAsciiCharacters(document, diagnostics);

  // Check for unsupported 'continue' keyword
  checkContinueKeyword(document, ignoredRanges, diagnostics);

  // `next` is only valid after `resume`, never as a loop statement.
  checkStandaloneNext(document, ignoredRanges, diagnostics);

  // Check for standalone 'goto' (only 'onerror goto' is valid in HSL)
  checkStandaloneGoto(document, ignoredRanges, diagnostics);

  // Compute lines inside #ifndef HSL_RUNTIME ... #endif blocks once,
  // so both variable-placement and declaration-pairing checks can use it.
  const hslRuntimeGuardedLines = getHslRuntimeGuardedLines(document, ignoredRanges);

  // Check that all variable declarations are at the top of their scope
  checkVariableDeclarationPlacement(document, ignoredRanges, diagnostics, hslRuntimeGuardedLines);

  // Check function call argument counts against known signatures
  await checkFunctionCallArity(document, diagnostics);

  // Check that every function has both a declaration (prototype) and definition (implementation)
  checkFunctionDeclarationDefinitionPairing(document, ignoredRanges, diagnostics, hslRuntimeGuardedLines);

  // Check for string-only member functions called on non-string types
  checkStringMemberOnWrongType(document, ignoredRanges, diagnostics);

  // Check that `string` values are not used with the variable-only '+' operator.
  checkStringConcatenation(document, ignoredRanges, diagnostics);

  // NOTE: Anonymous blocks with declarations at the top are valid VENUS code.
  // The Hamilton Method Editor generates `{ variable arrRetValues[]; ... }`
  // blocks for every device step.  The real constraint -- declarations must
  // precede executable code in any scope -- is enforced by
  // checkVariableDeclarationPlacement, which already tracks per-block scope.

  // Check for array element access used directly in + expressions
  checkArrayElementInExpression(document, ignoredRanges, diagnostics);

  // Check that ML_STAR is initialized before use
  checkInitializeBeforeDeviceUse(document, ignoredRanges, diagnostics);

  // Check for namespace-qualified variable access (only functions support ::)
  checkNamespaceQualifiedVariableAccess(document, ignoredRanges, diagnostics);

  if (!document.isClosed && document.version === expectedVersion) {
    collection.set(document.uri, diagnostics);
  }
}

// ── Function call arity enforcement ──────────────────────────────────────

interface ArityRule {
  minArgs: number;
  maxArgs: number;
  variadic: boolean;
}

function buildBuiltinArityMap(): Map<string, ArityRule> {
  const map = new Map<string, ArityRule>();
  for (const fn of BUILTIN_FUNCTIONS) {
    map.set(fn.name.toLowerCase(), parseSignatureArity(fn.signature));
  }
  return map;
}

const BUILTIN_ARITY_MAP = buildBuiltinArityMap();

function buildElementMethodArityMap(): Map<string, ArityRule[]> {
  const map = new Map<string, ArityRule[]>();
  for (const fn of ELEMENT_FUNCTIONS) {
    const key = fn.name.toLowerCase();
    const rule = parseSignatureArity(fn.signature);
    const existing = map.get(key);
    if (existing) {
      if (!existing.some((entry) => areArityRulesEqual(entry, rule))) {
        existing.push(rule);
      }
      continue;
    }
    map.set(key, [rule]);
  }
  return map;
}

const ELEMENT_METHOD_ARITY_MAP = buildElementMethodArityMap();

async function checkFunctionCallArity(
  document: vscode.TextDocument,
  diagnostics: vscode.Diagnostic[]
): Promise<void> {
  const fullText = document.getText();
  const cleanText = buildArityMaskedText(fullText);

  const localArity = extractLocalFunctionArity(cleanText);

  // Collect ALL library arity rules per simple function name so that method
  // calls can be validated against every known overload, not just the first.
  const libraryArityByName = new Map<string, ArityRule[]>();

  // Extend arity map with symbols from included library files.
  // IMPORTANT: system-defined names (builtins + element methods) must NOT be
  // overridden by library-parsed symbols.  Library files sometimes re-declare
  // wrappers with fewer parameters than the real system function accepts.
  const indexService = getHslIndexService();
  if (indexService) {
    try {
      const visible = await indexService.getVisibleSymbolContext(document);
      for (const symbol of visible.symbols) {
        const arity: ArityRule = {
          minArgs: symbol.parameters.length,
          maxArgs: symbol.parameters.length,
          variadic: false,
        };
        const qualifiedKey = symbol.qualifiedName.toLowerCase();
        const simpleKey = symbol.name.toLowerCase();

        // Skip library symbols whose simple name collides with a system-
        // defined element method (e.g. CreateObject, Open, ReleaseObject).
        // The ELEMENT_METHOD_ARITY_MAP already contains the correct, more
        // permissive arity for these -- letting the library override it is
        // the root cause of false-positive diagnostics.
        const isSystemElementMethod = ELEMENT_METHOD_ARITY_MAP.has(simpleKey);

        if (!isSystemElementMethod) {
          if (!localArity.has(qualifiedKey)) {
            localArity.set(qualifiedKey, arity);
          }
          if (!localArity.has(simpleKey) && !BUILTIN_ARITY_MAP.has(simpleKey)) {
            localArity.set(simpleKey, arity);
          }
        }

        // Always accumulate every unique arity rule per simple name so
        // the method-call fallback has the most complete picture.
        const existing = libraryArityByName.get(simpleKey);
        if (existing) {
          if (!existing.some((e) => areArityRulesEqual(e, arity))) {
            existing.push(arity);
          }
        } else {
          libraryArityByName.set(simpleKey, [arity]);
        }
      }
    } catch {
      // index not ready yet -- continue with local-only checking
    }
  }

  const callPattern = /\b([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*)\s*\(/g;
  let match: RegExpExecArray | null;

  while ((match = callPattern.exec(cleanText)) !== null) {
    const qualifiedName = match[1];
    const nameStart = match.index;
    const openParenIndex = callPattern.lastIndex - 1;

    if (isLikelyDeclarationContext(cleanText, nameStart)) {
      continue;
    }

    if (nameStart > 0 && cleanText[nameStart - 1] === ".") {
      continue;
    }

    const closeParenIndex = findMatchingParen(cleanText, openParenIndex);
    if (closeParenIndex < 0) {
      continue;
    }

    const innerArgsText = fullText.slice(openParenIndex + 1, closeParenIndex);
    const argCount = countTopLevelArguments(
      innerArgsText
    );

    const fnName = qualifiedName.split("::").pop() ?? qualifiedName;
    const qualifiedKey = qualifiedName.toLowerCase();
    const simpleKey = fnName.toLowerCase();
    const rule =
      localArity.get(qualifiedKey) ??
      localArity.get(simpleKey) ??
      BUILTIN_ARITY_MAP.get(simpleKey);
    if (!rule) {
      continue;
    }

    if (argCount < rule.minArgs || (!rule.variadic && argCount > rule.maxArgs)) {
      // Before flagging, check whether a system element function with the
      // same simple name would accept this arg count.  Library files can
      // define wrappers like Object::CreateObject(programId) with fewer
      // params than the real system function (which also takes withEvents).
      const elementRules = ELEMENT_METHOD_ARITY_MAP.get(simpleKey);
      if (elementRules && elementRules.some((r) => isArityValid(r, argCount))) {
        continue;
      }

      const linePos = document.positionAt(nameStart);
      const range = new vscode.Range(
        linePos.line,
        linePos.character,
        linePos.line,
        linePos.character + qualifiedName.length
      );

      const expected = formatExpectedArity(rule);
      const diagnostic = new vscode.Diagnostic(
        range,
        `Function '${qualifiedName}' expects ${expected}, but ${argCount} argument${argCount === 1 ? "" : "s"} ${argCount === 1 ? "was" : "were"} provided.`,
        vscode.DiagnosticSeverity.Error
      );
      diagnostic.source = "hsl";
      diagnostic.code = "invalid-function-arity";
      diagnostics.push(diagnostic);
      continue;
    }

    if (simpleKey === "gettime" || simpleKey === "getdate") {
      const args = splitTopLevelArguments(innerArgsText);
      if (args.length === 1 && isObviouslyNonStringLiteral(args[0])) {
        const linePos = document.positionAt(nameStart);
        const range = new vscode.Range(
          linePos.line,
          linePos.character,
          linePos.line,
          linePos.character + qualifiedName.length
        );

        const diagnostic = new vscode.Diagnostic(
          range,
          `Function '${qualifiedName}' requires a string 'format' argument.`,
          vscode.DiagnosticSeverity.Error
        );
        diagnostic.source = "hsl";
        diagnostic.code = "invalid-format-argument";
        diagnostics.push(diagnostic);
      }
    }
  }

  // Collect identifiers declared as `object` type so we can skip arity
  // checking on their method calls -- COM objects define their own methods
  // which cannot be statically validated.
  const objectTypedVars = new Set<string>();
  const objectDeclPattern = /\bobject\s+(\w+)/g;
  let objMatch: RegExpExecArray | null;
  while ((objMatch = objectDeclPattern.exec(cleanText)) !== null) {
    objectTypedVars.add(objMatch[1]);
  }

  const methodCallPattern = /\b([A-Za-z_]\w*)(?:\[[^\]]+\])?\s*\.\s*([A-Za-z_]\w*)\s*\(/g;
  while ((match = methodCallPattern.exec(cleanText)) !== null) {
    const receiverName = match[1];
    const methodName = match[2];
    // Find the actual start of the captured method name in the matched text
    const fullMatchText = match[0];
    const capturedOffset = fullMatchText.lastIndexOf(methodName);
    const methodNameIndex = match.index + capturedOffset;

    // Skip arity validation for COM objects -- they define their own methods
    if (objectTypedVars.has(receiverName)) {
      continue;
    }

    const openParenIndex = methodCallPattern.lastIndex - 1;
    const closeParenIndex = findMatchingParen(cleanText, openParenIndex);
    if (closeParenIndex < 0) {
      continue;
    }

    const innerArgsText = fullText.slice(openParenIndex + 1, closeParenIndex);
    const argCount = countTopLevelArguments(
      innerArgsText
    );

    const methodKey = methodName.toLowerCase();
    const rules = ELEMENT_METHOD_ARITY_MAP.get(methodKey);
    if (!rules || rules.length === 0) {
      // Method is not a known element method -- skip; we cannot validate it.
      continue;
    }

    const hasMatch = rules.some((rule) => isArityValid(rule, argCount));
    if (hasMatch) {
      continue;
    }

    // Also check library-defined functions with the same simple name.
    // The extension cannot determine the receiver type on a `.Method()` call,
    // so if any known function with this name accepts this arg count, allow it.
    const libraryRules = libraryArityByName.get(methodKey);
    if (libraryRules && libraryRules.some((rule) => isArityValid(rule, argCount))) {
      continue;
    }

    // Because this arity mismatch can prevent execution, report it as an
    // Error even though unresolved receiver typing can still cause
    // occasional false positives.
    const linePos = document.positionAt(methodNameIndex);
    const range = new vscode.Range(
      linePos.line,
      linePos.character,
      linePos.line,
      linePos.character + methodName.length
    );

    const expected = formatExpectedArityOptions(rules);
    const diagnostic = new vscode.Diagnostic(
      range,
      `Method '${methodName}' expects ${expected}, but ${argCount} argument${argCount === 1 ? "" : "s"} ${argCount === 1 ? "was" : "were"} provided. Note: the receiver's object type could not be determined; this may be a false positive if the object defines its own '${methodName}' method.`,
      vscode.DiagnosticSeverity.Error
    );
    diagnostic.source = "hsl";
    diagnostic.code = "invalid-method-arity";
    diagnostics.push(diagnostic);
  }
}

function parseSignatureArity(signature: string): ArityRule {
  const trimmed = signature.trim();
  const openParen = trimmed.indexOf("(");
  const closeParen = trimmed.lastIndexOf(")");
  const inner =
    openParen >= 0 && closeParen > openParen
      ? trimmed.slice(openParen + 1, closeParen)
      : trimmed;

  const outsideText = removeBracketGroups(inner);
  const insideGroups = extractBracketGroups(inner);

  const outside = parseArityText(outsideText);
  let optionalCount = 0;
  let variadic = outside.variadic;

  for (const group of insideGroups) {
    const parsed = parseArityText(group);
    optionalCount += parsed.count;
    variadic = variadic || parsed.variadic;
  }

  return {
    minArgs: outside.count,
    maxArgs: variadic ? Number.POSITIVE_INFINITY : outside.count + optionalCount,
    variadic,
  };
}

function removeBracketGroups(text: string): string {
  let result = "";
  let bracketDepth = 0;
  for (const ch of text) {
    if (ch === "[") {
      bracketDepth++;
      continue;
    }
    if (ch === "]") {
      bracketDepth = Math.max(0, bracketDepth - 1);
      continue;
    }
    if (bracketDepth === 0) {
      result += ch;
    }
  }
  return result;
}

function extractBracketGroups(text: string): string[] {
  const groups: string[] = [];
  let bracketDepth = 0;
  let current = "";

  for (const ch of text) {
    if (ch === "[") {
      if (bracketDepth === 0) {
        current = "";
      } else {
        current += ch;
      }
      bracketDepth++;
      continue;
    }

    if (ch === "]") {
      if (bracketDepth > 1) {
        current += ch;
      }
      bracketDepth = Math.max(0, bracketDepth - 1);
      if (bracketDepth === 0) {
        groups.push(current);
      }
      continue;
    }

    if (bracketDepth > 0) {
      current += ch;
    }
  }

  return groups;
}

function parseArityText(text: string): { count: number; variadic: boolean } {
  const parts = text
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p.length > 0);

  let count = 0;
  let variadic = false;

  for (const token of parts) {
    if (token.includes("...")) {
      variadic = true;
      continue;
    }
    count++;
  }

  return { count, variadic };
}

function areArityRulesEqual(left: ArityRule, right: ArityRule): boolean {
  return (
    left.minArgs === right.minArgs &&
    left.maxArgs === right.maxArgs &&
    left.variadic === right.variadic
  );
}

function extractLocalFunctionArity(cleanText: string): Map<string, ArityRule> {
  const map = new Map<string, ArityRule>();
  const pattern =
    /(^|\n)\s*(?:(?:private|public|static|global|const|synchronized)\s+)*function\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*[A-Za-z_]\w*\s*(?:;|\{)/g;

  let match: RegExpExecArray | null;
  while ((match = pattern.exec(cleanText)) !== null) {
    const name = match[2].toLowerCase();
    const params = match[3]
      .split(",")
      .map((p) => p.trim())
      .filter((p) => p.length > 0 && p !== "void");

    map.set(name, {
      minArgs: params.length,
      maxArgs: params.length,
      variadic: false,
    });
  }

  return map;
}

function isLikelyDeclarationContext(cleanText: string, nameStart: number): boolean {
  const contextStart = Math.max(0, nameStart - 80);
  const prefix = cleanText.slice(contextStart, nameStart);
  if (/\b(function|method|namespace)\s+$/.test(prefix)) {
    return true;
  }
  // If the line starts with "variable " or "variable& ", this is a variable
  // declaration -- parentheses are used for initialisation, not function calls.
  const lineStart = cleanText.lastIndexOf("\n", nameStart - 1) + 1;
  const linePrefix = cleanText.slice(lineStart, nameStart).trimStart();
  if (/^variable&?\s/i.test(linePrefix)) {
    return true;
  }
  return false;
}

function findMatchingParen(text: string, openParenIndex: number): number {
  let depth = 0;
  for (let i = openParenIndex; i < text.length; i++) {
    const ch = text[i];
    if (ch === "(") {
      depth++;
      continue;
    }
    if (ch === ")") {
      depth--;
      if (depth === 0) {
        return i;
      }
    }
  }
  return -1;
}

function countTopLevelArguments(innerArgs: string): number {
  return splitTopLevelArguments(innerArgs).length;
}

function formatExpectedArity(rule: ArityRule): string {
  if (rule.variadic) {
    if (rule.minArgs === 0) {
      return "zero or more arguments";
    }
    return `${rule.minArgs} or more arguments`;
  }

  if (rule.minArgs === rule.maxArgs) {
    if (rule.minArgs === 1) {
      return "exactly 1 argument";
    }
    return `exactly ${rule.minArgs} arguments`;
  }

  return `${rule.minArgs} to ${rule.maxArgs} arguments`;
}

function isArityValid(rule: ArityRule, argCount: number): boolean {
  if (argCount < rule.minArgs) {
    return false;
  }
  if (!rule.variadic && argCount > rule.maxArgs) {
    return false;
  }
  return true;
}

function formatExpectedArityOptions(rules: ArityRule[]): string {
  const uniqueLabels = Array.from(
    new Set(rules.map((rule) => formatExpectedArity(rule)))
  );

  if (uniqueLabels.length === 1) {
    return uniqueLabels[0];
  }

  return `one of: ${uniqueLabels.join("; ")}`;
}

function splitTopLevelArguments(innerArgs: string): string[] {
  if (innerArgs.trim() === "") {
    return [];
  }

  const args: string[] = [];
  let depthParen = 0;
  let depthBracket = 0;
  let depthBrace = 0;
  let current = "";

  let inString = false;
  let inLineComment = false;
  let inBlockComment = false;
  let escaped = false;

  for (let i = 0; i < innerArgs.length; i++) {
    const ch = innerArgs[i];
    const next = i + 1 < innerArgs.length ? innerArgs[i + 1] : "";

    if (inLineComment) {
      current += ch;
      if (ch === "\n") {
        inLineComment = false;
      }
      continue;
    }

    if (inBlockComment) {
      current += ch;
      if (ch === "*" && next === "/") {
        current += next;
        i++;
        inBlockComment = false;
      }
      continue;
    }

    if (inString) {
      current += ch;
      if (escaped) {
        escaped = false;
        continue;
      }
      if (ch === "\\") {
        escaped = true;
        continue;
      }
      if (ch === '"') {
        inString = false;
      }
      continue;
    }

    if (ch === '"') {
      current += ch;
      inString = true;
      escaped = false;
      continue;
    }

    if (ch === "/" && next === "/") {
      current += ch + next;
      i++;
      inLineComment = true;
      continue;
    }

    if (ch === "/" && next === "*") {
      current += ch + next;
      i++;
      inBlockComment = true;
      continue;
    }

    if (ch === "(") {
      depthParen++;
      current += ch;
      continue;
    }
    if (ch === ")") {
      depthParen = Math.max(0, depthParen - 1);
      current += ch;
      continue;
    }
    if (ch === "[") {
      depthBracket++;
      current += ch;
      continue;
    }
    if (ch === "]") {
      depthBracket = Math.max(0, depthBracket - 1);
      current += ch;
      continue;
    }
    if (ch === "{") {
      depthBrace++;
      current += ch;
      continue;
    }
    if (ch === "}") {
      depthBrace = Math.max(0, depthBrace - 1);
      current += ch;
      continue;
    }

    if (ch === "," && depthParen === 0 && depthBracket === 0 && depthBrace === 0) {
      args.push(current.trim());
      current = "";
      continue;
    }

    current += ch;
  }

  if (current.trim().length > 0) {
    args.push(current.trim());
  }

  return args;
}

function isObviouslyNonStringLiteral(argumentText: string): boolean {
  const arg = argumentText.trim();
  if (arg.length === 0) {
    return false;
  }

  if (/^"(?:[^"\\]|\\.)*"$/.test(arg)) {
    return false;
  }

  if (/^[+-]?(?:\d+\.\d*|\d*\.\d+|\d+)$/.test(arg)) {
    return true;
  }

  if (/^(hslTrue|hslFalse)$/i.test(arg)) {
    return true;
  }

  return false;
}

function buildMaskedText(text: string, ranges: OffsetRange[]): string {
  if (ranges.length === 0) {
    return text;
  }

  const chars = [...text];
  for (const range of ranges) {
    for (let i = range.start; i < range.end && i < chars.length; i++) {
      if (chars[i] !== "\n" && chars[i] !== "\r") {
        chars[i] = " ";
      }
    }
  }
  return chars.join("");
}

// ── Variable-declaration-at-top enforcement ─────────────────────────────

/**
 * Matches a variable declaration line: optional HSL storage modifiers
 * followed by a type keyword and the start of an identifier.
 */
const DECL_PATTERN = new RegExp(
  "^\\s*(?:(?:private|static|const|global|synchronized)\\s+)*" +
    "(?:variable|string|sequence|device|resource|dialog|object|" +
    "timer|event|file|char|short|long|float)\\s+\\w"
);

/** Matches a function or method header (with optional leading modifiers). */
const FUNC_METHOD_HEADER =
  /^\s*(?:(?:private|static|const|global|synchronized)\s+)*(?:function|method)\b/;

/** Matches a namespace header (with optional leading modifiers). */
const NAMESPACE_HEADER =
  /^\s*(?:(?:private|static|const|global|synchronized)\s+)*namespace\b/;

/** Matches a struct header (with optional leading modifiers). */
const STRUCT_HEADER =
  /^\s*(?:(?:private|static|const|global|synchronized)\s+)*struct\b/;

/** Matches a preprocessor directive. */
const PREPROCESSOR_LINE = /^\s*#/;

/**
 * Returns the set of 0-based line numbers that fall inside
 * `#ifndef HSL_RUNTIME ... #endif` blocks.  These blocks contain
 * design-time stubs generated by the Hamilton Method Editor and must
 * not trigger false-positive diagnostics for missing declarations or
 * out-of-order variable placement.
 */
function getHslRuntimeGuardedLines(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[]
): Set<number> {
  const guarded = new Set<number>();
  let insideGuard = false;
  let guardDepth = 0; // nesting depth of #if / #ifdef / #ifndef

  for (let i = 0; i < document.lineCount; i++) {
    const lineOffset = document.offsetAt(document.lineAt(i).range.start);
    let clean = "";
    for (let ci = 0; ci < document.lineAt(i).text.length; ci++) {
      clean += isInsideIgnoredRange(lineOffset + ci, ignoredRanges)
        ? " "
        : document.lineAt(i).text[ci];
    }
    const trimmed = clean.trim();

    if (!insideGuard) {
      // Look for #ifndef HSL_RUNTIME
      if (/^#\s*ifndef\s+HSL_RUNTIME\b/.test(trimmed)) {
        insideGuard = true;
        guardDepth = 1;
        guarded.add(i);
      }
    } else {
      guarded.add(i);
      // Track nesting of #if / #ifdef / #ifndef inside the guard
      if (/^#\s*(?:if|ifdef|ifndef)\b/.test(trimmed)) {
        guardDepth++;
      } else if (/^#\s*endif\b/.test(trimmed)) {
        guardDepth--;
        if (guardDepth <= 0) {
          insideGuard = false;
        }
      }
    }
  }
  return guarded;
}

/**
 * Matches a one-line namespace wrapper used purely for `#include` preamble,
 * e.g. `namespace _Method { #include "Lib\\File.hsl" }`.
 */
const INLINE_INCLUDE_NAMESPACE =
  /^\s*(?:(?:private|static|const|global|synchronized)\s+)*namespace\b[^{}]*\{\s*#\s*include\b[^{}]*\}\s*;?\s*$/i;

type ScopeKind =
  | "file"
  | "function"
  | "method"
  | "namespace"
  | "struct"
  | "block";

interface ScopeState {
  /** Brace depth at which this scope's opening `{` was counted. */
  braceDepth: number;
  /** True once a non-declaration statement has been seen at this scope level. */
  sawCode: boolean;
  /** Human-readable scope kind (used in diagnostic messages). */
  kind: ScopeKind;
}

// ── Non-ASCII character detection ────────────────────────────────────────

/**
 * Flags non-ASCII characters (em dashes, en dashes, smart quotes, etc.)
 * that cause silent corruption or compile failures in the VENUS toolchain.
 * The VENUS compiler reads files as ANSI/Windows-1252, so UTF-8 multi-byte
 * sequences (e.g. em dash U+2014 = 0xE2 0x80 0x94) are misinterpreted --
 * 0x94 in Windows-1252 is a right double quotation mark, which can corrupt
 * the parser state and cause cascading syntax errors.
 *
 * Unlike other checks, this flags characters even inside comments and
 * strings, because the VENUS toolchain processes raw bytes before parsing.
 */
function checkNonAsciiCharacters(
  document: vscode.TextDocument,
  diagnostics: vscode.Diagnostic[]
): void {
  // Match any character outside printable ASCII (0x20-0x7E), tab (0x09), CR, LF
  const nonAsciiPattern = /[^\x09\x0A\x0D\x20-\x7E]/g;

  // Map common offenders to friendly names
  const CHAR_NAMES: Record<string, string> = {
    "\u2014": "em dash",
    "\u2013": "en dash",
    "\u2018": "left single quote",
    "\u2019": "right single quote",
    "\u201C": "left double quote",
    "\u201D": "right double quote",
    "\u00A0": "non-breaking space",
    "\u2192": "right arrow",
  };

  for (let lineIndex = 0; lineIndex < document.lineCount; lineIndex++) {
    const lineText = document.lineAt(lineIndex).text;

    // Skip the checksum trailer line (contains $$ delimited metadata)
    if (/^\s*\/\/\s*\$\$author=/.test(lineText)) {
      continue;
    }

    nonAsciiPattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = nonAsciiPattern.exec(lineText)) !== null) {
      const char = match[0];
      const codePoint = char.codePointAt(0) ?? 0;
      const hex = `U+${codePoint.toString(16).toUpperCase().padStart(4, "0")}`;
      const friendlyName = CHAR_NAMES[char];
      const nameStr = friendlyName ? ` (${friendlyName})` : "";

      const range = new vscode.Range(
        lineIndex,
        match.index,
        lineIndex,
        match.index + char.length
      );
      const diag = new vscode.Diagnostic(
        range,
        `Non-ASCII character ${hex}${nameStr} will corrupt VENUS compilation. ` +
          `The VENUS toolchain reads files as ANSI/Windows-1252 and cannot ` +
          `handle UTF-8 multi-byte characters. Replace with an ASCII equivalent ` +
          `(e.g. use '--' instead of em dash, '-' instead of en dash).`,
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "non-ascii-character";
      diagnostics.push(diag);
    }
  }
}

/**
 * Flags any use of the `continue` keyword as a syntax error.
 * HSL does not support `continue` in loops or conditionals.
 */
function checkContinueKeyword(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const continuePattern = /\bcontinue\b/gi;

  for (let lineIndex = 0; lineIndex < document.lineCount; lineIndex++) {
    const line = document.lineAt(lineIndex);
    const lineText = line.text;
    const lineOffset = document.offsetAt(line.range.start);

    continuePattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = continuePattern.exec(lineText)) !== null) {
      const absoluteOffset = lineOffset + match.index;
      if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
        continue;
      }
      const range = new vscode.Range(
        lineIndex,
        match.index,
        lineIndex,
        match.index + match[0].length
      );
      const diag = new vscode.Diagnostic(
        range,
        "'continue' is not a valid HSL keyword. HSL does not support 'continue' in loops or conditionals.",
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "invalid-continue";
      diagnostics.push(diag);
    }
  }
}

/**
 * Detects standalone `goto <label>;` statements that are NOT part of
 * `onerror goto`.  HSL does **not** have a general-purpose `goto`
 * statement -- only `onerror goto <label>;` and `onerror goto 0;`
 * are valid.  A standalone `goto` causes VENUS error 1206.
 *
 * The recommended fix is to restructure the error handling using one of:
 *   - **Fall-through pattern**: let both success and error paths converge
 *     at the label block (e.g., `_myLabel: { err.Clear(); onerror goto 0; }`)
 *   - **Return pattern**: use `return;` before the error label on the
 *     success path, so the label block is only reached on error.
 */
function checkStandaloneGoto(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  // Match `goto <identifier>;` but NOT `onerror goto`.
  // We look for `goto` preceded by something other than `onerror`.
  const gotoPattern = /\bgoto\b/gi;
  const onerrorPrefix = /\bonerror\s+$/i;

  for (let lineIndex = 0; lineIndex < document.lineCount; lineIndex++) {
    const line = document.lineAt(lineIndex);
    const lineText = line.text;
    const lineOffset = document.offsetAt(line.range.start);

    gotoPattern.lastIndex = 0;
    let match: RegExpExecArray | null;
    while ((match = gotoPattern.exec(lineText)) !== null) {
      const absoluteOffset = lineOffset + match.index;
      if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
        continue;
      }
      // Check whether this `goto` is preceded by `onerror` on the same line
      const textBefore = lineText.substring(0, match.index);
      if (onerrorPrefix.test(textBefore)) {
        continue; // `onerror goto` is valid -- skip
      }
      const range = new vscode.Range(
        lineIndex,
        match.index,
        lineIndex,
        match.index + match[0].length
      );
      const diag = new vscode.Diagnostic(
        range,
        "Standalone 'goto' is not valid HSL. Only 'onerror goto <label>' and " +
          "'onerror goto 0' are supported. Use the fall-through pattern " +
          "(let both paths reach the label block) or return before the label " +
          "to skip the error handler on success.",
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "standalone-goto";
      diagnostics.push(diag);
    }
  }
}

/**
 * Ensures every variable declaration sits at the **top** of its enclosing
 * code block (`{ ... }`, function/method/namespace/struct body, or file scope).
 * A declaration is flagged only when it appears *after* executable code in the
 * same block.
 */
function checkVariableDeclarationPlacement(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[],
  hslRuntimeGuardedLines: Set<number> = new Set()
): void {
  const includePreambleLines = collectIncludePreambleNamespaceLines(
    document,
    ignoredRanges
  );

  // The file itself is the outermost declaration scope.
  const scopeStack: ScopeState[] = [
    { braceDepth: 0, sawCode: false, kind: "file" },
  ];
  let braceDepth = 0;
  let pendingKind: ScopeKind | null = null;

  for (let lineIdx = 0; lineIdx < document.lineCount; lineIdx++) {
    const line = document.lineAt(lineIdx);
    const lineOffset = document.offsetAt(line.range.start);
    const lineStartDepth = braceDepth;
    const scopeAtLineStart = scopeStack[scopeStack.length - 1];

    // Build a version of the line with comments / strings blanked out so
    // that keywords inside literals don't trigger false positives.
    let clean = "";
    for (let ci = 0; ci < line.text.length; ci++) {
      clean += isInsideIgnoredRange(lineOffset + ci, ignoredRanges)
        ? " "
        : line.text[ci];
    }

    if (includePreambleLines.has(lineIdx)) {
      for (let ci = 0; ci < clean.length; ci++) {
        if (clean[ci] === "{") {
          braceDepth++;
          continue;
        }
        if (clean[ci] === "}") {
          braceDepth = Math.max(0, braceDepth - 1);
        }
      }
      continue;
    }

    const trimmed = clean.trim();
    if (
      trimmed === "" ||
      PREPROCESSOR_LINE.test(trimmed) ||
      INLINE_INCLUDE_NAMESPACE.test(trimmed)
    ) {
      continue;
    }

    // ── Phase 1: detect scope-opening headers ──────────────────────
    let isScopeHeader = false;

    if (FUNC_METHOD_HEADER.test(trimmed)) {
      pendingKind = /\bfunction\b/.test(trimmed) ? "function" : "method";
      isScopeHeader = true;
    } else if (NAMESPACE_HEADER.test(trimmed)) {
      pendingKind = "namespace";
      isScopeHeader = true;
    } else if (STRUCT_HEADER.test(trimmed)) {
      pendingKind = "struct";
      isScopeHeader = true;
    }

    if (isScopeHeader) {
      // A function or method definition counts as "code" in the enclosing
      // scope, so later declarations in that enclosing scope are invalid.
      // Namespace (and struct) headers do NOT -- variables may still appear
      // after namespace blocks as long as no functions have been defined.
      //
      // Inside #ifndef HSL_RUNTIME blocks, function stubs are design-time
      // placeholders generated by Hamilton -- they should not flip sawCode
      // because subsequent global variable declarations are legitimate.
      const isInsideHslRuntimeGuard = hslRuntimeGuardedLines.has(lineIdx);
      const enclosing = scopeStack[scopeStack.length - 1];
      if (
        enclosing &&
        braceDepth === enclosing.braceDepth &&
        pendingKind !== "namespace" &&
        pendingKind !== "struct" &&
        !isInsideHslRuntimeGuard
      ) {
        enclosing.sawCode = true;
      }
    }

    // ── Phase 2: track braces ──────────────────────────────────────
    for (let ci = 0; ci < clean.length; ci++) {
      if (clean[ci] === "{") {
        braceDepth++;
        scopeStack.push({
          braceDepth,
          sawCode: false,
          kind: pendingKind ?? "block",
        });
        pendingKind = null;
      } else if (clean[ci] === "}") {
        const top = scopeStack[scopeStack.length - 1];
        if (top && braceDepth === top.braceDepth) {
          scopeStack.pop();
        }
        braceDepth--;
      }
    }

    // Skip lines that are purely headers or structural braces / semicolons
    if (isScopeHeader || /^[{}\s;]*$/.test(trimmed)) {
      continue;
    }

    // ── Phase 3: check variable declarations ───────────────────────
    const scope = scopeStack[scopeStack.length - 1];
    if (!scope) {
      continue;
    }

    // Between a function / method header and its opening '{',
    // declarations are parameters -- not local variables.
    if (
      (pendingKind === "function" || pendingKind === "method") &&
      DECL_PATTERN.test(trimmed)
    ) {
      continue;
    }

    if (DECL_PATTERN.test(trimmed)) {
      if (scope.sawCode) {
        const startCol = line.firstNonWhitespaceCharacterIndex;
        const endCol = line.text.trimEnd().length;
        const range = new vscode.Range(lineIdx, startCol, lineIdx, endCol);

        const scopeLabel =
          scope.kind === "block" ? "code block" : `${scope.kind} scope`;

        const reason =
          `Variable declarations must appear at the top of the ${scopeLabel}, before any executable code. ` +
          `In HSL, variables must be declared at the beginning of each code block.`;

        const diag = new vscode.Diagnostic(
          range,
          reason,
          vscode.DiagnosticSeverity.Error
        );
        diag.source = "hsl";
        diag.code = "declaration-not-at-top";
        diagnostics.push(diag);
      }
      // A valid top-of-scope declaration does NOT flip sawCode.
      continue;
    }

    // ── Phase 4: executable statement → mark scope as having code ──
    // Skip lines inside #ifndef HSL_RUNTIME guards -- these are
    // design-time stubs that should not affect variable placement.
    if (
      scopeAtLineStart &&
      lineStartDepth === scopeAtLineStart.braceDepth &&
      !hslRuntimeGuardedLines.has(lineIdx)
    ) {
      scopeAtLineStart.sawCode = true;
    }
  }
}

function collectIncludePreambleNamespaceLines(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[]
): Set<number> {
  const lines: string[] = [];

  for (let lineIdx = 0; lineIdx < document.lineCount; lineIdx++) {
    const line = document.lineAt(lineIdx);
    const lineOffset = document.offsetAt(line.range.start);

    let clean = "";
    for (let ci = 0; ci < line.text.length; ci++) {
      clean += isInsideIgnoredRange(lineOffset + ci, ignoredRanges)
        ? " "
        : line.text[ci];
    }

    lines.push(clean);
  }

  const includePreambleLines = new Set<number>();

  for (let lineIdx = 0; lineIdx < lines.length; lineIdx++) {
    const trimmed = lines[lineIdx].trim();
    if (!NAMESPACE_HEADER.test(trimmed)) {
      continue;
    }

    let openFound = false;
    let localDepth = 0;
    let endLine = -1;

    for (let scanLine = lineIdx; scanLine < lines.length; scanLine++) {
      const lineText = lines[scanLine];

      for (let ci = 0; ci < lineText.length; ci++) {
        const ch = lineText[ci];
        if (ch === "{") {
          openFound = true;
          localDepth++;
          continue;
        }

        if (ch === "}" && openFound) {
          localDepth = Math.max(0, localDepth - 1);
          if (localDepth === 0) {
            endLine = scanLine;
            break;
          }
        }
      }

      if (endLine >= 0) {
        break;
      }

      if (!openFound && /;/.test(lineText)) {
        break;
      }
    }

    if (!openFound || endLine < 0) {
      continue;
    }

    let hasInclude = false;
    let hasNonPreambleContent = false;

    for (let evalLine = lineIdx; evalLine <= endLine; evalLine++) {
      const text = lines[evalLine].trim();
      if (text === "" || /^[{}\s;]*$/.test(text)) {
        continue;
      }

      if (/#\s*include\b/i.test(text)) {
        hasInclude = true;
      }

      if (NAMESPACE_HEADER.test(text) || /^#/.test(text)) {
        continue;
      }

      hasNonPreambleContent = true;
      break;
    }

    if (!hasInclude || hasNonPreambleContent) {
      continue;
    }

    for (let markLine = lineIdx; markLine <= endLine; markLine++) {
      includePreambleLines.add(markLine);
    }

    lineIdx = endLine;
  }

  return includePreambleLines;
}

// ── Helpers to skip strings / comments ──────────────────────────────────

interface OffsetRange {
  start: number;
  end: number;
}

interface IgnoredSegment extends OffsetRange {
  kind: "string" | "comment";
}

/**
 * Returns an array of [start, end) offset ranges that cover string literals
 * and comments so diagnostics can avoid false positives inside them.
 */
function getIgnoredRanges(text: string): OffsetRange[] {
  return getIgnoredSegments(text).map(({ start, end }) => ({ start, end }));
}

function getIgnoredSegments(text: string): IgnoredSegment[] {
  const ranges: IgnoredSegment[] = [];
  // Matches:  // line comment  |  /* block comment */  |  "string"
  const pattern = /\/\/[^\n]*|\/\*[\s\S]*?\*\/|"(?:[^"\\]|\\.)*"/g;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    ranges.push({
      start: m.index,
      end: m.index + m[0].length,
      kind: m[0].startsWith('"') ? "string" : "comment",
    });
  }
  return ranges;
}


function buildArityMaskedText(text: string): string {
  const segments = getIgnoredSegments(text);
  if (segments.length === 0) {
    return text;
  }

  const ranges: OffsetRange[] = [];
  const chars = [...text];

  for (const segment of segments) {
    if (segment.kind === "comment") {
      ranges.push(segment);
      continue;
    }

    const stringInnerStart = segment.start + 1;
    const stringInnerEnd = Math.max(stringInnerStart, segment.end - 1);
    ranges.push({ start: stringInnerStart, end: stringInnerEnd });
  }

  for (const range of ranges) {
    for (let i = range.start; i < range.end && i < chars.length; i++) {
      if (chars[i] !== "\n" && chars[i] !== "\r") {
        chars[i] = " ";
      }
    }
  }

  return chars.join("");
}

function isInsideIgnoredRange(offset: number, ranges: OffsetRange[]): boolean {
  for (const r of ranges) {
    if (offset >= r.start && offset < r.end) {
      return true;
    }
  }
  return false;
}

// ── Function declaration / definition pairing enforcement ───────────────

interface FunctionRecord {
  /** Normalised key that identifies identical signatures. */
  signatureKey: string;
  /** Human-readable display name for diagnostic messages. */
  displayName: string;
  /** Whether this is a prototype (`;`) or an implementation (`{ … }`). */
  kind: "declaration" | "definition";
  /** Line index where the function header starts. */
  lineIndex: number;
  /** Column of the function name within that line. */
  nameCol: number;
  /** Length of the function name. */
  nameLen: number;
}

/**
 * Ensure that every function within the document has both a declaration
 * (prototype ending with `;`) and a definition (implementation with `{ … }`).
 */
function checkFunctionDeclarationDefinitionPairing(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[],
  hslRuntimeGuardedLines: Set<number> = new Set()
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);
  const records = collectFunctionRecords(cleanText);

  // Group records by signature key
  const byKey = new Map<string, FunctionRecord[]>();
  for (const rec of records) {
    const group = byKey.get(rec.signatureKey) ?? [];
    group.push(rec);
    byKey.set(rec.signatureKey, group);
  }

  // Build a secondary index by namespace-agnostic key so that declarations
  // at the top level can match definitions inside a namespace (and vice
  // versa).  Key format: `modKey||name|paramSig|returnType` (namespace
  // portion is always empty).
  const stripNamespace = (key: string): string => {
    const parts = key.split("|");
    // parts: [modKey, nsPrefix, name, paramSig, returnType]
    return `${parts[0]}||${parts[2]}|${parts.slice(3).join("|")}`;
  };

  const byNameKey = new Map<string, FunctionRecord[]>();
  for (const rec of records) {
    const nameKey = stripNamespace(rec.signatureKey);
    const group = byNameKey.get(nameKey) ?? [];
    group.push(rec);
    byNameKey.set(nameKey, group);
  }

  for (const [_key, group] of byKey) {
    const declarations = group.filter((r) => r.kind === "declaration");
    const definitions = group.filter((r) => r.kind === "definition");

    if (declarations.length > 0 && definitions.length === 0) {
      // Check if a definition exists in another namespace
      // (e.g. private helper declared at top level, defined in a namespace).
      const nameKey = stripNamespace(declarations[0].signatureKey);
      const crossNs = byNameKey.get(nameKey) ?? [];
      const crossNsDef = crossNs.find((r) => r.kind === "definition");

      if (crossNsDef) {
        // Definition exists but in a different namespace -- warn, not error
        for (const decl of declarations) {
          const range = new vscode.Range(
            decl.lineIndex,
            decl.nameCol,
            decl.lineIndex,
            decl.nameCol + decl.nameLen
          );
          const diag = new vscode.Diagnostic(
            range,
            `Declaration of '${decl.displayName}' and its definition '${crossNsDef.displayName}' are in different namespaces. Best practice: keep declaration and definition in the same namespace.`,
            vscode.DiagnosticSeverity.Warning
          );
          diag.source = "hsl";
          diag.code = "cross-namespace-function-definition";
          diagnostics.push(diag);
        }
      } else {
        for (const decl of declarations) {
          const range = new vscode.Range(
            decl.lineIndex,
            decl.nameCol,
            decl.lineIndex,
            decl.nameCol + decl.nameLen
          );
          const diag = new vscode.Diagnostic(
            range,
            `No matching definition for function '${decl.displayName}' was found in this file. Verify the companion implementation/include file.`,
            vscode.DiagnosticSeverity.Warning
          );
          diag.source = "hsl";
          diag.code = "missing-function-definition";
          diagnostics.push(diag);
        }
      }
    }

    if (definitions.length > 0 && declarations.length === 0) {
      // Inside #ifndef HSL_RUNTIME blocks, function stubs are design-time
      // placeholders whose real declarations live in the companion .hsi
      // file.  Do not flag "missing declaration" for these definitions.
      const allInsideGuard = definitions.every((d) =>
        hslRuntimeGuardedLines.has(d.lineIndex)
      );
      if (allInsideGuard) {
        // Silently skip -- the .hsi file provides the real declarations.
      } else {
        // Check if a declaration exists in another namespace.
        const nameKey = stripNamespace(definitions[0].signatureKey);
        const crossNs = byNameKey.get(nameKey) ?? [];
        const crossNsDecl = crossNs.find((r) => r.kind === "declaration");

        if (crossNsDecl) {
          // Declaration exists but in a different namespace -- warn, not error
          for (const def of definitions) {
            if (hslRuntimeGuardedLines.has(def.lineIndex)) {
              continue;
            }
            const range = new vscode.Range(
              def.lineIndex,
              def.nameCol,
              def.lineIndex,
              def.nameCol + def.nameLen
            );
            const diag = new vscode.Diagnostic(
              range,
              `Definition of '${def.displayName}' and its declaration '${crossNsDecl.displayName}' are in different namespaces. Best practice: keep declaration and definition in the same namespace.`,
              vscode.DiagnosticSeverity.Warning
            );
            diag.source = "hsl";
            diag.code = "cross-namespace-function-declaration";
            diagnostics.push(diag);
          }
        } else {
          for (const def of definitions) {
            if (hslRuntimeGuardedLines.has(def.lineIndex)) {
              continue;
            }
            const range = new vscode.Range(
              def.lineIndex,
              def.nameCol,
              def.lineIndex,
              def.nameCol + def.nameLen
            );
            const diag = new vscode.Diagnostic(
              range,
              `No matching declaration for function '${def.displayName}' was found in this file. Verify the companion interface/include file.`,
              vscode.DiagnosticSeverity.Warning
            );
            diag.source = "hsl";
            diag.code = "missing-function-declaration";
            diagnostics.push(diag);
          }
        }
      }
    }

    if (definitions.length > 1) {
      for (const def of definitions) {
        const range = new vscode.Range(
          def.lineIndex,
          def.nameCol,
          def.lineIndex,
          def.nameCol + def.nameLen
        );
        const diag = new vscode.Diagnostic(
          range,
          `Duplicate definition for function '${def.displayName}'.`,
          vscode.DiagnosticSeverity.Error
        );
        diag.source = "hsl";
        diag.code = "duplicate-function-definition";
        diagnostics.push(diag);
      }
    }
  }
}

/**
 * Walk through the comment/string-masked source text and collect every
 * function header, classifying each as either a declaration or definition.
 */
function collectFunctionRecords(cleanText: string): FunctionRecord[] {
  const records: FunctionRecord[] = [];
  const cleanLines = cleanText.split(/\r?\n/);

  const namespaceStack: Array<{ name: string; depth: number }> = [];
  let braceDepth = 0;
  let pendingNamespace: string | null = null;

  let collectingFunction = false;
  let functionStartLine = -1;
  let functionHeaderParts: string[] = [];

  for (let lineIndex = 0; lineIndex < cleanLines.length; lineIndex++) {
    const cleanLine = cleanLines[lineIndex];

    if (!collectingFunction) {
      // Detect namespace header
      const nsMatch =
        /^\s*(?:(?:private|public|static|global|const|synchronized)\s+)*namespace\s+([A-Za-z_]\w*)\b/.exec(
          cleanLine
        );
      if (nsMatch) {
        pendingNamespace = nsMatch[1];
      }

      // Detect function header start
      if (
        /^\s*(?:(?:private|public|static|global|const|synchronized)\s+)*function\b/.test(
          cleanLine
        )
      ) {
        collectingFunction = true;
        functionStartLine = lineIndex;
        functionHeaderParts = [cleanLine];
      }
    } else {
      functionHeaderParts.push(cleanLine);
    }

    if (collectingFunction) {
      const joinedClean = functionHeaderParts.join("\n");
      const openParens = (joinedClean.match(/\(/g) || []).length;
      const closeParens = (joinedClean.match(/\)/g) || []).length;
      const parenDelta = openParens - closeParens;

      if (parenDelta <= 0 && /[;{]/.test(joinedClean)) {
        // Extract function components
        const fnMatch =
          /^\s*((?:(?:private|public|static|global|const|synchronized)\s+)*)function\s+([A-Za-z_]\w*)\s*\(([\s\S]*?)\)\s*([A-Za-z_]\w*)\s*(;|\{)/m.exec(
            joinedClean
          );

        if (fnMatch) {
          const modifiers = fnMatch[1] ?? "";
          const name = fnMatch[2];
          const paramsRaw = fnMatch[3] ?? "";
          const returnTypeText = fnMatch[4] ?? "variable";
          const terminator = fnMatch[5];

          const kind: "declaration" | "definition" =
            terminator === ";" ? "declaration" : "definition";

          // Compute fully-qualified namespace prefix
          const nsPrefix = namespaceStack.map((n) => n.name).join("::");
          const qualifiedName =
            nsPrefix.length > 0 ? `${nsPrefix}::${name}` : name;

          // Normalise parameter signature (types only, no names)
          const paramSig = buildNormalizedParamSignature(paramsRaw);

          // Include private / static in the key so mismatches are caught
          const modParts: string[] = [];
          if (/\bprivate\b/i.test(modifiers)) {
            modParts.push("private");
          }
          if (/\bstatic\b/i.test(modifiers)) {
            modParts.push("static");
          }
          const modKey = modParts.join(" ");

          const signatureKey =
            `${modKey}|${nsPrefix.toLowerCase()}|${name.toLowerCase()}|${paramSig}|${returnTypeText.toLowerCase()}`;

          // Locate the function name within the starting line for a precise range
          const startLineText = cleanLines[functionStartLine];
          const funcKwIdx = startLineText.indexOf("function");
          const nameIdx =
            funcKwIdx >= 0
              ? startLineText.indexOf(name, funcKwIdx + 8)
              : startLineText.indexOf(name);
          const nameCol = nameIdx >= 0 ? nameIdx : 0;

          records.push({
            signatureKey,
            displayName: qualifiedName,
            kind,
            lineIndex: functionStartLine,
            nameCol,
            nameLen: name.length,
          });
        }

        collectingFunction = false;
        functionStartLine = -1;
        functionHeaderParts = [];
      }
    }

    // ── Track braces for namespace management ──────────────────────
    for (const ch of cleanLine) {
      if (ch === "{") {
        braceDepth++;
        if (pendingNamespace) {
          namespaceStack.push({
            name: pendingNamespace,
            depth: braceDepth,
          });
          pendingNamespace = null;
        }
      } else if (ch === "}") {
        while (
          namespaceStack.length > 0 &&
          namespaceStack[namespaceStack.length - 1].depth >= braceDepth
        ) {
          namespaceStack.pop();
        }
        braceDepth = Math.max(0, braceDepth - 1);
      }
    }
  }

  return records;
}

/**
 * Normalise a raw parameter list into a canonical form that only retains
 * the type, by-ref marker `&`, and array marker `[]` for each parameter.
 * Parameter names and default values are discarded.
 *
 * Example input:  `variable i_tblValues[], variable& o_x, string s`
 * Example output: `variable[],variable&,string`
 */
function buildNormalizedParamSignature(paramsRaw: string): string {
  const parts = splitArgsForSignature(paramsRaw).filter(
    (p) => p.toLowerCase() !== "void"
  );

  return parts
    .map((p) => {
      // Strip default value
      const noDefault = p.includes("=")
        ? p.slice(0, p.indexOf("=")).trim()
        : p.trim();
      // Detect array suffix
      const isArray = /\[\]\s*$/.test(noDefault);
      const noArray = noDefault.replace(/\[\]\s*$/, "").trim();
      // Detect by-ref
      const isByRef = noArray.includes("&");
      const noRef = noArray.replace(/&/g, "").trim();
      // Type is the first token
      const tokens = noRef.split(/\s+/).filter((t) => t.length > 0);
      const typeText = tokens.length > 0 ? tokens[0].toLowerCase() : "";
      return `${typeText}${isByRef ? "&" : ""}${isArray ? "[]" : ""}`;
    })
    .join(",");
}

/**
 * Split a parameter list by commas, respecting nested parentheses and brackets.
 */
function splitArgsForSignature(paramList: string): string[] {
  const parts: string[] = [];
  let current = "";
  let depth = 0;
  for (const c of paramList) {
    if (c === "(" || c === "[") {
      depth++;
      current += c;
      continue;
    }
    if (c === ")" || c === "]") {
      depth = Math.max(0, depth - 1);
      current += c;
      continue;
    }
    if (c === "," && depth === 0) {
      parts.push(current.trim());
      current = "";
      continue;
    }
    current += c;
  }
  if (current.trim().length > 0) {
    parts.push(current.trim());
  }
  return parts.filter((p) => p.length > 0);
}

// ── Initialize step enforcement ─────────────────────────────────────────

/** The Initialize CLSID (underscore format) used in ML_STAR._<CLSID>() calls. */
const INITIALIZE_CLSID = "1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2";

/**
 * Enforces device initialisation rules differently for methods and libraries:
 *
 * **Methods** (files with `method main()`):
 *   The Initialize step (`ML_STAR._1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2(...)`)
 *   must be called inside `method main()` before any other device commands.
 *   Reports an error on the first device usage that appears before (or without)
 *   the Initialize call.
 *
 * **Libraries** (files without `method main()`):
 *   Libraries must receive the device as a `device &` function parameter from
 *   the calling method -- they must NOT call Initialize themselves.  This
 *   function flags two library-specific issues:
 *   1. Initialize calls found in a library file (error) -- Initialize belongs
 *      in `method main()` only.
 *   2. `global device` declarations in a library file (warning) -- the library
 *      should receive the device via a `device &` parameter instead of
 *      declaring its own global device.
 */
function checkInitializeBeforeDeviceUse(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);

  // Find `method main()` body -- we enforce Initialize-before-device-use
  // only in method main(), which is the entry point where Initialize must be
  // called.  Library functions receive an already-initialised device via a
  // `device &` parameter and must not call Initialize themselves (that is
  // checked separately in checkLibraryDeviceUsage).
  const mainMethodPattern = /\bmethod\s+main\s*\(/gi;
  const mainMatch = mainMethodPattern.exec(cleanText);
  if (!mainMatch) {
    // No method main() -- this is a library file.
    // Check for library-specific device usage issues.
    checkLibraryDeviceUsage(document, cleanText, ignoredRanges, diagnostics);
    return;
  }

  // Find the opening brace of main()
  let mainBodyStart = -1;
  for (let i = mainMatch.index + mainMatch[0].length; i < cleanText.length; i++) {
    if (cleanText[i] === "{") {
      mainBodyStart = i;
      break;
    }
  }
  if (mainBodyStart < 0) {
    return;
  }

  // Find the matching closing brace
  let depth = 0;
  let mainBodyEnd = -1;
  for (let i = mainBodyStart; i < cleanText.length; i++) {
    if (cleanText[i] === "{") {
      depth++;
    } else if (cleanText[i] === "}") {
      depth--;
      if (depth === 0) {
        mainBodyEnd = i;
        break;
      }
    }
  }
  if (mainBodyEnd < 0) {
    mainBodyEnd = cleanText.length;
  }

  const mainBody = cleanText.slice(mainBodyStart, mainBodyEnd + 1);
  const mainBodyOffset = mainBodyStart;

  // Detect `global device` declarations to learn device variable names.
  // Default to ML_STAR if none found.
  const deviceNames = new Set<string>();
  const globalDevicePattern = /\bglobal\s+device\s+([A-Za-z_]\w*)/g;
  let devMatch: RegExpExecArray | null;
  while ((devMatch = globalDevicePattern.exec(cleanText)) !== null) {
    deviceNames.add(devMatch[1]);
  }
  if (deviceNames.size === 0) {
    deviceNames.add("ML_STAR");
  }

  // Build a pattern that matches:
  //   DEVICE._<any CLSID>( ... )   -- device step calls
  // We check for the Initialize CLSID specifically.
  const deviceNamesEscaped = Array.from(deviceNames)
    .map((n) => n.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("|");

  // Find the Initialize call within main body
  const initPattern = new RegExp(
    `\\b(?:${deviceNamesEscaped})\\._${INITIALIZE_CLSID}\\s*\\(`,
    "i"
  );
  const initMatch = initPattern.exec(mainBody);
  const initOffset = initMatch
    ? mainBodyOffset + initMatch.index
    : -1;

  // Find ALL device step calls (DEVICE._CLSID(...)) in main body
  const deviceStepPattern = new RegExp(
    `\\b(${deviceNamesEscaped})\\._([0-9A-Fa-f_]+)\\s*\\(`,
    "gi"
  );
  let stepMatch: RegExpExecArray | null;

  while ((stepMatch = deviceStepPattern.exec(mainBody)) !== null) {
    const absoluteOffset = mainBodyOffset + stepMatch.index;

    // Skip if inside a comment or string
    if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
      continue;
    }

    const clsid = stepMatch[2];

    // Skip the Initialize call itself
    if (clsid.toUpperCase() === INITIALIZE_CLSID) {
      continue;
    }

    // If Initialize was never found, or this call appears before it
    if (initOffset < 0 || absoluteOffset < initOffset) {
      const pos = document.positionAt(absoluteOffset);
      const endPos = document.positionAt(
        absoluteOffset + stepMatch[0].length - 1
      );
      const range = new vscode.Range(pos, endPos);

      const deviceName = stepMatch[1];
      const diag = new vscode.Diagnostic(
        range,
        `Device '${deviceName}' is used before the Initialize step in 'method main()'. ` +
          `The Initialize step (${deviceName}._${INITIALIZE_CLSID}(...)) ` +
          `must be called in 'method main()' before any device steps or other instrument commands. ` +
          `Without it, the instrument hardware is not initialised and all subsequent ` +
          `device commands will fail at runtime. Note: library functions should NOT ` +
          `call Initialize -- they receive the device via a 'device &' parameter ` +
          `from the calling method.`,
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "missing-initialize-step";
      diagnostics.push(diag);

      // Only flag the first occurrence -- one error is enough
      return;
    }
  }
}

/**
 * Library-specific device usage checks for files without `method main()`.
 *
 * Libraries that interact with hardware must receive the device as a
 * `device &` function parameter from the calling method.  They must NOT:
 * - Call the Initialize step (that belongs in `method main()` only)
 * - Declare a `global device` (they should receive it as a parameter)
 */
function checkLibraryDeviceUsage(
  document: vscode.TextDocument,
  fullText: string,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  // Check 1: Initialize calls in a library file
  const initPattern = new RegExp(
    `\\b([A-Za-z_]\\w*)\\._${INITIALIZE_CLSID}\\s*\\(`,
    "gi"
  );
  let initMatch: RegExpExecArray | null;
  while ((initMatch = initPattern.exec(fullText)) !== null) {
    const offset = initMatch.index;
    if (isInsideIgnoredRange(offset, ignoredRanges)) {
      continue;
    }
    const pos = document.positionAt(offset);
    const endPos = document.positionAt(offset + initMatch[0].length - 1);
    const range = new vscode.Range(pos, endPos);
    const deviceName = initMatch[1];
    const diag = new vscode.Diagnostic(
      range,
      `The Initialize step should only be called inside 'method main()' in a method file. ` +
        `Library functions must receive an already-initialised device via a 'device &' ` +
        `parameter -- they should not call '${deviceName}._${INITIALIZE_CLSID}(...)' themselves.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "initialize-in-library";
    diagnostics.push(diag);
  }

  // Check 2: global device declarations in a library file
  const globalDevicePattern = /\bglobal\s+device\s+([A-Za-z_]\w*)/g;
  let devMatch: RegExpExecArray | null;
  while ((devMatch = globalDevicePattern.exec(fullText)) !== null) {
    const offset = devMatch.index;
    if (isInsideIgnoredRange(offset, ignoredRanges)) {
      continue;
    }
    const pos = document.positionAt(offset);
    const endPos = document.positionAt(offset + devMatch[0].length - 1);
    const range = new vscode.Range(pos, endPos);
    const deviceName = devMatch[1];
    const diag = new vscode.Diagnostic(
      range,
      `Library file declares 'global device ${deviceName}' but has no 'method main()'. ` +
        `Libraries that use hardware commands should receive the device as a ` +
        `'device & ${deviceName}' function parameter from the calling method, ` +
        `rather than declaring their own global device. Only method files ` +
        `(with 'method main()') should declare global devices.`,
      vscode.DiagnosticSeverity.Warning
    );
    diag.source = "hsl";
    diag.code = "global-device-in-library";
    diagnostics.push(diag);
  }
}

// ── String member function on wrong type enforcement ────────────────────

/**
 * The following member functions are ONLY valid on the `string` type.
 * Calling them on a `variable` (or any other non-string type) produces
 * VENUS error 1317.
 */
const STRING_ONLY_METHODS = new Set([
  "getlength",
  "find",
  "left",
  "mid",
  "right",
  "compare",
  "makeupper",
  "makelower",
  "spanexcluding",
]);

/**
 * HSL types that are NOT `string` -- if a variable is declared with one of
 * these types and then has a string-only member function called on it, that
 * is an error.
 */
const NON_STRING_TYPES = new Set([
  "variable",
  "sequence",
  "device",
  "object",
  "timer",
  "event",
  "file",
  "resource",
  "dialog",
]);

interface TypedDeclaration {
  name: string;
  typeName: string;
  offset: number;
  scopeChain: number[];
}

/** Flags `next` unless it is the companion keyword in `resume next`. */
function checkStandaloneNext(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const nextPattern = /\bnext\b/gi;
  const resumePrefix = /\bresume\s+$/i;

  for (let lineIndex = 0; lineIndex < document.lineCount; lineIndex++) {
    const line = document.lineAt(lineIndex);
    const lineText = line.text;
    const lineOffset = document.offsetAt(line.range.start);
    nextPattern.lastIndex = 0;

    let match: RegExpExecArray | null;
    while ((match = nextPattern.exec(lineText)) !== null) {
      const absoluteOffset = lineOffset + match.index;
      if (isInsideIgnoredRange(absoluteOffset, ignoredRanges)) {
        continue;
      }
      if (resumePrefix.test(lineText.substring(0, match.index))) {
        continue;
      }

      const diag = new vscode.Diagnostic(
        new vscode.Range(lineIndex, match.index, lineIndex, match.index + match[0].length),
        "Standalone 'next' is not valid HSL. 'next' is only valid in 'resume next;' or 'onerror resume next;'. Restructure loop continuation with a condition or control flag.",
        vscode.DiagnosticSeverity.Error
      );
      diag.source = "hsl";
      diag.code = "invalid-standalone-next";
      diagnostics.push(diag);
    }
  }
}

interface BraceBlock {
  start: number;
  end: number;
}

function collectBraceBlocks(cleanText: string): BraceBlock[] {
  const stack: number[] = [];
  const blocks: BraceBlock[] = [];
  for (let i = 0; i < cleanText.length; i++) {
    if (cleanText[i] === "{") {
      stack.push(i);
    } else if (cleanText[i] === "}") {
      const start = stack.pop();
      if (start !== undefined) {
        blocks.push({ start, end: i });
      }
    }
  }
  for (const start of stack) {
    blocks.push({ start, end: cleanText.length });
  }
  return blocks;
}

function scopeChainAt(blocks: BraceBlock[], offset: number): number[] {
  return blocks
    .filter((block) => block.start < offset && offset <= block.end)
    .sort((left, right) => left.start - right.start)
    .map((block) => block.start);
}

function collectTypedDeclarations(cleanText: string): TypedDeclaration[] {
  const blocks = collectBraceBlocks(cleanText);
  const declarations: TypedDeclaration[] = [];
  const typePattern =
    "variable|string|sequence|device|object|timer|event|file|resource|dialog|char|short|long|float";

  // Local/global declaration statements, including comma-separated names.
  const statementPattern = new RegExp(
    `^[ \\t]*(?:(?:private|static|const|global|synchronized)\\s+)*(${typePattern})\\s+([^;\\r\\n]+);`,
    "gm"
  );
  let statementMatch: RegExpExecArray | null;
  while ((statementMatch = statementPattern.exec(cleanText)) !== null) {
    const typeName = statementMatch[1].toLowerCase();
    const body = statementMatch[2];
    const bodyOffset = statementMatch.index + statementMatch[0].indexOf(body);
    for (const part of splitTopLevelArguments(body)) {
      const nameMatch = /&?\s*([A-Za-z_]\w*)/.exec(part.trim());
      if (!nameMatch) {
        continue;
      }
      const relative = body.indexOf(part);
      const offset = bodyOffset + Math.max(0, relative) + nameMatch.index;
      declarations.push({
        name: nameMatch[1],
        typeName,
        offset,
        scopeChain: scopeChainAt(blocks, statementMatch.index),
      });
    }
  }

  // Function parameters belong to the function body's scope, not file scope.
  const functionPattern =
    /\b(?:function|method)\s+[A-Za-z_]\w*\s*\(([\s\S]*?)\)\s*(?:[A-Za-z_]\w*)?\s*\{/g;
  let functionMatch: RegExpExecArray | null;
  while ((functionMatch = functionPattern.exec(cleanText)) !== null) {
    const params = functionMatch[1];
    const bodyStart = functionMatch.index + functionMatch[0].lastIndexOf("{");
    for (const param of splitTopLevelArguments(params)) {
      const paramMatch = new RegExp(
        `^\\s*(${typePattern})\\s*&?\\s*([A-Za-z_]\\w*)`,
        "i"
      ).exec(param);
      if (!paramMatch) {
        continue;
      }
      declarations.push({
        name: paramMatch[2],
        typeName: paramMatch[1].toLowerCase(),
        offset: bodyStart,
        scopeChain: scopeChainAt(blocks, bodyStart + 1),
      });
    }
  }

  return declarations;
}

function resolveDeclaredType(
  declarations: TypedDeclaration[],
  blocks: BraceBlock[],
  name: string,
  useOffset: number
): string | undefined {
  const useScope = scopeChainAt(blocks, useOffset);
  const visible = declarations.filter((declaration) => {
    if (declaration.name !== name || declaration.offset > useOffset) {
      return false;
    }
    return declaration.scopeChain.every(
      (scope, index) => useScope[index] === scope
    );
  });
  visible.sort((left, right) =>
    right.scopeChain.length - left.scopeChain.length || right.offset - left.offset
  );
  return visible[0]?.typeName;
}

/**
 * Check for `.GetLength()`, `.Find()`, etc. called on identifiers whose
 * declared type is `variable` (or another non-string type).
 *
 * Approach:
 *   1. Scan for all `variable`, `string`, etc. declarations and record
 *      each identifier's type.
 *   2. Scan for `identifier.MethodName(` patterns where MethodName is a
 *      string-only method.
 *   3. If the identifier's declared type is non-string, report an error.
 *
 * Limitations: this is a best-effort, single-file analysis that cannot
 * track types across assignments or function returns.  It catches the most
 * common error pattern: a parameter or local declared as `variable` with
 * string member functions called directly on it.
 */
function checkStringMemberOnWrongType(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);
  const blocks = collectBraceBlocks(cleanText);
  const declarations = collectTypedDeclarations(cleanText);

  // ── Phase 2: find method calls on identifiers ─────────────────────
  const methodCallPattern =
    /\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\(/g;
  let mcMatch: RegExpExecArray | null;
  while ((mcMatch = methodCallPattern.exec(cleanText)) !== null) {
    const receiverName = mcMatch[1];
    const methodName = mcMatch[2];
    const methodKey = methodName.toLowerCase();

    if (!STRING_ONLY_METHODS.has(methodKey)) {
      continue;
    }

    const declaredType = resolveDeclaredType(
      declarations,
      blocks,
      receiverName,
      mcMatch.index
    );
    if (!declaredType) {
      // Type unknown -- could be from an included library; skip.
      continue;
    }

    if (declaredType === "string") {
      // Correct usage.
      continue;
    }

    if (!NON_STRING_TYPES.has(declaredType)) {
      continue;
    }

    // Build diagnostic
    const callOffset = mcMatch.index;
    const dotIdx = cleanText.indexOf(".", callOffset + receiverName.length);
    const methodStart = dotIdx + 1 + (cleanText.slice(dotIdx + 1).length - cleanText.slice(dotIdx + 1).trimStart().length);

    const linePos = document.positionAt(callOffset);
    const methodPos = document.positionAt(methodStart);
    const range = new vscode.Range(
      linePos.line,
      linePos.character,
      methodPos.line,
      methodPos.character + methodName.length
    );

    const diag = new vscode.Diagnostic(
      range,
      `'${methodName}' is a member function of 'string', not '${declaredType}'. ` +
        `Declare '${receiverName}' as 'string' instead, or assign to a local ` +
        `'string' variable before calling '.${methodName}()'.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "string-method-on-wrong-type";
    diagnostics.push(diag);
  }
}

// ── String concatenation with '+' on string-typed variables ─────────────

/**
 * The '+' concatenation operator is defined only for the `variable` type.
 * Using '+' on a `string`-typed variable produces VENUS error 1222.
 *
 * This checker scans for assignment expressions like:
 *   strX = ... + strY ...     where strY is declared as `string`
 *   strX = ... strY + ...     where strY is declared as `string`
 *
 * and flags them when a `string`-typed identifier participates as an
 * operand of '+' concatenation.
 */
// ── Array element in expression enforcement ─────────────────────────────

/**
 * Detects array element access (e.g. `arr[i]`) used directly as an operand
 * in a `+` expression.  The VENUS parser cannot handle `arr[idx]` inside
 * a `+` concatenation / addition -- the bracket notation confuses the
 * parser and produces cascading "syntax error before ';'" errors.
 *
 * The fix is to assign the array element to a temporary variable first:
 *
 *   strPos = arrAll[intIdx];       // extract first
 *   strResult = strResult + strPos; // then use in expression
 */
function checkArrayElementInExpression(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);

  // Match patterns like:
  //   expr + identifier[expr]       -- array element as right operand of +
  //   identifier[expr] + expr       -- array element as left operand of +
  // We look for `+` adjacent to `identifier[...]` (with optional whitespace).
  //
  // Pattern explanation:
  //   \b(\w+)\[  -- identifier followed by [
  //   [^\]]*\]   -- contents of brackets up to ]
  //   \s*\+      -- followed by +
  //   (?!\+|=)   -- not ++ or +=
  // And the reverse for + before array access.

  // Case 1: arr[expr] + ...
  const arrayThenPlus = /\b(\w+)\[([^\]]*)\]\s*\+(?!\+|=)/g;
  // Case 2: ... + arr[expr]
  const plusThenArray = /\+\s*\b(\w+)\[([^\]]*)\]/g;

  const flaggedLines = new Set<number>();

  let m: RegExpExecArray | null;

  // Case 1: arr[expr] +
  arrayThenPlus.lastIndex = 0;
  while ((m = arrayThenPlus.exec(cleanText)) !== null) {
    if (isInsideIgnoredRange(m.index, ignoredRanges)) {
      continue;
    }
    // Skip if this is on a line that looks like a standalone assignment
    // (arr[i] = expr + ...) -- the array access is the LHS, not in the + expr
    const linePos = document.positionAt(m.index);
    const lineText = document.lineAt(linePos.line).text;
    // Check that the array access is NOT on the left side of an assignment
    const assignMatch = lineText.match(/^\s*(\w+)\[[^\]]*\]\s*=/);
    if (assignMatch && assignMatch[1] === m[1]) {
      continue;
    }
    if (flaggedLines.has(linePos.line)) {
      continue;
    }
    flaggedLines.add(linePos.line);
    const bracketStart = m.index + m[1].length;
    const bracketEnd = bracketStart + 1 + m[2].length + 1; // [ + contents + ]
    const range = new vscode.Range(
      linePos.line,
      linePos.character,
      document.positionAt(bracketEnd).line,
      document.positionAt(bracketEnd).character
    );
    const diag = new vscode.Diagnostic(
      range,
      `Array element access '${m[1]}[${m[2]}]' cannot be used directly in a '+' expression. ` +
        `The VENUS parser does not support bracket notation as an operand of '+'. ` +
        `Assign the array element to a temporary variable first, then use that variable in the expression.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "array-element-in-expression";
    diagnostics.push(diag);
  }

  // Case 2: + arr[expr]
  plusThenArray.lastIndex = 0;
  while ((m = plusThenArray.exec(cleanText)) !== null) {
    if (isInsideIgnoredRange(m.index, ignoredRanges)) {
      continue;
    }
    const arrayIdStart = m.index + m[0].indexOf(m[1]);
    const linePos = document.positionAt(arrayIdStart);
    if (flaggedLines.has(linePos.line)) {
      continue;
    }
    flaggedLines.add(linePos.line);
    const bracketEnd = arrayIdStart + m[1].length + 1 + m[2].length + 1;
    const range = new vscode.Range(
      linePos.line,
      linePos.character,
      document.positionAt(bracketEnd).line,
      document.positionAt(bracketEnd).character
    );
    const diag = new vscode.Diagnostic(
      range,
      `Array element access '${m[1]}[${m[2]}]' cannot be used directly in a '+' expression. ` +
        `The VENUS parser does not support bracket notation as an operand of '+'. ` +
        `Assign the array element to a temporary variable first, then use that variable in the expression.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "array-element-in-expression";
    diagnostics.push(diag);
  }
}

// ── String concatenation enforcement ────────────────────────────────────

function checkStringConcatenation(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);
  const blocks = collectBraceBlocks(cleanText);
  const declarations = collectTypedDeclarations(cleanText);
  const candidates: Array<{ name: string; offset: number }> = [];

  let match: RegExpExecArray | null;
  const beforePlus = /\b([A-Za-z_]\w*)\s*\+(?!=|\+)/g;
  while ((match = beforePlus.exec(cleanText)) !== null) {
    candidates.push({ name: match[1], offset: match.index });
  }
  const afterPlus = /\+(?![+=])\s*\b([A-Za-z_]\w*)\b/g;
  while ((match = afterPlus.exec(cleanText)) !== null) {
    candidates.push({
      name: match[1],
      offset: match.index + match[0].lastIndexOf(match[1]),
    });
  }

  const flagged = new Set<number>();
  for (const candidate of candidates) {
    if (
      flagged.has(candidate.offset) ||
      resolveDeclaredType(declarations, blocks, candidate.name, candidate.offset) !== "string"
    ) {
      continue;
    }
    flagged.add(candidate.offset);
    const start = document.positionAt(candidate.offset);
    const range = new vscode.Range(
      start,
      document.positionAt(candidate.offset + candidate.name.length)
    );
    const diag = new vscode.Diagnostic(
      range,
      `The '+' operator does not work with HSL 'string' values. Use a 'variable' for concatenation, then assign to 'string' when string member functions are needed.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "string-concat-with-plus";
    diagnostics.push(diag);
  }
}

// ── Namespace-qualified variable access ─────────────────────────────────

/**
 * HSL only supports the `::` scope-resolution operator for function calls,
 * not for accessing global variables.  `Namespace::Function()` is valid,
 * but `Namespace::variable` (without a trailing `(`) is a compile error.
 *
 * However, HSL **does** allow `::` access for `const` declarations in
 * namespaces (e.g., `MECC::IDS::stepNameFileOpen`).  Since the extension
 * cannot distinguish `const` from `variable` in external/included files,
 * we only flag namespace-qualified non-function names that appear at
 * parenthesis depth 0 (i.e., NOT inside a function call argument list).
 * Qualified names inside parentheses are overwhelmingly `const` references
 * passed as arguments -- a pattern the Hamilton Method Editor generates
 * extensively (e.g., `MECC::RaiseRuntimeErrorEx(..., MECC::IDS::xxx, ...)`).
 */
function checkNamespaceQualifiedVariableAccess(
  document: vscode.TextDocument,
  ignoredRanges: OffsetRange[],
  diagnostics: vscode.Diagnostic[]
): void {
  const fullText = document.getText();
  const cleanText = buildMaskedText(fullText, ignoredRanges);

  // Pre-compute parenthesis depth at each position so we can skip
  // qualified names that appear inside function call argument lists.
  // depth[i] = number of unmatched '(' before position i.
  const parenDepth = new Uint16Array(cleanText.length + 1);
  let depth = 0;
  for (let i = 0; i < cleanText.length; i++) {
    parenDepth[i] = depth;
    if (cleanText[i] === "(") {
      depth++;
    } else if (cleanText[i] === ")") {
      depth = Math.max(0, depth - 1);
    }
  }
  parenDepth[cleanText.length] = depth;

  // Match a qualified name (A::B or A::B::C etc.) that is NOT followed by
  // `(` (which would make it a function call) or `::` (which would make
  // this just a prefix of a longer qualified name).
  //
  // The negative lookbehind avoids matching inside namespace declarations
  // (e.g., `namespace Foo`) and the negative lookahead after the last
  // identifier avoids matching function calls.
  const pattern = /\b([A-Za-z_]\w*(?:::[A-Za-z_]\w*)+)\b/g;
  let m: RegExpExecArray | null;

  while ((m = pattern.exec(cleanText)) !== null) {
    const qualifiedName = m[1];
    const matchStart = m.index;
    const matchEnd = matchStart + qualifiedName.length;

    if (isInsideIgnoredRange(matchStart, ignoredRanges)) {
      continue;
    }

    // Skip if followed by `(` -- that is a valid namespace-qualified function call
    const afterMatch = cleanText.slice(matchEnd);
    if (/^\s*\(/.test(afterMatch)) {
      continue;
    }

    // Skip if inside parentheses (paren depth > 0).  Namespace-qualified
    // names inside function call arguments are typically `const` references
    // from included libraries (e.g., MECC::IDS::stepNameFileOpen).  The
    // extension cannot resolve whether the referenced symbol is `const` or
    // `variable` from an external file, so we allow read-only usage inside
    // expressions to avoid false positives on standard Hamilton library code.
    if (parenDepth[matchStart] > 0) {
      continue;
    }

    // Skip if preceded by `namespace` keyword -- this is a namespace declaration
    const beforeMatch = cleanText.slice(
      Math.max(0, matchStart - 200),
      matchStart
    );
    if (/\bnamespace\s+$/.test(beforeMatch)) {
      continue;
    }

    // Skip if preceded by `function` or `method` keyword -- this is a definition
    if (/\b(?:function|method)\s+$/.test(beforeMatch)) {
      continue;
    }

    // Skip if this looks like a function declaration/definition context
    // (return type position, parameter list, etc.)
    if (isLikelyDeclarationContext(cleanText, matchStart)) {
      continue;
    }

    // Skip if the line is a forward declaration (prototype ending with ;)
    // e.g.: function Namespace::MyFunc(variable x) void;
    const lineStart = cleanText.lastIndexOf("\n", matchStart - 1) + 1;
    const lineEnd = cleanText.indexOf("\n", matchStart);
    const lineText = cleanText.slice(
      lineStart,
      lineEnd === -1 ? undefined : lineEnd
    );
    if (
      /^\s*(?:(?:private|static|const|global|synchronized)\s+)*(?:function|method)\b/.test(
        lineText
      )
    ) {
      continue;
    }

    // Skip device object patterns like ML_STAR._CLSID -- these use `.` not `::`
    // but just in case, skip if the prefix before `::` looks like a device call
    // (starts with an underscore-GUID pattern)
    const parts = qualifiedName.split("::");
    const lastPart = parts[parts.length - 1];
    if (/^_[0-9A-Fa-f]{8}_/.test(lastPart)) {
      continue;
    }

    // Skip if the entire line is a #include or #define directive
    if (/^\s*#/.test(lineText)) {
      continue;
    }

    const linePos = document.positionAt(matchStart);
    const range = new vscode.Range(
      linePos.line,
      linePos.character,
      linePos.line,
      linePos.character + qualifiedName.length
    );

    const namespacePart = parts.slice(0, -1).join("::");
    const diag = new vscode.Diagnostic(
      range,
      `Namespace-qualified variable access '${qualifiedName}' is not supported in HSL. ` +
        `The '::' operator can only be used for function calls (e.g., '${namespacePart}::SomeFunction()'). ` +
        `To use a namespace variable, access it from within the same namespace or pass it as a function argument.`,
      vscode.DiagnosticSeverity.Error
    );
    diag.source = "hsl";
    diag.code = "namespace-qualified-variable";
    diagnostics.push(diag);
  }
}
