"""Gate: a positionally-named input pair must sit on the slots its names imply.

Why this exists: ``connector_geometry``'s index space runs Right->Left,
Bottom->Top (idx0 = bottom-right), so on a two-input primitive the UPPER
terminal carries the HIGHER index. NI documents the upper input of the dyadic
arithmetic, comparison and logic primitives as ``x`` and the lower as ``y``
(``n`` for the shift/scale pair). So ``x`` must always hold the higher index.

Nineteen entries had it backwards, because the names were filled in from
NI-doc LISTING order (x first) against ASCENDING index -- which is bottom-up,
so ``x`` landed on the lower slot. That is the same bug class as the
Clear-Errors fix in 2f71ff2 (doc listing order mistaken for pane geometry),
in the one variant ``connector_geometry_profile.audit_primitive`` explicitly
cannot catch: both terminals share a type family, so a per-index direction/
type diff sees nothing wrong either way round.

For this family the doc-image half of the auditor is not needed, because the
NAMES themselves encode the geometry. That makes the invariant checkable as
pure data, which is what this test does.

Consequence of getting it wrong: ``python_code`` operands bind to the wrong
wires, so every non-commutative primitive silently computes the inverse --
``Divide`` returns the reciprocal, ``Subtract`` flips sign, the ordered
comparisons invert. Commutative entries (Add, Multiply, And, Or, Xor, Equal?,
Not Equal?) show no symptom, which is why this survived.

SCOPE -- the SHIPPED catalog only, matching test_primitive_name_uniqueness.py.
A project-local ``.lvkit/primitives.json`` override is the designed escape
hatch and must never be scanned here.
"""

from __future__ import annotations

import json
from pathlib import Path

PRIMS = Path(__file__).resolve().parents[1] / "src/lvkit/data/primitives.json"

# Input-name sets whose ordering is fixed by NI's documented pane layout.
# First element is the DOCUMENTED UPPER terminal -> must hold the higher index.
POSITIONAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("x", "y"),
    ("x", "n"),
)


def _entries() -> dict[str, dict]:
    return json.loads(PRIMS.read_text(encoding="utf-8"))["primitives"]


def _positional_two_input_entries():
    """Yield (prim_id, entry, upper_name, lower_name) for each pair entry."""
    for prim_id, entry in _entries().items():
        ins = [t for t in entry.get("terminals", []) if t.get("direction") == "in"]
        if len(ins) != 2:
            continue
        names = {t.get("name") for t in ins}
        for upper, lower in POSITIONAL_PAIRS:
            if names == {upper, lower}:
                yield prim_id, entry, upper, lower
                break


def _input_indices(entry: dict) -> dict[str, int]:
    return {
        t["name"]: t["index"] for t in entry["terminals"] if t.get("direction") == "in"
    }


def test_positional_upper_input_has_higher_index() -> None:
    """``x`` sits above ``y``/``n``, so it must carry the higher slot index."""
    offenders = []
    for prim_id, entry, upper, lower in _positional_two_input_entries():
        idx = _input_indices(entry)
        if idx[upper] < idx[lower]:
            offenders.append(
                f"{prim_id} {entry.get('name')}: "
                f"{upper}=idx{idx[upper]} must sit ABOVE {lower}=idx{idx[lower]}, "
                f"but the index space is bottom-up so it needs the HIGHER index"
            )
    assert not offenders, (
        "positionally-named inputs on the wrong slots:\n  " + "\n  ".join(offenders)
    )


def test_gate_is_actually_covering_something() -> None:
    """Guard against the gate silently matching zero entries."""
    found = list(_positional_two_input_entries())
    assert len(found) >= 15, (
        f"expected the dyadic arithmetic/comparison/logic family, got {len(found)}"
    )
