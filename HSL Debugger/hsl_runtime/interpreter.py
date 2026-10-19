"""
HSL Simulation Runtime / Interpreter
=====================================
Executes HSL AST in simulation mode. No hardware interaction.
Implements the core HSL type system, built-in functions, and control flow.

SIMULATION ONLY - This code NEVER connects to any Hamilton hardware.
"""

import os
import sys
import math
import time
import uuid
import traceback
from typing import Any, Optional, Callable
from dataclasses import dataclass, field
from .ast_nodes import *
from .com_objects import create_com_object, GenericComObject
from .firmware import DeviceSimulator, CLSID_MAP


# ============================================================================
# HSL Value System
# ============================================================================

class HslType:
    """HSL variant type - holds int, float, or string."""
    INTEGER = "hslInteger"
    FLOAT = "hslFloat"
    STRING = "hslString"


class HslValue:
    """A single HSL variant value."""
    __slots__ = ('_value', '_type')

    def __init__(self, value=0):
        if isinstance(value, bool):
            self._value = 1 if value else 0
            self._type = HslType.INTEGER
        elif isinstance(value, int):
            self._value = value
            self._type = HslType.INTEGER
        elif isinstance(value, float):
            self._value = value
            self._type = HslType.FLOAT
        elif isinstance(value, str):
            self._value = value
            self._type = HslType.STRING
        elif isinstance(value, HslValue):
            self._value = value._value
            self._type = value._type
        else:
            self._value = 0
            self._type = HslType.INTEGER

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, v):
        if isinstance(v, bool):
            self._value = 1 if v else 0
            self._type = HslType.INTEGER
        elif isinstance(v, int):
            self._value = v
            self._type = HslType.INTEGER
        elif isinstance(v, float):
            self._value = v
            self._type = HslType.FLOAT
        elif isinstance(v, str):
            self._value = v
            self._type = HslType.STRING
        elif isinstance(v, HslValue):
            self._value = v._value
            self._type = v._type
        else:
            self._value = 0
            self._type = HslType.INTEGER

    @property
    def hsl_type(self):
        return self._type

    def to_int(self) -> int:
        if self._type == HslType.INTEGER:
            return self._value
        elif self._type == HslType.FLOAT:
            return int(self._value)
        elif self._type == HslType.STRING:
            try:
                return int(self._value)
            except (ValueError, TypeError):
                return 0
        return 0

    def to_float(self) -> float:
        if self._type == HslType.FLOAT:
            return self._value
        elif self._type == HslType.INTEGER:
            return float(self._value)
        elif self._type == HslType.STRING:
            try:
                return float(self._value)
            except (ValueError, TypeError):
                return 0.0
        return 0.0

    def to_string(self) -> str:
        if self._type == HslType.STRING:
            return self._value
        elif self._type == HslType.INTEGER:
            return str(self._value)
        elif self._type == HslType.FLOAT:
            return str(self._value)
        return ""

    def to_bool(self) -> bool:
        if self._type == HslType.INTEGER:
            return self._value != 0
        elif self._type == HslType.FLOAT:
            return self._value != 0.0
        elif self._type == HslType.STRING:
            return len(self._value) > 0
        return False

    def __repr__(self):
        return f"HslValue({self._value!r})"

    def __eq__(self, other):
        if isinstance(other, HslValue):
            return self._value == other._value
        return self._value == other

    def __hash__(self):
        return hash(self._value)


class HslArray:
    """HSL dynamic array."""
    def __init__(self):
        self.data: list[HslValue] = []

    def set_size(self, n: int):
        if n < len(self.data):
            self.data = self.data[:n]
        else:
            while len(self.data) < n:
                self.data.append(HslValue(0))

    def get_size(self) -> int:
        return len(self.data)

    def add_as_last(self, value):
        if isinstance(value, HslValue):
            self.data.append(HslValue(value._value))  # Copy to avoid shared refs
        else:
            self.data.append(HslValue(value))

    def get_at(self, index: int) -> HslValue:
        if 0 <= index < len(self.data):
            return self.data[index]
        return HslValue(0)

    def set_at(self, index: int, value):
        if 0 <= index < len(self.data):
            if isinstance(value, HslValue):
                self.data[index] = HslValue(value._value)  # Copy to avoid shared refs
            else:
                self.data[index] = HslValue(value)

    def element_at(self, index: int) -> HslValue:
        return self.get_at(index)

    def __repr__(self):
        return f"HslArray({self.data})"


class HslSequence:
    """HSL sequence - ordered list of labware positions."""
    def __init__(self, name: str = ""):
        self.name = name
        self.positions: list[dict] = []  # [{labware_id, position_id}]
        self.current_pos = 1
        self.max_pos = 0
        self.count = 0

    def get_current_position(self) -> int:
        return self.current_pos

    def set_current_position(self, pos: int):
        self.current_pos = pos

    def get_total(self) -> int:
        return len(self.positions)

    def get_count(self) -> int:
        return self.count

    def set_count(self, n: int):
        self.count = n

    def get_max(self) -> int:
        return self.max_pos

    def set_max(self, n: int):
        self.max_pos = n

    def get_name(self) -> str:
        return self.name

    def add(self, labware_id: str, position_id: str):
        """Add a position to the sequence."""
        self.positions.append({'labware_id': labware_id, 'position_id': position_id})

    def get_position_id(self) -> str:
        """Get position ID at the current position (1-based)."""
        idx = self.current_pos - 1
        if 0 <= idx < len(self.positions):
            return self.positions[idx]['position_id']
        return ""

    def get_labware_id(self) -> str:
        """Get labware ID at the current position (1-based)."""
        idx = self.current_pos - 1
        if 0 <= idx < len(self.positions):
            return self.positions[idx]['labware_id']
        return ""

    def increment(self, n: int = 1):
        self.current_pos += n

    def __repr__(self):
        return f"HslSequence({self.name!r}, pos={self.current_pos}, total={len(self.positions)})"


class HslDevice:
    """HSL device - simulation stub. Never connects to hardware."""
    def __init__(self, name: str = "", layout: str = "", device_name: str = ""):
        self.name = name
        self.layout = layout
        self.device_name = device_name
        self.sequences: dict[str, HslSequence] = {}
        self._is_simulation = True  # ALWAYS simulation

    def __getattr__(self, name):
        if name.startswith('_') or name in ('name', 'layout', 'device_name', 'sequences'):
            return super().__getattribute__(name)
        # Return a sequence proxy for any attribute access
        if name not in self.sequences:
            self.sequences[name] = HslSequence(name)
        return self.sequences[name]

    def __repr__(self):
        return f"HslDevice({self.device_name!r}, SIMULATION)"


class HslFile:
    """HSL file handle - simulation of file I/O."""
    def __init__(self):
        self.path = ""
        self.mode = ""
        self.handle = None
        self.fields: dict[str, HslValue] = {}
        self.delimiter = ","
        self._is_open = False

    def open(self, path: str, mode: str, *args):
        self.path = path
        self.mode = mode
        self._is_open = True
        # In simulation, we can actually open files for reading
        try:
            py_mode = {'hslRead': 'r', 'hslWrite': 'w', 'hslAppend': 'a'}.get(mode, 'r')
            self.handle = open(path, py_mode, encoding='utf-8', errors='replace')
        except Exception:
            self._is_open = False

    def close(self):
        if self.handle:
            try:
                self.handle.close()
            except Exception:
                pass
        self._is_open = False

    def eof(self) -> bool:
        if not self.handle:
            return True
        return False


class HslObject:
    """HSL COM object wrapper. Uses real Python-backed COM implementations
    when available, falls back to a generic property bag."""
    def __init__(self):
        self.prog_id = ""
        self.properties: dict[str, Any] = {}
        self._com_impl = None  # Real Python-backed COM object

    def create_object(self, prog_id: str, *args):
        self.prog_id = prog_id
        # Try to create a real Python-backed COM implementation
        impl = create_com_object(prog_id)
        if impl is not None:
            self._com_impl = impl
        else:
            self._com_impl = GenericComObject(prog_id)
        return HslValue(1)

    def call_method(self, method_name: str, args: list) -> Any:
        """Call a method on the underlying COM implementation."""
        if self._com_impl is not None:
            func = getattr(self._com_impl, method_name, None)
            if func and callable(func):
                return func(*args)
        return None

    def __getattr__(self, name):
        if name.startswith('_') or name in ('prog_id', 'properties'):
            return super().__getattribute__(name)
        return self.properties.get(name, HslValue(0))

    def __setattr__(self, name, value):
        if name.startswith('_') or name in ('prog_id', 'properties'):
            super().__setattr__(name, value)
        else:
            self.properties[name] = value


class HslTimer:
    """HSL timer - simulation."""
    def __init__(self):
        self.start_time = 0.0
        self.duration = 0.0
        self.name = ""

    def set_timer(self, seconds: float):
        self.duration = seconds
        self.start_time = time.time()

    def wait_timer(self, *args):
        # In simulation, don't actually wait
        pass

    def elapsed(self) -> float:
        return time.time() - self.start_time


class HslDialog:
    """HSL dialog - simulation stub. Returns defaults."""
    def __init__(self):
        self.properties: dict[str, Any] = {}
        self.array_properties: dict[str, list] = {}

    def init_custom_dialog(self, dialog_id: str):
        self.properties = {"ReturnValue": HslValue(1)}  # OK by default
        self.array_properties = {}

    def set_custom_dialog_property(self, prop: str, value):
        self.properties[prop] = value if isinstance(value, HslValue) else HslValue(value)

    def set_custom_dialog_array_property(self, prop: str, array):
        if isinstance(array, HslArray):
            self.array_properties[prop] = [v.value for v in array.data]
        else:
            self.array_properties[prop] = array

    def show_custom_dialog(self):
        # Simulation: just log it, return OK
        pass

    def get_custom_dialog_property(self, prop: str):
        return self.properties.get(prop, HslValue(""))


class HslEvent:
    """HSL event - simulation stub."""
    def __init__(self):
        self.is_set = False

    def set_event(self):
        self.is_set = True

    def wait_event(self, timeout: int = -1):
        self.is_set = False


# ============================================================================
# Control flow exceptions
# ============================================================================

class BreakException(Exception):
    pass

class ReturnException(Exception):
    def __init__(self, value=None):
        self.value = value

class AbortException(Exception):
    pass

class ResumeNextException(Exception):
    pass

class HslRuntimeError(RuntimeError):
    """Runtime failure that can be handled by HSL onerror directives."""
    pass


# ============================================================================
# Execution Environment
# ============================================================================

@dataclass
class Scope:
    """A variable scope frame."""
    variables: dict[str, Any] = field(default_factory=dict)
    parent: Optional['Scope'] = None

    def get(self, name: str) -> Any:
        if name in self.variables:
            return self.variables[name]
        if self.parent:
            return self.parent.get(name)
        return None

    def set(self, name: str, value: Any):
        # Check if variable exists in this scope or parent
        if name in self.variables:
            self.variables[name] = value
            return
        if self.parent and self.parent.has(name):
            self.parent.set(name, value)
            return
        # New variable in current scope
        self.variables[name] = value

    def has(self, name: str) -> bool:
        if name in self.variables:
            return True
        if self.parent:
            return self.parent.has(name)
        return False

    def set_local(self, name: str, value: Any):
        """Set variable in this scope only (for declarations)."""
        self.variables[name] = value


class TraceOutput:
    """Collects trace/log output during simulation."""
    def __init__(self, verbose: bool = True):
        self.messages: list[str] = []
        self.verbose = verbose

    def trace(self, *args):
        msg = " ".join(str(a) for a in args)
        self.messages.append(msg)
        if self.verbose:
            print(f"[HSL TRACE] {msg}", flush=True)

    def warn(self, msg: str):
        self.messages.append(f"WARNING: {msg}")
        if self.verbose:
            print(f"[HSL WARN] {msg}", flush=True)

    def error(self, msg: str):
        self.messages.append(f"ERROR: {msg}")
        if self.verbose:
            print(f"[HSL ERROR] {msg}", flush=True)


# ============================================================================
# Interpreter
# ============================================================================

class Interpreter:
    """HSL AST Interpreter - SIMULATION ONLY."""

    def __init__(self, trace: Optional[TraceOutput] = None, strict: bool = False,
                 hamilton_dir: Optional[str] = None):
        self.trace = trace or TraceOutput()
        self.strict = strict
        self.global_scope = Scope()
        self.current_scope = self.global_scope
        self.functions: dict[str, FunctionDeclaration] = {}
        self.namespaces: dict[str, dict] = {}  # namespace -> {name: value}
        self.error_handler: Optional[str] = None
        self._last_error = {"id": 0, "description": "", "source": ""}
        self._lock_depth = 0
        self.execution_mode = "executor"
        self.hamilton_dir = hamilton_dir or os.environ.get(
            'HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'
        )
        self.source_file = ""
        self.call_stack: list[str] = []
        self._max_iterations = 100000  # Safety limit
        self._iteration_count = 0
        self._step_callback: Optional[Callable] = None  # For debugger breakpoints

        # Firmware-aware device simulator
        self.device_sim = DeviceSimulator(trace_fn=self.trace.trace)

        # Initialize built-in constants
        self._init_builtins()

    def _init_builtins(self):
        """Initialize built-in constants and functions."""
        builtins = {
            'hslTrue': HslValue(1),
            'hslFalse': HslValue(0),
            'hslInfinite': HslValue(2147483647),
            'hslInteger': HslValue("i"),
            'hslFloat': HslValue("f"),
            'hslString': HslValue("s"),
            'hslRead': HslValue("r"),
            'hslWrite': HslValue("w"),
            'hslAppend': HslValue("a"),
            'hslCSVDelimited': HslValue(","),
            'hslAsciiText': HslValue("\t"),
            'hslFirst': HslValue(0),
            'hslLast': HslValue(-1),
            'hslHide': HslValue(0),
            'hslSynchronous': HslValue(0),
            'hslAsynchronous': HslValue(1),
            'hslOK': HslValue(1),
            'hslOKOnly': HslValue(0),
            'hslOKCancel': HslValue(1),
            'hslYesNo': HslValue(4),
            'hslExclamation': HslValue(48),
            'hslError': HslValue(16),
            'hslInformation': HslValue(64),
            'hslDefButton1': HslValue(0),
            # Trace levels
            'TRACE_LEVEL_RELEASE': HslValue(1),
            'TRACE_LEVEL_DEBUG': HslValue(2),
            'TRACE_LEVEL_DETAIL': HslValue(3),
        }
        for name, value in builtins.items():
            self.global_scope.set_local(name, value)

    def set_step_callback(self, callback: Callable):
        """Set a callback for stepping/debugging. Called before each statement."""
        self._step_callback = callback

    def execute(self, program: Program):
        """Execute a parsed HSL program."""
        self.source_file = program.source_file
        self.trace.trace(f"=== HSL Simulation Runtime v0.1 ===")
        self.trace.trace(f"Source: {program.source_file}")
        self.trace.trace(f"SIMULATION MODE - No hardware interaction")

        # First pass: collect all function/method/namespace declarations
        self._collect_declarations(program.declarations)

        # Second pass: execute the main method
        main_name = None
        for candidate in ['main', '_Method::main', 'Main', '_Method::Main']:
            if candidate in self.functions:
                main_name = candidate
                break

        if main_name is None:
            for name, declaration in self.functions.items():
                if declaration.is_workflow and declaration.body is not None:
                    main_name = name
                    self.execution_mode = "scheduler"
                    break

        if main_name:
            self.trace.trace(f"Executing {main_name}()...")
            try:
                self._call_function(main_name, [])
            except AbortException:
                self.trace.trace("Method aborted.")
            except ReturnException:
                self.trace.trace("Method returned from main.")
            except Exception as e:
                self.trace.error(f"Runtime error: {e}")
                if self.strict:
                    raise
                traceback.print_exc()
        else:
            if self.strict:
                raise HslRuntimeError("No executable method main() or workflow was found")
            self.trace.warn("No main() method found. Executing declarations only.")
            for decl in program.declarations:
                try:
                    self._execute_node(decl)
                except (AbortException, ReturnException):
                    break

        self.trace.trace("=== Simulation complete ===")

        # Dump firmware simulation summary
        self.trace.trace(self.device_sim.get_state_summary())
        cmd_log = self.device_sim.get_command_log()
        if cmd_log:
            self.trace.trace("=== Firmware Command Log ===")
            for entry in cmd_log:
                self.trace.trace(entry)

    def _collect_declarations(self, declarations: list):
        """First pass: collect all function and namespace declarations."""
        for decl in declarations:
            if isinstance(decl, FunctionDeclaration):
                existing = self.functions.get(decl.name)
                if existing is None or decl.body is not None or existing.body is None:
                    self.functions[decl.name] = decl
            elif isinstance(decl, NamespaceDeclaration):
                self._collect_namespace(decl, "")
            elif isinstance(decl, VariableDeclaration):
                self._execute_var_decl(decl)
            elif isinstance(decl, Block):
                self._collect_declarations(decl.statements)

    def _collect_namespace(self, ns: NamespaceDeclaration, prefix: str):
        """Recursively collect namespace declarations."""
        full_name = f"{prefix}::{ns.name}" if prefix else ns.name
        if full_name not in self.namespaces:
            self.namespaces[full_name] = {}

        if ns.body and isinstance(ns.body, Block):
            for stmt in ns.body.statements:
                if isinstance(stmt, FunctionDeclaration):
                    qualified_name = f"{full_name}::{stmt.name}"
                    existing = self.functions.get(qualified_name)
                    if existing is None or stmt.body is not None or existing.body is None:
                        self.functions[qualified_name] = stmt
                        self.namespaces[full_name][stmt.name] = stmt
                elif isinstance(stmt, NamespaceDeclaration):
                    self._collect_namespace(stmt, full_name)
                elif isinstance(stmt, VariableDeclaration):
                    self._execute_var_decl(stmt, namespace=full_name)
                elif isinstance(stmt, Block):
                    # Nested block in namespace
                    for inner in stmt.statements:
                        if isinstance(inner, FunctionDeclaration):
                            qualified_name = f"{full_name}::{inner.name}"
                            self.functions[qualified_name] = inner
                        elif isinstance(inner, VariableDeclaration):
                            self._execute_var_decl(inner, namespace=full_name)

    # ========================================================================
    # Statement execution
    # ========================================================================

    def _execute_node(self, node: ASTNode) -> Any:
        """Execute an AST node."""
        if node is None:
            return None

        # Safety: iteration limit
        self._iteration_count += 1
        if self._iteration_count > self._max_iterations:
            raise RuntimeError(f"Maximum iteration count ({self._max_iterations}) exceeded. Possible infinite loop.")

        # Debugger step callback
        if self._step_callback and hasattr(node, 'line'):
            self._step_callback(node)

        # Dispatch by node type
        if isinstance(node, Block):
            return self._execute_block(node)
        if isinstance(node, ExpressionStatement):
            return self._eval_expr(node.expression)
        if isinstance(node, Assignment):
            return self._execute_assignment(node)
        if isinstance(node, VariableDeclaration):
            return self._execute_var_decl(node)
        if isinstance(node, IfStatement):
            return self._execute_if(node)
        if isinstance(node, ForLoop):
            return self._execute_for(node)
        if isinstance(node, WhileLoop):
            return self._execute_while(node)
        if isinstance(node, LoopStatement):
            return self._execute_loop(node)
        if isinstance(node, BreakStatement):
            raise BreakException()
        if isinstance(node, ReturnStatement):
            value = self._eval_expr(node.value) if node.value else None
            raise ReturnException(value)
        if isinstance(node, AbortStatement):
            self.trace.trace("ABORT called")
            raise AbortException()
        if isinstance(node, PauseStatement):
            self.trace.trace("PAUSE (simulation: continuing)")
            return None
        if isinstance(node, LockStatement):
            self._lock_depth += 1
            return None
        if isinstance(node, UnlockStatement):
            if self._lock_depth == 0:
                if self.strict:
                    raise HslRuntimeError("unlock; executed without a matching lock;")
                self.trace.warn("unlock; executed without a matching lock;")
            else:
                self._lock_depth -= 1
            return None
        if isinstance(node, RuntimeExecute):
            return self._execute_runtime_source(node)
        if isinstance(node, OnErrorGoto):
            self.error_handler = node.label if node.label != "0" else None
            return None
        if isinstance(node, ResumeNext):
            raise ResumeNextException()
        if isinstance(node, OnErrorResumeNext):
            self.error_handler = "__resume_next__"
            return None
        if isinstance(node, NamespaceDeclaration):
            self._collect_namespace(node, "")
            return None
        if isinstance(node, FunctionDeclaration):
            self.functions[node.name] = node
            return None
        if isinstance(node, SchedulerOnlyBlock):
            if self.execution_mode == "scheduler":
                return self._execute_node(node.body)
            return None
        if isinstance(node, ExecutorOnlyBlock):
            if self.execution_mode == "executor":
                return self._execute_node(node.body)
            return None
        if isinstance(node, SchedulerBlock):
            self.trace.trace(f"[SIM] {node.kind} block")
            return self._execute_node(node.body)
        if isinstance(node, Label):
            # Labels are jump targets and do not execute during fall-through.
            return None

        # Expression nodes
        return self._eval_expr(node)

    def _execute_block(self, block: Block) -> Any:
        """Execute a block of statements."""
        result = None
        labels = {
            stmt.name: stmt for stmt in block.statements if isinstance(stmt, Label)
        }
        index = 0
        while index < len(block.statements):
            stmt = block.statements[index]
            if isinstance(stmt, Label):
                index += 1
                continue
            try:
                result = self._execute_node(stmt)
            except HslRuntimeError as error:
                self._last_error["description"] = str(error)
                self._last_error["source"] = self.call_stack[-1] if self.call_stack else "main"
                if self.error_handler == "__resume_next__":
                    self.trace.warn(f"onerror resume next: {error}")
                    index += 1
                    continue
                handler = labels.get(self.error_handler or "")
                if handler is None:
                    raise
                try:
                    if handler.body is not None:
                        self._execute_node(handler.body)
                except ResumeNextException:
                    index += 1
                    continue
                return result
            index += 1
        return result

    def _execute_runtime_source(self, node: RuntimeExecute) -> None:
        """Implement HSL's << expression runtime parse/execute operator."""
        source_value = str(self._to_python(self._eval_expr(node.source)))
        candidate_paths = []
        if self.source_file:
            candidate_paths.append(os.path.join(os.path.dirname(self.source_file), source_value))
        candidate_paths.extend([
            os.path.join(self.hamilton_dir, 'Library', source_value),
            os.path.join(self.hamilton_dir, 'Methods', source_value),
            source_value,
        ])
        source_path = next((path for path in candidate_paths if os.path.isfile(path)), None)

        try:
            from .lexer import Lexer
            from .parser import Parser
            if source_path is not None:
                from .preprocessor import Preprocessor
                source_text = Preprocessor(
                    hamilton_dir=self.hamilton_dir, strict=self.strict
                ).preprocess_file(source_path)
                filename = source_path
            else:
                source_text = source_value
                filename = node.file or '<runtime>'

            parser = Parser(
                Lexer(source_text, filename).tokenize(),
                filename,
                strict=self.strict,
            )
            program = parser.parse()
            self._collect_declarations(program.declarations)
            for declaration in program.declarations:
                if not isinstance(declaration, (FunctionDeclaration, NamespaceDeclaration)):
                    self._execute_node(declaration)
        except Exception as error:
            if self.strict:
                raise HslRuntimeError(
                    f"Runtime HSL execution failed for {source_value!r}: {error}"
                ) from error
            self.trace.warn(f"Runtime HSL execution skipped: {error}")

    def _execute_var_decl(self, decl: VariableDeclaration, namespace: str = "") -> None:
        """Execute a variable declaration."""
        var_name = decl.name
        if namespace:
            var_name = f"{namespace}::{decl.name}"

        if decl.var_type == 'device':
            layout = ""
            device_name = ""
            if decl.device_args:
                args = [self._eval_expr(a) for a in decl.device_args]
                if len(args) >= 1:
                    layout = self._to_python(args[0])
                if len(args) >= 2:
                    device_name = self._to_python(args[1])
            val = HslDevice(name=decl.name, layout=str(layout), device_name=str(device_name))
            self.trace.trace(f"[SIM] Device declared: {decl.name} (layout={layout}, SIMULATION)")
        elif decl.is_array:
            val = HslArray()
            if decl.initializer is not None:
                size = int(self._to_python(self._eval_expr(decl.initializer)))
                if size < 0:
                    raise HslRuntimeError(f"Array size cannot be negative: {size}")
                val.set_size(size)
        elif decl.var_type == 'sequence':
            val = HslSequence(decl.name)
        elif decl.var_type == 'file':
            val = HslFile()
        elif decl.var_type == 'object':
            val = HslObject()
        elif decl.var_type == 'timer':
            val = HslTimer()
        elif decl.var_type == 'event':
            val = HslEvent()
        elif decl.var_type == 'dialog':
            val = HslDialog()
        elif decl.var_type == 'resource':
            val = HslObject()
            val.properties['constructor_args'] = [
                self._eval_expr(argument) for argument in decl.constructor_args
            ]
        elif decl.var_type == 'string':
            if decl.initializer is not None:
                val = HslValue(str(self._to_python(self._eval_expr(decl.initializer))))
            else:
                val = HslValue("")
        else:
            # variable
            if decl.initializer is not None:
                val = self._eval_expr(decl.initializer)
                if isinstance(val, HslValue):
                    # Always copy to avoid shared references with builtins
                    val = HslValue(val._value)
                else:
                    val = HslValue(val)
            else:
                val = HslValue(0)

        self.current_scope.set_local(var_name, val)

    def _execute_assignment(self, assign: Assignment) -> None:
        """Execute an assignment statement."""
        value = self._eval_expr(assign.value)
        self._assign_to(assign.target, value)

    def _assign_to(self, target: ASTNode, value: Any):
        """Assign a value to a target (identifier, array access, member access)."""
        if isinstance(target, Identifier):
            existing = self.current_scope.get(target.name)
            # If not found in current scope, try namespace-qualified name
            if existing is None and '::' not in target.name and self.call_stack:
                caller = self.call_stack[-1]
                if '::' in caller:
                    parts = caller.split('::')
                    for depth in range(len(parts) - 1, 0, -1):
                        ns_prefix = '::'.join(parts[:depth])
                        qualified = f"{ns_prefix}::{target.name}"
                        existing = self.current_scope.get(qualified)
                        if existing is None:
                            existing = self.global_scope.get(qualified)
                        if existing is not None:
                            # Found it -- assign to the qualified name
                            if isinstance(existing, HslValue):
                                existing.value = self._to_python(value)
                            else:
                                self.global_scope.set(qualified,
                                    value if isinstance(value, HslValue) else HslValue(value))
                            return
            if isinstance(existing, HslValue):
                existing.value = self._to_python(value)
            elif existing is None:
                self.current_scope.set(target.name, 
                    value if isinstance(value, HslValue) else HslValue(value))
            else:
                self.current_scope.set(target.name,
                    value if isinstance(value, HslValue) else HslValue(value))

        elif isinstance(target, ScopedName):
            name = "::".join(target.parts)
            existing = self.current_scope.get(name)
            if isinstance(existing, HslValue):
                existing.value = self._to_python(value)
            else:
                self.current_scope.set(name,
                    value if isinstance(value, HslValue) else HslValue(value))

        elif isinstance(target, ArrayAccess):
            array = self._eval_expr(target.array)
            index = self._eval_expr(target.index)
            idx = self._to_python(index)
            if isinstance(array, HslArray):
                array.set_at(int(idx), value)
            elif isinstance(array, list):
                array[int(idx)] = value

        elif isinstance(target, MemberAccess):
            obj = self._eval_expr(target.object)
            if isinstance(obj, HslDevice):
                # Setting device member
                pass
            elif isinstance(obj, HslObject):
                obj.properties[target.member] = value

    def _execute_if(self, node: IfStatement) -> None:
        """Execute an if/else statement."""
        cond = self._eval_expr(node.condition)
        if self._is_truthy(cond):
            self._execute_node(node.then_block)
        elif node.else_block:
            self._execute_node(node.else_block)

    def _execute_for(self, node: ForLoop) -> None:
        """Execute a for loop."""
        if node.init:
            self._execute_node(node.init) if isinstance(node.init, (VariableDeclaration, Assignment)) else self._eval_expr(node.init)

        iteration = 0
        max_iter = self._max_iterations
        while iteration < max_iter:
            iteration += 1
            if node.condition:
                cond = self._eval_expr(node.condition)
                if not self._is_truthy(cond):
                    break

            try:
                self._execute_node(node.body)
            except BreakException:
                break

            if node.update:
                self._eval_expr(node.update)

    def _execute_while(self, node: WhileLoop) -> None:
        """Execute a while loop."""
        iteration = 0
        max_iter = self._max_iterations
        while iteration < max_iter:
            iteration += 1
            cond = self._eval_expr(node.condition)
            if not self._is_truthy(cond):
                break

            try:
                self._execute_node(node.body)
            except BreakException:
                break

    def _execute_loop(self, node) -> None:
        """Execute a loop(N) statement -- repeat body N times."""
        count_val = self._eval_expr(node.count)
        count = int(self._to_python(count_val))
        for _ in range(count):
            try:
                self._execute_node(node.body)
            except BreakException:
                break

    # ========================================================================
    # Expression evaluation
    # ========================================================================

    def _eval_expr(self, node: ASTNode) -> Any:
        """Evaluate an expression node."""
        if node is None:
            return HslValue(0)

        if isinstance(node, IntegerLiteral):
            return HslValue(node.value)
        if isinstance(node, FloatLiteral):
            return HslValue(node.value)
        if isinstance(node, StringLiteral):
            return HslValue(node.value)
        if isinstance(node, BoolLiteral):
            return HslValue(1 if node.value else 0)

        if isinstance(node, Identifier):
            return self._resolve_identifier(node.name)

        if isinstance(node, ScopedName):
            return self._resolve_scoped_name(node.parts)

        if isinstance(node, BinaryOp):
            return self._eval_binary_op(node)

        if isinstance(node, UnaryOp):
            return self._eval_unary_op(node)

        if isinstance(node, Assignment):
            value = self._eval_expr(node.value)
            self._assign_to(node.target, value)
            return value

        if isinstance(node, FunctionCall):
            return self._eval_function_call(node)

        if isinstance(node, MethodCall):
            return self._eval_method_call(node)

        if isinstance(node, MemberAccess):
            return self._eval_member_access(node)

        if isinstance(node, ArrayAccess):
            array = self._eval_expr(node.array)
            index = self._eval_expr(node.index)
            idx = int(self._to_python(index))
            if isinstance(array, HslArray):
                return array.get_at(idx)
            return HslValue(0)

        if isinstance(node, ExpressionStatement):
            return self._eval_expr(node.expression)

        # Fallback
        return HslValue(0)

    def _resolve_identifier(self, name: str) -> Any:
        """Resolve a variable name to its value."""
        val = self.current_scope.get(name)
        if val is not None:
            return val

        # Try resolving relative to the current namespace context
        if '::' not in name and self.call_stack:
            caller = self.call_stack[-1]
            if '::' in caller:
                parts = caller.split('::')
                for depth in range(len(parts) - 1, 0, -1):
                    ns_prefix = '::'.join(parts[:depth])
                    qualified = f"{ns_prefix}::{name}"
                    val = self.current_scope.get(qualified)
                    if val is not None:
                        return val
                    # Also check global scope directly
                    val = self.global_scope.get(qualified)
                    if val is not None:
                        return val

        # Check namespaces
        for ns_name, ns_dict in self.namespaces.items():
            if name in ns_dict:
                return ns_dict[name]
        # Unknown variable - return 0 in simulation
        return HslValue(0)

    def _resolve_scoped_name(self, parts: list[str]) -> Any:
        """Resolve a scoped name like NS::SubNS::Name."""
        # Try full qualified name
        full_name = "::".join(parts)

        # Check if it's a function name (valid use of ::)
        if full_name in self.functions:
            return full_name  # Return the name for function call resolution

        # Global scope (::name) -- leading :: is valid for both variables and functions
        if parts and parts[0] == "":
            name = parts[1] if len(parts) > 1 else ""
            val = self.global_scope.get(name)
            if val is not None:
                return val

        # HSL does not support namespace-qualified variable access via ::
        # Only function calls can use Namespace::Function().
        # Warn but still resolve for simulation continuity.
        is_namespace_var = len(parts) >= 2 and parts[0] != ""
        if is_namespace_var:
            self.trace.warn(
                f"Namespace-qualified variable access '{full_name}' is not "
                f"supported in HSL. The '::' operator can only be used for "
                f"function calls. This will cause a compile error in VENUS."
            )

        val = self.current_scope.get(full_name)
        if val is not None:
            return val

        # Try as namespace member
        if len(parts) >= 2:
            ns = "::".join(parts[:-1])
            member = parts[-1]
            if ns in self.namespaces and member in self.namespaces[ns]:
                return self.namespaces[ns][member]

        # Default - return value of 0 in sim
        return HslValue(0)

    def _eval_binary_op(self, node: BinaryOp) -> HslValue:
        """Evaluate a binary operation."""
        left = self._eval_expr(node.left)
        op = node.operator

        # HSL logical operators short-circuit like their C counterparts.
        if op == '&&' and not self._is_truthy(left):
            return HslValue(0)
        if op == '||' and self._is_truthy(left):
            return HslValue(1)

        right = self._eval_expr(node.right)
        lv = self._to_python(left)
        rv = self._to_python(right)

        # String concatenation with +
        if op == '+' and (isinstance(lv, str) or isinstance(rv, str)):
            return HslValue(str(lv) + str(rv))

        # Arithmetic
        try:
            if op == '+':
                return HslValue(lv + rv)
            if op == '-':
                return HslValue(lv - rv)
            if op == '*':
                return HslValue(lv * rv)
            if op == '/':
                if rv == 0:
                    if self.strict:
                        raise HslRuntimeError("Division by zero")
                    self.trace.warn("Division by zero")
                    return HslValue(0)
                if isinstance(lv, int) and isinstance(rv, int):
                    return HslValue(lv // rv)
                return HslValue(lv / rv)
            if op == '%':
                if rv == 0:
                    if self.strict:
                        raise HslRuntimeError("Modulo by zero")
                    return HslValue(0)
                return HslValue(lv % rv)
            if op == '^':
                return HslValue(lv ** rv)
        except TypeError:
            return HslValue(0)

        # Comparison - coerce types for mixed comparisons
        try:
            if op == '==':
                return HslValue(1 if lv == rv else 0)
            if op == '!=':
                return HslValue(1 if lv != rv else 0)
            if op == '<':
                return HslValue(1 if lv < rv else 0)
            if op == '>':
                return HslValue(1 if lv > rv else 0)
            if op == '<=':
                return HslValue(1 if lv <= rv else 0)
            if op == '>=':
                return HslValue(1 if lv >= rv else 0)
        except TypeError:
            # Mixed type comparison (e.g., string vs int): coerce both to string
            try:
                sv_l, sv_r = str(lv), str(rv)
                if op == '==': return HslValue(1 if sv_l == sv_r else 0)
                if op == '!=': return HslValue(1 if sv_l != sv_r else 0)
                if op == '<': return HslValue(1 if sv_l < sv_r else 0)
                if op == '>': return HslValue(1 if sv_l > sv_r else 0)
                if op == '<=': return HslValue(1 if sv_l <= sv_r else 0)
                if op == '>=': return HslValue(1 if sv_l >= sv_r else 0)
            except Exception:
                return HslValue(0)

        # Bitwise OR
        if op == '|':
            try:
                return HslValue(int(lv) | int(rv))
            except (TypeError, ValueError):
                return HslValue(0)

        # Bitwise AND
        if op == '&':
            try:
                return HslValue(int(lv) & int(rv))
            except (TypeError, ValueError):
                return HslValue(0)

        # Logical (the right operand was evaluated only when required).
        if op == '&&':
            return HslValue(1 if (self._is_truthy(left) and self._is_truthy(right)) else 0)
        if op == '||':
            return HslValue(1 if (self._is_truthy(left) or self._is_truthy(right)) else 0)

        return HslValue(0)

    def _eval_unary_op(self, node: UnaryOp) -> Any:
        """Evaluate a unary operation."""
        if node.operator == '-':
            val = self._eval_expr(node.operand)
            pv = self._to_python(val)
            return HslValue(-pv)

        if node.operator == '!':
            val = self._eval_expr(node.operand)
            return HslValue(0 if self._is_truthy(val) else 1)

        if node.operator in ('++', '--'):
            # Get current value
            val = self._eval_expr(node.operand)
            pv = self._to_python(val)

            if isinstance(pv, (int, float)):
                if node.operator == '++':
                    new_val = pv + 1
                else:
                    new_val = pv - 1

                self._assign_to(node.operand, HslValue(new_val))

                if node.prefix:
                    return HslValue(new_val)
                else:
                    return HslValue(pv)

            # Sequence increment/decrement
            if isinstance(val, HslSequence):
                if node.operator == '++':
                    val.increment(1)
                else:
                    val.increment(-1)
                return val

        return HslValue(0)

    def _eval_function_call(self, node: FunctionCall) -> Any:
        """Evaluate a function call."""
        # Resolve function name
        func_name = ""
        if isinstance(node.function, Identifier):
            func_name = node.function.name
        elif isinstance(node.function, ScopedName):
            func_name = "::".join(node.function.parts)
        elif isinstance(node.function, str):
            func_name = node.function

        # Evaluate arguments
        args = [self._eval_expr(a) for a in node.arguments]

        # Check for built-in functions first
        result = self._call_builtin(func_name, args)
        if result is not None:
            return result

        # Try to find the user-defined function
        return self._call_function(func_name, args)

    def _eval_method_call(self, node: MethodCall) -> Any:
        """Evaluate a method call on an object."""
        if isinstance(node.object, Identifier) and node.object.name == 'err':
            args = [self._eval_expr(a) for a in node.arguments]
            method = node.method
            if method == 'Raise':
                values = [self._to_python(a) for a in args]
                error_id = values[0] if values else 0
                description = values[1] if len(values) > 1 else (values[0] if values else "HSL error")
                source = values[2] if len(values) > 2 else (self.call_stack[-1] if self.call_stack else "main")
                self._last_error = {
                    "id": error_id,
                    "description": str(description),
                    "source": str(source),
                }
                raise HslRuntimeError(str(description))
            if method == 'GetDescription':
                return HslValue(self._last_error["description"])
            if method == 'GetId':
                return HslValue(self._last_error["id"])
            if method == 'GetSource':
                return HslValue(self._last_error["source"])
            if method == 'Clear':
                self._last_error = {"id": 0, "description": "", "source": ""}
                return HslValue(0)

        obj = self._eval_expr(node.object)
        args = [self._eval_expr(a) for a in node.arguments]
        method = node.method

        # Array methods
        if isinstance(obj, HslArray):
            return self._call_array_method(obj, method, args)

        # Sequence methods
        if isinstance(obj, HslSequence):
            return self._call_sequence_method(obj, method, args)

        # String methods (HslValue with string type)
        if isinstance(obj, HslValue) and obj.hsl_type == HslType.STRING:
            return self._call_string_method(obj, method, args)

        # File methods
        if isinstance(obj, HslFile):
            return self._call_file_method(obj, method, args)

        # Dialog methods
        if isinstance(obj, HslDialog):
            return self._call_dialog_method(obj, method, args)

        # Object methods
        if isinstance(obj, HslObject):
            return self._call_object_method(obj, method, args)

        # Timer methods
        if isinstance(obj, HslTimer):
            return self._call_timer_method(obj, method, args)

        # Device methods - firmware-aware simulation
        if isinstance(obj, HslDevice):
            method_name = node.method
            step_guid = str(self._to_python(args[0])) if args else ""
            result = self.device_sim.execute_step(method_name, step_guid)
            if not result.success:
                self.trace.trace(
                    f"[SIM] Device step FAILED: {result.description}")
                for w in result.warnings:
                    self.trace.trace(f"[SIM] WARNING: {w}")
            return HslValue(1 if result.success else 0)

        # Generic: try calling as regular function
        if self.strict:
            raise HslRuntimeError(f"Unknown method: {method} on {type(obj).__name__}")
        self.trace.trace(f"[SIM] Unknown method: {method} on {type(obj).__name__}")
        return HslValue(0)

    def _eval_member_access(self, node: MemberAccess) -> Any:
        """Evaluate member access: obj.member"""
        obj = self._eval_expr(node.object)

        if isinstance(obj, HslDevice):
            if node.member not in obj.sequences:
                obj.sequences[node.member] = HslSequence(node.member)
            return obj.sequences[node.member]

        if isinstance(obj, HslSequence):
            # Sequence property access
            if node.member == 'current':
                return HslValue(obj.current_pos)

        if isinstance(obj, HslObject):
            return obj.properties.get(node.member, HslValue(0))

        return HslValue(0)

    # ========================================================================
    # Built-in function calls
    # ========================================================================

    def _call_builtin(self, name: str, args: list) -> Any:
        """Call a built-in HSL function. Returns None if not a built-in."""

        # Normalize scoped names - strip _Method:: prefix
        if name.startswith("_Method::"):
            name = name[len("_Method::"):]

        # Global Trace
        if name == 'Trace' or name == '::Trace':
            msg = " ".join(str(self._to_python(a)) for a in args)
            self.trace.trace(msg)
            return HslValue(0)

        if name == 'FormatTrace' or name == '::FormatTrace':
            msg = " ".join(str(self._to_python(a)) for a in args)
            self.trace.trace(msg)
            return HslValue(0)

        # Type conversion
        if name in ('IStr', '::IStr'):
            return HslValue(str(int(self._to_python(args[0]))) if args else "0")
        if name in ('FStr', '::FStr'):
            return HslValue(str(float(self._to_python(args[0]))) if args else "0.0")
        if name in ('IVal', '::IVal', 'IVal2', '::IVal2'):
            try:
                return HslValue(int(self._to_python(args[0])) if args else 0)
            except (ValueError, TypeError):
                return HslValue(0)
        if name in ('FVal', '::FVal', 'FVal2', '::FVal2'):
            try:
                return HslValue(float(self._to_python(args[0])) if args else 0.0)
            except (ValueError, TypeError):
                return HslValue(0.0)

        # Type checking
        if name in ('GetType', '::GetType'):
            if args:
                val = args[0]
                if isinstance(val, HslValue):
                    return HslValue(val.hsl_type)
            return HslValue(HslType.INTEGER)

        # String functions (also available as global funcs)
        if name in ('StrGetLength', '::StrGetLength'):
            return HslValue(len(str(self._to_python(args[0]))) if args else 0)
        if name in ('StrFind', '::StrFind'):
            if len(args) >= 2:
                s = str(self._to_python(args[0]))
                sub = str(self._to_python(args[1]))
                return HslValue(s.find(sub))
            return HslValue(-1)
        if name in ('StrLeft', '::StrLeft'):
            if len(args) >= 2:
                s = str(self._to_python(args[0]))
                n = int(self._to_python(args[1]))
                return HslValue(s[:n])
            return HslValue("")
        if name in ('StrRight', '::StrRight'):
            if len(args) >= 2:
                s = str(self._to_python(args[0]))
                n = int(self._to_python(args[1]))
                return HslValue(s[-n:] if n > 0 else "")
            return HslValue("")
        if name in ('StrMid', '::StrMid'):
            if len(args) >= 3:
                s = str(self._to_python(args[0]))
                start = int(self._to_python(args[1]))
                count = int(self._to_python(args[2]))
                return HslValue(s[start:start + count])
            return HslValue("")
        if name in ('StrMakeUpper', '::StrMakeUpper', 'StrMakeUpperCopy'):
            if args:
                s = str(self._to_python(args[0]))
                return HslValue(s.upper())
            return HslValue("")
        if name in ('StrMakeLower', '::StrMakeLower', 'StrMakeLowerCopy'):
            if args:
                s = str(self._to_python(args[0]))
                return HslValue(s.lower())
            return HslValue("")
        if name in ('StrConcat2', '::StrConcat2'):
            parts = [str(self._to_python(a)) for a in args[:2]]
            return HslValue("".join(parts))
        if name in ('StrConcat4', '::StrConcat4'):
            parts = [str(self._to_python(a)) for a in args[:4]]
            return HslValue("".join(parts))
        if name in ('StrConcat8', '::StrConcat8'):
            parts = [str(self._to_python(a)) for a in args[:8]]
            return HslValue("".join(parts))
        if name in ('StrConcat12', '::StrConcat12'):
            parts = [str(self._to_python(a)) for a in args[:12]]
            return HslValue("".join(parts))
        if name in ('StrReplace', '::StrReplace'):
            if len(args) >= 3:
                s = str(self._to_python(args[0]))
                old = str(self._to_python(args[1]))
                new = str(self._to_python(args[2]))
                return HslValue(s.replace(old, new))
            return HslValue("")
        if name in ('StrTrimLeft', '::StrTrimLeft'):
            if len(args) >= 2:
                s = str(self._to_python(args[0]))
                ch = str(self._to_python(args[1]))
                return HslValue(s.lstrip(ch))
            return HslValue("")
        if name in ('StrTrimRight', '::StrTrimRight'):
            if len(args) >= 2:
                s = str(self._to_python(args[0]))
                ch = str(self._to_python(args[1]))
                return HslValue(s.rstrip(ch))
            return HslValue("")
        if name in ('StrIsDigit', '::StrIsDigit'):
            if args:
                s = str(self._to_python(args[0]))
                return HslValue(1 if s.isdigit() else 0)
            return HslValue(0)
        if name in ('StrFillLeft', '::StrFillLeft'):
            if len(args) >= 3:
                s = str(self._to_python(args[0]))
                ch = str(self._to_python(args[1]))
                width = int(self._to_python(args[2]))
                return HslValue(s.rjust(width, ch[0] if ch else ' '))
            return HslValue("")
        if name in ('StrFillRight', '::StrFillRight'):
            if len(args) >= 3:
                s = str(self._to_python(args[0]))
                ch = str(self._to_python(args[1]))
                width = int(self._to_python(args[2]))
                return HslValue(s.ljust(width, ch[0] if ch else ' '))
            return HslValue("")
        if name in ('StrIStr', '::StrIStr'):
            return HslValue(str(int(self._to_python(args[0]))) if args else "0")
        if name in ('StrFStr', '::StrFStr'):
            return HslValue(str(float(self._to_python(args[0]))) if args else "0.0")
        if name in ('StrFStrEx', '::StrFStrEx'):
            if len(args) >= 3:
                val = float(self._to_python(args[0]))
                prec = int(self._to_python(args[2]))
                return HslValue(f"{val:.{prec}f}")
            return HslValue("0.0")
        if name in ('StrIVal', '::StrIVal'):
            try:
                return HslValue(int(self._to_python(args[0])) if args else 0)
            except (ValueError, TypeError):
                return HslValue(0)
        if name in ('StrFVal', '::StrFVal'):
            try:
                return HslValue(float(self._to_python(args[0])) if args else 0.0)
            except (ValueError, TypeError):
                return HslValue(0.0)
        if name in ('StrGetType', '::StrGetType'):
            if args:
                val = args[0]
                if isinstance(val, HslValue):
                    return HslValue(val.hsl_type)
            return HslValue(HslType.INTEGER)

        # Math functions
        if name in ('MthAbs', '::MthAbs'):
            return HslValue(abs(self._to_python(args[0])) if args else 0)
        if name in ('MthSqrt', '::MthSqrt'):
            return HslValue(math.sqrt(float(self._to_python(args[0]))) if args else 0.0)
        if name in ('MthPow', '::MthPow'):
            if len(args) >= 2:
                return HslValue(math.pow(float(self._to_python(args[0])),
                                         float(self._to_python(args[1]))))
            return HslValue(0.0)
        if name in ('MthMin', '::MthMin'):
            if len(args) >= 2:
                return HslValue(min(self._to_python(args[0]), self._to_python(args[1])))
            return HslValue(0)
        if name in ('MthMax', '::MthMax'):
            if len(args) >= 2:
                return HslValue(max(self._to_python(args[0]), self._to_python(args[1])))
            return HslValue(0)
        if name in ('MthRound', '::MthRound'):
            if len(args) >= 2:
                val = float(self._to_python(args[0]))
                digits = int(self._to_python(args[1]))
                return HslValue(round(val, digits))
            return HslValue(0.0)

        # System path functions
        if name in ('GetBinPath', '::GetBinPath', 'FilGetBinPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Bin'))
        if name in ('GetLibraryPath', '::GetLibraryPath', 'FilGetLibraryPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Library'))
        if name in ('GetMethodsPath', '::GetMethodsPath', 'FilGetMethodsPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Methods'))
        if name in ('GetLogFilesPath', '::GetLogFilesPath', 'FilGetLogFilesPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Logfiles'))
        if name in ('GetConfigPath', '::GetConfigPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Config'))
        if name in ('GetLabwarePath', '::GetLabwarePath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'Labware'))
        if name in ('GetSystemPath', '::GetSystemPath'):
            return HslValue(os.path.join(os.environ.get('HAMILTON_DIR', r'C:\Program Files (x86)\Hamilton'), 'System'))

        # File functions
        if name in ('GetFileName', '::GetFileName'):
            return HslValue(self.call_stack[-1] if self.call_stack else "<main>")
        if name in ('GetFunctionName', '::GetFunctionName'):
            return HslValue(self.call_stack[-1] if self.call_stack else "main")
        if name in ('GetMethodFileName', '::GetMethodFileName'):
            return HslValue("<simulation>")
        if name in ('GetLineNumber', '::GetLineNumber'):
            return HslValue(0)
        if name in ('SearchPath', '::SearchPath'):
            return HslValue(str(self._to_python(args[0])) if args else "")

        # I/O
        if name in ('InputBox', '::InputBox'):
            prompt = str(self._to_python(args[0])) if args else ""
            self.trace.trace(f"[SIM] InputBox: {prompt} -> returning empty string")
            return HslValue("")
        if name in ('MessageBox', '::MessageBox'):
            msg = str(self._to_python(args[0])) if args else ""
            self.trace.trace(f"[SIM] MessageBox: {msg}")
            return HslValue(1)  # OK

        # Shell
        if name in ('Shell', '::Shell'):
            cmd = str(self._to_python(args[0])) if args else ""
            self.trace.trace(f"[SIM] Shell (not executed in simulation): {cmd}")
            return HslValue(1)

        # Translate
        if name in ('Translate', '::Translate'):
            return args[0] if args else HslValue("")

        # GetUniqueRunId -- returns a 32-char hex string (no dashes)
        if name in ('GetUniqueRunId', '::GetUniqueRunId'):
            run_id = uuid.uuid4().hex
            return HslValue(run_id)

        # GetSimulationMode
        if name in ('GetSimulationMode', '::GetSimulationMode'):
            return HslValue(1)  # Always simulation

        # RegisterAbortHandler
        if name in ('RegisterAbortHandler', '::RegisterAbortHandler'):
            self.trace.trace(f"[SIM] RegisterAbortHandler: {self._to_python(args[0]) if args else 'none'}")
            return HslValue(0)

        # AddCheckSum
        if name in ('AddCheckSum', '::AddCheckSum'):
            return HslValue(0)

        # IsDBNull
        if name in ('IsDBNull', '::IsDBNull'):
            return HslValue(0)

        # Executor
        if name in ('GetExecutorObject', '::GetExecutorObject'):
            return HslObject()

        # PTL (Pipetting Transport Language) - simulation stubs
        if name.startswith('PTL::') or name.startswith('_Method::PTL::'):
            clean_name = name.split('::')[-1]
            self.trace.trace(f"[SIM] PTL::{clean_name}({', '.join(str(self._to_python(a)) for a in args)})")
            return HslValue(1)

        # TRACELEVEL functions
        if 'TRACELEVEL' in name or 'Trace_' in name or 'TraceAction' in name:
            parts_str = ", ".join(str(self._to_python(a)) for a in args)
            self.trace.trace(f"[TRACE] {name}: {parts_str}")
            return HslValue(1)

        # MECC functions
        if 'MECC::' in name:
            self.trace.trace(f"[SIM] {name}({', '.join(str(self._to_python(a)) for a in args)})")
            return HslValue(0)

        # ASWGLOBAL constants
        if name.startswith('ASWGLOBAL::'):
            parts = name.split('::')
            if 'BOOL' in parts:
                if 'TRUE' in parts:
                    return HslValue(1)
                if 'FALSE' in parts:
                    return HslValue(0)
            if 'DIALOG' in parts:
                if 'OK' in parts:
                    return HslValue(1)
                if 'CANCEL' in parts:
                    return HslValue(2)
                if 'YES' in parts:
                    return HslValue(6)
                if 'NO' in parts:
                    return HslValue(7)
            return HslValue(0)

        # Hamilton PDF Report Generator (simulation)
        if 'Hamilton_PDF_Report_Generator::' in name or 'Hamilton_PDF_Report_Generator' in name:
            method = name.split('::')[-1]
            self.trace.trace(f"[SIM] PDF Report: {method}({', '.join(str(self._to_python(a)) for a in args)})")
            return HslValue(1)  # Success

        # err object
        if name.startswith('err.') or name.startswith('err::'):
            method = name.split('.')[-1] if '.' in name else name.split('::')[-1]
            if method == 'Raise':
                msg = str(self._to_python(args[0])) if args else "Error"
                self.trace.error(f"err.Raise: {msg}")
                return HslValue(0)
            if method == 'GetDescription':
                return HslValue("Simulation error")
            if method == 'Clear':
                return HslValue(0)

        # Not a built-in
        return None

    def _call_function(self, name: str, args: list) -> Any:
        """Call a user-defined HSL function."""
        resolved_name = name
        # Try exact name
        func = self.functions.get(name)

        # Try with _Method:: prefix
        if func is None:
            candidate = f"_Method::{name}"
            func = self.functions.get(candidate)
            if func is not None:
                resolved_name = candidate

        # Try stripping _Method:: prefix
        if func is None and name.startswith("_Method::"):
            candidate = name[len("_Method::"):]
            func = self.functions.get(candidate)
            if func is not None:
                resolved_name = candidate

        # Try resolving relative to the current namespace context
        if func is None and '::' not in name and self.call_stack:
            caller = self.call_stack[-1]
            if '::' in caller:
                # Try each parent namespace level
                parts = caller.split('::')
                for depth in range(len(parts) - 1, 0, -1):
                    ns_prefix = '::'.join(parts[:depth])
                    candidate = f"{ns_prefix}::{name}"
                    func = self.functions.get(candidate)
                    if func is not None:
                        resolved_name = candidate
                        break

        # Try as built-in one more time
        if func is None:
            result = self._call_builtin(name, args)
            if result is not None:
                return result

        if func is None:
            if self.strict:
                raise HslRuntimeError(f"Unknown function: {name}")
            # Unknown function - log and return 0 in permissive simulation mode
            self.trace.trace(f"[SIM] Unknown function: {name}({', '.join(str(self._to_python(a)) for a in args)})")
            return HslValue(0)

        if func.body is None:
            if self.strict:
                raise HslRuntimeError(f"Function has no implementation: {name}")
            # Forward declaration or interface stub
            self.trace.trace(f"[SIM] Stub function: {name}()")
            return HslValue(0)

        if self.strict and len(args) != len(func.parameters):
            raise HslRuntimeError(
                f"Function {name} expects {len(func.parameters)} argument(s), got {len(args)}"
            )

        # Create new scope for function
        old_scope = self.current_scope
        old_error_handler = self.error_handler
        old_lock_depth = self._lock_depth
        self.current_scope = Scope(parent=self.global_scope)
        self.error_handler = None
        self.call_stack.append(resolved_name)
        if func.is_synchronized:
            self._lock_depth += 1

        # Bind parameters
        for i, param in enumerate(func.parameters):
            if i < len(args):
                val = args[i]
                if not isinstance(val, (HslValue, HslArray, HslSequence, HslDevice,
                                        HslFile, HslObject, HslTimer, HslEvent, HslDialog)):
                    val = HslValue(val)
                elif isinstance(val, HslValue) and not param.is_reference:
                    val = HslValue(val._value)
                self.current_scope.set_local(param.name, val)
            else:
                self.current_scope.set_local(param.name, HslValue(0))

        # Execute function body
        result = HslValue(0)
        try:
            try:
                self._execute_node(func.body)
            except ReturnException as e:
                if e.value is not None:
                    result = e.value
        finally:
            self.call_stack.pop()
            self.current_scope = old_scope
            self.error_handler = old_error_handler
            self._lock_depth = old_lock_depth
        return result

    # ========================================================================
    # Type method calls
    # ========================================================================

    def _call_array_method(self, arr: HslArray, method: str, args: list) -> Any:
        """Handle array method calls."""
        if method == 'SetSize':
            arr.set_size(int(self._to_python(args[0])) if args else 0)
            return HslValue(0)
        if method == 'GetSize':
            return HslValue(arr.get_size())
        if method == 'AddAsLast':
            if args:
                arr.add_as_last(args[0])
            return HslValue(0)
        if method == 'GetAt':
            idx = int(self._to_python(args[0])) if args else 0
            return arr.get_at(idx)
        if method == 'SetAt':
            if len(args) >= 2:
                idx = int(self._to_python(args[0]))
                arr.set_at(idx, args[1])
            return HslValue(0)
        if method == 'ElementAt':
            idx = int(self._to_python(args[0])) if args else 0
            return arr.element_at(idx)

        self.trace.trace(f"[SIM] Array.{method}()")
        return HslValue(0)

    def _call_sequence_method(self, seq: HslSequence, method: str, args: list) -> Any:
        """Handle sequence method calls."""
        if method == 'GetCurrentPosition':
            return HslValue(seq.get_current_position())
        if method == 'SetCurrentPosition':
            seq.set_current_position(int(self._to_python(args[0])) if args else 1)
            return HslValue(0)
        if method == 'GetCount':
            return HslValue(seq.get_count())
        if method == 'SetCount':
            seq.set_count(int(self._to_python(args[0])) if args else 0)
            return HslValue(0)
        if method == 'GetTotal':
            return HslValue(seq.get_total())
        if method == 'GetMax':
            return HslValue(seq.get_max())
        if method == 'SetMax':
            seq.set_max(int(self._to_python(args[0])) if args else 0)
            return HslValue(0)
        if method == 'GetName':
            return HslValue(seq.get_name())
        if method == 'Increment':
            seq.increment(int(self._to_python(args[0])) if args else 1)
            return HslValue(0)
        if method == 'GetLabwareId':
            return HslValue(seq.get_labware_id())
        if method == 'GetPositionId':
            return HslValue(seq.get_position_id())
        if method == 'Add':
            labware_id = str(self._to_python(args[0])) if args else ""
            position_id = str(self._to_python(args[1])) if len(args) > 1 else ""
            seq.add(labware_id, position_id)
            return HslValue(0)
        if method == 'SetUsedPositions':
            return HslValue(0)
        if method == 'GetUsedPositions':
            return HslValue(0)

        self.trace.trace(f"[SIM] Sequence.{method}()")
        return HslValue(0)

    def _call_string_method(self, val: HslValue, method: str, args: list) -> Any:
        """Handle string method calls."""
        s = val.to_string()

        if method == 'Find':
            sub = str(self._to_python(args[0])) if args else ""
            return HslValue(s.find(sub))
        if method == 'Left':
            n = int(self._to_python(args[0])) if args else 0
            return HslValue(s[:n])
        if method == 'Right':
            n = int(self._to_python(args[0])) if args else 0
            return HslValue(s[-n:] if n > 0 else "")
        if method == 'Mid':
            start = int(self._to_python(args[0])) if args else 0
            count = int(self._to_python(args[1])) if len(args) > 1 else len(s)
            return HslValue(s[start:start + count])
        if method == 'GetLength':
            return HslValue(len(s))
        if method == 'MakeUpper':
            val.value = s.upper()
            return HslValue(s.upper())
        if method == 'MakeLower':
            val.value = s.lower()
            return HslValue(s.lower())
        if method == 'SpanExcluding':
            chars = str(self._to_python(args[0])) if args else ""
            result = ""
            for ch in s:
                if ch in chars:
                    break
                result += ch
            return HslValue(result)
        if method == 'Compare':
            other = str(self._to_python(args[0])) if args else ""
            if s < other:
                return HslValue(-1)
            elif s > other:
                return HslValue(1)
            return HslValue(0)

        self.trace.trace(f"[SIM] String.{method}()")
        return HslValue("")

    def _call_file_method(self, f: HslFile, method: str, args: list) -> Any:
        """Handle file method calls."""
        if method == 'Open':
            path = str(self._to_python(args[0])) if args else ""
            mode = str(self._to_python(args[1])) if len(args) > 1 else "hslRead"
            f.open(path, mode)
            self.trace.trace(f"[SIM] File.Open({path}, {mode})")
            return HslValue(0)
        if method == 'Close':
            f.close()
            return HslValue(0)
        if method == 'Eof':
            return HslValue(1 if f.eof() else 0)
        if method in ('AddField', 'RemoveFields', 'SetDelimiter',
                       'ReadRecord', 'WriteRecord', 'ReadString', 'WriteString',
                       'Seek', 'UpdateRecord'):
            self.trace.trace(f"[SIM] File.{method}()")
            return HslValue(0)

        return HslValue(0)

    def _call_dialog_method(self, dlg: HslDialog, method: str, args: list) -> Any:
        """Handle dialog method calls."""
        if method == 'InitCustomDialog':
            dialog_id = str(self._to_python(args[0])) if args else ""
            dlg.init_custom_dialog(dialog_id)
            self.trace.trace(f"[SIM] Dialog.InitCustomDialog({dialog_id})")
            return HslValue(0)
        if method == 'SetCustomDialogProperty':
            if len(args) >= 2:
                prop = str(self._to_python(args[0]))
                val = args[1]
                dlg.set_custom_dialog_property(prop, val)
            return HslValue(0)
        if method == 'SetCustomDialogArrayProperty':
            if len(args) >= 2:
                prop = str(self._to_python(args[0]))
                arr = args[1]
                dlg.set_custom_dialog_array_property(prop, arr)
            return HslValue(0)
        if method == 'ShowCustomDialog':
            self.trace.trace(f"[SIM] Dialog.ShowCustomDialog() -> auto-OK")
            return HslValue(0)
        if method == 'GetCustomDialogProperty':
            prop = str(self._to_python(args[0])) if args else ""
            result = dlg.get_custom_dialog_property(prop)
            return result if result is not None else HslValue("")
        if method == 'SetOutput':
            msg = str(self._to_python(args[0])) if args else ""
            self.trace.trace(f"[SIM] Dialog output: {msg}")
            return HslValue(0)
        if method == 'ShowOutput':
            self.trace.trace(f"[SIM] Dialog.ShowOutput()")
            return HslValue(1)

        return HslValue(0)

    def _call_object_method(self, obj: HslObject, method: str, args: list) -> Any:
        """Handle COM object method calls."""
        if method == 'CreateObject':
            prog_id = str(self._to_python(args[0])) if args else ""
            obj.create_object(prog_id)
            if obj._com_impl is not None and not isinstance(obj._com_impl, GenericComObject):
                self.trace.trace(f"[COM] Object.CreateObject({prog_id}) -- using Python implementation")
            else:
                self.trace.trace(f"[SIM] Object.CreateObject({prog_id}) -- generic stub")
            return HslValue(1)
        if method == 'GetObject':
            return HslObject()

        # Try calling the method on the real COM implementation
        py_args = [self._to_python(a) for a in args]
        if obj._com_impl is not None:
            func = getattr(obj._com_impl, method, None)
            if func and callable(func):
                try:
                    result = func(*py_args)
                    self.trace.trace(f"[COM] {obj.prog_id}.{method}({', '.join(str(a) for a in py_args)})")
                    if result is None:
                        return HslValue(0)
                    elif isinstance(result, str):
                        return HslValue(result)
                    elif isinstance(result, (int, float)):
                        return HslValue(result)
                    return HslValue(str(result))
                except Exception as e:
                    self.trace.error(f"[COM] {obj.prog_id}.{method}() failed: {e}")
                    return HslValue(0)

        self.trace.trace(f"[SIM] Object.{method}({', '.join(str(a) for a in py_args)})")
        return HslValue(0)

    def _call_timer_method(self, timer: HslTimer, method: str, args: list) -> Any:
        """Handle timer method calls."""
        if method == 'SetTimer':
            seconds = float(self._to_python(args[0])) if args else 0.0
            timer.set_timer(seconds)
            self.trace.trace(f"[SIM] Timer.SetTimer({seconds}s)")
            return HslValue(0)
        if method == 'WaitTimer':
            self.trace.trace(f"[SIM] Timer.WaitTimer() -> skip in simulation")
            return HslValue(0)
        if method in ('SetTimerViewName', 'ReadElapsed', 'Restart', 'Stop'):
            return HslValue(0)

        return HslValue(0)

    # ========================================================================
    # Helpers
    # ========================================================================

    def _to_python(self, value: Any) -> Any:
        """Convert an HSL value to a Python value."""
        if isinstance(value, HslValue):
            return value.value
        if isinstance(value, (int, float, str, bool)):
            return value
        if isinstance(value, HslArray):
            return value
        if value is None:
            return 0
        return value

    def _is_truthy(self, value: Any) -> bool:
        """Check if a value is truthy in HSL."""
        if isinstance(value, HslValue):
            return value.to_bool()
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return len(value) > 0
        return value is not None
