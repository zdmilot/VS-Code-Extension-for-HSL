"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = __importStar(require("vscode"));
const path = __importStar(require("path"));
const fs = __importStar(require("fs"));
const cp = __importStar(require("child_process"));
const builtins_1 = require("./builtins");
const diagnostics_1 = require("./diagnostics");
const hslIntellisense_1 = require("./hslIntellisense");
const stpHoverProvider_1 = require("./stpHoverProvider");
const traceLanguageSupport_1 = require("./traceLanguageSupport");
const hslMethodEditor_1 = require("./hslMethodEditor");
const hslCore_1 = require("./hslCore");
const hslDebugAdapter_1 = require("./hslDebugAdapter");
const DEFAULT_HAMILTON_DIR = "C:\\Program Files (x86)\\Hamilton";
function getHamiltonDir() {
    return path.normalize(vscode.workspace
        .getConfiguration("hsl")
        .get("hamiltonDir", DEFAULT_HAMILTON_DIR));
}
function getHamiltonEditorPath() {
    return path.join(getHamiltonDir(), "Bin", "HxHSLMetEd.exe");
}
function getHamiltonRunPath() {
    return path.join(getHamiltonDir(), "Bin", "HxRun.exe");
}
/** Called once when the extension is activated. */
function activate(context) {
    const completionItems = (0, builtins_1.buildCompletionItems)();
    context.subscriptions.push(vscode.languages.registerCompletionItemProvider({ language: "hsl", scheme: "file" }, {
        provideCompletionItems() {
            return completionItems;
        },
    }));
    (0, hslIntellisense_1.registerHslIntelliSense)(context);
    (0, diagnostics_1.createHslDiagnostics)(context);
    (0, stpHoverProvider_1.registerStpHoverProvider)(context);
    (0, traceLanguageSupport_1.registerTraceLanguageSupport)(context);
    (0, hslMethodEditor_1.registerHslMethodEditor)(context);
    const debugAdapterFactory = new hslDebugAdapter_1.HslDebugAdapterFactory();
    context.subscriptions.push(vscode.debug.registerDebugAdapterDescriptorFactory("hsl", debugAdapterFactory), vscode.debug.registerDebugConfigurationProvider("hsl", new hslDebugAdapter_1.HslDebugConfigurationProvider()));
    function resolveHslFile(uri) {
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
    function validateTarget(filePath, simulation) {
        if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
            vscode.window.showErrorMessage(`HSL target does not exist: ${filePath}`);
            return false;
        }
        const ext = path.extname(filePath).toLowerCase();
        const allowed = simulation ? hslCore_1.SIMULATION_EXTENSIONS : hslCore_1.HXRUN_EXTENSIONS;
        if (!(0, hslCore_1.isSupportedLaunchTarget)(filePath, simulation)) {
            const purpose = simulation ? "simulate" : "run with HxRun";
            vscode.window.showErrorMessage(`Cannot ${purpose} '${ext || "extensionless"}' files. Valid extensions: ${allowed.join(", ")}.`);
            return false;
        }
        if (simulation) {
            try {
                const source = fs.readFileSync(filePath, "utf8");
                if (!(0, hslCore_1.hasMethodMain)(source)) {
                    vscode.window.showErrorMessage("Simulation requires a source method containing 'method main()'. To test a library, create a small HSL method that includes and calls it.");
                    return false;
                }
            }
            catch (error) {
                const message = error instanceof Error ? error.message : String(error);
                vscode.window.showErrorMessage(`Unable to read HSL target: ${message}`);
                return false;
            }
        }
        return true;
    }
    async function saveAndLaunch(filePath, noDebug) {
        try {
            const doc = vscode.workspace.textDocuments.find((candidate) => path.normalize(candidate.fileName).toLowerCase() === path.normalize(filePath).toLowerCase());
            if (doc?.isDirty) {
                const saved = await doc.save();
                if (!saved) {
                    vscode.window.showErrorMessage("The HSL file could not be saved, so it was not launched.");
                    return;
                }
                await (0, diagnostics_1.waitForPendingHslChecksum)(filePath);
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
        }
        catch (error) {
            const message = error instanceof Error ? error.message : String(error);
            vscode.window.showErrorMessage(`HSL launch failed: ${message}`);
        }
    }
    context.subscriptions.push(vscode.commands.registerCommand("hsl.runMethod", async (uri) => {
        const filePath = resolveHslFile(uri);
        if (filePath && validateTarget(filePath, false)) {
            await saveAndLaunch(filePath, true);
        }
    }), vscode.commands.registerCommand("hsl.debugMethod", async (uri) => {
        const filePath = resolveHslFile(uri);
        if (filePath && validateTarget(filePath, true)) {
            await saveAndLaunch(filePath, false);
        }
    }));
    const updateEditorAvailability = () => {
        void vscode.commands.executeCommand("setContext", "hsl.hamiltonEditorAvailable", fs.existsSync(getHamiltonEditorPath()));
        void vscode.commands.executeCommand("setContext", "hsl.hamiltonRunAvailable", fs.existsSync(getHamiltonRunPath()));
    };
    updateEditorAvailability();
    context.subscriptions.push(vscode.workspace.onDidChangeConfiguration((event) => {
        if (event.affectsConfiguration("hsl.hamiltonDir")) {
            updateEditorAvailability();
        }
    }), vscode.commands.registerCommand("hsl.openInHamiltonEditor", (uri) => {
        const filePath = resolveHslFile(uri);
        if (!filePath) {
            return;
        }
        const editorPath = getHamiltonEditorPath();
        cp.execFile(editorPath, [filePath], { windowsHide: true }, (error) => {
            if (error) {
                vscode.window.showErrorMessage(`Failed to open Hamilton HSL Editor: ${error.message}`);
            }
        });
    }));
}
function deactivate() {
    // Resources are owned by context.subscriptions.
}
//# sourceMappingURL=extension.js.map