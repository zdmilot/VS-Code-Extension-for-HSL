# HSL Debugger -- Simulation Runtime for Hamilton Standard Language

> **SIMULATION ONLY** -- This tool never connects to Hamilton hardware. It replaces `HXRun.exe` and `HxHSLMetEd.exe` for offline testing, debugging, and method validation.

## Overview

The HSL Debugger is a complete **simulation-only** runtime for [Hamilton Standard Language (HSL)](docs/HSL_LANGUAGE_REFERENCE.md) files. It preprocesses, tokenizes, parses, and interprets HSL source code entirely in Python, producing trace output and validating program logic -- without requiring a Hamilton instrument or the VENUS software environment.

### Key Features

- **Full HSL pipeline**: Preprocessor → Lexer → Parser → Interpreter
- **No hardware dependency**: Every device, timer, dialog, and COM object is stubbed for simulation
- **Real Hamilton library support**: Resolves `#include` directives against the Hamilton installation directory
- **Trace output**: Captures all `Trace()` and `FormatTrace()` calls for offline analysis
- **AST / token dump modes**: Inspect internal representations for debugging
- **Debugger hook**: Step callback support for future VS Code integration

---

## Quick Start

### Prerequisites (for end users)

- **No Python required.** The runtime is compiled into a standalone executable via PyInstaller.
- Hamilton software installed at `C:\Program Files (x86)\Hamilton` (for library includes)

### Prerequisites (for developers modifying the Python code)

- Python 3.10+ (tested on 3.13)
- PyInstaller (`pip install pyinstaller`)
- After modifying any `.py` file under `hsl_runtime/`, you **must** rebuild the exe (see "Building the Executable" below)

### Run a Method

Using the bundled executable (no Python needed):

```powershell
& "dist\hsl_debugger\hsl_debugger.exe" "C:\Program Files (x86)\Hamilton\Methods\MyMethod.hsl"
```

Using Python directly (developer mode):

```powershell
cd "HSL Debugger"
python -m hsl_runtime.main "C:\Program Files (x86)\Hamilton\Methods\MyMethod.hsl"
```

### Common Options

| Flag | Description |
|------|-------------|
| `--verbose` | Show detailed trace output (default) |
| `--quiet` | Suppress trace output |
| `--dump-tokens` | Print token stream and exit |
| `--dump-ast` | Print abstract syntax tree and exit |
| `--dump-preprocessed` | Print preprocessed source and exit |
| `--max-iterations N` | Safety limit for loops (default: 100,000) |
| `--hamilton-dir PATH` | Override Hamilton installation path |

### Example

```powershell
python -m hsl_runtime.main "C:\Program Files (x86)\Hamilton\Methods\Library Demo Methods\TraceLevel Demo.hsl" --quiet
```

Output:

```
============================================================
  HSL Debugger - Simulation Runtime v0.1
  SIMULATION ONLY - No hardware interaction
============================================================
  File:    C:\...\TraceLevel Demo.hsl
  Hamilton: C:\Program Files (x86)\Hamilton

[1/4] Preprocessing...
  Done (0.XXXs)
[2/4] Tokenizing...
  Tokens: 186,927
[3/4] Parsing...
  Declarations: XXX
[4/4] Executing (SIMULATION)...
  ...
============================================================
  Simulation finished successfully
============================================================
```

---

## Architecture

The runtime is a four-phase pipeline. Each phase is a self-contained module:

```
┌─────────────┐    ┌───────┐    ┌────────┐    ┌─────────────┐
│ Preprocessor │───▶│ Lexer │───▶│ Parser │───▶│ Interpreter │
│   (.hsl)     │    │       │    │  (AST) │    │ (Simulation)│
└─────────────┘    └───────┘    └────────┘    └─────────────┘
```

| Phase | Module | Input | Output |
|-------|--------|-------|--------|
| 1. Preprocess | [`preprocessor.py`](docs/PREPROCESSOR.md) | HSL file path | Flattened source string |
| 2. Tokenize | [`lexer.py`](docs/LEXER.md) | Source string | List of `Token` objects |
| 3. Parse | [`parser.py`](docs/PARSER.md) | Token list | `Program` AST node |
| 4. Execute | [`interpreter.py`](docs/INTERPRETER.md) | AST | Trace output + simulation results |

For detailed architecture documentation, see [Compiler Architecture](docs/COMPILER_ARCHITECTURE.md).

---

## Documentation

| Document | Description |
|----------|-------------|
| [HSL Language Reference](docs/HSL_LANGUAGE_REFERENCE.md) | Complete guide to HSL syntax, types, operators, and built-in functions |
| [Compiler Architecture](docs/COMPILER_ARCHITECTURE.md) | Pipeline design, data flow, and design decisions |
| [Preprocessor](docs/PREPROCESSOR.md) | `#include`, `#define`, `#ifdef/#ifndef`, `#pragma once` |
| [Lexer / Tokenizer](docs/LEXER.md) | Token types, keyword map, tokenization rules |
| [AST Node Reference](docs/AST_NODES.md) | All 25+ AST node types with fields and hierarchy |
| [Parser](docs/PARSER.md) | Recursive descent, operator precedence, error recovery |
| [Interpreter / Runtime](docs/INTERPRETER.md) | Type system, built-in functions, simulation stubs |
| [CLI Entry Point](docs/MAIN.md) | Command-line interface and pipeline orchestration |

---

## Building the Executable

**IMPORTANT:** After modifying any `.py` file under `hsl_runtime/`, you must rebuild the standalone exe. The VS Code extension runs `dist/hsl_debugger/hsl_debugger.exe` -- it does **not** invoke Python directly. Changes to the Python source will have **no effect** until the exe is rebuilt.

```powershell
cd "HSL Debugger"
pyinstaller --onedir --name hsl_debugger --console --noconfirm hsl_runtime/main.py
```

This produces `dist/hsl_debugger/hsl_debugger.exe` plus supporting DLLs. The `--console` flag is required so stdout/stderr are piped to the VS Code debug adapter. The `--onedir` flag is preferred over `--onefile` for faster startup.

The runtime uses **only Python standard library modules** (no pip packages), so the build is straightforward with no hidden imports needed.

**Do not commit `build/`, `dist/`, or `*.spec` to git** -- they are in `.gitignore`. The `dist/` folder is included in the packaged `.vsix` extension via `.vscodeignore`.

---

## Project Structure

```
HSL Debugger/
├── README.md                    # This file
├── docs/
│   ├── HSL_LANGUAGE_REFERENCE.md
│   ├── COMPILER_ARCHITECTURE.md
│   ├── PREPROCESSOR.md
│   ├── LEXER.md
│   ├── AST_NODES.md
│   ├── PARSER.md
│   ├── INTERPRETER.md
│   └── MAIN.md
├── hsl_runtime/
│   ├── __init__.py              # Package init (v0.1.0)
│   ├── preprocessor.py          # Phase 1: Include/define/ifdef resolution
│   ├── lexer.py                 # Phase 2: Tokenization
│   ├── ast_nodes.py             # AST node type definitions
│   ├── parser.py                # Phase 3: Recursive-descent parser
│   ├── interpreter.py           # Phase 4: Simulation interpreter
│   └── main.py                  # CLI entry point
├── dist/                        # PyInstaller output (NOT in git)
│   └── hsl_debugger/
│       └── hsl_debugger.exe     # Standalone exe shipped with extension
└── build/                       # PyInstaller intermediates (NOT in git)
```

---

## Simulation Behavior

All hardware-facing operations are stubbed:

| HSL Feature | Simulation Behavior |
|-------------|-------------------|
| `device` declarations | Creates `HslDevice` stub; logs layout info |
| Device step calls (e.g., `ML_STAR.Aspirate()`) | Logged; returns success |
| `Trace()` / `FormatTrace()` | Captured in `TraceOutput`; optionally printed |
| `MessageBox()` | Logged; returns `hslOK` (1) |
| `InputBox()` | Logged; returns empty string |
| `Shell()` | Logged; **never executed** |
| `timer.WaitTimer()` | Returns immediately (no delay) |
| `dialog.ShowCustomDialog()` | Returns OK automatically |
| `object.CreateObject()` | Creates stub; logs ProgID |
| `file.Open()` / `file.ReadString()` | File reads work; writes are stubbed |
| `Fork()` / `Join()` | Not implemented (single-threaded simulation) |
| `onerror goto` / `onerror resume next` / `resume next` | Simulated error-handler state and label dispatch |

---

## Test Results

Tested against Hamilton demo methods:

| File | Tokens | Functions | Parse Warnings | Result |
|------|--------|-----------|----------------|--------|
| TraceLevel Demo.hsl | 186,927 | 648 | ~100 | Executes `main()`, hits intentional `abort` |
| CustomDialogs.hsl | 213,969 | 704 | ~123 | Executes through full dialog simulation |

Parse warnings are mostly in Hamilton library code for constructs that are intentionally stubbed or only partially simulated.

---

## Limitations

- **No real hardware**: By design. All device calls are simulation stubs.
- **No COM interop**: `object.CreateObject()` creates a property bag, not a real COM object.
- **No threading**: `Fork()` / `Join()` are not simulated.
- **No structure-value simulation**: permissive mode may recover past `struct` declarations; strict mode rejects them explicitly instead of pretending to execute their semantics.
- **Selectable external-call handling**: permissive mode logs unsupported external functions and returns a neutral value; strict mode fails on them.
- **Behavioral compatibility, not VENUS replacement**: parser and control-flow behavior are tested against installed HSL sources, but hardware, COM, Scheduler concurrency, firmware, and proprietary runtime services remain explicit simulations.

---

## License

This project is an independent simulation tool and is not affiliated with, endorsed by, or supported by Hamilton Company. Hamilton Standard Language and VENUS are trademarks of Hamilton Company.
