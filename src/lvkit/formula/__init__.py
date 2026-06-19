"""LabVIEW Formula Node → C compilation.

A Formula Node (block-diagram class ``fBox``) holds a script in NI's
Formula-Node DSL — a restricted language the docs describe as "similar to
C." It is ~99% C syntax; the deltas that a C compiler cannot accept are:

  * the ``**`` exponent operator (C has no power operator) — maps to ``pow``,
  * LabVIEW type keywords (``int16``, ``float32``, …) — handled by a typedef
    prelude,
  * a handful of function spellings (``int``, ``intrz``, ``ln``, …) — mapped
    to ``<math.h>`` or one-line helpers in the prelude,
  * float→int assignment rounding (LabVIEW rounds to nearest; C truncates).

This package tokenizes and parses the script into a small AST (failing loud
on anything outside the supported grammar) and emits a C translation unit
that compiles with a stock C compiler. No part of the algorithm is
interpreted into Python — the original logic runs as compiled C.
"""

from __future__ import annotations


class FormulaTranspileError(Exception):
    """Raised when a Formula Node script uses a construct we do not support.

    Carries 1-based line/column so the diagnostic points at the offending
    token rather than failing silently or emitting wrong code.
    """

    def __init__(self, message: str, line: int | None = None, col: int | None = None):
        self.line = line
        self.col = col
        loc = f" (line {line}, col {col})" if line is not None else ""
        super().__init__(f"{message}{loc}")
