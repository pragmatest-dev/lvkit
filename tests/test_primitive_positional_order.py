"""Gate: a positionally-named input pair must sit on the slots its names imply.

Why this exists: ``connector_geometry``'s index space runs Right->Left,
Bottom->Top (idx0 = bottom-right), so on a two-input primitive with `x`/`y`
(or `x`/`n`) inputs, one of them sits physically ABOVE the other and so must
carry the HIGHER index -- WHICH terminal that is depends on the primitive
FAMILY, and is measured from nodes.json's terminal geometry (#59, VI-Scripting
nodes export), not assumed from NI-doc reading order:

- The dyadic ARITHMETIC/comparison/logic family (Add, Subtract, Divide,
  Equal?, And, Or, ...): `x` sits ABOVE `y`/`n` -> `x` must hold the HIGHER
  index. NI documents `x` as the upper input for this family.
- The SHIFT/POWER family (X_LOWER_PRIMS below): `x` sits BELOW `y`/`n` --
  confirmed by nodes.json's own output-terminal EXPRESSION text, which spells
  the physical layout out directly: 1081/1082 output `x << y` (x first,
  low/right slot in a left-shift idiom), 1074 outputs `x*2^n`, 1213/1224/1228/
  23062 output `x^y`/`logx(y)`/`atan2(y,x)`/`y-th root(x)` -- in every one of
  these, `y`/`n` is the exponent/shift-count/angle operand that NI's pane
  puts on TOP, with `x` (the base/value operand) on the bottom. So for this
  family `x` must hold the LOWER index -- the OPPOSITE rule from the
  arithmetic family, not a violation of the same rule.

Nineteen entries in the arithmetic/comparison/logic family had it backwards,
because the names were filled in from NI-doc LISTING order (x first) against
ASCENDING index -- which is bottom-up, so `x` landed on the lower slot. That
is the same bug class as the Clear-Errors fix in 2f71ff2 (doc listing order
mistaken for pane geometry), in the one variant
``connector_geometry_profile.audit_primitive`` explicitly cannot catch: both
terminals share a type family, so a per-index direction/type diff sees
nothing wrong either way round.

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

UNVERIFIED-IMPORT CAVEAT (#59): entries carrying ``"source":
"vi-scripting-nodes-lv2025"`` (the bulk VI-Scripting nodes-export merge,
scripts/import_nodes_primitives.py) that are still ``verified: false`` get
their terminal ``index`` verbatim from the export's own per-terminal
``index`` field -- which the SHIFT/POWER-family discovery above shows is NOT
uniformly the arithmetic-family convention; a given entry needs its own
geometry confirmation (lvkit-resolve-primitive Step 6, or the family
membership check above) before this gate can judge it. No python_code ships
for an unconfirmed entry, so there is no operand-binding consequence today.
Excluded below; once a vi-scripting-sourced entry is confirmed and verified
(as 1074/1081/1213 -- and 1057/1058, single-input so exempt from this gate
entirely -- now are, #59), it drops out of the exemption and this gate covers
it like everything else.
"""

from __future__ import annotations

import json
from pathlib import Path

PRIMS = Path(__file__).resolve().parents[1] / "src/lvkit/data/primitives.json"

# Input-name sets whose ordering is fixed by NI's documented pane layout.
# First element is the DOCUMENTED UPPER terminal for the ARITHMETIC family
# (X_LOWER_PRIMS below is the opposite convention -- see module docstring).
POSITIONAL_PAIRS: tuple[tuple[str, str], ...] = (
    ("x", "y"),
    ("x", "n"),
)

# The shift/power family: `x` sits on the LOWER slot (geometry-confirmed from
# nodes.json's own output-terminal expression text -- see module docstring),
# the opposite of the arithmetic/comparison/logic family's convention. Every
# member is non-commutative in x/y, so getting this backwards is exactly the
# same silent-inversion risk the arithmetic family's rule guards against.
X_LOWER_PRIMS: set[int] = {1074, 1081, 1082, 1213, 1224, 1228, 23062}


def _entries() -> dict[str, dict]:
    return json.loads(PRIMS.read_text(encoding="utf-8"))["primitives"]


def _positional_two_input_entries():
    """Yield (prim_id, entry, upper_name, lower_name) for each pair entry.

    ``upper_name``/``lower_name`` are the LOGICAL roles (must-hold-higher-index
    / must-hold-lower-index) after accounting for family: for the default
    (arithmetic/comparison/logic) family ``x`` is upper; for X_LOWER_PRIMS
    (shift/power family) the roles are swapped -- ``x`` is lower.
    """
    for prim_id, entry in _entries().items():
        if (
            entry.get("verified") is False
            and entry.get("source") == "vi-scripting-nodes-lv2025"
        ):
            continue  # see the UNVERIFIED-IMPORT CAVEAT in the module docstring
        ins = [t for t in entry.get("terminals", []) if t.get("direction") == "in"]
        if len(ins) != 2:
            continue
        names = {t.get("name") for t in ins}
        for x_name, other_name in POSITIONAL_PAIRS:
            if names == {x_name, other_name}:
                if int(prim_id) in X_LOWER_PRIMS:
                    yield prim_id, entry, other_name, x_name
                else:
                    yield prim_id, entry, x_name, other_name
                break


def _input_indices(entry: dict) -> dict[str, int]:
    return {
        t["name"]: t["index"] for t in entry["terminals"] if t.get("direction") == "in"
    }


def test_positional_upper_input_has_higher_index() -> None:
    """The logical upper-slot input must carry the higher index.

    For the default (arithmetic/comparison/logic) family that's ``x``; for
    X_LOWER_PRIMS (shift/power family) the roles are swapped and it's
    ``y``/``n`` -- ``_positional_two_input_entries`` already resolves which
    name plays which role, so this check itself is family-agnostic.
    """
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
        f"expected the dyadic arithmetic/comparison/logic + shift/power "
        f"families, got {len(found)}"
    )
