import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import * as cp from "child_process";
import { buildCompletionItems } from "./builtins";
import { createHslDiagnostics, waitForPendingHslChecksum } from "./diagnostics";
import { registerHslIntelliSense } from "./hslIntellisense";
import { registerStpHoverProvider } from "./stpHoverProvider";
import { registerTraceLanguageSupport } from "./traceLanguageSupport";
import { registerHslMethodEditor } from "./hslMethodEditor";
import {
  HXRUN_EXTENSIONS,
  SIMULATION_EXTENSIONS,
  hasMethodMain,
  isSupportedLaunchTarget,
} from "./hslCore";
import {
  HslDebugAdapterFactory,
  HslDebugConfigurationProvider,
} from "./hslDebugAdapter";

const DEFAULT_HAMILTON_DIR = "C:\\Program Files (x86)\\Hamilton";

function getHamiltonDir(): string {
  return path.normalize(
    vscode.workspace
      .getConfiguration("hsl")
      .get<string>("hamiltonDir", DEFAULT_HAMILTON_DIR)
  );
}

function getHamiltonEditorPath(): string {
  return path.join(getHamiltonDir(), "Bin", "HxHSLMetEd.exe");
}

function getHamiltonRunPath(): string {
  return path.join(getHamiltonDir(), "Bin", "HxRun.exe");
}

/** Called once when the extension is activated. */
export function activate(context: vscode.ExtensionContext): void {
  const completionItems = buildCompletionItems();
  context.subscriptions.push(
    vscode.languages.registerCompletionItemProvider(
      { language: "hsl", scheme: "file" },
      {
        provideCompletionItems(): vscode.CompletionItem[] {
          return completionItems;
        },
      }
    )
  );

  registerHslIntelliSense(context);
  createHslDiagnostics(context);
  registerStpHoverProvider(context);
  registerTraceLanguageSupport(context);
  registerHslMethodEditor(context);

  const debugAdapterFactory = new HslDebugAdapterFactory();
  context.subscriptions.push(
    vscode.debug.registerDebugAdapterDescriptorFactory("hsl", debugAdapterFactory),
    vscode.debug.registerDebugConfigurationProvider(
      "hsl",
      new HslDebugConfigurationProvider()
    )
  );

  function resolveHslFile(uri?: vscode.Uri): string | undefined {
    if (uri?.scheme === "file") {
      return uri.fsPath;
    }
    const editor = vscode.window.activeTextEditor;
    if (!editor || editor.document.uri.scheme !== "file") {
      vscode.window.showErrorMessage("No local HSL file is active.");
      return undefined;
    }
    return editor.document.fileName;
  }

  function validateTarget(filePath: string, simulation: boolean): boolean {
    if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
      vscode.window.showErrorMessage(`HSL target does not exist: ${filePath}`);
      return false;
    }

    const ext = path.extname(filePath).toLowerCase();
    const allowed = simulation ? SIMULATION_EXTENSIONS : HXRUN_EXTENSIONS;
    if (!isSupportedLaunchTarget(filePath, simulation)) {
      const purpose = simulation ? "simulate" : "run with HxRun";
      vscode.window.showErrorMessage(
        `Cannot ${purpose} '${ext || "extensionless"}' files. Valid extensions: ${allowed.join(", ")}.`
      );
      return false;
    }

    if (simulation) {
      try {
        const source = fs.readFileSync(filePath, "utf8");
        if (!hasMethodMain(source)) {
          vscode.window.showErrorMessage(
            "Simulation requires a source method containing 'method main()'. To test a library, create a small HSL method that includes and calls it."
          );
          return false;
        }
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        vscode.window.showErrorMessage(`Unable to read HSL target: ${message}`);
        return false;
      }
    }
    return true;
  }

  async function saveAndLaunch(filePath: string, noDebug: boolean): Promise<void> {
    try {
      const doc = vscode.workspace.textDocuments.find(
        (candidate) => path.normalize(candidate.fileName).toLowerCase() === path.normalize(filePath).toLowerCase()
      );
      if (doc?.isDirty) {
        const saved = await doc.save();
        if (!saved) {
          vscode.window.showErrorMessage("The HSL file could not be saved, so it was not launched.");
          return;
        }
        await waitForPendingHslChecksum(filePath);
      }

      const started = await vscode.debug.startDebugging(undefined, {
        type: "hsl",
        name: noDebug ? "Run HSL Method" : "Simulate HSL Method",
        request: "launch",
        program: filePath,
        hamiltonDir: getHamiltonDir(),
        noDebug,
        strictSimulation: true,
        internalConsoleOptions: "openOnSessionStart",
      });
      if (!started) {
        vscode.window.showErrorMessage("VS Code could not start the HSL launch session.");
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(`HSL launch failed: ${message}`);
    }
  }

  context.subscriptions.push(
    vscode.commands.registerCommand("hsl.runMethod", async (uri?: vscode.Uri) => {
      const filePath = resolveHslFile(uri);
      if (filePath && validateTarget(filePath, false)) {
        await saveAndLaunch(filePath, true);
      }
    }),
    vscode.commands.registerCommand("hsl.debugMethod", async (uri?: vscode.Uri) => {
      const filePath = resolveHslFile(uri);
      if (filePath && validateTarget(filePath, true)) {
        await saveAndLaunch(filePath, false);
      }
    })
  );

  const updateEditorAvailability = (): void => {
    void vscode.commands.executeCommand(
      "setContext",
      "hsl.hamiltonEditorAvailable",
      fs.existsSync(getHamiltonEditorPath())
    );
    void vscode.commands.executeCommand(
      "setContext",
      "hsl.hamiltonRunAvailable",
      fs.existsSync(getHamiltonRunPath())
    );
  };
  updateEditorAvailability();
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((event) => {
      if (event.affectsConfiguration("hsl.hamiltonDir")) {
        updateEditorAvailability();
      }
    }),
    vscode.commands.registerCommand(
      "hsl.openInHamiltonEditor",
      (uri?: vscode.Uri) => {
        const filePath = resolveHslFile(uri);
        if (!filePath) {
          return;
        }
        const editorPath = getHamiltonEditorPath();
        cp.execFile(editorPath, [filePath], { windowsHide: true }, (error) => {
          if (error) {
            vscode.window.showErrorMessage(
              `Failed to open Hamilton HSL Editor: ${error.message}`
            );
          }
        });
      }
    )
  );
}

export function deactivate(): void {
  // Resources are owned by context.subscriptions.
}
