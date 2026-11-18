"""``Enum to Array of Enums.vim``: NI's "array of enums returns an array containing
each enum value in order" (the example {cat, dog, bird} gives a three-element array
of cat, dog and bird). The caller diagram is generated from the catalog entry the
recorded path names and its Python is run; the array is the item values of the
enum type wired in, whatever value the enum holds."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import EnumValue, LVType, LVTypeKind
from tests.native_vi import array
from tests.vilib_caller import I32, run_catalog_call

PATH = ["<vilib>", "numeric", "Enum to Array of Enums.vim"]


def _enum(*labels: str) -> LVType:
    return LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt8",
        values={label: EnumValue(value=i) for i, label in enumerate(labels)},
        typedef_name="Lib.lvlib:Animal.ctl",
    )


def _call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    enum: LVType,
    value: int,
    gaps: list[Gap] | None = None,
):
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        PATH,
        pane=[(0, "output", array("UnitUInt8")), (1, "input", enum)],
        inputs={"enum": (1, enum)},
        outputs={"out": (0, array("UnitUInt8"))},
        arguments={"enum": value},
        gaps=gaps,
        runtime_not_implemented=gaps is not None,
    )


def test_the_array_holds_every_item_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, result = _call(tmp_path, monkeypatch, _enum("cat", "dog", "bird"), 1)
    assert result.out == [0, 1, 2]
    assert "lvkit" not in source


def test_the_value_wired_in_does_not_change_the_array(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _call(tmp_path, monkeypatch, _enum("a", "b"), 0)
    assert result.out == [0, 1]


def test_an_input_that_is_not_an_enum_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError, match="not recorded as an enum"):
        _call(tmp_path, monkeypatch, I32, 0, gaps)
    assert len(gaps) == 1
    assert "not recorded as an enum" in gaps[0].detail
