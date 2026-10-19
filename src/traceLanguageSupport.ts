import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

// ---------------------------------------------------------------------------
// Types mirroring firmware_commands.json schema
// ---------------------------------------------------------------------------

interface FirmwareParamDef {
  name: string;
  type: string;
  min: number;
  max: number;
  default: number;
  width: number;
  description: string;
}

interface FirmwareCommandDef {
  code: string;
  sfcoId: string;
  category: string;
  specSection: string;
  description: string;
  commandPoint: number;
  params: FirmwareParamDef[];
  responseFields: string[];
  notes: string;
}

interface FirmwareCommandsFile {
  errorCodes: Record<string, string>;
  paramTypes: Record<string, string>;
  commands: FirmwareCommandDef[];
}

// ---------------------------------------------------------------------------
// Registry -- loads once from the JSON file, provides fast lookups
// ---------------------------------------------------------------------------

class FirmwareRegistry {
  private _byCode = new Map<string, FirmwareCommandDef>();
  private _byParamName = new Map<string, FirmwareParamDef[]>();
  private _errorCodes: Record<string, string> = {};
  private _allCodes: string[] = [];

  constructor(jsonPath: string) {
    this._load(jsonPath);
  }

  private _load(jsonPath: string): void {
    if (!fs.existsSync(jsonPath)) {
      console.warn(`HSL firmware command registry was not found at ${jsonPath}`);
      return;
    }
    let data: FirmwareCommandsFile;
    try {
      const raw = fs.readFileSync(jsonPath, "utf-8");
      data = JSON.parse(raw) as FirmwareCommandsFile;
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      console.error(`Unable to load HSL firmware command registry: ${message}`);
      return;
    }

    this._errorCodes = data.errorCodes ?? {};

    for (const cmd of data.commands) {
      this._byCode.set(cmd.code, cmd);
      for (const p of cmd.params) {
        const existing = this._byParamName.get(p.name) ?? [];
        existing.push(p);
        this._byParamName.set(p.name, existing);
      }
    }

    this._allCodes = Array.from(this._byCode.keys()).sort();
  }

  getCommand(code: string): FirmwareCommandDef | undefined {
    return this._byCode.get(code);
  }

  getErrorDescription(code: string): string | undefined {
    return this._errorCodes[code];
  }

  getParamInfo(name: string): FirmwareParamDef[] {
    return this._byParamName.get(name) ?? [];
  }

  allCommandCodes(): string[] {
    return this._allCodes;
  }

  allCommands(): FirmwareCommandDef[] {
    return Array.from(this._byCode.values());
  }
}

// ---------------------------------------------------------------------------
// Markdown builders
// ---------------------------------------------------------------------------

function buildCommandHover(cmd: FirmwareCommandDef): vscode.MarkdownString {
  const md = new vscode.MarkdownString();

  md.appendMarkdown(`### Firmware Command \`${cmd.code}\`\n\n`);
  md.appendMarkdown(`**${cmd.description}**\n\n`);

  if (cmd.sfcoId) {
    md.appendMarkdown(`- **SFCO ID:** \`${cmd.sfcoId}\`\n`);
  }
  md.appendMarkdown(`- **Category:** ${cmd.category}\n`);
  if (cmd.specSection) {
    md.appendMarkdown(`- **IDL Spec Section:** ${cmd.specSection}\n`);
  }
  md.appendMarkdown(`- **Command Point:** ${cmd.commandPoint}\n`);

  if (cmd.params.length > 0) {
    md.appendMarkdown(`\n#### Parameters\n\n`);
    md.appendMarkdown(`| Name | Type | Range | Default | Description |\n`);
    md.appendMarkdown(`|------|------|-------|---------|-------------|\n`);
    for (const p of cmd.params) {
      md.appendMarkdown(
        `| \`${p.name}\` | ${p.type} | ${p.min}..${p.max} | ${p.default} | ${p.description} |\n`
      );
    }
  }

  if (cmd.responseFields.length > 0) {
    md.appendMarkdown(`\n#### Response\n\n`);
    md.appendMarkdown(`\`${cmd.responseFields.join(" ")}\`\n`);
  }

  if (cmd.notes) {
    md.appendMarkdown(`\n> ${cmd.notes}\n`);
  }

  return md;
}

function buildParamHover(
  paramName: string,
  value: string | undefined,
  params: FirmwareParamDef[]
): vscode.MarkdownString {
  const md = new vscode.MarkdownString();
  md.isTrusted = true;

  md.appendMarkdown(`### Firmware Parameter \`${paramName}\`\n\n`);

  for (const p of params) {
    md.appendMarkdown(`- **${p.description}**\n`);
    md.appendMarkdown(`  - Type: ${p.type}, Range: ${p.min}..${p.max}, Default: ${p.default}\n`);
  }

  if (value !== undefined) {
    md.appendMarkdown(`\n**Current value:** \`${value}\`\n`);
    const numVal = parseInt(value, 10);
    if (!isNaN(numVal)) {
      for (const p of params) {
        if (numVal < p.min || numVal > p.max) {
          md.appendMarkdown(
            `\n> **Warning:** Value ${numVal} is outside range [${p.min}..${p.max}]\n`
          );
        }
      }
    }
  }

  return md;
}

function buildErrorHover(
  errCode: string,
  traceCode: string,
  description: string | undefined
): vscode.MarkdownString {
  const md = new vscode.MarkdownString();
  md.isTrusted = true;

  if (errCode === "00") {
    md.appendMarkdown(`### Error Code \`er${errCode}/${traceCode}\`\n\n`);
    md.appendMarkdown(`**No error** - Command completed successfully.\n`);
  } else {
    md.appendMarkdown(`### Error Code \`er${errCode}/${traceCode}\`\n\n`);
    if (description) {
      md.appendMarkdown(`**${description}**\n\n`);
    }
    md.appendMarkdown(`- Error code: ${errCode}\n`);
    md.appendMarkdown(`- Trace info: ${traceCode}\n`);
  }

  return md;
}

// ---------------------------------------------------------------------------
// Regex patterns for token detection in trace lines
// ---------------------------------------------------------------------------

// Full firmware response: e.g. "CLid0001er00/00ci..."
const RE_FW_RESPONSE = /\b([A-Z]{2})id(\d{4})er(\d{2})\/(\d{2})/g;

// [FW XX] simulator markers
const RE_FW_SIM = /\[FW\s+([A-Z]{2})\]/g;

// [SIM] Device step markers with step type
const RE_SIM_STEP = /\[SIM\]\s+Device step:\s+(\w+)/g;

// Error code pattern: er##/##
const RE_ERROR = /\ber(\d{2})\/(\d{2})/g;

// Firmware param key-value: two lowercase letters followed by digits
const RE_PARAM = /\b([a-z]{2})(\d+)/g;

// Step type names
const STEP_TYPES = new Set([
  "Initialize", "Aspirate", "Dispense", "TipPickUp", "TipEject",
  "UnloadCarrier", "LoadCarrier", "MoveAutoLoad",
  "Head96Aspirate", "Head96Dispense", "Head96TipPickUp", "Head96TipEject",
  "EasyHead96Aspirate", "EasyHead96Dispense", "GetLastLiquidLevel",
]);

// CLSID to step type (for hover on GUIDs in trace output)
const CLSID_TO_STEP: Record<string, string> = {
  "1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2": "Initialize",
  "541143F5_7FA2_11D3_AD85_0004ACB1DCB2": "Aspirate",
  "541143F8_7FA2_11D3_AD85_0004ACB1DCB2": "Dispense",
  "541143FA_7FA2_11D3_AD85_0004ACB1DCB2": "TipPickUp",
  "541143FC_7FA2_11D3_AD85_0004ACB1DCB2": "TipEject",
  "54114400_7FA2_11D3_AD85_0004ACB1DCB2": "UnloadCarrier",
  "54114402_7FA2_11D3_AD85_0004ACB1DCB2": "LoadCarrier",
  "827392A0_B7E8_4472_9ED3_B45B71B5D27A": "Head96Aspirate",
  "A48573A5_62ED_4951_9EF9_03207EFE34FB": "Head96Dispense",
  "BD0D210B_0816_4C86_A903_D6B2DF73F78B": "Head96TipPickUp",
  "2880E77A_3D6D_40FE_AF57_1BD1FE13960C": "Head96TipEject",
  "EA251BFB_66DE_48D1_83E5_6884B4DD8D11": "MoveAutoLoad",
  "9FB6DFE0_4132_4D09_B502_98C722734D4C": "GetLastLiquidLevel",
  "E294A9A7_BEFC_4000_9A4C_926B91B8DE1C": "EasyHead96Aspirate",
  "7DE53592_BBE5_4F1D_B657_161F1AAECA3E": "EasyHead96Dispense",
};

// ---------------------------------------------------------------------------
// Hover Provider
// ---------------------------------------------------------------------------

class TraceHoverProvider implements vscode.HoverProvider {
  constructor(private registry: FirmwareRegistry) {}

  provideHover(
    document: vscode.TextDocument,
    position: vscode.Position,
    _token: vscode.CancellationToken
  ): vscode.Hover | undefined {
    const line = document.lineAt(position.line).text;
    const offset = position.character;

    // 1. Check for firmware response string: XXid####er##/##
    for (const m of line.matchAll(RE_FW_RESPONSE)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (offset >= start && offset < end) {
        const cmdCode = m[1];
        const cmd = this.registry.getCommand(cmdCode);
        if (cmd) {
          const errCode = m[3];
          const traceCode = m[4];
          const md = buildCommandHover(cmd);
          if (errCode !== "00") {
            const errDesc = this.registry.getErrorDescription(errCode);
            md.appendMarkdown(`\n---\n`);
            md.appendMarkdown(
              buildErrorHover(errCode, traceCode, errDesc).value
            );
          }
          return new vscode.Hover(
            md,
            new vscode.Range(position.line, start, position.line, end)
          );
        }
      }
    }

    // 2. Check for [FW XX] simulator markers
    for (const m of line.matchAll(RE_FW_SIM)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (offset >= start && offset < end) {
        const cmd = this.registry.getCommand(m[1]);
        if (cmd) {
          return new vscode.Hover(
            buildCommandHover(cmd),
            new vscode.Range(position.line, start, position.line, end)
          );
        }
      }
    }

    // 3. Check for error codes: er##/##
    for (const m of line.matchAll(RE_ERROR)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (offset >= start && offset < end) {
        const errCode = m[1];
        const traceCode = m[2];
        const desc = this.registry.getErrorDescription(errCode);
        return new vscode.Hover(
          buildErrorHover(errCode, traceCode, desc),
          new vscode.Range(position.line, start, position.line, end)
        );
      }
    }

    // 4. Check for step type names
    const wordRange = document.getWordRangeAtPosition(position, /\b[A-Za-z]\w+\b/);
    if (wordRange) {
      const word = document.getText(wordRange);
      if (STEP_TYPES.has(word)) {
        const md = new vscode.MarkdownString();
        md.appendMarkdown(`### HSL Device Step: \`${word}\`\n\n`);
        md.appendMarkdown(`Hamilton ML STAR device step type.\n`);

        // Find matching CLSID
        for (const [clsid, stepName] of Object.entries(CLSID_TO_STEP)) {
          if (stepName === word) {
            md.appendMarkdown(`\n- **CLSID:** \`${clsid}\`\n`);
            break;
          }
        }

        return new vscode.Hover(md, wordRange);
      }

      // 5. Check for CLSID patterns (underscore-delimited GUIDs)
      const clsidRange = document.getWordRangeAtPosition(
        position,
        /[0-9a-fA-F]{8}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4}[-_][0-9a-fA-F]{4,}[-_]?[0-9a-fA-F]{12}/
      );
      if (clsidRange) {
        const clsidText = document.getText(clsidRange).replace(/-/g, "_").toUpperCase();
        const stepType = CLSID_TO_STEP[clsidText];
        if (stepType) {
          const md = new vscode.MarkdownString();
          md.appendMarkdown(`### CLSID: \`${stepType}\`\n\n`);
          md.appendMarkdown(`Device step CLSID for **${stepType}** operation.\n`);
          return new vscode.Hover(md, clsidRange);
        }
      }
    }

    // 6. Check for firmware parameter key-value pairs
    for (const m of line.matchAll(RE_PARAM)) {
      const start = m.index!;
      const end = start + m[0].length;
      if (offset >= start && offset < end) {
        const paramName = m[1];
        const paramValue = m[2];
        const paramInfos = this.registry.getParamInfo(paramName);
        if (paramInfos.length > 0) {
          return new vscode.Hover(
            buildParamHover(paramName, paramValue, paramInfos),
            new vscode.Range(position.line, start, position.line, end)
          );
        }
      }
    }

    return undefined;
  }
}

// ---------------------------------------------------------------------------
// Completion Provider -- suggests firmware command codes
// ---------------------------------------------------------------------------

class TraceCompletionProvider implements vscode.CompletionItemProvider {
  constructor(private registry: FirmwareRegistry) {}

  provideCompletionItems(
    _document: vscode.TextDocument,
    _position: vscode.Position,
    _token: vscode.CancellationToken,
    _context: vscode.CompletionContext
  ): vscode.CompletionItem[] {
    const items: vscode.CompletionItem[] = [];

    for (const cmd of this.registry.allCommands()) {
      const item = new vscode.CompletionItem(
        cmd.code,
        vscode.CompletionItemKind.Constant
      );
      item.detail = `${cmd.description} (${cmd.category})`;
      item.documentation = buildCommandHover(cmd);
      items.push(item);
    }

    return items;
  }
}

// ---------------------------------------------------------------------------
// Public registration function
// ---------------------------------------------------------------------------

export function registerTraceLanguageSupport(
  context: vscode.ExtensionContext
): void {
  const jsonPath = path.join(context.extensionPath, "firmware_commands.json");
  const registry = new FirmwareRegistry(jsonPath);

  const selector: vscode.DocumentSelector = {
    language: "hsl-trace",
    scheme: "file",
  };

  context.subscriptions.push(
    vscode.languages.registerHoverProvider(selector, new TraceHoverProvider(registry))
  );

  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      selector,
      new TraceCompletionProvider(registry)
    )
  );
}
