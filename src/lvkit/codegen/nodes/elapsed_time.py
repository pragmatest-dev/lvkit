"""The Elapsed Time Express VI (``subElapsedTime.vi``).

NI, "Elapsed Time" (labview-api-ref,
``vi-lib/express/express-execution-control/elapsedtimeblock-llb/
ex-inst-elapsed-time-vi.html``): the VI keeps a start time between calls. It is
the time the VI first ran, or the ``Set Start Time (s)`` input when that is not 0
(seconds since 1904-01-01 00:00:00 UTC; 0 means the current time), and ``Reset``
"controls the initialization of the internal state of the VI", so a call with
``Reset`` TRUE starts again from the present. ``Present (s)`` is "the present time
in seconds since 12:00 a.m., Friday, January 1, 1904, Universal Time",
``Elapsed Time (s)`` the seconds from the start time to ``Present (s)``, and
``Time has Elapsed`` is TRUE once that reaches ``Time Target (s)`` ("how much time
must elapse before the Time has Elapsed Boolean is set to TRUE", default 1).
``Auto Reset`` "resets the start time to the value in Present (s) when the Express
VI reaches the Time Target (s)": the call that reaches the target reports it, and
the next call measures from that call's present time. ``Get Start Time (s)`` is
the start time the call measured from ("the date and time the VI first ran or the
time you wire to the Set Start Time (s) input. Otherwise ... of the last reset").

Recorded defaults, none stated by NI (a web search of forums.ni.com, lavag.org and
NI's help found none): ``Time has Elapsed`` is ``elapsed >= target`` (NI's output
text says "greater than", its input text "must elapse before ... TRUE"); an
unwired ``Auto Reset`` is FALSE (its value is set in the Express VI's dialog,
which the VI file the caller saves does not give lvkit); an unwired ``Set Start
Time (s)`` is 0, the value NI names as "the current time". NI gives no format for
``Start Time Text``, ``Present Text`` and ``Elapsed Time Text``, so a node that
wires one of them is a gap.

The start time is a module-level variable the function keeps between calls
(``add_state_global``), one per call site, None until the first call.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import TerminalDirection
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, node_owned_name, parse_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment

# The seconds from 1904-01-01 00:00:00 UTC to the Unix epoch, 1970-01-01 (24107 days).
_PRESENT_SECONDS = "time.time() + 2082844800"
_TEXT_OUTPUTS = ("start_time_text", "present_text", "elapsed_time_text")


def _index(entry: VIEntry, param: str, direction: TerminalDirection) -> int | None:
    return next(
        (
            t.index
            for t in entry.terminals
            if t.python_param == param and t.direction == direction
        ),
        None,
    )


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of the Elapsed Time Express VI: the state read, the outputs
    computed from it and the state written for the next call."""
    outputs = {}
    for term in node.terminals:
        if term.direction != TerminalDirection.OUTPUT:
            continue
        for param in (
            "present",
            "elapsed_time",
            "time_has_elapsed",
            "get_start_time",
            *_TEXT_OUTPUTS,
        ):
            if term.index == _index(entry, param, TerminalDirection.OUTPUT):
                outputs[param] = term
    for param in _TEXT_OUTPUTS:
        term = outputs.get(param)
        if term is not None and ctx.is_wired(term.id):
            raise ConversionGap(
                f"{entry.name}: the {term.name or param} output is wired, and NI "
                "does not document the format of its text",
                node,
            )

    def wired(param: str) -> str | None:
        index = _index(entry, param, TerminalDirection.INPUT)
        term = next(
            (
                t
                for t in node.terminals
                if t.index == index and t.direction == TerminalDirection.INPUT
            ),
            None,
        )
        return (ctx.wired_input(term.id, node) or None) if term is not None else None

    target = wired("time_target") or "1.0"
    reset = wired("reset")
    auto_reset = wired("auto_reset")
    state = node_owned_name("_lv_elapsed_start", ctx.function_name, node.id)
    ctx.add_state_global(state, ast.Constant(value=None), owner=node.id)
    present = ctx.make_output_var("present", node.id)
    # 0 (the unwired value too) is "the current time".
    set_start = wired("set_start_time")
    first_start = f"{set_start} or {present}" if set_start else present
    elapsed = ctx.make_output_var("elapsed_time", node.id)
    has_elapsed = ctx.make_output_var("time_has_elapsed", node.id)
    start_used = ctx.make_output_var("get_start_time", node.id)

    starts = f"{reset} or {state} is None" if reset else f"{state} is None"
    source = (
        f"if {starts}:\n"
        f"    {state} = {first_start}\n"
        f"{start_used} = {state}\n"
        f"{elapsed} = {present} - {start_used}\n"
        f"{has_elapsed} = {elapsed} >= {target}\n"
    )
    if auto_reset:
        source += f"if {auto_reset} and {has_elapsed}:\n    {state} = {present}\n"
    statements: list[ast.stmt] = [
        ast.Global(names=[state]),
        build_assign(present, parse_expr(_PRESENT_SECONDS)),
        *ast.parse(source).body,
    ]
    names = {
        "present": present,
        "elapsed_time": elapsed,
        "time_has_elapsed": has_elapsed,
        "get_start_time": start_used,
    }
    bindings = {
        outputs[param].id: name
        for param, name in names.items()
        if param in outputs and ctx.is_wired(outputs[param].id)
    }
    return CodeFragment(
        statements=statements, bindings=bindings, imports={"import time"}
    )
