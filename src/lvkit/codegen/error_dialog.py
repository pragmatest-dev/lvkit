"""The dialog box of the error handlers (Simple Error Handler, General Error
Handler), shown through ``live_io.prompt``.

``type of dialog`` is 0 (no dialog), 1 (OK message: one ``Continue`` button), 2
(``Continue`` or ``Stop``), 3 (OK message, also for a warning) or 4 (``Continue``
or ``Stop``, also for a warning). A dialog is shown for an error, and for 3 and 4
also for a warning; ``Stop`` calls the Stop function (``sys.exit()``).
"""

from __future__ import annotations

import ast

from lvkit.graph.models import VINode

from .ast_utils import LIVE_IO, build_assign, parse_expr, parse_stmt
from .context import CodeGenContext
from .error_flow import error_occurred
from .errors import CodeGenError
from .fragment import CodeFragment

_CONTINUE = "Continue"
_STOP = "Stop"
# The values of ``type of dialog`` that show a dialog, that also show one for a
# warning, and that offer ``Stop``.
_SHOWN = (1, 2, 3, 4)
_WARNINGS = (3, 4)
_WITH_STOP = (2, 4)


def dialog_statements(
    node: VINode,
    ctx: CodeGenContext,
    error: str,
    message: str,
    dialog_type: str,
    constant: int | None,
) -> CodeFragment:
    """The statements that show the dialog for the error wire ``error`` (an
    expression) with ``message``, for the ``type of dialog`` ``dialog_type`` (an
    expression; ``constant`` is its value when it is a known integer). Empty when
    the dialog is never shown."""
    if error == "None" or (constant is not None and constant not in _SHOWN):
        return CodeFragment.empty()
    if ctx.panel_hooks is None:
        raise CodeGenError("A node that shows a dialog needs live_io", node)
    statements: list[ast.stmt] = []
    if constant is None:
        kind = ctx.make_output_var("type_of_dialog", node.id)
        statements.append(build_assign(kind, parse_expr(dialog_type)))
        occurred = (
            f"{kind} in {_SHOWN} and ({error_occurred(error)} or "
            f"{kind} in {_WARNINGS} and {error} is not None)"
        )
        buttons = (
            f"[{_CONTINUE!r}, {_STOP!r}] if {kind} in {_WITH_STOP} else [{_CONTINUE!r}]"
        )
        stops = True
    else:
        occurred = (
            f"{error} is not None" if constant in _WARNINGS else error_occurred(error)
        )
        stops = constant in _WITH_STOP
        buttons = f"[{_CONTINUE!r}, {_STOP!r}]" if stops else f"[{_CONTINUE!r}]"
    ask = f"{LIVE_IO}.prompt({message}, {buttons})"
    body: list[ast.stmt] = (
        [
            ast.If(
                test=parse_expr(f"{ask} == 1"),
                body=[parse_stmt("sys.exit()")],
                orelse=[],
            )
        ]
        if stops
        else [parse_stmt(ask)]
    )
    statements.append(ast.If(test=parse_expr(occurred), body=body, orelse=[]))
    return CodeFragment(
        statements=statements, imports={"import sys"} if stops else set()
    )
