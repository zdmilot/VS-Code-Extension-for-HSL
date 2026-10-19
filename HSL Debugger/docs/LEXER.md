# Lexer Module

> **Module**: `hsl_runtime/lexer.py` (416 lines)  
> **Phase**: 2 of 4  
> **Input**: Flattened source string  
> **Output**: Filtered `List[Token]` (no comments, markers, or checksums)

---

## Table of Contents

1. [Purpose](#purpose)
2. [Public API](#public-api)
3. [Token Types](#token-types)
4. [Tokenization Rules](#tokenization-rules)
5. [Keyword Map](#keyword-map)
6. [Operator Tokens](#operator-tokens)
7. [Special Token Detection](#special-token-detection)
8. [Filtering](#filtering)
9. [Error Handling](#error-handling)

---

## Purpose

The lexer converts a flattened source string into a stream of typed tokens. Each token carries:

- **Type**: What kind of token (keyword, operator, literal, etc.)
- **Value**: The actual text or parsed value
- **Position**: Line number and column for error reporting
- **File**: Source file name (for multi-file error reporting)

Comments, editor markers, and checksum lines are identified but **excluded** from the output, as they are not needed by the parser.

---

## Public API

### Class: Token

```python
@dataclass
class Token:
    type: TokenType     # Enum member
    value: Any          # String, int, float, or None
    line: int           # 1-based line number
    column: int         # 1-based column number
    file: str = ""      # Source filename
```

### Class: Lexer

```python
lexer = Lexer(source: str, filename: str = "<unknown>")
tokens = lexer.tokenize() → List[Token]
```

**Parameters:**
- `source`: The complete source code string to tokenize
- `filename`: Optional filename for error reporting

**Returns:** A list of `Token` objects, excluding comments, markers, and checksums. Always ends with an `EOF` token.

---

## Token Types

The `TokenType` enum defines all recognized token types:

### Literals

| TokenType | Description | Example |
|-----------|-------------|---------|
| `INTEGER` | Integer literal | `42`, `0xFF` |
| `FLOAT` | Floating-point literal | `3.14`, `1e-3` |
| `STRING` | String literal | `"hello"` |

### Keywords (30+)

| TokenType | Keyword | TokenType | Keyword |
|-----------|---------|-----------|---------|
| `NAMESPACE` | `namespace` | `FUNCTION` | `function` |
| `METHOD` | `method` | `VARIABLE` | `variable` |
| `STRING_KW` | `string` | `SEQUENCE` | `sequence` |
| `DEVICE` | `device` | `FILE_KW` | `file` |
| `OBJECT` | `object` | `TIMER` | `timer` |
| `EVENT` | `event` | `DIALOG` | `dialog` |
| `VOID` | `void` | `PRIVATE` | `private` |
| `STATIC` | `static` | `GLOBAL` | `global` |
| `CONST` | `const` | `IF` | `if` |
| `ELSE` | `else` | `FOR` | `for` |
| `WHILE` | `while` | `BREAK` | `break` |
| `LOCK` | `lock` | `UNLOCK` | `unlock` |
| `ABORT` | `abort` | `PAUSE` | `pause` |
| `ONERROR` | `onerror` | `GOTO` | `goto` |
| `RESUME` | `resume` | `NEXT` | `next` |
| `SCHEDULERONLY` | `scheduleronly` | `EXECUTORONLY` | `executoronly` |
| `SYNCHRONIZED` | `synchronized` | `WORKFLOW` | `workflow` |
| `ACTIVITY` | `activity` | `ACTIONBLOCK` | `actionblock` |

### Operators

| TokenType | Symbol | TokenType | Symbol |
|-----------|--------|-----------|--------|
| `PLUS` | `+` | `MINUS` | `-` |
| `STAR` | `*` | `SLASH` | `/` |
| `PERCENT` | `%` | `ASSIGN` | `=` |
| `EQ` | `==` | `NEQ` | `!=` |
| `LT` | `<` | `GT` | `>` |
| `LE` | `<=` | `GE` | `>=` |
| `AND` | `&&` | `OR` | `\|\|` |
| `NOT` | `!` | `AMPERSAND` | `&` |
| `PIPE` | `\|` | `INCREMENT` | `++` |
| `DECREMENT` | `--` | `LSHIFT` | `<<` |

### Delimiters

| TokenType | Symbol | TokenType | Symbol |
|-----------|--------|-----------|--------|
| `LPAREN` | `(` | `RPAREN` | `)` |
| `LBRACE` | `{` | `RBRACE` | `}` |
| `LBRACKET` | `[` | `RBRACKET` | `]` |
| `SEMICOLON` | `;` | `COMMA` | `,` |
| `DOT` | `.` | `SCOPE` | `::` |
| `COLON` | `:` | | |

### Special

| TokenType | Description |
|-----------|-------------|
| `IDENTIFIER` | User-defined name (`myVar`, `ML_STAR`) |
| `COMMENT` | Line or block comment (filtered out) |
| `EDITOR_MARKER` | Block marker comment `// {{{ ...` (filtered out) |
| `CHECKSUM` | Footer metadata line `// $$...$$` (filtered out) |
| `EOF` | End-of-file sentinel |

---

## Tokenization Rules

### Whitespace Handling

Whitespace (spaces, tabs, carriage returns) is skipped between tokens. Newlines are tracked for line counting but do not produce tokens.

### Number Lexing (`_read_number`)

```
Digit → Check for hex prefix '0x' or '0X'
  If hex: read hex digits [0-9a-fA-F] → INTEGER
  Else: read decimal digits
    If '.' follows → read fractional digits → FLOAT
    If 'e'/'E' follows → read exponent [+-]?digits → FLOAT
    Else → INTEGER
```

Examples:
| Input | Type | Value |
|-------|------|-------|
| `42` | `INTEGER` | `42` |
| `0xFF` | `INTEGER` | `255` |
| `3.14` | `FLOAT` | `3.14` |
| `1e-3` | `FLOAT` | `0.001` |
| `2.5E+10` | `FLOAT` | `25000000000.0` |

### String Lexing (`_read_string`)

Strings are delimited by double quotes (`"`). Escape sequences are processed:

| Escape | Replacement |
|--------|-------------|
| `\n` | Newline |
| `\t` | Tab |
| `\\` | Backslash |
| `\"` | Double quote |
| `\` + other | Kept as-is |

Unterminated strings (no closing `"` before end of line/file) raise a `LexerError`.

### Identifier / Keyword Lexing (`_read_identifier`)

```
Letter or '_' → read while letter, digit, or '_'
  → Lookup in keyword map
    If found: KEYWORD token type
    Else: IDENTIFIER token type
```

Identifiers are case-sensitive: `variable` is a keyword, `Variable` is an identifier.

### Operator Lexing

Operators are matched greedily (longest match first):

```
'+' → check next char
  '+' → INCREMENT (++)
  else → PLUS (+)

'-' → check next char
  '-' → DECREMENT (--)
  else → MINUS (-)

'=' → check next char
  '=' → EQ (==)
  else → ASSIGN (=)

'!' → check next char
  '=' → NEQ (!=)
  else → NOT (!)

'<' → check next char
  '=' → LE (<=)
  '<' → LSHIFT (<<)
  else → LT (<)

'>' → check next char
  '=' → GE (>=)
  else → GT (>)

'&' → check next char
  '&' → AND (&&)
  else → AMPERSAND (&)

'|' → check next char
  '|' → OR (||)
  else → PIPE (|)

':' → check next char
  ':' → SCOPE (::)
  else → COLON (:)
```

### Comment Lexing

#### Line Comments (`_read_line_comment`)

Starting with `//`, the lexer reads to end of line. During this read, it checks for:

1. **Editor markers**: Lines matching `// {{{ ...` or `// }}} ...`
   → Token type: `EDITOR_MARKER`

2. **Checksum lines**: Lines containing `$$author=` or `$$checksum=`
   → Token type: `CHECKSUM`

3. **Regular comments**: Everything else
   → Token type: `COMMENT`

#### Block Comments (`_read_block_comment`)

Starting with `/*`, the lexer reads until `*/`, tracking newlines for line counting. Returns a single `COMMENT` token.

---

## Keyword Map

The lexer uses a dictionary for O(1) keyword lookup:

```python
KEYWORDS = {
    "namespace":     TokenType.NAMESPACE,
    "function":      TokenType.FUNCTION,
    "method":        TokenType.METHOD,
    "variable":      TokenType.VARIABLE,
    "string":        TokenType.STRING_KW,
    "sequence":      TokenType.SEQUENCE,
    "device":        TokenType.DEVICE,
    "file":          TokenType.FILE_KW,
    "object":        TokenType.OBJECT,
    "timer":         TokenType.TIMER,
    "event":         TokenType.EVENT,
    "dialog":        TokenType.DIALOG,
    "void":          TokenType.VOID,
    "private":       TokenType.PRIVATE,
    "static":        TokenType.STATIC,
    "global":        TokenType.GLOBAL,
    "const":         TokenType.CONST,
    "if":            TokenType.IF,
    "else":          TokenType.ELSE,
    "for":           TokenType.FOR,
    "while":         TokenType.WHILE,
    "break":         TokenType.BREAK,
    "return":        TokenType.RETURN,
    "abort":         TokenType.ABORT,
    "pause":         TokenType.PAUSE,
    "onerror":       TokenType.ONERROR,
    "goto":          TokenType.GOTO,
    "resume":        TokenType.RESUME,
    "next":          TokenType.NEXT,
    "scheduleronly": TokenType.SCHEDULERONLY,
    "executoronly":  TokenType.EXECUTORONLY,
}
```

Any identifier not found in this map is classified as `IDENTIFIER`.

---

## Special Token Detection

### Editor Markers

The Hamilton Method Editor inserts block markers to track compound steps:

```csharp
// {{{ 5 1 "Aspirate" "GUID-HERE"
ML_STAR.Aspirate(...);
// }}} 5
```

The lexer detects these patterns inside line comments and creates `EDITOR_MARKER` tokens. These are filtered from the output since the parser doesn't need them.

### Checksum / Footer Lines

HSL files end with metadata:

```csharp
// $$author=admin$$_$$date=2024-01-01$$_$$checksum=ABCDEF01$$_$$length=1234$$_$$valid=0$$
```

The lexer detects `$$` patterns and creates `CHECKSUM` tokens. These are also filtered.

---

## Filtering

After tokenization, the lexer filters the token list:

```python
tokens = [t for t in raw_tokens if t.type not in (
    TokenType.COMMENT,
    TokenType.EDITOR_MARKER, 
    TokenType.CHECKSUM
)]
```

This produces a clean token stream containing only:
- Keywords
- Identifiers
- Literals (integers, floats, strings)
- Operators
- Delimiters
- EOF

---

## Error Handling

### LexerError

```python
class LexerError(Exception):
    pass
```

Raised for unrecoverable lexing errors:

| Error | Cause |
|-------|-------|
| Unterminated string | `"hello` with no closing `"` |
| Unterminated block comment | `/* ...` with no closing `*/` |

### Graceful Handling

For unexpected characters, the lexer:
1. Logs a warning
2. Skips the character
3. Continues tokenization

This prevents single invalid characters from halting the entire pipeline.

---

## Usage Example

```python
from hsl_runtime.lexer import Lexer

source = '''
variable x(42);
if (x > 0)
{
    Trace("positive");
}
'''

lexer = Lexer(source, "example.hsl")
tokens = lexer.tokenize()

for t in tokens:
    print(f"{t.type.name:15s} {t.value!r:20s} line={t.line} col={t.column}")
```

Output:
```
VARIABLE        'variable'           line=2 col=1
IDENTIFIER      'x'                  line=2 col=10
LPAREN          '('                  line=2 col=11
INTEGER         42                   line=2 col=12
RPAREN          ')'                  line=2 col=14
SEMICOLON       ';'                  line=2 col=15
IF              'if'                 line=3 col=1
LPAREN          '('                  line=3 col=4
IDENTIFIER      'x'                  line=3 col=5
GT              '>'                  line=3 col=7
INTEGER         0                    line=3 col=9
RPAREN          ')'                  line=3 col=10
LBRACE          '{'                  line=4 col=1
IDENTIFIER      'Trace'              line=5 col=5
LPAREN          '('                  line=5 col=10
STRING          'positive'           line=5 col=11
RPAREN          ')'                  line=5 col=21
SEMICOLON       ';'                  line=5 col=22
RBRACE          '}'                  line=6 col=1
EOF             None                 line=7 col=1
```
