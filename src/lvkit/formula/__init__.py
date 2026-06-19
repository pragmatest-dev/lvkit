"""LabVIEW Formula Node → Python transpilation.

A Formula Node (block-diagram class ``fBox``) holds a script in NI's
Formula-Node DSL — a restricted, sandboxed language the docs describe as
"similar to C": typed scalar locals, arrays that are wired terminals,
``if``/``for``/``while``/``do``, operators, and a fixed set of math
functions. There are no pointers and no allocation.

This package tokenizes and parses the script into a small AST (failing loud
on anything outside the supported grammar) and emits a deterministic,
self-contained **Python** function. The translation is mechanical — no AI
interprets the algorithm — and the result needs no C compiler, no FFI, and
no shared library: the generated module is pure Python that runs anywhere.
LabVIEW numeric semantics (int/int real division, round-to-nearest-even on
int assignment, fixed-width wrap) are reproduced via small ``lvkit.runtime.lv``
helpers. See ``formula.emit`` for the mapping.
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
