"""The pictures a front-panel control's parts embed are read from the VI's own
picture sections (``MNGI``), by picture id."""

from __future__ import annotations

from pathlib import Path

from lvkit.parser.fp_pictures import attach_panel_pictures
from lvkit.parser.models import ParsedFPControl, ParsedFPPart


def _control(uid: str, ids: tuple[int, ...], children=()) -> ParsedFPControl:
    return ParsedFPControl(
        uid=uid,
        name=uid,
        control_type="stdBool",
        bounds=(0, 0, 10, 10),
        parts=[ParsedFPPart(21, "multiCosm", (0, 0, 10, 10), image_ids=ids)],
        children=list(children),
    )


def test_a_positive_id_gets_the_picture_the_vi_stores_for_it(tmp_path: Path) -> None:
    (tmp_path / "vi.xml").write_text(
        "<VI><MNGI>"
        '<Section Index="2" Format="png" File="vi_MNGI2.png"/>'
        '<Section Index="3" Format="png" File="vi_MNGI3.png"/>'
        "</MNGI><DSIM>"
        '<Section Index="9" File="vi_DSIM.bin"/></DSIM></VI>',
        encoding="utf-8",
    )
    (tmp_path / "vi_MNGI2.png").write_bytes(b"two")
    png = b"\x89PNG\r\n\x1a\nxxxxIEND1234"
    (tmp_path / "vi_DSIM.bin").write_bytes(b"\x00\x01" + png + b"tail")
    (tmp_path / "vi_MNGI3.png").write_bytes(b"three")
    field = _control("field", (3, -404, 5))
    cluster = _control("cluster", (2,), children=[field])
    attach_panel_pictures(tmp_path / "vi_FPHb.xml", [cluster])
    # a negative id is a built-in shape; 5 has no picture
    assert cluster.pictures == {2: b"two"}
    assert field.pictures == {3: b"three"}
    # a DSIM picture is carved from its section's bytes
    dsim = _control("dsim", (9,))
    attach_panel_pictures(tmp_path / "vi_FPHb.xml", [dsim])
    assert dsim.pictures == {9: png}


def test_a_vi_without_picture_sections_leaves_pictures_empty(tmp_path: Path) -> None:
    (tmp_path / "vi.xml").write_text("<VI/>", encoding="utf-8")
    ctrl = _control("a", (2,))
    attach_panel_pictures(tmp_path / "vi_FPHb.xml", [ctrl])
    assert ctrl.pictures == {}
