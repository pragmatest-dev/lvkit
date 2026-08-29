"""Gate: a primitive NAME must never be silently reused across primResIDs.

Why this exists: resolving a primitive to a name that is already claimed by
another entry is the classic symptom of guessing by shape — the resolve-primitive
skill calls duplicate names a red flag. In practice a batch of resolutions landed
six such duplicates with nothing to catch them, because the only safeguard was
"remember to check", which is the wrong place for an invariant.

LabVIEW *does* legitimately give one function two resIDs on occasion (identical
panes, e.g. one variant returning a view onto the other's result). So
duplicates are not banned outright: they must be **explicitly reviewed**.
Anything not listed below fails, which makes an accidental duplicate
impossible to land silently while a proven twin is a one-line, evidence-carrying
addition. (REVIEWED_DUPLICATES is currently empty — its last two entries,
Absolute Value 1054/1057 and Split 1D Array 1056/1908, turned out not to be
twins after all: nodes.json's VI-Scripting export (#59) proved 1057 is really
Increment and 1056 is really Quotient & Remainder, each a different function
from 1054/1908, not a shared-pane variant.)

To add an entry here you must have compared the two panes AS OBSERVED in the
corpus (scripts/audit_primitive_consistency.py + the duplicate pane dump) — not
just the names.

SCOPE — deliberately the SHIPPED catalog only (``src/lvkit/data/primitives.json``).
Name uniqueness is an invariant *within* one catalog, never across catalogs. A
project-local ``.lvkit/primitives.json`` entry that reuses a shipped name is the
designed OVERRIDE mechanism (resolvers read ``.lvkit/`` first and fall back to the
shipped data). Crucially, such an overlay usually exists to **correct the shipped
understanding**: everything lvkit ships is a cleanroom *deduction* and can simply
be wrong, so a licensee who can see the real vi.lib may override an entry under
the same name. That override must win and must never fail this gate. Do NOT
"improve" this test by scanning ``.lvkit/`` as well.

(License boundary: their corrections stay in their project — lvkit never ingests
``.lvkit/`` content upstream unless it is independently cleanroom-derived.)
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

PRIMS = Path(__file__).resolve().parents[1] / "src/lvkit/data/primitives.json"

# name -> (pids, status). CONFIRMED-TWIN = panes compared and identical.
# NEEDS-ADJUDICATION = panes materially DIFFER, so one of the pair is mislabeled;
# tracked as debt, must not grow.
REVIEWED_DUPLICATES: dict[str, set[str]] = {
    # --- pre-existing, inherited (predate the audit; not yet pane-compared) ---
    # NOTE (#59, VI-Scripting nodes-export import): 1082, 1056, 1165/1189/1608,
    # and 1164/8203 were renamed away from these shared labels once the export
    # supplied their real style_name ("Rotate", "Quotient & Remainder",
    # "Unflatten From String"/"To Lower Case"/"String To Byte Array", and
    # "Flatten To String"/"Variant To Flattened String" respectively) — so
    # "Logical Shift", "Split 1D Array" (this pair), "Flatten To String" (this
    # pair) and "Get Type Information" are no longer duplicated.
    #
    # "Absolute Value" {1054, 1057} was the one pair the import initially held
    # back (import_nodes_primitives.py's HOLD_IDS), because 1057's nodes.json
    # claim ("Increment") contradicted a verified:true corpus dataflow chain.
    # The maintainer reviewed the conflict and confirmed nodes.json is correct
    # (1057's own output terminal is literally named "x+1", not an
    # absolute-value expression) — 1057 was corrected to "Increment", which
    # dissolves this duplicate too, so it is no longer listed here either.
    # REVIEWED_DUPLICATES is currently empty; kept as the mechanism for a
    # FUTURE pane-compared twin.
}
# RESOLVED, no longer duplicated (kept as a record of what the gate caught):
#   Close File / Open/Create/Replace File — 8011/8010 were never file I/O; they are
#     Close Reference / Open VI Reference. 8010's template had been calling
#     open(str(vi_path)) for a VI-server call.
#   Close Reference — 9102/9104 were never VI references either: every refnum on
#     9101/9102/9104 carries ref_type=NotifierRef, so they are Obtain Notifier /
#     Release Notifier / Send Notification (and 9105 is Wait on Notification, not
#     Call By Reference). Renaming them dissolved all three collisions at once.
#   File Dialog / List Folder — 8056 is Delete, 8070 is Read from Text File.
#   Reverse 1D Array — 1900/1902 were never twins: 1902 is Transpose 2D Array
#     (2D-in/2D-out across 8 element types in the corpus; 1900 is the real 1D
#     reverse). Also Format Value (1540) was Array To Spreadsheet String (every
#     instance takes a 1D array + single %s + delimiter; inverse of 1539).

# --- ground-truth IMPORT collisions (#59), NOT pane-compared ---
# The VI-Scripting nodes export (695 primResID -> real style_name) was bulk
# merge-imported (scripts/import_nodes_primitives.py) as UNVERIFIED baseline
# data (see project_080_primitives_baseline). Every pid below that carries
# `"source": "vi-scripting-nodes-lv2025"` makes no verified-identity claim at
# all — its name is the primitive's REAL name (confirmed straight from
# LabVIEW's own VI-Scripting API, not guessed), so two such entries sharing a
# name is expected LabVIEW reality (the same palette label legitimately
# covers multiple resIDs — polymorphic/legacy/type-family variants), not a
# guessing error the pane-comparison rule exists to catch. Listed here so the
# gate doesn't block a legitimate provisional import; NOT a claim that any
# pair's panes were compared. Re-verify (and fold into REVIEWED_DUPLICATES
# proper once confirmed) via the normal lvkit-resolve-primitive workflow.
IMPORT_UNVERIFIED_DUPLICATES: dict[str, set[str]] = {
    "Add": {"1050", "3915"},
    "Close File": {"1405", "8052"},
    "Copy": {"1416", "8053"},
    "Delete": {"1417", "8056"},
    "Divide": {"1053", "3918"},
    "File Dialog": {"1429", "8058", "8081"},
    "File/Directory Info": {"1413", "8082"},
    "Flatten To String": {"1160", "1164"},
    "Flatten To XML": {"8400", "8402"},
    "Flush File": {"1406", "8059"},
    "List Folder": {"1418", "8083"},
    # NOTE (#59, resolved 2026-08-27): "Merge Errors" {2147, 2401} and
    # "Quotient & Remainder" {1056, 1108} were both listed here as HOLD_IDS
    # corroborating evidence -- 2401/1108 each had a prior identity (Merge
    # Errors / Quotient & Remainder respectively) that collided with the
    # nodes.json-sourced 2147/1056. The maintainer confirmed nodes.json is
    # correct for both (2401 is really Swap Values; 1108 is really Max & Min,
    # each proven by their own terminal EXPRESSION text, not just style_name),
    # so 2401 and 1108 were corrected away from these labels and both
    # collisions are dissolved -- removed from this dict.
    "Move": {"1415", "8068"},
    "Multiply": {"1052", "3917"},
    "Subtract": {"1051", "3916"},
    "Text to UTF-8": {"23066", "23067"},
    "Unflatten From String": {"1161", "1165"},
    "Unknown": {"2372", "11163"},
}


def _duplicate_groups() -> dict[str, set[str]]:
    entries = json.loads(PRIMS.read_text())["primitives"]
    by_name: dict[str, set[str]] = collections.defaultdict(set)
    for pid, entry in entries.items():
        name = entry.get("name")
        if name:
            by_name[name].add(pid)
    return {n: p for n, p in by_name.items() if len(p) > 1}


def test_no_unreviewed_duplicate_primitive_names() -> None:
    """Any duplicate name must be explicitly reviewed above, with its exact pids."""
    groups = _duplicate_groups()
    problems = []
    for name, pids in sorted(groups.items()):
        reviewed = REVIEWED_DUPLICATES.get(name) or IMPORT_UNVERIFIED_DUPLICATES.get(
            name
        )
        if reviewed is None:
            problems.append(
                f"NEW duplicate name {name!r} -> {sorted(pids, key=int)}. "
                "Compare the OBSERVED panes of both resIDs; if they differ, one is "
                "mislabeled — fix it. If identical, add it to REVIEWED_DUPLICATES."
            )
        elif pids != reviewed:
            problems.append(
                f"duplicate group {name!r} changed: expected "
                f"{sorted(reviewed, key=int)}, found {sorted(pids, key=int)}"
            )
    assert not problems, "Unreviewed primitive name duplication:\n" + "\n".join(
        problems
    )  # noqa: E501


def test_reviewed_duplicates_still_exist() -> None:
    """Keep the review list honest — drop entries once a mislabel is fixed."""
    groups = _duplicate_groups()
    stale = [
        n
        for n in {**REVIEWED_DUPLICATES, **IMPORT_UNVERIFIED_DUPLICATES}
        if n not in groups
    ]
    assert not stale, (
        "REVIEWED_DUPLICATES/IMPORT_UNVERIFIED_DUPLICATES lists names that are "
        f"no longer duplicated (remove them): {stale}"
    )
