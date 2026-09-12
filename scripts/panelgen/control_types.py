"""Single choke point mapping a LabVIEW FP ``control_type`` string to the
Python field type / default / NiceGUI widget it drives.

Every generator module that needs this mapping (``state_gen``, ``panel_gen``)
reads it from here — no per-site re-derivation of "what does stdBool mean".
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from lvkit.parser.models import ParsedFPControl


@dataclass(frozen=True)
class ControlTypeInfo:
    """What a control_type maps to in generated Python."""

    py_type: str  # annotation used in state.py, e.g. "str"
    default_literal: str  # Python source for a bare default, e.g. '""'
    widget: str  # nicegui.ui factory name, e.g. "input"


# The only place `control_type` strings are enumerated. A type absent here
# falls back to _UNKNOWN (str field, ui.input, TODO note) rather than
# raising — front panels routinely mix controls the generator doesn't know
# about yet, and skipping the whole panel over one odd control is worse than
# emitting a clearly-marked TODO widget for it.
_KNOWN: dict[str, ControlTypeInfo] = {
    "stdString": ControlTypeInfo("str", '""', "input"),
    "stdPath": ControlTypeInfo("str", '""', "input"),
    "stdNumeric": ControlTypeInfo("float", "0.0", "number"),
    "stdNum": ControlTypeInfo("float", "0.0", "number"),
    "stdBool": ControlTypeInfo("bool", "False", "switch"),
    "stdEnum": ControlTypeInfo("str", '""', "select"),
    "stdRing": ControlTypeInfo("str", '""', "select"),  # ring = dropdown, like enum
    # Arrays: an editable list, rendered by controls.array_control (composed
    # from native NiceGUI). indArr is the FP control_type for an array
    # control/indicator; "array" is a defensive alias.
    "indArr": ControlTypeInfo("list", "[]", "array"),
    "array": ControlTypeInfo("list", "[]", "array"),
}

_UNKNOWN = ControlTypeInfo("str", '""', "input")


def control_type_info(control_type: str) -> ControlTypeInfo:
    """Look up the mapping for a control_type, defaulting to a plain str
    ui.input with a TODO note (see panel_gen.render_widget) for anything not
    in ``_KNOWN`` — including "stdClust", which callers special-case before
    reaching here."""
    return _KNOWN.get(control_type, _UNKNOWN)


def is_known_control_type(control_type: str) -> bool:
    return control_type in _KNOWN


def default_source(control: ParsedFPControl) -> str:
    """Python source for this control's default value, honoring its
    control_type's decoded ``default_value`` (a string produced by
    ``lvkit.parser.vi._decode_default_data``) when present and well-formed,
    falling back to the control_type's zero-value otherwise.

    Note: `_parse_ddo` (lvkit/parser/vi.py) never decodes DefaultData for
    cluster CHILD controls -- it always recurses with ``default_data=None``
    -- so every ``stdClust`` child always falls through to its type's zero
    value here, regardless of what the VI's binary actually records. See the
    "FP data insufficiency" note in this generator's report.
    """
    info = control_type_info(control.control_type)
    raw = control.default_value

    if control.control_type == "stdPath":
        return repr(raw) if raw else info.default_literal

    if raw is None:
        return info.default_literal

    if control.control_type == "stdString":
        # Already a quoted Python string literal source, e.g. '"foo"'.
        try:
            ast.literal_eval(raw)
            return raw
        except (ValueError, SyntaxError):
            return info.default_literal

    if control.control_type in ("stdNumeric", "stdNum"):
        try:
            float(raw)
            return raw
        except ValueError:
            return info.default_literal

    if control.control_type == "stdBool":
        return "True" if raw.strip() == "True" else "False"

    if control.control_type == "stdEnum":
        try:
            ast.literal_eval(raw)
            return raw
        except (ValueError, SyntaxError):
            pass
        if control.enum_values:
            return repr(control.enum_values[0])
        return info.default_literal

    return info.default_literal
