import * as vscode from "vscode";
import { randomBytes } from "crypto";

// ─── Step CLSID → human-readable names ──────────────────────────────────────────
const STEP_NAMES: Record<string, { name: string; icon: string; color: string }> = {
  "1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2": { name: "Initialize", icon: "⚡", color: "#4CAF50" },
  "541143F5_7FA2_11D3_AD85_0004ACB1DCB2": { name: "Aspirate", icon: "🔼", color: "#2196F3" },
  "541143F8_7FA2_11D3_AD85_0004ACB1DCB2": { name: "Dispense", icon: "🔽", color: "#FF9800" },
  "541143FA_7FA2_11D3_AD85_0004ACB1DCB2": { name: "TipPickUp", icon: "📌", color: "#9C27B0" },
  "541143FC_7FA2_11D3_AD85_0004ACB1DCB2": { name: "TipEject", icon: "📤", color: "#795548" },
  "54114400_7FA2_11D3_AD85_0004ACB1DCB2": { name: "UnloadCarrier", icon: "📦", color: "#607D8B" },
  "54114402_7FA2_11D3_AD85_0004ACB1DCB2": { name: "LoadCarrier", icon: "📥", color: "#607D8B" },
  "827392A0_B7E8_4472_9ED3_B45B71B5D27A": { name: "Head96 Aspirate", icon: "🔼", color: "#1565C0" },
  "A48573A5_62ED_4951_9EF9_03207EFE34FB": { name: "Head96 Dispense", icon: "🔽", color: "#E65100" },
  "BD0D210B_0816_4C86_A903_D6B2DF73F78B": { name: "Head96 TipPickUp", icon: "📌", color: "#6A1B9A" },
  "2880E77A_3D6D_40FE_AF57_1BD1FE13960C": { name: "Head96 TipEject", icon: "📤", color: "#4E342E" },
  "EA251BFB_66DE_48D1_83E5_6884B4DD8D11": { name: "MoveAutoLoad", icon: "🔄", color: "#00796B" },
};

interface ParsedStep {
  line: number;
  type: "device-step" | "function-call" | "comment" | "variable-decl" | "control-flow" | "include" | "namespace-open" | "namespace-close" | "method-start" | "method-end";
  text: string;
  stepName?: string;
  stepIcon?: string;
  stepColor?: string;
  guid?: string;
}

function parseHslDocument(text: string): ParsedStep[] {
  const lines = text.split(/\r?\n/);
  const steps: ParsedStep[] = [];

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed.length === 0) {
      continue;
    }

    // Skip block markers
    if (/^\s*\/\/\s*\{\{/.test(line) || /^\s*\/\/\s*\}\}/.test(line)) {
      continue;
    }

    // #include directives
    if (/^\s*#include\s+"/.test(line)) {
      const match = line.match(/#include\s+"([^"]+)"/);
      steps.push({
        line: i + 1,
        type: "include",
        text: match ? match[1] : trimmed,
      });
      continue;
    }

    // Device step calls: ML_STAR._<CLSID>("guid"); // StepName
    const deviceMatch = trimmed.match(
      /(\w+)\._([A-Fa-f0-9_]+)\s*\(\s*"([^"]*)"\s*\)\s*;?\s*(?:\/\/\s*(.*))?$/
    );
    if (deviceMatch) {
      const clsid = deviceMatch[2];
      const guid = deviceMatch[3];
      const inlineComment = deviceMatch[4]?.trim();
      const stepInfo = STEP_NAMES[clsid];
      steps.push({
        line: i + 1,
        type: "device-step",
        text: inlineComment || stepInfo?.name || `Step ${clsid.substring(0, 8)}...`,
        stepName: stepInfo?.name || inlineComment || "Unknown Step",
        stepIcon: stepInfo?.icon || "⚙️",
        stepColor: stepInfo?.color || "#757575",
        guid: guid,
      });
      continue;
    }

    // method main()
    if (/^\s*method\s+main\s*\(\s*\)/.test(line)) {
      steps.push({ line: i + 1, type: "method-start", text: "method main()" });
      continue;
    }

    // namespace open
    const nsMatch = trimmed.match(/^namespace\s+(\w+)/);
    if (nsMatch) {
      steps.push({ line: i + 1, type: "namespace-open", text: `namespace ${nsMatch[1]}` });
      continue;
    }

    // function/method declarations (forward declarations -- skip)
    if (/^\s*(private\s+)?function\s+\w+\s*\(.*\)\s*\w+\s*;/.test(trimmed)) {
      continue;
    }

    // function/method definitions
    const fnMatch = trimmed.match(/^(?:private\s+)?function\s+(\w+)\s*\(/);
    if (fnMatch) {
      steps.push({
        line: i + 1,
        type: "function-call",
        text: `function ${fnMatch[1]}(...)`,
        stepIcon: "📋",
        stepColor: "#00897B",
      });
      continue;
    }

    // Single-line comments (standalone, not inline)
    if (/^\s*\/\//.test(line) && !/^\s*\/\/\s*[{}]/.test(line)) {
      const commentText = trimmed.replace(/^\/\/\s*/, "");
      if (commentText.length > 2 && !commentText.startsWith("#")) {
        steps.push({ line: i + 1, type: "comment", text: commentText });
      }
      continue;
    }

    // Control flow: if, while, for, loop
    if (/^\s*(if|else|while|for|loop)\b/.test(line)) {
      steps.push({
        line: i + 1,
        type: "control-flow",
        text: trimmed.replace(/\{?\s*$/, "").trim(),
      });
      continue;
    }

    // Variable declarations
    if (/^\s*(variable|string|sequence|device|object|timer|event|file|resource|dialog)\s+/.test(line)) {
      steps.push({ line: i + 1, type: "variable-decl", text: trimmed.replace(/;$/, "") });
      continue;
    }
  }

  return steps;
}

function getWebviewContent(
  webview: vscode.Webview,
  steps: ParsedStep[],
  docName: string
): string {
  const nonce = randomBytes(16).toString("base64");
  const stepsHtml = steps
    .map((step) => {
      switch (step.type) {
        case "include":
          return `<div class="step include" data-line="${step.line}">
            <span class="step-icon">📂</span>
            <span class="step-label">#include "${escapeHtml(step.text)}"</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "method-start":
          return `<div class="step method-start" data-line="${step.line}">
            <span class="step-icon">▶️</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "namespace-open":
          return `<div class="step namespace" data-line="${step.line}">
            <span class="step-icon">📁</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "device-step":
          return `<div class="step device-step" data-line="${step.line}" style="border-left-color: ${step.stepColor}">
            <span class="step-icon">${step.stepIcon}</span>
            <div class="step-content">
              <span class="step-name">${escapeHtml(step.stepName ?? "")}</span>
              <span class="step-detail">${escapeHtml(step.text)}</span>
              ${step.guid ? `<span class="step-guid" title="${escapeHtml(step.guid)}">${escapeHtml(step.guid.substring(0, 16))}…</span>` : ""}
            </div>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "function-call":
          return `<div class="step function" data-line="${step.line}">
            <span class="step-icon">${step.stepIcon ?? "📋"}</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "control-flow":
          return `<div class="step control-flow" data-line="${step.line}">
            <span class="step-icon">🔀</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "variable-decl":
          return `<div class="step variable" data-line="${step.line}">
            <span class="step-icon">📝</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        case "comment":
          return `<div class="step comment" data-line="${step.line}">
            <span class="step-icon">💬</span>
            <span class="step-label">${escapeHtml(step.text)}</span>
            <span class="step-line">L${step.line}</span>
          </div>`;

        default:
          return "";
      }
    })
    .join("\n");

  const deviceStepCount = steps.filter((s) => s.type === "device-step").length;
  const functionCount = steps.filter((s) => s.type === "function-call").length;
  const includeCount = steps.filter((s) => s.type === "include").length;

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src ${webview.cspSource} 'unsafe-inline'; script-src 'nonce-${nonce}';">
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Hamilton HSL Method Editor</title>
  <style>
    :root {
      --bg: var(--vscode-editor-background);
      --fg: var(--vscode-editor-foreground);
      --border: var(--vscode-panel-border, #444);
      --hover: var(--vscode-list-hoverBackground);
      --badge-bg: var(--vscode-badge-background);
      --badge-fg: var(--vscode-badge-foreground);
      --header-bg: var(--vscode-sideBarSectionHeader-background, #252526);
    }

    * { margin: 0; padding: 0; box-sizing: border-box; }

    body {
      background: var(--bg);
      color: var(--fg);
      font-family: var(--vscode-font-family, 'Segoe UI', sans-serif);
      font-size: 13px;
      padding: 0;
    }

    .header {
      background: var(--header-bg);
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 12px;
      position: sticky;
      top: 0;
      z-index: 10;
    }

    .header-icon {
      font-size: 24px;
    }

    .header-info h1 {
      font-size: 14px;
      font-weight: 600;
      margin-bottom: 2px;
    }

    .header-info .subtitle {
      font-size: 11px;
      opacity: 0.7;
    }

    .stats {
      display: flex;
      gap: 8px;
      margin-left: auto;
    }

    .stat-badge {
      background: var(--badge-bg);
      color: var(--badge-fg);
      padding: 2px 8px;
      border-radius: 10px;
      font-size: 11px;
      white-space: nowrap;
    }

    .toolbar {
      padding: 8px 16px;
      display: flex;
      gap: 6px;
      border-bottom: 1px solid var(--border);
    }

    .toolbar button {
      background: var(--vscode-button-secondaryBackground, #3a3d41);
      color: var(--vscode-button-secondaryForeground, #ccc);
      border: none;
      padding: 4px 10px;
      border-radius: 3px;
      cursor: pointer;
      font-size: 12px;
    }

    .toolbar button:hover {
      background: var(--vscode-button-secondaryHoverBackground, #505357);
    }

    .toolbar button.active {
      background: var(--vscode-button-background, #0e639c);
      color: var(--vscode-button-foreground, #fff);
    }

    .step-list {
      padding: 8px 16px;
    }

    .step {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 6px 10px;
      margin: 2px 0;
      border-radius: 4px;
      cursor: pointer;
      border-left: 3px solid transparent;
      transition: background 0.1s;
    }

    .step:hover {
      background: var(--hover);
    }

    .step-icon {
      font-size: 14px;
      width: 22px;
      text-align: center;
      flex-shrink: 0;
    }

    .step-label {
      flex: 1;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .step-line {
      font-size: 11px;
      opacity: 0.5;
      flex-shrink: 0;
      font-family: var(--vscode-editor-font-family, monospace);
    }

    .step.device-step {
      border-left-width: 3px;
      border-left-style: solid;
    }

    .step.device-step .step-content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: 1px;
      overflow: hidden;
    }

    .step.device-step .step-name {
      font-weight: 600;
      font-size: 13px;
    }

    .step.device-step .step-detail {
      font-size: 11px;
      opacity: 0.7;
    }

    .step.device-step .step-guid {
      font-size: 10px;
      opacity: 0.4;
      font-family: var(--vscode-editor-font-family, monospace);
    }

    .step.include { opacity: 0.8; }
    .step.comment { opacity: 0.6; font-style: italic; }
    .step.variable { opacity: 0.7; }

    .step.method-start {
      border-left: 3px solid #4CAF50;
      font-weight: 600;
    }

    .step.namespace {
      border-left: 3px solid #FF9800;
      font-weight: 500;
    }

    .step.control-flow {
      border-left: 3px solid #03A9F4;
    }

    .step.function {
      border-left: 3px solid #00897B;
    }

    .empty-state {
      text-align: center;
      padding: 40px;
      opacity: 0.6;
    }

    .empty-state .icon { font-size: 48px; margin-bottom: 12px; }

    .hidden { display: none !important; }
  </style>
</head>
<body>
  <div class="header">
    <span class="header-icon">🧪</span>
    <div class="header-info">
      <h1>${escapeHtml(docName)}</h1>
      <div class="subtitle">Hamilton HSL Method Editor</div>
    </div>
    <div class="stats">
      <span class="stat-badge">📌 ${deviceStepCount} device step${deviceStepCount !== 1 ? "s" : ""}</span>
      <span class="stat-badge">📋 ${functionCount} function${functionCount !== 1 ? "s" : ""}</span>
      <span class="stat-badge">📂 ${includeCount} include${includeCount !== 1 ? "s" : ""}</span>
    </div>
  </div>

  <div class="toolbar">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="device-step">Device Steps</button>
    <button class="filter-btn" data-filter="function">Functions</button>
    <button class="filter-btn" data-filter="include">Includes</button>
    <button class="filter-btn" data-filter="variable">Variables</button>
    <button class="filter-btn" data-filter="control-flow">Control Flow</button>
  </div>

  <div class="step-list">
    ${stepsHtml || '<div class="empty-state"><div class="icon">📄</div><p>No steps found in this file.</p></div>'}
  </div>

  <script nonce="${nonce}">
    const vscode = acquireVsCodeApi();

    // Click step → navigate to line in text editor
    document.querySelectorAll('.step').forEach(el => {
      el.addEventListener('click', () => {
        const line = parseInt(el.dataset.line, 10);
        if (!isNaN(line)) {
          vscode.postMessage({ type: 'goto', line: line });
        }
      });
    });

    // Filter buttons
    document.querySelectorAll('.filter-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const filter = btn.dataset.filter;
        document.querySelectorAll('.step').forEach(step => {
          if (filter === 'all') {
            step.classList.remove('hidden');
          } else {
            step.classList.toggle('hidden', !step.classList.contains(filter));
          }
        });
      });
    });
  </script>
</body>
</html>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export class HslMethodEditorProvider implements vscode.CustomTextEditorProvider {
  public static readonly viewType = "hsl.methodEditor";

  constructor(private readonly context: vscode.ExtensionContext) {}

  public async resolveCustomTextEditor(
    document: vscode.TextDocument,
    webviewPanel: vscode.WebviewPanel,
    _token: vscode.CancellationToken
  ): Promise<void> {
    webviewPanel.webview.options = { enableScripts: true };

    const updateWebview = (): void => {
      const steps = parseHslDocument(document.getText());
      const docName = document.fileName.split(/[\\/]/).pop() ?? document.fileName;
      webviewPanel.webview.html = getWebviewContent(webviewPanel.webview, steps, docName);
    };

    updateWebview();

    const changeDocSub = vscode.workspace.onDidChangeTextDocument((e) => {
      if (e.document.uri.toString() === document.uri.toString()) {
        updateWebview();
      }
    });

    const messageSub = webviewPanel.webview.onDidReceiveMessage((message: unknown) => {
      if (
        typeof message !== "object" ||
        message === null ||
        !("type" in message) ||
        !("line" in message) ||
        message.type !== "goto" ||
        typeof message.line !== "number" ||
        !Number.isInteger(message.line) ||
        message.line < 1 ||
        message.line > document.lineCount
      ) {
        return;
      }
      void vscode.window.showTextDocument(document.uri, {
        viewColumn: vscode.ViewColumn.Beside,
        selection: new vscode.Range(message.line - 1, 0, message.line - 1, 0),
        preserveFocus: false,
      });
    });

    webviewPanel.onDidDispose(() => {
      changeDocSub.dispose();
      messageSub.dispose();
    });
  }
}

export function registerHslMethodEditor(context: vscode.ExtensionContext): void {
  const provider = new HslMethodEditorProvider(context);
  context.subscriptions.push(
    vscode.window.registerCustomEditorProvider(
      HslMethodEditorProvider.viewType,
      provider,
      {
        webviewOptions: { retainContextWhenHidden: true },
      }
    )
  );
}
