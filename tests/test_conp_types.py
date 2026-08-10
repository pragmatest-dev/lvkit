"""CONP connector-pane type-pool decoding — the authoritative, fully-named type
source for pre-LV9 VIs (``conp_types``).

Hermetic tests hand-build CONP byte streams (pinning the binary format); the
``needs_samples`` tests prove end-to-end recovery on real LabVIEW 8.2 VIs.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lvkit.parser.conp_types import decode_conp_terminals


def _td(type_code: int, body: bytes) -> bytes:
    """One TypeDesc: u16 total_len (incl. these 2 bytes), u8 flags, u8 type."""
    return struct.pack(">HBB", 4 + len(body), 0x40, type_code) + body


def _pstr(s: str) -> bytes:
    return bytes([len(s)]) + s.encode("latin1")


def _conp(*tds: bytes) -> bytes:
    return struct.pack(">I", len(tds)) + b"".join(tds)


def test_error_cluster_fields_and_terminal_recovered():
    """A cluster names its fields by index into earlier TDs; the connector-pane
    map (0xf0) names the terminal slots."""
    tds = [
        _td(0x21, _pstr("status")),                       # [0] Boolean
        _td(0x03, _pstr("code")),                         # [1] I32
        _td(0x30, b"\xff\xff\xff\xff" + _pstr("source")),  # [2] String
        _td(0x50, struct.pack(">HHHH", 3, 0, 1, 2)         # [3] Cluster refs 0,1,2
              + _pstr("error out")),
        _td(0xF0, struct.pack(">HH", 1, 3)),               # [4] conpane: slot0->TD3
    ]
    terms = decode_conp_terminals(_conp(*tds))
    assert len(terms) == 1
    t = terms[0]
    assert t.slot == 0 and t.name == "error out"
    assert t.lv_type is not None and t.lv_type.kind == "cluster"
    assert [f.name for f in (t.lv_type.fields or [])] == [
        "status", "code", "source"
    ]
    # Named status/code/source -> the shared error-cluster detector fires.
    assert t.lv_type.lv_label() == "error cluster"


def test_empty_conp_returns_no_terminals():
    """An LV9+ VI carries a 2-byte empty CONP stub (types are in VCTP)."""
    assert decode_conp_terminals(b"\x00\x00") == []
    assert decode_conp_terminals(struct.pack(">I", 0)) == []


def test_void_slots_are_skipped():
    """Empty connector-pane slots point at a Void (0x00) TD and yield no
    terminal."""
    tds = [
        _td(0x00, b""),                        # [0] Void
        _td(0x0A, _pstr("x")),                 # [1] DBL
        _td(0xF0, struct.pack(">HHH", 2, 0, 1)),  # slots: void, TD1
    ]
    terms = decode_conp_terminals(_conp(*tds))
    assert [t.slot for t in terms] == [1]
    assert terms[0].lv_type is not None
    assert terms[0].lv_type.lv_label() == "DBL"


def test_refnum_kind_and_class_name():
    """A refnum's u16 sub-kind maps to a RefType (``Queue refnum``); a class
    refnum (UDClassInst) additionally carries its ``.lvclass`` name."""
    queue = _td(0x70, struct.pack(">HHH", 18, 1, 1) + _pstr("queue"))
    cls = _td(
        0x70,
        struct.pack(">H", 30) + b"\x00" * 6 + b"\x13" + _pstr("Foo.lvclass")
        + b"\x00" + _pstr("obj in"),
    )
    tds = [queue, cls, _td(0xF0, struct.pack(">HHH", 2, 0, 1))]
    terms = decode_conp_terminals(_conp(*tds))
    by_slot = {t.slot: t for t in terms}
    assert by_slot[0].lv_type is not None
    assert by_slot[0].lv_type.lv_label() == "Queue refnum"
    assert by_slot[1].lv_type is not None
    assert by_slot[1].lv_type.lv_label() == "Foo.lvclass"


def test_class_refnum_label_follows_class_name():
    """A class refnum is ``[class name][terminal label]``. The class name is the
    TYPE; the label follows it. LabVIEW's default label for a dynamic-dispatch
    terminal is its class name, disambiguated ``<class> 2`` for a second
    same-class terminal — that IS the label, not garbled."""
    cls = _td(
        0x70,
        struct.pack(">H", 30) + b"\x00" * 6
        + _pstr("Class1.lvclass") + b"\x00\x00" + _pstr("Class1.lvclass 2"),
    )
    terms = decode_conp_terminals(_conp(cls, _td(0xF0, struct.pack(">HH", 1, 0))))
    assert len(terms) == 1
    assert terms[0].lv_type is not None
    assert terms[0].lv_type.classname == "Class1.lvclass"  # the TYPE
    assert terms[0].name == "Class1.lvclass 2"             # the LABEL

    # A developer-renamed terminal sits in the same slot.
    renamed = _td(
        0x70,
        struct.pack(">H", 30) + b"\x00" * 6
        + _pstr("Class1.lvclass") + b"\x00\x00" + _pstr("reference in"),
    )
    t = decode_conp_terminals(
        _conp(renamed, _td(0xF0, struct.pack(">HH", 1, 0)))
    )[0]
    assert t.name == "reference in"


def test_malformed_conp_never_raises():
    assert decode_conp_terminals(b"\xff\xff\xff\xff\x00\x0c") == []
    assert decode_conp_terminals(b"") == []


# --- end-to-end on real LabVIEW 8.2 VIs -------------------------------------

_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


def _labels(vi_path: Path) -> set[str]:
    from lvkit.mcp.server import _load_one

    g, name = _load_one(str(vi_path))
    terms = [
        *g.get_inputs(name, public_only=False),
        *g.get_outputs(name, public_only=False),
    ]
    return {t.faithful_type_label() for t in terms}


_NEW_VI = (
    _SAMPLES / "JKI-VI-Tester" / "source" / "Classes" / "TestSuite" / "New.vi"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _NEW_VI.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_class_refnum_resolves_to_class_name():
    """A pre-LV9 class method's dynamic-dispatch terminal resolves to its class
    name via CONP (the class name isn't in the FP heap)."""
    labels = _labels(_NEW_VI)
    assert "TestSuite.lvclass" in labels, labels
    assert "class" not in labels, labels  # no bare family word left


_LV82_VI = (
    _SAMPLES / "JKI-VI-Tester" / "source" / "Build Support"
    / "Package Builder Utilities"
    / "Auto Increment Package Version__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
)


@pytest.mark.needs_samples
@pytest.mark.skipif(not _LV82_VI.exists(), reason="JKI-VI-Tester sample absent")
def test_lv82_terminal_names_recovered_from_conp():
    """Pre-LV9 controls have no resolvable FP-heap label (they fall back to
    ``control_<uid>``); CONP carries the real connector-pane terminal names."""
    from lvkit.mcp.server import _load_one

    g, name = _load_one(str(_LV82_VI))
    terms = [
        *g.get_inputs(name, public_only=False),
        *g.get_outputs(name, public_only=False),
    ]
    names = {t.name for t in terms}
    assert {"error out", "Version String In", "Version String Out"} <= names, names
    assert not any((n or "").startswith("control_") for n in names), names
