#!/usr/bin/env python3
"""Merge-import LabVIEW VI-Scripting node export into primitives.json (#59).

Ground truth source: a public LabVIEW VI Scripting export (695 nodes, one per
primResID/"style_id"), NOT bundled with the repo (gitignored under .tmp/).
Pass its path with --nodes-json (defaults to the maintainer's known location).

This is a MERGE, not a regenerate, against src/lvkit/data/primitives.json's
``primitives`` dict. The identity check is CASE-INSENSITIVE (name.casefold()):

  - primResID not present                    -> ADD an unverified entry
                                                 (no python_code)
  - primResID present, name mismatch         -> CORRECT name+terminals from
    (case-insensitive)                          nodes.json, drop the
                                                 now-misattached python_code,
                                                 mark verified:false
  - primResID present, name matches          -> PRESERVE: adopt nodes.json's
    case-insensitively but differs only         casing but KEEP python_code/
    in casing                                    verified/terminals as-is
  - primResID present, name matches exactly  -> PRESERVE untouched (only
                                                 fills a missing terminal name
                                                 on exact index+direction
                                                 match)

A case-sensitive identity check landed a real regression (#59): "Array Of
Strings To Path" (primitives.json) vs nodes.json's "Array of Strings to Path"
differ only in capitalization -- the SAME primitive -- but a `!=` comparison
routed them through CORRECT, silently dropping their working, verified
python_code (1422/1423). Fixed by casefold()-comparing identity while still
adopting nodes.json's casing as the canonical spelling going forward.

A small, explicitly-justified HOLD set is carved out of the "name mismatch"
bucket: primResIDs where the *current* primitives.json entry is backed by
evidence stronger than a bare name match -- a pinned regression test built
from real-VI dataflow, or live codegen that hardcodes the primResID's
identity outside primitives.json entirely. Overwriting those from the nodes
export's bare style_name, without the maintainer weighing the conflicting
evidence, is exactly the kind of "fix" CLAUDE.md's BUGS gate reserves for the
maintainer. See HOLD_IDS below for the itemized evidence.

Deterministic and re-runnable: same nodes.json + same primitives.json in ->
same primitives.json out.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMITIVES_PATH = REPO_ROOT / "src" / "lvkit" / "data" / "primitives.json"
BUILTIN_TYPES_PATH = REPO_ROOT / "src" / "lvkit" / "data" / "builtin_types.json"
DEFAULT_NODES_JSON = Path("/home/ryanf/repos/lvkit/.tmp/nodes.json")

SOURCE_TAG = "vi-scripting-nodes-lv2025"

# --------------------------------------------------------------------------
# Structures: no fixed data connector pane -- excluded from primitive import.
# --------------------------------------------------------------------------

# The 12 structures named explicitly in the import brief (issue #59).
EXCLUDED_STRUCTURES_BRIEF: dict[int, str] = {
    2000: "Stacked Sequence",
    2001: "Case Structure",
    2002: "For Loop",
    2003: "While Loop",
    2052: "Event Structure",
    2054: "Diagram Disable Structure",
    2064: "Conditional Disable Structure",
    2352: "While Loop",
    2360: "In Place Element Structure",
    2373: "Type Specialization Structure",
    5570: "Timed Loop",
    5573: "Timed Sequence",
}

# Re-scan finding (not in the brief's list): these 3 nodes.json entries show
# the SAME "no fixed data connector pane" signature as the 12 above --
# terminals: [] (the VI-Scripting export reports zero terminals at all, a
# stronger form of "no fixed pane" than several of the 12 explicitly-listed
# structures which do carry a couple of framework terminals e.g. loop index).
# 2004 "Formula Node" (self-named). 2370/2371 additionally carry style_name
# "Unknown" with style_description "Target Structure" / "Race Structure" --
# i.e. the export itself calls them structures. None of the three are
# currently in primitives.json, so excluding them changes nothing destructive
# -- it only withholds three new adds. Flagged for maintainer confirmation;
# see the script's printed summary.
EXCLUDED_STRUCTURES_RESCAN: dict[int, str] = {
    2004: "Formula Node",
    2370: "Unknown (style_description: Target Structure)",
    2371: "Unknown (style_description: Race Structure)",
}

EXCLUDED_STRUCTURES: dict[int, str] = {
    **EXCLUDED_STRUCTURES_BRIEF,
    **EXCLUDED_STRUCTURES_RESCAN,
}

# --------------------------------------------------------------------------
# HOLD: name-mismatch primResIDs carved OUT of the "CORRECT" bucket.
#
# Each entry needs SPECIFIC, checked-this-session evidence contradicting the
# nodes.json style_name -- not just "it was already named something else" --
# to be held here instead of corrected. Held entries are left completely
# untouched; reported to the maintainer for adjudication.
#
# RESOLVED (2026-08-27): 1057, 1058, 1108, 1116, 2401 were all held here after
# the first import pass, each backed by a prior verified:true entry, a pinned
# regression test, or live codegen hardcoding a different identity. The
# maintainer reviewed the conflicts and confirmed nodes.json is correct for
# all five, proven by LabVIEW's own per-terminal EXPRESSION text (not just the
# style_name): 1057's output terminal is literally named "x+1" (Increment, not
# Absolute Value); 1058's is "x-1" (Decrement); 1108's two outputs are
# "min(x,y)" and "max(x,y)" (Max & Min, not Quotient & Remainder or a
# comparison); 1116's output is "x != 0?" over a scalar double_float input
# (Not Equal To 0?, not the ruled-out numeric-comparison family -- the old
# "corpus feeders are boolean/cluster" finding was itself wrong); 2401's pane
# (x'/y' outputs, x/y/condition inputs) matches Swap Values, and the real
# Merge Errors is 2147 (error_cluster in x2 / out x1, the expandable pane the
# old 2401 entry actually described). The prior "verified" identifications and
# the condition_builder.py / error_handler.py hardcoding were themselves
# wrong. See the corresponding fixes in codegen/condition_builder.py,
# codegen/error_handler.py, codegen/nodes/primitive.py, codegen/class_builder.py,
# codegen/builder.py, and parser/node_types.py. HOLD_IDS is now empty; kept as
# the mechanism for any FUTURE conflict that needs the same treatment.
# --------------------------------------------------------------------------

HOLD_IDS: dict[int, str] = {}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def build_alias_map(builtin_types: dict[str, Any]) -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for canonical, info in builtin_types["types"].items():
        for alias in info["aliases"]:
            alias_map[alias] = canonical
    return alias_map


def canonical_type(
    data_type: dict[str, Any] | None, alias_map: dict[str, str], sid: str
) -> tuple[str, str | None]:
    """Return (canonical_type, note). note is set only for the null case.

    Raises a diagnostic KeyError (naming the offending style_id) instead of a
    bare one, so a future re-run against an updated/expanded export fails
    with something actionable rather than an opaque traceback.
    """
    if data_type is None:
        return "Polymorphic", "data_type was null in the VI-Scripting export"
    if "type" not in data_type:
        raise KeyError(
            f"style_id {sid}: data_type {data_type!r} has no 'type' key"
        )
    raw = data_type["type"]
    if raw not in alias_map:
        raise KeyError(
            f"style_id {sid}: data_type {raw!r} has no alias in "
            "builtin_types.json -- add one (or an alias onto an existing "
            "canonical type) before re-running the import"
        )
    return alias_map[raw], None


def convert_terminal(
    term: dict[str, Any], alias_map: dict[str, str], sid: str
) -> dict[str, Any]:
    for field in ("index", "is_source", "name"):
        if field not in term:
            raise KeyError(
                f"style_id {sid}: terminal {term!r} is missing required "
                f"field {field!r}"
            )
    data_type = term.get("data_type")
    ctype, note = canonical_type(data_type, alias_map, sid)
    out: dict[str, Any] = {
        "index": term["index"],
        "direction": "out" if term["is_source"] else "in",
        "name": term["name"],
        "type": ctype,
    }
    if term.get("is_hidden"):
        out["hidden"] = True
    if data_type is not None and data_type.get("type") == "refnum":
        for key in ("refnum_type", "class_id", "class_name"):
            if key in data_type:
                out[key] = data_type[key]
    if note:
        out["note"] = note
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--nodes-json",
        type=Path,
        default=DEFAULT_NODES_JSON,
        help="Path to the VI-Scripting nodes export (gitignored, not in-tree)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the summary without writing primitives.json",
    )
    args = parser.parse_args()

    nodes = load_json(args.nodes_json)
    builtin_types = load_json(BUILTIN_TYPES_PATH)
    alias_map = build_alias_map(builtin_types)

    data = load_json(PRIMITIVES_PATH)
    primitives: dict[str, Any] = data["primitives"]

    nodes_by_id: dict[int, dict[str, Any]] = {n["style_id"]: n for n in nodes}
    assert len(nodes_by_id) == len(nodes), "duplicate style_id in nodes.json"

    excluded_found: list[int] = []
    other_structure_candidates: list[tuple[int, str]] = []
    added: list[str] = []
    corrected: list[str] = []
    case_renamed: list[str] = []
    preserved_filled: list[str] = []
    preserved_untouched: list[str] = []
    held: list[str] = []

    for style_id, node in sorted(nodes_by_id.items()):
        sid = str(style_id)
        style_name = node["style_name"]

        if style_id in EXCLUDED_STRUCTURES:
            excluded_found.append(style_id)
            continue

        # Re-scan signal, independent of the fixed exclude list: zero
        # terminals is the same "no fixed data connector pane" signature the
        # excluded structures show. Anything NOT already in EXCLUDED_STRUCTURES
        # that matches it is reported, never silently included or excluded.
        if len(node["terminals"]) == 0 and style_id not in EXCLUDED_STRUCTURES:
            other_structure_candidates.append((style_id, style_name))

        if style_id in HOLD_IDS:
            held.append(f"{sid} ({style_name!r} per nodes.json)")
            continue

        new_terminals = [
            convert_terminal(t, alias_map, sid) for t in node["terminals"]
        ]

        if sid not in primitives:
            primitives[sid] = {
                "name": style_name,
                "terminals": new_terminals,
                "verified": False,
                "source": SOURCE_TAG,
            }
            added.append(sid)
            continue

        entry = primitives[sid]
        entry_name = entry.get("name", "")
        # Identity check is case-INSENSITIVE: "Array Of Strings To Path" vs
        # nodes.json's "Array of Strings to Path" is the SAME primitive, a
        # capitalization-only difference -- not a name mismatch. Treating it
        # as one (case-sensitive ==) hit the CORRECT branch and dropped
        # working, verified python_code for no reason (#59 regression:
        # 1422/1423). Only a genuine case-insensitive mismatch is a real
        # identity conflict.
        if entry_name.casefold() != style_name.casefold():
            primitives[sid] = {
                "name": style_name,
                "terminals": new_terminals,
                "verified": False,
                "source": SOURCE_TAG,
                "note": (
                    "corrected from VI-Scripting nodes export (#59); codegen "
                    "dropped pending re-derivation"
                ),
            }
            corrected.append(f"{sid} ({entry_name!r} -> {style_name!r})")
            continue

        # Same primitive (case-insensitively). A case-only difference adopts
        # nodes.json's casing but KEEPS python_code/verified/terminals/etc --
        # this is still the PRESERVE path, not a correction.
        if entry_name != style_name:
            entry["name"] = style_name
            case_renamed.append(f"{sid} ({entry_name!r} -> {style_name!r})")

        # PRESERVE: only fill a currently-absent terminal name when
        # index+direction match exactly; never touch type/python_code/
        # verified/etc.
        nodes_terms_by_key = {
            (t["index"], "out" if t["is_source"] else "in"): t["name"]
            for t in node["terminals"]
        }
        filled_any = False
        for term in entry.get("terminals", []):
            key = (term.get("index"), term.get("direction"))
            if not term.get("name") and key in nodes_terms_by_key:
                term["name"] = nodes_terms_by_key[key]
                filled_any = True
        if filled_any:
            preserved_filled.append(sid)
        else:
            preserved_untouched.append(sid)

    # Order the whole primitives dict by int(primResID) ascending.
    data["primitives"] = {
        k: primitives[k] for k in sorted(primitives, key=int)
    }

    verified_count = sum(
        1 for v in data["primitives"].values() if v.get("verified") is True
    )
    total = len(data["primitives"])
    data["metadata"] = {
        "description": "Primitive ID to Python mapping (runtime version)",
        "source": (
            "OpenG + JKI VI Tester + LabVIEW VI Scripting nodes export "
            "(issue #59)"
        ),
        "total_primitives": total,
        # NOT total: "identified" means confirmed identity + codegen, i.e.
        # verified_count. Counting all 680 here (as an earlier version of
        # this script did) overstated coverage -- it silently folded in the
        # ~590 unverified VI-Scripting baseline entries (real name+terminals
        # from nodes.json, but no python_code and no independent
        # confirmation) as if they were as trustworthy as a verified entry.
        "identified": verified_count,
        "verified_count": verified_count,
        "unverified_count": total - verified_count,
        "note": (
            "verified_count = confirmed identity + codegen (doc/corpus/"
            "geometry-checked, has python_code); unverified_count = "
            "VI-Scripting export baseline only (real name+terminals from "
            "nodes.json, #59, but no codegen and no independent "
            "confirmation yet). See primitives-codegen-full.json for "
            "count/vis metadata."
        ),
    }

    print(f"Structures excluded: {len(excluded_found)} {sorted(excluded_found)}")
    if other_structure_candidates:
        print(
            "Additional structure-like candidates found by re-scan "
            f"(0 terminals, not in the brief's exclude list) -- already "
            f"folded into EXCLUDED_STRUCTURES_RESCAN, confirm with maintainer: "
            f"{other_structure_candidates}"
        )
    print(f"Added: {len(added)}")
    print(f"Corrected: {len(corrected)}")
    for c in corrected:
        print(f"  {c}")
    print(
        f"Preserved, case-only rename (python_code/verified kept): "
        f"{len(case_renamed)}"
    )
    for c in case_renamed:
        print(f"  {c}")
    print(f"Preserved (terminal name filled): {len(preserved_filled)}")
    print(f"Preserved (untouched): {len(preserved_untouched)}")
    print(f"Held (excluded from CORRECT pending maintainer review): {len(held)}")
    for h in held:
        print(f"  {h}")
    print(f"Total primitives in output: {total} (verified={verified_count})")

    if not args.dry_run:
        PRIMITIVES_PATH.write_text(json.dumps(data, indent=2) + "\n")
        print(f"Wrote {PRIMITIVES_PATH}")


if __name__ == "__main__":
    main()
