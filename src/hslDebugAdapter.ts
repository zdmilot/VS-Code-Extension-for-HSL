import * as vscode from "vscode";
import * as path from "path";
import * as fs from "fs";
import * as cp from "child_process";

// --- Debug Adapter Protocol message types ---

interface DapMessage {
  seq: number;
  type: string;
}

interface DapRequest extends DapMessage {
  type: "request";
  command: string;
  arguments?: unknown;
}

interface DapResponse extends DapMessage {
  type: "response";
  request_seq: number;
  command: string;
  success: boolean;
  body?: unknown;
  message?: string;
}

interface DapEvent extends DapMessage {
  type: "event";
  event: string;
  body?: unknown;
}

interface LaunchArguments {
  program?: string;
  hamiltonDir?: string;
  verbose?: boolean;
  noDebug?: boolean;
  strictSimulation?: boolean;
}

const DEFAULT_HAMILTON_DIR = "C:\\Program Files (x86)\\Hamilton";
const VALID_HXRUN_EXTENSIONS = [".hsl", ".sub", ".med"];
const VALID_SIMULATION_EXTENSIONS = [".hsl", ".sub"];

/**
 * Minimal inline Debug Adapter that supports two modes:
 *
 * - **Start Debugging (F5)**: Runs the bundled HSL simulation debugger.
 * - **Run Without Debugging (Ctrl+F5)**: Launches HxRun.exe with -t -minimized,
 *   then tails the resulting trace file into the Debug Console.
 */
export class HslInlineDebugAdapter implements vscode.DebugAdapter {
  private _seq = 1;
  private _process: cp.ChildProcess | undefined;
  private _traceWatcher: fs.FSWatcher | undefined;
  private _traceStream: fs.ReadStream | undefined;
  private _traceInterval: ReturnType<typeof setInterval> | undefined;
  private _pendingLaunchRequest: DapRequest | undefined;
  private _pendingLaunchArgs: LaunchArguments | undefined;
  private _logfileDir = path.join(DEFAULT_HAMILTON_DIR, "Logfiles");
  private _traceReadNewContent: (() => void) | undefined;
  private _traceBuffer = "";
  private _simulationFaults = 0;
  private readonly _onDidSendMessage = new vscode.EventEmitter<DapResponse | DapEvent>();
  readonly onDidSendMessage: vscode.Event<DapResponse | DapEvent> = this._onDidSendMessage.event;

  handleMessage(message: DapRequest): void {
    switch (message.command) {
      case "initialize":
        this._sendResponse(message, {
          supportsConfigurationDoneRequest: true,
        });
        this._sendEvent("initialized");
        break;

      case "launch":
        this._handleLaunch(message);
        break;

      case "configurationDone":
        this._sendResponse(message, {});
        // VS Code is now fully ready -- start the actual execution
        if (this._pendingLaunchRequest && this._pendingLaunchArgs) {
          const req = this._pendingLaunchRequest;
          const args = this._pendingLaunchArgs;
          this._pendingLaunchRequest = undefined;
          this._pendingLaunchArgs = undefined;

          const program = args.program!;
          if (args.noDebug) {
            this._handleRunWithoutDebugging(req, program, args);
          } else {
            this._handleDebugWithPython(req, program, args);
          }
        }
        break;

      case "disconnect":
        this._handleDisconnect(message);
        break;

      case "threads":
        this._sendResponse(message, { threads: [{ id: 1, name: "main" }] });
        break;

      default:
        this._sendResponse(message, {});
        break;
    }
  }

  private async _handleLaunch(request: DapRequest): Promise<void> {
    const args = request.arguments as LaunchArguments;

    const program = args?.program;
    if (!program) {
      this._sendErrorResponse(request, "No HSL file specified in launch configuration.");
      this._sendEvent("terminated");
      return;
    }

    const ext = path.extname(program).toLowerCase();
    const validExtensions = args.noDebug
      ? VALID_HXRUN_EXTENSIONS
      : VALID_SIMULATION_EXTENSIONS;
    if (!validExtensions.includes(ext)) {
      this._sendErrorResponse(
        request,
        `Cannot ${args.noDebug ? "run" : "simulate"} file with extension '${ext}'. Valid: ${validExtensions.join(", ")}`
      );
      this._sendEvent("terminated");
      return;
    }

    // Store the launch request -- actual execution is deferred until
    // configurationDone so VS Code's Debug Console is fully ready.
    this._pendingLaunchRequest = request;
    this._pendingLaunchArgs = args;
    this._sendResponse(request, {});
  }

  // ─── Run Without Debugging (Ctrl+F5): HxRun.exe + trace tailing ───

  private _handleRunWithoutDebugging(
    _request: DapRequest,
    program: string,
    args: LaunchArguments
  ): void {
    const hamiltonDir = args.hamiltonDir || DEFAULT_HAMILTON_DIR;
    const hxRunPath = path.join(hamiltonDir, "Bin", "HxRun.exe");
    this._logfileDir = path.join(hamiltonDir, "Logfiles");
    if (!fs.existsSync(hxRunPath)) {
      this._outputLine(`HxRun.exe not found at: ${hxRunPath}`, "stderr");
      this._outputLine("Make sure Hamilton VENUS is installed.", "stderr");
      this._sendEvent("terminated");
      return;
    }

    const methodName = path.basename(program, path.extname(program));

    this._outputLine("=".repeat(60));
    this._outputLine("  Hamilton HxRun -- Run Without Debugging");
    this._outputLine("=".repeat(60));
    this._outputLine(`  Method:  ${path.basename(program)}`);
    this._outputLine(`  HxRun:   ${hxRunPath}`);
    this._outputLine(`  Logdir:  ${this._logfileDir}`);
    this._outputLine("");

    // Record the timestamp just before launch so we can find only newer trace files
    const launchTime = Date.now();

    // Spawn HxRun.exe with -t (run & terminate) and -minimized
    try {
      this._process = cp.spawn(
        hxRunPath,
        [program, "-t", "-minimized"],
        { shell: false }
      );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      this._outputLine(`Failed to launch HxRun.exe: ${msg}`, "stderr");
      this._sendEvent("terminated");
      return;
    }

    this._process.on("error", (err) => {
      this._outputLine(`HxRun.exe error: ${err.message}`, "stderr");
      this._cleanupTraceWatching();
      this._sendEvent("terminated");
    });

    this._outputLine("HxRun.exe launched. Waiting for trace file...");

    // Start watching for the trace file
    this._watchForTraceFile(methodName, launchTime);

    // When HxRun exits, finish tailing and terminate
    this._process.on("close", (code) => {
      // Give a brief moment for any final file writes to flush
      setTimeout(() => {
        this._finishTraceTailing(code);
      }, 1500);
    });
  }

  /**
   * Watches the Hamilton Logfiles directory for a new trace file matching
   * the pattern: <methodName>_<guid>_Trace.trc
   *
   * Uses fs.watch on the directory (not recursive) for a lightweight,
   * event-driven approach. As soon as the file is found, the watcher is
   * closed immediately to avoid lingering processes.
   */
  private _watchForTraceFile(methodName: string, launchTime: number): void {
    // Pattern: methodName_<32-hex-guid>_Trace.trc
    const traceRegex = new RegExp(
      `^${this._escapeRegex(methodName)}_[0-9a-fA-F]+_Trace\\.trc$`
    );

    let found = false;

    // First, check if a matching file already exists (race condition guard)
    const existing = this._findLatestTraceFile(methodName, traceRegex, launchTime);
    if (existing) {
      found = true;
      this._onTraceFileFound(existing);
      return;
    }

    // Watch the directory for new files. The watcher fires on any change.
    try {
      this._traceWatcher = fs.watch(this._logfileDir, (_eventType, filename) => {
        if (found || !filename) {
          return;
        }
        if (traceRegex.test(filename)) {
          const fullPath = path.join(this._logfileDir, filename);
          // Verify it was created after our launch
          try {
            const stat = fs.statSync(fullPath);
            if (stat.mtimeMs >= launchTime - 2000) {
              found = true;
              this._closeTraceWatcher();
              this._onTraceFileFound(fullPath);
            }
          } catch {
            // File might not be fully written yet; ignore
          }
        }
      });

      this._traceWatcher.on("error", () => {
        this._closeTraceWatcher();
      });
    } catch {
      this._outputLine(
        "Warning: Could not watch Logfiles directory. Trace output will not be streamed.",
        "stderr"
      );
    }

    // Safety timeout: if no trace file appears within 120 seconds, stop watching
    const watchTimeout = setTimeout(() => {
      if (!found) {
        this._closeTraceWatcher();
        this._outputLine(
          "Warning: Trace file not found within timeout. HxRun may still be running."
        );
      }
    }, 120_000);

    // Store so we can clean up on disconnect
    const origClose = this._closeTraceWatcher.bind(this);
    this._closeTraceWatcher = () => {
      clearTimeout(watchTimeout);
      origClose();
    };
  }

  /**
   * Scans the logfile directory for the most recently modified trace file
   * matching the pattern that was modified after launchTime.
   */
  private _findLatestTraceFile(
    _methodName: string,
    traceRegex: RegExp,
    launchTime: number
  ): string | undefined {
    try {
      const files = fs.readdirSync(this._logfileDir);
      let best: { path: string; mtime: number } | undefined;

      for (const f of files) {
        if (!traceRegex.test(f)) {
          continue;
        }
        const full = path.join(this._logfileDir, f);
        try {
          const stat = fs.statSync(full);
          if (stat.mtimeMs >= launchTime - 2000) {
            if (!best || stat.mtimeMs > best.mtime) {
              best = { path: full, mtime: stat.mtimeMs };
            }
          }
        } catch {
          // skip
        }
      }
      return best?.path;
    } catch {
      return undefined;
    }
  }

  /**
   * Called once the trace file is found. Starts tailing it.
   */
  private _onTraceFileFound(tracePath: string): void {
    this._outputLine(`Trace file found: ${path.basename(tracePath)}`);
    this._outputLine("-".repeat(60));
    this._startTailing(tracePath);
  }

  /**
   * Tails the trace file by polling for new content.
   * We use polling (setInterval + fs.read) because fs.watch on individual
   * files fires on every write but doesn't tell us the new content, and
   * fs.createReadStream in 'flowing' mode can miss partial writes.
   * The interval is short (500ms) for near-realtime output.
   */
  private _startTailing(tracePath: string): void {
    let offset = 0;
    this._traceBuffer = "";

    const readNewContent = () => {
      try {
        const stat = fs.statSync(tracePath);
        if (stat.size <= offset) {
          return;
        }
        const fd = fs.openSync(tracePath, "r");
        try {
          const chunk = Buffer.alloc(stat.size - offset);
          fs.readSync(fd, chunk, 0, chunk.length, offset);
          offset = stat.size;
          this._traceBuffer += chunk.toString("utf-8");

          // Emit complete lines
          const lines = this._traceBuffer.split(/\r?\n/);
          // Keep the last (possibly incomplete) line in the buffer
          this._traceBuffer = lines.pop() || "";
          for (const line of lines) {
            this._outputLine(line);
          }
        } finally {
          fs.closeSync(fd);
        }
      } catch {
        // File may be locked by HxRun; skip this tick
      }
    };

    // Poll every 500ms
    this._traceInterval = setInterval(readNewContent, 500);
    this._traceReadNewContent = readNewContent;

    // Do an immediate first read
    readNewContent();
  }

  /**
   * Called when HxRun.exe exits. Does one final read of the trace file,
   * then cleans up all watchers/intervals and fires the terminated event.
   */
  private _finishTraceTailing(exitCode: number | null): void {
    this._traceReadNewContent?.();
    if (this._traceBuffer.length > 0) {
      this._outputLine(this._traceBuffer);
      this._traceBuffer = "";
    }

    // Small delay to catch final writes, then clean up
    setTimeout(() => {
      this._cleanupTraceWatching();
      this._outputLine("-".repeat(60));
      if (exitCode === 0) {
        this._outputLine("HxRun.exe completed successfully.");
      } else {
        this._outputLine(`HxRun.exe exited with code ${exitCode}.`);
      }
      this._sendEvent("terminated");
    }, 500);
  }

  private _closeTraceWatcher(): void {
    if (this._traceWatcher) {
      this._traceWatcher.close();
      this._traceWatcher = undefined;
    }
  }

  private _cleanupTraceWatching(): void {
    this._closeTraceWatcher();
    if (this._traceInterval) {
      clearInterval(this._traceInterval);
      this._traceInterval = undefined;
    }
    this._traceReadNewContent = undefined;
    this._traceBuffer = "";
    if (this._traceStream) {
      this._traceStream.destroy();
      this._traceStream = undefined;
    }
  }

  private _escapeRegex(str: string): string {
    return str.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  // ─── Start Debugging (F5): Python HSL simulation debugger ───

  private _handleDebugWithPython(
    _request: DapRequest,
    program: string,
    args: LaunchArguments
  ): void {
    const extensionRoot = this._findExtensionRoot();
    const debuggerDir = path.join(extensionRoot, "HSL Debugger");
    const bundledExe = path.join(debuggerDir, "dist", "hsl_debugger", "hsl_debugger.exe");

    const hamiltonDir = args?.hamiltonDir || DEFAULT_HAMILTON_DIR;
    const verbose = args?.verbose !== false;
    const strictSimulation = args?.strictSimulation !== false;

    if (!fs.existsSync(bundledExe)) {
      this._outputLine(
        `HSL debugger executable not found at:\n${bundledExe}\n\nPlease reinstall the extension or rebuild the debugger.`,
        "stderr"
      );
      this._sendEvent("terminated");
      return;
    }

    const executable = bundledExe;
    const spawnArgs: string[] = [program, "--hamilton-dir", hamiltonDir];

    if (!verbose) {
      spawnArgs.push("--quiet");
    }
    if (strictSimulation) {
      spawnArgs.push("--strict");
    }

    this._outputLine("=".repeat(60));
    this._outputLine("  HSL Debugger -- Python Simulation");
    this._outputLine("=".repeat(60));
    this._outputLine(`  Method:  ${path.basename(program)}`);
    this._outputLine("  Engine:  Bundled runtime");
    this._outputLine(`  Hamilton dir: ${hamiltonDir}`);
    this._outputLine("");

    try {
      // Force unbuffered Python output and explicit piped stdio
      const env = { ...process.env, PYTHONUNBUFFERED: "1" };
      this._process = cp.spawn(executable, spawnArgs, {
        cwd: debuggerDir,
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
        env,
      });

      this._simulationFaults = 0;
      this._process.stdout!.on("data", (data: Buffer) => {
        const text = data.toString();
        for (const line of text.split(/\r?\n/)) {
          if (line.length > 0) {
            if (
              strictSimulation &&
              /\[SIM\] Unknown function:|Division by zero|Modulo by zero|\[HSL ERROR\]/i.test(line)
            ) {
              this._simulationFaults++;
            }
            this._outputLine(line);
          }
        }
      });

      this._process.stderr!.on("data", (data: Buffer) => {
        const text = data.toString();
        for (const line of text.split(/\r?\n/)) {
          if (line.length > 0) {
            this._outputLine(line, "stderr");
          }
        }
      });

      this._process.on("close", (code) => {
        this._outputLine("");
        if (code === 0) {
          if (strictSimulation && this._simulationFaults > 0) {
            this._outputLine(
              `HSL simulation completed with ${this._simulationFaults} strict validation fault${this._simulationFaults === 1 ? "" : "s"}.`,
              "stderr"
            );
          } else {
            this._outputLine("HSL method completed successfully.");
          }
        } else {
          this._outputLine(`HSL method exited with code ${code}.`, "stderr");
        }
        // Delay terminated event to allow VS Code to render buffered output
        setTimeout(() => {
          this._sendEvent("terminated");
        }, 300);
      });

      this._process.on("error", (err) => {
        this._outputLine(`Failed to start HSL debugger: ${err.message}`, "stderr");
        setTimeout(() => {
          this._sendEvent("terminated");
        }, 300);
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      this._outputLine(`Error launching HSL debugger: ${msg}`, "stderr");
      this._sendEvent("terminated");
    }
  }

  // ─── Common helpers ───

  private _handleDisconnect(request: DapRequest): void {
    this._cleanupTraceWatching();
    if (this._process && !this._process.killed) {
      this._process.kill();
    }
    this._sendResponse(request, {});
    this._sendEvent("terminated");
  }

  private _findExtensionRoot(): string {
    return path.resolve(__dirname, "..");
  }

  private _outputLine(text: string, category: string = "stdout"): void {
    this._sendEvent("output", {
      category,
      output: text + "\n",
    });
  }

  private _sendResponse(request: DapRequest, body: unknown): void {
    const response: DapResponse = {
      seq: this._seq++,
      type: "response",
      request_seq: request.seq,
      command: request.command,
      success: true,
      body,
    };
    this._onDidSendMessage.fire(response);
  }

  private _sendErrorResponse(request: DapRequest, message: string): void {
    const response: DapResponse = {
      seq: this._seq++,
      type: "response",
      request_seq: request.seq,
      command: request.command,
      success: false,
      message,
    };
    this._onDidSendMessage.fire(response);
  }

  private _sendEvent(event: string, body?: unknown): void {
    const evt: DapEvent = {
      seq: this._seq++,
      type: "event",
      event,
      body,
    };
    this._onDidSendMessage.fire(evt);
  }

  dispose(): void {
    this._cleanupTraceWatching();
    if (this._process && !this._process.killed) {
      this._process.kill();
    }
    this._onDidSendMessage.dispose();
  }
}

/**
 * Factory that creates our inline debug adapter instances.
 */
export class HslDebugAdapterFactory implements vscode.DebugAdapterDescriptorFactory {
  createDebugAdapterDescriptor(
    _session: vscode.DebugSession
  ): vscode.ProviderResult<vscode.DebugAdapterDescriptor> {
    return new vscode.DebugAdapterInlineImplementation(new HslInlineDebugAdapter());
  }
}

/**
 * Provides automatic debug configurations for HSL files so F5 works
 * without requiring a launch.json.
 */
export class HslDebugConfigurationProvider implements vscode.DebugConfigurationProvider {
  resolveDebugConfiguration(
    _folder: vscode.WorkspaceFolder | undefined,
    config: vscode.DebugConfiguration,
    _token?: vscode.CancellationToken
  ): vscode.ProviderResult<vscode.DebugConfiguration> {
    // If the user pressed F5 without any launch.json or with an empty config
    if (!config.type && !config.request && !config.name) {
      const editor = vscode.window.activeTextEditor;
      if (editor && editor.document.languageId === "hsl") {
        config.type = "hsl";
        config.name = "Simulate HSL Method";
        config.request = "launch";
        config.program = "${file}";
        config.strictSimulation = true;
      }
    }

    if (!config.program) {
      config.program = "${file}";
    }

    return config;
  }
}
