"""
HSL Debugger - Main Entry Point
================================
SIMULATION ONLY - Never connects to Hamilton hardware.
Replaces HXRun.exe and HxHSLMetEd.exe for testing/debugging HSL methods.

Usage:
    python -m hsl_runtime.main <path-to-hsl-file> [options]

Options:
    --verbose    Show detailed trace output
    --quiet      Suppress trace output
    --dump-ast   Print the AST instead of executing
    --dump-tokens Print tokens instead of parsing
"""

import sys
import os
import argparse
import time
import uuid
import winreg
import tempfile
from datetime import datetime

# Force unbuffered stdout/stderr so output streams in real time
# when spawned as a child process (e.g. from VS Code debug adapter).
# Without this, PyInstaller exe output is fully buffered and nothing
# appears until the process exits.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hsl_runtime.preprocessor import Preprocessor
from hsl_runtime.lexer import Lexer
from hsl_runtime.parser import Parser
from hsl_runtime.interpreter import Interpreter, TraceOutput


def main():
    parser = argparse.ArgumentParser(
        description="HSL Debugger - Simulation Runtime for Hamilton HSL files",
        epilog="SIMULATION ONLY - Never connects to Hamilton hardware."
    )
    parser.add_argument("hsl_file", help="Path to the HSL file to execute")
    parser.add_argument("--verbose", action="store_true", default=True,
                        help="Show detailed trace output (default)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress trace output")
    parser.add_argument("--dump-ast", action="store_true",
                        help="Print the AST instead of executing")
    parser.add_argument("--dump-tokens", action="store_true",
                        help="Print tokens instead of parsing")
    parser.add_argument("--dump-preprocessed", action="store_true",
                        help="Print preprocessed source instead of parsing")
    parser.add_argument("--max-iterations", type=int, default=100000,
                        help="Maximum loop iterations (safety limit)")
    parser.add_argument("--strict", action="store_true",
                        help="Reject missing includes, parser recovery, unsupported constructs, and unknown calls")
    parser.add_argument("--hamilton-dir", type=str,
                        default=r"C:\Program Files (x86)\Hamilton",
                        help="Hamilton installation directory")
    parser.add_argument("--log-dir", type=str,
                        default=None,
                        help="Directory for trace logs (default: per-user local application data)")

    args = parser.parse_args()

    hsl_path = os.path.abspath(args.hsl_file)
    if not os.path.exists(hsl_path):
        print(f"Error: File not found: {hsl_path}")
        sys.exit(1)

    verbose = not args.quiet

    print("=" * 60)
    print("  HSL Debugger - Simulation Runtime v0.1")
    print("  SIMULATION ONLY - No hardware interaction")
    print("=" * 60)
    print(f"  File:    {hsl_path}")
    print(f"  Hamilton: {args.hamilton_dir}")
    print()

    # === Phase 1: Preprocess ===
    print("[1/4] Preprocessing...")
    t0 = time.time()
    try:
        preprocessor = Preprocessor(hamilton_dir=args.hamilton_dir, strict=args.strict)
        source = preprocessor.preprocess_file(hsl_path)
        t1 = time.time()
        print(f"  Done ({t1 - t0:.3f}s)")
        print(f"  Defines: {len(preprocessor.defines)}")
        print(f"  Included files: {len(preprocessor.included_files)}")

        if args.dump_preprocessed:
            print("\n=== PREPROCESSED SOURCE ===")
            for i, line in enumerate(source.split('\n'), 1):
                print(f"{i:6d}: {line}")
            print("=== END PREPROCESSED SOURCE ===")
            return 0
    except Exception as e:
        print(f"  Preprocessor error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1

    # === Phase 2: Tokenize ===
    print("[2/4] Tokenizing...")
    t0 = time.time()
    try:
        lexer = Lexer(source, filename=hsl_path)
        tokens = lexer.tokenize()
        t1 = time.time()
        print(f"  Done ({t1 - t0:.3f}s)")
        print(f"  Tokens: {len(tokens)}")

        if args.dump_tokens:
            print("\n=== TOKENS ===")
            for tok in tokens:
                print(f"  {tok}")
            print("=== END TOKENS ===")
            return 0
    except Exception as e:
        print(f"  Lexer error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1

    # === Phase 3: Parse ===
    print("[3/4] Parsing...")
    t0 = time.time()
    try:
        p = Parser(tokens, filename=hsl_path, strict=args.strict)
        program = p.parse()
        t1 = time.time()
        print(f"  Done ({t1 - t0:.3f}s)")
        print(f"  Declarations: {len(program.declarations)}")
        if p.errors:
            print(f"  Parser warnings/errors: {len(p.errors)}")
            for err in p.errors[:10]:
                print(f"    - {err}")

        if args.dump_ast:
            print("\n=== AST ===")
            _dump_ast(program, indent=0)
            print("=== END AST ===")
            return 0
    except Exception as e:
        print(f"  Parser error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1

    # === Phase 4: Execute (Simulation) ===
    print("[4/4] Executing (SIMULATION)...")
    print("-" * 40)
    t0 = time.time()
    run_id = uuid.uuid4().hex
    try:
        trace = TraceOutput(verbose=verbose)
        interp = Interpreter(
            trace=trace, strict=args.strict, hamilton_dir=args.hamilton_dir
        )
        interp._max_iterations = args.max_iterations
        interp.execute(program)
        t1 = time.time()
        print("-" * 40)
        print(f"  Execution complete ({t1 - t0:.3f}s)")
        print(f"  Trace messages: {len(trace.messages)}")
        print(f"  Functions found: {len(interp.functions)}")
        print(f"  Namespaces: {list(interp.namespaces.keys())}")
    except Exception as e:
        print(f"  Runtime error: {e}")
        if verbose:
            import traceback
            traceback.print_exc()
        return 1

    # === Write trace log file ===
    local_data = os.getenv("LOCALAPPDATA") or tempfile.gettempdir()
    log_dir = args.log_dir or os.path.join(
        local_data, "HSL Language Support", "Simulator Logs"
    )
    method_name = os.path.splitext(os.path.basename(hsl_path))[0]
    log_filename = f"{method_name}_{run_id}_Trace.trc"
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, log_filename)
        _write_trace_log(log_path, hsl_path, trace, method_name, run_id,
                        hamilton_dir=args.hamilton_dir)
        print(f"  Trace log: {log_path}")
    except Exception as e:
        print(f"  Warning: Could not write trace log: {e}")

    print()
    print("=" * 60)
    print("  Simulation finished successfully")
    print("=" * 60)
    return 0


# Internal trace messages that should NOT appear in the .trc file.
# These are runtime status messages, not user Trace() calls.
_INTERNAL_TRACE_PREFIXES = (
    "=== ",            # "=== HSL Simulation Runtime..." / "=== Simulation complete ==="
    "[SIM] ",          # Simulated device stubs
    "[COM] ",          # COM dispatch logs
    "Source: ",        # Runtime source file echo
    "SIMULATION MODE", # Runtime mode banner
    "Executing ",      # "Executing main()..."
    "Method aborted",
    "Method returned",
    "ABORT called",
    "PAUSE (simulation",
)


def _get_venus_version(hamilton_dir: str) -> str:
    """Read the installed Venus software version from the Windows registry.
    Falls back to 'VS Code HSL Debugger v0.1 (Simulation)' if not found."""
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Hamilton\Venus",
            0,
            winreg.KEY_READ,
        )
        version, _ = winreg.QueryValueEx(key, "Version")
        winreg.CloseKey(key)
        return str(version)
    except OSError:
        pass
    return "VS Code HSL Debugger v0.1 (Simulation)"


def _get_phoenix_version(hamilton_dir: str) -> str:
    """Read the Phoenix (HxRun.exe) file version. Returns '' if not found."""
    hxrun = os.path.join(hamilton_dir, "Bin", "HxRun.exe")
    if not os.path.isfile(hxrun):
        return ""
    try:
        # Use ctypes to read the file version resource
        import ctypes
        from ctypes import wintypes
        size = ctypes.windll.version.GetFileVersionInfoSizeW(hxrun, None)
        if not size:
            return ""
        buf = ctypes.create_string_buffer(size)
        ctypes.windll.version.GetFileVersionInfoW(hxrun, 0, size, buf)
        # Query the root block for VS_FIXEDFILEINFO
        p = ctypes.c_void_p()
        length = wintypes.UINT()
        ctypes.windll.version.VerQueryValueW(
            buf, r"\\", ctypes.byref(p), ctypes.byref(length)
        )
        if not length.value:
            return ""
        # VS_FIXEDFILEINFO struct: first two DWORDs after signature are
        # dwFileVersionMS and dwFileVersionLS
        info = ctypes.cast(p, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
        ms = info[2]  # dwFileVersionMS
        ls = info[3]  # dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:
        return ""


def _write_trace_log(log_path: str, hsl_path: str, trace: TraceOutput,
                     method_name: str, run_id: str, hamilton_dir: str = ""):
    """Write trace output in Hamilton .trc format.

    The output replicates the format produced by the Hamilton VENUS Run Control
    engine (HxRun.exe) so that existing log-analysis tools can parse it.
    """
    if not hamilton_dir:
        hamilton_dir = r"C:\Program Files (x86)\Hamilton"

    venus_ver = _get_venus_version(hamilton_dir)
    phoenix_ver = _get_phoenix_version(hamilton_dir)
    username = os.getenv("USERNAME", "unknown")

    now = datetime.now()

    def _ts():
        n = datetime.now()
        return n.strftime("%Y-%m-%d %H:%M:%S.") + f"{n.microsecond // 1000:03d}"

    with open(log_path, 'w', encoding='utf-8') as f:
        # -- Header (matches HxRun.exe output line-for-line) --
        f.write(f"{_ts()} Venus software version: {venus_ver}\n")
        f.write(f"{_ts()} SYSTEM : Analyze method - start; Method file {hsl_path}\n")
        f.write(f"{_ts()} SYSTEM : Analyze method - complete; \n")
        f.write(f"{_ts()} SYSTEM : Start method - start; \n")
        f.write(f"{_ts()} SYSTEM : Start method - progress; User name: {username}\n")
        if phoenix_ver:
            f.write(f"{_ts()} SYSTEM : Start method - progress; Phoenix software version: {phoenix_ver}\n")
        f.write(f"{_ts()} SYSTEM : Start method - progress; Database version: Standard\n")
        f.write(f"{_ts()} SYSTEM : Start method - progress; Sample tracking: Off\n")
        f.write(f"{_ts()} SYSTEM : Start method - progress; Vector Database: Off\n")
        f.write(f"{_ts()} SYSTEM : Start method - progress; Simulation mode: VS Code HSL Debugger\n")
        f.write(f"{_ts()} SYSTEM : Start method - complete; \n")
        f.write(f"{_ts()} SYSTEM : Execute method - start; Method file {hsl_path}\n")

        # -- User trace messages --
        for msg in trace.messages:
            # Skip internal runtime messages
            skip = False
            for prefix in _INTERNAL_TRACE_PREFIXES:
                if msg.startswith(prefix):
                    skip = True
                    break
            if skip:
                continue

            if msg.startswith("WARNING:") or msg.startswith("ERROR:"):
                f.write(f"{_ts()} SYSTEM : {msg}\n")
            else:
                f.write(f"{_ts()} USER : Trace - complete; {msg}\n")

        # -- Footer --
        f.write(f"{_ts()} SYSTEM : Execute method - complete; \n")
        f.write(f"{_ts()} SYSTEM : End method - start; \n")
        f.write(f"{_ts()} SYSTEM : End method - complete; \n")


def _dump_ast(node, indent=0):
    """Recursively print AST for debugging."""
    prefix = "  " * indent
    if node is None:
        return

    if isinstance(node, list):
        for item in node:
            _dump_ast(item, indent)
        return

    name = type(node).__name__
    attrs = {}

    if hasattr(node, '__dict__'):
        for k, v in node.__dict__.items():
            if k.startswith('_'):
                continue
            if isinstance(v, list):
                attrs[k] = f"[{len(v)} items]"
            elif isinstance(v, str):
                attrs[k] = repr(v) if len(v) < 60 else repr(v[:57] + "...")
            elif v is None:
                continue
            elif hasattr(v, '__dict__') and not isinstance(v, (int, float, bool)):
                attrs[k] = type(v).__name__
            else:
                attrs[k] = repr(v)

    attr_str = ", ".join(f"{k}={v}" for k, v in attrs.items())
    print(f"{prefix}{name}({attr_str})")

    if hasattr(node, '__dict__'):
        for k, v in node.__dict__.items():
            if k.startswith('_'):
                continue
            if isinstance(v, list):
                for item in v:
                    if hasattr(item, '__dict__') and not isinstance(item, (int, float, str, bool)):
                        _dump_ast(item, indent + 1)
            elif hasattr(v, '__dict__') and not isinstance(v, (int, float, str, bool)):
                _dump_ast(v, indent + 1)


if __name__ == "__main__":
    sys.exit(main())
