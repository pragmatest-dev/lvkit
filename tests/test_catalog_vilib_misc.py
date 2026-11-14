"""vi.lib statistics, array, timing, random, GUID, set, assert and browser VIs: each
caller diagram is generated from the catalog entry the recorded path names (the
connector pane numbers are the ones the corpus callers record), and its Python is
run. The behaviour is what NI's public page for the VI documents."""

from __future__ import annotations

import math
import re
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.vilib_resolver import VILibResolver
from tests.vilib_caller import BOOL, DBL, I32, PATH, STRING, run_catalog_call

ARRAY = LVType(kind=LVTypeKind.ARRAY)
SET = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Set", element_type=STRING)
ERROR_CLUSTER = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)


def _call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[str],
    pane: list[Any],
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    arguments: dict[str, Any],
) -> Any:
    _, result = run_catalog_call(
        tmp_path, monkeypatch, recorded, pane, inputs, outputs, arguments
    )
    return result


# --- Std Deviation and Variance ----------------------------------------------

STD_PANE = [
    (0, "input", ARRAY),
    (1, "input", I32),
    (2, "output", DBL),
    (3, "output", DBL),
    (4, "output", DBL),
]


def _std(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    values: list[float],
    weighting: int | None = None,
) -> Any:
    inputs: dict[str, Any] = {"x": (0, ARRAY)}
    arguments: dict[str, Any] = {"x": values}
    if weighting is not None:
        inputs["weighting"] = (1, I32)
        arguments["weighting"] = weighting
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Analysis", "baseanly.llb", "Std Deviation and Variance.vi"],
        STD_PANE,
        inputs,
        {"mean": (2, DBL), "sd": (3, DBL), "variance": (4, DBL)},
        arguments,
    )


def test_std_deviation_is_the_sample_one_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _std(tmp_path, monkeypatch, [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    assert result.mean == 5.0
    assert result.variance == pytest.approx(32 / 7)
    assert result.sd == pytest.approx(math.sqrt(32 / 7))


def test_std_deviation_with_population_weighting_divides_by_n(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _std(
        tmp_path, monkeypatch, [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0], weighting=1
    )
    assert result.variance == pytest.approx(4.0)
    assert result.sd == pytest.approx(2.0)


def test_std_deviation_with_sample_weighting_divides_by_n_minus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _std(tmp_path, monkeypatch, [1.0, 3.0], weighting=0)
    assert result.variance == pytest.approx(2.0)


# --- Search Unsorted 1D Array -------------------------------------------------

SEARCH_PANE = [
    (2, "output", I32),
    (9, "input", I32),
    (10, "input", STRING),
    (11, "input", ARRAY),
]
SEARCH_RECORDED = ["<vilib>", "Array", "Search Unsorted 1D Array.vim"]


def _search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    array: list[str],
    element: str,
    start: int | None = None,
) -> Any:
    inputs: dict[str, Any] = {"array": (11, ARRAY), "element": (10, STRING)}
    arguments: dict[str, Any] = {"array": array, "element": element}
    if start is not None:
        inputs["start"] = (9, I32)
        arguments["start"] = start
    return _call(
        tmp_path,
        monkeypatch,
        SEARCH_RECORDED,
        SEARCH_PANE,
        inputs,
        {"index": (2, I32)},
        arguments,
    ).index


def test_search_unsorted_finds_the_first_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _search(tmp_path, monkeypatch, ["a", "b", "c", "b"], "b") == 1


def test_search_unsorted_starts_at_the_start_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _search(tmp_path, monkeypatch, ["a", "b", "c", "b"], "b", 2) == 3


def test_search_unsorted_gives_minus_one_when_it_is_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _search(tmp_path, monkeypatch, ["a", "b"], "z") == -1
    assert _search(tmp_path, monkeypatch, ["a", "b"], "b", 2) == -1
    assert _search(tmp_path, monkeypatch, [], "a") == -1


# --- Stall Data Flow ----------------------------------------------------------


def test_stall_data_flow_waits_and_passes_the_value_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started = time.time()
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Utility", "Stall Data Flow.vim"],
        [
            (0, "input", STRING),
            (1, "input", I32),
            (2, "output", I32),
            (3, "output", STRING),
        ],
        {"value": (0, STRING), "wait": (1, I32)},
        {"stamp": (2, I32), "out": (3, STRING)},
        {"value": "kept", "wait": 120},
    )
    assert result.out == "kept"
    assert time.time() - started >= 0.12
    assert abs(result.stamp - int(time.time() * 1000)) < 1000


# --- Random Number (Range) ----------------------------------------------------


def _random(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    variant: str,
    kind: LVType,
    lower: float | None,
    upper: float | None,
) -> Any:
    inputs: dict[str, Any] = {}
    arguments: dict[str, Any] = {}
    for name, index, value in (("lower", 10, lower), ("upper", 11, upper)):
        if value is not None:
            inputs[name] = (index, kind)
            arguments[name] = value
    return _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "numeric", f"Random Number (Range) {variant}.vi"],
        [(3, "output", kind), (10, "input", kind), (11, "input", kind)],
        inputs,
        {"number": (3, kind)},
        arguments,
    ).number


def test_random_dbl_is_in_the_range_and_defaults_to_zero_to_a_hundred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for _ in range(20):
        assert 5.0 <= _random(tmp_path / "a", monkeypatch, "DBL", DBL, 5.0, 6.0) < 6.0
    assert 0.0 <= _random(tmp_path / "b", monkeypatch, "DBL", DBL, None, None) < 100.0


def test_random_dbl_with_an_empty_range_is_the_lower_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _random(tmp_path / "a", monkeypatch, "DBL", DBL, 3.0, 3.0) == 3.0
    assert _random(tmp_path / "b", monkeypatch, "DBL", DBL, 3.0, 1.0) == 3.0


def test_random_i64_includes_both_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = {
        _random(tmp_path / str(i), monkeypatch, "I64", I32, 1, 2) for i in range(40)
    }
    assert seen == {1, 2}


def test_random_i64_defaults_to_minus_a_hundred_to_a_hundred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert -100 <= _random(tmp_path, monkeypatch, "I64", I32, None, None) <= 100


def test_random_i64_with_an_empty_range_is_the_lower_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _random(tmp_path, monkeypatch, "I64", I32, 7, 7) == 7


# --- Create NI GUID -----------------------------------------------------------


def test_create_ni_guid_is_an_uppercase_guid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "string", "Create NI GUID.vi"],
        [(0, "output", STRING)],
        {},
        {"guid": (0, STRING)},
        {},
    )
    assert re.fullmatch(r"[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}", result.guid)


# --- Set Difference -----------------------------------------------------------


def test_set_difference_is_the_elements_of_set_1_not_in_set_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "set operations", "Set Difference.vim"],
        [(3, "output", SET), (10, "input", SET), (11, "input", SET)],
        {"one": (11, SET), "two": (10, SET)},
        {"difference": (3, SET)},
        {"one": {"a", "b", "c"}, "two": {"b", "z"}},
    )
    assert result.difference == {"a", "c"}


# --- Assert Array Dimension Count --------------------------------------------


def test_assert_array_dimension_count_passes_the_value_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _call(
        tmp_path,
        monkeypatch,
        ["<vilib>", "Utility", "TypeAssert", "Assert Array Dimension Count.vim"],
        [(1, "output", ARRAY), (2, "input", ARRAY), (4, "input", ARRAY)],
        {"kind": (2, ARRAY), "value": (4, ARRAY)},
        {"out": (1, ARRAY)},
        {"kind": [], "value": [1, 2, 3]},
    )
    assert result.out == [1, 2, 3]


# --- Open URL in Default Browser ---------------------------------------------


def _fake_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    fake = types.ModuleType("webbrowser")
    fake.open = lambda url, *a, **k: opened.append(url) or True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "webbrowser", fake)
    return opened


def test_open_url_string_encodes_a_space_and_opens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _fake_browser(monkeypatch)
    result = _call(
        tmp_path,
        monkeypatch,
        [
            "<vilib>",
            "Platform",
            "browser.llb",
            "Open URL in Default Browser (string).vi",
        ],
        [
            (0, "output", ERROR_CLUSTER),
            (3, "output", STRING),
            (4, "input", ERROR_CLUSTER),
            (7, "input", STRING),
        ],
        {"url": (7, STRING)},
        {"shown": (3, STRING)},
        {"url": "http://site.com/my report.html"},
    )
    assert opened == ["http://site.com/my%20report.html"]
    assert result.shown == "http://site.com/my%20report.html"


def test_open_url_path_opens_the_file_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _fake_browser(monkeypatch)
    page = tmp_path / "a report.html"
    result = _call(
        tmp_path / "run",
        monkeypatch,
        ["<vilib>", "Platform", "browser.llb", "Open URL in Default Browser (path).vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (3, "output", STRING),
            (4, "input", ERROR_CLUSTER),
            (7, "input", PATH),
        ],
        {"path": (7, PATH)},
        {"shown": (3, STRING)},
        {"path": page},
    )
    assert opened == [page.as_uri()]
    assert result.shown == page.as_uri()
    assert "%20" in result.shown


def test_open_url_path_relative_is_beside_the_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = _fake_browser(monkeypatch)
    app = tmp_path / "app" / "main.py"
    app.parent.mkdir()
    app.touch()
    monkeypatch.setattr(sys, "argv", [str(app)])
    _call(
        tmp_path / "run",
        monkeypatch,
        ["<vilib>", "Platform", "browser.llb", "Open URL in Default Browser (path).vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (3, "output", STRING),
            (4, "input", ERROR_CLUSTER),
            (7, "input", PATH),
        ],
        {"path": (7, PATH)},
        {"shown": (3, STRING)},
        {"path": Path("docs/index.html")},
    )
    assert opened == [(app.parent / "docs" / "index.html").as_uri()]


def test_enum_to_array_of_enums_stays_a_gap() -> None:
    entry = VILibResolver().resolve_recorded(
        "<vilib>/numeric/Enum to Array of Enums.vim"
    )
    assert entry is None or entry.python_code is None
