"""Polymorphic-variant discovery and matching, plus in-memory terminal learning."""

from __future__ import annotations

from typing import Any

from lvkit.models import LVType, LVTypeKind

from .models import VIEntry, VITerminal


class _VILibVariantMixin:
    """Match and discover polymorphic vilib VI variants; learn terminals in memory."""

    # Instance attributes populated by _VILibLoaderMixin.__init__. Declared
    # here (annotation only, no assignment — zero runtime effect) so pyright
    # can type-check attribute access within this mixin.
    _types: dict[str, LVType]
    _variants: dict[str, list[VIEntry]]
    _vis: dict[str, VIEntry]
    _by_name: dict[str, VIEntry]
    _by_poly_selector: dict[tuple[str, str], VIEntry]
    # Set on a fork: it shares its entries with the resolver it was made from.
    _shares_entries: bool
    _owned_entries: set[int]

    # Implemented by _VILibLookupMixin. Declared here (stub body, never
    # executed) only so pyright can type-check the call below — on the
    # composed VILibResolver, MRO always resolves to the real
    # implementation since _VILibLookupMixin precedes this mixin.
    def resolve_by_name(self, vi_name: str) -> VIEntry | None: ...

    def _own(self, entry: VIEntry) -> VIEntry:
        """``entry`` for this resolver to change: itself, or on a fork the copy that
        replaces it in every index of this resolver."""
        if not self._shares_entries or id(entry) in self._owned_entries:
            return entry
        own = entry.model_copy(deep=True)
        for index in (self._vis, self._by_name, self._by_poly_selector):
            for key, held in index.items():
                if held is entry:
                    index[key] = own  # type: ignore[index]
        for variants in self._variants.values():
            for position, held in enumerate(variants):
                if held is entry:
                    variants[position] = own
        self._owned_entries.add(id(own))
        return own

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

        return variant

    def auto_update_terminals(
        self,
        vi_name: str,
        wired_terminals: list[Any],
        caller_vi: str | None = None,
    ) -> VIEntry:
        """Auto-update VI terminals from caller observations.

        The bundled catalog is read-only: what is learned lives in this
        resolver instance's memory and is gone with it. Typedefs seen on a
        wire are registered in memory as needed.
        On conflicts, creates a polymorphic variant instead of failing.
        Observations are always trusted - they come from actual wire connections.
        """
        shipped = self.resolve_by_name(vi_name)
        if not shipped:
            raise ValueError(f"VI not found: {vi_name}")
        vi = self._own(shipped)

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
                "direction": "out" if wired_term.direction == "output" else "in",
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
                matching = self._own(matching)
                self._update_variant_terminals(matching, observed_map)
                return matching

            # Create a new variant from observation
            return self._create_variant(vi_name, observed_map, vi, caller_vi)

        # No conflict - update base entry
        for idx, obs_data in observed_map.items():
            if idx in existing_map:
                term = existing_map[idx]
                if not term.direction and obs_data["direction"]:
                    term.direction = obs_data["direction"]
                if not term.type and obs_data["type"]:
                    term.type = obs_data["type"]
            elif obs_data["name"]:
                # A catalog terminal with no number takes the number of the wired
                # terminal that has exactly its name; a number no name gives stays
                # unknown.
                for term in vi.terminals:
                    if term.name == obs_data["name"] and term.index is None:
                        term.index = idx
                        term.direction = obs_data["direction"]
                        term.type = obs_data["type"]
                        break

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
        """Register a typedef seen on a wire if the catalog lacks it."""
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
