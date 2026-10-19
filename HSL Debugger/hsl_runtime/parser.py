"""
HSL Parser
==========
Recursive-descent parser that transforms a flat token stream (from the Lexer)
into a structured Abstract Syntax Tree (AST) representing Hamilton Standard
Language (HSL) programs.

Architecture
------------
The parser uses a **recursive-descent** strategy with **precedence climbing**
for expressions.  Each grammar production maps to a ``parse_*`` or ``_parse_*``
method.  The public ``parse()`` entry point drives the top-level loop and
returns a ``Program`` AST node.

  Token stream  -->  Parser.parse()  -->  Program (AST root)

Grammar overview (simplified EBNF)
----------------------------------
::

    program        = { top_level } EOF ;
    top_level      = namespace | qualified_decl | function_decl | method_decl
                   | variable_decl | scheduleronly_block | executoronly_block
                   | struct_decl | statement ;
    namespace      = 'namespace' IDENT '{' { top_level } '}' ;
    function_decl  = 'function' IDENT '(' params ')' [ return_type ] ( block | ';' ) ;
    variable_decl  = type_kw ['&'] IDENT [ '[]' ] [ '(' expr ')' | '=' expr ]
                     { ',' ... } ';' ;
    statement      = block | var_decl | if | for | while | loop | break
                   | return | abort | pause | onerror | resume_next | label
                   | expr_stmt ;
    block          = '{' { statement } '}' ;
    if             = 'if' '(' expr ')' stmt_or_block [ 'else' stmt_or_block ] ;
    for            = 'for' '(' [init] ';' [cond] ';' [update] ')' stmt_or_block ;
    while          = 'while' '(' expr ')' stmt_or_block ;
    loop           = 'loop' '(' expr ')' stmt_or_block ;
    label          = IDENT ':' [ block ] ;   // error handler target
    expr_stmt      = expr [ '=' expr ] ';' ;

Expression precedence (lowest to highest)
-----------------------------------------
1. Assignment  ``=``
2. Logical OR  ``||``
3. Logical AND ``&&``
4. Bitwise OR  ``|``
5. Bitwise AND ``&``
6. Equality    ``==  !=``
7. Comparison  ``<  >  <=  >=``
8. Additive    ``+  -``
9. Multiplicative ``*  /  %``
10. Power      ``^``  (right-associative)
11. Unary      ``-  !  ++  --``  (prefix)
12. Postfix    ``++  --  .member  [index]  (args)  ::scope``
13. Primary    literals, identifiers, ``(expr)``

Error handling
--------------
Parse errors are collected in ``self.errors`` rather than aborting immediately.
Two recovery strategies are provided:

* ``_recover()`` - skips to the next ``;`` or ``}`` at brace-depth 0 (top-level).
* ``_recover_in_block()`` - skips to the next ``;`` at depth 0 **without**
  consuming the closing ``}`` so the enclosing ``parse_block`` can finish.

Usage
-----
::

    from hsl_runtime.lexer import Lexer
    from hsl_runtime.parser import Parser

    tokens = Lexer(source, "demo.hsl").tokenize()
    parser = Parser(tokens, "demo.hsl")
    program = parser.parse()        # -> Program AST
    if parser.errors:
        for e in parser.errors:
            print(e)

SIMULATION ONLY - No hardware interaction.
"""

from typing import Optional
from .lexer import Token, TokenType, Lexer
from .ast_nodes import *


class ParseError(Exception):
    """Raised (and caught) when the parser encounters an unexpected token.

    Attributes
    ----------
    token : Token | None
        The offending token, used to report file, line and column.
    """

    def __init__(self, message: str, token: Optional[Token] = None):
        self.token = token
        loc = ""
        if token:
            loc = f"{token.file}({token.line}:{token.column}): "
        super().__init__(f"{loc}Parse error: {message}")


class Parser:
    """Recursive-descent parser for the Hamilton Standard Language (HSL).

    Converts a list of ``Token`` objects (produced by ``Lexer.tokenize()``)
    into a ``Program`` AST node tree that the ``Interpreter`` can execute.

    Parameters
    ----------
    tokens : list[Token]
        Flat token stream from the lexer.  Must end with a ``TokenType.EOF``
        sentinel.
    filename : str
        Source filename used for diagnostics (default ``"<string>"``).

    Attributes
    ----------
    pos : int
        Current read position in the token list.
    errors : list[ParseError]
        Non-fatal parse errors collected during parsing.  The parser
        attempts error-recovery so that multiple issues can be reported
        from a single pass.
    """

    def __init__(self, tokens: list[Token], filename: str = "<string>", strict: bool = False):
        # Comments and Method Editor metadata are lexed for tooling, but they
        # are grammatically insignificant and may appear between any tokens.
        self.tokens = [
            token for token in tokens
            if token.type not in (
                TokenType.COMMENT,
                TokenType.EDITOR_MARKER,
                TokenType.CHECKSUM,
            )
        ]
        self.filename = filename
        self.pos = 0
        self.errors: list[ParseError] = []
        self.strict = strict

    # ========================================================================
    # Token stream navigation
    #
    # Low-level helpers that advance through the token list.  These form the
    # foundation for every ``parse_*`` method.
    # ========================================================================

    def current(self) -> Token:
        """Return the token at the current position without consuming it.

        If the position is past the end, an EOF sentinel token is returned
        so callers never need to bounds-check.
        """
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return Token(TokenType.EOF, "", 0, 0, self.filename)

    def peek(self, offset: int = 1) -> Token:
        """Return the token at ``pos + offset`` without consuming it.

        Parameters
        ----------
        offset : int
            Number of positions ahead to look (default 1 = next token).

        Returns
        -------
        Token
            The token at the requested offset, or an EOF sentinel.
        """
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx]
        return Token(TokenType.EOF, "", 0, 0, self.filename)

    def advance(self) -> Token:
        """Consume and return the current token, advancing ``pos`` by one."""
        token = self.current()
        if self.pos < len(self.tokens):
            self.pos += 1
        return token

    def expect(self, token_type: TokenType, value: Optional[str] = None) -> Token:
        """Assert the current token matches *token_type* and consume it.

        Parameters
        ----------
        token_type : TokenType
            Required token type.
        value : str | None
            If given, the token's text value must also match.

        Returns
        -------
        Token
            The consumed token.

        Raises
        ------
        ParseError
            If the current token does not match.
        """
        if token_type not in (TokenType.COMMENT, TokenType.EDITOR_MARKER, TokenType.CHECKSUM):
            self.skip_comments_and_markers()
        token = self.current()
        if token.type != token_type:
            raise ParseError(
                f"Expected {token_type.name} but got {token.type.name} ({token.value!r})",
                token
            )
        if value is not None and token.value != value:
            raise ParseError(
                f"Expected {value!r} but got {token.value!r}",
                token
            )
        return self.advance()

    def match(self, token_type: TokenType, value: Optional[str] = None) -> Optional[Token]:
        """Conditionally consume the current token if it matches.

        Unlike ``expect()``, this method never raises.  Returns ``None`` when
        the current token does not match, leaving ``pos`` unchanged.

        Parameters
        ----------
        token_type : TokenType
            Token type to match.
        value : str | None
            Optional text value that must also match.

        Returns
        -------
        Token | None
            The consumed token on match, otherwise ``None``.
        """
        if token_type not in (TokenType.COMMENT, TokenType.EDITOR_MARKER, TokenType.CHECKSUM):
            self.skip_comments_and_markers()
        token = self.current()
        if token.type == token_type:
            if value is None or token.value == value:
                return self.advance()
        return None

    def skip_comments_and_markers(self):
        """Advance past any comment, editor-marker, or checksum tokens.

        The lexer preserves these tokens in the stream so they are available
        for tooling, but the parser has no use for them during tree
        construction.
        """
        while self.current().type in (TokenType.COMMENT, TokenType.EDITOR_MARKER,
                                       TokenType.CHECKSUM):
            self.advance()

    def at_end(self) -> bool:
        """Return ``True`` when the current token is EOF (end of source)."""
        return self.current().type == TokenType.EOF

    def peek_type(self, offset: int = 1) -> TokenType:
        """Return just the *type* of the token at ``pos + offset``.

        Convenience wrapper around ``peek()`` used for simple lookahead
        decisions (e.g.  detecting ``IDENT ':'`` label patterns).

        Parameters
        ----------
        offset : int
            Number of positions ahead (default 1).

        Returns
        -------
        TokenType
            The type of the peeked token, or ``TokenType.EOF``.
        """
        idx = self.pos + offset
        if idx < len(self.tokens):
            return self.tokens[idx].type
        return TokenType.EOF

    # ========================================================================
    # Main parsing entry point
    #
    # Drives the top-level loop, collecting declarations into a ``Program``
    # node.  Parse errors are caught, recorded, and recovered from so that
    # as many issues as possible are reported in a single pass.
    # ========================================================================

    def parse(self) -> Program:
        """Parse the complete token stream into a ``Program`` AST.

        Iterates over top-level constructs (namespaces, functions, variable
        declarations, bare statements, etc.) until EOF.  Parse errors are
        caught and appended to ``self.errors``; the parser then skips ahead
        via ``_recover()`` and continues.

        Returns
        -------
        Program
            Root AST node whose ``declarations`` list contains every
            successfully-parsed top-level construct.
        """
        program = Program(source_file=self.filename)

        while not self.at_end():
            self.skip_comments_and_markers()
            if self.at_end():
                break

            try:
                decl = self.parse_top_level()
                if decl is not None:
                    program.declarations.append(decl)
            except ParseError as e:
                self.errors.append(e)
                if self.strict:
                    raise
                # Error recovery: skip to next semicolon or close brace
                self._recover()

        return program

    def _recover(self):
        """Top-level error recovery: skip tokens until a safe restart point.

        Consumes tokens until one of the following is reached at brace
        depth 0:

        * A semicolon ``;`` - consumed, then returns.
        * A closing brace ``}`` when depth ≤ 0 - consumed, then returns.
        * EOF - returns.

        Brace depth is tracked so that an entire ``{ … }`` block is
        skipped as a unit when encountered.
        """
        depth = 0
        while not self.at_end():
            tok = self.current()
            if tok.type == TokenType.LBRACE:
                depth += 1
            elif tok.type == TokenType.RBRACE:
                if depth <= 0:
                    self.advance()
                    return
                depth -= 1
            elif tok.type == TokenType.SEMICOLON and depth == 0:
                self.advance()
                return
            self.advance()

    def _recover_in_block(self):
        """In-block error recovery: skip to a safe point *inside* a block.

        Behaves like ``_recover()`` but does **not** consume a closing
        ``}`` at depth 0.  Instead it stops and returns so that the
        enclosing ``parse_block()`` can match the ``}`` itself.

        This ensures that a single bad statement does not swallow the
        block terminator and cascade into additional spurious errors.
        """
        depth = 0
        while not self.at_end():
            tok = self.current()
            if tok.type == TokenType.LBRACE:
                depth += 1
            elif tok.type == TokenType.RBRACE:
                if depth <= 0:
                    # Don't consume - leave for the enclosing parse_block
                    return
                depth -= 1
            elif tok.type == TokenType.SEMICOLON and depth == 0:
                self.advance()
                return
            self.advance()

    # ========================================================================
    # Top-level declarations
    #
    # HSL programs are a sequence of top-level constructs: namespaces,
    # function/method declarations, variable declarations, scheduling
    # blocks, struct stubs, and bare statements.  ``parse_top_level()``
    # dispatches to the appropriate sub-parser based on the leading token.
    # ========================================================================

    def parse_top_level(self) -> Optional[ASTNode]:
        """Parse one top-level declaration or statement.

        Examines the current token and delegates to the matching production:

        =========================  ==============================
        Leading token(s)           Handler
        =========================  ==============================
        ``namespace``              ``parse_namespace()``
        ``private|static|global``  ``parse_qualified_declaration()``
        ``function``               ``parse_function_declaration()``
        ``method``                 ``parse_function_declaration(is_method=True)``
        type keywords              ``parse_variable_declaration()``
        ``scheduleronly``          block → ``SchedulerOnlyBlock``
        ``executoronly``           block → ``ExecutorOnlyBlock``
        ``struct``                 ``_skip_struct_declaration()``
        anything else              ``parse_statement()``
        =========================  ==============================

        Returns
        -------
        ASTNode | None
            The parsed construct, or ``None`` for empty / skipped items.
        """
        self.skip_comments_and_markers()

        tok = self.current()

        # Namespace
        if tok.type == TokenType.NAMESPACE:
            return self.parse_namespace()

        # Function/method with qualifiers
        if tok.type in (TokenType.PRIVATE, TokenType.STATIC, TokenType.GLOBAL,
                        TokenType.SYNCHRONIZED):
            return self.parse_qualified_declaration()

        # Function declaration
        if tok.type == TokenType.FUNCTION:
            return self.parse_function_declaration()

        # Method declaration (entry point)
        if tok.type == TokenType.METHOD:
            return self.parse_function_declaration(is_method=True)

        if tok.type == TokenType.WORKFLOW:
            return self.parse_function_declaration(is_workflow=True)

        # Variable declaration
        if tok.type in (TokenType.VARIABLE, TokenType.STRING_TYPE, TokenType.SEQUENCE,
                        TokenType.DEVICE, TokenType.FILE_TYPE, TokenType.OBJECT,
                        TokenType.TIMER, TokenType.EVENT, TokenType.DIALOG,
                        TokenType.RESOURCE, TokenType.CHAR_TYPE, TokenType.SHORT_TYPE,
                        TokenType.LONG_TYPE, TokenType.FLOAT_TYPE,
                        TokenType.CONST):
            return self.parse_variable_declaration()

        # scheduleronly / executoronly blocks
        if tok.type == TokenType.SCHEDULERONLY:
            self.advance()
            body = self.parse_block()
            return SchedulerOnlyBlock(body=body, line=tok.line, column=tok.column)
        if tok.type == TokenType.EXECUTORONLY:
            self.advance()
            body = self.parse_block()
            return ExecutorOnlyBlock(body=body, line=tok.line, column=tok.column)

        # struct declaration (skip gracefully)
        if tok.type == TokenType.STRUCT:
            if self.strict:
                raise ParseError("struct values are not implemented by the simulator", tok)
            return self._skip_struct_declaration()

        # Bare statement at top level (includes assignments, function calls)
        return self.parse_statement()

    # ========================================================================
    # Namespace
    #
    # HSL namespaces group functions, variables and nested namespaces.
    # Syntax:  namespace <Name> { <body> }
    # ========================================================================

    def parse_namespace(self) -> NamespaceDeclaration:
        """Parse a namespace declaration.

        Grammar::

            namespace_decl = 'namespace' IDENT '{' { top_level } '}' ;

        Returns
        -------
        NamespaceDeclaration
        """
        tok = self.expect(TokenType.NAMESPACE)
        name_tok = self.expect(TokenType.IDENTIFIER)
        body = self.parse_block(enforce_declaration_order=False)
        return NamespaceDeclaration(
            name=name_tok.value, body=body,
            line=tok.line, column=tok.column, file=tok.file
        )

    # ========================================================================
    # Declarations with qualifiers
    #
    # HSL declarations may be prefixed with one or more qualifiers:
    # ``private``, ``static``, ``global``, ``const``.  These are consumed
    # first, then the actual declaration is parsed and the qualifier flags
    # are set on the resulting AST node.
    # ========================================================================

    def parse_qualified_declaration(self) -> ASTNode:
        """Parse a declaration preceded by ``private`` / ``static`` / ``global``.

        After consuming the qualifier tokens, the method dispatches to either
        ``parse_function_declaration()`` or ``parse_variable_declaration()``
        based on the next token, then stamps the qualifier flags onto the
        returned node.

        Returns
        -------
        ASTNode
            A ``FunctionDeclaration`` or ``VariableDeclaration`` with its
            ``is_private`` / ``is_static`` / ``is_global`` flags set.

        Raises
        ------
        ParseError
            If the token after the qualifiers is not a valid declaration
            keyword.
        """
        qualifiers = self._parse_qualifiers()

        tok = self.current()

        if tok.type == TokenType.FUNCTION:
            decl = self.parse_function_declaration()
            if isinstance(decl, FunctionDeclaration):
                decl.is_private = qualifiers.get('private', False)
                decl.is_static = qualifiers.get('static', False)
                decl.is_synchronized = qualifiers.get('synchronized', False)
            return decl

        if tok.type == TokenType.METHOD:
            decl = self.parse_function_declaration(is_method=True)
            return decl

        if tok.type in (TokenType.VARIABLE, TokenType.STRING_TYPE, TokenType.SEQUENCE,
                        TokenType.DEVICE, TokenType.FILE_TYPE, TokenType.OBJECT,
                        TokenType.TIMER, TokenType.EVENT, TokenType.DIALOG,
                        TokenType.RESOURCE, TokenType.CHAR_TYPE, TokenType.SHORT_TYPE,
                        TokenType.LONG_TYPE, TokenType.FLOAT_TYPE,
                        TokenType.CONST):
            decl = self.parse_variable_declaration()
            if isinstance(decl, VariableDeclaration):
                decl.is_private = qualifiers.get('private', False)
                decl.is_static = qualifiers.get('static', False)
                decl.is_global = qualifiers.get('global', False)
            return decl

        # Could be: static const variable ...
        if tok.type == TokenType.CONST:
            decl = self.parse_variable_declaration()
            if isinstance(decl, VariableDeclaration):
                decl.is_private = qualifiers.get('private', False)
                decl.is_static = qualifiers.get('static', False)
                decl.is_global = qualifiers.get('global', False)
                decl.is_const = True
            return decl

        raise ParseError(f"Unexpected token after qualifiers: {tok.value!r}", tok)

    def _parse_qualifiers(self) -> dict:
        """Consume and return any leading ``private|static|global|const`` tokens.

        Returns
        -------
        dict[str, bool]
            Keys ``'private'``, ``'static'``, ``'global'``, ``'const'`` -
            each ``True`` if the corresponding keyword was present.
        """
        quals = {'private': False, 'static': False, 'global': False,
                 'const': False, 'synchronized': False}
        while self.current().type in (TokenType.PRIVATE, TokenType.STATIC,
                                       TokenType.GLOBAL, TokenType.CONST,
                                       TokenType.SYNCHRONIZED):
            tok = self.advance()
            quals[tok.value] = True
        return quals

    # ========================================================================
    # Variable declaration
    #
    # HSL supports many typed variable declarations.  The general pattern is:
    #
    #   [qualifiers] <type> ['&'] IDENT ['[]'] ['(' init ')' | '=' init]
    #                        { ',' ... } ';'
    #
    # Type keywords: variable, string, sequence, device, file, object,
    #                timer, event, dialog, const.
    # ========================================================================

    def parse_variable_declaration(self) -> ASTNode:
        """Parse one or more variable declarations of the same type.

        Handles all HSL typed declarations including:

        * Simple:         ``variable x;``
        * Initialised:    ``variable x(0);``  or  ``variable x = 0;``
        * Array:          ``variable x[];``
        * Reference:      ``variable& x;``
        * Device:         ``device dev(\"layout\", \"name\", flag);``
        * Comma-list:     ``variable a(0), b(0), c[];``

        When multiple comma-separated names appear, a ``Block`` containing
        one ``VariableDeclaration`` per name is returned instead of a single
        node.

        Returns
        -------
        VariableDeclaration | Block
        """
        qualifiers = self._parse_qualifiers()

        tok = self.current()
        var_type = tok.value  # variable, string, sequence, etc.
        self.advance()

        declarations = []
        first = True
        while True:
            if not first:
                if not self.match(TokenType.COMMA):
                    break
            first = False

            # Handle reference: variable& name
            is_reference = False
            if self.match(TokenType.AMPERSAND):
                is_reference = True

            name_tok = self.expect(TokenType.IDENTIFIER)
            name = name_tok.value

            is_array = False
            initializer = None
            device_args = []
            constructor_args = []

            # Array declaration: variable name[]
            if self.match(TokenType.LBRACKET):
                is_array = True
                self.expect(TokenType.RBRACKET)

            # Constructor initialization. Resource and device declarations
            # commonly use multiple arguments; scalar types normally use one.
            if self.current().type == TokenType.LPAREN:
                self.advance()
                while self.current().type != TokenType.RPAREN and not self.at_end():
                    constructor_args.append(self.parse_expression())
                    if not self.match(TokenType.COMMA):
                        break
                self.expect(TokenType.RPAREN)
                if var_type == 'device':
                    device_args = constructor_args
                elif len(constructor_args) == 1:
                    initializer = constructor_args[0]
            elif self.match(TokenType.ASSIGN):
                initializer = self.parse_expression()

            decl = VariableDeclaration(
                name=name, var_type=var_type, is_array=is_array,
                initializer=initializer, is_reference=is_reference,
                is_private=qualifiers.get('private', False),
                is_static=qualifiers.get('static', False),
                is_global=qualifiers.get('global', False),
                is_const=qualifiers.get('const', False),
                device_args=device_args,
                constructor_args=constructor_args,
                line=tok.line, column=tok.column, file=tok.file
            )
            declarations.append(decl)

        if self.strict:
            self.expect(TokenType.SEMICOLON)
        else:
            self.match(TokenType.SEMICOLON)

        if len(declarations) == 1:
            return declarations[0]
        return Block(statements=declarations, line=tok.line, column=tok.column)

    # ========================================================================
    # Function / method declaration
    #
    # Grammar::
    #
    #     function_decl = ('function' | 'method') IDENT '(' params ')'
    #                     [ return_type ] ( block | ';' ) ;
    #     return_type   = ('variable' | 'string' | 'void' | IDENT) ['[]'] ;
    #     params        = param { ',' param } ;
    #     param         = [ type_kw ] ['&'] IDENT ['[]'] ;
    #
    # Forward declarations have no body and end with ';'.
    # The ``method`` keyword marks the function as a VENUS entry point.
    # ========================================================================

    def parse_function_declaration(self, is_method: bool = False,
                                   is_workflow: bool = False) -> FunctionDeclaration:
        """Parse a function or method declaration (with optional body).

        Parameters
        ----------
        is_method : bool
            ``True`` when the leading keyword was ``method`` (VENUS entry
            point), ``False`` for ``function``.

        Returns
        -------
        FunctionDeclaration
            If no body brace block follows (only ``;``), the node's ``body``
            is ``None`` (forward declaration).
        """
        tok = self.advance()  # consume 'function' or 'method'

        name_tok = self.expect(TokenType.IDENTIFIER)
        name_parts = [name_tok.value]
        while self.match(TokenType.SCOPE):
            name_parts.append(self.expect(TokenType.IDENTIFIER).value)
        name = "::".join(name_parts)

        # Parameters
        self.expect(TokenType.LPAREN)
        params = []
        while self.current().type != TokenType.RPAREN and not self.at_end():
            param = self._parse_parameter()
            params.append(param)
            if not self.match(TokenType.COMMA):
                break
        self.expect(TokenType.RPAREN)

        # Return type
        return_type = "void"
        if self.current().type in (
            TokenType.VARIABLE, TokenType.STRING_TYPE, TokenType.SEQUENCE,
            TokenType.DEVICE, TokenType.FILE_TYPE, TokenType.OBJECT,
            TokenType.TIMER, TokenType.EVENT, TokenType.DIALOG,
            TokenType.RESOURCE, TokenType.CHAR_TYPE, TokenType.SHORT_TYPE,
            TokenType.LONG_TYPE, TokenType.FLOAT_TYPE, TokenType.VOID,
            TokenType.IDENTIFIER,
        ):
            return_type = self.advance().value
            # Handle array return type: variable[]
            if self.current().type == TokenType.LBRACKET:
                self.advance()  # [
                self.match(TokenType.RBRACKET)  # ]
                return_type += "[]"

        # Body or forward declaration
        body = None
        if self.current().type == TokenType.LBRACE:
            body = self.parse_block()
        elif self.match(TokenType.SEMICOLON):
            pass  # forward declaration
        elif self.current().type == TokenType.SEMICOLON:
            self.advance()

        return FunctionDeclaration(
            name=name, parameters=params, return_type=return_type,
            body=body, is_method=is_method, is_workflow=is_workflow,
            line=tok.line, column=tok.column, file=tok.file
        )

    def _parse_parameter(self) -> Parameter:
        """Parse a single function/method parameter.

        Grammar::

            param = [ type_kw ] ['&'] IDENT ['[]'] ;

        The type keyword defaults to ``"variable"`` if omitted.  An ``&``
        before the name marks a pass-by-reference parameter.  Trailing
        ``[]`` marks an array parameter.

        Returns
        -------
        Parameter
        """
        self.skip_comments_and_markers()
        tok = self.current()
        param_type = "variable"

        if tok.type in (TokenType.VARIABLE, TokenType.STRING_TYPE, TokenType.SEQUENCE,
                        TokenType.DEVICE, TokenType.FILE_TYPE, TokenType.OBJECT,
                        TokenType.TIMER, TokenType.EVENT, TokenType.DIALOG,
                        TokenType.RESOURCE, TokenType.CHAR_TYPE, TokenType.SHORT_TYPE,
                        TokenType.LONG_TYPE, TokenType.FLOAT_TYPE):
            param_type = self.advance().value

        self.skip_comments_and_markers()
        is_reference = bool(self.match(TokenType.AMPERSAND))
        self.skip_comments_and_markers()

        name_tok = self.expect(TokenType.IDENTIFIER)

        is_array = False
        if self.match(TokenType.LBRACKET):
            is_array = True
            self.match(TokenType.RBRACKET)

        return Parameter(
            name=name_tok.value, param_type=param_type,
            is_reference=is_reference, is_array=is_array,
            line=tok.line, column=tok.column
        )

    # ========================================================================
    # Statements
    #
    # HSL statements closely mirror C: blocks, if/else, for, while, loop,
    # break, continue, return, abort, pause, error handling (onerror /
    # resume next / labels), scheduling blocks, struct stubs, runtime
    # includes (``<<``), and expression statements.  Variable and function
    # declarations may also appear inside blocks.
    # ========================================================================

    def parse_block(self, enforce_declaration_order: bool = True) -> Block:
        """Parse a brace-enclosed block of statements.

        Grammar::

            block = '{' { statement } '}' ;

        Each statement is individually wrapped in a ``try``/``except`` so
        that a single bad statement triggers ``_recover_in_block()`` and
        parsing continues with the next statement.

        Returns
        -------
        Block
        """
        tok = self.expect(TokenType.LBRACE)
        stmts = []
        seen_executable = False

        while self.current().type != TokenType.RBRACE and not self.at_end():
            self.skip_comments_and_markers()
            if self.current().type == TokenType.RBRACE or self.at_end():
                break
            try:
                statement_token = self.current()
                stmt = self.parse_statement()
                if stmt is not None:
                    is_variable_declaration = isinstance(stmt, VariableDeclaration) or (
                        isinstance(stmt, Block)
                        and bool(stmt.statements)
                        and all(isinstance(item, VariableDeclaration) for item in stmt.statements)
                    )
                    if (
                        self.strict
                        and enforce_declaration_order
                        and is_variable_declaration
                        and seen_executable
                    ):
                        raise ParseError(
                            "Variable declarations must appear before executable statements in the same block",
                            statement_token,
                        )
                    if not is_variable_declaration:
                        seen_executable = True
                    stmts.append(stmt)
            except ParseError as e:
                self.errors.append(e)
                if self.strict:
                    raise
                self._recover_in_block()

        self.expect(TokenType.RBRACE)
        return Block(statements=stmts, line=tok.line, column=tok.column)

    def parse_statement(self) -> Optional[ASTNode]:
        """Parse a single statement and return its AST node.

        Dispatches based on the leading token:

        ===========================  ==============================
        Token(s)                     Result
        ===========================  ==============================
        ``;``                        ``None`` (empty statement)
        ``{``                        ``parse_block()``
        type keywords                ``parse_variable_declaration()``
        ``private|static|global``    ``parse_qualified_declaration()``
        ``const``                    ``parse_variable_declaration()``
        ``namespace``                ``parse_namespace()``
        ``function``                 ``parse_function_declaration()``
        ``method``                   ``parse_function_declaration(is_method=True)``
        ``if``                       ``parse_if()``
        ``for``                      ``parse_for()``
        ``while``                    ``parse_while()``
        ``loop``                     ``parse_loop()``
        ``break``                    ``BreakStatement``
        ``return``                   ``parse_return()``
        ``abort``                    ``AbortStatement``
        ``pause``                    ``PauseStatement``
        ``onerror``                  ``parse_onerror()``
        ``resume`` ``next``          ``ResumeNext``
        ``scheduleronly``            ``SchedulerOnlyBlock``
        ``executoronly``             ``ExecutorOnlyBlock``
        ``struct``                   ``_skip_struct_declaration()``
        ``<<``                       runtime include (skipped)
        ``\\``                       line continuation (skipped)
        IDENT ``:``                  ``Label`` (error handler target)
        other                        ``parse_expression_statement()``
        ===========================  ==============================

        Returns
        -------
        ASTNode | None
        """
        self.skip_comments_and_markers()
        tok = self.current()

        if tok.type == TokenType.EOF:
            return None

        # Empty statement
        if tok.type == TokenType.SEMICOLON:
            self.advance()
            return None

        # Block
        if tok.type == TokenType.LBRACE:
            return self.parse_block()

        # Variable declaration
        if tok.type in (TokenType.VARIABLE, TokenType.STRING_TYPE, TokenType.SEQUENCE,
                        TokenType.DEVICE, TokenType.FILE_TYPE, TokenType.OBJECT,
                        TokenType.TIMER, TokenType.EVENT, TokenType.DIALOG,
                        TokenType.RESOURCE, TokenType.CHAR_TYPE, TokenType.SHORT_TYPE,
                        TokenType.LONG_TYPE, TokenType.FLOAT_TYPE):
            return self.parse_variable_declaration()

        # Qualified declarations inside functions
        if tok.type in (TokenType.PRIVATE, TokenType.STATIC, TokenType.GLOBAL,
                        TokenType.SYNCHRONIZED):
            return self.parse_qualified_declaration()

        # const
        if tok.type == TokenType.CONST:
            return self.parse_variable_declaration()

        # Namespace (can appear inside blocks in HSL)
        if tok.type == TokenType.NAMESPACE:
            return self.parse_namespace()

        # Function declaration (can appear inside namespace blocks)
        if tok.type == TokenType.FUNCTION:
            return self.parse_function_declaration()

        # Method declaration
        if tok.type == TokenType.METHOD:
            return self.parse_function_declaration(is_method=True)

        if tok.type == TokenType.WORKFLOW:
            return self.parse_function_declaration(is_workflow=True)

        # Control flow
        if tok.type == TokenType.IF:
            return self.parse_if()
        if tok.type == TokenType.FOR:
            return self.parse_for()
        if tok.type == TokenType.WHILE:
            return self.parse_while()
        if tok.type == TokenType.LOOP:
            return self.parse_loop()
        if tok.type == TokenType.BREAK:
            self.advance()
            if self.strict:
                self.expect(TokenType.SEMICOLON)
            else:
                self.match(TokenType.SEMICOLON)
            return BreakStatement(line=tok.line, column=tok.column)
        if tok.type == TokenType.RETURN:
            return self.parse_return()
        if tok.type == TokenType.ABORT:
            self.advance()
            if self.strict:
                self.expect(TokenType.SEMICOLON)
            else:
                self.match(TokenType.SEMICOLON)
            return AbortStatement(line=tok.line, column=tok.column)
        if tok.type == TokenType.PAUSE:
            self.advance()
            if self.strict:
                self.expect(TokenType.SEMICOLON)
            else:
                self.match(TokenType.SEMICOLON)
            return PauseStatement(line=tok.line, column=tok.column)
        if tok.type == TokenType.LOCK:
            self.advance()
            self.expect(TokenType.SEMICOLON)
            return LockStatement(line=tok.line, column=tok.column)
        if tok.type == TokenType.UNLOCK:
            self.advance()
            self.expect(TokenType.SEMICOLON)
            return UnlockStatement(line=tok.line, column=tok.column)

        # Error handling
        if tok.type == TokenType.ONERROR:
            return self.parse_onerror()
        if tok.type == TokenType.RESUME:
            self.advance()
            self.expect(TokenType.NEXT)
            if self.strict:
                self.expect(TokenType.SEMICOLON)
            else:
                self.match(TokenType.SEMICOLON)
            return ResumeNext(line=tok.line, column=tok.column)

        # scheduleronly / executoronly
        if tok.type == TokenType.SCHEDULERONLY:
            self.advance()
            body = self.parse_block()
            return SchedulerOnlyBlock(body=body, line=tok.line, column=tok.column)
        if tok.type == TokenType.EXECUTORONLY:
            self.advance()
            body = self.parse_block()
            return ExecutorOnlyBlock(body=body, line=tok.line, column=tok.column)
        if tok.type in (TokenType.ACTIVITY, TokenType.ACTIONBLOCK):
            return self.parse_scheduler_block()

        # struct - skip the entire struct declaration gracefully
        if tok.type == TokenType.STRUCT:
            if self.strict:
                raise ParseError("struct values are not implemented by the simulator", tok)
            return self._skip_struct_declaration()

        # loop(N) { body }
        if tok.type == TokenType.LOOP:
            return self.parse_loop()

        # Runtime include: << "file.hsl"
        if tok.type == TokenType.LSHIFT:
            self.advance()
            source = self.parse_expression()
            if self.strict:
                self.expect(TokenType.SEMICOLON)
            else:
                self.match(TokenType.SEMICOLON)
            return RuntimeExecute(
                source=source, line=tok.line, column=tok.column, file=tok.file
            )

        # Backslash (line continuation or path separator) - skip
        if tok.type == TokenType.BACKSLASH:
            self.advance()
            return None

        # Label statement: identifier : { block }  (error handler labels)
        if tok.type == TokenType.IDENTIFIER and self.peek_type(1) == TokenType.COLON:
            label_name = self.advance().value  # consume identifier
            self.advance()  # consume colon
            body = None
            if self.current().type == TokenType.LBRACE:
                body = self.parse_block()
            return Label(name=label_name, body=body, line=tok.line, column=tok.column)

        # Expression statement (assignment, function call, etc.)
        return self.parse_expression_statement()

    def parse_if(self) -> IfStatement:
        """Parse an if / else-if / else statement.

        Grammar::

            if_stmt = 'if' '(' expr ')' stmt_or_block
                      [ 'else' stmt_or_block ] ;

        The ``else`` clause is optional.  Chained ``else if`` is handled
        naturally because the ``else`` branch parses another statement
        which may itself be an ``if``.

        Returns
        -------
        IfStatement
        """
        tok = self.expect(TokenType.IF)
        self.expect(TokenType.LPAREN)
        condition = self.parse_expression()
        self.expect(TokenType.RPAREN)

        then_block = self.parse_statement_or_block()

        else_block = None
        self.skip_comments_and_markers()
        if self.match(TokenType.ELSE):
            else_block = self.parse_statement_or_block()

        return IfStatement(
            condition=condition, then_block=then_block, else_block=else_block,
            line=tok.line, column=tok.column
        )

    def parse_for(self) -> ForLoop:
        """Parse a C-style for loop.

        Grammar::

            for_stmt = 'for' '(' [init] ';' [cond] ';' [update] ')'
                       stmt_or_block ;

        Each of the three clauses (initialiser, condition, update) is
        optional.  The initialiser may be either a variable declaration
        (via ``parse_expression_or_decl()``) or any expression.

        Returns
        -------
        ForLoop
        """
        tok = self.expect(TokenType.FOR)
        self.expect(TokenType.LPAREN)

        # Init
        init = None
        if self.current().type != TokenType.SEMICOLON:
            init = self.parse_expression_or_decl()
        self.expect(TokenType.SEMICOLON)

        # Condition
        condition = None
        if self.current().type != TokenType.SEMICOLON:
            condition = self.parse_expression()
        self.expect(TokenType.SEMICOLON)

        # Update
        update = None
        if self.current().type != TokenType.RPAREN:
            update = self.parse_expression()
        self.expect(TokenType.RPAREN)

        body = self.parse_statement_or_block()

        return ForLoop(
            init=init, condition=condition, update=update, body=body,
            line=tok.line, column=tok.column
        )

    def parse_while(self) -> WhileLoop:
        """Parse a while loop.

        Grammar::

            while_stmt = 'while' '(' expr ')' stmt_or_block ;

        Returns
        -------
        WhileLoop
        """
        tok = self.expect(TokenType.WHILE)
        self.expect(TokenType.LPAREN)
        condition = self.parse_expression()
        self.expect(TokenType.RPAREN)
        body = self.parse_statement_or_block()
        return WhileLoop(
            condition=condition, body=body,
            line=tok.line, column=tok.column
        )

    def parse_loop(self):
        """Parse an HSL ``loop`` statement.

        Grammar::

            loop_stmt = 'loop' '(' expr ')' stmt_or_block ;

        The expression gives the number of iterations.  Unlike ``while``,
        the count is evaluated once before the loop begins.

        Returns
        -------
        LoopStatement
        """
        tok = self.expect(TokenType.LOOP)
        self.expect(TokenType.LPAREN)
        count = self.parse_expression()
        self.expect(TokenType.RPAREN)
        body = self.parse_statement_or_block()
        return LoopStatement(
            count=count, body=body,
            line=tok.line, column=tok.column
        )

    def _skip_struct_declaration(self):
        """Skip over a ``struct`` declaration without producing an AST node.

        HSL supports ``struct`` types, but they are not needed for
        simulation.  This method gracefully consumes the ``struct`` keyword,
        an optional name, an optional brace-enclosed body, and a trailing
        semicolon so the parser can continue.

        Returns
        -------
        None
        """
        tok = self.advance()  # consume 'struct'
        # Skip the struct name if present
        if self.current().type == TokenType.IDENTIFIER:
            self.advance()
        # Skip the body block if present
        if self.current().type == TokenType.LBRACE:
            self._skip_braces()
        self.match(TokenType.SEMICOLON)
        return None

    def _skip_braces(self):
        """Consume a balanced ``{ … }`` region, including nested braces.

        Used by ``_skip_struct_declaration()`` to jump over a struct body
        without attempting to parse its contents.
        """
        depth = 0
        if self.current().type == TokenType.LBRACE:
            depth = 1
            self.advance()
        while depth > 0 and not self.at_end():
            if self.current().type == TokenType.LBRACE:
                depth += 1
            elif self.current().type == TokenType.RBRACE:
                depth -= 1
            self.advance()

    def parse_return(self) -> ReturnStatement:
        """Parse a return statement.

        HSL allows three syntactic forms::

            return;
            return(expr);
            return expr;

        All three are normalised into a ``ReturnStatement`` whose ``value``
        is either an expression node or ``None``.

        Returns
        -------
        ReturnStatement
        """
        tok = self.expect(TokenType.RETURN)
        value = None
        if self.match(TokenType.LPAREN):
            if self.current().type != TokenType.RPAREN:
                value = self.parse_expression()
            self.expect(TokenType.RPAREN)
        elif self.current().type not in (TokenType.SEMICOLON, TokenType.RBRACE, TokenType.EOF):
            value = self.parse_expression()
        if self.strict:
            self.expect(TokenType.SEMICOLON)
        else:
            self.match(TokenType.SEMICOLON)
        return ReturnStatement(value=value, line=tok.line, column=tok.column)

    def parse_onerror(self) -> ASTNode:
        """Parse an ``onerror goto`` statement.

        Grammar::

            onerror_stmt = 'onerror' 'goto' ( IDENT | INTEGER ) ';' ;

        ``onerror goto 0`` disables the current error handler.  Any other
        identifier names the error-handler label to jump to.

        Returns
        -------
        OnErrorGoto
        """
        tok = self.expect(TokenType.ONERROR)
        if self.match(TokenType.RESUME):
            self.expect(TokenType.NEXT)
            self.expect(TokenType.SEMICOLON)
            return OnErrorResumeNext(line=tok.line, column=tok.column)

        self.expect(TokenType.GOTO)
        label = ""
        if self.current().type == TokenType.INTEGER:
            label = self.advance().value
        elif self.current().type == TokenType.IDENTIFIER:
            label = self.advance().value
        if self.strict:
            self.expect(TokenType.SEMICOLON)
        else:
            self.match(TokenType.SEMICOLON)
        return OnErrorGoto(label=label, line=tok.line, column=tok.column)

    def parse_scheduler_block(self) -> SchedulerBlock:
        """Parse activity(...) { ... } and actionblock(...) { ... }."""
        tok = self.advance()
        self.expect(TokenType.LPAREN)
        arguments = []
        while self.current().type != TokenType.RPAREN and not self.at_end():
            arguments.append(self.parse_expression())
            if not self.match(TokenType.COMMA):
                break
        self.expect(TokenType.RPAREN)
        body = self.parse_block()
        return SchedulerBlock(
            kind=tok.value, arguments=arguments, body=body,
            line=tok.line, column=tok.column, file=tok.file
        )

    def parse_statement_or_block(self) -> ASTNode:
        """Parse either a ``{ block }`` or a single statement.

        Used after control-flow keywords (``if``, ``for``, ``while``,
        ``loop``) where HSL allows either form.  If the result is
        ``None`` (e.g. an empty statement), an empty ``Block`` is returned
        to keep the AST uniform.

        Returns
        -------
        ASTNode
        """
        self.skip_comments_and_markers()
        if self.current().type == TokenType.LBRACE:
            return self.parse_block()
        stmt = self.parse_statement()
        if stmt is None:
            return Block(statements=[])
        return stmt

    def parse_expression_or_decl(self) -> ASTNode:
        """Parse an expression **or** a variable declaration.

        This is needed for the *init* clause of ``for`` loops, which may
        be either ``variable i = 0`` or a plain expression like ``i = 0``.

        Returns
        -------
        ASTNode
        """
        if self.current().type in (TokenType.VARIABLE, TokenType.STRING_TYPE):
            return self.parse_variable_declaration()
        return self.parse_expression()

    def parse_expression_statement(self) -> Optional[ASTNode]:
        """Parse an expression-level statement optionally followed by ``= expr``.

        If the expression is followed by ``=``, the whole construct is
        treated as an ``Assignment`` (``target = value``).  Otherwise it
        is wrapped in an ``ExpressionStatement``.  A trailing semicolon
        is consumed if present.

        Returns
        -------
        Assignment | ExpressionStatement | None
        """
        expr = self.parse_expression()
        if expr is None:
            return None

        # Check for assignment
        if self.match(TokenType.ASSIGN):
            value = self.parse_expression()
            self.match(TokenType.SEMICOLON)
            return Assignment(
                target=expr, value=value,
                line=expr.line, column=expr.column
            )

        self.match(TokenType.SEMICOLON)
        return ExpressionStatement(expression=expr, line=expr.line, column=expr.column)

    # ========================================================================
    # Expression parsing (precedence climbing)
    #
    # Expressions are parsed using a set of mutually-recursive methods,
    # one per precedence level.  Each method parses its own operators and
    # delegates to the next-higher-precedence method for operands.
    #
    # Precedence (lowest → highest):
    #
    #   1.  Assignment       =         (right-associative, in parse_expression)
    #   2.  Logical OR       ||        (_parse_or_expr)
    #   3.  Logical AND      &&        (_parse_and_expr)
    #   4.  Bitwise OR       |         (_parse_bitor_expr)
    #   5.  Bitwise AND      &         (_parse_bitand_expr)
    #   6.  Equality         ==  !=    (_parse_equality_expr)
    #   7.  Comparison       < > <= >= (_parse_comparison_expr)
    #   8.  Additive         +  -      (_parse_additive_expr)
    #   9.  Multiplicative   *  /  %   (_parse_multiplicative_expr)
    #  10.  Power            ^         (_parse_power_expr, right-assoc)
    #  11.  Unary prefix     - ! ++ -- (_parse_unary_expr)
    #  12.  Postfix          ++ -- . [] () ::  (_parse_postfix_expr)
    #  13.  Primary          literals, identifiers, (expr)
    # ========================================================================

    def parse_expression(self) -> ASTNode:
        """Parse a full expression, handling top-level assignment.

        Assignment (``=``) is right-associative and has the lowest
        precedence.  If the token following the first sub-expression is
        ``=``, the left-hand side is used as the assignment target.

        Returns
        -------
        ASTNode
            The root of the expression sub-tree.
        """
        left = self._parse_or_expr()

        # Assignment as expression (for_init, etc.)
        if self.current().type == TokenType.ASSIGN:
            self.advance()
            value = self.parse_expression()
            return Assignment(target=left, value=value,
                            line=left.line, column=left.column)

        return left

    def _parse_or_expr(self) -> ASTNode:
        """Precedence level 2: logical OR (``||``, left-associative)."""
        left = self._parse_and_expr()
        while self.current().type == TokenType.OR:
            op = self.advance().value
            right = self._parse_and_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_and_expr(self) -> ASTNode:
        """Precedence level 3: logical AND (``&&``, left-associative)."""
        left = self._parse_bitor_expr()
        while self.current().type == TokenType.AND:
            op = self.advance().value
            right = self._parse_bitor_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_bitor_expr(self) -> ASTNode:
        """Precedence level 4: bitwise OR (``|``, left-associative)."""
        left = self._parse_bitand_expr()
        while self.current().type == TokenType.PIPE:
            op = self.advance().value
            right = self._parse_bitand_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_bitand_expr(self) -> ASTNode:
        """Precedence level 5: bitwise AND (``&``, left-associative).

        Note: ``&`` also serves as the reference operator in declarations
        (``variable& name``), but references are handled in
        ``parse_variable_declaration`` / ``_parse_parameter`` *before*
        expression parsing is reached, so there is no ambiguity here.
        """
        left = self._parse_equality_expr()
        while self.current().type == TokenType.AMPERSAND:
            op = self.advance().value
            right = self._parse_equality_expr()
            left = BinaryOp(operator='&', left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_equality_expr(self) -> ASTNode:
        """Precedence level 6: equality (``==``, ``!=``, left-associative)."""
        left = self._parse_comparison_expr()
        while self.current().type in (TokenType.EQ, TokenType.NEQ):
            op = self.advance().value
            right = self._parse_comparison_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_comparison_expr(self) -> ASTNode:
        """Precedence level 7: relational comparison (``< > <= >=``)."""
        left = self._parse_additive_expr()
        while self.current().type in (TokenType.LT, TokenType.GT, TokenType.LTE, TokenType.GTE):
            op = self.advance().value
            right = self._parse_additive_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_additive_expr(self) -> ASTNode:
        """Precedence level 8: addition and subtraction (``+``, ``-``)."""
        left = self._parse_multiplicative_expr()
        while self.current().type in (TokenType.PLUS, TokenType.MINUS):
            op = self.advance().value
            right = self._parse_multiplicative_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_multiplicative_expr(self) -> ASTNode:
        """Precedence level 9: multiplication, division, modulo (``*``, ``/``, ``%``)."""
        left = self._parse_power_expr()
        while self.current().type in (TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            op = self.advance().value
            right = self._parse_power_expr()
            left = BinaryOp(operator=op, left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_power_expr(self) -> ASTNode:
        """Precedence level 10: exponentiation (``^``, **right-associative**).

        Right-associativity is achieved by recursing into ``_parse_power_expr``
        for the right operand (rather than looping).  This means
        ``2 ^ 3 ^ 4`` is parsed as ``2 ^ (3 ^ 4)``.
        """
        left = self._parse_unary_expr()
        if self.current().type == TokenType.CARET:
            self.advance()
            right = self._parse_power_expr()  # right-associative
            left = BinaryOp(operator='^', left=left, right=right,
                           line=left.line, column=left.column)
        return left

    def _parse_unary_expr(self) -> ASTNode:
        """Precedence level 11: prefix unary operators.

        Handles ``-expr``, ``!expr`` (both recursive into ``_parse_unary_expr``),
        and ``++expr``, ``--expr`` (which delegate to ``_parse_postfix_expr``
        for the operand to avoid double-prefix ambiguity).
        """
        tok = self.current()

        # Prefix operators
        if tok.type == TokenType.MINUS:
            self.advance()
            operand = self._parse_unary_expr()
            return UnaryOp(operator='-', operand=operand, prefix=True,
                          line=tok.line, column=tok.column)
        if tok.type == TokenType.NOT:
            self.advance()
            operand = self._parse_unary_expr()
            return UnaryOp(operator='!', operand=operand, prefix=True,
                          line=tok.line, column=tok.column)
        if tok.type == TokenType.INCREMENT:
            self.advance()
            operand = self._parse_postfix_expr()
            return UnaryOp(operator='++', operand=operand, prefix=True,
                          line=tok.line, column=tok.column)
        if tok.type == TokenType.DECREMENT:
            self.advance()
            operand = self._parse_postfix_expr()
            return UnaryOp(operator='--', operand=operand, prefix=True,
                          line=tok.line, column=tok.column)

        return self._parse_postfix_expr()

    def _parse_postfix_expr(self) -> ASTNode:
        """Precedence level 12: postfix / access operators.

        After parsing a primary expression, this method loops to consume
        any number of postfix operations:

        * ``++`` / ``--``    - postfix increment / decrement
        * ``.member``        - member access (or ``.method(args)`` call)
        * ``[index]``        - array subscript
        * ``(args)``         - function call (only if the base is an
          ``Identifier``, ``ScopedName``, or ``MemberAccess``)
        * ``::name``         - scope resolution → ``ScopedName``

        The loop terminates when none of these operators follow.
        """
        expr = self._parse_primary_expr()

        while True:
            # Postfix ++/--
            if self.current().type == TokenType.INCREMENT:
                self.advance()
                expr = UnaryOp(operator='++', operand=expr, prefix=False,
                              line=expr.line, column=expr.column)
                continue

            if self.current().type == TokenType.DECREMENT:
                self.advance()
                expr = UnaryOp(operator='--', operand=expr, prefix=False,
                              line=expr.line, column=expr.column)
                continue

            # Member access: expr.member
            if self.current().type == TokenType.DOT:
                self.advance()
                member_tok = self.expect(TokenType.IDENTIFIER)
                # Check if it's a method call: expr.method(args)
                if self.current().type == TokenType.LPAREN:
                    self.advance()
                    args = self._parse_argument_list()
                    self.expect(TokenType.RPAREN)
                    expr = MethodCall(
                        object=expr, method=member_tok.value, arguments=args,
                        line=expr.line, column=expr.column
                    )
                else:
                    expr = MemberAccess(
                        object=expr, member=member_tok.value,
                        line=expr.line, column=expr.column
                    )
                continue

            # Array access: expr[index]
            if self.current().type == TokenType.LBRACKET:
                self.advance()
                index = self.parse_expression()
                self.expect(TokenType.RBRACKET)
                expr = ArrayAccess(
                    array=expr, index=index,
                    line=expr.line, column=expr.column
                )
                continue

            # Function call: expr(args) - but only if expr is an identifier/scoped name
            if self.current().type == TokenType.LPAREN:
                if isinstance(expr, (Identifier, ScopedName, MemberAccess)):
                    self.advance()
                    args = self._parse_argument_list()
                    self.expect(TokenType.RPAREN)
                    expr = FunctionCall(
                        function=expr, arguments=args,
                        line=expr.line, column=expr.column
                    )
                    continue
                else:
                    break

            # Scope resolution: expr::name
            if self.current().type == TokenType.SCOPE:
                # Build scoped name
                parts = []
                if isinstance(expr, Identifier):
                    parts.append(expr.name)
                elif isinstance(expr, ScopedName):
                    parts.extend(expr.parts)
                else:
                    break

                while self.match(TokenType.SCOPE):
                    next_tok = self.current()
                    if next_tok.type == TokenType.IDENTIFIER:
                        parts.append(self.advance().value)
                    elif next_tok.type in KEYWORDS.values().__class__.__mro__:
                        # Sometimes keywords are used as namespace members
                        parts.append(self.advance().value)
                    else:
                        break

                expr = ScopedName(parts=parts, line=expr.line, column=expr.column)

                # Check for function call after scoped name
                if self.current().type == TokenType.LPAREN:
                    self.advance()
                    args = self._parse_argument_list()
                    self.expect(TokenType.RPAREN)
                    expr = FunctionCall(
                        function=expr, arguments=args,
                        line=expr.line, column=expr.column
                    )
                continue

            break

        return expr

    def _parse_primary_expr(self) -> ASTNode:
        """Precedence level 13 - the innermost expressions.

        Matches one of:

        * **Integer literal** - decimal or hexadecimal (``0x`` prefix).
        * **Float literal** - decimal with optional scientific notation.
        * **String literal** - ``"..."``.
        * **Identifier** - optionally followed by ``::`` for a scoped name.
        * **Global-scope reference** - ``::name``.
        * **Parenthesised expression** - ``( expr )``.

        Raises ``ParseError`` if no production matches.

        Returns
        -------
        ASTNode
        """
        tok = self.current()

        # Integer literal
        if tok.type == TokenType.INTEGER:
            self.advance()
            try:
                if tok.value.startswith('0x') or tok.value.startswith('0X'):
                    val = int(tok.value, 16)
                else:
                    val = int(tok.value)
            except ValueError:
                val = 0
            return IntegerLiteral(value=val, line=tok.line, column=tok.column)

        # Float literal
        if tok.type == TokenType.FLOAT:
            self.advance()
            try:
                val = float(tok.value)
            except ValueError:
                val = 0.0
            return FloatLiteral(value=val, line=tok.line, column=tok.column)

        # String literal
        if tok.type == TokenType.STRING:
            self.advance()
            return StringLiteral(value=tok.value, line=tok.line, column=tok.column)

        # Identifier (may be followed by :: for scoped name)
        if tok.type == TokenType.IDENTIFIER:
            self.advance()
            ident = Identifier(name=tok.value, line=tok.line, column=tok.column)

            # Check for scope resolution
            if self.current().type == TokenType.SCOPE:
                parts = [tok.value]
                while self.match(TokenType.SCOPE):
                    next_tok = self.current()
                    if next_tok.type == TokenType.IDENTIFIER:
                        parts.append(self.advance().value)
                    else:
                        break
                return ScopedName(parts=parts, line=tok.line, column=tok.column)

            return ident

        # Global scope: ::function
        if tok.type == TokenType.SCOPE:
            self.advance()
            name_tok = self.expect(TokenType.IDENTIFIER)
            return ScopedName(parts=["", name_tok.value], line=tok.line, column=tok.column)

        # Parenthesized expression
        if tok.type == TokenType.LPAREN:
            self.advance()
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return expr

        # Handle keywords that might appear as identifiers in some contexts
        # (e.g., hslTrue is technically an identifier)
        if tok.type in (TokenType.IDENTIFIER,):
            self.advance()
            return Identifier(name=tok.value, line=tok.line, column=tok.column)

        raise ParseError(f"Unexpected token in expression: {tok.type.name} ({tok.value!r})", tok)

    def _parse_argument_list(self) -> list[ASTNode]:
        """Parse a comma-separated list of argument expressions.

        Called *after* the opening ``(`` has been consumed.  Returns an
        empty list when the next token is ``)``.

        Returns
        -------
        list[ASTNode]
            Zero or more argument expression nodes.
        """
        args = []
        if self.current().type == TokenType.RPAREN:
            return args

        args.append(self.parse_expression())
        while self.match(TokenType.COMMA):
            args.append(self.parse_expression())
        return args
