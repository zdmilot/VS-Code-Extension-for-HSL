import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { ELEMENT_FUNCTIONS, type ElementFunction } from "./builtins";
import { isAbsoluteWindowsPath, resolveHslIncludeTarget } from "./hslCore";

const DEFAULT_HAMILTON_DIR = "C:\\Program Files (x86)\\Hamilton";
const INDEX_CACHE_FILE = "hsl-global-index-cache.json";

/**
 * Parse an element-function signature (e.g. "object.CreateObject(progId [, withEvents])")
 * into HslParameter[].  Required params are outside brackets; optional ones are inside.
 */
function parseElementSignatureParams(signature: string): HslParameter[] {
  const openParen = signature.indexOf("(");
  const closeParen = signature.lastIndexOf(")");
  if (openParen < 0 || closeParen <= openParen) {
    return [];
  }
  const inner = signature.slice(openParen + 1, closeParen);
  // Strip bracket notation but keep the param names inside
  const flat = inner.replace(/[\[\]]/g, "");
  const parts = flat
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p.length > 0 && !p.includes("..."));

  return parts.map((p) => ({
    typeText: "variable",
    nameText: p,
    isByRef: false,
    isArray: false,
  }));
}

/** Convert a single ElementFunction to an HslFunctionSymbol. */
function elementFunctionToSymbol(fn: ElementFunction): HslFunctionSymbol {
  return {
    name: fn.name,
    qualifiedName: `${fn.objectType.charAt(0).toUpperCase() + fn.objectType.slice(1)}::${fn.name}`,
    parameters: parseElementSignatureParams(fn.signature),
    returnTypeText: "",
    docComment: fn.documentation,
    definedInFile: "(system)",
    requiredInclude: { includeText: "", resolvedPath: "" },
    isPrivate: false,
  };
}

/**
 * A map from lowercase method name → HslFunctionSymbol[] for every system-defined
 * element function.  These always take priority over library-parsed symbols.
 */
const SYSTEM_ELEMENT_SYMBOL_MAP: Map<string, HslFunctionSymbol[]> = (() => {
  const map = new Map<string, HslFunctionSymbol[]>();
  for (const fn of ELEMENT_FUNCTIONS) {
    const key = fn.name.toLowerCase();
    const sym = elementFunctionToSymbol(fn);
    const existing = map.get(key);
    if (existing) {
      existing.push(sym);
    } else {
      map.set(key, [sym]);
    }
  }
  return map;
})();

interface HslParameter {
  typeText: string;
  nameText: string;
  isByRef: boolean;
  isArray: boolean;
}

interface RequiredInclude {
  includeText: string;
  resolvedPath: string;
}

export interface HslFunctionSymbol {
  name: string;
  qualifiedName: string;
  parameters: HslParameter[];
  returnTypeText: string;
  docComment: string;
  definedInFile: string;
  requiredInclude: RequiredInclude;
  isPrivate: boolean;
}

interface ParsedInclude {
  rawTarget: string;
  resolvedPath?: string;
  isAbsolute: boolean;
  lineIndex: number;
}

interface FileParseResult {
  filePath: string;
  mtimeMs: number;
  includes: ParsedInclude[];
  functions: HslFunctionSymbol[];
}

interface CacheFileRecord {
  filePath: string;
  mtimeMs: number;
  includes: ParsedInclude[];
  functions: HslFunctionSymbol[];
}

interface GlobalCacheFile {
  version: 2;
  libraryRoots: string[];
  createdAt: number;
  records: CacheFileRecord[];
}

export interface VisibleSymbolContext {
  symbols: HslFunctionSymbol[];
  visibleFiles: Set<string>;
}

type IncludeStyle = "absolute" | "relative";

function normalizeWindowsPath(filePath: string): string {
  return path.win32.normalize(filePath).toLowerCase();
}

function getLibraryRoots(): string[] {
  const cfg = vscode.workspace.getConfiguration("hsl");
  const hamiltonDir = path.win32.normalize(
    cfg.get<string>("hamiltonDir", DEFAULT_HAMILTON_DIR)
  );
  const configured = cfg.get<string[]>("libraryRoots", []);
  const roots = configured.length > 0
    ? configured
    : [
        path.win32.join(hamiltonDir, "Library"),
        path.win32.join(hamiltonDir, "Methods"),
        path.win32.join(hamiltonDir, "Methods", "Library Demo Methods"),
        path.win32.join(hamiltonDir, "Bin"),
      ];
  return Array.from(new Set(roots.map((root) => path.win32.normalize(root))));
}

function toWindowsLike(input: string): string {
  return input.replace(/[\/]/g, "\\");
}

function markdownForSymbol(symbol: HslFunctionSymbol): vscode.MarkdownString {
  const params = symbol.parameters
    .map((p) => `${p.typeText}${p.isByRef ? "&" : ""} ${p.nameText}${p.isArray ? "[]" : ""}`)
    .join(", ");
  const lines: string[] = [];
  lines.push(`**${symbol.qualifiedName}**`);
  lines.push("");
  lines.push(`\`${symbol.name}(${params}) ${symbol.returnTypeText}\``);
  if (symbol.docComment.trim().length > 0) {
    lines.push("");
    lines.push(symbol.docComment);
  }
  lines.push("");
  lines.push(`Defined in: ${symbol.definedInFile}`);
  return new vscode.MarkdownString(lines.join("\n"));
}

function sanitizeForParsing(text: string): string {
  const chars = [...text];
  let i = 0;
  while (i < chars.length) {
    const ch = chars[i];
    const next = i + 1 < chars.length ? chars[i + 1] : "";

    if (ch === '"') {
      let j = i;
      chars[j] = " ";
      j++;
      while (j < chars.length) {
        const c = chars[j];
        if (c === "\\" && j + 1 < chars.length) {
          chars[j] = " ";
          chars[j + 1] = " ";
          j += 2;
          continue;
        }
        chars[j] = c === "\n" || c === "\r" ? c : " ";
        if (c === '"') {
          j++;
          break;
        }
        j++;
      }
      i = j;
      continue;
    }

    if (ch === "/" && next === "/") {
      chars[i] = " ";
      chars[i + 1] = " ";
      i += 2;
      while (i < chars.length && chars[i] !== "\n") {
        chars[i] = " ";
        i++;
      }
      continue;
    }

    if (ch === "/" && next === "*") {
      chars[i] = " ";
      chars[i + 1] = " ";
      i += 2;
      while (i < chars.length) {
        if (chars[i] === "*" && i + 1 < chars.length && chars[i + 1] === "/") {
          chars[i] = " ";
          chars[i + 1] = " ";
          i += 2;
          break;
        }
        chars[i] = chars[i] === "\n" || chars[i] === "\r" ? chars[i] : " ";
        i++;
      }
      continue;
    }

    i++;
  }
  return chars.join("");
}

function extractIncludeTargets(
  text: string,
  libraryRoots: string[],
  includingFile?: string
): ParsedInclude[] {
  const includes: ParsedInclude[] = [];
  const pattern = /^\s*#include\s+(?:"([^"]+)"|__filename__\s*"([^"]*)")/gm;
  let m: RegExpExecArray | null;
  while ((m = pattern.exec(text)) !== null) {
    const rawTarget = m[1]
      ? m[1].trim()
      : `${includingFile ? path.win32.basename(includingFile, path.win32.extname(includingFile)) : "__filename__"}${m[2] ?? ""}`;
    const resolvedPath = resolveHslIncludeTarget(
      rawTarget,
      libraryRoots,
      includingFile,
      (candidate) => fs.existsSync(candidate)
    );
    includes.push({
      rawTarget,
      isAbsolute: isAbsoluteWindowsPath(rawTarget),
      resolvedPath: resolvedPath ?? undefined,
      lineIndex: text.slice(0, m.index).split(/\r?\n/).length - 1,
    });
  }
  return includes;
}

function splitArgs(paramList: string): string[] {
  const parts: string[] = [];
  let current = "";
  let depth = 0;
  for (const c of paramList) {
    if (c === "(") {
      depth++;
      current += c;
      continue;
    }
    if (c === ")") {
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
  return parts;
}

function parseParameter(param: string): HslParameter {
  const trimmed = param.trim();
  const rawNoDefault = trimmed.includes("=") ? trimmed.slice(0, trimmed.indexOf("=")).trim() : trimmed;
  const isArray = /\[\]\s*$/.test(rawNoDefault);
  const noArray = rawNoDefault.replace(/\[\]\s*$/, "").trim();
  const nameMatch = /([A-Za-z_]\w*)\s*$/.exec(noArray);
  const nameText = nameMatch ? nameMatch[1] : noArray;
  let beforeName = nameMatch ? noArray.slice(0, nameMatch.index).trim() : "";
  const isByRef = beforeName.includes("&");
  beforeName = beforeName.replace(/&/g, "").trim();
  return {
    typeText: beforeName,
    nameText,
    isByRef,
    isArray,
  };
}

function extractDocComment(originalLines: string[], functionStartLine: number): string {
  let i = functionStartLine - 1;
  while (i >= 0 && originalLines[i].trim() === "") {
    i--;
  }
  if (i < 0) {
    return "";
  }

  const line = originalLines[i].trim();
  if (line.startsWith("//")) {
    const buf: string[] = [];
    while (i >= 0 && originalLines[i].trim().startsWith("//")) {
      buf.push(originalLines[i].trim().replace(/^\/\/\s?/, ""));
      i--;
    }
    buf.reverse();
    return buf.join("\n").trim();
  }

  if (line.endsWith("*/") || line.includes("*/")) {
    const buf: string[] = [];
    while (i >= 0) {
      buf.push(originalLines[i]);
      if (originalLines[i].includes("/*")) {
        break;
      }
      i--;
    }
    buf.reverse();
    const block = buf.join("\n");
    return block
      .replace(/^\s*\/\*+/, "")
      .replace(/\*+\/\s*$/, "")
      .split(/\r?\n/)
      .map((s) => s.replace(/^\s*\*\s?/, ""))
      .join("\n")
      .trim();
  }

  return "";
}

function toRelativeIncludePath(absoluteFile: string, libraryRoots: string[]): string {
  const normalizedFile = path.win32.normalize(absoluteFile);
  for (const root of libraryRoots) {
    const relative = path.win32.relative(path.win32.normalize(root), normalizedFile);
    if (relative !== "" && !relative.startsWith("..") && !path.win32.isAbsolute(relative)) {
      return toWindowsLike(relative);
    }
  }
  return normalizedFile;
}

function parseFunctionsAndNamespaces(
  text: string,
  filePath: string,
  libraryRoots: string[]
): HslFunctionSymbol[] {
  const sanitized = sanitizeForParsing(text);
  const originalLines = text.split(/\r?\n/);
  const cleanLines = sanitized.split(/\r?\n/);
  const functions: HslFunctionSymbol[] = [];

  const namespaceStack: Array<{ name: string; depth: number }> = [];
  let braceDepth = 0;
  let pendingNamespace: string | null = null;

  let collectingFunction = false;
  let functionStartLine = -1;
  let functionHeaderParts: string[] = [];

  for (let lineIndex = 0; lineIndex < cleanLines.length; lineIndex++) {
    const cleanLine = cleanLines[lineIndex];
    const originalLine = originalLines[lineIndex] ?? "";

    if (!collectingFunction) {
      const nsMatch = /^\s*(?:(?:private|public|static|global|const|synchronized)\s+)*namespace\s+([A-Za-z_]\w*)\b/.exec(
        cleanLine
      );
      if (nsMatch) {
        pendingNamespace = nsMatch[1];
      }

      if (/^\s*(?:(?:private|public|static|global|const|synchronized)\s+)*function\b/.test(cleanLine)) {
        collectingFunction = true;
        functionStartLine = lineIndex;
        functionHeaderParts = [originalLine];
      }
    } else {
      functionHeaderParts.push(originalLine);
    }

    if (collectingFunction) {
      const joinedClean = sanitizeForParsing(functionHeaderParts.join("\n"));
      const parenDelta = (joinedClean.match(/\(/g)?.length ?? 0) - (joinedClean.match(/\)/g)?.length ?? 0);
      const hasTerminator = /[;{]/.test(joinedClean);
      if (parenDelta <= 0 && hasTerminator) {
        const joinedOriginal = functionHeaderParts.join("\n");
        const fnMatch = /^\s*((?:(?:private|public|static|global|const|synchronized)\s+)*)function\s+([A-Za-z_]\w*)\s*\(([\s\S]*?)\)\s*([A-Za-z_]\w*)\s*(?:;|\{)/m.exec(
          joinedOriginal
        );
        if (fnMatch) {
          const modifiers = fnMatch[1] ?? "";
          const name = fnMatch[2];
          const paramsRaw = fnMatch[3] ?? "";
          const returnTypeText = fnMatch[4] ?? "variable";

          const parameters = splitArgs(paramsRaw)
            .filter((p) => p.length > 0)
            .map(parseParameter);

          const nsPrefix = namespaceStack.map((n) => n.name).join("::");
          const qualifiedName = nsPrefix.length > 0 ? `${nsPrefix}::${name}` : name;
          const docComment = extractDocComment(originalLines, functionStartLine);
          const includeText = toRelativeIncludePath(filePath, libraryRoots);

          functions.push({
            name,
            qualifiedName,
            parameters,
            returnTypeText,
            docComment,
            definedInFile: path.win32.normalize(filePath),
            requiredInclude: {
              includeText,
              resolvedPath: path.win32.normalize(filePath),
            },
            isPrivate: /\bprivate\b/.test(modifiers),
          });
        }

        collectingFunction = false;
        functionStartLine = -1;
        functionHeaderParts = [];
      }
    }

    for (const ch of cleanLine) {
      if (ch === "{") {
        braceDepth++;
        if (pendingNamespace) {
          namespaceStack.push({ name: pendingNamespace, depth: braceDepth });
          pendingNamespace = null;
        }
      } else if (ch === "}") {
        while (namespaceStack.length > 0 && namespaceStack[namespaceStack.length - 1].depth >= braceDepth) {
          namespaceStack.pop();
        }
        braceDepth = Math.max(0, braceDepth - 1);
      }
    }
  }

  return functions;
}

async function* walkHslFiles(root: string): AsyncGenerator<string> {
  let entries: fs.Dirent[] = [];
  try {
    entries = await fs.promises.readdir(root, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    const full = path.win32.join(root, entry.name);
    if (entry.isDirectory()) {
      yield* walkHslFiles(full);
      continue;
    }
    const lower = full.toLowerCase();
    if (entry.isFile() && (lower.endsWith(".hsl") || lower.endsWith(".hs_") || lower.endsWith(".hsi"))) {
      yield full;
    }
  }
}

class HslIndexService {
  private readonly context: vscode.ExtensionContext;
  private readonly parseCache = new Map<string, FileParseResult>();
  private readonly globalSymbols: HslFunctionSymbol[] = [];
  private readonly unresolvedIncludeCollection = vscode.languages.createDiagnosticCollection("hsl-includes");
  private buildComplete = false;
  private buildPromise: Promise<void> | undefined;
  private indexGeneration = 0;

  constructor(context: vscode.ExtensionContext) {
    this.context = context;
  }

  public register(context: vscode.ExtensionContext): void {
    context.subscriptions.push(this.unresolvedIncludeCollection);

    const provider = vscode.languages.registerCompletionItemProvider(
      { language: "hsl", scheme: "file" },
      {
        provideCompletionItems: async (document) => {
          const visible = await this.getVisibleSymbolContext(document);
          await this.ensureGlobalIndex();
          const allItems = this.buildCompletionItems(document, visible);
          this.publishUnresolvedIncludeDiagnostics(document);
          return allItems;
        },
      },
      ":"
    );

    const hoverProvider = vscode.languages.registerHoverProvider(
      { language: "hsl", scheme: "file" },
      {
        provideHover: async (document, position) => {
          const token = this.getSymbolTokenAtPosition(document, position);
          if (!token) {
            return undefined;
          }

          const isMethod = this.isMethodCallContext(document, position);
          const symbol = await this.findBestSymbol(document, token, isMethod);
          if (!symbol) {
            return undefined;
          }

          return new vscode.Hover(markdownForSymbol(symbol));
        },
      }
    );

    const signatureProvider = vscode.languages.registerSignatureHelpProvider(
      { language: "hsl", scheme: "file" },
      {
        provideSignatureHelp: async (document, position) => {
          const call = this.findCallAtPosition(document, position);
          if (!call) {
            return undefined;
          }

          const symbol = await this.findBestSymbol(document, call.name, call.isMethod);
          if (!symbol) {
            return undefined;
          }

          const sig = new vscode.SignatureInformation(
            `${symbol.qualifiedName}(${symbol.parameters
              .map((p) => `${p.typeText}${p.isByRef ? "&" : ""} ${p.nameText}${p.isArray ? "[]" : ""}`)
              .join(", ")}) ${symbol.returnTypeText}`,
            symbol.docComment
          );
          sig.parameters = symbol.parameters.map(
            (p) => new vscode.ParameterInformation(`${p.typeText}${p.isByRef ? "&" : ""} ${p.nameText}${p.isArray ? "[]" : ""}`)
          );

          const help = new vscode.SignatureHelp();
          help.signatures = [sig];
          help.activeSignature = 0;
          help.activeParameter = Math.min(call.activeParameter, Math.max(0, symbol.parameters.length - 1));
          return help;
        },
      },
      "(",
      ","
    );

    context.subscriptions.push(provider, hoverProvider, signatureProvider);

    const includeTimers = new Map<string, ReturnType<typeof setTimeout>>();
    const scheduleIncludeDiagnostics = (document: vscode.TextDocument): void => {
      const key = document.uri.toString();
      const existing = includeTimers.get(key);
      if (existing) {
        clearTimeout(existing);
      }
      includeTimers.set(
        key,
        setTimeout(() => {
          includeTimers.delete(key);
          if (!document.isClosed) {
            this.publishUnresolvedIncludeDiagnostics(document);
          }
        }, 150)
      );
    };
    context.subscriptions.push(
      vscode.workspace.onDidOpenTextDocument((document) => {
        if (document.languageId === "hsl") {
          scheduleIncludeDiagnostics(document);
        }
      }),
      vscode.workspace.onDidChangeTextDocument((event) => {
        if (event.document.languageId === "hsl") {
          scheduleIncludeDiagnostics(event.document);
        }
      }),
      vscode.workspace.onDidCloseTextDocument((document) => {
        const key = document.uri.toString();
        const timer = includeTimers.get(key);
        if (timer) {
          clearTimeout(timer);
          includeTimers.delete(key);
        }
        this.unresolvedIncludeCollection.delete(document.uri);
      }),
      { dispose: () => includeTimers.forEach((timer) => clearTimeout(timer)) }
    );

    for (const root of getLibraryRoots()) {
      const watcher = vscode.workspace.createFileSystemWatcher(
        new vscode.RelativePattern(root, "**/*.{hsl,hs_,hsi}")
      );
      const invalidate = (): void => this.invalidateGlobalIndex();
      watcher.onDidCreate(invalidate, undefined, context.subscriptions);
      watcher.onDidChange(invalidate, undefined, context.subscriptions);
      watcher.onDidDelete(invalidate, undefined, context.subscriptions);
      context.subscriptions.push(watcher);
    }

    void this.ensureGlobalIndex().catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`HSL global index failed: ${message}`);
    });
  }

  public async getVisibleSymbolContext(document: vscode.TextDocument): Promise<VisibleSymbolContext> {
    const visited = new Set<string>();
    const symbols: HslFunctionSymbol[] = [];
    const visibleFiles = new Set<string>();
    const queue: Array<{ filePath: string; inlineText?: string }> = [
      { filePath: document.uri.fsPath, inlineText: document.getText() },
    ];

    while (queue.length > 0) {
      const next = queue.shift();
      if (!next) {
        continue;
      }

      const normalized = normalizeWindowsPath(next.filePath);
      if (visited.has(normalized)) {
        continue;
      }
      visited.add(normalized);

      const parsed = await this.getParseResult(next.filePath, next.inlineText);
      if (!parsed) {
        continue;
      }
      visibleFiles.add(normalized);
      const isCurrentDocument =
        normalized === normalizeWindowsPath(document.uri.fsPath);
      for (const fn of parsed.functions) {
        if (!fn.isPrivate || isCurrentDocument) {
          symbols.push(fn);
        }
      }
      for (const inc of parsed.includes) {
        if (inc.resolvedPath) {
          queue.push({ filePath: inc.resolvedPath });
        }
      }
    }

    return { symbols, visibleFiles };
  }

  private buildCompletionItems(document: vscode.TextDocument, visible: VisibleSymbolContext): vscode.CompletionItem[] {
    const items: vscode.CompletionItem[] = [];
    const seen = new Set<string>();

    const pushSymbol = (symbol: HslFunctionSymbol, source: "visible" | "global") => {
      const key = `${symbol.qualifiedName}|${normalizeWindowsPath(symbol.definedInFile)}`;
      if (seen.has(key)) {
        return;
      }
      seen.add(key);

      const signature = `${symbol.qualifiedName}(${symbol.parameters
        .map((p) => `${p.typeText}${p.isByRef ? "&" : ""} ${p.nameText}${p.isArray ? "[]" : ""}`)
        .join(", ")})`;

      const item = new vscode.CompletionItem(symbol.qualifiedName, vscode.CompletionItemKind.Function);
      item.detail = `${signature} : ${symbol.returnTypeText}`;
      item.documentation = markdownForSymbol(symbol);
      item.insertText = new vscode.SnippetString(`${symbol.qualifiedName}($0)`);
      item.filterText = `${symbol.qualifiedName} ${symbol.name}`;
      item.sortText = source === "visible" ? `0_${symbol.qualifiedName}` : `1_${symbol.qualifiedName}`;

      if (source === "global") {
        const definingNormalized = normalizeWindowsPath(symbol.definedInFile);
        const alreadyVisible = visible.visibleFiles.has(definingNormalized);
        if (!alreadyVisible) {
          const includeEdit = this.getIncludeInsertionEdit(document, symbol.definedInFile);
          if (includeEdit) {
            item.additionalTextEdits = [includeEdit];
          }
        }
      }

      items.push(item);
    };

    for (const symbol of visible.symbols) {
      pushSymbol(symbol, "visible");
    }

    for (const symbol of this.globalSymbols) {
      if (!symbol.isPrivate) {
        pushSymbol(symbol, "global");
      }
    }

    return items;
  }

  private chooseIncludeStyle(document: vscode.TextDocument): IncludeStyle {
    const includes = this.readDocumentIncludes(document);
    if (includes.length === 0) {
      return "relative";
    }
    let abs = 0;
    let rel = 0;
    for (const inc of includes) {
      if (isAbsoluteWindowsPath(inc.rawTarget)) {
        abs++;
      } else {
        rel++;
      }
    }
    return abs > rel ? "absolute" : "relative";
  }

  private readDocumentIncludes(document: vscode.TextDocument): ParsedInclude[] {
    return extractIncludeTargets(document.getText(), getLibraryRoots(), document.uri.fsPath);
  }

  private getIncludeInsertionEdit(document: vscode.TextDocument, absoluteIncludeFile: string): vscode.TextEdit | null {
    const includes = this.readDocumentIncludes(document);
    const normalizedTarget = normalizeWindowsPath(absoluteIncludeFile);

    for (const inc of includes) {
      if (inc.resolvedPath && normalizeWindowsPath(inc.resolvedPath) === normalizedTarget) {
        return null;
      }
    }

    const style = this.chooseIncludeStyle(document);
    const libraryRoots = getLibraryRoots();
    const includeText = style === "absolute"
      ? path.win32.normalize(absoluteIncludeFile)
      : toRelativeIncludePath(absoluteIncludeFile, libraryRoots);
    const includeLine = `#include "${includeText}"`;
    const insertLine = this.findIncludeInsertLine(document);
    const eol = document.eol === vscode.EndOfLine.CRLF ? "\r\n" : "\n";

    return new vscode.TextEdit(new vscode.Range(insertLine, 0, insertLine, 0), `${includeLine}${eol}`);
  }

  private findIncludeInsertLine(document: vscode.TextDocument): number {
    let pragmaOnceLine = -1;
    let lastIncludeLine = -1;
    let ifndefLine = -1;
    let defineLine = -1;

    for (let i = 0; i < document.lineCount; i++) {
      const text = document.lineAt(i).text.trim();
      if (/^#pragma\s+once\b/.test(text)) {
        pragmaOnceLine = i;
      }
      if (/^#include\s+"[^"]+"/.test(text)) {
        lastIncludeLine = i;
      }
      if (ifndefLine < 0 && /^#ifndef\b/.test(text)) {
        ifndefLine = i;
      }
      if (defineLine < 0 && /^#define\b/.test(text)) {
        defineLine = i;
      }
    }

    if (pragmaOnceLine >= 0) {
      return pragmaOnceLine + 1;
    }
    if (lastIncludeLine >= 0) {
      return lastIncludeLine + 1;
    }
    if (ifndefLine >= 0 && defineLine >= ifndefLine) {
      return defineLine + 1;
    }
    return 0;
  }

  private async findBestSymbol(document: vscode.TextDocument, symbolText: string, isMethodCall: boolean = false): Promise<HslFunctionSymbol | undefined> {
    // For method calls (preceded by "."), system-defined element functions
    // ALWAYS take priority over library-parsed symbols.
    if (isMethodCall) {
      const systemSymbols = SYSTEM_ELEMENT_SYMBOL_MAP.get(symbolText.toLowerCase());
      if (systemSymbols && systemSymbols.length > 0) {
        return systemSymbols[0];
      }
    }

    const visible = await this.getVisibleSymbolContext(document);

    // For non-method calls, also check if the symbol shadows a system-defined
    // name.  System definitions always win.
    const lowerText = symbolText.toLowerCase();
    const simpleName = symbolText.includes("::") ? symbolText.split("::").pop()!.toLowerCase() : lowerText;
    if (SYSTEM_ELEMENT_SYMBOL_MAP.has(simpleName)) {
      const systemSymbols = SYSTEM_ELEMENT_SYMBOL_MAP.get(simpleName)!;
      // If the qualified name matches an element function's qualified name, use it
      for (const sys of systemSymbols) {
        if (sys.qualifiedName.toLowerCase() === lowerText) {
          return sys;
        }
      }
    }

    const visibleMatch =
      visible.symbols.find((s) => s.qualifiedName === symbolText) ??
      visible.symbols.find((s) => s.name === symbolText);
    if (visibleMatch) {
      return visibleMatch;
    }

    await this.ensureGlobalIndex();
    const exactGlobal = this.globalSymbols.find((s) => s.qualifiedName === symbolText);
    if (exactGlobal) {
      return exactGlobal;
    }
    return this.globalSymbols.find((s) => s.name === symbolText);
  }

  private getSymbolTokenAtPosition(document: vscode.TextDocument, position: vscode.Position): string | null {
    const line = document.lineAt(position.line).text;
    let start = position.character;
    let end = position.character;

    while (start > 0 && /[A-Za-z0-9_:]/.test(line[start - 1])) {
      start--;
    }
    while (end < line.length && /[A-Za-z0-9_:]/.test(line[end])) {
      end++;
    }

    const token = line.slice(start, end).trim();
    return token.length > 0 ? token : null;
  }

  /**
   * Returns true if the token at `position` is preceded by a `.`, indicating
   * that it is a method call on an object (e.g. `obj.CreateObject`).
   */
  private isMethodCallContext(document: vscode.TextDocument, position: vscode.Position): boolean {
    const line = document.lineAt(position.line).text;
    let start = position.character;
    while (start > 0 && /[A-Za-z0-9_:]/.test(line[start - 1])) {
      start--;
    }
    // Walk backwards over any whitespace then check for '.'
    let check = start - 1;
    while (check >= 0 && (line[check] === " " || line[check] === "\t")) {
      check--;
    }
    return check >= 0 && line[check] === ".";
  }

  private findCallAtPosition(
    document: vscode.TextDocument,
    position: vscode.Position
  ): { name: string; activeParameter: number; isMethod: boolean } | null {
    const offset = document.offsetAt(position);
    const text = document.getText();
    let depth = 0;
    let openIndex = -1;

    for (let i = offset - 1; i >= 0; i--) {
      const ch = text[i];
      if (ch === ")") {
        depth++;
      } else if (ch === "(") {
        if (depth === 0) {
          openIndex = i;
          break;
        }
        depth--;
      }
    }

    if (openIndex < 0) {
      return null;
    }

    let nameEnd = openIndex;
    let nameStart = openIndex;
    while (nameStart > 0 && /[A-Za-z0-9_:]/.test(text[nameStart - 1])) {
      nameStart--;
    }
    const name = text.slice(nameStart, nameEnd).trim();
    if (name.length === 0) {
      return null;
    }

    // If the line starts with "variable " or "variable& ", this is a variable
    // declaration -- parentheses are initialisers, not function calls.
    const lineStart = text.lastIndexOf("\n", nameStart - 1) + 1;
    const linePrefix = text.slice(lineStart, nameStart).trimStart();
    if (/^variable&?\s/i.test(linePrefix)) {
      return null;
    }

    let activeParameter = 0;
    let nested = 0;
    for (let i = openIndex + 1; i < offset; i++) {
      const ch = text[i];
      if (ch === "(") {
        nested++;
      } else if (ch === ")") {
        nested = Math.max(0, nested - 1);
      } else if (ch === "," && nested === 0) {
        activeParameter++;
      }
    }

    // Detect if this call is a method call (preceded by ".")
    let isMethod = false;
    if (nameStart > 0) {
      let dotCheck = nameStart - 1;
      while (dotCheck >= 0 && (text[dotCheck] === " " || text[dotCheck] === "\t")) {
        dotCheck--;
      }
      isMethod = dotCheck >= 0 && text[dotCheck] === ".";
    }

    return { name, activeParameter, isMethod };
  }

  private ensureGlobalIndex(): Promise<void> {
    if (this.buildComplete) {
      return Promise.resolve();
    }
    if (this.buildPromise) {
      return this.buildPromise;
    }
    this.buildPromise = this.buildGlobalIndex().finally(() => {
      this.buildPromise = undefined;
    });
    return this.buildPromise;
  }

  private invalidateGlobalIndex(): void {
    this.indexGeneration++;
    this.buildComplete = false;
    this.parseCache.clear();
    this.globalSymbols.length = 0;
    const rebuild = this.buildPromise
      ? this.buildPromise.catch(() => undefined).then(() => this.ensureGlobalIndex())
      : this.ensureGlobalIndex();
    void rebuild.catch((error: unknown) => {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`HSL global index rebuild failed: ${message}`);
    });
  }

  private async buildGlobalIndex(): Promise<void> {
    if (this.buildComplete) {
      return;
    }

    const generation = this.indexGeneration;
    const libraryRoots = getLibraryRoots();
    await fs.promises.mkdir(this.context.globalStorageUri.fsPath, { recursive: true });

    const cachePath = path.join(this.context.globalStorageUri.fsPath, INDEX_CACHE_FILE);
    const cache = await this.readGlobalCache(cachePath, libraryRoots);
    const cacheByPath = new Map<string, CacheFileRecord>();
    for (const record of cache?.records ?? []) {
      cacheByPath.set(normalizeWindowsPath(record.filePath), record);
    }

    const newRecords: CacheFileRecord[] = [];

    const indexedPaths = new Set<string>();
    for (const libraryRoot of libraryRoots) {
      for await (const hslFile of walkHslFiles(libraryRoot)) {
      const normalizedFile = normalizeWindowsPath(hslFile);
      if (indexedPaths.has(normalizedFile)) {
        continue;
      }
      indexedPaths.add(normalizedFile);
      let stat: fs.Stats;
      try {
        stat = await fs.promises.stat(hslFile);
      } catch {
        continue;
      }

      const norm = normalizeWindowsPath(hslFile);
      const cached = cacheByPath.get(norm);
      if (cached && cached.mtimeMs === stat.mtimeMs) {
        newRecords.push(cached);
        continue;
      }

      let text = "";
      try {
        text = await fs.promises.readFile(hslFile, "utf8");
      } catch {
        continue;
      }

      const includes = extractIncludeTargets(text, libraryRoots, hslFile);
      const functions = parseFunctionsAndNamespaces(text, hslFile, libraryRoots);
      newRecords.push({
        filePath: path.win32.normalize(hslFile),
        mtimeMs: stat.mtimeMs,
        includes,
        functions,
      });
      }
    }

    const cacheToWrite: GlobalCacheFile = {
      version: 2,
      libraryRoots,
      createdAt: Date.now(),
      records: newRecords,
    };

    try {
      await fs.promises.writeFile(cachePath, JSON.stringify(cacheToWrite), "utf8");
    } catch {
      // cache write is best-effort only
    }

    if (generation !== this.indexGeneration) {
      return;
    }

    this.globalSymbols.length = 0;

    for (const record of newRecords) {
      const parsed: FileParseResult = {
        filePath: record.filePath,
        mtimeMs: record.mtimeMs,
        includes: record.includes,
        functions: record.functions,
      };
      this.parseCache.set(normalizeWindowsPath(record.filePath), parsed);

      for (const fn of record.functions) {
        if (fn.isPrivate) {
          continue;
        }
        this.globalSymbols.push(fn);
      }
    }

    if (generation === this.indexGeneration) {
      this.buildComplete = true;
    }
  }

  private async readGlobalCache(cachePath: string, libraryRoots: string[]): Promise<GlobalCacheFile | null> {
    try {
      const raw = await fs.promises.readFile(cachePath, "utf8");
      const parsed = JSON.parse(raw) as GlobalCacheFile;
      if (parsed.version !== 2 || !Array.isArray(parsed.libraryRoots)) {
        return null;
      }
      const cachedRoots = parsed.libraryRoots.map(normalizeWindowsPath).sort();
      const currentRoots = libraryRoots.map(normalizeWindowsPath).sort();
      if (JSON.stringify(cachedRoots) !== JSON.stringify(currentRoots)) {
        return null;
      }
      return parsed;
    } catch {
      return null;
    }
  }

  private async getParseResult(filePath: string, inlineText?: string): Promise<FileParseResult | null> {
    const normalized = normalizeWindowsPath(filePath);

    if (!inlineText) {
      const cached = this.parseCache.get(normalized);
      if (cached) {
        try {
          const stat = await fs.promises.stat(filePath);
          if (cached.mtimeMs === stat.mtimeMs) {
            return cached;
          }
        } catch {
          return cached;
        }
      }
    }

    let text = inlineText;
    if (text === undefined) {
      try {
        text = await fs.promises.readFile(filePath, "utf8");
      } catch {
        return null;
      }
    }

    if (text === undefined) {
      return null;
    }

    const libraryRoots = getLibraryRoots();
    const includes = extractIncludeTargets(text, libraryRoots, filePath);
    const functions = parseFunctionsAndNamespaces(text, filePath, libraryRoots);

    let mtimeMs = Date.now();
    if (!inlineText) {
      try {
        mtimeMs = (await fs.promises.stat(filePath)).mtimeMs;
      } catch {
        // leave default
      }
    }

    const parsed: FileParseResult = {
      filePath: path.win32.normalize(filePath),
      mtimeMs,
      includes,
      functions,
    };

    this.parseCache.set(normalized, parsed);
    return parsed;
  }

  private publishUnresolvedIncludeDiagnostics(document: vscode.TextDocument): void {
    const includes = this.readDocumentIncludes(document);
    const diagnostics: vscode.Diagnostic[] = [];
    const lines = document.getText().split(/\r?\n/);

    includes.forEach((inc) => {
      if (inc.resolvedPath) {
        return;
      }

      const lineIndex = Math.min(Math.max(inc.lineIndex, 0), lines.length - 1);
      const line = lines[lineIndex] ?? "";
      const idx = Math.max(0, line.indexOf("#include"));
      const range = new vscode.Range(lineIndex, idx, lineIndex, line.length);
      const diagnostic = new vscode.Diagnostic(
        range,
        `Unable to resolve include target \"${inc.rawTarget}\" from the current file or configured HSL roots: ${getLibraryRoots().join(", ")}.`,
        vscode.DiagnosticSeverity.Warning
      );
      diagnostic.source = "hsl-intellisense";
      diagnostics.push(diagnostic);
    });

    this.unresolvedIncludeCollection.set(document.uri, diagnostics);
  }
}

let indexServiceInstance: HslIndexService | null = null;

export function getHslIndexService(): HslIndexService | null {
  return indexServiceInstance;
}

export function registerHslIntelliSense(
  context: vscode.ExtensionContext
): void {
  const service = new HslIndexService(context);
  indexServiceInstance = service;
  service.register(context);
}
