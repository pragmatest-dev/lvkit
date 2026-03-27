"""Incremental terminal index collection for vilib VIs.

When the code generator encounters a vilib VI with missing terminal indices,
this module collects observations from the caller's dataflow context.
Over time, observations from multiple callers build confidence in terminal mappings.

Usage:
    collector = TerminalCollector()
    collector.observe(vi_name, caller_vi, node_terminals)
    collector.save()
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class TerminalObservation:
    """A single observation of terminal usage from a caller."""

    def __init__(
        self,
        index: int,
        direction: str,
        name: str | None = None,
        wire_type: str | None = None,
        type_info: Any | None = None,  # LVType when available
    ):
        self.index = index
        self.direction = direction  # "input" or "output"
        self.name = name  # Terminal name from connector pane if available
        self.wire_type = wire_type  # Type name string (backward compat)
        self.type_info = type_info  # Full LVType structure

    def to_dict(self) -> dict[str, Any]:
        result = {
            "index": self.index,
            "direction": self.direction,
            "name": self.name,
            "wire_type": self.wire_type,
        }
        if self.type_info:
            result["type_info"] = self._serialize_type(self.type_info)
        return result

    def _serialize_type(self, type_obj: Any) -> dict[str, Any] | str:
        """Serialize LVType to JSON-compatible dict."""
        # Import here to avoid circular dependency
        from vipy.graph_types import LVType

        if isinstance(type_obj, LVType):
            result = {
                "kind": type_obj.kind,
                "underlying_type": type_obj.underlying_type,
            }
            if type_obj.values:
                result["values"] = {
                    name: {"value": ev.value, "description": ev.description}
                    for name, ev in type_obj.values.items()
                }
            if type_obj.fields:
                result["fields"] = [
                    {"name": f.name, "type": self._serialize_type(f.type)}
                    for f in type_obj.fields
                ]
            if type_obj.element_type:
                result["element_type"] = self._serialize_type(type_obj.element_type)
            if type_obj.dimensions:
                result["dimensions"] = type_obj.dimensions
            if type_obj.typedef_path:
                result["typedef_path"] = type_obj.typedef_path
            return result
        else:
            # Fallback for non-LVType objects
            return str(type_obj)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TerminalObservation:
        return cls(
            index=data["index"],
            direction=data["direction"],
            name=data.get("name"),
            wire_type=data.get("wire_type"),
            type_info=data.get("type_info"),  # Store raw dict for now
        )


class TerminalCollector:
    """Collects terminal observations from caller dataflow.

    Observations are stored per VI and merged across callers.
    When enough observations agree, terminal indices can be inferred.
    """

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = Path(__file__).parent.parent.parent / "data"
        self.pending_file = data_dir / "vilib" / "_pending_terminals.json"
        self.data: dict[str, Any] = {"observations": {}}
        self._load()

    def _load(self) -> None:
        """Load existing observations."""
        if self.pending_file.exists():
            try:
                with open(self.pending_file) as f:
                    self.data = json.load(f)
            except (OSError, json.JSONDecodeError):
                self.data = {"observations": {}}

    def save(self) -> None:
        """Save observations to pending file."""
        self.pending_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.pending_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def observe(
        self,
        vi_name: str,
        caller_vi: str,
        node_terminals: list[dict[str, Any]],
        vilib_terminals: list[dict[str, Any]] | None = None,
    ) -> None:
        """Record terminal observations from a caller.

        Args:
            vi_name: The vilib VI being called
            caller_vi: The VI making the call
            node_terminals: Terminal data from the call node (has index, direction)
            vilib_terminals: Known terminal names from vilib JSON (for matching)
        """
        if vi_name not in self.data["observations"]:
            self.data["observations"][vi_name] = {
                "callers": [],
                "terminal_map": {},  # index -> inferred info
                "vilib_terminals": vilib_terminals or [],
            }

        vi_data = self.data["observations"][vi_name]

        # Build observation from node terminals
        terminals = []
        for term in node_terminals:
            # Support both dict and Terminal dataclass
            if hasattr(term, 'index'):  # Terminal dataclass
                index = term.index
                direction = term.direction
                name = term.name
                wire_type = term.python_type()  # String type name
                type_info = getattr(term, 'type_info', None)  # LVType if available
            else:  # dict
                index = term.get("index", -1)
                direction = term.get("direction", "unknown")
                name = term.get("name")
                wire_type = term.get("type")
                type_info = term.get("type_info")

            obs = TerminalObservation(
                index=index,
                direction=direction,
                name=name,
                wire_type=wire_type,
                type_info=type_info,
            )
            terminals.append(obs.to_dict())

            # Update terminal map with this observation
            idx = str(obs.index)
            if idx not in vi_data["terminal_map"]:
                vi_data["terminal_map"][idx] = {
                    "direction": obs.direction,
                    "observed_names": [],
                    "observed_types": [],
                    "observed_type_info": [],  # Full LVType structures
                    "count": 0,
                }

            term_info = vi_data["terminal_map"][idx]
            term_info["count"] += 1
            if obs.name and obs.name not in term_info["observed_names"]:
                term_info["observed_names"].append(obs.name)
            if obs.wire_type and obs.wire_type not in term_info["observed_types"]:
                term_info["observed_types"].append(obs.wire_type)
            if obs.type_info:
                # Serialize and store type_info if not already present
                type_dict = obs._serialize_type(obs.type_info)
                if type_dict not in term_info["observed_type_info"]:
                    term_info["observed_type_info"].append(type_dict)

        # Record this caller's observation
        vi_data["callers"].append({
            "caller_vi": caller_vi,
            "timestamp": datetime.now().isoformat(),
            "terminals": terminals,
        })

    def get_inferred_indices(self, vi_name: str) -> dict[str, dict[str, Any]] | None:
        """Get inferred terminal indices for a VI.

        Returns a dict mapping index -> terminal info if we have observations.
        """
        if vi_name not in self.data["observations"]:
            return None
        return self.data["observations"][vi_name].get("terminal_map")

    def match_terminals(
        self,
        vi_name: str,
        vilib_terminals: list[dict[str, Any]],
    ) -> list[tuple[int, str]] | None:
        """Try to match observed indices to vilib terminal names.

        Returns list of (index, terminal_name) tuples if matching is possible.
        """
        terminal_map = self.get_inferred_indices(vi_name)
        if not terminal_map:
            return None

        # Try to match by direction and type
        matches = []
        unmatched_vilib = list(vilib_terminals)

        for idx_str, info in terminal_map.items():
            idx = int(idx_str)
            direction = info["direction"]
            types = info["observed_types"]

            # Find matching vilib terminal
            for vt in unmatched_vilib:
                if vt.get("direction") == direction:
                    # Check type compatibility
                    vt_type = vt.get("type")
                    if vt_type and types and vt_type in types:
                        matches.append((idx, vt.get("name", "")))
                        unmatched_vilib.remove(vt)
                        break
                    elif not types:
                        # No type info, match by direction only if unambiguous
                        same_dir = [t for t in unmatched_vilib if t.get("direction") == direction]
                        if len(same_dir) == 1:
                            matches.append((idx, vt.get("name", "")))
                            unmatched_vilib.remove(vt)
                            break

        return matches if matches else None

    def report(self) -> str:
        """Generate a report of collected observations."""
        lines = ["# Pending Terminal Observations", ""]

        for vi_name, vi_data in self.data["observations"].items():
            lines.append(f"## {vi_name}")
            lines.append(f"Observed by {len(vi_data['callers'])} caller(s)")
            lines.append("")

            term_map = vi_data.get("terminal_map", {})
            if term_map:
                lines.append("| Index | Direction | Names | Types | Count |")
                lines.append("|-------|-----------|-------|-------|-------|")
                for idx in sorted(term_map.keys(), key=int):
                    info = term_map[idx]
                    names = ", ".join(info["observed_names"][:3]) or "-"
                    types = ", ".join(info["observed_types"][:3]) or "-"
                    lines.append(
                        f"| {idx} | {info['direction']} | {names} | {types} | {info['count']} |"
                    )
                lines.append("")

            vilib_terms = vi_data.get("vilib_terminals", [])
            if vilib_terms:
                lines.append("Known vilib terminals:")
                for t in vilib_terms:
                    lines.append(f"  - {t.get('name')} ({t.get('direction')})")
                lines.append("")

        return "\n".join(lines)


# Global collector instance
_collector: TerminalCollector | None = None


def get_collector() -> TerminalCollector:
    """Get the global terminal collector instance."""
    global _collector
    if _collector is None:
        _collector = TerminalCollector()
    return _collector
