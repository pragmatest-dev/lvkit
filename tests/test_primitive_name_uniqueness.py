"""Gate: a primitive NAME must never be silently reused across primResIDs.

Why this exists: resolving a primitive to a name that is already claimed by
another entry is the classic symptom of guessing by shape — the resolve-primitive
skill calls duplicate names a red flag. In practice a batch of resolutions landed
six such duplicates with nothing to catch them, because the only safeguard was
"remember to check", which is the wrong place for an invariant.

LabVIEW *does* legitimately give one function two resIDs (Absolute Value
1054/1057; Split 1D Array 1056/1908 — identical panes, one returning SubArray
views). So duplicates are not banned outright: they must be **explicitly
reviewed**. Anything not listed below fails, which makes an accidental duplicate
impossible to land silently while a proven twin is a one-line, evidence-carrying
addition.

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
    # --- confirmed twins (observed panes identical) ---
    "Logical Shift": {"1081", "1082"},          # same (data, shift)->data pane
    "Split 1D Array": {"1056", "1908"},         # same pane; 1908 yields SubArray
    # --- pre-existing, inherited (predate the audit; not yet pane-compared) ---
    "Absolute Value": {"1054", "1057"},
    "Flatten To String": {"1165", "1189", "1608"},
    "Get Type Information": {"1164", "8203"},
    "Reverse 1D Array": {"1900", "1902"},
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
        reviewed = REVIEWED_DUPLICATES.get(name)
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
    assert not problems, "Unreviewed primitive name duplication:\n" + "\n".join(problems)


def test_reviewed_duplicates_still_exist() -> None:
    """Keep the review list honest — drop entries once a mislabel is fixed."""
    groups = _duplicate_groups()
    stale = [n for n in REVIEWED_DUPLICATES if n not in groups]
    assert not stale, (
        "REVIEWED_DUPLICATES lists names that are no longer duplicated "
        f"(remove them): {stale}"
    )
