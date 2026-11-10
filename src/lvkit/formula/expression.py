"""Expression Node → Python.

An Expression Node (block-diagram class ``exprNode``) is one expression over one
variable, in the same syntax and with the same functions as a Formula Node.
The input terminal is the variable and the output terminal is the expression's
value, of the input's type. It is emitted as the Formula Node script
``<result> = <expression>;`` with the variable as an input and the result as an
output of the same numeric type, so the Formula Node's numeric rules apply
unchanged (``emit.emit_expression``).
"""

from __future__ import annotations

from . import FormulaTranspileError
from .cparser import parse
from .emit import EXPRESSION_RESULT
from .nodes import Assign, Binary, Call, Expr, Index, Num, Unary, Var

# The variable name of an expression that names none (``2+3``).
_DEFAULT_VARIABLE = "x"

# The constant the syntax names; not a variable.
_CONSTANTS = frozenset({"pi"})


def _variables(e: Expr, found: list[str]) -> None:
    if isinstance(e, Var):
        if e.name not in _CONSTANTS and e.name not in found:
            found.append(e.name)
    elif isinstance(e, Index):
        _variables(e.base, found)
        _variables(e.index, found)
    elif isinstance(e, Unary):
        _variables(e.operand, found)
    elif isinstance(e, Binary):
        _variables(e.left, found)
        _variables(e.right, found)
    elif isinstance(e, Call):
        for arg in e.args:
            _variables(arg, found)
    elif not isinstance(e, Num):
        raise FormulaTranspileError(f"cannot read expression {e!r}")


def expression_variable(text: str) -> str:
    """The one variable of the expression ``text`` (``x`` for ``2*x+3``). An
    expression that names none has ``x``; one that names several is an error."""
    tree = parse(f"{EXPRESSION_RESULT} = {text};")
    statement = tree.stmts[0] if len(tree.stmts) == 1 else None
    if not isinstance(statement, Assign):
        raise FormulaTranspileError(f"{text!r} is not a single expression")
    found: list[str] = []
    _variables(statement.value, found)
    if len(found) > 1:
        raise FormulaTranspileError(
            f"{text!r} has more than one variable ({', '.join(found)}); "
            "an Expression Node has one"
        )
    return found[0] if found else _DEFAULT_VARIABLE
