"""A reference type prints in LabVIEW's words (``Queue refnum{Error}``, ``VI
refnum``, ``.NET refnum{...}``) in every view, and lvnet reads those words back
to the same type."""

from __future__ import annotations

import pytest

from lvkit.graph.lvnet_parse import _parsed_type_ref_shape
from lvkit.graph.lvnet_reconstruct import _reconstruct_type_ref
from lvkit.graph.render_lvnet import _lvnet_type_inline, lvnet_type_ref
from lvkit.models import LVType, LVTypeKind
from lvkit.refnum_kinds import (
    REFNUM_KINDS,
    ServerClass,
    parse_refnum_kind,
    refnum_kind_words,
)


def _refnum(
    ref_type: str | None,
    *,
    server_class: ServerClass | None = None,
    element: LVType | None = None,
    classname: str | None = None,
    event_name: str | None = None,
) -> LVType:
    return LVType(
        kind=LVTypeKind.PRIMITIVE,
        underlying_type="Refnum",
        ref_type=ref_type,
        server_class=server_class,
        element_type=element,
        classname=classname,
        user_event_name=event_name,
    )


def _dbl() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumFloat64")


def _string() -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


_KIND_WORDS = {
    "Occurrence": "Occurrence refnum",
    "Menu": "Menu refnum",
    "DataSocket": "DataSocket refnum",
    "ByteStream": "Byte Stream File refnum",
    "DataLog": "Data Log File refnum",
    "TCPNetConn": "TCP Network Connection refnum",
    "UDPNetConn": "UDP Network Connection refnum",
    "IrdaNetConn": "IrDA Network Connection refnum",
    "BluetoothCon": "Bluetooth Network Connection refnum",
    "DotNet": ".NET refnum",
    "AutoRef": "Automation refnum",
    "Callback": "Event Callback refnum",
    "Imaq": "IMAQ Session refnum",
    "TDMSFile": "TDMS File refnum",
    "Queue": "Queue refnum",
    "NotifierRef": "Notifier refnum",
    "DataValueRef": "Data Value Reference refnum",
    "UserEvent": "User Event refnum",
    "EventReg": "Event Registration refnum",
    "VisaRef": "VISA Resource Name refnum",
    "IVIRef": "IVI refnum",
}


@pytest.mark.parametrize("binary", sorted(_KIND_WORDS))
def test_each_kind_prints_its_words_in_every_type_view(binary: str) -> None:
    lv_type = _refnum(binary)
    expected = _KIND_WORDS[binary]
    assert lv_type.type_descriptor() == expected
    assert lv_type.type_descriptor(expand_named=False) == expected
    assert _lvnet_type_inline(lv_type) == expected
    assert lvnet_type_ref(lv_type) == expected


def test_the_table_lists_exactly_the_kinds_these_tests_name() -> None:
    plain = {k.binary for k in REFNUM_KINDS if k.binary != "LVObjCtl"}
    assert plain == set(_KIND_WORDS)


def test_a_generic_vi_server_reference_names_its_class() -> None:
    cases = [
        (ServerClass.VI, "VI refnum"),
        (ServerClass.APPLICATION, "Application refnum"),
        (None, "Control refnum"),
    ]
    for server_class, expected in cases:
        lv_type = _refnum("LVObjCtl", server_class=server_class)
        assert lv_type.type_descriptor() == expected
        assert _lvnet_type_inline(lv_type) == expected
        assert lvnet_type_ref(lv_type) == expected


def test_a_refnum_carries_its_type_in_braces() -> None:
    queue = _refnum("Queue", element=_dbl())
    assert queue.type_descriptor() == "Queue refnum{DBL}"
    assert _lvnet_type_inline(queue) == "Queue refnum{DBL}"
    assert lvnet_type_ref(queue) == "Queue refnum{ DBL }"
    notifier = _refnum("NotifierRef", element=_string())
    assert notifier.type_descriptor() == "Notifier refnum{String}"


def test_a_user_event_keeps_its_event_name() -> None:
    event = _refnum("UserEvent", element=_dbl(), event_name="stopped")
    assert event.type_descriptor() == "User Event refnum{stopped: DBL}"
    assert _lvnet_type_inline(event) == "User Event refnum{stopped : DBL}"


def test_a_generic_reference_prints_bare_refnum() -> None:
    for ref_type in ("Generic", None):
        lv_type = _refnum(ref_type)
        assert lv_type.type_descriptor() == "refnum"
        assert _lvnet_type_inline(lv_type) == "refnum"
        assert lvnet_type_ref(lv_type) == "refnum"


def test_a_class_reference_prints_its_class_name_alone() -> None:
    lv_type = _refnum("UDClassInst", classname="Lib.lvlib:Cls.lvclass")
    assert lv_type.type_descriptor() == "Lib.lvlib:Cls.lvclass"
    assert _lvnet_type_inline(lv_type) == "Lib.lvlib:Cls.lvclass"


def test_a_kind_with_no_ni_name_prints_its_binary_name() -> None:
    for binary in ("FIFORef", "UsrDefined", "UsrDefndTag", "UsrDefTagFlt"):
        assert _refnum(binary).type_descriptor() == f"{binary} refnum"


def _rows() -> list[tuple[str, ServerClass | None]]:
    return [(kind.binary, kind.server_class) for kind in REFNUM_KINDS]


@pytest.mark.parametrize(("binary", "server_class"), _rows())
def test_every_row_reads_back_to_the_same_type(
    binary: str, server_class: ServerClass | None
) -> None:
    for element in (None, _dbl()):
        original = _refnum(binary, server_class=server_class, element=element)
        text = _lvnet_type_inline(original)
        rebuilt = _reconstruct_type_ref(text, {}, {})
        assert rebuilt.ref_type == binary
        assert rebuilt.server_class == server_class
        assert (rebuilt.element_type is not None) == (element is not None)
        assert lvnet_type_ref(rebuilt) == lvnet_type_ref(original)
        shape = _parsed_type_ref_shape(text, {})
        if element is not None:
            assert shape[0] == "refnum"
            assert shape[1] == refnum_kind_words(binary, server_class)


def test_unknown_kind_words_read_back_unchanged() -> None:
    assert parse_refnum_kind("Mystery Thing") == ("Mystery Thing", None)
    rebuilt = _reconstruct_type_ref("Mystery Thing refnum{DBL}", {}, {})
    assert rebuilt.ref_type == "Mystery Thing"
    assert lvnet_type_ref(rebuilt) == "Mystery Thing refnum{ DBL }"
    bare = _reconstruct_type_ref("Mystery Thing refnum", {}, {})
    assert bare.ref_type == "Mystery Thing"
    assert _lvnet_type_inline(bare) == "Mystery Thing refnum"


def test_the_machine_kind_stays_the_binary_name() -> None:
    lv_type = _refnum("NotifierRef", element=_dbl())
    assert lv_type.ref_type == "NotifierRef"
