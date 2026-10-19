"""
HSL Abstract Syntax Tree (AST) Node Definitions
=================================================
Defines all AST node types for the HSL language.

SIMULATION ONLY - No hardware interaction.
"""

from dataclasses import dataclass, field
from typing import Optional, Any


@dataclass
class ASTNode:
    """Base class for all AST nodes."""
    line: int = 0
    column: int = 0
    file: str = ""


# ============================================================================
# Expressions
# ============================================================================

@dataclass
class IntegerLiteral(ASTNode):
    value: int = 0

@dataclass
class FloatLiteral(ASTNode):
    value: float = 0.0

@dataclass
class StringLiteral(ASTNode):
    value: str = ""

@dataclass
class BoolLiteral(ASTNode):
    value: bool = False

@dataclass
class Identifier(ASTNode):
    name: str = ""

@dataclass
class ScopedName(ASTNode):
    """Namespace-qualified name like MECC::IDS::stepName or ::RegisterAbortHandler."""
    parts: list[str] = field(default_factory=list)

@dataclass
class ArrayAccess(ASTNode):
    """Array subscript: arr[index]"""
    array: Any = None   # Expression
    index: Any = None   # Expression

@dataclass
class MemberAccess(ASTNode):
    """Dot access: obj.member"""
    object: Any = None   # Expression
    member: str = ""

@dataclass
class FunctionCall(ASTNode):
    """Function/method call: func(args)"""
    function: Any = None  # Expression (Identifier, ScopedName, or MemberAccess)
    arguments: list = field(default_factory=list)

@dataclass
class MethodCall(ASTNode):
    """Method call on object: obj.method(args)"""
    object: Any = None    # Expression
    method: str = ""
    arguments: list = field(default_factory=list)

@dataclass
class UnaryOp(ASTNode):
    """Unary operation: -x, !x, x++, x--"""
    operator: str = ""
    operand: Any = None
    prefix: bool = True  # prefix vs postfix

@dataclass
class BinaryOp(ASTNode):
    """Binary operation: x + y, x == y, etc."""
    operator: str = ""
    left: Any = None
    right: Any = None

@dataclass
class Assignment(ASTNode):
    """Assignment: target = value"""
    target: Any = None    # Identifier, ArrayAccess, MemberAccess
    value: Any = None     # Expression


# ============================================================================
# Statements
# ============================================================================

@dataclass
class ExpressionStatement(ASTNode):
    """Statement consisting of a single expression."""
    expression: Any = None

@dataclass
class Block(ASTNode):
    """Block of statements: { stmt; stmt; }"""
    statements: list = field(default_factory=list)

@dataclass
class IfStatement(ASTNode):
    """if (cond) { ... } else { ... }"""
    condition: Any = None
    then_block: Any = None
    else_block: Any = None  # Optional

@dataclass
class ForLoop(ASTNode):
    """for (init; cond; update) { body }"""
    init: Any = None
    condition: Any = None
    update: Any = None
    body: Any = None

@dataclass
class WhileLoop(ASTNode):
    """while (cond) { body }"""
    condition: Any = None
    body: Any = None

@dataclass
class LoopStatement(ASTNode):
    """loop(count) { body } - repeat body count times."""
    count: Any = None  # Expression for iteration count
    body: Any = None

@dataclass
class BreakStatement(ASTNode):
    pass

@dataclass
class LockStatement(ASTNode):
    """lock; -- begin a non-interruptible execution region."""
    pass

@dataclass
class UnlockStatement(ASTNode):
    """unlock; -- end a non-interruptible execution region."""
    pass

@dataclass
class RuntimeExecute(ASTNode):
    """<< expression; -- parse and execute HSL source at runtime."""
    source: Any = None

@dataclass
class ReturnStatement(ASTNode):
    """return or return(value)"""
    value: Any = None  # Optional expression

@dataclass
class AbortStatement(ASTNode):
    pass

@dataclass
class PauseStatement(ASTNode):
    pass

@dataclass
class OnErrorGoto(ASTNode):
    """onerror goto label / onerror goto 0"""
    label: str = ""  # "" or "0" means disable error handler

@dataclass
class ResumeNext(ASTNode):
    """resume next"""
    pass

@dataclass
class OnErrorResumeNext(ASTNode):
    """onerror resume next -- suppress the next runtime error."""
    pass

@dataclass
class Label(ASTNode):
    """Label: { statements }"""
    name: str = ""
    body: Any = None


# ============================================================================
# Declarations
# ============================================================================

@dataclass
class VariableDeclaration(ASTNode):
    """variable name; variable name(init); variable name[];"""
    name: str = ""
    var_type: str = "variable"  # variable, string, sequence, device, file, object, timer, event, dialog
    is_array: bool = False
    initializer: Any = None
    is_private: bool = False
    is_static: bool = False
    is_global: bool = False
    is_const: bool = False
    is_reference: bool = False
    # For device: (layout_file, device_name, flag)
    device_args: list = field(default_factory=list)
    constructor_args: list = field(default_factory=list)

@dataclass
class Parameter(ASTNode):
    """Function parameter."""
    name: str = ""
    param_type: str = "variable"
    is_reference: bool = False
    is_array: bool = False

@dataclass
class FunctionDeclaration(ASTNode):
    """function name(params) return_type { body }"""
    name: str = ""
    parameters: list[Parameter] = field(default_factory=list)
    return_type: str = "void"
    body: Any = None  # Block, or None for forward declarations
    is_private: bool = False
    is_static: bool = False
    is_synchronized: bool = False
    is_method: bool = False  # True for 'method' keyword (entry point)
    is_workflow: bool = False

@dataclass
class NamespaceDeclaration(ASTNode):
    """namespace Name { ... }"""
    name: str = ""
    body: Any = None  # Block

@dataclass
class SchedulerOnlyBlock(ASTNode):
    """scheduleronly { ... }"""
    body: Any = None

@dataclass
class ExecutorOnlyBlock(ASTNode):
    """executoronly { ... }"""
    body: Any = None

@dataclass
class SchedulerBlock(ASTNode):
    """activity(...) { ... } or actionblock(...) { ... }."""
    kind: str = ""
    arguments: list = field(default_factory=list)
    body: Any = None


# ============================================================================
# Top-level
# ============================================================================

@dataclass
class Program(ASTNode):
    """Root node of the AST."""
    declarations: list = field(default_factory=list)
    source_file: str = ""
