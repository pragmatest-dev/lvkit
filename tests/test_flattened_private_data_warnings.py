"""An unreadable private-data control a class keeps inside its ``.lvclass`` is
the control's problem, named by the path the class records for it (never the
cache copy that was read), and what pylabview prints while reading it does not
reach stderr."""

from __future__ import annotations

from pathlib import Path

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode


def _packed(data: bytes) -> str:
    """``data`` as a LabVIEW packed string: 6 bits per character from ``!``."""
    bits = "".join(f"{b:08b}" for b in data)
    bits += "0" * (-len(bits) % 6)
    return "".join(chr(0x21 + int(bits[i : i + 6], 2)) for i in range(0, len(bits), 6))


def test_an_unreadable_saved_private_data_control_is_the_controls_problem(
    tmp_path: Path, capsys
) -> None:
    garbage = b"\x00\x00RSRC\r\n" + b"\x00\x03\x00\x00" + b"\xff" * 64
    lvclass = tmp_path / "Broken.lvclass"
    lvclass.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Library LVVersion="21008000">\n'
        '\t<Property Name="NI.LVClass.FlattenedPrivateDataCTL" Type="Bin">'
        f"{_packed(garbage)}</Property>\n"
        '\t<Item Name="Broken.ctl" Type="Class Private Data" URL="Broken.ctl"/>\n'
        "</Library>\n",
        encoding="utf-8",
    )

    g = InMemoryVIGraph()
    g.load_lvclass(lvclass, mode=LoadMode.MINIMAL)

    problems = g.problems()
    assert [Path(p.vi).name for p in problems] == ["Broken.ctl"]
    assert g.get_class_fields(str(lvclass.resolve())) == []
    assert capsys.readouterr().err == ""
