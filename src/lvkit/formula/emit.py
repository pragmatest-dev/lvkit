"""Emit Python from a parsed Formula Node script.

The Formula Node language is a small, sandboxed C-like DSL (typed scalar
locals, arrays that are wired terminals, ``if``/``for``/``while``/``do``,
operators, and a fixed set of math functions — no pointers, no allocation).
Every one of its constructs maps cleanly onto Python, so we emit a
self-contained Python function instead of C. That keeps the original
"no AI interprets the algorithm" guarantee (the translation is deterministic
from the parsed AST) while dropping the C compiler / FFI / .so machinery
entirely — generated code is pure Python and runs anywhere.

LabVIEW numeric semantics (confirmed against a real Formula Node, issue #8)
are preserved:
  * ``int / int``                -> real (float) division; a zero divisor
                                    yields IEEE inf/nan (``_lv.div``) since a
                                    Formula Node has no error terminal
  * float assigned to an int var -> round-to-nearest-even then wrap to the
                                    declared width (``_lv.i16`` etc.)
  * ``**``                       -> right-assoc, tighter than unary minus; a
                                    negative base with a non-integer exponent
                                    is nan (``_lv.powf``), not a complex
  * ``%`` / ``rem``              -> truncated remainder, sign of dividend;
                                    ``mod`` is floored, sign of divisor
  * ``&&`` ``||`` ``!``          -> 1/0 (``_lv.land``/``lor``/``lnot``)
  * ``abs``                      -> Python ``abs`` (polymorphic int/float)
  * domain funcs (sqrt/ln/...)   -> non-raising ``_lv.*`` (nan/inf on domain)
  * N-D arrays                   -> nested indexing ``a[i][j]``; ``sizeOfDim``

Unknown functions or type keywords raise FormulaTranspileError — never a
silent guess.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from . import FormulaTranspileError
from .cparser import parse
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

# --- type maps -------------------------------------------------------------

# LabVIEW numeric type-name (parser's ParsedType.type_name) -> (kind, _lv int
# coercion helper). Float types have no coercion helper.
_LV_INT_HELPER = {
    "NumInt8": "i8",
    "NumInt16": "i16",
    "NumInt32": "i32",
    "NumInt64": "i64",
    "NumUInt8": "u8",
    "NumUInt16": "u16",
    "NumUInt32": "u32",
    "NumUInt64": "u64",
}
_LV_FLOAT = {"NumFloat32", "NumFloat64", "NumFloatExt"}

# Formula-node declaration keyword -> _lv int coercion helper. ``float`` is a
# LabVIEW alias for float64.
_KW_INT_HELPER = {
    "int8": "i8",
    "int16": "i16",
    "int32": "i32",
    "int64": "i64",
    "uInt8": "u8",
    "uInt16": "u16",
    "uInt32": "u32",
    "uInt64": "u64",
}
_KW_FLOAT = {"float32", "float64", "float"}

# Formula-node function spelling -> emitted Python callee. Anything not here
# fails loud. ``int`` rounds to nearest (ties to even); ``intrz`` truncates.
# Domain-prone functions route through the non-raising _lv.* wrappers (a
# Formula Node has no error terminal — LabVIEW coerces domain/zero errors to
# IEEE inf/nan rather than trapping). Functions that never raise on real
# inputs (sin, cos, atan, exp, …) keep their direct math.* mapping.
_FUNC_MAP = {
    "abs": "abs",
    "sqrt": "_lv.sqrt",
    "exp": "math.exp",
    "expm1": "math.expm1",
    "ln": "_lv.ln",
    "lnp1": "math.log1p",
    "log": "_lv.log10",
    "log2": "_lv.log2",
    "ceil": "math.ceil",
    "floor": "math.floor",
    "sin": "math.sin",
    "cos": "math.cos",
    "tan": "math.tan",
    "asin": "_lv.asin",
    "acos": "_lv.acos",
    "atan": "math.atan",
    "atan2": "math.atan2",
    "sinh": "math.sinh",
    "cosh": "math.cosh",
    "tanh": "math.tanh",
    "asinh": "math.asinh",
    "acosh": "_lv.acosh",
    "atanh": "_lv.atanh",
    "pow": "_lv.powf",
    "int": "round",
    "intrz": "math.trunc",
    "mod": "_lv.lvmod",
    "rem": "_lv.rem",
    "sign": "_lv.sign",
    "getexp": "_lv.getexp",
    "getman": "_lv.getman",
    "max": "max",
    "min": "min",
    "cot": "_lv.cot",
    "csc": "_lv.csc",
    "sec": "_lv.sec",
    "sinc": "_lv.sinc",
    "rand": "_lv.rand",
    "sizeOfDim": "_lv.size_of_dim",
}
# Functions whose result is an integer (drives int/float inference).
_INT_RESULT_FUNCS = {"int", "intrz", "sign", "sizeOfDim"}

# Logical operators -> runtime helper. LabVIEW ``&&``/``||`` yield 1/0, not
# the operand Python ``and``/``or`` would return. Comparisons and bitwise
# operators map 1:1 onto Python and need no entry here.
_LOGICAL_OP = {"&&": "_lv.land", "||": "_lv.lor"}


@dataclass
class VarSpec:
    """One Formula Node variable (a wired terminal)."""

    name: str
    lv_type: str  # NumInt16/NumFloat64/... (element type for arrays)
    direction: str  # "in" | "out" | "inout"
    is_array: bool


@dataclass
class TranspileResult:
    """A generated module-level Python function plus its call contract."""

    func_def: ast.FunctionDef
    func_name: str
    input_names: list[str]  # keyword params the function accepts
    output_names: list[str]  # keys present in the returned dict
    imports: set[str] = field(default_factory=set)
    source: str = ""  # the unparsed function (handy for tests)


def _kind_and_helper(lv_type: str) -> tuple[str, str | None]:
    """Return ("int"|"float", int-coercion-helper-or-None) for an LV type."""
    if lv_type in _LV_INT_HELPER:
        return "int", _LV_INT_HELPER[lv_type]
    if lv_type in _LV_FLOAT:
        return "float", None
    raise FormulaTranspileError(f"unsupported variable type {lv_type!r}")


class _Emitter:
    def __init__(self, variables: list[VarSpec], func_name: str):
        self.vars = {v.name: v for v in variables}
        self.var_list = variables
        self.func_name = func_name
        # abstract kind ("int"/"float") and int coercion helper per name.
        # For arrays these describe the element type (a[i] is an element).
        self.kind: dict[str, str] = {}
        self.int_helper: dict[str, str] = {}
        for v in variables:
            k, helper = _kind_and_helper(v.lv_type)
            self.kind[v.name] = k
            if helper:
                self.int_helper[v.name] = helper

    # --- type inference (coarse: int vs float) ---

    def _texpr(self, e: Expr) -> str:
        if isinstance(e, Num):
            return "float" if e.is_float else "int"
        if isinstance(e, Var):
            return self.kind.get(e.name, "float")
        if isinstance(e, Index):
            # An element has the array's (scalar) element kind, found via the
            # root array variable — works for any nesting depth (a[i][j]).
            return self.kind.get(_index_root(e), "float")
        if isinstance(e, Call):
            if e.name == "abs" and e.args:
                return self._texpr(e.args[0])
            return "int" if e.name in _INT_RESULT_FUNCS else "float"
        if isinstance(e, Unary):
            if e.op in ("!", "~"):
                return "int"
            return self._texpr(e.operand)
        if isinstance(e, Binary):
            if e.op in ("<", "<=", ">", ">=", "==", "!=", "&&", "||"):
                return "int"
            if e.op in ("&", "|", "^", "<<", ">>"):
                return "int"
            if e.op in ("**", "/"):
                # Power and division always yield a real (float) in a Formula
                # Node — int/int is real division, not truncating.
                return "float"
            lt, rt = self._texpr(e.left), self._texpr(e.right)
            return "float" if "float" in (lt, rt) else "int"
        return "float"

    # --- expression emission ---

    def _emit_expr(self, e: Expr) -> str:
        if isinstance(e, Num):
            return e.text
        if isinstance(e, Var):
            return "math.pi" if e.name == "pi" else e.name
        if isinstance(e, Index):
            return f"{self._emit_expr(e.base)}[{self._emit_expr(e.index)}]"
        if isinstance(e, Call):
            callee = _FUNC_MAP.get(e.name)
            if callee is None:
                raise FormulaTranspileError(f"unsupported function {e.name!r}")
            args = ", ".join(self._emit_expr(a) for a in e.args)
            return f"{callee}({args})"
        if isinstance(e, Unary):
            inner = self._emit_expr(e.operand)
            if e.op == "!":
                return f"_lv.lnot({inner})"
            return f"({e.op}{inner})"
        if isinstance(e, Binary):
            left = self._emit_expr(e.left)
            right = self._emit_expr(e.right)
            # LabVIEW ``%`` is the truncated remainder (sign of dividend) for
            # both ints and floats — Python ``%`` is floored, so always route
            # through the helper.
            if e.op == "%":
                return f"_lv.rem({left}, {right})"
            if e.op in _LOGICAL_OP:
                return f"{_LOGICAL_OP[e.op]}({left}, {right})"
            # `/` yields IEEE inf/nan on a zero divisor (no error terminal to
            # raise into). A literal nonzero divisor can't trap, so keep the
            # plain operator there and only wrap the ambiguous cases.
            if e.op == "/" and not _is_nonzero_literal(e.right):
                return f"_lv.div({left}, {right})"
            # `**` with a negative base and non-integer exponent is nan in
            # LabVIEW, but Python returns a complex. Route through the helper
            # unless the base is a provably non-negative literal (the common
            # ``2 ** n`` case, kept as the plain operator).
            if e.op == "**" and not _is_nonneg_literal(e.left):
                return f"_lv.powf({left}, {right})"
            return f"({left} {e.op} {right})"
        raise FormulaTranspileError(f"cannot emit expression {e!r}")

    # --- assignment with int rounding/width coercion ---

    def _emit_store(self, name: str, value: Expr) -> str:
        """Right-hand side for an assignment to ``name``. An integer target
        rounds-to-nearest-even and wraps to its declared width (LabVIEW
        coerces a real into an integer terminal); a float target is direct."""
        rhs = self._emit_expr(value)
        helper = self.int_helper.get(name)
        return f"_lv.{helper}({rhs})" if helper else rhs

    # --- statement emission ---

    def _emit_stmt(self, s: Stmt, out: list[str], indent: int) -> None:
        pad = "    " * indent
        if isinstance(s, Empty):
            return
        if isinstance(s, Block):
            for inner in s.stmts:
                self._emit_stmt(inner, out, indent)
            return
        if isinstance(s, Decl):
            self._emit_decl(s, out, indent)
            return
        if isinstance(s, Assign):
            out.append(f"{pad}{self._emit_assign(s)}")
            return
        if isinstance(s, IncDec):
            out.append(f"{pad}{s.name} {'+' if s.op == '++' else '-'}= 1")
            return
        if isinstance(s, ExprStmt):
            out.append(f"{pad}{self._emit_expr(s.expr)}")
            return
        if isinstance(s, If):
            out.append(f"{pad}if {self._emit_expr(s.cond)}:")
            self._emit_body(s.then, out, indent)
            if s.orelse is not None:
                out.append(f"{pad}else:")
                self._emit_body(s.orelse, out, indent)
            return
        if isinstance(s, While):
            out.append(f"{pad}while {self._emit_expr(s.cond)}:")
            self._emit_body(s.body, out, indent)
            return
        if isinstance(s, DoWhile):
            # Python has no do/while: run the body once, then loop on cond.
            out.append(f"{pad}while True:")
            self._emit_body(s.body, out, indent)
            out.append(f"{pad}    if not ({self._emit_expr(s.cond)}):")
            out.append(f"{pad}        break")
            return
        if isinstance(s, For):
            self._emit_for(s, out, indent)
            return
        raise FormulaTranspileError(f"cannot emit statement {s!r}")

    def _emit_assign(self, s: Assign) -> str:
        if isinstance(s.target, Index):
            tgt = self._emit_expr(s.target)
            # element store: an int-element array coerces like a scalar int,
            # keyed by the root array variable (handles a[i] and a[i][j]).
            rhs = self._emit_store(_index_root(s.target), s.value)
            return f"{tgt} = {rhs}"
        return f"{s.target.name} = {self._emit_store(s.target.name, s.value)}"

    def _emit_body(self, s: Stmt, out: list[str], indent: int) -> None:
        """Emit a control-flow body at indent+1, guaranteeing a non-empty
        suite (an empty/again-empty block becomes ``pass``)."""
        start = len(out)
        self._emit_stmt(s, out, indent + 1)
        if len(out) == start:
            out.append("    " * (indent + 1) + "pass")

    def _emit_for(self, s: For, out: list[str], indent: int) -> None:
        pad = "    " * indent
        # Idiomatic case: for(i=0; i<N; i++) with i untouched in the body ->
        # a Python range loop. Everything else becomes init + while + post.
        rng = self._as_range_loop(s)
        if rng is not None:
            var, limit = rng
            out.append(f"{pad}for {var} in range({limit}):")
            self._emit_body(s.body, out, indent)
            return
        if s.init is not None:
            self._emit_stmt(s.init, out, indent)
        cond = self._emit_expr(s.cond) if s.cond else "True"
        out.append(f"{pad}while {cond}:")
        start = len(out)
        self._emit_stmt(s.body, out, indent + 1)
        if s.post is not None:
            self._emit_stmt(s.post, out, indent + 1)
        if len(out) == start:
            out.append("    " * (indent + 1) + "pass")

    def _as_range_loop(self, s: For) -> tuple[str, str] | None:
        """Return (index_var, limit_expr) if ``s`` is a simple ascending
        count loop ``for(i=0; i<N; i++)`` whose index isn't reassigned in the
        body; otherwise None (caller falls back to a while loop)."""
        init, cond, post = s.init, s.cond, s.post
        if not (
            isinstance(init, Assign)
            and isinstance(init.target, Var)
            and isinstance(init.value, Num)
            and init.value.text == "0"
        ):
            return None
        i = init.target.name
        if not (
            isinstance(cond, Binary)
            and cond.op == "<"
            and isinstance(cond.left, Var)
            and cond.left.name == i
        ):
            return None
        post_ok = isinstance(post, IncDec) and post.name == i and post.op == "++"
        if not post_ok:
            return None
        if self._assigns(s.body, i) or _refs(cond.right, i):
            return None
        return i, self._emit_expr(cond.right)

    def _assigns(self, s: Stmt, name: str) -> bool:
        """True if ``name`` is assigned/incremented anywhere within ``s``."""
        if isinstance(s, Assign):
            return isinstance(s.target, Var) and s.target.name == name
        if isinstance(s, IncDec):
            return s.name == name
        if isinstance(s, Block):
            return any(self._assigns(i, name) for i in s.stmts)
        if isinstance(s, If):
            return self._assigns(s.then, name) or (
                s.orelse is not None and self._assigns(s.orelse, name)
            )
        if isinstance(s, (While, DoWhile)):
            return self._assigns(s.body, name)
        if isinstance(s, For):
            return (
                (s.init is not None and self._assigns(s.init, name))
                or (s.post is not None and self._assigns(s.post, name))
                or self._assigns(s.body, name)
            )
        if isinstance(s, Decl):
            return any(n == name for n, _ in s.items)
        return False

    def _emit_decl(self, s: Decl, out: list[str], indent: int) -> None:
        pad = "    " * indent
        for name, init in s.items:
            if name in self.vars:
                # Re-declaration of a terminal variable: it is already a
                # function parameter/local. Keep only the initializer as a
                # plain assignment so the value is preserved.
                if init is not None:
                    out.append(f"{pad}{name} = {self._emit_store(name, init)}")
                continue
            if s.type_kw in _KW_INT_HELPER:
                self.kind[name] = "int"
                self.int_helper[name] = _KW_INT_HELPER[s.type_kw]
            elif s.type_kw in _KW_FLOAT:
                self.kind[name] = "float"
            else:
                raise FormulaTranspileError(f"unsupported type keyword {s.type_kw!r}")
            if init is not None:
                out.append(f"{pad}{name} = {self._emit_store(name, init)}")
            else:
                # Python needs the name to exist; default by kind.
                default = "0" if self.kind[name] == "int" else "0.0"
                out.append(f"{pad}{name} = {default}")

    # --- pre-pass: collect script-local declaration kinds for inference ---

    def _collect_local_kinds(self, block: Block) -> None:
        def walk(s: Stmt) -> None:
            if isinstance(s, Decl):
                for name, _ in s.items:
                    if name in self.vars:
                        continue
                    if s.type_kw in _KW_INT_HELPER:
                        self.kind.setdefault(name, "int")
                        self.int_helper.setdefault(name, _KW_INT_HELPER[s.type_kw])
                    elif s.type_kw in _KW_FLOAT:
                        self.kind.setdefault(name, "float")
            elif isinstance(s, Block):
                for i in s.stmts:
                    walk(i)
            elif isinstance(s, If):
                walk(s.then)
                if s.orelse:
                    walk(s.orelse)
            elif isinstance(s, (While, DoWhile)):
                walk(s.body)
            elif isinstance(s, For):
                if s.init:
                    walk(s.init)
                walk(s.body)

        for s in block.stmts:
            walk(s)

    # --- assembly ---

    def emit(self, tree: Block) -> TranspileResult:
        self._collect_local_kinds(tree)

        params: list[str] = []
        prologue: list[str] = []
        outputs: list[str] = []
        for v in self.var_list:
            if v.direction in ("in", "inout"):
                params.append(v.name)
            elif v.direction == "out":
                if v.is_array:
                    raise FormulaTranspileError(
                        f"output-only array {v.name!r} has no length source; "
                        "wire it as in/out so its size is known"
                    )
                prologue.append(
                    f"    {v.name} = {'0' if self.kind[v.name] == 'int' else '0.0'}"
                )
            if v.direction in ("out", "inout"):
                outputs.append(v.name)

        body: list[str] = []
        for s in tree.stmts:
            self._emit_stmt(s, body, 1)

        ret = "{" + ", ".join(f"{n!r}: {n}" for n in outputs) + "}"
        sig = "*, " + ", ".join(params) if params else ""
        lines = [f"def {self.func_name}({sig}):"]
        lines.extend(prologue)
        lines.extend(body)
        lines.append(f"    return {ret}")
        if not prologue and not body:
            lines.insert(1, "    pass")
        source = "\n".join(lines) + "\n"

        func_def = ast.parse(source).body[0]
        assert isinstance(func_def, ast.FunctionDef)

        imports: set[str] = set()
        if "math." in source:
            imports.add("import math")
        if "_lv." in source:
            imports.add("from lvkit.runtime import lv as _lv")

        return TranspileResult(
            func_def=func_def,
            func_name=self.func_name,
            input_names=params,
            output_names=outputs,
            imports=imports,
            source=source,
        )


def _index_root(e: Expr) -> str:
    """The root array variable name of a (possibly nested) subscript, e.g.
    ``a`` for ``a[i]`` and ``a[i][j]``. Empty string if the base isn't a Var."""
    while isinstance(e, Index):
        e = e.base
    return e.name if isinstance(e, Var) else ""


def _is_nonzero_literal(e: Expr) -> bool:
    """True if ``e`` is a numeric literal that is provably nonzero — so a
    division by it cannot trap and needs no inf/nan wrapper."""
    if not isinstance(e, Num):
        return False
    try:
        return float(e.text) != 0.0
    except ValueError:
        return False


def _is_nonneg_literal(e: Expr) -> bool:
    """True if ``e`` is a provably non-negative numeric literal — so a power
    with this base can never hit the negative-base complex-result case and
    needs no helper. A unary-minus literal parses as Unary, not Num, so it
    is correctly excluded."""
    if not isinstance(e, Num):
        return False
    try:
        return float(e.text) >= 0.0
    except ValueError:
        return False


def _refs(e: Expr, name: str) -> bool:
    """True if expression ``e`` references variable ``name``."""
    if isinstance(e, Var):
        return e.name == name
    if isinstance(e, Index):
        return _refs(e.base, name) or _refs(e.index, name)
    if isinstance(e, Unary):
        return _refs(e.operand, name)
    if isinstance(e, Binary):
        return _refs(e.left, name) or _refs(e.right, name)
    if isinstance(e, Call):
        return any(_refs(a, name) for a in e.args)
    return False


def transpile(
    script: str,
    variables: list[VarSpec],
    func_name: str = "formula",
) -> TranspileResult:
    """Transpile a Formula Node script to a self-contained Python function.

    ``variables`` are the node's wired terminals (deduped so a name wired on
    both sides is one ``inout`` entry). The function takes the inputs as
    keyword arguments and returns a dict of output-name -> value. Raises
    FormulaTranspileError on any unsupported construct.
    """
    tree = parse(script)
    return _Emitter(variables, func_name).emit(tree)
