"""Unified primitive resolver with multi-strategy lookup.

Lookup order:
1. primResID -> exact match from primitives-codegen.json
2. Name -> exact name match from primitives-codegen.json or primitives-from-pdf.json
3. Exact type signature match
4. Compatible type match (polymorphic/adapt-to-type)
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class PrimitiveResolutionNeeded(Exception):
    """Raised when a primitive has no definition in primitives-codegen.json.

    Same pattern as VILibResolutionNeeded — the whole primitive is unknown,
    here are all its terminals from the graph with directions and types.
    """

    def __init__(
        self,
        prim_id: int | str,
        prim_name: str,
        terminals: list[dict[str, str | int | None]],
        vi_name: str | None = None,
    ):
        self.prim_id = str(prim_id)
        self.prim_name = prim_name
        self.terminals = terminals
        self.vi_name = vi_name
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = (
            f"Primitive resolution needed for {self.prim_id}"
            f" ({self.prim_name}).\n"
        )
        if self.vi_name:
            msg += f"  In VI: {self.vi_name}\n"
        msg += "  Wired terminals from graph:\n"
        for t in self.terminals:
            parts = [
                f"index={t['index']}",
                f"direction={t['direction']}",
                f"type={t['type']}",
            ]
            if t.get("name"):
                parts.append(f"name={t['name']}")
            msg += f"    - {' '.join(parts)}\n"
        if not self.terminals:
            msg += "    (none)\n"
        msg += (
            f"\n  Fix: add primitive {self.prim_id} to"
            f" data/primitives-codegen.json"
        )
        return msg


class TerminalResolutionNeeded(Exception):
    """Raised when a specific wired terminal cannot be resolved to a known index.

    The primitive definition exists, but a terminal's index doesn't match.
    """

    def __init__(
        self,
        prim_id: str | int,
        prim_name: str,
        terminal_direction: str,
        terminal_type: str | None,
        available: list[dict[str, str | int | None]],
        vi_name: str | None = None,
    ):
        self.prim_id = str(prim_id)
        self.prim_name = prim_name
        self.terminal_direction = terminal_direction
        self.terminal_type = terminal_type
        self.available = available
        self.vi_name = vi_name
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        msg = (
            f"Terminal resolution needed for primitive"
            f" {self.prim_id} ({self.prim_name}).\n"
        )
        if self.vi_name:
            msg += f"  In VI: {self.vi_name}\n"
        msg += (
            f"  Wired terminal: direction={self.terminal_direction},"
            f" type={self.terminal_type}\n"
        )
        msg += "  Available resolver terminals (same direction, unassigned):\n"
        for t in self.available:
            msg += (
                f"    - index={t['index']}"
                f" name={t['name']} type={t['type']}\n"
            )
        if not self.available:
            msg += "    (none available)\n"
        msg += (
            f"\n  Fix: add/update terminal in data/primitives-codegen.json"
            f" under primitive {self.prim_id}"
        )
        return msg


class PrimitiveTerminal(BaseModel):
    """A terminal on a primitive.

    Note: direction uses "in"/"out" (from JSON data), while
    graph_types.Terminal uses "input"/"output" (from parser).
    """
    index: int
    direction: str  # "in" or "out"
    name: str | None = ""
    # LabVIEW type: "numeric", "string", "boolean", "array",
    # "cluster", "path", "refnum", "variant", "polymorphic"
    type: str | None = None
    default_value: str | None = None
    expandable: bool = False  # True if this terminal can be resized (N instances)
    # Named DCO reference tag in XML (e.g., "srcDCO4", "delDCO")
    dco_ref: str | None = None


class PrimitiveEntry(BaseModel):
    """A primitive entry from JSON."""
    name: str = ""
    python_code: str | dict[str, str] | None = None
    inline: bool = True
    terminals: list[PrimitiveTerminal] = Field(default_factory=list)
    guess_reason: str | None = None
    imports: list[str] = Field(default_factory=list)


class ResolvedPrimitive(BaseModel):
    """Resolved primitive with full info."""
    prim_id: str | None = None
    name: str = ""
    python_code: str | dict[str, str] | None = None
    inline: bool = True
    terminals: list[PrimitiveTerminal] = Field(default_factory=list)
    confidence: str = "unknown"
    description: str = ""
    imports: list[str] = Field(default_factory=list)


def _collect_imports(prim: dict) -> list[str]:
    """Collect imports from both 'imports' (list) and '_import' (string) fields."""
    imports = list(prim.get("imports", []))
    imp = prim.get("_import")
    if isinstance(imp, str):
        imports.append(imp)
    elif isinstance(imp, list):
        imports.extend(imp)
    return imports


class PrimitiveResolver:
    """Multi-strategy primitive resolver."""

    def __init__(
        self,
        codegen_path: Path | str | None = None,
        pdf_path: Path | str | None = None,
    ):
        """Load primitive database.

        Args:
            codegen_path: Path to primitives-codegen.json
            pdf_path: Path to primitives-from-pdf.json
        """
        data_dir = Path(__file__).parent.parent.parent / "data"
        if codegen_path is None:
            codegen_path = data_dir / "primitives-codegen.json"
        if pdf_path is None:
            pdf_path = data_dir / "primitives-from-pdf.json"

        self._by_id: dict[str, dict] = {}
        self._by_name: dict[str, dict] = {}  # Normalized name -> primitive
        self._by_signature: dict[tuple, list[dict]] = {}
        # node_type -> info (aBuild, cpdArith, etc.)
        self._by_node_type: dict[str, dict] = {}
        self._type_aliases: dict[str, str] = {
            # Normalize type names
            "string": "String",
            "str": "String",
            "path": "Path",
            "boolean": "Boolean",
            "bool": "Boolean",
            "i16": "I16", "int16": "I16",
            "i32": "I32", "int32": "I32",
            "i64": "I64", "int64": "I64",
            "u16": "U16", "uint16": "U16",
            "u32": "U32", "uint32": "U32",
            "u64": "U64", "uint64": "U64",
            "dbl": "DBL", "float64": "DBL", "double": "DBL",
            "sgl": "SGL", "float32": "SGL", "single": "SGL",
            "ext": "EXT", "extended": "EXT",
            "variant": "Variant",
            "array": "Array",
            "cluster": "Cluster",
            "refnum": "Refnum",
        }

        # Compatible type pairs (can be used interchangeably)
        self._compatible_types: set[tuple[str, str]] = {
            ("Path", "String"),
            ("String", "Path"),
            ("I32", "I16"),
            ("I16", "I32"),
            ("DBL", "SGL"),
            ("SGL", "DBL"),
        }

        self._load_codegen(Path(codegen_path))
        self._load_pdf(Path(pdf_path))

    def _normalize_name(self, name: str) -> str:
        """Normalize primitive name for lookup."""
        # Remove common suffixes, lowercase, replace spaces/underscores
        n = name.lower().strip()
        for suffix in (" function", " vi", " primitive"):
            if n.endswith(suffix):
                n = n[:-len(suffix)]
        return n.replace(" ", "_").replace("-", "_")

    def _load_codegen(self, path: Path) -> None:
        """Load primitives from codegen file (with known IDs)."""
        if not path.exists():
            return

        with open(path) as f:
            data = json.load(f)

        primitives = data.get("primitives", {})

        for prim_id, prim_data in primitives.items():
            # Index by ID
            self._by_id[prim_id] = prim_data

            # Index by name (ID-based entries have priority)
            name = prim_data.get("name", "")
            if name:
                norm_name = self._normalize_name(name)
                self._by_name[norm_name] = {"id": prim_id, **prim_data}

            # Index by type signature if we have terminal info
            terminals = prim_data.get("terminals", [])
            if terminals:
                inputs = tuple(sorted(
                    self._normalize_type(t.get("name", ""))
                    for t in terminals if t.get("direction") == "in"
                ))
                outputs = tuple(sorted(
                    self._normalize_type(t.get("name", ""))
                    for t in terminals if t.get("direction") == "out"
                ))
                sig = (inputs, outputs)
                if sig not in self._by_signature:
                    self._by_signature[sig] = []
                self._by_signature[sig].append({"id": prim_id, **prim_data})

        # Load node_types section (aBuild, cpdArith, etc.)
        node_types = data.get("node_types", {})
        for node_type, info in node_types.items():
            self._by_node_type[node_type] = info

    def _load_pdf(self, path: Path) -> None:
        """Load primitives from PDF extraction (no IDs, for name-based lookup)."""
        if not path.exists():
            return

        with open(path) as f:
            pdf_prims = json.load(f)

        for python_name, prim_data in pdf_prims.items():
            # Only add if not already present (codegen has priority)
            norm_name = self._normalize_name(prim_data.get("name", python_name))
            if norm_name not in self._by_name:
                self._by_name[norm_name] = prim_data
            # Also index by python_name
            if python_name not in self._by_name:
                self._by_name[python_name] = prim_data

    def _normalize_type(self, type_str: str) -> str:
        """Normalize type string."""
        if not type_str:
            return ""
        # Handle TypeID(N) format
        if type_str.startswith("TypeID("):
            return type_str  # Keep as-is, we'll match by structure
        t = type_str.lower().strip()
        return self._type_aliases.get(t, type_str)

    def resolve(
        self,
        prim_id: int | str | None = None,
        name: str | None = None,
        input_types: list[str] | None = None,
        output_types: list[str] | None = None,
    ) -> ResolvedPrimitive | None:
        """Resolve a primitive using multi-strategy lookup.

        Args:
            prim_id: primResID (preferred lookup)
            name: Primitive name (for name-based lookup)
            input_types: Input type signatures
            output_types: Output type signatures

        Returns:
            ResolvedPrimitive or None
        """
        # Strategy 1: Exact ID match
        if prim_id is not None:
            prim_id_str = str(prim_id)
            if prim_id_str in self._by_id:
                prim = self._by_id[prim_id_str]
                confidence = (
                    "placeholder"
                    if prim.get("placeholder")
                    else "exact_id"
                )
                return ResolvedPrimitive(
                    prim_id=prim_id_str,
                    name=prim.get("name", f"primitive_{prim_id}"),
                    python_code=prim.get("python_code", ""),
                    inline=prim.get("inline", True),
                    terminals=[
                        PrimitiveTerminal.model_validate(t)
                        for t in prim.get("terminals", [])
                    ],
                    confidence=confidence,
                    description=prim.get("guess_reason", ""),
                    imports=_collect_imports(prim),
                )

        # Strategy 2: Name-based lookup
        if name is not None:
            result = self.resolve_by_name(name)
            if result:
                return result

        # Strategy 3 & 4: Type-based matching
        if input_types is not None and output_types is not None:
            result = self._match_by_types(input_types, output_types)
            if result:
                return result

        # Fallback: unknown primitive
        if prim_id is not None:
            return ResolvedPrimitive(
                prim_id=str(prim_id),
                name=f"unknown_primitive_{prim_id}",
                python_code="# TODO: unknown primitive",
                terminals=[],
                confidence="unknown",
            )

        return None

    def resolve_by_name(self, name: str) -> ResolvedPrimitive | None:
        """Resolve primitive by name.

        Args:
            name: Primitive name (e.g., "Build Array", "build_array",
                "Index Array Function")

        Returns:
            ResolvedPrimitive or None if not found
        """
        norm_name = self._normalize_name(name)
        if norm_name in self._by_name:
            prim = self._by_name[norm_name]
            return ResolvedPrimitive(
                prim_id=prim.get("id") or prim.get("prim_id"),
                name=prim.get("name", name),
                python_code=prim.get("python_code", ""),
                inline=prim.get("inline", True),
                terminals=[
                    PrimitiveTerminal.model_validate(t)
                    for t in prim.get("terminals", [])
                ],
                confidence="exact_name",
                description=prim.get("guess_reason", prim.get("category", "")),
                imports=_collect_imports(prim),
            )
        return None

    def resolve_by_node_type(self, node_type: str) -> ResolvedPrimitive | None:
        """Resolve by node_type (class name like aBuild, cpdArith).

        Args:
            node_type: The XML class name (e.g., "aBuild", "cpdArith")

        Returns:
            ResolvedPrimitive with name and any additional info, or None
        """
        if node_type in self._by_node_type:
            info = self._by_node_type[node_type]
            return ResolvedPrimitive(
                name=info.get("name", node_type),
                python_code=info.get("python_code"),
                inline=info.get("inline", True),
                terminals=[
                    PrimitiveTerminal.model_validate(t)
                    for t in info.get("terminals", [])
                ],
                confidence="node_type",
                description=info.get("description", ""),
                imports=_collect_imports(info),
            )
        return None

    def _match_by_types(
        self,
        input_types: list[str],
        output_types: list[str],
    ) -> ResolvedPrimitive | None:
        """Match by type signature."""
        in_norm = tuple(sorted(self._normalize_type(t) for t in input_types))
        out_norm = tuple(sorted(self._normalize_type(t) for t in output_types))

        # Exact type match
        sig = (in_norm, out_norm)
        if sig in self._by_signature:
            prim = self._by_signature[sig][0]
            return ResolvedPrimitive(
                prim_id=prim.get("id"),
                name=prim.get("name", "unknown"),
                python_code=prim.get("python_code", ""),
                inline=prim.get("inline", True),
                terminals=[
                    PrimitiveTerminal.model_validate(t)
                    for t in prim.get("terminals", [])
                ],
                confidence="exact_type",
            )

        # Compatible type match (adapt-to-type)
        best_match = None
        best_score = 0

        for (ref_in, ref_out), prims in self._by_signature.items():
            score = self._compatibility_score(in_norm, ref_in, out_norm, ref_out)
            if score > best_score:
                best_score = score
                best_match = prims[0]

        if best_match and best_score > 0:
            return ResolvedPrimitive(
                prim_id=best_match.get("id"),
                name=best_match.get("name", "unknown"),
                python_code=best_match.get("python_code", ""),
                inline=best_match.get("inline", True),
                terminals=[
                    PrimitiveTerminal.model_validate(t)
                    for t in best_match.get("terminals", [])
                ],
                confidence="compatible_type",
            )

        return None

    def _compatibility_score(
        self,
        actual_in: tuple,
        ref_in: tuple,
        actual_out: tuple,
        ref_out: tuple,
    ) -> int:
        """Score type compatibility (higher = better match)."""
        if len(actual_in) != len(ref_in) or len(actual_out) != len(ref_out):
            return 0

        score = 0
        for a, r in list(zip(actual_in, ref_in)) + list(zip(actual_out, ref_out)):
            if a == r:
                score += 2  # Exact match
            elif (a, r) in self._compatible_types:
                score += 1  # Compatible types
            else:
                return 0  # Incompatible

        return score

    def get_by_id(self, prim_id: int | str) -> dict | None:
        """Direct lookup by primResID."""
        return self._by_id.get(str(prim_id))

    def get_all_ids(self) -> list[str]:
        """Get all known primitive IDs."""
        return list(self._by_id.keys())

    def get_all_names(self) -> list[str]:
        """Get all known primitive names."""
        return list(self._by_name.keys())

    def stats(self) -> dict:
        """Get resolver statistics."""
        # Count primitives by source
        with_id = sum(
            1 for p in self._by_name.values() if p.get("id") or p.get("prim_id")
        )
        from_pdf = sum(
            1 for p in self._by_name.values()
            if p.get("source") == "NI PDF Documentation"
        )
        return {
            "primitives_by_id": len(self._by_id),
            "primitives_by_name": len(self._by_name),
            "primitives_with_known_id": with_id,
            "primitives_from_pdf": from_pdf,
            "type_signatures": len(self._by_signature),
        }

    def get_python_code(self, prim_id: int | str) -> str:
        """Get Python code for a primitive."""
        prim = self.get_by_id(prim_id)
        if prim:
            return prim.get("python_code", "")
        return ""

    def get_terminal_names(self, prim_id: int | str) -> tuple[list[str], list[str]]:
        """Get input and output terminal names.

        Returns:
            (input_names, output_names)
        """
        prim = self.get_by_id(prim_id)
        if not prim:
            return [], []

        terminals = prim.get("terminals", [])
        inputs = [t.get("name", f"in_{t['index']}")
                  for t in terminals if t.get("direction") == "in"]
        outputs = [t.get("name", f"out_{t['index']}")
                   for t in terminals if t.get("direction") == "out"]
        return inputs, outputs


# Global instance
_resolver: PrimitiveResolver | None = None


def get_resolver() -> PrimitiveResolver:
    """Get global resolver instance."""
    global _resolver
    if _resolver is None:
        _resolver = PrimitiveResolver()
    return _resolver


def resolve_primitive(
    prim_id: int | str | None = None,
    name: str | None = None,
    input_types: list[str] | None = None,
    output_types: list[str] | None = None,
) -> ResolvedPrimitive | None:
    """Convenience function for resolving primitives."""
    return get_resolver().resolve(prim_id, name, input_types, output_types)
