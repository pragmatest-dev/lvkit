"""Recursive-descent parser for the LabVIEW Formula Node language.

Built to NI's documented grammar (operators, precedence, control flow), not
to any particular sample. Anything outside the supported grammar raises
FormulaTranspileError with a location, so an unhandled construct fails loud
rather than producing wrong code.

Operator precedence and associativity follow the Formula Node docs:
``**`` is right-associative and binds tighter than unary minus (so
``-2**15`` is ``-(2**15)``); all other binary operators are left-associative.
"""

from __future__ import annotations

from . import FormulaTranspileError
from .lexer import Token, tokenize
from .nodes import (
    Assign,
    Binary,
    Block,
    Call,
    Decl,
    DoWhile,
    Empty,
    Expr,
    ExprStmt,
    For,
    If,
    IncDec,
    Index,
    Num,
    Stmt,
    Unary,
    Var,
    While,
)

# Numeric type keywords that may open a declaration. ``float`` is a LabVIEW
# alias for float64. ``int`` is intentionally absent — it is a function.
TYPE_KEYWORDS: frozenset[str] = frozenset({
    "int8", "int16", "int32", "int64",
    "uInt8", "uInt16", "uInt32", "uInt64",
    "float32", "float64", "float",
})

# Left-associative binary operators by precedence (higher binds tighter).
# ``**`` is handled separately (right-assoc, above unary).
_BINARY_PREC: dict[str, int] = {
    "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5,
    "==": 6, "!=": 6,
    "<": 7, "<=": 7, ">": 7, ">=": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}

_UNARY_OPS = frozenset({"-", "+", "!", "~"})


class Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.pos = 0

    # --- token helpers ---

    def _peek(self) -> Token | None:
        return self.toks[self.pos] if self.pos < len(self.toks) else None

    def _at_end(self) -> bool:
        return self.pos >= len(self.toks)

    def _is(self, value: str) -> bool:
        t = self._peek()
        return t is not None and t.value == value

    def _next(self) -> Token:
        t = self._peek()
        if t is None:
            last = self.toks[-1] if self.toks else None
            raise FormulaTranspileError(
                "unexpected end of script",
                last.line if last else None,
                last.col if last else None,
            )
        self.pos += 1
        return t

    def _expect(self, value: str) -> Token:
        t = self._peek()
        if t is None or t.value != value:
            where = t or (self.toks[-1] if self.toks else None)
            got = "end of script" if t is None else repr(t.value)
            raise FormulaTranspileError(
                f"expected {value!r} but got {got}",
                where.line if where else None,
                where.col if where else None,
            )
        return self._next()

    def _err(self, msg: str, tok: Token | None = None) -> FormulaTranspileError:
        t = tok or self._peek() or (self.toks[-1] if self.toks else None)
        return FormulaTranspileError(
            msg, t.line if t else None, t.col if t else None
        )

    # --- entry ---

    def parse_program(self) -> Block:
        stmts: list[Stmt] = []
        while not self._at_end():
            stmts.append(self.parse_stmt())
        return Block(stmts)

    # --- statements ---

    def parse_stmt(self) -> Stmt:
        t = self._peek()
        if t is None:
            raise self._err("unexpected end of script")

        if t.value == ";":
            self._next()
            return Empty()
        if t.value == "{":
            return self._parse_block()
        if t.kind == "ident":
            if t.value in TYPE_KEYWORDS and self._is_decl():
                return self._parse_decl()
            if t.value == "if":
                return self._parse_if()
            if t.value == "for":
                return self._parse_for()
            if t.value == "while":
                return self._parse_while()
            if t.value == "do":
                return self._parse_do()
            if t.value in ("switch", "case", "default", "break", "continue", "goto"):
                raise self._err(f"unsupported statement keyword {t.value!r}", t)
        return self._parse_simple_stmt(require_semi=True)

    def _is_decl(self) -> bool:
        # A declaration is a type keyword followed by an identifier (the name).
        nxt = self.toks[self.pos + 1] if self.pos + 1 < len(self.toks) else None
        return nxt is not None and nxt.kind == "ident"

    def _parse_block(self) -> Block:
        self._expect("{")
        stmts: list[Stmt] = []
        while not self._is("}"):
            if self._at_end():
                raise self._err("unterminated '{' block")
            stmts.append(self.parse_stmt())
        self._expect("}")
        return Block(stmts)

    def _parse_decl(self) -> Decl:
        type_kw = self._next().value
        items: list[tuple[str, Expr | None]] = []
        while True:
            name_tok = self._next()
            if name_tok.kind != "ident":
                raise self._err("expected variable name in declaration", name_tok)
            init: Expr | None = None
            if self._is("="):
                self._next()
                init = self.parse_expr()
            items.append((name_tok.value, init))
            if self._is(","):
                self._next()
                continue
            break
        self._expect(";")
        return Decl(type_kw, items)

    def _parse_if(self) -> If:
        self._next()  # 'if'
        self._expect("(")
        cond = self.parse_expr()
        self._expect(")")
        then = self.parse_stmt()
        orelse: Stmt | None = None
        if self._is("else"):
            self._next()
            orelse = self.parse_stmt()
        return If(cond, then, orelse)

    def _parse_for(self) -> For:
        self._next()  # 'for'
        self._expect("(")
        init = None if self._is(";") else self._parse_simple_stmt(require_semi=False)
        self._expect(";")
        cond = None if self._is(";") else self.parse_expr()
        self._expect(";")
        post = None if self._is(")") else self._parse_simple_stmt(require_semi=False)
        self._expect(")")
        body = self.parse_stmt()
        return For(init, cond, post, body)

    def _parse_while(self) -> While:
        self._next()  # 'while'
        self._expect("(")
        cond = self.parse_expr()
        self._expect(")")
        return While(cond, self.parse_stmt())

    def _parse_do(self) -> DoWhile:
        self._next()  # 'do'
        body = self.parse_stmt()
        if not self._is("while"):
            raise self._err("expected 'while' after 'do' body")
        self._next()
        self._expect("(")
        cond = self.parse_expr()
        self._expect(")")
        self._expect(";")
        return DoWhile(body, cond)

    def _parse_simple_stmt(self, require_semi: bool) -> Stmt:
        """Assignment, increment/decrement, or bare expression statement."""
        # Declarations can appear in a for-init position.
        t = self._peek()
        if t is not None and t.kind == "ident" and t.value in TYPE_KEYWORDS \
                and self._is_decl():
            # _parse_decl consumes its own ';'; only valid when require_semi.
            if not require_semi:
                raise self._err("declaration not allowed here", t)
            return self._parse_decl()

        expr = self.parse_expr()
        stmt: Stmt
        nxt = self._peek()
        if nxt is not None and nxt.value == "=":
            self._next()
            value = self.parse_expr()
            if not isinstance(expr, (Var, Index)):
                raise self._err("invalid assignment target")
            stmt = Assign(expr, value)
        elif nxt is not None and nxt.value in ("++", "--"):
            self._next()
            if not isinstance(expr, Var):
                raise self._err("++/-- requires a simple variable")
            stmt = IncDec(expr.name, nxt.value)
        else:
            stmt = ExprStmt(expr)
        if require_semi:
            self._expect(";")
        return stmt

    # --- expressions ---

    def parse_expr(self) -> Expr:
        return self._parse_binary(1)

    def _parse_binary(self, min_prec: int) -> Expr:
        left = self._parse_unary()
        while True:
            t = self._peek()
            if t is None or t.kind != "op" or t.value not in _BINARY_PREC:
                break
            prec = _BINARY_PREC[t.value]
            if prec < min_prec:
                break
            op = self._next().value
            right = self._parse_binary(prec + 1)
            left = Binary(op, left, right)
        return left

    def _parse_unary(self) -> Expr:
        t = self._peek()
        if t is not None and t.kind == "op" and t.value in _UNARY_OPS:
            op = self._next().value
            operand = self._parse_unary()
            # Unary '+' is a no-op; drop it.
            return operand if op == "+" else Unary(op, operand)
        return self._parse_power()

    def _parse_power(self) -> Expr:
        base = self._parse_primary()
        if self._is("**"):
            self._next()
            # Right-associative; allow a unary exponent (e.g. 2**-3).
            exp = self._parse_unary()
            return Binary("**", base, exp)
        return base

    def _parse_primary(self) -> Expr:
        t = self._next()
        if t.kind == "num":
            return Num(t.value, is_float=("." in t.value or "e" in t.value
                                          or "E" in t.value))
        if t.value == "(":
            inner = self.parse_expr()
            self._expect(")")
            return inner
        if t.kind == "ident":
            if self._is("("):
                return self._parse_call(t.value)
            if self._is("["):
                self._next()
                idx = self.parse_expr()
                self._expect("]")
                return Index(t.value, idx)
            return Var(t.value)
        raise self._err(f"unexpected token {t.value!r}", t)

    def _parse_call(self, name: str) -> Call:
        self._expect("(")
        args: list[Expr] = []
        if not self._is(")"):
            args.append(self.parse_expr())
            while self._is(","):
                self._next()
                args.append(self.parse_expr())
        self._expect(")")
        return Call(name, args)


def parse(src: str) -> Block:
    """Tokenize and parse a Formula Node script into a Block of statements."""
    return Parser(tokenize(src)).parse_program()
