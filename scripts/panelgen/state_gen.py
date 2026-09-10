"""Generates ``state.py``: a plain ``@dataclass State`` with one field per
front-panel control (inputs) and indicator (outputs), defaulted from the
VI's own recorded ``default_value`` where available. A ``stdClust`` control
becomes a nested dataclass field, recursively. No UI imports -- ``panel.py``
binds widgets to this, never the other way round.
"""

from __future__ import annotations

from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .control_types import control_type_info, default_source
from .naming import pascal_case, unique_field_names

_EMPTY_FACTORY = {"[]": "list", "list()": "list", "{}": "dict", "dict()": "dict"}


def _field_default_rhs(default: str) -> str:
    """A dataclass forbids a bare mutable default (``list``/``dict``), so route
    any mutable literal through ``default_factory``. Keyed on the literal, not
    on a control type — so a future decoded array/cluster default is handled the
    same way as today's empty ``[]``."""
    stripped = default.strip()
    if stripped in _EMPTY_FACTORY:
        return f"field(default_factory={_EMPTY_FACTORY[stripped]})"
    if stripped[:1] in "[{":
        return f"field(default_factory=lambda: {stripped})"
    return default

_MODULE_HEADER = '''"""Front-panel state: one field per control/indicator, typed
and defaulted from the VI's own front panel. No UI imports -- panel.py
binds to this."""

from __future__ import annotations

from dataclasses import dataclass, field
'''


def build_state_module(front_panel: ParsedFrontPanel) -> str:
    """Build the full ``state.py`` source for one VI's front panel."""
    used_class_names: dict[str, int] = {}
    class_blocks: list[str] = []  # filled depth-first, so children precede parents

    def reserve_class_name(base: str) -> str:
        count = used_class_names.get(base, 0)
        used_class_names[base] = count + 1
        return base if count == 0 else f"{base}{count + 1}"

    def emit_class(controls: list[ParsedFPControl], class_name: str) -> None:
        field_names = unique_field_names(controls)
        field_lines: list[str] = []
        for control in controls:
            fname = field_names[control.uid]
            if control.control_type == "stdClust":
                nested_name = reserve_class_name(pascal_case(control.name) + "State")
                emit_class(control.children, nested_name)
                field_lines.append(
                    f"    {fname}: {nested_name} = "
                    f"field(default_factory={nested_name})"
                )
            else:
                info = control_type_info(control.control_type)
                rhs = _field_default_rhs(default_source(control))
                field_lines.append(f"    {fname}: {info.py_type} = {rhs}")
        body = "\n".join(field_lines) if field_lines else "    pass"
        class_blocks.append(f"@dataclass\nclass {class_name}:\n{body}")

    emit_class(front_panel.controls, "State")
    return _MODULE_HEADER + "\n\n" + "\n\n\n".join(class_blocks) + "\n"
