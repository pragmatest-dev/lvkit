"""The drawing a stored Picture holds, read as records (``lvkit.picture_records``).

The bytes are those of the ni-labview-icon-editor sample: the 3,102 byte Picture
of the ``Layer`` constant of ``Create Icon Framework.vi`` (one copy pixmap
record, 32 x 32, all white; its 3,072 pixel bytes are written out here as
``ff``) and the 272 byte Picture of ``Install Glyphs.vi``'s ``Glyph list``
default (eight records, ``tests/data/picture_icon_library_272.hex``)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lvkit.codegen.context import _format_constant
from lvkit.conversion_gap import ConversionGap
from lvkit.graph.construction import decode_constant
from lvkit.graph.models import Constant
from lvkit.models import ClusterField, LVType, LVTypeKind, UndecodedPicture
from lvkit.parser.models import ParsedConstant
from lvkit.parser.vi import _decode_element
from lvkit.picture_records import PictureData, walk_picture
from tests.class_graphs import Project
from tests.native_vi import generate
from tests.test_flat_class import BASE, PICTURE, pascal_name, round_trip

DATA = Path(__file__).parent / "data"

# The record of the 3,102 byte Picture: operation 29, payload length 3,096, the
# 24 byte header (pixel byte count 3,072, 0, 32, 32, 24 bits, 10 bytes), pixels.
COPY_PIXMAP_HEADER = bytes.fromhex(
    "001d00000c1800000c00000000000020002000180000000000ffffff0000"
)
WHITE_32 = COPY_PIXMAP_HEADER + b"\xff" * 3072


def _stored(drawing: bytes) -> bytes:
    """A Picture as stored: its length, then its drawing."""
    return struct.pack(">I", len(drawing)) + drawing


def _icon_library() -> bytes:
    return bytes.fromhex(
        (DATA / "picture_icon_library_272.hex").read_text(encoding="utf-8").strip()
    )


def test_the_white_icon_is_one_copy_pixmap_record() -> None:
    value, consumed, structured = _decode_element(_stored(WHITE_32), PICTURE)
    assert consumed == 3106 and len(WHITE_32) == 3102
    assert isinstance(structured, PictureData)
    assert [r.opcode for r in structured.records] == [29]
    assert value == "<Picture, 3102 bytes: 29 copy pixmap (3096 bytes)>"
    pixmap = structured.pixmap()
    assert pixmap is not None
    assert (pixmap.width, pixmap.height) == (32, 32)
    assert set(pixmap.pixels) == {255} and len(pixmap.pixels) == 3072
    assert structured.undecoded() is None


def test_the_272_byte_picture_shows_its_eight_records() -> None:
    drawing = _icon_library()
    value, consumed, structured = _decode_element(_stored(drawing), PICTURE)
    assert consumed == 276
    assert isinstance(structured, PictureData)
    assert [r.opcode for r in structured.records] == [31, 18] * 4
    assert [len(r.payload) for r in structured.records] == [20, 36] * 4
    assert value.startswith(
        "<Picture, 272 bytes: 31 (20 bytes), 18 text box (36 bytes), 31 (20 bytes)"
    )
    why = structured.undecoded()
    assert why is not None and "31, 18 text box" in why


def test_records_that_do_not_end_at_the_length_are_undecoded() -> None:
    # a record whose length runs past the end
    past_end = struct.pack(">HI", 29, 100) + b"\x00" * 10
    assert walk_picture(past_end) is None
    value, consumed, structured = _decode_element(_stored(past_end), PICTURE)
    assert structured == UndecodedPicture(16) and consumed == 20
    # a stub too short for a record header
    assert walk_picture(b"\x01\x02\x03") is None
    # no records at all is not a drawing
    assert walk_picture(b"") == PictureData(())


def test_a_copy_pixmap_that_is_not_square_or_not_24_bit_is_not_read() -> None:
    wide = bytearray(WHITE_32)
    wide[6 + 8 : 6 + 10] = struct.pack(">H", 16)
    assert "which of the two is the width" in str(walk_picture(bytes(wide)).undecoded())
    shallow = bytearray(WHITE_32)
    shallow[6 + 12 : 6 + 14] = struct.pack(">H", 8)
    assert "8 bits" in str(walk_picture(bytes(shallow)).undecoded())
    two = WHITE_32 + WHITE_32
    assert "2 copy pixmap records" in str(walk_picture(two).undecoded())


def test_a_picture_that_does_not_read_is_a_load_problem() -> None:
    from lvkit.graph.construction import ConstructionMixin
    from lvkit.run_report import Problem, ProblemKind, ProblemStage

    class Host(ConstructionMixin):
        def __init__(self) -> None:
            self.problems: list[Problem] = []

        def key_label(self, key: str) -> str:
            return key

        def record_problem(
            self, vi_key: str, problem: Problem, may_stop: bool = True
        ) -> None:
            self.problems.append(problem)

    host = Host()
    host._record_unread_picture("V.vi", PictureData(()), "the constant (uid 5)")
    assert host.problems == []
    host._record_unread_picture("V.vi", UndecodedPicture(7), "the constant (uid 5)")
    (problem,) = host.problems
    assert (problem.vi, problem.stage, problem.kind) == (
        "V.vi",
        ProblemStage.LOAD,
        ProblemKind.VALUE_NOT_READ,
    )
    assert problem.reason == (
        "the constant (uid 5) holds a Picture of 7 bytes whose records do not "
        "add up to its stored length"
    )


def _constant_of(drawing: bytes) -> Constant:
    decoded = decode_constant(
        ParsedConstant(uid="c", type_desc="", value=_stored(drawing).hex()),
        lv_type=PICTURE,
    )
    return Constant(
        id="c",
        value=decoded.value,
        lv_type=PICTURE,
        unwritable=decoded.unwritable,
        structured_value=decoded.structured,
    )


def test_a_constant_of_one_copy_pixmap_generates_a_white_image() -> None:
    constant = _constant_of(WHITE_32)
    assert constant.unwritable is None
    generated = generate(
        {},
        {"icon": PICTURE},
        [],
        [("c", "fp:icon")],
        constants={"c": (constant.value, PICTURE, constant.structured_value)},
    )
    assert "lvkit" not in generated.source
    image = generated.function().icon
    assert image.mode == "RGBA" and image.size == (32, 32)
    assert image.getcolors() == [(1024, (255, 255, 255, 255))]


def test_a_constant_of_other_operations_is_a_gap_naming_them() -> None:
    constant = _constant_of(_icon_library())
    assert isinstance(constant.unwritable, PictureData)
    with pytest.raises(ConversionGap, match=r"31, 18 text box"):
        _format_constant(constant)


def test_a_constant_of_other_operations_raises_where_it_runs() -> None:
    from lvkit.codegen.gaps import Gap

    constant = _constant_of(_icon_library())
    gaps: list[Gap] = []
    generated = generate(
        {},
        {"icon": PICTURE},
        [],
        [("c", "fp:icon")],
        constants={
            "c": (
                constant.value,
                PICTURE,
                constant.structured_value,
                constant.unwritable,
            )
        },
        gaps=gaps,
    )
    assert len(gaps) == 1 and "31, 18 text box" in gaps[0].reason
    with pytest.raises(NotImplementedError, match="31, 18 text box"):
        generated.function()


def test_an_empty_picture_constant_is_none() -> None:
    constant = _constant_of(b"")
    assert (constant.value, constant.unwritable) == ("None", None)


# === Flatten and unflatten ===


def _picture_cast(source: LVType, target: LVType):
    from tests.test_flat import cast

    return cast(source, target)


STRING = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def test_an_empty_picture_flattens_to_a_zero_length_and_reads_back() -> None:
    flatten = _picture_cast(PICTURE, STRING)
    assert flatten(None).r.encode("latin-1") == b"\x00\x00\x00\x00"
    unflatten = _picture_cast(STRING, PICTURE)
    assert unflatten("\x00\x00\x00\x00").r is None


def test_a_picture_of_one_copy_pixmap_reads_back_as_its_image() -> None:
    unflatten = _picture_cast(STRING, PICTURE)
    image = unflatten(_stored(WHITE_32).decode("latin-1")).r
    assert image.size == (32, 32)
    assert image.getcolors() == [(1024, (255, 255, 255, 255))]


def test_other_operations_raise_naming_them_where_they_run() -> None:
    unflatten = _picture_cast(STRING, PICTURE)
    with pytest.raises(NotImplementedError, match="operation 31, 18, 31"):
        unflatten(_stored(_icon_library()).decode("latin-1"))


def test_a_picture_that_runs_past_its_data_is_a_struct_error() -> None:
    import struct as struct_module

    unflatten = _picture_cast(STRING, PICTURE)
    with pytest.raises(struct_module.error, match="runs past"):
        unflatten("\x00\x00\x00\x10\x00\x1d")


def test_a_drawn_picture_is_not_written_and_says_its_size() -> None:
    from PIL import Image

    flatten = _picture_cast(PICTURE, STRING)
    with pytest.raises(NotImplementedError) as raised:
        flatten(Image.new("RGBA", (5, 7)))
    assert "writing a drawn Picture in LabVIEW's flattened form is not supported" in (
        str(raised.value)
    )
    assert "5 x 7 pixels" in str(raised.value)
    # a picture with no pixels is the empty one
    assert flatten(Image.new("RGBA", (0, 0))).r.encode("latin-1") == b"\x00" * 4


# === A class with a Picture field ===


def test_a_class_with_a_picture_field_round_trips_when_it_is_empty(
    tmp_path: Path,
) -> None:
    project = Project(tmp_path)
    project.add_class("Base", [ClusterField(name="Drawing", type=PICTURE)])
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    assert package.gaps == []
    with package.use() as module:
        obj = module("base").Base()
        obj._Base__drawing = None
        result = module("roundtrip").roundtrip(obj=obj)
        private = struct.pack(">i", 0)
        assert result.data.encode("latin-1") == (
            struct.pack(">I", 1)
            + pascal_name("Base.lvclass")
            + struct.pack(">HHHH", 1, 0, 0, 0)
            + struct.pack(">i", len(private))
            + private
        )
        assert result.back._Base__drawing is None


def test_a_class_with_a_picture_field_raises_when_a_drawn_picture_is_written(
    tmp_path: Path,
) -> None:
    from PIL import Image

    project = Project(tmp_path)
    project.add_class("Base", [ClusterField(name="Drawing", type=PICTURE)])
    project.add_vi("RoundTrip", round_trip(BASE))
    package = project.generate()
    with package.use() as module:
        obj = module("base").Base()
        obj._Base__drawing = Image.new("RGBA", (3, 4))
        with pytest.raises(NotImplementedError, match="3 x 4 pixels"):
            module("roundtrip").roundtrip(obj=obj)
