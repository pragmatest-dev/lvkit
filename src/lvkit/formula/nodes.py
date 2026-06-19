"""AST node types for the Formula Node language.

Small, explicit dataclasses. Expressions and statements are separate trees.
The C emitter walks these; the parser produces them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Expressions ---


@dataclass
class Num:
    text: str          # original literal text (preserves int vs float form)
    is_float: bool


@dataclass
class Var:
    name: str


@dataclass
class Index:
    name: str
    index: Expr


@dataclass
class Call:
    name: str
    args: list[Expr]


@dataclass
class Unary:
    op: str            # "-", "!", "~"
    operand: Expr


@dataclass
class Binary:
    op: str            # "+", "-", "*", "/", "%", "**", "<", "&", "<<", ...
    left: Expr
    right: Expr


Expr = Num | Var | Index | Call | Unary | Binary
LValue = Var | Index

# --- Statements ---


@dataclass
class Decl:
    type_kw: str                       # formula-node type keyword, e.g. "int16"
    # one entry per declared name; init is None when there is no initializer
    items: list[tuple[str, Expr | None]]


@dataclass
class Assign:
    target: LValue
    value: Expr


@dataclass
class IncDec:
    name: str
    op: str            # "++" or "--"


@dataclass
class If:
    cond: Expr
    then: Stmt
    orelse: Stmt | None


@dataclass
class For:
    init: Stmt | None
    cond: Expr | None
    post: Stmt | None
    body: Stmt


@dataclass
class While:
    cond: Expr
    body: Stmt


@dataclass
class DoWhile:
    body: Stmt
    cond: Expr


@dataclass
class Block:
    stmts: list[Stmt] = field(default_factory=list)


@dataclass
class ExprStmt:
    expr: Expr


@dataclass
class Empty:
    pass


Stmt = (
    Decl | Assign | IncDec | If | For | While | DoWhile | Block | ExprStmt | Empty
)
