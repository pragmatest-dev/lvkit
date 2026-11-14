"""A call of a polymorphic VI of the project that selects a variant, recorded
relative to its caller (three folders up and down into ``Runtime``), calls that
variant: the variant is a VI of the project, not a vi.lib catalog entry, so its
call is no gap and imports the wrapper's module."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.pipeline import generate_python
from tests.helpers import without_panel_attributes
from tests.test_call_identity import _Call, _record, _static, _Tree

_SELECTOR = (
    '<instanceSelector class="polySelector"><menuInstanceUsed>0x1'
    '</menuInstanceUsed><buf>"Automatic" "Variant"</buf></instanceSelector>'
)
_UP = ["", "", ""]


def _tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Tree:
    """``Tests/Group/main.vi`` calls the wrapper ``Runtime/wrap.vi`` and selects its
    variant ``Runtime/variant.vi``; both are recorded by the path up from ``main``."""
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    built.vi("Runtime/variant.vi", "variant.vi", [])
    wrapper = built.vi(
        "Runtime/wrap.vi",
        "wrap.vi",
        [],
        [_static(["variant.vi"], ["", "variant.vi"])],
    )
    slot = built.extract(wrapper)[2]
    slot.write_text(
        slot.read_text()
        .replace(
            "</Section></LIvi>",
            _record("VIVI", ["variant.vi"], ["", "variant.vi"], 0, 10)
            + "</Section></LIvi>",
        )
        .replace(
            "</VI>",
            '<LVSR><Section><Execution2 AllowPolyTypeAdapt="1"/></Section></LVSR></VI>',
        )
    )
    call = _Call(
        node_class="polyIUse",
        label="wrap.vi",
        records=[
            ("PUPV", ["wrap.vi"], [*_UP, "Runtime", "wrap.vi"], 0),
            ("IUVI", ["variant.vi"], [*_UP, "Runtime", "variant.vi"], 0),
        ],
    )
    caller = built.vi("Tests/Group/main.vi", "main.vi", [], [call])
    bd = built.extract(caller)[0]
    bd.write_text(bd.read_text().replace("<termList>", _SELECTOR + "<termList>"))
    return built


def test_a_call_that_selects_a_project_variant_calls_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path, monkeypatch)
    out = tmp_path / "gen"
    result = generate_python(tree.root / "Tests/Group/main.vi", out)
    assert not result["gaps"]
    source = next(out.rglob("main.py")).read_text()
    assert without_panel_attributes(source).splitlines()[1:] == [
        "from .external.runtime.wrap import variant",
        "",
        "def main():",
        "    variant()",
    ]
