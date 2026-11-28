"""A Select whose result and ``t`` input are Variants takes a number on ``f``
through a coercion dot: the number becomes a Variant of its own type (a function's
polymorphic terminals share one type). Tiny handmade diagram (``native_vi.py``),
generated and executed."""

from __future__ import annotations

from tests.helpers import generated_variants
from tests.native_vi import generate, node, scalar

SELECT = 1516
VARIANT = scalar("Variant")
DBL = scalar("NumFloat64")
BOOLEAN = scalar("Boolean")


def _select(f_type, expected_type):
    terminals = [
        (0, "output", VARIANT),
        (1, "input", f_type),
        (2, "input", BOOLEAN),
        (3, "input", VARIANT),
    ]
    return generate(
        {"f": f_type, "s": BOOLEAN, "t": VARIANT},
        {"r": VARIANT},
        [node("sel", SELECT, terminals)],
        [("fp:f", "sel:1"), ("fp:s", "sel:2"), ("fp:t", "sel:3"), ("sel:0", "fp:r")],
    )


def test_a_number_on_f_is_a_variant_of_its_type() -> None:
    variants = generated_variants()
    run = _select(DBL, variants.LV_DBL)
    held = variants.Variant("kept", variants.LV_String)
    assert run.function(2.5, False, held).r == variants.Variant(2.5, variants.LV_DBL)
    assert run.function(2.5, True, held).r is held
    assert "Variant(f, LV_DBL" in run.source


def test_a_string_on_f_is_a_variant_of_its_type() -> None:
    variants = generated_variants()
    run = _select(scalar("String"), variants.LV_String)
    held = variants.Variant(1, variants.LV_I32)
    assert run.function("x", False, held).r == variants.Variant(
        "x", variants.LV_String
    )
