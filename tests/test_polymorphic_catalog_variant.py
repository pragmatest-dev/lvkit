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
    # shortDescription is a method of the TestCase class module.
    source = next(tmp_path.rglob("testcase.py")).read_text()
    assert "import trim_whitespace" not in source
    line = next(ln.strip() for ln in source.splitlines() if ".strip() if" in ln)
    scope = {"element_0": "  hi \n"}
    exec(line, scope)  # noqa: S102
    assert scope["trim_whitespace__ogtk_string_out"] == "  hi"
