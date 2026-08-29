"""Exceptions raised when a primitive or one of its terminals is unresolvable.

Both exceptions carry the graph-observed context (terminals, directions,
types) needed to fill in the missing data/primitives.json entry — see the
module docstring in ``lvkit.primitive_resolver`` for the overall lookup
strategy these exceptions are the escape hatch for.
"""

from __future__ import annotations


class PrimitiveResolutionNeeded(Exception):
    """Raised when a primitive has no definition in primitives.json.

    Same pattern as VILibResolutionNeeded — the whole primitive is unknown,
    here are all its terminals from the graph with directions and types.
    """

    def __init__(
        self,
        prim_id: int | str,
        prim_name: str,
        terminals: list[dict[str, str | int | bool | None]],
        vi_name: str | None = None,
        qualified_vi_name: str | None = None,
    ):
        self.prim_id = str(prim_id)
        self.prim_name = prim_name
        self.terminals = terminals
        self.vi_name = vi_name
        # Fully qualified name of the VI being converted (library + class +
        # name, e.g. "MyProject/utils/path_tools.lvlib:Build My Path.vi"),
        # so an LLM can find the source file. Optional — bare vi_name still
        # works as before.
        self.qualified_vi_name = qualified_vi_name
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = f"Primitive resolution needed for {self.prim_id} ({self.prim_name}).\n"
        if self.qualified_vi_name:
            msg += f"  In VI: {self.qualified_vi_name}\n"
        elif self.vi_name:
            msg += f"  In VI: {self.vi_name}\n"
        msg += (
            "  Full connector pane (every terminal the heap serialized — wired\n"
            "  AND unwired — with its declared type; identify by this whole\n"
            "  signature, not just the wired ones):\n"
        )
        for t in self.terminals:
            parts = [
                f"index={t['index']}",
                f"direction={t['direction']}",
                f"type={t['type']}",
            ]
            if "wired" in t:
                parts.append("wired" if t["wired"] else "UNWIRED")
            if t.get("name"):
                parts.append(f"name={t['name']}")
            msg += f"    - {' '.join(parts)}\n"
        if not self.terminals:
            msg += "    (none)\n"
        msg += (
            f"\n  Fix: add primitive {self.prim_id} to"
            f" .lvkit/primitives.json (project-local) or"
            f" data/primitives.json (cleanroom upstream)"
        )
        return msg


class TerminalResolutionNeeded(Exception):
    """Raised when a specific wired terminal cannot be resolved to a known index.

    kind="primitive": a built-in LabVIEW primitive with a missing terminal index
    kind="vilib":     a vilib VI with a missing terminal index in data/vilib/
    kind="subvi":     a user project VI whose terminal name could not be resolved
    """

    def __init__(
        self,
        prim_id: str | int,
        prim_name: str,
        terminal_direction: str,
        terminal_type: str | None,
        available: list[dict[str, str | int | None]],
        vi_name: str | None = None,
        kind: str = "primitive",
    ):
        self.prim_id = str(prim_id)
        self.prim_name = prim_name
        self.terminal_direction = terminal_direction
        self.terminal_type = terminal_type
        self.available = available
        self.vi_name = vi_name
        self.kind = kind
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.kind == "vilib":
            header = f"Terminal resolution needed for vilib VI '{self.prim_name}'."
        elif self.kind == "subvi":
            header = f"Terminal resolution needed for project VI '{self.prim_name}'."
        else:
            header = (
                f"Terminal resolution needed for primitive"
                f" {self.prim_id} ({self.prim_name})."
            )
        msg = header + "\n"
        if self.vi_name:
            msg += f"  In VI: {self.vi_name}\n"
        msg += (
            f"  Wired terminal: direction={self.terminal_direction},"
            f" type={self.terminal_type}\n"
        )
        msg += "  Available resolver terminals (same direction, unassigned):\n"
        for t in self.available:
            msg += f"    - index={t['index']} name={t['name']} type={t['type']}\n"
        if not self.available:
            msg += "    (none available)\n"
        if self.kind == "vilib":
            msg += (
                f"\n  Fix: add/update terminal index in"
                f" .lvkit/data/vilib/<category>.json (project-local)"
                f" or data/vilib/<category>.json (upstream)"
                f" under VI '{self.prim_name}'"
            )
        elif self.kind == "subvi":
            msg += (
                f"\n  Fix: ensure '{self.prim_name}' is reachable via --search-path"
                f" and its terminal names are present in the VI's front panel"
            )
        else:
            msg += (
                f"\n  Fix: add primitive {self.prim_id} to"
                f" .lvkit/data/primitives.json (project-local)"
                f" or data/primitives.json (upstream)"
            )
        return msg
