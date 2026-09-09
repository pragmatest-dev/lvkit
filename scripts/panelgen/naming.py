"""Single source of truth for turning a ``ParsedFPControl`` name into a
Python identifier -- state_gen and panel_gen both call this, so a State
dataclass field and the widget that ``bind_value``s to it always agree on
the field name.
"""

from __future__ import annotations

from lvkit.codegen.ast_utils import to_var_name
from lvkit.parser.models import ParsedFPControl


def unique_field_names(controls: list[ParsedFPControl]) -> dict[str, str]:
    """control.uid -> deduped, valid Python field name (declaration order)."""
    used: dict[str, int] = {}
    names: dict[str, str] = {}
    for control in controls:
        base = to_var_name(control.name)
        count = used.get(base, 0)
        used[base] = count + 1
        names[control.uid] = base if count == 0 else f"{base}_{count + 1}"
    return names


def pascal_case(name: str) -> str:
    parts = [p for p in to_var_name(name).split("_") if p]
    return "".join(p.capitalize() for p in parts) or "Cluster"
