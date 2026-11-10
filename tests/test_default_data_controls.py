"""A control with no ``DefaultData`` takes its default from the VI's default
data: the control's ``tdOffset`` plus the ``TM80`` ``IndexShift`` names its
``DataFill``. The VI is a tiny front panel written as heap XML, run through the
parser, graph, describe, lvnet (terse and verbose) and JSON."""

from __future__ import annotations

from pathlib import Path

from tests.structure_diagrams import Views, root_diagram, srn
from tests.test_default_data_values import (
    ARRAY,
    _data,
    _front_panel,
    _indicator,
    _load,
)

_EMPTY_ARRAY = "<SpecialDSTMCluster><Array><dim>0</dim></Array></SpecialDSTMCluster>"


def _control_vi(
    folder: Path,
    extra: str,
    slots: list[tuple[str, str | None]],
    version: tuple[int, int] | None = None,
) -> Views:
    """A VI with one array control ``indices`` on the connector pane, carrying
    ``extra`` (its ``tdOffset`` or its ``DefaultData``), saved by LabVIEW
    ``version``."""
    folder.mkdir()
    fp = _front_panel(
        _indicator(30, "indices", ARRAY, "indArr", extra, indicator=False), {0: 30}
    )
    xml = root_diagram(
        [],
        [
            srn(
                800,
                [
                    '<SL__arrayElement class="fPTerm" uid="5030">'
                    '<dco uid="30"/></SL__arrayElement>'
                ],
            )
        ],
        [],
    )
    return _load(folder, xml, slots, fp, version)


def test_a_control_with_no_default_takes_it_from_the_default_data(
    tmp_path: Path,
) -> None:
    from_fill = _control_vi(
        tmp_path / "fill", "<tdOffset>0</tdOffset>", [("empty array", _EMPTY_ARRAY)]
    )
    from_default_data = _control_vi(
        tmp_path / "saved",
        f'<DefaultData>"{_data(bytes(4))}"</DefaultData>',
        [("empty array", _EMPTY_ARRAY)],
    )
    assert '  indices: [I32] = "[]"' in from_fill.describe.splitlines()
    assert from_fill.describe == from_default_data.describe
    assert from_fill.terse == from_default_data.terse
    assert from_fill.lvnet == from_default_data.lvnet
    assert {**from_fill.json, "vi": ""} == {**from_default_data.json, "vi": ""}
    assert from_fill.json["connector_pane"]["terminals"][0]["default"] == "[]"
    assert '    in   indices : [I32] default "[]" @0' in from_fill.lvnet.splitlines()
    assert "    in   indices : [I32] @0" in from_fill.terse.splitlines()


def test_a_control_whose_slot_holds_its_default_keeps_no_default(
    tmp_path: Path,
) -> None:
    views = _control_vi(
        tmp_path / "none", "<tdOffset>0</tdOffset>", [("array default", None)]
    )
    assert "  indices: [I32] = " not in views.describe
    assert views.json["connector_pane"]["terminals"][0]["default"] is None
    assert "default" not in views.terse


def test_a_control_of_a_vi_saved_before_8_6_takes_no_default_from_the_default_data(
    tmp_path: Path,
) -> None:
    """Before LabVIEW 8.6 a control's ``tdOffset`` names its range or options
    entry, so no value is taken from it; the same VI saved by 8.6 or later takes
    one."""
    slots = [("empty array", _EMPTY_ARRAY)]
    offset = "<tdOffset>0</tdOffset>"
    saved_8_5 = _control_vi(tmp_path / "v85", offset, slots, version=(8, 5))
    saved_8_6 = _control_vi(tmp_path / "v86", offset, slots, version=(8, 6))
    saved_9_0 = _control_vi(tmp_path / "v90", offset, slots, version=(9, 0))
    assert "  indices: [I32]" in saved_8_5.describe.splitlines()
    assert saved_8_5.json["connector_pane"]["terminals"][0]["default"] is None
    assert '  indices: [I32] = "[]"' in saved_8_6.describe.splitlines()
    assert saved_8_6.describe == saved_9_0.describe
