"""LabVIEW flag bit constants for terminal and control parsing."""

# Terminal flags (from objFlags/termFlags)
TERMINAL_IS_OUTPUT = 0x1  # Bit 0: terminal is output (vs input)

# Front panel control flags
FP_IS_INDICATOR = 0x10000  # Bit 16: control is indicator (vs control)

# Wiring rule extraction
WIRING_RULE_SHIFT = 8
WIRING_RULE_MASK = 0x03  # 2 bits for wiring rule


def is_output_terminal(flags: int) -> bool:
    """Check if terminal flags indicate an output terminal."""
    return bool(flags & TERMINAL_IS_OUTPUT)


def is_indicator(flags: int) -> bool:
    """Check if control flags indicate an indicator."""
    return bool(flags & FP_IS_INDICATOR)


def get_wiring_rule(flags: int) -> int:
    """Extract wiring rule from connector pane flags."""
    return (flags >> WIRING_RULE_SHIFT) & WIRING_RULE_MASK
