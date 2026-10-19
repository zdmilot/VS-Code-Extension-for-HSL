# Compiler Architecture

> Complete architectural documentation of the HSL Debugger simulation runtime pipeline.

---

## Table of Contents

1. [Pipeline Overview](#pipeline-overview)
2. [Data Flow](#data-flow)
3. [Phase 1: Preprocessor](#phase-1-preprocessor)
4. [Phase 2: Lexer](#phase-2-lexer)
5. [Phase 3: Parser](#phase-3-parser)
6. [Phase 4: Interpreter](#phase-4-interpreter)
7. [Design Decisions](#design-decisions)
8. [Error Handling Strategy](#error-handling-strategy)
9. [Debugger Integration](#debugger-integration)

---

## Pipeline Overview

The HSL Debugger processes HSL source files through a four-phase pipeline:

```
                     ┌──────────────┐
                     │  HSL Source   │
                     │    (.hsl)     │
                     └──────┬───────┘
                            │
                    ┌───────▼────────┐
                    │  PREPROCESSOR  │ Phase 1
                    │                │
                    │ • #include     │
                    │ • #define      │
                    │ • #ifdef       │
                    │ • #pragma once │
                    └───────┬────────┘
                            │ Flattened source string
                    ┌───────▼────────┐
                    │     LEXER      │ Phase 2
                    │                │
                    │ • Tokenize     │
                    │ • Classify     │
                    │ • Filter       │
                    └───────┬────────┘
                            │ List[Token]
                    ┌───────▼────────┐
                    │     PARSER     │ Phase 3
                    │                │
                    │ • Recursive    │
                    │   descent      │
                    │ • Precedence   │
                    │   climbing     │
                    │ • Error        │
                    │   recovery     │
                    └───────┬────────┘
                            │ Program (AST)
                    ┌───────▼────────┐
                    │  INTERPRETER   │ Phase 4
                    │                │
                    │ • Two-pass     │
                    │   execution    │
                    │ • Scope chain  │
                    │ • Type system  │
                    │ • Built-ins    │
                    │ • Stubs        │
                    └───────┬────────┘
                            │
                    ┌───────▼────────┐
                    │  Trace Output  │
                    │  + Simulation  │
                    │    Results     │
                    └────────────────┘
```

Each phase is implemented as a self-contained Python module with well-defined inputs and outputs. Phases communicate through intermediate data structures -- there are no global variables or shared mutable state between phases.

---

## Data Flow

### Inter-Phase Communication

```
Phase 1 (Preprocessor)
  Input:  File path (str), Hamilton search paths
  Output: Flattened source code (str), source map (dict)
  
Phase 2 (Lexer)
  Input:  Source code (str), file name (str)
  Output: List[Token] (filtered: no comments, markers, checksums)
  
Phase 3 (Parser)
  Input:  List[Token]
  Output: Program AST node (tree of ASTNode subclasses)
  Errors: List of ParseError (non-fatal warnings)
  
Phase 4 (Interpreter)
  Input:  Program AST, TraceOutput instance, options
  Output: TraceOutput (list of messages), execution result
```

### Key Data Structures

| Structure | Module | Purpose |
|-----------|--------|---------|
| `Token(type, value, line, column, file)` | lexer.py | Single lexical token |
| `ASTNode` (25+ subclasses) | ast_nodes.py | Abstract syntax tree nodes |
| `HslValue(value)` | interpreter.py | Runtime variant value |
| `HslArray`, `HslSequence`, `HslDevice`, ... | interpreter.py | Runtime type instances |
| `Scope(parent)` | interpreter.py | Variable scope with parent chain |
| `TraceOutput()` | interpreter.py | Collected trace messages |

---

## Phase 1: Preprocessor

**Module**: [preprocessor.py](PREPROCESSOR.md) (305 lines)

### Responsibility

Transform raw HSL source files into a single flattened source string by:

1. Resolving `#include` directives (recursive, with search paths)
2. Substituting `#define` macros (whole-word replacement)
3. Evaluating conditional compilation (`#ifdef`, `#ifndef`, `#else`, `#endif`)
4. Handling `#pragma once` (include-once guards)

### Key Design

- **HSL_RUNTIME always defined**: The preprocessor sets `HSL_RUNTIME = 1`, ensuring that the runtime implementation sections (inside `#ifdef HSL_RUNTIME`) are always included. This mirrors the Hamilton executor's behavior.
- **Search path resolution**: Includes are searched relative to the current file, then in `Hamilton\Library\`, then `Hamilton\Methods\`, following the Hamilton search order.
- **Maximum include depth**: 50 levels (prevents infinite recursion from circular includes).
- **Source map**: Tracks which lines came from which source file (for error reporting).

### Flow

```
Input HSL file
  → Read file content
  → For each line:
      If #include → resolve path, recursively preprocess included file, insert result
      If #define  → store macro name → value mapping
      If #ifdef   → push condition stack; if false, skip lines until #else/#endif
      If #ifndef  → push inverted condition stack
      If #else    → invert top of condition stack
      If #endif   → pop condition stack
      If #pragma once → mark file; skip if already included
      Otherwise   → substitute macros, emit line
  → Return flattened source
```

---

## Phase 2: Lexer

**Module**: [lexer.py](LEXER.md) (416 lines)

### Responsibility

Convert the flattened source string into a list of classified tokens, filtering out comments, editor markers, and checksum lines.

### Token Categories

| Category | Examples | Count |
|----------|---------|-------|
| Keywords | `function`, `method`, `variable`, `if`, `while`, `namespace`, ... | 30+ |
| Operators | `+`, `-`, `*`, `/`, `==`, `!=`, `&&`, `\|\|`, `!`, `++`, `--`, ... | 25+ |
| Delimiters | `(`, `)`, `{`, `}`, `[`, `]`, `;`, `,`, `.` | 10 |
| Literals | `42`, `3.14`, `"hello"`, `0xFF` | 4 types |
| Identifiers | `myVar`, `ML_STAR`, `_Method` | ∞ |
| Special | `<<` (runtime include), `::` (scope), `&` (reference) | 5 |

### Key Design

- **Greedy tokenization**: Longer matches take priority (e.g., `==` over `=`, `&&` over `&`)
- **Contextual detection**: The lexer identifies editor markers (`// {{{ ...`) and checksum lines (`// $$...$$`) during line comment scanning and classifies them as separate token types
- **Escape handling**: String literals support `\n`, `\t`, `\\`, `\"`
- **Number formats**: Decimal integers, hexadecimal (`0x...`), floats, scientific notation (`1e-3`)
- **Filtering**: Comments, editor markers, and checksums are excluded from the output token list

### Flow

```
Source string
  → Initialize position (line 1, column 1)
  → While not at end:
      Skip whitespace
      Match next token:
        '/' → line comment (//) or block comment (/* */)
        '"' → string literal (with escapes)
        digit → number (int, hex, float, scientific)
        letter/_ → identifier or keyword (lookup in keyword table)
        operator chars → operator token (greedy match)
        delimiter chars → delimiter token
      → Append Token(type, value, line, column, file)
  → Append EOF token
  → Filter out COMMENT, EDITOR_MARKER, CHECKSUM tokens
  → Return filtered list
```

---

## Phase 3: Parser

**Module**: [parser.py](PARSER.md) (938 lines)

### Responsibility

Build an Abstract Syntax Tree (AST) from the token stream using recursive descent parsing with precedence climbing for expressions.

### Parsing Strategy

1. **Recursive descent** for statements and declarations
2. **Precedence climbing** (Pratt-style) for expressions
3. **Error recovery** to continue parsing after syntax errors

### Grammar Summary

```
Program        → Declaration*
Declaration    → NamespaceDecl | FunctionDecl | MethodDecl | VarDecl | Statement
NamespaceDecl  → 'namespace' IDENT '{' Declaration* '}'
FunctionDecl   → ['static'|'private'] 'function' IDENT '(' Params ')' [ReturnType] Block
MethodDecl     → 'method' IDENT '(' Params ')' [ReturnType] Block
VarDecl        → TypeKeyword IDENT ['(' Expr ')'] ['[' [Expr] ']'] [',' ...] ';'
Block          → '{' Statement* '}'
Statement      → IfStmt | ForStmt | WhileStmt | ReturnStmt | BreakStmt
               | ContinueStmt | AbortStmt | PauseStmt | OnErrorStmt
               | ResumeNext | Label ':' | ExpressionStmt | Block
               | SchedulerOnlyBlock | ExecutorOnlyBlock
```

### Expression Precedence (implemented)

```
Assignment     (lowest)  =
LogicalOr               ||
LogicalAnd              &&
BitwiseOr               |
Equality                ==  !=
Comparison              <  <=  >  >=
Additive                +  -
Multiplicative          *  /  %
Unary          (prefix)  -  !  ++  --
Postfix        (highest) ()  []  .  ::  ++  --
```

### Error Recovery

The parser uses two recovery strategies:

1. **Top-level recovery** (`_recover`): On error during a top-level declaration, consumes tokens until a closing `}` is found, then continues parsing the next declaration.
2. **Block-level recovery** (`_recover_in_block`): On error inside a block, consumes tokens until a statement boundary (`;`, `}`, or closing keywords) without consuming the `}` itself, allowing the block to continue parsing.

This approach allows the parser to produce a partial AST even when there are syntax errors, enabling the interpreter to execute the valid portions of the program.

### Special Cases

| Feature | Handling |
|---------|----------|
| Comma-separated declarations | `variable a, b(1), c;` parsed as multiple `VariableDeclaration` nodes |
| Device constructor arguments | `device d("layout.lay", "name", hslTrue);` parsed with positional args |
| Assignment as expression | `for (i = 0; ...)` -- assignment returns the assigned value |
| Runtime includes | `<< "file.hsl"` parsed as a special include directive |
| Backslash skipping | `\` at certain positions is consumed and skipped (path handling) |
| Labels | `ErrorHandler:` -- identifier followed by `:` outside `?:` context |

---

## Phase 4: Interpreter

**Module**: [interpreter.py](INTERPRETER.md) (1,666 lines)

### Responsibility

Execute the AST in simulation mode, providing:

1. A variant type system with type coercion
2. Scope-based variable resolution
3. ~120+ built-in function implementations
4. Simulation stubs for all automation types
5. Error handling via `onerror goto` / `resume next`

### Execution Model

The interpreter uses a **two-pass execution** model:

```
Pass 1: Declaration Collection
  → Walk the AST top-down
  → Register all function/method declarations in the functions dict
  → Register namespace contents in the namespaces dict
  → Process global variable declarations

Pass 2: Execution
  → Search for entry point: main / Main / _Method::main / _Method::Main
  → Call the entry function
  → Execute statements in order
  → Handle control flow (if/for/while/loop/break/return/abort)
  → Dispatch function calls (built-in → builtin handler, user → AST walk)
  → Evaluate expressions with type coercion
```

### Type System

The core runtime type is `HslValue`, a variant that wraps Python values:

```python
HslValue(42)         # integer
HslValue(3.14)       # float
HslValue("hello")    # string
```

Type coercion methods:
- `to_int()` -- converts to integer (truncates floats, parses strings)
- `to_float()` -- converts to float (parses strings)
- `to_string()` -- converts to string representation
- `to_bool()` -- falsy: `0`, `0.0`, `""`, `None`; everything else is truthy

### Scope Chain

Variable lookup follows a scope chain from innermost to outermost:

```
Function local scope
  → Enclosing function scope (if nested)
    → Namespace scope
      → Global scope
```

Each scope is a `Scope(parent)` object with a `variables` dictionary. Variable resolution calls `scope.get(name)`, which walks the parent chain.

### Simulation Stubs

Every automation type has a simulation stub class:

| HSL Type | Python Class | Behavior |
|----------|-------------|----------|
| `device` | `HslDevice` | `_is_simulation = True`; logs layout; dynamic sequence properties |
| `sequence` | `HslSequence` | Tracks position/count/max; `Increment()` / `GetLabwareId()` return stubs |
| `file` | `HslFile` | File reads work; writes are stubbed |
| `object` | `HslObject` | Property bag; `CreateObject()` logs ProgID |
| `timer` | `HslTimer` | `SetTimer()` / `ReadElapsed()` -- no real delay |
| `event` | `HslEvent` | Stub; operations are no-ops |
| `dialog` | `HslDialog` | Property tracking; `ShowCustomDialog()` returns OK |

### Control Flow

Control flow is implemented via Python exceptions:

| HSL Statement | Python Exception |
|---------------|-----------------|
| `break` | `BreakException` |
| `return(value)` | `ReturnException(value)` |
| `abort` | `AbortException` |

Loop constructs (`for`, `while`) catch `BreakException`. Function calls catch `ReturnException` to extract the return value.

### Safety Limits

- **Max iterations**: 100,000 per loop (configurable via `--max-iterations`)
- **Step callback**: Optional `_step_callback` called before each node execution (for debugger integration)

---

## Design Decisions

### Why an Interpreter (Not a Compiler)?

1. **Simulation fidelity**: An interpreter can stub every hardware call at the function level without generating intermediate code
2. **Debugger integration**: Step-by-step execution with breakpoints is natural in an interpreter
3. **Selectable validation**: permissive mode can stub unknown external calls, while strict mode reports them as runtime errors
4. **Simplicity**: No code generation target, no linking, no runtime library

### Why Recursive Descent?

1. **HSL grammar is LL(1)-ish**: Most constructs can be parsed with one token of lookahead
2. **Error recovery**: Recursive descent makes it easy to skip tokens and resume parsing at known synchronization points
3. **Extensibility**: Adding new statement types is straightforward

### Why Python?

1. **Rapid prototyping**: Dynamic typing and rich standard library accelerate development
2. **Cross-platform potential**: Python runs on Windows (the Hamilton platform) and can be packaged
3. **VS Code integration**: Python is well-supported in the VS Code extension ecosystem

### Why Always Define HSL_RUNTIME?

The Hamilton runtime always defines `HSL_RUNTIME` when executing methods. Libraries use `#ifdef HSL_RUNTIME` to gate their full implementations. By always defining it in our preprocessor, we ensure that:

1. Library implementations (not just edit-time stubs) are included
2. The simulation sees the same code paths as the real executor
3. The full function bodies are available for AST construction

---

## Error Handling Strategy

### Preprocessor Errors

- Missing include files: Warning logged, include skipped
- Malformed directives: Warning logged, line skipped

### Lexer Errors

- Unterminated strings: `LexerError` raised
- Invalid characters: Skipped with warning

### Parser Errors

- Syntax errors: `ParseError` caught, error logged, recovery attempted
- The parser collects errors into a list and returns a partial AST
- Typical parse error count: ~100-123 on large Hamilton library files (from unsupported constructs like `^`, `struct`, `loop`)

### Interpreter Errors

- Unknown functions: Logged as warning, return `HslValue(0)`
- Type mismatches: Caught per-operation, coerced when possible
- `onerror goto`: Sets error handler label in current scope
- `onerror resume next`: Errors are suppressed
- `AbortException`: Propagates to top level, terminates execution

---

## Debugger Integration

The interpreter supports a step callback mechanism for future VS Code debugger integration:

```python
def my_step_callback(node, interpreter):
    """Called before each AST node is executed."""
    print(f"Executing {type(node).__name__} at line {node.line}")

interp = Interpreter(ast, trace)
interp._step_callback = my_step_callback
interp.execute()
```

This enables:
- **Breakpoints**: Check if the current node's line matches a breakpoint
- **Step-over / step-into**: Control execution granularity
- **Variable inspection**: Read `interpreter.current_scope.variables`
- **Call stack viewing**: Read `interpreter.call_stack`

---

## Performance Characteristics

| Metric | TraceLevel Demo | CustomDialogs |
|--------|----------------|---------------|
| Preprocessing | ~0.1-0.3s | ~0.1-0.3s |
| Tokenization | ~0.5-1s (186,927 tokens) | ~0.5-1s (213,969 tokens) |
| Parsing | ~0.3-0.5s (648 functions) | ~0.3-0.5s (704 functions) |
| Execution | ~0.1-0.5s | ~0.1-0.5s |
| **Total** | **~1-2s** | **~1-2s** |

Most time is spent in tokenization due to the large volume of tokens from Hamilton's standard libraries. Execution is fast because device operations are stubbed.
