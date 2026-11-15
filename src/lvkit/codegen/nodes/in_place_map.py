"""The In Place Element Structure's Map Get / Replace Value border node pair.

A LabVIEW Map is a Python ``dict`` (``nodes/ops/collections.py``). The READ tile
(left half) looks a key up without copying the value out; the WRITE tile (right
half) puts the value the body produced back, or discards it.

NI ("Map Get / Replace Value", labview-api-ref ``functions/map-get-replace-value.html``;
the page text is saved under ``.tmp/factory/lanes/ni/map_get_replace_value.json``):

* left half: inputs ``map``, ``key`` and ``default value`` ("the value to return in
  value if the specified key does not exist in the map"); outputs ``value`` ("the
  value of the entry with the associated key if key not found? is FALSE. If key not
  found? is TRUE, this output returns the value in default value") and ``key not
  found?`` ("TRUE if map does not include an entry with the specified key").
* right half: inputs ``value`` ("the new value of the entry with the associated
  key") and ``action``, which "assumes the value 0 if unwired": ``0`` Modify Entry
  only if found (found: replace the value; not found: nothing), ``1`` Add or Modify
  Entry (found: replace; not found: add the key and the value), ``2`` Delete Entry
  (found: delete the entry; not found: nothing). Outputs ``map`` ("the resulting
  map"), ``key`` ("the same value as the key input on the left side") and ``key not
  found?`` ("The two key not found? outputs return the same value").

The terminals are told apart by what the file records, not by their order alone:
the map is the one of Map type, the ``value`` is the ``dcoAgg`` terminal (role
``agg``), the others are the ``dcoList`` terminals (role ``list``) in the order the
node records them: on the left the key then the default value, on the right the key
then ``key not found?`` (a Boolean) among the outputs and the action as the one
list input.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import Terminal

from ..context import CodeGenContext
from ..native_array import operand
from ..type_default import type_default
from .ops.collections import ensure_hashable


def _is_map(term: Terminal) -> bool:
    lv_type = term.lv_type
    return lv_type is not None and lv_type.underlying_type == "Map"


def _listed(op: PrimitiveNode, direction: str) -> list[Terminal]:
    """The ``dcoList`` terminals in ``direction``, in the order the node records."""
    return sorted(
        (t for t in op.terminals if t.nmux_role == "list" and t.direction == direction),
        key=lambda t: t.index,
    )


def _agg(op: PrimitiveNode, direction: str) -> Terminal | None:
    return next(
        (t for t in op.terminals if t.nmux_role == "agg" and t.direction == direction),
        None,
    )


def map_terminal(op: PrimitiveNode, direction: str) -> Terminal | None:
    """The tile's map terminal: the one of Map type."""
    return next(
        (t for t in op.terminals if t.direction == direction and _is_map(t)), None
    )


def read_tile(op: PrimitiveNode, ctx: CodeGenContext) -> tuple[str, str] | None:
    """Bind the READ tile's outputs to expressions over the map and the key.
    Returns ``(map, key)`` as the expressions that feed it, None when either is
    not wired."""
    map_in = map_terminal(op, "input")
    inputs = _listed(op, "input")
    value_out = _agg(op, "output")
    not_found = _listed(op, "output")
    if map_in is None or len(inputs) < 2 or value_out is None:
        return None
    key_in, default_in = inputs[0], inputs[1]
    whole = ctx.wired_input(map_in.id, op)
    key = ctx.wired_input(key_in.id, op)
    if whole is None or key is None:
        return None
    ensure_hashable(op, key_in.lv_type, "a key")
    default = ctx.wired_input(default_in.id, op)
    if default is None:
        default = ast.unparse(type_default(ctx, value_out.lv_type))
    ctx.bind(value_out.id, f"{operand(whole)}.get({key}, {default})")
    for out in not_found:
        ctx.bind(out.id, _not_found(whole, key))
    return whole, key


def _not_found(whole: str, key: str) -> str:
    return f"{operand(key)} not in {operand(whole)}"


def write_tile(
    op: PrimitiveNode, whole: str, key: str, ctx: CodeGenContext
) -> list[ast.stmt]:
    """Bind the WRITE tile's outputs and return the statements that make the
    resulting map: a new dict, the one that came in is not changed."""
    value_in = _agg(op, "input")
    action_in = next(iter(_listed(op, "input")), None)
    map_out = map_terminal(op, "output")
    outputs = _listed(op, "output")
    if map_out is None or value_in is None:
        raise ConversionGap("Map Replace Value has no map output", op)
    value = ctx.wired_input(value_in.id, op)
    action = ctx.wired_input(action_in.id, op) if action_in is not None else None
    name = ctx.make_output_var("map", op.id, map_out.id)
    ctx.bind(map_out.id, name)
    if outputs:
        ctx.bind(outputs[0].id, key)
    for out in outputs[1:]:
        ctx.bind(out.id, _not_found(whole, key))
    return _action_statements(op, name, whole, key, value, action, ctx)


def _action_statements(
    op: PrimitiveNode,
    name: str,
    whole: str,
    key: str,
    value: str | None,
    action: str | None,
    ctx: CodeGenContext,
) -> list[ast.stmt]:
    m, k = operand(whole), operand(key)
    branches = {
        0: [f"{name} = {{**{m}, {k}: {value}}} if {k} in {m} else {m}"],
        1: [f"{name} = {{**{m}, {k}: {value}}}"],
        2: [f"{name} = dict({m})", f"{name}.pop({k}, None)"],
    }
    chosen = 0 if action is None else _literal_action(action)
    if chosen is not None:
        if chosen not in branches:
            raise ConversionGap(
                f"Map Get / Replace Value with action {chosen}: NI documents "
                "0 (Modify Entry only if found), 1 (Add or Modify Entry) and "
                "2 (Delete Entry)",
                op,
            )
        _need_value(op, value, chosen)
        return ast.parse("\n".join(branches[chosen])).body
    _need_value(op, value, 0)
    lines: list[str] = []
    for position, (number, body) in enumerate(branches.items()):
        lines.append(f"{'if' if position == 0 else 'elif'} {action} == {number}:")
        lines.extend(f"    {line}" for line in body)
    lines.append("else:")
    lines.append(
        "    raise NotImplementedError("
        f"f'Map Get / Replace Value action {{{action}}} is not one of the "
        "documented 0, 1 or 2')"
    )
    return ast.parse("\n".join(lines)).body


def _literal_action(action: str) -> int | None:
    try:
        value = ast.literal_eval(action)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _need_value(op: PrimitiveNode, value: str | None, action: int) -> None:
    if value is None and action != 2:
        raise ConversionGap(
            "Map Replace Value has no value wired: NI does not say what the "
            "entry becomes",
            op,
        )
