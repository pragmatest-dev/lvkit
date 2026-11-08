"""How the panel module calls the builder of each kind of control: the arguments
after the holder and the name, and the names the module imports for it."""

from __future__ import annotations

from collections.abc import Callable

from .kinds import PanelControl, PanelKind


def _number(control: PanelControl) -> str:
    return f"{control.default!r}, {control.integer}"


def _default_only(control: PanelControl) -> str:
    return repr(control.default)


def _choice(control: PanelControl) -> str:
    return f"{control.items!r}, {control.default!r}"


ARGUMENTS: dict[PanelKind, Callable[[PanelControl], str]] = {
    PanelKind.NUMBER: _number,
    PanelKind.BOOLEAN: _default_only,
    PanelKind.STRING: _default_only,
    PanelKind.PATH: _default_only,
    PanelKind.ENUM: _choice,
    PanelKind.RING: _choice,
}


def builder_of(control: PanelControl, kind: PanelKind) -> str:
    return kind.indicator_builder if control.is_indicator else kind.control_builder


def widget_call(control: PanelControl, kind: PanelKind, key: str) -> str:
    """The call that draws ``control`` of ``kind`` and ties it to the holder
    ``values`` under the hook name ``key``."""
    arguments = ARGUMENTS[kind](control)
    return f"{builder_of(control, kind)}(values, {key}, {arguments})"


def builders_used(control: PanelControl, kind: PanelKind) -> set[str]:
    """The names of the ``controls`` package the call of ``control`` needs."""
    return {builder_of(control, kind)}
