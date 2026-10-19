"""
HSL Lexer / Tokenizer
=====================
Tokenizes HSL source code into a stream of typed tokens.
Handles HSL-specific syntax including :: scope resolution, method editor markers, etc.

SIMULATION ONLY - No hardware interaction.
"""

import re
from enum import Enum, auto
from dataclasses import dataclass
from typing import Optional


class TokenType(Enum):
    """All token types in HSL."""
    # Literals
    INTEGER = auto()
    FLOAT = auto()
    STRING = auto()

    # Identifiers & keywords
    IDENTIFIER = auto()

    # Keywords
    NAMESPACE = auto()
    FUNCTION = auto()
    METHOD = auto()
    VARIABLE = auto()
    STRING_TYPE = auto()
    SEQUENCE = auto()
    DEVICE = auto()
    FILE_TYPE = auto()
    OBJECT = auto()
    TIMER = auto()
    EVENT = auto()
    DIALOG = auto()
    RESOURCE = auto()
    CHAR_TYPE = auto()
    SHORT_TYPE = auto()
    LONG_TYPE = auto()
    FLOAT_TYPE = auto()
    VOID = auto()
    PRIVATE = auto()
    STATIC = auto()
    GLOBAL = auto()
    CONST = auto()
    SYNCHRONIZED = auto()
    IF = auto()
    ELSE = auto()
    FOR = auto()
    WHILE = auto()
    BREAK = auto()
    RETURN = auto()
    ABORT = auto()
    PAUSE = auto()
    ONERROR = auto()
    GOTO = auto()
    RESUME = auto()
    NEXT = auto()
    SCHEDULERONLY = auto()
    EXECUTORONLY = auto()
    LOOP = auto()
    STRUCT = auto()
    LOCK = auto()
    UNLOCK = auto()
    WORKFLOW = auto()
    ACTIVITY = auto()
    ACTIONBLOCK = auto()

    # Operators
    PLUS = auto()          # +
    MINUS = auto()         # -
    STAR = auto()          # *
    SLASH = auto()         # /
    PERCENT = auto()       # %
    ASSIGN = auto()        # =
    EQ = auto()            # ==
    NEQ = auto()           # !=
    LT = auto()            # <
    GT = auto()            # >
    LTE = auto()           # <=
    GTE = auto()           # >=
    AND = auto()           # &&
    OR = auto()            # ||
    NOT = auto()           # !
    AMPERSAND = auto()     # & (reference)
    INCREMENT = auto()     # ++
    DECREMENT = auto()     # --
    LSHIFT = auto()        # << (runtime include)
    PIPE = auto()          # | (bitwise OR)
    CARET = auto()         # ^ (power)
    BACKSLASH = auto()     # \ (line continuation or path separator)

    # Delimiters
    LPAREN = auto()        # (
    RPAREN = auto()        # )
    LBRACE = auto()        # {
    RBRACE = auto()        # }
    LBRACKET = auto()      # [
    RBRACKET = auto()      # ]
    SEMICOLON = auto()     # ;
    COMMA = auto()         # ,
    DOT = auto()           # .
    SCOPE = auto()         # ::
    COLON = auto()         # :
    HASH = auto()          # #

    # Special
    COMMENT = auto()       # // or /* */
    EDITOR_MARKER = auto() # // {{ ... }} method editor markers
    CHECKSUM = auto()      # // $$author=...$$
    NEWLINE = auto()
    WHITESPACE = auto()
    EOF = auto()
    ERROR = auto()


# Keyword map
KEYWORDS = {
    'namespace': TokenType.NAMESPACE,
    'function': TokenType.FUNCTION,
    'method': TokenType.METHOD,
    'variable': TokenType.VARIABLE,
    'string': TokenType.STRING_TYPE,
    'sequence': TokenType.SEQUENCE,
    'device': TokenType.DEVICE,
    'file': TokenType.FILE_TYPE,
    'object': TokenType.OBJECT,
    'timer': TokenType.TIMER,
    'event': TokenType.EVENT,
    'dialog': TokenType.DIALOG,
    'resource': TokenType.RESOURCE,
    'char': TokenType.CHAR_TYPE,
    'short': TokenType.SHORT_TYPE,
    'long': TokenType.LONG_TYPE,
    'float': TokenType.FLOAT_TYPE,
    'void': TokenType.VOID,
    'private': TokenType.PRIVATE,
    'static': TokenType.STATIC,
    'global': TokenType.GLOBAL,
    'const': TokenType.CONST,
    'synchronized': TokenType.SYNCHRONIZED,
    'if': TokenType.IF,
    'else': TokenType.ELSE,
    'for': TokenType.FOR,
    'while': TokenType.WHILE,
    'break': TokenType.BREAK,
    'return': TokenType.RETURN,
    'abort': TokenType.ABORT,
    'pause': TokenType.PAUSE,
    'onerror': TokenType.ONERROR,
    'goto': TokenType.GOTO,
    'resume': TokenType.RESUME,
    'next': TokenType.NEXT,
    'scheduleronly': TokenType.SCHEDULERONLY,
    'executoronly': TokenType.EXECUTORONLY,
    'loop': TokenType.LOOP,
    'struct': TokenType.STRUCT,
    'lock': TokenType.LOCK,
    'unlock': TokenType.UNLOCK,
    'workflow': TokenType.WORKFLOW,
    'activity': TokenType.ACTIVITY,
    'actionblock': TokenType.ACTIONBLOCK,
}


@dataclass
class Token:
    """A single token from the HSL source."""
    type: TokenType
    value: str
    line: int
    column: int
    file: str = ""

    def __repr__(self):
        return f"Token({self.type.name}, {self.value!r}, L{self.line}:{self.column})"


class LexerError(Exception):
    """Error during lexing."""
    def __init__(self, message: str, file: str = "", line: int = 0, column: int = 0):
        self.file = file
        self.line = line
        self.column = column
        super().__init__(f"{file}({line}:{column}): Lexer error: {message}")


class Lexer:
    """HSL Tokenizer - converts source text into token stream."""

    def __init__(self, source: str, filename: str = "<string>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []

    def tokenize(self) -> list[Token]:
        """Tokenize the entire source and return list of tokens."""
        self.tokens = []
        while self.pos < len(self.source):
            token = self._next_token()
            if token:
                # Skip whitespace and certain comment types for the parser
                if token.type not in (TokenType.WHITESPACE, TokenType.NEWLINE):
                    self.tokens.append(token)

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column, self.filename))
        return self.tokens

    def _next_token(self) -> Optional[Token]:
        """Read the next token from the source."""
        if self.pos >= len(self.source):
            return None

        ch = self.source[self.pos]
        start_line = self.line
        start_col = self.column

        # Newlines
        if ch == '\n':
            self._advance()
            return Token(TokenType.NEWLINE, '\n', start_line, start_col, self.filename)

        if ch == '\r':
            self._advance()
            if self.pos < len(self.source) and self.source[self.pos] == '\n':
                self._advance()
            return Token(TokenType.NEWLINE, '\n', start_line, start_col, self.filename)

        # Whitespace
        if ch in ' \t':
            while self.pos < len(self.source) and self.source[self.pos] in ' \t':
                self._advance()
            return Token(TokenType.WHITESPACE, ' ', start_line, start_col, self.filename)

        # Comments
        if ch == '/' and self.pos + 1 < len(self.source):
            next_ch = self.source[self.pos + 1]

            # Line comment
            if next_ch == '/':
                comment = self._read_line_comment()
                # Check for editor markers
                stripped = comment.strip()
                if stripped.startswith('// {{') or stripped.startswith('// }}') or \
                   stripped.startswith('// {{{') or stripped.startswith('// }}}'):
                    return Token(TokenType.EDITOR_MARKER, comment, start_line, start_col, self.filename)
                # Check for checksum
                if '$$author=' in comment or '$$valid=' in comment:
                    return Token(TokenType.CHECKSUM, comment, start_line, start_col, self.filename)
                return Token(TokenType.COMMENT, comment, start_line, start_col, self.filename)

            # Block comment
            if next_ch == '*':
                comment = self._read_block_comment()
                # Check for editor markers inside block comments
                if '{{ ' in comment and '}}' in comment:
                    return Token(TokenType.EDITOR_MARKER, comment, start_line, start_col, self.filename)
                return Token(TokenType.COMMENT, comment, start_line, start_col, self.filename)

        # String literals
        if ch == '"':
            return self._read_string(start_line, start_col)

        # Numbers
        if ch.isdigit() or (ch == '.' and self.pos + 1 < len(self.source) and
                            self.source[self.pos + 1].isdigit()):
            return self._read_number(start_line, start_col)

        # Negative numbers (handled in parser as unary minus)

        # Identifiers and keywords
        if ch.isalpha() or ch == '_':
            return self._read_identifier(start_line, start_col)

        # Two-character operators
        if self.pos + 1 < len(self.source):
            two_char = self.source[self.pos:self.pos + 2]
            token_type = {
                '==': TokenType.EQ,
                '!=': TokenType.NEQ,
                '<=': TokenType.LTE,
                '>=': TokenType.GTE,
                '&&': TokenType.AND,
                '||': TokenType.OR,
                '++': TokenType.INCREMENT,
                '--': TokenType.DECREMENT,
                '::': TokenType.SCOPE,
                '<<': TokenType.LSHIFT,
            }.get(two_char)
            if token_type:
                self._advance()
                self._advance()
                return Token(token_type, two_char, start_line, start_col, self.filename)

        # Single-character operators and delimiters
        # Special case: '*' at the start of a line followed by '$$author=' or '$$valid='
        # is a checksum trailer line (Hamilton Method Editor format).
        # VENUS rejects this format (error 1220), but we recognize it to avoid
        # cascading parse errors and report a clear warning instead.
        if ch == '*' and start_col == 1:
            rest_of_line = ''
            peek = self.pos + 1
            while peek < len(self.source) and self.source[peek] != '\n':
                rest_of_line += self.source[peek]
                peek += 1
            if '$$author=' in rest_of_line or '$$valid=' in rest_of_line:
                # Consume the entire line as a CHECKSUM token
                full = ch
                self._advance()
                while self.pos < len(self.source) and self.source[self.pos] != '\n':
                    full += self.source[self.pos]
                    self._advance()
                return Token(TokenType.CHECKSUM, full, start_line, start_col, self.filename)

        token_type = {
            '+': TokenType.PLUS,
            '-': TokenType.MINUS,
            '*': TokenType.STAR,
            '/': TokenType.SLASH,
            '%': TokenType.PERCENT,
            '=': TokenType.ASSIGN,
            '<': TokenType.LT,
            '>': TokenType.GT,
            '!': TokenType.NOT,
            '&': TokenType.AMPERSAND,
            '|': TokenType.PIPE,
            '^': TokenType.CARET,
            '\\': TokenType.BACKSLASH,
            '(': TokenType.LPAREN,
            ')': TokenType.RPAREN,
            '{': TokenType.LBRACE,
            '}': TokenType.RBRACE,
            '[': TokenType.LBRACKET,
            ']': TokenType.RBRACKET,
            ';': TokenType.SEMICOLON,
            ',': TokenType.COMMA,
            '.': TokenType.DOT,
            ':': TokenType.COLON,
            '#': TokenType.HASH,
        }.get(ch)

        if token_type:
            self._advance()
            return Token(token_type, ch, start_line, start_col, self.filename)

        # Unknown character - advance and report error
        self._advance()
        return Token(TokenType.ERROR, ch, start_line, start_col, self.filename)

    def _advance(self):
        """Advance the position by one character."""
        if self.pos < len(self.source):
            if self.source[self.pos] == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.pos += 1

    def _read_line_comment(self) -> str:
        """Read a line comment starting with //."""
        start = self.pos
        while self.pos < len(self.source) and self.source[self.pos] != '\n':
            self._advance()
        return self.source[start:self.pos]

    def _read_block_comment(self) -> str:
        """Read a block comment starting with /*."""
        start = self.pos
        self._advance()  # /
        self._advance()  # *
        while self.pos + 1 < len(self.source):
            if self.source[self.pos] == '*' and self.source[self.pos + 1] == '/':
                self._advance()  # *
                self._advance()  # /
                return self.source[start:self.pos]
            self._advance()
        # Unterminated block comment
        return self.source[start:self.pos]

    def _read_string(self, start_line: int, start_col: int) -> Token:
        """Read a string literal enclosed in double quotes."""
        self._advance()  # opening "
        result = []
        while self.pos < len(self.source) and self.source[self.pos] != '"':
            if self.source[self.pos] == '\\' and self.pos + 1 < len(self.source):
                next_ch = self.source[self.pos + 1]
                escape_map = {'n': '\n', 't': '\t', '\\': '\\', '"': '"', 'r': '\r', '0': '\0'}
                if next_ch in escape_map:
                    result.append(escape_map[next_ch])
                    self._advance()
                    self._advance()
                    continue
                # Decimal escape: \NNN (1-3 digits for character code)
                if next_ch.isdigit():
                    self._advance()  # skip backslash
                    digits = ''
                    for _ in range(3):
                        if self.pos < len(self.source) and self.source[self.pos].isdigit():
                            digits += self.source[self.pos]
                            self._advance()
                        else:
                            break
                    try:
                        result.append(chr(int(digits)))
                    except (ValueError, OverflowError):
                        result.append('?')
                    continue
                # Line continuation: backslash followed by newline
                if next_ch == '\n':
                    self._advance()  # skip backslash
                    self._advance()  # skip newline
                    continue
                if next_ch == '\r':
                    self._advance()  # skip backslash
                    self._advance()  # skip \r
                    if self.pos < len(self.source) and self.source[self.pos] == '\n':
                        self._advance()  # skip \n after \r
                    continue
                # Unknown escape - keep the character after backslash
                self._advance()  # skip backslash
                result.append(self.source[self.pos] if self.pos < len(self.source) else '')
                if self.pos < len(self.source):
                    self._advance()
                continue
            if self.source[self.pos] == '\n':
                break  # unterminated string
            result.append(self.source[self.pos])
            self._advance()

        if self.pos < len(self.source) and self.source[self.pos] == '"':
            self._advance()  # closing "

        return Token(TokenType.STRING, ''.join(result), start_line, start_col, self.filename)

    def _read_number(self, start_line: int, start_col: int) -> Token:
        """Read an integer or float literal."""
        start = self.pos
        is_float = False

        # Check for hex
        if (self.source[self.pos] == '0' and self.pos + 1 < len(self.source) and
                self.source[self.pos + 1] in 'xX'):
            self._advance()  # 0
            self._advance()  # x
            while self.pos < len(self.source) and self.source[self.pos] in '0123456789abcdefABCDEF':
                self._advance()
            return Token(TokenType.INTEGER, self.source[start:self.pos], start_line, start_col, self.filename)

        while self.pos < len(self.source) and self.source[self.pos].isdigit():
            self._advance()

        if self.pos < len(self.source) and self.source[self.pos] == '.':
            is_float = True
            self._advance()
            while self.pos < len(self.source) and self.source[self.pos].isdigit():
                self._advance()

        # Scientific notation
        if self.pos < len(self.source) and self.source[self.pos] in 'eE':
            is_float = True
            self._advance()
            if self.pos < len(self.source) and self.source[self.pos] in '+-':
                self._advance()
            while self.pos < len(self.source) and self.source[self.pos].isdigit():
                self._advance()

        value = self.source[start:self.pos]
        token_type = TokenType.FLOAT if is_float else TokenType.INTEGER
        return Token(token_type, value, start_line, start_col, self.filename)

    def _read_identifier(self, start_line: int, start_col: int) -> Token:
        """Read an identifier or keyword."""
        start = self.pos
        while self.pos < len(self.source) and (self.source[self.pos].isalnum() or self.source[self.pos] == '_'):
            self._advance()

        value = self.source[start:self.pos]

        # Check keywords (case-sensitive in HSL)
        token_type = KEYWORDS.get(value, TokenType.IDENTIFIER)
        
        # Special built-in constants treated as identifiers
        # hslTrue, hslFalse, hslInfinite, etc. stay as IDENTIFIER

        return Token(token_type, value, start_line, start_col, self.filename)
