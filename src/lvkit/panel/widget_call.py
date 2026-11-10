"""How the panel module calls the builder of each kind of control: the arguments
after the holder and the name, and the names the module imports for it. A field
of a cluster and the element of an array are a ``Member`` of the port builder of
their kind, built the same way."""

from __future__ import annotations

from collections.abc import Callable

from .kinds import PanelControl, PanelKind

ARRAY_ROW_HEIGHT = 24
ARRAY_CHROME_HEIGHT = 32


def _number(control: PanelControl) -> str:
    return f"{control.default!r}, {control.integer}"


def _default_only(control: PanelControl) -> str:
    return repr(control.default)


def _choice(control: PanelControl) -> str:
    return f"{control.items!r}, {control.default!r}"


def _cluster(control: PanelControl) -> str:
    members = ", ".join(member_call(m) for m in control.members)
    return f"{control.named}, [{members}]"


def _array(control: PanelControl) -> str:
    assert control.element is not None
    height = control.bounds[2] - control.bounds[0] - ARRAY_CHROME_HEIGHT
    rows = max(2, height // ARRAY_ROW_HEIGHT)
    return f"{member_call(control.element)}, {rows}, {list(control.initial)!r}"


def _no_arguments(_control: PanelControl) -> str:
    return ""


ARGUMENTS: dict[PanelKind, Callable[[PanelControl], str]] = {
    PanelKind.ERROR: _no_arguments,
    PanelKind.NUMBER: _number,
    PanelKind.BOOLEAN: _default_only,
    PanelKind.STRING: _default_only,
    PanelKind.PATH: _default_only,
    PanelKind.ENUM: _choice,
    PanelKind.RING: _choice,
    PanelKind.CLUSTER: _cluster,
    PanelKind.ARRAY: _array,
}


def builder_of(control: PanelControl, kind: PanelKind) -> str:
    return kind.indicator_builder if control.is_indicator else kind.control_builder


def widget_call(control: PanelControl, kind: PanelKind, key: str) -> str:
    """The call that draws ``control`` of ``kind`` and ties it to the holder
    ``values`` under the hook name ``key``."""
    arguments = ARGUMENTS[kind](control)
    tail = f", {arguments}" if arguments else ""
    return f"{builder_of(control, kind)}(values, {key}{tail})"


def member_call(control: PanelControl) -> str:
    """The ``Member`` that builds a field of a cluster or the element of an
    array."""
    kind = control.kind
    assert kind is not None
    arguments = ARGUMENTS[kind](control)
    tail = f", {arguments}" if arguments else ""
    return f"Member({control.key!r}, {control.label!r}, {kind.port_builder}{tail})"


def _parts(control: PanelControl) -> tuple[PanelControl, ...]:
    return (*control.members, *([control.element] if control.element else []))


def builders_used(control: PanelControl, kind: PanelKind) -> set[str]:
    """The names of the ``controls`` package the call of ``control`` needs."""
    names = {builder_of(control, kind)}
    for part in _parts(control):
        assert part.kind is not None
        names |= {"Member", part.kind.port_builder}
        names |= builders_used(part, part.kind) - {builder_of(part, part.kind)}
    return names
