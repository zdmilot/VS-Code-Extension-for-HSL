# HSL Language Reference

> This document describes the Hamilton Standard Language (HSL) as implemented by the HSL Debugger simulation runtime. It covers syntax, data types, operators, control flow, functions, and built-in features.

---

## Table of Contents

1. [Overview](#overview)
2. [Syntax Fundamentals](#syntax-fundamentals)
3. [Data Types](#data-types)
4. [Variables and Declarations](#variables-and-declarations)
5. [Operators](#operators)
6. [Control Flow](#control-flow)
7. [Functions and Methods](#functions-and-methods)
8. [Namespaces](#namespaces)
9. [Error Handling](#error-handling)
10. [Preprocessor Directives](#preprocessor-directives)
11. [Built-in Functions](#built-in-functions)
12. [Data Element Functions](#data-element-functions)
13. [Scheduler Keywords](#scheduler-keywords)
14. [File Structure](#file-structure)

---

## Overview

HSL is a C-like procedural language designed for Hamilton liquid-handling instrument automation. It features:

- **Variant typing** -- the `variable` type can hold integers, floats, or strings
- **Namespace scoping** -- `namespace Name { ... }` groups related declarations
- **COM integration** -- `object` type for Windows COM automation
- **Dual interface/implementation pattern** -- `#ifndef HSL_RUNTIME` / `#ifdef HSL_RUNTIME` separates edit-time stubs from run-time implementations
- **Error handling** -- `onerror goto label` and `onerror resume next`
- **Scheduler integration** -- `workflow`, `activity`, `resource`, `scheduleronly`, `executoronly` keywords

The entry point for an HSL program is `method Main()`.

---

## Syntax Fundamentals

### Statements

All statements are terminated with a semicolon (`;`):

```csharp
variable x(42);
Trace("Hello");
x = x + 1;
```

### Blocks

Curly braces group statements into blocks:

```csharp
if (x > 0)
{
    Trace("positive");
    x = x - 1;
}
```

### Comments

```csharp
// Single-line comment

/* Multi-line
   comment */
```

### Identifiers

- Must start with a letter (`a-z`, `A-Z`) or underscore (`_`)
- May contain letters, digits (`0-9`), and underscores
- **Case-sensitive**: `myVar` and `MyVar` are different identifiers
- Maximum length varies by context; practically unlimited in modern HSL

### Literals

| Type | Examples |
|------|---------|
| Integer | `0`, `42`, `-1`, `0xFF` (hexadecimal) |
| Float | `3.14`, `0.001`, `1e-3`, `2.5E+10` |
| String | `"hello"`, `"line1\nline2"`, `""` |

String escape sequences:

| Escape | Meaning |
|--------|---------|
| `\n` | Newline |
| `\t` | Tab |
| `\\` | Backslash |
| `\"` | Double quote |

### Reserved Keywords

```
abort        activity     actionblock  break        const
device       dialog       else         error        event
executoronly file         for          fork         function
global       goto         if           join         lock
loop         method       namespace    next         object
oncanceltask onerror      pause        private      resource
resume       return       schedule     scheduleronly sequence
static       string       struct       synchronized timer
unlock       variable     void         while        workflow
```

### Special Tokens

| Token | Purpose |
|-------|---------|
| `<< "file.hsl"` | Runtime include: loads and executes another HSL file |
| `// {method editor markers}` | Block markers mapping GUI steps to HSL code |
| `// $$author=...$$` | Footer metadata (author, date, checksum) |

---

## Data Types

HSL uses a **variant** type system. The core type is `variable`, which can hold an integer, float, or string at runtime. Additional types represent specific automation objects.

### Elementary Types

| Type Keyword | Description | Default Value |
|-------------|-------------|---------------|
| `variable` | Variant: integer, float, or string | `0` |
| `string` | Alias for string-valued variable | `""` |

### Automation Types

| Type Keyword | Description |
|-------------|-------------|
| `device` | Instrument connection (e.g., ML_STAR) |
| `sequence` | Ordered list of labware positions |
| `file` | File I/O handle |
| `object` | COM automation object |
| `timer` | Countdown/stopwatch timer |
| `event` | Synchronization event |
| `dialog` | Custom dialog window |
| `resource` | Scheduler resource (instrument units) |
| `error` | Error information (the global `err` object) |

### Type Constants

The runtime defines type indicator constants:

| Constant | Value | Meaning |
|----------|-------|---------|
| `hslInteger` | `"i"` | Integer type indicator |
| `hslFloat` | `"f"` | Float type indicator |
| `hslString` | `"s"` | String type indicator |

### Type Conversion Functions

| Function | Description | Example |
|----------|-------------|---------|
| `IVal(s)` | String → integer | `IVal("42")` → `42` |
| `FVal(s)` | String → float | `FVal("3.14")` → `3.14` |
| `IStr(n)` | Number → integer string | `IStr(42)` → `"42"` |
| `FStr(n)` | Number → float string | `FStr(3.14)` → `"3.140000"` |
| `GetType(v)` | Returns type string | `GetType(42)` → `"i"` |

### Predefined Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `hslTrue` | `1` | Boolean true |
| `hslFalse` | `0` | Boolean false |
| `hslInfinite` | `2147483647` | Maximum integer / infinite timeout |
| `hslOK` | `1` | Dialog OK button result |
| `hslCancel` | `2` | Dialog Cancel button result |

---

## Variables and Declarations

### Basic Declaration

```csharp
variable x;              // default value 0
variable y(42);          // initialized to 42
variable s("hello");     // initialized to string
variable a, b, c;        // comma-separated declarations
variable d(1), e(2);     // comma-separated with initializers
```

### Type-Specific Declarations

```csharp
string name("default");
device ML_STAR("deck.lay", "ML_STAR", hslTrue);
sequence mySeq;
file logFile;
object comObj;
timer myTimer;
event syncEvent;
dialog myDialog;
```

### Arrays

```csharp
variable arr[];                // dynamic array
variable fixedArr[10];         // fixed-size array (10 elements)
```

Dynamic arrays are resized with element functions:

```csharp
arr.SetSize(5);
arr.SetAt(0, "first");
arr.AddAsLast("appended");
variable val;
val = arr.GetAt(0);
```

### Qualifiers

| Qualifier | Meaning |
|-----------|---------|
| `static` | Function-local persistent variable |
| `private` | Not exported to the Method Editor |
| `global` | Shared across methods in a workflow |
| `const` | Compile-time constant (limited use) |

```csharp
global device ML_STAR("deck.lay", "ML_STAR", hslTrue);
static variable callCount(0);
private function helper() { }
```

### Variable Placement

Variables must be declared at the top of a scope (function/method body) before any executable statements -- similar to C89 rules.

---

## Operators

### Precedence Table (highest to lowest)

| Precedence | Operator | Description | Associativity |
|-----------|----------|-------------|---------------|
| 1 | `()` `[]` `.` `::` | Grouping, array index, member, scope | Left |
| 2 | `++` `--` `!` `-` (unary) | Increment, decrement, NOT, negation | Right |
| 3 | `*` `/` `%` | Multiply, divide, modulo | Left |
| 4 | `+` `-` | Add/concatenate, subtract | Left |
| 5 | `<` `<=` `>` `>=` | Relational comparison | Left |
| 6 | `==` `!=` | Equality, inequality | Left |
| 7 | `&` | Bitwise AND | Left |
| 8 | `^` | Bitwise XOR / Power | Left |
| 9 | `\|` | Bitwise OR | Left |
| 10 | `&&` | Logical AND | Left |
| 11 | `\|\|` | Logical OR | Left |
| 12 | `=` | Assignment | Right |

### Arithmetic Operators

```csharp
variable a(10), b(3);
variable sum, diff, prod, quot, rem;
sum  = a + b;     // 13
diff = a - b;     // 7
prod = a * b;     // 30
quot = a / b;     // 3 (integer division when both operands are integer)
rem  = a % b;     // 1
```

### String Concatenation

The `+` operator concatenates when either operand is a string:

```csharp
variable greeting;
greeting = "Hello" + " " + "World";   // "Hello World"
greeting = "Value: " + IStr(42);       // "Value: 42"
```

### Comparison Operators

```csharp
if (a == b) { }   // equal
if (a != b) { }   // not equal
if (a < b)  { }   // less than
if (a <= b) { }   // less than or equal
if (a > b)  { }   // greater than
if (a >= b) { }   // greater than or equal
```

### Logical Operators

```csharp
if (a > 0 && b > 0) { }   // logical AND
if (a > 0 || b > 0) { }   // logical OR
if (!done)           { }   // logical NOT
```

### Increment / Decrement

```csharp
variable i(0);
i++;    // post-increment: i becomes 1
++i;    // pre-increment: i becomes 2
i--;    // post-decrement: i becomes 1
--i;    // pre-decrement: i becomes 0
```

### Bitwise Operators

```csharp
variable flags;
flags = a | b;     // bitwise OR
flags = a & b;     // bitwise AND (not yet in simulation runtime)
```

---

## Control Flow

### if / else

```csharp
if (condition)
{
    // then branch
}
else if (otherCondition)
{
    // else-if branch
}
else
{
    // else branch
}
```

### for Loop

```csharp
for (variable i(0); i < 10; i++)
{
    Trace(IStr(i));
}
```

The three parts (initializer, condition, increment) are separated by semicolons. All three are optional:

```csharp
for (;;)    // infinite loop
{
    break;  // exit the loop
}
```

### while Loop

```csharp
while (condition)
{
    // loop body
    if (done)
        break;
    if (!skip)
    {
        // work for this iteration
    }
}
```

### break and loop continuation

- `break;` -- exits the innermost `for` or `while` loop

HSL does not support the C/C++ `continue` keyword and does not have a
standalone `next;` loop statement. Restructure the loop body with a condition
or control flag. The word `next` is reserved for the error-handling forms
`resume next;` and `onerror resume next;`.

### return

```csharp
function add(variable a, variable b) variable
{
    return (a + b);
}
```

### abort

```csharp
abort;    // terminates the entire program
```

### pause

```csharp
pause;    // pauses execution (in simulation: logged and continued)
```

### Labels and goto

Labels are used with `onerror goto` for error handling:

```csharp
onerror goto ErrorHandler;
// ... code that might fail ...
return;

ErrorHandler:
{
    // handle the error
    resume next;
}
```

---

## Functions and Methods

### Function Declaration

```csharp
function MyFunction(variable param1, variable& param2) variable
{
    // param1: passed by value
    // param2: passed by reference (note the &)
    param2 = param1 * 2;
    return (param1);
}
```

The return type appears after the parameter list. If omitted, the function returns `void`.

### Method Declaration

Methods are top-level entry points. The program starts at `method Main()`:

```csharp
method Main()
{
    variable result;
    result = MyFunction(42, result);
    Trace("Result: ", IStr(result));
}
```

### Function Modifiers

| Modifier | Effect |
|----------|--------|
| `static` | Not exported to the Method Editor; internal helper |
| `private` | Same as static for functions |

```csharp
static function _internalHelper(variable x) variable
{
    return (x * x);
}
```

### Parameter Passing

- **By value** (default): `variable param` -- a copy is made
- **By reference**: `variable& param` -- modifications affect the caller's variable

```csharp
function swap(variable& a, variable& b)
{
    variable temp;
    temp = a;
    a = b;
    b = temp;
}
```

### Calling Functions

```csharp
variable result;
result = MyNamespace::MyFunction(arg1, arg2);
```

Qualified calls use `::` to specify the namespace.

---

## Namespaces

Namespaces prevent name collisions between libraries:

```csharp
namespace MyLibrary
{
    variable libVersion("1.0");

    function Initialize()
    {
        Trace("MyLibrary initialized");
    }
}
```

Access from outside:

```csharp
MyLibrary::Initialize();
variable v;
v = MyLibrary::libVersion;
```

### The `_Method` Namespace

The Method Editor places the `Main` method inside the `_Method` namespace:

```csharp
namespace _Method
{
    method Main()
    {
        // entry point
    }
}
```

The HSL Debugger searches for `main` / `Main` / `_Method::main` / `_Method::Main` as the program entry point.

---

## Error Handling

HSL provides structured error handling through the `onerror` statement and the global `err` object.

### onerror goto

```csharp
function riskyOperation()
{
    onerror goto HandleError;

    // ... operations that might fail ...

    return;

    HandleError:
    {
        variable errId, errDesc;
        errId = err.GetId();
        errDesc = err.GetDescription();
        Trace("Error: ", errDesc);
        err.Clear();
        resume next;    // continue after the failing statement
    }
}
```

### onerror resume next

Suppress errors and continue execution:

```csharp
onerror resume next;
// errors are silently ignored; check err.GetId() after each call
riskyCall();
if (err.GetId() != 0)
{
    Trace("Call failed: ", err.GetDescription());
    err.Clear();
}
```

### onerror goto 0

Disable the current error handler:

```csharp
onerror goto 0;    // errors will now abort the program
```

### The `err` Object

The global `err` object provides error information:

| Function | Description |
|----------|-------------|
| `err.GetId()` | Returns the error code (integer) |
| `err.GetDescription()` | Returns the error message (string) |
| `err.Clear()` | Resets the error state |
| `err.Raise(id, desc, source)` | Raises a custom error |

### resume next

Inside an error handler, `resume next` continues execution after the statement that caused the error:

```csharp
ErrorHandler:
{
    // log the error
    Trace("Error caught: ", err.GetDescription());
    err.Clear();
    resume next;    // continue execution
}
```

---

## Preprocessor Directives

HSL uses C-style preprocessor directives processed before compilation.

### #include

```csharp
#include "HSLStrLib.hsl"          // relative path
#include "C:\\Hamilton\\Library\\HSLMthLib.hsl"   // absolute path
```

### #define / #undef

```csharp
#define DEBUG_MODE 1
#define VERSION "2.0"
#undef DEBUG_MODE
```

### Conditional Compilation

```csharp
#ifdef HSL_RUNTIME
    // included only at runtime
#endif

#ifndef MY_LIB_GUARD
#define MY_LIB_GUARD 1
    // library body (include guard)
#endif

#ifdef HSL_RUNTIME
    // runtime implementation
#else
    // edit-time stub
#endif
```

### #pragma once

Prevents a file from being included more than once:

```csharp
#pragma once
```

### HSL_RUNTIME Constant

The preprocessor always defines `HSL_RUNTIME = 1` at runtime. This enables the standard dual-section library pattern:

```csharp
#ifndef HSL_RUNTIME
    // Section 1: Interface (empty stubs for edit-time syntax checking)
    namespace MyLib { function DoWork() {} }
#endif

#ifdef HSL_RUNTIME
    // Section 2: Implementation (full code for runtime)
    namespace MyLib { function DoWork() { /* real code */ } }
#endif
```

---

## Built-in Functions

### Trace and Diagnostics

| Function | Description |
|----------|-------------|
| `Trace(...)` | Output trace message (variable number of args) |
| `FormatTrace(category, level, msg, ...)` | Categorized trace with level filtering |

### Type Conversion

| Function | Description |
|----------|-------------|
| `IStr(n)` | Integer/float → integer string |
| `FStr(n)` | Integer/float → float string |
| `IVal(s)` | String → integer |
| `FVal(s)` | String → float |
| `IVal2(s)` | String → integer (variant 2) |
| `FVal2(s)` | String → float (variant 2) |
| `GetType(v)` | Returns type string: `"i"`, `"f"`, or `"s"` |

### String Functions

| Function | Description |
|----------|-------------|
| `StrGetLength(s)` | String length |
| `StrFind(s, sub)` | Find substring position (-1 if not found) |
| `StrLeft(s, n)` | Left n characters |
| `StrRight(s, n)` | Right n characters |
| `StrMid(s, start, len)` | Substring from position |
| `StrMakeUpper(s)` | Convert to uppercase |
| `StrMakeLower(s)` | Convert to lowercase |
| `StrConcat2(a, b)` | Concatenate 2 strings |
| `StrConcat4(a, b, c, d)` | Concatenate 4 strings |
| `StrConcat8(...)` | Concatenate 8 strings |
| `StrConcat12(...)` | Concatenate 12 strings |
| `StrReplace(s, old, new)` | Replace all occurrences |
| `StrTrimLeft(s)` | Remove leading whitespace |
| `StrTrimRight(s)` | Remove trailing whitespace |
| `StrIsDigit(s)` | Test if string is numeric |
| `StrFillLeft(s, n, c)` | Pad left with character |
| `StrFillRight(s, n, c)` | Pad right with character |
| `StrIStr(n)` | Integer → string (in Str namespace) |
| `StrFStr(n)` | Float → string (in Str namespace) |
| `StrFStrEx(n, prec)` | Float → string with precision |
| `StrIVal(s)` | String → integer (in Str namespace) |
| `StrFVal(s)` | String → float (in Str namespace) |
| `StrGetType(v)` | Type string (in Str namespace) |

### Math Functions

| Function | Description |
|----------|-------------|
| `MthAbs(n)` | Absolute value |
| `MthSqrt(n)` | Square root |
| `MthPow(base, exp)` | Power |
| `MthMin(a, b)` | Minimum of two values |
| `MthMax(a, b)` | Maximum of two values |
| `MthRound(n)` | Round to nearest integer |

### System Path Functions

| Function | Returns |
|----------|---------|
| `GetBinPath()` | Hamilton `Bin\` directory |
| `GetLibraryPath()` | Hamilton `Library\` directory |
| `GetMethodsPath()` | Hamilton `Methods\` directory |
| `GetLogFilesPath()` | Hamilton `Logfiles\` directory |
| `GetConfigPath()` | Hamilton `Config\` directory |
| `GetLabwarePath()` | Hamilton `Labware\` directory |
| `GetSystemPath()` | Hamilton `System\` directory |

### I/O Functions

| Function | Description |
|----------|-------------|
| `InputBox(prompt, title, default)` | Prompt for user input (simulation: returns `""`) |
| `MessageBox(msg, flags)` | Show message box (simulation: returns `hslOK`) |
| `Shell(cmd)` | Execute OS command (simulation: logged, not executed) |

### Miscellaneous

| Function | Description |
|----------|-------------|
| `SearchPath(filename)` | Find file in Hamilton search paths |
| `GetFileName()` | Current source file path |
| `GetFunctionName()` | Current function name |
| `GetMethodFileName()` | Current method file path |
| `GetLineNumber()` | Current source line number |
| `RegisterAbortHandler(func)` | Register cleanup function on abort |
| `Translate(s)` | Language translation (passthrough in simulation) |
| `AddCheckSum()` | Footer checksum utility |

---

## Data Element Functions

Automation types have built-in element functions called with dot notation.

### Array Element Functions

```csharp
variable arr[];
arr.SetSize(10);              // Set array size
variable size;
size = arr.GetSize();         // Get current size
arr.SetAt(0, "hello");        // Set element at index
variable val;
val = arr.GetAt(0);           // Get element at index
val = arr.ElementAt(0);       // Get element reference at index
arr.AddAsLast("world");       // Append element
```

### Sequence Element Functions

```csharp
sequence mySeq;
mySeq.SetCurrentPosition(1);
variable pos;
pos = mySeq.GetCurrentPosition();
mySeq.SetCount(96);
variable total;
total = mySeq.GetCount();
total = mySeq.GetTotal();
mySeq.SetMax(96);
variable max;
max = mySeq.GetMax();
variable name;
name = mySeq.GetName();
mySeq.Increment(1);
variable labId;
labId = mySeq.GetLabwareId();
variable posId;
posId = mySeq.GetPositionId();
```

### File Element Functions

```csharp
file f;
f.Open("data.txt", hslRead);
f.SetDelimiter(hslAsciiText);
f.AddField(1, fieldVar, hslString);
f.ReadRecord();
f.WriteRecord();
f.ReadString(strVar);
f.WriteString("output");
variable eof;
eof = f.Eof();
f.Close();
```

File access mode constants:

| Constant | Value | Description |
|----------|-------|-------------|
| `hslRead` | `1` | Read only |
| `hslWrite` | `2` | Write (create/overwrite) |
| `hslAppend` | `3` | Append to existing |
| `hslReadWrite` | `4` | Read and write |

### Dialog Element Functions

```csharp
dialog myDialog;
myDialog.InitCustomDialog("MyDialog");
myDialog.SetCustomDialogProperty("Title", "Configuration");
myDialog.ShowCustomDialog();
```

### Object Element Functions

```csharp
object comObj;
comObj.CreateObject("Scripting.FileSystemObject");
// Access properties and methods via dot notation
comObj.GetObject();
comObj.ReleaseObject();
```

### Timer Element Functions

```csharp
timer myTimer;
myTimer.SetTimer(60);          // Set 60-second timer
myTimer.WaitTimer(hslFalse);   // Wait for timer
variable elapsed;
elapsed = myTimer.ReadElapsed();
myTimer.Stop();
myTimer.Restart();
```

### String Element Functions

```csharp
string s("Hello World");
variable pos, len;
pos = s.Find("World");
len = s.GetLength();
variable left;
left = s.Left(5);              // "Hello"
variable right;
right = s.Right(5);            // "World"
variable mid;
mid = s.Mid(6, 5);             // "World"
s.MakeUpper();                 // "HELLO WORLD"
s.MakeLower();                 // "hello world"
variable cmp;
cmp = s.Compare("other");
variable span;
span = s.SpanExcluding(" ");   // characters before first space
```

### Error Element Functions

```csharp
variable id, desc;
id = err.GetId();
desc = err.GetDescription();
err.Clear();
err.Raise(1001, "Custom error", "MyFunction");
```

---

## Scheduler Keywords

HSL includes scheduler-related keywords for multi-instrument workflow orchestration. These are parsed but **not simulated** by the HSL Debugger.

### workflow

Top-level scheduling entry point:

```csharp
workflow MyWorkflow()
{
    RegisterMethod("Method1.hsl", "Method 1", methodId);
    ActivateDelay(0, hslSchedulingStart, methodId, "Task 1", taskId);
}
```

### scheduleronly / executoronly

```csharp
scheduleronly
{
    // Code only processed during scheduling phase
}

executoronly
{
    // Code only processed during execution phase
    // In non-workflow methods, this block IS executed
}
```

In the simulation runtime:
- `scheduleronly` blocks are **skipped** (not executed)
- `executoronly` blocks are **executed**

### activity / actionblock

```csharp
activity(duration, resource, count, unit, color, "name")
{
    // Scheduled activity code
}
oncanceltask
{
    // Cleanup on task cancellation
}

actionblock(duration, color, "name")
{
    // Timed action (no resource needed)
}
```

### resource

```csharp
resource ML_STARRes(1, hslBlack, "Microlab STAR");
```

### Fork / Join

Parallel execution in non-scheduler context:

```csharp
variable taskHandle;
taskHandle = Fork("parallelFunction");
// ... other work ...
Join(taskHandle, hslInfinite);
```

---

## File Structure

### Standard Method File

```csharp
// Includes
#include "HSLStrLib.hsl"
#include "HSLMthLib.hsl"

// Global declarations
// ...

namespace _Method
{
    method Main()
    {
        variable x(42);
        Trace("Value: ", IStr(x));
    }
}

// Footer metadata (auto-generated)
// $$author=User$$_$$date=2024-01-01$$_$$checksum=ABCDEF01$$_$$length=1234$$_$$valid=0$$
```

### Standard Library File

```csharp
#ifndef __MyLibrary_hsl__
#define __MyLibrary_hsl__ 1

// Section 1: Interface (edit-time)
#ifndef HSL_RUNTIME
namespace MyLibrary
{
    function DoWork(variable& input) variable
    {}
}
#endif

// Section 2: Implementation (runtime)
#ifdef HSL_RUNTIME
namespace MyLibrary
{
    function DoWork(variable& input) variable
    {
        // full implementation
        return (input * 2);
    }
}
#endif

#endif // __MyLibrary_hsl__
// $$author=...$$
```

### Block Markers

Method files contain structured comments that map GUI compound steps to HSL code:

```csharp
// {{{ N 1 "step name" "GUID"
// generated HSL code for this step
// }}} N
```

Where `N` is the sequential step number. These markers are critical for the Method Editor to interpret the file.

### Footer Metadata

Every HSL file ends with a metadata line:

```
// $$author=username$$_$$date=YYYY-MM-DD ...$$_$$checksum=HEX$$_$$length=NNN$$_$$valid=V$$
```

| Field | Description |
|-------|-------------|
| `author` | Last editor username |
| `date` | Last modification timestamp |
| `checksum` | CRC-32 integrity check |
| `length` | File size in bytes |
| `valid` | `0` = user-edited, `1` = Hamilton-validated |

---

## Simulation Differences

The HSL Debugger provides a faithful simulation of the language, with these differences from the Hamilton runtime:

| Feature | Hamilton Runtime | HSL Debugger |
|---------|-----------------|--------------|
| Device commands | Execute on hardware | Logged; return success stubs |
| COM objects | Real COM interop | Property-bag stubs |
| `Shell()` | Executes OS command | Logged; **never executed** |
| `MessageBox()` | Shows dialog | Returns `hslOK` (1) |
| `InputBox()` | Shows input dialog | Returns empty string |
| Timers | Real-time delays | Immediate return (no delay) |
| Dialogs | Interactive windows | Auto-OK with property tracking |
| Fork/Join | True multithreading | Not simulated |
| Scheduler | Full scheduling engine | `scheduleronly` skipped; `executoronly` executed |
| `struct` | Structure type support | Not parsed |
| `loop(N)` | Loop N times | Not implemented |
| `^` operator | Power / XOR | Not yet implemented |
