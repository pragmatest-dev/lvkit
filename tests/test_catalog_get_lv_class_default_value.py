"""``Get LV Class Default Value.vi``: the generated caller runs and gets a new
instance of the project's class at the path it is given, or error 7 when the
project has no class at that path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.vilib_caller import BOOL, I32, PATH, STRING, run_catalog_call

ERROR = LVType(
    kind=LVTypeKind.CLUSTER,
    fields=[
        ClusterField(name="status", type=BOOL),
        ClusterField(name="code", type=I32),
        ClusterField(name="source", type=STRING),
    ],
)
_OBJECT = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
_RECORDED = ["<vilib>", "Utility", "LVClass", "Get LV Class Default Value.vi"]
_PANE = [
    (0, "output", ERROR),
    (3, "output", _OBJECT),
    (8, "input", ERROR),
    (11, "input", PATH),
]
_CLASSES = {
    "Alpha.lvclass": "class Alpha:\n    data = 'alpha default'\n",
    "Beta.lvclass": "class Beta:\n    data = 'beta default'\n",
}


def _call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, class_path: str
) -> tuple[str, Any]:
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        _RECORDED,
        _PANE,
        {"class_path": (11, PATH)},
        {"object_": (3, _OBJECT), "error_out": (0, ERROR)},
        {"class_path": Path(class_path)},
        classes=_CLASSES,
    )


@pytest.mark.parametrize(
    ("class_path", "name", "data"),
    [
        ("Alpha.lvclass", "Alpha", "alpha default"),
        ("Beta.lvclass", "Beta", "beta default"),
    ],
)
def test_the_class_at_the_path_gets_a_default_instance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    class_path: str,
    name: str,
    data: str,
) -> None:
    source, result = _call(tmp_path, monkeypatch, class_path)
    assert "lvkit" not in source
    assert type(result.object_).__name__ == name
    assert result.object_.data == data
    assert result.error_out is None


def test_an_unknown_class_path_is_error_7_naming_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = _call(tmp_path, monkeypatch, "Missing.lvclass")
    assert result.object_ is None
    assert result.error_out.code == 7
    assert "Missing.lvclass" in result.error_out.message
