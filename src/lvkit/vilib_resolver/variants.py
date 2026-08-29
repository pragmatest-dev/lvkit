"""Polymorphic-variant discovery, matching, and JSON persistence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lvkit.models import LVType, LVTypeKind

from .models import VIEntry, VITerminal
from .naming import derive_python_name


class _VILibVariantMixin:
    """Match, discover, and persist polymorphic vilib VI variants."""

    # Instance attributes populated by _VILibLoaderMixin.__init__. Declared
    # here (annotation only, no assignment — zero runtime effect) so pyright
    # can type-check attribute access within this mixin.
    data_dir: Path
    _types: dict[str, LVType]
    _variants: dict[str, list[VIEntry]]
    _category_files: dict[str, Path]

    # Implemented by _VILibLookupMixin. Declared here (stub body, never
    # executed) only so pyright can type-check the call below — on the
    # composed VILibResolver, MRO always resolves to the real
    # implementation since _VILibLookupMixin precedes this mixin.
    def resolve_by_name(self, vi_name: str) -> VIEntry | None: ...

    def _compute_signature(self, terminals: dict[int, dict[str, Any]]) -> str:
        """Compute a signature from terminal observations.

        The signature captures the terminal types at each index, which
        is what distinguishes polymorphic variants.
        """
        parts = []
        for idx in sorted(terminals.keys()):
            term = terminals[idx]
            type_str = term.get("type") or "any"
            # Simplify typedef paths to just the filename
            if "/" in type_str:
                type_str = type_str.split("/")[-1].replace(".ctl", "")
            direction = term.get("direction", "?")[0] if term.get("direction") else "?"
            parts.append(f"{idx}:{direction}:{type_str}")
        return "|".join(parts)

    def find_matching_variant(
        self,
        vi_name: str,
        observed_terminals: dict[int, dict[str, Any]],
    ) -> VIEntry | None:
        """Find the best matching variant for observed terminals.

        Args:
            vi_name: VI filename like "Get System Directory.vi"
            observed_terminals: Dict of index -> terminal info from caller

        Returns:
            Best matching VIEntry, or None if no match
        """
        # Check base entry first
        base = self.resolve_by_name(vi_name)
        if base:
            # Check if base entry matches all observed terminals
            base_map = {t.index: t for t in base.terminals if t.index is not None}
            all_match = True
            for idx, obs in observed_terminals.items():
                if idx in base_map:
                    existing = base_map[idx]
                    if (
                        existing.name
                        and obs.get("name")
                        and existing.name != obs.get("name")
                    ):
                        all_match = False
                        break
                    if (
                        existing.direction
                        and obs.get("direction")
                        and existing.direction != obs.get("direction")
                    ):
                        all_match = False
                        break
            if all_match:
                return base

        # Check variants
        if vi_name not in self._variants:
            return base  # No variants, return base even if imperfect

        best_match: VIEntry | None = None
        best_score = -1

        for variant in self._variants[vi_name]:
            variant_map = {t.index: t for t in variant.terminals if t.index is not None}
            score = 0
            mismatch = False

            for idx, obs in observed_terminals.items():
                if idx in variant_map:
                    existing = variant_map[idx]
                    # Check for conflicts
                    if (
                        existing.name
                        and obs.get("name")
                        and existing.name != obs.get("name")
                    ):
                        mismatch = True
                        break
                    if (
                        existing.direction
                        and obs.get("direction")
                        and existing.direction != obs.get("direction")
                    ):
                        mismatch = True
                        break
                    # Matching terminal adds to score
                    score += 1
                    if existing.type == obs.get("type"):
                        score += 1  # Extra point for type match

            if not mismatch and score > best_score:
                best_score = score
                best_match = variant

        return best_match or base

    def _create_variant(
        self,
        vi_name: str,
        observed_terminals: dict[int, dict[str, Any]],
        base_entry: VIEntry,
        caller_vi: str | None = None,
    ) -> VIEntry:
        """Create a new polymorphic variant from observations.

        Args:
            vi_name: VI filename
            observed_terminals: Terminal observations from caller
            base_entry: The base VI entry to clone from
            caller_vi: Name of calling VI (for tracking)

        Returns:
            Newly created variant entry
        """
        signature = self._compute_signature(observed_terminals)

        # Create variant entry
        variant = VIEntry(
            name=vi_name,
            vi_path=base_entry.vi_path,
            category=base_entry.category,
            description=f"Variant observed from {caller_vi or 'unknown'}",
            terminals=[],
            python=base_entry.python,
            inline=base_entry.inline,
            imports=base_entry.imports.copy(),
            status="auto_variant",
            variant_signature=signature,
            is_variant=True,
        )

        # Copy terminals from observed data
        for idx, obs in observed_terminals.items():
            variant.terminals.append(
                VITerminal(
                    name=obs.get("name", ""),
                    index=idx,
                    direction=obs.get("direction"),
                    type=obs.get("type"),
                )
            )

        # Store variant
        if vi_name not in self._variants:
            self._variants[vi_name] = []
        self._variants[vi_name].append(variant)

        # Save to pending for review (variants need human verification)
        self._add_variant_to_pending(vi_name, variant, caller_vi)

        return variant

    def _add_variant_to_pending(
        self,
        vi_name: str,
        variant: VIEntry,
        caller_vi: str | None,
    ) -> None:
        """Save discovered variant to _pending_terminals.json for review."""
        pending_file = self.data_dir / "vilib" / "_pending_terminals.json"

        if pending_file.exists():
            with open(pending_file, encoding="utf-8") as f:
                pending_data = json.load(f)
        else:
            pending_data = {"conflicts": {}, "variants": {}}

        if "variants" not in pending_data:
            pending_data["variants"] = {}

        if vi_name not in pending_data["variants"]:
            pending_data["variants"][vi_name] = []

        variant_entry = {
            "signature": variant.variant_signature,
            "caller_vi": caller_vi,
            "terminals": [
                {
                    "index": t.index,
                    "name": t.name,
                    "direction": t.direction,
                    "type": t.type,
                }
                for t in variant.terminals
            ],
        }

        # Don't add duplicate signatures
        existing_sigs = {v.get("signature") for v in pending_data["variants"][vi_name]}
        if variant.variant_signature not in existing_sigs:
            pending_data["variants"][vi_name].append(variant_entry)

            pending_file.parent.mkdir(parents=True, exist_ok=True)
            with open(pending_file, "w", encoding="utf-8") as f:
                json.dump(pending_data, f, indent=2)

    def auto_update_terminals(
        self,
        vi_name: str,
        wired_terminals: list[Any],
        caller_vi: str | None = None,
    ) -> VIEntry:
        """Auto-update VI terminals from caller observations.

        Creates new typedefs in _types.json as needed.
        On conflicts, creates a polymorphic variant instead of failing.
        Observations are always trusted - they come from actual wire connections.
        """
        vi = self.resolve_by_name(vi_name)
        if not vi:
            raise ValueError(f"VI not found: {vi_name}")

        existing_map: dict[int, VITerminal] = {
            t.index: t for t in vi.terminals if t.index is not None
        }

        observed_map: dict[int, dict[str, Any]] = {}
        for wired_term in wired_terminals:
            if wired_term.index < 0:
                continue  # Unresolved — should be resolved during graph construction

            lv_type = getattr(wired_term, "lv_type", None)
            type_str = None

            if lv_type:
                if lv_type.kind == LVTypeKind.TYPEDEF_REF and lv_type.typedef_path:
                    type_str = lv_type.typedef_path
                    # Auto-create typedef if needed
                    self._ensure_typedef(lv_type)
                elif lv_type.underlying_type:
                    type_str = lv_type.underlying_type
            elif hasattr(wired_term, "type"):
                type_str = wired_term.type

            observed_map[wired_term.index] = {
                "name": wired_term.name or "",
                "direction": wired_term.direction,
                "type": type_str,
            }

        # Check for conflicts with base entry
        has_conflict = False
        for idx, obs_data in observed_map.items():
            if idx in existing_map:
                existing = existing_map[idx]
                if (
                    existing.name
                    and obs_data["name"]
                    and existing.name != obs_data["name"]
                ):
                    has_conflict = True
                    break
                if (
                    existing.direction
                    and obs_data["direction"]
                    and existing.direction != obs_data["direction"]
                ):
                    has_conflict = True
                    break

        if has_conflict:
            # Conflict detected - this is likely a polymorphic variant
            # Check if we already have a matching variant
            matching = self.find_matching_variant(vi_name, observed_map)
            if matching and matching.is_variant:
                # Update existing variant with any new info
                self._update_variant_terminals(matching, observed_map)
                return matching

            # Create a new variant from observation
            return self._create_variant(vi_name, observed_map, vi, caller_vi)

        # No conflict - update base entry
        updated = False
        unmatched_obs: list[tuple[int, dict[str, Any]]] = []
        for idx, obs_data in observed_map.items():
            if idx in existing_map:
                term = existing_map[idx]
                if not term.direction and obs_data["direction"]:
                    term.direction = obs_data["direction"]
                    updated = True
                if not term.type and obs_data["type"]:
                    term.type = obs_data["type"]
                    updated = True
            else:
                # Try name-based matching first
                matched = False
                if obs_data["name"]:
                    for term in vi.terminals:
                        if term.name == obs_data["name"] and term.index is None:
                            term.index = idx
                            term.direction = obs_data["direction"]
                            term.type = obs_data["type"]
                            updated = True
                            matched = True
                            break
                if not matched:
                    unmatched_obs.append((idx, obs_data))

        # Fallback 1: Match by type when exactly one null-index terminal
        # shares the type AND direction (unambiguous type match).
        if unmatched_obs:
            null_terms = [t for t in vi.terminals if t.index is None]
            still_unmatched = []
            for idx, obs_data in unmatched_obs:
                obs_dir = obs_data["direction"]
                obs_type = obs_data["type"]
                # Try type + direction match
                candidates = (
                    [
                        t
                        for t in null_terms
                        if t.direction == obs_dir and t.type == obs_type
                    ]
                    if obs_type
                    else []
                )
                if len(candidates) == 1:
                    t = candidates[0]
                    t.index = idx
                    if obs_data["direction"]:
                        t.direction = obs_data["direction"]
                    if obs_data["type"]:
                        t.type = obs_data["type"]
                    null_terms.remove(t)
                    updated = True
                else:
                    still_unmatched.append((idx, obs_data))

            # Fallback 2: Match by direction alone when exactly one
            # null-index terminal shares the direction.
            for idx, obs_data in still_unmatched:
                obs_dir = obs_data["direction"]
                candidates = [t for t in null_terms if t.direction == obs_dir]
                if len(candidates) == 1:
                    t = candidates[0]
                    t.index = idx
                    if obs_data["direction"]:
                        t.direction = obs_data["direction"]
                    if obs_data["type"]:
                        t.type = obs_data["type"]
                    null_terms.remove(t)
                    updated = True

        if updated:
            self._save_vi_entry(vi_name, vi)

        return vi

    def _update_variant_terminals(
        self,
        variant: VIEntry,
        observed_map: dict[int, dict[str, Any]],
    ) -> None:
        """Update variant with additional terminal observations."""
        existing_indices = {t.index for t in variant.terminals if t.index is not None}
        updated = False

        for idx, obs in observed_map.items():
            if idx not in existing_indices:
                # New terminal observation
                variant.terminals.append(
                    VITerminal(
                        name=obs.get("name", ""),
                        index=idx,
                        direction=obs.get("direction"),
                        type=obs.get("type"),
                    )
                )
                updated = True

        if updated:
            # Update signature
            new_map = {
                t.index: {"name": t.name, "direction": t.direction, "type": t.type}
                for t in variant.terminals
                if t.index is not None
            }
            variant.variant_signature = self._compute_signature(new_map)

    def _ensure_typedef(self, lv_type: LVType) -> None:
        """Create typedef in _types.json if it doesn't exist."""
        if not lv_type.typedef_path:
            return

        if lv_type.typedef_path in self._types:
            return

        # Set typedef metadata on LVType
        if not lv_type.typedef_name:
            lv_type.typedef_name = lv_type.typedef_path
        if not lv_type.description:
            lv_type.description = f"Auto-discovered typedef from {lv_type.typedef_path}"

        self._types[lv_type.typedef_path] = lv_type
        self._save_typedef(lv_type)

    def _save_typedef(self, lv_type: LVType) -> None:
        """Save typedef to _types.json."""
        types_path = self.data_dir / "vilib" / "_types.json"

        if types_path.exists():
            with open(types_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = {}

        # Derive Python name from typedef_name
        python_name = (
            derive_python_name(lv_type.typedef_name)
            if lv_type.typedef_name
            else "Unknown"
        )

        # Serialize LVType
        type_data: dict[str, Any] = {
            "name": python_name,
            "kind": lv_type.kind,
            "underlying_type": lv_type.underlying_type,
        }

        if lv_type.description:
            type_data["description"] = lv_type.description

        if lv_type.values:
            type_data["values"] = {
                name: {
                    "value": ev.value,
                    "description": ev.description,
                }
                for name, ev in lv_type.values.items()
            }

        if lv_type.fields:
            type_data["fields"] = [
                {
                    "name": f.name,
                    "type": (f.type.underlying_type or "Any") if f.type else "Any",
                }
                for f in lv_type.fields
            ]

        data[lv_type.typedef_path] = type_data

        types_path.parent.mkdir(parents=True, exist_ok=True)
        with open(types_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _save_vi_entry(self, vi_name: str, vi: VIEntry) -> None:
        """Save updated VI to category JSON."""
        category_file = self._category_files.get(vi_name)
        if not category_file:
            return

        with open(category_file, encoding="utf-8") as f:
            data = json.load(f)

        for i, entry in enumerate(data.get("entries", [])):
            entry_name = entry.get("name", "")
            if not entry_name.endswith(".vi"):
                entry_vi_name = f"{entry_name}.vi"
            else:
                entry_vi_name = entry_name
            if entry_vi_name == vi_name:
                data["entries"][i] = json.loads(vi.model_dump_json(exclude_none=True))
                break

        with open(category_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def _add_to_pending(
        self,
        vi_name: str,
        caller_vi: str | None,
        conflicts: list[dict[str, Any]],
        observed_map: dict[int, dict[str, Any]],
        existing_map: dict[int, VITerminal],
    ) -> None:
        """Save conflict to _pending_terminals.json."""
        pending_file = self.data_dir / "vilib" / "_pending_terminals.json"

        if pending_file.exists():
            with open(pending_file, encoding="utf-8") as f:
                pending_data = json.load(f)
        else:
            pending_data = {"conflicts": {}}

        if vi_name not in pending_data["conflicts"]:
            pending_data["conflicts"][vi_name] = []

        conflict_entry = {
            "caller_vi": caller_vi,
            "conflicts": conflicts,
            "observed": {k: v for k, v in observed_map.items()},
            "existing": {
                k: {"name": v.name, "direction": v.direction, "type": v.type}
                for k, v in existing_map.items()
            },
        }

        pending_data["conflicts"][vi_name].append(conflict_entry)

        pending_file.parent.mkdir(parents=True, exist_ok=True)
        with open(pending_file, "w", encoding="utf-8") as f:
            json.dump(pending_data, f, indent=2)
