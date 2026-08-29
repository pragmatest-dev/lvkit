"""Resolution exceptions and diagnostic context for vilib VIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolutionContext:
    """Context passed to VILibResolutionNeeded for diagnostics."""

    caller_vi: str | None = None
    poly_selector: str | None = None
    wire_types: list[str] = field(default_factory=list)
    terminal_names: list[str] = field(default_factory=list)
    # Fully qualified on-disk path of the unknown SubVI, e.g.
    # "<vilib>/Utility/error.llb/Error Cluster From Error Code.vi".
    # Lets an LLM with access to a real LabVIEW install find the source.
    qualified_path: str | None = None
    # Fully qualified name of the caller VI (library + class + name),
    # complementing caller_vi which may just be the bare name.
    caller_qualified_name: str | None = None


class VILibResolutionNeeded(Exception):
    """Raised when vi.lib terminal info is missing.

    Claude should use the VI dependencies in the files being processed
    to figure out terminal information and add Python hints based on context.
    """

    def __init__(self, vi_name: str, context: ResolutionContext | None = None):
        self.vi_name = vi_name
        self.context = context or ResolutionContext()
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"VILib resolution needed for '{self.vi_name}'.\n"

        if self.context.qualified_path:
            msg += f"\nQualified path: {self.context.qualified_path}"
            msg += (
                "\n  (Open this file in a LabVIEW install to read the real"
                " terminal layout)"
            )

        if self.context.poly_selector:
            msg += f"\nPolymorphic selector: {self.context.poly_selector}"
            msg += "\n  (Add this to poly_selector_names in the variant's JSON entry)"

        caller_label = self.context.caller_qualified_name or self.context.caller_vi
        if caller_label:
            msg += f"\nCaller VI: {caller_label}"

        if self.context.terminal_names:
            msg += "\n\nTerminal names from XML:\n"
            for name in self.context.terminal_names:
                msg += f"  - {name}\n"

        if self.context.wire_types:
            msg += "\n\nWire types from dataflow:\n"
            for wt in self.context.wire_types:
                msg += f"  - {wt}\n"

        msg += (
            "\nFix: add terminal info to .lvkit/vilib/<category>.json"
            " (project-local) or data/vilib/<category>.json"
            " (cleanroom upstream)"
        )
        return msg


class VILibConflict(Exception):
    """Terminal conflict detected across callers."""

    def __init__(self, vi_name: str, conflicts: list[dict[str, Any]]):
        self.vi_name = vi_name
        self.conflicts = conflicts
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"Terminal conflict for '{self.vi_name}'.\n\nConflicts:\n"
        for c in self.conflicts:
            msg += f"  Index {c['index']} ({c['field']}): "
            msg += f"{c['existing']} → {c['observed']}\n"
        msg += "\nSee data/vilib/_pending_terminals.json"
        return msg
