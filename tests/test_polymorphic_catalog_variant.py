"""A call of a polymorphic VI that resolves to a variant the catalog gives Python
for uses that Python at the call site: no wrapper module, no dispatch, no module
for that variant."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.pipeline import generate_python

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
CALLER = SAMPLES / "JKI-VI-Tester/source/Classes/TestCase/shortDescription.vi"
OPENG = SAMPLES / "OpenG/extracted"


def test_a_catalog_variant_is_inlined_and_no_wrapper_module_is_written(
    tmp_path: Path,
) -> None:
    generate_python(CALLER, tmp_path, search_paths=[OPENG])
    modules = [p.name for p in tmp_path.rglob("*.py")]
    assert not [m for m in modules if m.startswith("trim_whitespace")]
    source = next(tmp_path.rglob("shortdescription.py")).read_text()
    assert "import trim_whitespace" not in source
    line = next(ln.strip() for ln in source.splitlines() if ".strip() if" in ln)
    scope = {"element_0": "  hi \n"}
    exec(line, scope)  # noqa: S102
    assert scope["trim_whitespace__ogtk_string_out"] == "  hi"


class _Graph:
    def __init__(self, called: set[str] | None) -> None:
        self._called = called

    def called_poly_variants(self, wrapper: str) -> set[str] | None:
        return self._called

    def recorded_path(self, key: str) -> str:
        return key


class _Resolver:
    def has_python(self, path: str | None) -> bool:
        return path == "catalog"


@pytest.mark.parametrize(
    ("called", "expected"),
    [
        ({"own"}, ["own"]),
        ({"catalog"}, []),
        ({"own", "catalog"}, ["own"]),
        (None, ["own", "other"]),
    ],
)
def test_only_called_variants_without_catalog_python_are_generated(
    called: set[str] | None, expected: list[str]
) -> None:
    from lvkit.pipeline import _variants_to_generate

    chosen = _variants_to_generate(
        _Graph(called),  # type: ignore[arg-type]
        _Resolver(),  # type: ignore[arg-type]
        "wrapper",
        ["catalog", "own", "other"],
    )
    assert chosen == expected
