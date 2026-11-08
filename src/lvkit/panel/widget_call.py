"""How the panel module calls the builder of each kind of control: the arguments
after the holder and the name, and the names the module imports for it. A field
of a cluster is a ``Member`` of the port builder of its kind, built the same
way."""

from __future__ import annotations

from collections.abc import Callable

from .kinds import PanelControl, PanelKind


def _number(control: PanelControl) -> str:
    return f"{control.default!r}, {control.integer}"


def _default_only(control: PanelControl) -> str:
    return repr(control.default)


def _choice(control: PanelControl) -> str:
    return f"{control.items!r}, {control.default!r}"


def _cluster(control: PanelControl) -> str:
    members = ", ".join(member_call(m) for m in control.members)
    return f"{control.named}, [{members}]"


ARGUMENTS: dict[PanelKind, Callable[[PanelControl], str]] = {
    PanelKind.NUMBER: _number,
    PanelKind.BOOLEAN: _default_only,
    PanelKind.STRING: _default_only,
    PanelKind.PATH: _default_only,
    PanelKind.ENUM: _choice,
    PanelKind.RING: _choice,
    PanelKind.CLUSTER: _cluster,
}


def builder_of(control: PanelControl, kind: PanelKind) -> str:
    return kind.indicator_builder if control.is_indicator else kind.control_builder


def widget_call(control: PanelControl, kind: PanelKind, key: str) -> str:
    """The call that draws ``control`` of ``kind`` and ties it to the holder
    ``values`` under the hook name ``key``."""
    arguments = ARGUMENTS[kind](control)
    return f"{builder_of(control, kind)}(values, {key}, {arguments})"


def member_call(control: PanelControl) -> str:
    """The ``Member`` that builds a field of a cluster."""
    kind = control.kind
    assert kind is not None
    return (
        f"Member({control.key!r}, {control.label!r}, {kind.port_builder}, "
        f"{ARGUMENTS[kind](control)})"
    )


def builders_used(control: PanelControl, kind: PanelKind) -> set[str]:
    """The names of the ``controls`` package the call of ``control`` needs."""
    names = {builder_of(control, kind)}
    for member in control.members:
        assert member.kind is not None
        names |= {"Member", member.kind.port_builder}
        names |= builders_used(member, member.kind) - {builder_of(member, member.kind)}
    return names
