"""File Dialog: an unwired ``start path`` opens the dialog at the last directory
viewed (NI). The generated code shows no dialog and has no last directory, so the
start path it works with is Not A Path (``None``)."""

from __future__ import annotations

from lvkit.terminal_default import DefaultBehavior

from . import register_op_behaviors

register_op_behaviors("prim:8058", {DefaultBehavior.LAST_DIRECTORY: "None"})
