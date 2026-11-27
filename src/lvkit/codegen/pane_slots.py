"""A call through a reference value, matched to the called VI by connector-pane slot.

Call By Reference and Start Asynchronous Call call a VI that is only known when the
diagram runs. LabVIEW matches the call to that VI by connector pane, not by terminal
name (NI, Call By Reference: the node "enables you to call any VI dynamically whose
connector pane matches that of the strictly typed reference input"; "Wire data to
the input terminals of the connector pane to pass the data to the called VI. Use the
output terminals of the connector pane to access the data that the called VI
returns"), so a terminal is its pane slot whatever either VI names it.

When the project's VIs with the reference's pane do not all name their terminals
alike, the module holds a table of them, by the ``vi_name`` each function carries:

    _pane_slots = {
        "A.vi": ({0: "request_data", 1: "error_in"}, {0: "response_data"}, {}),
        "B.vi": ({0: "service_request", 1: "error_in"}, {0: "service_location"}, {}),
        "C.lvclass:M.vi": ({0: "self", 1: "x"}, {1: "y"}, {2: 0}),
    }

(each VI's parameter name and result field name by slot index, and the output
slots that are the object the call was made on, by the input slot that gave it),
and the call reads the names of the function it holds:
``vi_ref(**{_pane_slots[vi_ref.vi_name][0][0]: x})`` and
``getattr(result, _pane_slots[vi_ref.vi_name][1][0])``. A member VI of a class is
the function on its class (``Class.method``), so it is called like any function with
its object as the ``self`` keyword, which the table records as the name of the
object's slot; a class output that is that object is read from the argument that
was given, not from the result.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping

from .ast_utils import parse_expr
from .context import CodeGenContext
from .pane_candidates import CallForm

_INPUTS, _OUTPUTS, _PASSED = 0, 1, 2


def pane_slots_table(
    ctx: CodeGenContext, name: str, owner: str, forms: Mapping[str, CallForm]
) -> None:
    """The module-level table ``name`` (owned by the node ``owner``): the
    parameter names, result field names and passed-on objects by slot index of each
    VI in ``forms`` (the project's VIs with the reference's pane, by the VI's
    name)."""
    entries = sorted(forms.items())
    ctx.add_module_global(
        name,
        ast.Dict(
            keys=[ast.Constant(value=vi_name) for vi_name, _ in entries],
            values=[
                ast.Tuple(
                    elts=[
                        ast.Dict(
                            keys=[ast.Constant(value=i) for i, _ in names],
                            values=[ast.Constant(value=n) for _, n in names],
                        )
                        for names in (
                            form.keywords,
                            form.fields,
                            form.passed_on,
                        )
                    ],
                    ctx=ast.Load(),
                )
                for _, form in entries
            ],
        ),
        owner,
    )


def pane_parameters(
    table: str, reference: str, arguments: list[tuple[int, str]]
) -> ast.keyword:
    """``**{table[reference.vi_name][0][slot]: value, ...}``: the arguments of a
    call of the function ``reference`` holds, each named as that function names
    its slot."""
    return ast.keyword(
        arg=None,
        value=ast.Dict(
            keys=[
                parse_expr(f"{table}[{reference}.vi_name][{_INPUTS}][{slot}]")
                for slot, _ in arguments
            ],
            values=[parse_expr(value) for _, value in arguments],
        ),
    )


def pane_result(
    table: str,
    reference: str,
    result: str,
    slot: int,
    given: dict[int, str] | None = None,
) -> str:
    """The expression that reads output slot ``slot`` from the ``result`` of a call
    of the function ``reference`` holds. ``given`` are the values the call was
    given at the class input slots; with them, an output that is the object the
    call was made on is read from there."""
    field = f"getattr({result}, {table}[{reference}.vi_name][{_OUTPUTS}][{slot}])"
    if not given:
        return field
    objects = "{" + ", ".join(f"{i}: {v}" for i, v in sorted(given.items())) + "}"
    passed = f"{table}[{reference}.vi_name][{_PASSED}]"
    return f"({objects}[{passed}[{slot}]] if {slot} in {passed} else {field})"
