"""Lookup LabVIEW primitives by type signature using scraped API reference."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrimitiveInfo:
    """Information about a LabVIEW primitive."""
    name: str
    python_name: str  # snake_case version
    inputs: list[tuple[str, str]]  # [(name, type), ...]
    outputs: list[tuple[str, str]]  # [(name, type), ...]
    description: str = ""
    url: str = ""


class PrimitiveLookup:
    """Look up primitives by type signature."""

    def __init__(self, api_data_path: Path | str | None = None):
        """Load API reference data.

        Args:
            api_data_path: Path to labview-api-scraped.json.
                          If None, uses default location.
        """
        if api_data_path is None:
            # Default to data/ directory relative to this file
            api_data_path = (
                Path(__file__).parent.parent.parent
                / "data" / "labview-api-scraped.json"
            )

        self.primitives: list[PrimitiveInfo] = []
        self._by_signature: dict[
            tuple[tuple[str, ...], tuple[str, ...]], list[PrimitiveInfo]
        ] = {}

        self._load(Path(api_data_path))

    def _load(self, path: Path) -> None:
        """Load and index the API reference data."""
        if not path.exists():
            return

        with open(path) as f:
            data = json.load(f)

        for func in data:
            name = func.get("name", "")
            inputs = [(i.get("name", ""), self._normalize_type(i.get("type", "")))
                     for i in func.get("inputs", [])]
            outputs = [(o.get("name", ""), self._normalize_type(o.get("type", "")))
                      for o in func.get("outputs", [])]

            info = PrimitiveInfo(
                name=name,
                python_name=self._to_python_name(name),
                inputs=inputs,
                outputs=outputs,
                description=func.get("description", ""),
                url=func.get("url", ""),
            )
            self.primitives.append(info)

            # Index by type signature
            sig = self._make_signature(inputs, outputs)
            if sig not in self._by_signature:
                self._by_signature[sig] = []
            self._by_signature[sig].append(info)

    def _normalize_type(self, t: str) -> str:
        """Normalize type names for matching."""
        t = t.lower().strip()
        # Map common variations
        type_map = {
            "string": "string",
            "str": "string",
            "path": "path",
            "boolean": "boolean",
            "bool": "boolean",
            "i16": "int16",
            "i32": "int32",
            "i64": "int64",
            "u16": "uint16",
            "u32": "uint32",
            "u64": "uint64",
            "dbl": "float64",
            "sgl": "float32",
            "numint16": "int16",
            "numint32": "int32",
            "numfloat64": "float64",
            "numfloat32": "float32",
            "polymorphic": "*",  # Wildcard
            "any": "*",
        }
        return type_map.get(t, t)

    def _make_signature(
        self,
        inputs: list[tuple[str, str]],
        outputs: list[tuple[str, str]]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Create a signature tuple from inputs/outputs."""
        in_types = tuple(sorted(t for _, t in inputs))
        out_types = tuple(sorted(t for _, t in outputs))
        return (in_types, out_types)

    def _to_python_name(self, name: str) -> str:
        """Convert function name to Python snake_case."""
        # Remove "Function", "VI" suffix
        name = name.replace(" Function", "").replace(" VI", "")
        # Convert to snake_case
        result = name.lower().replace(" ", "_").replace("-", "_")
        result = "".join(c for c in result if c.isalnum() or c == "_")
        return result or "unknown"

    def find_by_types(
        self,
        input_types: list[str],
        output_types: list[str],
    ) -> list[PrimitiveInfo]:
        """Find primitives matching the given type signature.

        Args:
            input_types: List of input types (e.g., ["Path", "Path"])
            output_types: List of output types (e.g., ["Path", "String"])

        Returns:
            List of matching primitives, sorted by match quality (exact first)
        """
        # Normalize input types
        in_norm = tuple(sorted(self._normalize_type(t) for t in input_types))
        out_norm = tuple(sorted(self._normalize_type(t) for t in output_types))

        # Exact match
        sig = (in_norm, out_norm)
        if sig in self._by_signature:
            return self._by_signature[sig]

        # Try matching with wildcards/compatibility, score by match quality
        scored_matches: list[tuple[int, PrimitiveInfo]] = []
        for (ref_in, ref_out), primitives in self._by_signature.items():
            if (
                self._types_match(in_norm, ref_in)
                and self._types_match(out_norm, ref_out)
            ):
                # Score: higher is better (exact matches score higher)
                score = (
                    self._match_score(in_norm, ref_in)
                    + self._match_score(out_norm, ref_out)
                )
                for p in primitives:
                    scored_matches.append((score, p))

        # Sort by score descending, return just the primitives
        scored_matches.sort(key=lambda x: -x[0])
        return [p for _, p in scored_matches]

    def _match_score(self, actual: tuple[str, ...], reference: tuple[str, ...]) -> int:
        """Score how well types match (higher = better)."""
        if len(actual) != len(reference):
            return 0
        score = 0
        for a, r in zip(actual, reference):
            if a == r:
                score += 2  # Exact match
            elif a == "*" or r == "*":
                score += 1  # Wildcard
            else:
                score += 0  # Compatible but not exact
        return score

    def _types_match(self, actual: tuple[str, ...], reference: tuple[str, ...]) -> bool:
        """Check if actual types match reference (with wildcard and flexibility).

        Handles:
        - Wildcards (*) match anything
        - Path and String are often interchangeable in LabVIEW
        - Order-independent matching (sorted tuples)
        """
        if len(actual) != len(reference):
            return False

        # Compatible types that can match each other
        compatible = {
            ("path", "string"),
            ("string", "path"),
        }

        for a, r in zip(actual, reference):
            if r == "*":  # Wildcard matches anything
                continue
            if a == "*":  # Actual is polymorphic, matches anything
                continue
            if a == r:
                continue
            if (a, r) in compatible:
                continue
            return False
        return True

    def find_by_name(self, name: str) -> PrimitiveInfo | None:
        """Find a primitive by name (case-insensitive)."""
        name_lower = name.lower()
        for prim in self.primitives:
            if prim.name.lower() == name_lower:
                return prim
            if prim.python_name == name_lower:
                return prim
        return None


# Global instance for convenience
_lookup: PrimitiveLookup | None = None


def get_primitive_lookup() -> PrimitiveLookup:
    """Get the global primitive lookup instance."""
    global _lookup
    if _lookup is None:
        _lookup = PrimitiveLookup()
    return _lookup


def lookup_primitive(
    input_types: list[str],
    output_types: list[str],
) -> str | None:
    """Look up a primitive name by its type signature.

    Args:
        input_types: List of input types
        output_types: List of output types

    Returns:
        Python function name if found, None otherwise
    """
    lookup = get_primitive_lookup()
    matches = lookup.find_by_types(input_types, output_types)
    if matches:
        # Return first match's python name
        return matches[0].python_name
    return None
