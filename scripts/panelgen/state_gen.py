"""Generates the ``@binding.bindable_dataclass State`` — one field per
front-panel control (input) and indicator (output), defaulted from the VI's own
recorded ``default_value`` where available. A ``stdClust`` control becomes a
nested bindable dataclass, recursively (``ClusterStrategy.state_field``).

This is the UI-layer VIEW-MODEL: every field is a NiceGUI ``BindableProperty``,
so the panel can ``bind_value``/``bind_value_from`` widgets to it and outputs
update reactively. ``build_state_classes`` returns just the class blocks, which
``panel_gen`` inlines into the ``<vi>_panel.py`` file (the State lives with the
UI, beside the pure ``<vi>.py`` logic that has no UI imports).
"""

from __future__ import annotations

from lvkit.parser.models import ParsedFPControl, ParsedFrontPanel

from .naming import unique_field_names
from .strategies import strategy_for

_MODULE_HEADER = '''"""Front-panel view-model: one bindable field per control/
indicator, typed and defaulted from the VI's own front panel. Every field is a
NiceGUI BindableProperty (via @binding.bindable_dataclass) so panel.py can bind
widgets to it; the pure logic in logic.py stays UI-free.

Standalone module for tests/callers that want the view-model on its own --
NOT what generation emits: ``panel_gen`` builds its own inline header (this
one predates a shared header build and intentionally isn't unified with it,
see ``build_state_module``)."""

from __future__ import annotations

from dataclasses import field

from nicegui import binding
'''


def build_state_classes(front_panel: ParsedFrontPanel) -> str:
    """The ``@binding.bindable_dataclass`` blocks (``State`` plus any nested
    cluster classes) for one VI's front panel, children before parents. No
    module header — ``panel_gen`` inlines this into the panel file."""
    used_class_names: dict[str, int] = {}
    class_blocks: list[str] = []  # filled depth-first, so children precede parents

    def reserve_class_name(base: str) -> str:
        count = used_class_names.get(base, 0)
        used_class_names[base] = count + 1
        return base if count == 0 else f"{base}{count + 1}"

    def emit_class(controls: list[ParsedFPControl], class_name: str) -> None:
        field_names = unique_field_names(controls)
        field_lines = [
            strategy_for(control).state_field(
                field_names[control.uid], reserve_class_name, emit_class
            )
            for control in controls
        ]
        body = "\n".join(field_lines) if field_lines else "    pass"
        class_blocks.append(
            f"@binding.bindable_dataclass\nclass {class_name}:\n{body}"
        )

    emit_class(front_panel.controls, "State")
    return "\n\n\n".join(class_blocks)


def build_state_module(front_panel: ParsedFrontPanel) -> str:
    """The full standalone state module (header + classes). Kept for tests /
    callers that want the view-model on its own."""
    return _MODULE_HEADER + "\n\n" + build_state_classes(front_panel) + "\n"
