"""LabVIEW flag bit constants for terminal and control parsing."""

from .models import ParsedWiringRule

# Terminal flags (from objFlags/termFlags)
TERMINAL_IS_OUTPUT = 0x1  # Bit 0: terminal is output (vs input)

# Terminal DCO flags (from <dco><objFlags>)
# Compound Arithmetic (and similar) nodes: bit 16 set on a terminal's DCO
# means "invert this terminal" (Not before an input, Not after the output).
TERMINAL_DCO_INVERTED = 0x00010000  # Bit 16: terminal value is inverted
# Invoke Nodes: bit 16 on an ``invokeItem`` DCO marks the method row (its
# return value). LabVIEW stores that row as an output DCO and an input DCO; only
# the output is a terminal on the node.
INVOKE_ITEM_METHOD_ROW = 0x00010000  # Bit 16 on an invokeItem DCO


def is_method_row_input(dco_class: str | None, flags: int) -> bool:
    """Whether a DCO is the input half of an Invoke Node's method row, which
    has no terminal on the node (the return value is an output only)."""
    return (
        dco_class == "invokeItem"
        and bool(flags & INVOKE_ITEM_METHOD_ROW)
        and not is_output_terminal(flags)
    )


# Call Library Function Nodes: bit 16 on an ``extFuncArg`` DCO marks the
# return-value row. LabVIEW stores every row as an input terminal and an output
# terminal; the return value's input terminal is never wired and the node draws
# only its output.
EXT_FUNC_ARG_RETURN_VALUE = 0x00010000  # Bit 16 on an extFuncArg DCO


def is_ext_func_return_value(dco_class: str | None, flags: int) -> bool:
    """Whether a DCO is a Call Library Function Node's return-value row."""
    return dco_class == "extFuncArg" and bool(flags & EXT_FUNC_ARG_RETURN_VALUE)


# Front panel control flags
FP_IS_INDICATOR = 0x10000  # Bit 16: control is indicator (vs control)

# Connector-pane TypeDesc ``Flags`` (the Function TypeDesc that CONP points to,
# one child per connector slot). Bit 8 marks an output; bits 11 and 12 are the
# wiring rule and never both set: bit 12 = Required, bit 11 = Recommended,
# neither = Optional. Bit 15 marks a Dynamic Dispatch terminal (always
# alongside bit 12 on an input / bit 11 on an output), so it adds no state.
CONPANE_FLAG_RECOMMENDED = 0x0800  # Bit 11
CONPANE_FLAG_REQUIRED = 0x1000  # Bit 12


def is_output_terminal(flags: int) -> bool:
    """Check if terminal flags indicate an output terminal."""
    return bool(flags & TERMINAL_IS_OUTPUT)


def is_inverted_terminal(flags: int) -> bool:
    """Check if a terminal's DCO objFlags mark it as inverted ("Not")."""
    return bool(flags & TERMINAL_DCO_INVERTED)


def is_indicator(flags: int) -> bool:
    """Check if control flags indicate an indicator."""
    return bool(flags & FP_IS_INDICATOR)


def get_wiring_rule(flags: int) -> ParsedWiringRule:
    """Decode a connector-pane slot's wiring rule from its TypeDesc Flags."""
    if flags & CONPANE_FLAG_REQUIRED:
        return ParsedWiringRule.REQUIRED
    if flags & CONPANE_FLAG_RECOMMENDED:
        return ParsedWiringRule.RECOMMENDED
    return ParsedWiringRule.OPTIONAL
