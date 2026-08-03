"""Corpus-driven connector-geometry terminal auditor — Phase 0.5 + Phase 1.

Builds on the pure ``Slot`` extraction in ``lvkit.connector_geometry``
(Phase 0) to catch the bug class fixed by hand in commit 2f71ff2: a
``primitives.json`` entry whose ``terminals[].index`` was entered from NI-doc
LISTING order rather than connector-pane GEOMETRY, putting a name/type/
direction on the wrong slot.

Two phases live here:

Phase 0.5 — ``build_primresid_index`` + ``gather_profile`` pool up to
``DEFAULT_FILE_CAP`` real corpus instances of a primResID into a
``PrimProfile``: per connector-pane index, the UNION of observed types
(different callers wire different terminals, so pooling maximizes type
coverage) and the set of observed directions. A slot never wired by any
gathered instance stays ``observed_types=frozenset()`` — never invented.

CORPUS-SAFETY (project rule): this NEVER parses the whole corpus. It greps
the pre-extracted ``*_BDHb.xml`` cache for ``<primResID>`` markers ONCE up
front (``build_primresid_index``), then ``parse_vi`` only the handful of
matched files per primResID, capped, dropping each parse before the next.

Phase 1 — ``audit_primitive`` is a PURE diff (no I/O) between one
``PrimEntry`` (a parsed ``primitives.json`` entry) and its ``PrimProfile``,
emitting ``Finding``s:

  DIRECTION_MISMATCH   JSON declares direction X at index N; every gathered
                       instance agrees on the OPPOSITE direction. (The
                       Clear-Errors class: an input registered where
                       geometry shows an output.)
  TYPE_MISMATCH        JSON's declared type FAMILY at index N contradicts
                       every observed instance's type family (only checked
                       when a type was actually observed, and only when the
                       corpus itself agrees on one family — see
                       DISAGREEMENT below for when it doesn't).
  DISAGREEMENT         Gathered instances disagree with EACH OTHER at the
                       same index — either on direction (which is
                       structurally fixed by the connector pane, so any
                       disagreement is a mis-ID/collision signal, never
                       legitimate) or across type FAMILIES (ordinary
                       same-family polymorphism, e.g. NumInt32 vs
                       NumFloat64, is NOT a disagreement; Boolean vs String
                       at the same index is).
  UNOBSERVED           (not a flag) JSON declares index N; no gathered
                       instance ever wired it — cannot validate.
  MISSING_FROM_ENTRY   (not a flag) a gathered instance wires index N;
                       the JSON entry declares no terminal there.

IMPORTANT LIMITATION — READ BEFORE TRUSTING A CLEAN REPORT: Phase 1 can only
ever compare what corpus geometry shows against what JSON declares AT THE
SAME INDEX. It CANNOT catch a same-type swap — two terminals of the SAME
type family trading names/positions (e.g. Insert Menu Items' ``item_names``
<-> ``item_tags``, both Array; both "look right" to this diff either way
round). That class needs the doc-image half of the auditor (a later phase,
not built here) to compare against the NI doc's connector-pane diagram.
Every ``Finding`` this module emits is real evidence; the ABSENCE of a
finding for a same-type pair proves nothing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ..connector_geometry import extract_slots
from ..parser.node_types import PrimitiveNode
from ..parser.vi import parse_vi

DEFAULT_FILE_CAP = 5


# ---------------------------------------------------------------------------
# primitives.json, typed (project rule: dataclasses, not raw dicts)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PrimTerminalEntry:
    """One ``terminals[]`` entry declared in a primitives.json prim."""

    index: int
    direction: str  # "in" | "out", as declared
    name: str | None
    type: str | None  # declared LabVIEW type name, or a generic ("numeric", ...)


@dataclass(frozen=True)
class PrimEntry:
    """One ``primitives.json`` ``"primitives"`` entry."""

    prim_res_id: int
    name: str
    terminals: tuple[PrimTerminalEntry, ...]

    def by_index(self) -> dict[int, PrimTerminalEntry]:
        return {t.index: t for t in self.terminals}


def load_primitive_entries(primitives_json: Path) -> dict[int, PrimEntry]:
    """Load every ``primitives.json`` ``"primitives"`` entry as a typed
    ``PrimEntry``, keyed by primResID."""
    data = json.loads(primitives_json.read_text(encoding="utf-8"))
    out: dict[int, PrimEntry] = {}
    for pid_str, entry in data.get("primitives", {}).items():
        terminals = tuple(
            PrimTerminalEntry(
                index=t["index"],
                direction=t.get("direction", ""),
                name=t.get("name"),
                type=t.get("type"),
            )
            for t in entry.get("terminals", [])
        )
        pid = int(pid_str)
        out[pid] = PrimEntry(
            prim_res_id=pid, name=entry.get("name", "?"), terminals=terminals
        )
    return out


# ---------------------------------------------------------------------------
# Pooled corpus profile (Phase 0.5 output)
# ---------------------------------------------------------------------------
@dataclass
class SlotProfile:
    """Pooled corpus observations at ONE connector-pane index, across every
    gathered instance of a primResID."""

    index: int
    directions: frozenset[str]
    observed_types: frozenset[str]
    instance_count: int  # gathered node instances where this index appeared
    wired_instance_count: int  # subset with a resolved (non-None) type
    direction_disagreement: bool
    type_disagreement: bool  # observed types span >1 type FAMILY
    sources: tuple[str, ...]  # file names the observations came from


@dataclass
class PrimProfile:
    """Pooled corpus profile for one primResID (Phase 0.5 output)."""

    prim_res_id: int
    slots: dict[int, SlotProfile]
    instances_found: int  # files the grep pass located, BEFORE capping
    files_considered: tuple[str, ...]  # files actually parsed (<= cap)
    files_parsed_ok: int  # of files_considered, how many parsed cleanly
    instance_count: int  # total primitive node instances successfully gathered


def _type_family(type_name: str | None) -> str | None:
    """Collapse a concrete LabVIEW type name to a comparable family, or
    ``None`` for a wildcard/generic placeholder with no comparable signal
    (``polymorphic``/``any``). Mirrors the numeric/array/enum groupings used
    elsewhere in the codebase (``scripts/audit_primitive_consistency.py``)
    so legitimate same-family polymorphism (NumInt32 vs NumFloat64) is never
    mistaken for a cross-instance disagreement or a JSON mismatch."""
    if not type_name:
        return None
    t = type_name.strip().lower()
    if t in ("polymorphic", "any", "unknown"):
        return None
    if t.startswith("num"):
        return "numeric"
    if t in ("array", "subarray"):
        return "array"
    if t.startswith("unit") or t == "enum":
        return "enum"
    return t


@dataclass
class _Accum:
    directions: set[str] = field(default_factory=set)
    types: set[str] = field(default_factory=set)
    instance_count: int = 0
    wired_instance_count: int = 0
    sources: set[str] = field(default_factory=set)


def build_primresid_index(
    cache_root: Path, target_ids: set[int]
) -> dict[int, list[Path]]:
    """Single pass over every cached ``*_BDHb.xml`` under ``cache_root``,
    collecting which files contain each of ``target_ids``' ``<primResID>``
    marker. Grep the whole cache exactly ONCE (never re-grep per primResID —
    that's the whole point of building an index up front).

    Restricted to ``target_ids`` (the primResIDs actually present in
    ``primitives.json``) rather than every primResID in the wild — those are
    the only ones Phase 1 can audit, and the corpus has many more distinct
    primResIDs than that.
    """
    pattern = re.compile(r"<primResID>(\d+)</primResID>")
    index: dict[int, list[Path]] = {pid: [] for pid in target_ids}
    for f in sorted(cache_root.rglob("*_BDHb.xml")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        seen_here: set[int] = set()
        for m in pattern.finditer(text):
            pid = int(m.group(1))
            if pid in target_ids and pid not in seen_here:
                seen_here.add(pid)
                index[pid].append(f)
    return index


def gather_profile(
    prim_res_id: int, files: Sequence[Path], cap: int = DEFAULT_FILE_CAP
) -> PrimProfile:
    """Parse up to ``cap`` of ``files`` (the grep-matched instances of
    ``prim_res_id``) and pool their terminal geometry into a ``PrimProfile``.

    Each parse is dropped (``del pv``) before the next, per the
    never-parse-the-whole-corpus rule — only ever ``cap`` files are opened,
    however many total ``files`` were found.
    """
    capped = list(files[:cap])
    per_index: dict[int, _Accum] = {}
    parsed_ok = 0
    total_instances = 0

    for f in capped:
        try:
            pv = parse_vi(bd_xml=f, layout=True)
        except Exception:  # noqa: BLE001 — unparseable VI: skip, not an audit finding
            continue
        parsed_ok += 1

        bd = pv.block_diagram
        bounds = pv.layout.node_bounds if pv.layout is not None else {}
        by_parent: dict[str, list] = {}
        for ti in bd.terminal_info.values():
            by_parent.setdefault(ti.parent_uid, []).append(ti)

        for node in bd.nodes:
            if not isinstance(node, PrimitiveNode) or node.prim_res_id != prim_res_id:
                continue
            total_instances += 1
            terms = by_parent.get(node.uid, [])
            for slot in extract_slots(terms, bounds):
                acc = per_index.setdefault(slot.index, _Accum())
                acc.directions.add(slot.direction)
                acc.instance_count += 1
                acc.sources.add(f.name)
                if slot.observed_type is not None:
                    acc.types.add(slot.observed_type)
                    acc.wired_instance_count += 1
        del pv

    slots: dict[int, SlotProfile] = {}
    for index, acc in per_index.items():
        families = {fam for t in acc.types if (fam := _type_family(t)) is not None}
        slots[index] = SlotProfile(
            index=index,
            directions=frozenset(acc.directions),
            observed_types=frozenset(acc.types),
            instance_count=acc.instance_count,
            wired_instance_count=acc.wired_instance_count,
            direction_disagreement=len(acc.directions) > 1,
            type_disagreement=len(families) > 1,
            sources=tuple(sorted(acc.sources)),
        )

    return PrimProfile(
        prim_res_id=prim_res_id,
        slots=slots,
        instances_found=len(files),
        files_considered=tuple(str(f) for f in capped),
        files_parsed_ok=parsed_ok,
        instance_count=total_instances,
    )


# ---------------------------------------------------------------------------
# Phase 1 — pure diff
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Finding:
    kind: str  # DIRECTION_MISMATCH | TYPE_MISMATCH | DISAGREEMENT
    #            | UNOBSERVED | MISSING_FROM_ENTRY
    prim_res_id: int
    name: str
    index: int
    json_says: str
    corpus_shows: str
    instance_count: int
    confidence: str  # HIGH | MEDIUM | LOW | "-"
    detail: str = ""


# Findings that are real red flags (as opposed to informational notes).
FLAG_KINDS = frozenset({"DIRECTION_MISMATCH", "TYPE_MISMATCH", "DISAGREEMENT"})

_KIND_RANK = {
    "DIRECTION_MISMATCH": 0,
    "TYPE_MISMATCH": 0,
    "DISAGREEMENT": 1,
    "MISSING_FROM_ENTRY": 2,
    "UNOBSERVED": 3,
}


def _confidence(instance_count: int) -> str:
    """Higher with more agreeing instances (task requirement) — 3+ gathered
    instances all agreeing is HIGH, 2 is MEDIUM, 1 is LOW."""
    if instance_count >= 3:
        return "HIGH"
    if instance_count == 2:
        return "MEDIUM"
    if instance_count == 1:
        return "LOW"
    return "-"


def rank_findings(findings: Sequence[Finding]) -> list[Finding]:
    """Hard direction/type mismatches first, then disagreements, then the
    informational notes — each tier ranked by instance_count descending."""
    return sorted(
        findings,
        key=lambda f: (
            _KIND_RANK.get(f.kind, 9), -f.instance_count, f.prim_res_id, f.index,
        ),
    )


def audit_primitive(entry: PrimEntry, profile: PrimProfile) -> list[Finding]:
    """Diff one ``primitives.json`` entry against its pooled corpus profile.

    Pure — no I/O. See module docstring for the Finding kinds and, in
    particular, the same-type-swap limitation this function CANNOT catch.
    """
    findings: list[Finding] = []
    declared = entry.by_index()

    for index, decl in sorted(declared.items()):
        slot = profile.slots.get(index)
        if slot is None:
            findings.append(
                Finding(
                    kind="UNOBSERVED",
                    prim_res_id=entry.prim_res_id,
                    name=entry.name,
                    index=index,
                    json_says=f"{decl.direction} {decl.type} ({decl.name})",
                    corpus_shows="no gathered instance wired this index",
                    instance_count=0,
                    confidence="-",
                    detail="JSON index absent from every gathered corpus "
                    "instance — cannot validate, not a flag.",
                )
            )
            continue

        if slot.direction_disagreement:
            findings.append(
                Finding(
                    kind="DISAGREEMENT",
                    prim_res_id=entry.prim_res_id,
                    name=entry.name,
                    index=index,
                    json_says=f"{decl.direction} ({decl.name})",
                    corpus_shows=(
                        f"direction varies across instances: "
                        f"{sorted(slot.directions)}"
                    ),
                    instance_count=slot.instance_count,
                    confidence=_confidence(slot.instance_count),
                    detail="Direction is structurally fixed by the connector "
                    "pane — instances disagreeing on it is a mis-ID/"
                    "collision signal, not polymorphism.",
                )
            )
        else:
            observed_dir = next(iter(slot.directions), None)
            if observed_dir is not None and observed_dir != decl.direction:
                findings.append(
                    Finding(
                        kind="DIRECTION_MISMATCH",
                        prim_res_id=entry.prim_res_id,
                        name=entry.name,
                        index=index,
                        json_says=f"{decl.direction} ({decl.name})",
                        corpus_shows=observed_dir,
                        instance_count=slot.instance_count,
                        confidence=_confidence(slot.instance_count),
                        detail="Every gathered instance agrees on the "
                        "OPPOSITE direction from what JSON declares.",
                    )
                )

        if slot.type_disagreement:
            findings.append(
                Finding(
                    kind="DISAGREEMENT",
                    prim_res_id=entry.prim_res_id,
                    name=entry.name,
                    index=index,
                    json_says=f"{decl.type} ({decl.name})",
                    corpus_shows=(
                        f"type family varies across instances: "
                        f"{sorted(slot.observed_types)}"
                    ),
                    instance_count=slot.wired_instance_count,
                    confidence=_confidence(slot.wired_instance_count),
                    detail="Observed types span more than one type family "
                    "at this index — mis-ID/collision signal, not ordinary "
                    "same-family polymorphism.",
                )
            )
        elif slot.observed_types:
            decl_family = _type_family(decl.type)
            observed_family = _type_family(next(iter(slot.observed_types)))
            if (
                decl_family is not None
                and observed_family is not None
                and decl_family != observed_family
            ):
                findings.append(
                    Finding(
                        kind="TYPE_MISMATCH",
                        prim_res_id=entry.prim_res_id,
                        name=entry.name,
                        index=index,
                        json_says=f"{decl.type} ({decl.name})",
                        corpus_shows="/".join(sorted(slot.observed_types)),
                        instance_count=slot.wired_instance_count,
                        confidence=_confidence(slot.wired_instance_count),
                        detail="Declared type family contradicts every "
                        "observed instance's type family.",
                    )
                )

    for index, slot in sorted(profile.slots.items()):
        if index in declared:
            continue
        findings.append(
            Finding(
                kind="MISSING_FROM_ENTRY",
                prim_res_id=entry.prim_res_id,
                name=entry.name,
                index=index,
                json_says="(index not declared)",
                corpus_shows=(
                    f"directions={sorted(slot.directions)} "
                    f"types={sorted(slot.observed_types)}"
                ),
                instance_count=slot.instance_count,
                confidence=_confidence(slot.instance_count),
                detail="A gathered instance wires this index but the JSON "
                "entry declares no terminal there.",
            )
        )

    return findings
