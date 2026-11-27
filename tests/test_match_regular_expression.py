"""Match Regular Expression (an XNode, identified by its recorded library path):
the generated Python runs and gives what NI's page says for each output."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE
from lvkit.codegen.nodes.match_regular_expression import (
    MATCH_REGULAR_EXPRESSION_PATH,
)
from lvkit.codegen.panels_module import PANELS_MODULE_NAME, PANELS_MODULE_SOURCE
from lvkit.codegen.variants_module import VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE
from lvkit.graph.models import Constant, PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.helpers import build_graph, error_cluster_type

_STR = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")
_I32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
_BOOL = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Boolean")
_ERROR = error_cluster_type()

# (index, direction, name, type) of the XNode's pane.
_PANE = [
    (0, "input", "input string", _STR),
    (1, "input", "regular expression", _STR),
    (2, "output", "before match", _STR),
    (3, "output", "whole match", _STR),
    (4, "output", "after match", _STR),
    (5, "output", "offset past match", _I32),
    (6, "input", "offset", _I32),
    (7, "input", "error in", _ERROR),
    (8, "output", "error out", _ERROR),
    (9, "input", "ignore case", _BOOL),
    (10, "input", "multiline", _BOOL),
    (11, "output", "submatch 1", _STR),
    (12, "output", "submatch 2", _STR),
]
_OUTPUTS = {
    "before": 2,
    "whole": 3,
    "after": 4,
    "offset_past": 5,
    "error_out": 8,
    "sub1": 11,
    "sub2": 12,
}
_CONTROLS = {
    "text": (0, _STR),
    "offset": (6, _I32),
    "ignore": (9, _BOOL),
    "multi": (10, _BOOL),
    "pattern": (1, _STR),
}


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant_pattern: str | None = None,
    **arguments,
):
    """Generate a VI whose controls feed the XNode (the regular expression is a
    constant when ``constant_pattern`` is given) and run it."""
    node = PrimitiveNode(
        id="x",
        vi_path="caller.vi",
        name="Match Regular Expression",
        node_type="xNode",
        xnode_library_path=MATCH_REGULAR_EXPRESSION_PATH,
        terminals=[
            FPTerminal(
                id=f"x:{index}",
                index=index,
                direction=direction,
                name=name,
                lv_type=lv_type,
            )  # type: ignore[arg-type]
            for index, direction, name, lv_type in _PANE
        ],
    )
    controls = {
        k: v
        for k, v in _CONTROLS.items()
        if constant_pattern is None or k not in ("pattern", "ignore", "multi")
    }
    context = VIContext(
        name="caller.vi",
        inputs=[
            FPTerminal(
                id=f"fp:{name}",
                index=position,
                direction="input",
                name=name,
                is_public=True,
                is_indicator=False,
                lv_type=lv_type,
            )
            for position, (name, (_i, lv_type)) in enumerate(controls.items())
        ],
        constants=(
            []
            if constant_pattern is None
            else [Constant(id="c", value=constant_pattern, lv_type=_STR)]
        ),
        outputs=[
            FPTerminal(
                id=f"fp:{name}",
                index=position,
                direction="output",
                name=name,
                is_public=True,
                is_indicator=True,
                lv_type=next(t for i, _d, _n, t in _PANE if i == index),
            )
            for position, (name, index) in enumerate(_OUTPUTS.items())
        ],
        data_flow=[
            *(
                Wire.from_terminals(from_terminal_id=f"fp:{n}", to_terminal_id=f"x:{i}")
                for n, (i, _t) in controls.items()
            ),
            *(
                [Wire.from_terminals(from_terminal_id="c", to_terminal_id="x:1")]
                if constant_pattern is not None
                else []
            ),
            *(
                Wire.from_terminals(from_terminal_id=f"x:{i}", to_terminal_id=f"fp:{n}")
                for n, i in _OUTPUTS.items()
            ),
        ],
    )
    graph = build_graph(context, "caller.vi", [node])
    source = build_module(context, "caller.vi", graph=graph)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "caller.py").write_text(source, encoding="utf-8")
    for module, text in (
        (ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE),
        (VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE),
        (LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE),
        (PANELS_MODULE_NAME, PANELS_MODULE_SOURCE),
    ):
        (package / f"{module}.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        caller = importlib.import_module("pkg.caller")
        defaults = {"text": "", "offset": 0}
        if constant_pattern is None:
            defaults.update(ignore=False, multi=False)
        if constant_pattern is None:
            defaults["pattern"] = ""
        return source, caller.caller(**{**defaults, **arguments})
    finally:
        for name in ("pkg.caller", "pkg.errors", "pkg.panels", "pkg"):
            sys.modules.pop(name, None)


def _fresh(tmp_path: Path) -> Path:
    folder = tmp_path / "second"
    folder.mkdir()
    return folder


def test_a_match_splits_the_string_and_gives_the_submatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NI: (el.)..(L..) on "Hello LabVIEW!" gives submatches ell and Lab."""
    _, result = _run(
        tmp_path, monkeypatch, text="Hello LabVIEW!", pattern="(el.)..(L..)"
    )
    assert (result.before, result.whole, result.after) == ("H", "ello Lab", "VIEW!")
    assert (result.sub1, result.sub2) == ("ell", "Lab")
    assert result.offset_past == 9
    assert result.error_out is None


def test_no_match_gives_the_whole_string_before_and_minus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(tmp_path, monkeypatch, text="Hello", pattern="(x)(y)")
    assert (result.before, result.whole, result.after) == ("Hello", "", "")
    assert (result.sub1, result.sub2, result.offset_past) == ("", "", -1)
    assert result.error_out is None


def test_the_search_starts_at_the_offset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(tmp_path, monkeypatch, text="a1 b2 c3", pattern="[0-9]", offset=3)
    assert (result.before, result.whole, result.after) == ("a1 b", "2", " c3")
    assert result.offset_past == 5


def test_ignore_case_and_multiline_follow_their_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, plain = _run(tmp_path, monkeypatch, text="x\nVOLTS", pattern="^volts")
    assert plain.whole == ""
    for name in ("pkg.caller", "pkg.errors", "pkg.panels", "pkg"):
        sys.modules.pop(name, None)
    _, both = _run(
        _fresh(tmp_path),
        monkeypatch,
        text="x\nVOLTS",
        pattern="^volts",
        ignore=True,
        multi=True,
    )
    assert both.whole == "VOLTS"


def test_a_constant_pattern_is_compiled_once_at_module_level(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, result = _run(
        tmp_path, monkeypatch, constant_pattern="[0-9]+", text="ab 42 cd"
    )

    assert "_regex_" in source and source.count("re.compile(") == 1
    assert (result.before, result.whole, result.after) == ("ab ", "42", " cd")


def test_a_pattern_re_cannot_read_is_the_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(tmp_path, monkeypatch, text="abc", pattern="(unclosed")
    assert result.error_out is not None
    assert "unterminated subpattern" in result.error_out.message
    assert result.whole == ""


def test_a_null_character_is_error_4702(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _run(tmp_path, monkeypatch, text="a\x00b", pattern="a")
    assert result.error_out is not None and result.error_out.code == -4702
