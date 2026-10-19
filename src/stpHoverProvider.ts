import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";

// ─── CLSID → Step Type Mapping ──────────────────────────────────────────────
// These CLSIDs appear in HSL code as ML_STAR._<CLSID_underscored>("stepGuid")
const CLSID_TO_STEP_TYPE: Record<string, string> = {
  // Single-channel
  "1C0C0CB0_7C87_11D3_AD83_0004ACB1DCB2": "Initialize",
  "541143F5_7FA2_11D3_AD85_0004ACB1DCB2": "Aspirate",
  "541143F8_7FA2_11D3_AD85_0004ACB1DCB2": "Dispense",
  "541143FA_7FA2_11D3_AD85_0004ACB1DCB2": "TipPickUp",
  "541143FC_7FA2_11D3_AD85_0004ACB1DCB2": "TipEject",
  "54114400_7FA2_11D3_AD85_0004ACB1DCB2": "UnloadCarrier",
  "54114402_7FA2_11D3_AD85_0004ACB1DCB2": "LoadCarrier",
  "EA251BFB_66DE_48D1_83E5_6884B4DD8D11": "MoveAutoLoad",
  "9FB6DFE0_4132_4D09_B502_98C722734D4C": "GetLastLiquidLevel",
  // CO-RE 96 Head
  "BD0D210B_0816_4C86_A903_D6B2DF73F78B": "Head96TipPickUp",
  "827392A0_B7E8_4472_9ED3_B45B71B5D27A": "Head96Aspirate",
  "A48573A5_62ED_4951_9EF9_03207EFE34FB": "Head96Dispense",
  "2880E77A_3D6D_40FE_AF57_1BD1FE13960C": "Head96TipEject",
  // Easy 96 Head
  "E294A9A7_BEFC_4000_9A4C_926B91B8DE1C": "EasyHead96Aspirate",
  "7DE53592_BBE5_4F1D_B657_161F1AAECA3E": "EasyHead96Dispense",
};

// ─── Numeric Field ID → Human-Readable Name ────────────────────────────────
// Field IDs are negative integers stored as string keys like "5-534183924"
// where the prefix (3/5) indicates int/float type.
const FIELD_NAMES: Record<string, string> = {
  // Aspirate-specific
  "-534183924": "Aspirate Volume (µL)",
  "-534183918": "Pressure LLD Sensing",
  "-534183933": "cLLD Sensitivity",
  // Dispense-specific
  "-534183908": "Dispense Volume (µL)",
  "-534183909": "Dispense Position Above Z-Start",
  // Shared aspirate/dispense
  "-534183915": "Mix Volume (µL)",
  "-534183914": "Mix Cycles",
  "-534183925": "Mix Position from Liquid Surface (mm)",
  "-534183913": "Submerge Depth (mm)",
  "-534183919": "LLD Mode",
  "-534183928": "LLD Sensitivity",
  "-534183622": "Retract Distance (mm)",
  "-534183629": "Side Touch",
  "-534183926": "Retract Speed",
  "-534183920": "Swap Speed",
  "-534183700": "Pressure LLD Sensitivity",
  "-534183876": "Channel Enable",
  "-534183813": "Touch Off Distance",
  // Per-channel / 96-head group markers
  "-534183935": "(96-Head Parameters)",
  "-534183936": "(Per-Channel Parameters)",
};

const LLD_MODE_LABELS: Record<string, string> = {
  "0": "Off",
  "1": "pLLD (Pressure)",
  "5": "Capacitive",
};

const SEQUENCE_COUNTING_LABELS: Record<string, string> = {
  "0": "Manually",
  "1": "Automatic",
};

const DISPENSE_MODE_LABELS: Record<string, string> = {
  "3": "Jet",
  "4": "Surface Empty",
  "5": "Drain Tip in Jet Mode",
};

// ─── Binary Parser (port of hxcfgfile_codec.py) ────────────────────────────

interface StpSection {
  key: string;
  tokens: string[];
}

function readU16LE(buf: Buffer, pos: number): [number, number] {
  return [buf.readUInt16LE(pos), pos + 2];
}

function readU32LE(buf: Buffer, pos: number): [number, number] {
  return [buf.readUInt32LE(pos), pos + 4];
}

function readShortString(buf: Buffer, pos: number): [string, number] {
  const len = buf[pos];
  pos += 1;
  const value = buf.toString("latin1", pos, pos + len);
  return [value, pos + len];
}

function readVarString(buf: Buffer, pos: number): [string, number] {
  const marker = buf[pos];
  pos += 1;
  let len: number;
  if (marker === 0xff) {
    [len, pos] = readU16LE(buf, pos);
  } else {
    len = marker;
  }
  const value = buf.toString("latin1", pos, pos + len);
  return [value, pos + len];
}

function parseStpBinary(data: Buffer): StpSection[] {
  let pos = 0;

  let version: number;
  [version, pos] = readU16LE(data, pos);
  if (version !== 3) {
    return [];
  }

  let sectionType: number;
  [sectionType, pos] = readU16LE(data, pos);

  let namedSectionCount: number;
  [namedSectionCount, pos] = readU32LE(data, pos);

  // Skip through named sections (Method,Properties or ActivityData)
  for (let n = 0; n < namedSectionCount; n++) {
    let _sectionName: string;
    [_sectionName, pos] = readShortString(data, pos);
    let fieldType: number;
    [fieldType, pos] = readU16LE(data, pos);
    let fieldCount: number;
    [fieldCount, pos] = readU32LE(data, pos);
    for (let f = 0; f < fieldCount; f++) {
      [, pos] = readShortString(data, pos); // field key
      [, pos] = readVarString(data, pos);   // field value
    }
  }

  // HxPars count (1 byte) + 3 bytes padding
  const hxparsCount = data[pos];
  pos += 1 + 3;

  const sections: StpSection[] = [];
  for (let h = 0; h < hxparsCount; h++) {
    let rawName: string;
    [rawName, pos] = readShortString(data, pos);

    const sectionKey = rawName.startsWith("HxPars,")
      ? rawName.slice(7)
      : rawName;

    let pVersion: number;
    [pVersion, pos] = readU16LE(data, pos);

    let tokenCount: number;
    [tokenCount, pos] = readU32LE(data, pos);

    const tokens: string[] = [];
    for (let t = 0; t < tokenCount; t++) {
      let token: string;
      [token, pos] = readVarString(data, pos);
      tokens.push(token);
    }

    sections.push({ key: sectionKey, tokens });
  }

  return sections;
}

// ─── Token Extraction ──────────────────────────────────────────────────────

interface StepParams {
  stepName: string;
  sequenceObject: string;
  sequenceName: string;
  liquidName: string;
  channelPattern: string;
  sequenceCounting: string;
  liquidFollowing: string;
  dispenseMode: string;
  tipType: string;
  timestamp: string;
  usePickUpPosition: string;
  touchOffMode: string;
  sameLiquid: string;
  sideTouchMode: string;
  numericFields: Map<string, string>;
  /** For single-channel: per-channel parameter groups */
  channelFields: Map<string, string>[];
}

function extractStepParams(tokens: string[]): StepParams {
  const params: StepParams = {
    stepName: "",
    sequenceObject: "",
    sequenceName: "",
    liquidName: "",
    channelPattern: "",
    sequenceCounting: "",
    liquidFollowing: "",
    dispenseMode: "",
    tipType: "",
    timestamp: "",
    usePickUpPosition: "",
    touchOffMode: "",
    sameLiquid: "",
    sideTouchMode: "",
    numericFields: new Map(),
    channelFields: [],
  };

  let i = 0;
  while (i < tokens.length) {
    const t = tokens[i];

    // Skip error/recovery blocks
    if (t === "(Errors" || t === "(Recoveries") {
      let nest = 1;
      i++;
      while (i < tokens.length && nest > 0) {
        if (tokens[i].startsWith("(")) { nest++; }
        if (tokens[i] === ")") { nest--; }
        i++;
      }
      continue;
    }

    // 96-head numeric parameter group: (-534183935 ...)
    if (t === "(-534183935") {
      i++;
      while (i < tokens.length && tokens[i] !== ")") {
        const key = tokens[i];
        if (key.startsWith("(")) { i++; continue; }
        if (i + 1 < tokens.length && tokens[i + 1] !== ")") {
          // Extract field ID from key like "3-534183919" or "5-534183924"
          const fieldMatch = key.match(/^[35](-\d+)$/);
          if (fieldMatch) {
            params.numericFields.set(fieldMatch[1], tokens[i + 1]);
          } else {
            // Named fields inside the group (e.g., "3LiquidFollowing")
            const namedMatch = key.match(/^[135](.+)$/);
            if (namedMatch) {
              setNamedField(params, namedMatch[1], tokens[i + 1]);
            }
          }
          i += 2;
        } else {
          i++;
        }
      }
      if (i < tokens.length) { i++; } // skip closing )
      continue;
    }

    // Single-channel per-channel group: (-534183936 ...)
    if (t === "(-534183936") {
      i++;
      // Inside: (3 ... ) (1 ... ) etc. repeated per channel
      while (i < tokens.length && tokens[i] !== ")") {
        if (tokens[i].startsWith("(") && tokens[i] !== "(-534183936") {
          // Start of a channel sub-group
          const channelMap = new Map<string, string>();
          i++;
          while (i < tokens.length && tokens[i] !== ")") {
            const ck = tokens[i];
            if (ck.startsWith("(")) { i++; continue; }
            if (i + 1 < tokens.length && tokens[i + 1] !== ")") {
              const cfm = ck.match(/^[35](-\d+)$/);
              if (cfm) {
                channelMap.set(cfm[1], tokens[i + 1]);
              } else {
                const cnm = ck.match(/^[135](.+)$/);
                if (cnm) {
                  channelMap.set(cnm[1], tokens[i + 1]);
                }
              }
              i += 2;
            } else {
              i++;
            }
          }
          if (i < tokens.length) { i++; } // skip )
          params.channelFields.push(channelMap);
          continue;
        }
        i++;
      }
      if (i < tokens.length) { i++; } // skip outer )
      continue;
    }

    // Skip Variables block and other nested groups
    if (t.startsWith("(")) {
      let nest = 1;
      i++;
      while (i < tokens.length && nest > 0) {
        if (tokens[i].startsWith("(")) { nest++; }
        if (tokens[i] === ")") { nest--; }
        i++;
      }
      continue;
    }

    if (t === ")") { i++; continue; }

    // Regular key-value pairs
    if (i + 1 < tokens.length) {
      const key = t;
      const val = tokens[i + 1];
      const namedMatch = key.match(/^[135](.+)$/);
      if (namedMatch) {
        setNamedField(params, namedMatch[1], val);
      }
      i += 2;
    } else {
      i++;
    }
  }

  // For single-channel steps, populate numericFields from first channel
  if (params.channelFields.length > 0 && params.numericFields.size === 0) {
    params.numericFields = params.channelFields[0];
  }

  return params;
}

function setNamedField(params: StepParams, key: string, value: string): void {
  switch (key) {
    case "StepName": params.stepName = value; break;
    case "SequenceObject": params.sequenceObject = value; break;
    case "SequenceName": params.sequenceName = value; break;
    case "LiquidName": params.liquidName = value; break;
    case "ChannelPattern": params.channelPattern = value; break;
    case "SequenceCounting": params.sequenceCounting = value; break;
    case "LiquidFollowing": params.liquidFollowing = value; break;
    case "DispenseMode": params.dispenseMode = value; break;
    case "TipType": params.tipType = value; break;
    case "Timestamp": params.timestamp = value; break;
    case "UsePickUpPosition": params.usePickUpPosition = value; break;
    case "TouchOffMode": params.touchOffMode = value; break;
    case "SameLiquid": params.sameLiquid = value; break;
    case "SideTouchMode": params.sideTouchMode = value; break;
  }
}

// ─── Tooltip Formatting ────────────────────────────────────────────────────

function formatStepTooltip(
  stepType: string,
  params: StepParams
): vscode.MarkdownString {
  const md = new vscode.MarkdownString();
  md.isTrusted = true;

  const displayName = params.stepName || stepType;
  md.appendMarkdown(`### ${displayName}\n\n`);

  // Common step info
  if (params.sequenceObject) {
    md.appendMarkdown(`**Sequence:** \`${params.sequenceObject}\`\n\n`);
  }
  if (params.sequenceCounting) {
    const label = SEQUENCE_COUNTING_LABELS[params.sequenceCounting] ?? params.sequenceCounting;
    md.appendMarkdown(`**Sequence Counting:** ${label}\n\n`);
  }

  // Step-type-specific formatting
  if (isAspirateStep(stepType)) {
    formatAspirateTooltip(md, params);
  } else if (isDispenseStep(stepType)) {
    formatDispenseTooltip(md, params);
  } else if (isTipPickUpStep(stepType)) {
    formatTipPickUpTooltip(md, params);
  } else if (isTipEjectStep(stepType)) {
    formatTipEjectTooltip(md, params);
  } else if (stepType === "Initialize") {
    formatInitializeTooltip(md, params);
  }

  if (params.timestamp) {
    md.appendMarkdown(`\n---\n*Last modified: ${params.timestamp}*\n`);
  }

  return md;
}

function isAspirateStep(stepType: string): boolean {
  return ["Aspirate", "Head96Aspirate", "EasyHead96Aspirate"].includes(stepType);
}

function isDispenseStep(stepType: string): boolean {
  return ["Dispense", "Head96Dispense", "EasyHead96Dispense"].includes(stepType);
}

function isTipPickUpStep(stepType: string): boolean {
  return ["TipPickUp", "Head96TipPickUp"].includes(stepType);
}

function isTipEjectStep(stepType: string): boolean {
  return ["TipEject", "Head96TipEject"].includes(stepType);
}

function formatAspirateTooltip(
  md: vscode.MarkdownString,
  params: StepParams
): void {
  const fields = params.numericFields;

  if (params.liquidName) {
    md.appendMarkdown(`**Liquid Class:** \`${params.liquidName.replace(/^"|"$/g, "")}\`\n\n`);
  }

  const vol = fields.get("-534183924") ?? "0";
  md.appendMarkdown(`**Volume:** ${vol} µL\n\n`);

  const mixVol = fields.get("-534183915") ?? "0";
  const mixCycles = fields.get("-534183914") ?? "0";
  if (mixCycles !== "0" && mixVol !== "0") {
    md.appendMarkdown(`**Mix:** ${mixVol} µL × ${mixCycles} cycles\n\n`);
  }

  const submerge = fields.get("-534183913") ?? "--";
  md.appendMarkdown(`**Submerge Depth:** ${submerge} mm\n\n`);

  const lldMode = fields.get("-534183919") ?? "0";
  const lldLabel = LLD_MODE_LABELS[lldMode] ?? `Mode ${lldMode}`;
  const lldSens = fields.get("-534183928") ?? "";
  if (lldMode !== "0") {
    md.appendMarkdown(`**LLD:** ${lldLabel}, Sensitivity: ${lldSens}\n\n`);
  } else {
    md.appendMarkdown(`**LLD:** Off\n\n`);
  }

  if (params.liquidFollowing === "1") {
    md.appendMarkdown(`**Liquid Following:** On\n\n`);
  }

  const retract = fields.get("-534183622");
  if (retract && retract !== "0") {
    md.appendMarkdown(`**Retract Distance:** ${retract} mm\n\n`);
  }
}

function formatDispenseTooltip(
  md: vscode.MarkdownString,
  params: StepParams
): void {
  const fields = params.numericFields;

  if (params.liquidName) {
    md.appendMarkdown(`**Liquid Class:** \`${params.liquidName.replace(/^"|"$/g, "")}\`\n\n`);
  } else if (params.sameLiquid === "1") {
    md.appendMarkdown(`**Liquid Class:** *(Same as aspiration)*\n\n`);
  }

  const vol = fields.get("-534183908") ?? "0";
  md.appendMarkdown(`**Volume:** ${vol} µL\n\n`);

  if (params.dispenseMode) {
    const label = DISPENSE_MODE_LABELS[params.dispenseMode] ?? `Mode ${params.dispenseMode}`;
    md.appendMarkdown(`**Dispense Mode:** ${label}\n\n`);
  }

  const mixVol = fields.get("-534183915") ?? "0";
  const mixCycles = fields.get("-534183914") ?? "0";
  if (mixCycles !== "0" && mixVol !== "0") {
    md.appendMarkdown(`**Mix:** ${mixVol} µL × ${mixCycles} cycles\n\n`);
  }

  const submerge = fields.get("-534183913") ?? "--";
  md.appendMarkdown(`**Submerge Depth:** ${submerge} mm\n\n`);

  const lldMode = fields.get("-534183919") ?? "0";
  const lldLabel = LLD_MODE_LABELS[lldMode] ?? `Mode ${lldMode}`;
  const lldSens = fields.get("-534183928") ?? "";
  if (lldMode !== "0") {
    md.appendMarkdown(`**LLD:** ${lldLabel}, Sensitivity: ${lldSens}\n\n`);
  } else {
    md.appendMarkdown(`**LLD:** Off\n\n`);
  }

  if (params.liquidFollowing === "1") {
    md.appendMarkdown(`**Liquid Following:** On\n\n`);
  }

  const retract = fields.get("-534183622");
  if (retract && retract !== "0") {
    md.appendMarkdown(`**Retract Distance:** ${retract} mm\n\n`);
  }
}

function formatTipPickUpTooltip(
  md: vscode.MarkdownString,
  params: StepParams
): void {
  if (params.channelPattern) {
    const activeCount = (params.channelPattern.match(/1/g) ?? []).length;
    md.appendMarkdown(`**Channels:** ${activeCount} active\n\n`);
  }
  if (params.sequenceCounting) {
    const label = SEQUENCE_COUNTING_LABELS[params.sequenceCounting] ?? params.sequenceCounting;
    md.appendMarkdown(`**Sequence Counting:** ${label}\n\n`);
  }
}

function formatTipEjectTooltip(
  md: vscode.MarkdownString,
  params: StepParams
): void {
  if (params.usePickUpPosition) {
    const labels: Record<string, string> = {
      "0": "Default position",
      "1": "To pick-up position",
      "2": "Default waste",
    };
    const label = labels[params.usePickUpPosition] ?? `Position ${params.usePickUpPosition}`;
    md.appendMarkdown(`**Eject Position:** ${label}\n\n`);
  }
}

function formatInitializeTooltip(
  md: vscode.MarkdownString,
  _params: StepParams
): void {
  md.appendMarkdown(`Initializes the ML_STAR instrument.\n\n`);
}

// ─── STP File Cache ────────────────────────────────────────────────────────

interface StpCache {
  filePath: string;
  mtimeMs: number;
  sections: StpSection[];
}

const stpCacheMap = new Map<string, StpCache>();

function getStpSections(stpPath: string): StpSection[] | undefined {
  try {
    const stat = fs.statSync(stpPath);
    const cached = stpCacheMap.get(stpPath.toLowerCase());
    if (cached && cached.mtimeMs === stat.mtimeMs) {
      return cached.sections;
    }

    const data = fs.readFileSync(stpPath);
    const sections = parseStpBinary(data);

    stpCacheMap.set(stpPath.toLowerCase(), {
      filePath: stpPath,
      mtimeMs: stat.mtimeMs,
      sections,
    });

    return sections;
  } catch {
    return undefined;
  }
}

// ─── HSL Line Matching ─────────────────────────────────────────────────────

// Pattern: ML_STAR._<CLSID>("stepGuid")
// The CLSID in HSL uses underscores instead of hyphens, e.g., _827392A0_B7E8_4472_9ED3_B45B71B5D27A
const DEVICE_STEP_PATTERN =
  /(\w+)\._([0-9A-Fa-f]{8}_[0-9A-Fa-f]{4}_[0-9A-Fa-f]{4}_[0-9A-Fa-f]{4}_[0-9A-Fa-f]{12})\s*\(\s*"([0-9a-f_]+)"\s*\)/;

// ─── Public API ────────────────────────────────────────────────────────────

/**
 * Register a hover provider that shows pipetting step parameters from .stp files
 * when hovering over device step calls in HSL code.
 */
export function registerStpHoverProvider(
  context: vscode.ExtensionContext
): void {
  const hoverProvider = vscode.languages.registerHoverProvider(
    { language: "hsl", scheme: "file" },
    {
      provideHover(
        document: vscode.TextDocument,
        position: vscode.Position
      ): vscode.Hover | undefined {
        const line = document.lineAt(position.line).text;
        const match = DEVICE_STEP_PATTERN.exec(line);
        if (!match) {
          return undefined;
        }

        const [fullMatch, _deviceVar, clsid, stepGuid] = match;

        // Check if the cursor is within the matched range
        const matchStart = line.indexOf(fullMatch);
        const matchEnd = matchStart + fullMatch.length;
        if (position.character < matchStart || position.character > matchEnd) {
          return undefined;
        }

        // Look up step type from CLSID
        const stepType = CLSID_TO_STEP_TYPE[clsid.toUpperCase()] ?? "Unknown Step";

        // Find the .stp file (same name as .hsl but with .stp extension)
        const hslPath = document.uri.fsPath;
        const stpPath = findStpFile(hslPath);
        if (!stpPath) {
          // Return a basic tooltip with just the step type
          const md = new vscode.MarkdownString();
          md.appendMarkdown(`### ${stepType}\n\n`);
          md.appendMarkdown(`*No .stp file found -- cannot show parameters.*\n`);
          return new vscode.Hover(
            md,
            new vscode.Range(
              position.line,
              matchStart,
              position.line,
              matchEnd
            )
          );
        }

        // Parse the .stp file and find the step section
        const sections = getStpSections(stpPath);
        if (!sections) {
          return undefined;
        }

        const section = sections.find(
          (s) => s.key === stepGuid
        );
        if (!section) {
          const md = new vscode.MarkdownString();
          md.appendMarkdown(`### ${stepType}\n\n`);
          md.appendMarkdown(`*Step \`${stepGuid}\` not found in .stp file.*\n`);
          return new vscode.Hover(
            md,
            new vscode.Range(
              position.line,
              matchStart,
              position.line,
              matchEnd
            )
          );
        }

        const params = extractStepParams(section.tokens);
        const tooltip = formatStepTooltip(stepType, params);

        return new vscode.Hover(
          tooltip,
          new vscode.Range(
            position.line,
            matchStart,
            position.line,
            matchEnd
          )
        );
      },
    }
  );

  context.subscriptions.push(hoverProvider);
}

/**
 * Find the .stp file for a given .hsl file.
 * Looks for a file with the same base name but .stp extension in the same directory.
 * If the direct match is absent, accepts a fallback only when exactly one
 * .stp file exists in the directory. Ambiguous matches are never guessed.
 */
function findStpFile(hslPath: string): string | undefined {
  const dir = path.dirname(hslPath);
  const baseName = path.basename(hslPath, path.extname(hslPath));

  // Direct match: same name, .stp extension
  const directPath = path.join(dir, baseName + ".stp");
  if (fs.existsSync(directPath)) {
    return directPath;
  }

  // Try to find .stp files in the same directory
  try {
    const candidates = fs
      .readdirSync(dir)
      .filter((file) => file.toLowerCase().endsWith(".stp"));
    if (candidates.length === 1) {
      return path.join(dir, candidates[0]);
    }
  } catch {
    // ignore
  }

  return undefined;
}

/**
 * Parse the stp tokens for a given step GUID and return a structured
 * representation. Exported for use by other modules (e.g., LLM context).
 */
export function getStepSummary(
  stpPath: string,
  stepGuid: string
): { stepType: string; summary: string } | undefined {
  const sections = getStpSections(stpPath);
  if (!sections) {
    return undefined;
  }

  const section = sections.find((s) => s.key === stepGuid);
  if (!section) {
    return undefined;
  }

  const params = extractStepParams(section.tokens);
  const stepType = params.stepName || "Unknown";

  const lines: string[] = [`Step: ${stepType}`];

  if (params.sequenceObject) {
    lines.push(`Sequence: ${params.sequenceObject}`);
  }
  if (params.liquidName) {
    lines.push(`Liquid Class: ${params.liquidName.replace(/^"|"$/g, "")}`);
  }
  if (isAspirateStep(stepType)) {
    const vol = params.numericFields.get("-534183924") ?? "0";
    lines.push(`Aspirate Volume: ${vol} µL`);
    const mixVol = params.numericFields.get("-534183915") ?? "0";
    const mixCycles = params.numericFields.get("-534183914") ?? "0";
    if (mixCycles !== "0") {
      lines.push(`Mix: ${mixVol} µL × ${mixCycles} cycles`);
    }
  } else if (isDispenseStep(stepType)) {
    const vol = params.numericFields.get("-534183908") ?? "0";
    lines.push(`Dispense Volume: ${vol} µL`);
  }

  return { stepType, summary: lines.join("\n") };
}
