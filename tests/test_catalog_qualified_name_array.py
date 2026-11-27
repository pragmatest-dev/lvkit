"""``Qualified Name Array To Single String.vi`` and ``Single String To Qualified
Name Array.vi`` (``<vilib>/Utility/LVClass``): a qualified LabVIEW name is its parts
joined with a colon ('library1.lvlib:init.vi', NI's 'Using Libraries in LabVIEW
Projects'). The generated callers are run."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.vilib_caller import STRING, run_catalog_call

ARRAY = LVType(kind=LVTypeKind.ARRAY)
_DIR = ["<vilib>", "Utility", "LVClass"]


def _join(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parts: list[str]) -> str:
    source, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*_DIR, "Qualified Name Array To Single String.vi"],
        [(0, "output", STRING), (1, "input", ARRAY)],
        {"parts": (1, ARRAY)},
        {"name": (0, STRING)},
        {"parts": parts},
    )
    assert "lvkit" not in source
    return result.name


def _split(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> list[str]:
    source, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*_DIR, "Single String To Qualified Name Array.vi"],
        [(0, "output", ARRAY), (1, "input", STRING)],
        {"name": (1, STRING)},
        {"parts": (0, ARRAY)},
        {"name": name},
    )
    assert "lvkit" not in source
    return result.parts


def test_parts_are_joined_with_colons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _join(tmp_path, monkeypatch, ["Lib.lvlib", "Cls.lvclass"]) == (
        "Lib.lvlib:Cls.lvclass"
    )


def test_a_single_part_is_the_name_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _join(tmp_path, monkeypatch, ["Cls.lvclass"]) == "Cls.lvclass"


def test_a_qualified_name_splits_at_its_colons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _split(tmp_path, monkeypatch, "Outer.lvlib:Inner.lvlib:Cls.lvclass") == [
        "Outer.lvlib",
        "Inner.lvlib",
        "Cls.lvclass",
    ]
