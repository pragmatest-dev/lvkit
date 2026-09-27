"""``describe`` on a ``.ctl``: the text page, the JSON shape and the CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lvkit import cli
from lvkit.graph.describe_typedef import describe_ctl_file, describe_typedef
from lvkit.graph.node_kinds import NodeType
from lvkit.graph.typedef import TypedefField, TypedefInfo, TypedefRef
from lvkit.models import EnumValue, LVType, LVTypeKind

from .conftest import SAMPLES_ROOT

_ENUM_CTL = SAMPLES_ROOT / "DCAF-DAQModule/source/editor node/Permissions Enum.ctl"
_CLUSTER_CTL = (
    SAMPLES_ROOT
    / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/API_Test Settings.ctl"
)

_STRING = LVType(LVTypeKind.PRIMITIVE, underlying_type="String")
_DBL = LVType(LVTypeKind.PRIMITIVE, underlying_type="DBL")


def _info(**kw) -> TypedefInfo:
    base = {
        "key": "/p/Cfg.ctl",
        "name": "Cfg.ctl",
        "root_type": LVType(LVTypeKind.CLUSTER),
        "default": None,
        "fields": (),
        "uses": (),
        "owned_by": (),
        "used_by": (),
    }
    return TypedefInfo(**{**base, **kw})


def test_a_cluster_lists_its_fields_with_typed_defaults() -> None:
    fields = (
        TypedefField("Name", _STRING, "abc", ()),
        TypedefField("Gain", _DBL, "3600.0", ()),
        TypedefField("Steps", LVType(LVTypeKind.ARRAY, element_type=_DBL), None, (),
                     ("1.0", "2.0")),
        TypedefField("Sub", LVType(LVTypeKind.CLUSTER), None, (
            TypedefField("Inner", _DBL, "0.5", ()),
        )),
    )
    text = describe_typedef(_info(fields=fields))
    lines = text.splitlines()
    assert lines[0] == "# Cfg.ctl" and "  Cluster (4 fields)" in lines
    assert '  Name: String = "abc"' in lines  # a string default is quoted
    assert "  Gain: DBL = 3600.0" in lines  # a number's is not
    assert "  Steps: [DBL] = [1.0, 2.0]" in lines
    assert "    Inner: DBL = 0.5" in lines  # nested one level in


def test_an_enum_lists_its_values_and_default() -> None:
    enum = LVType(LVTypeKind.ENUM, values={"Read": EnumValue(0), "Write": EnumValue(1)})
    text = describe_typedef(_info(root_type=enum, default="Write"))
    assert "## Values\n  0: Read\n  1: Write" in text
    assert "  Default = Write" in text  # an enum item is bare, not a string


def test_empty_sections_are_left_out_and_refs_are_listed() -> None:
    text = describe_typedef(_info())
    assert "## Uses" not in text and "## Owned by" not in text
    ref = TypedefRef(NodeType.LIBRARY, "Lib.lvlib", "/p/Lib.lvlib")
    with_refs = _info(owned_by=(ref,))
    assert "## Owned by\n  Lib.lvlib (library)" in describe_typedef(with_refs)
    assert "-- /p/Lib.lvlib" in describe_typedef(with_refs, verbose=True)
    assert "Path: /p/Cfg.ctl" in describe_typedef(_info(), verbose=True)


def test_a_missing_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        describe_ctl_file(tmp_path / "Nope.ctl")


@pytest.mark.needs_samples
@pytest.mark.skipif(not _CLUSTER_CTL.exists(), reason="icon-editor sample absent")
def test_a_real_cluster_control_text_and_json():
    text = describe_ctl_file(_CLUSTER_CTL)
    assert isinstance(text, str) and "## Fields" in text and "Text color" in text
    data = describe_ctl_file(_CLUSTER_CTL, fmt="json")
    assert isinstance(data, dict) and data["kind"] == "cluster"
    assert {f["name"] for f in data["fields"]} >= {"Font", "Size", "Text color"}


@pytest.mark.needs_samples
@pytest.mark.skipif(not _ENUM_CTL.exists(), reason="DCAF sample absent")
def test_the_cli_describes_a_control(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["lvkit", "describe", str(_ENUM_CTL)])
    assert cli.main() == 0
    assert "  0: Read from HW" in capsys.readouterr().out
    monkeypatch.setattr(
        sys, "argv", ["lvkit", "describe", str(_ENUM_CTL), "--format", "json"]
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "enum"
    monkeypatch.setattr(
        sys, "argv", ["lvkit", "describe", str(_ENUM_CTL), "--format", "lvnet"]
    )
    assert cli.main() == 0
    assert capsys.readouterr().out.startswith("typedef Permissions Enum.ctl :")
