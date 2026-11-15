"""The gRPC library's enum conversion VIs write Python on the proto enum's own
class: ``Enum.values().index(proto)`` and ``Enum.values()[lv]``. The generated
callers are run: with protobuf's real ``type_pb2`` enums, with a
``session_pb2`` built the way NI's package builds it, and with an enum whose
numbers are sparse and whose items include an alias."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.proto_enums import ProtoEnum, find_proto_enum
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import I32, STRING, U32, run_catalog_call

GRPC_ID = I32
SHARED = ["<vilib>", "gRPC", "LabVIEW gRPC Library", "Shared"]
TO_LV = [*SHARED, "Get LV Enum Value from Proto Value.vi"]
TO_PROTO = [*SHARED, "Get Proto Enum Value from LV Value.vi"]


def _pane(value_in: Any, value_out: Any) -> list[tuple[int, str, Any]]:
    return [
        (0, "output", ERROR_CLUSTER),
        (1, "output", value_out),
        (3, "output", GRPC_ID),
        (8, "input", ERROR_CLUSTER),
        (9, "input", value_in),
        (10, "input", STRING),
        (11, "input", GRPC_ID),
    ]


def _convert(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    to_lv: bool,
    enum_name: str,
    value: int,
    gaps: list[Gap] | None = None,
    name_is_constant: bool = True,
) -> Any:
    value_type_in, value_type_out = (I32, U32) if to_lv else (U32, I32)
    out_name = "lv" if to_lv else "proto"
    inputs: dict[str, Any] = {"value": (9, value_type_in), "grpc_id": (11, GRPC_ID)}
    arguments: dict[str, Any] = {"value": value, "grpc_id": 7}
    constants = None
    if name_is_constant:
        constants = {10: (enum_name, STRING)}
    else:
        inputs["name"] = (10, STRING)
        arguments["name"] = enum_name
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        TO_LV if to_lv else TO_PROTO,
        _pane(value_type_in, value_type_out),
        inputs,
        {
            out_name: (1, value_type_out),
            "id_out": (3, GRPC_ID),
            "error": (0, ERROR_CLUSTER),
        },
        arguments,
        gaps=gaps,
        constants=constants,
        runtime_not_implemented=gaps is not None,
    )
    return result


def test_proto_numbers_become_the_position_of_the_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, True, "google_protobuf_Field_Kind", 18)
    assert result.lv == 18
    assert result.id_out == 7
    assert result.error is None


def test_a_position_becomes_the_number_of_the_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, False, "google_protobuf_Syntax", 2)
    assert result.proto == 2
    assert result.id_out == 7


def test_the_order_is_the_registered_one_for_every_enum_of_the_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from google.protobuf import type_pb2

    registered = {
        "google_protobuf_Field_Kind": "TYPE_UNKNOWN=0;TYPE_DOUBLE=1;TYPE_FLOAT=2;"
        "TYPE_INT64=3;TYPE_UINT64=4;TYPE_INT32=5;TYPE_FIXED64=6;TYPE_FIXED32=7;"
        "TYPE_BOOL=8;TYPE_STRING=9;TYPE_GROUP=10;TYPE_MESSAGE=11;TYPE_BYTES=12;"
        "TYPE_UINT32=13;TYPE_ENUM=14;TYPE_SFIXED32=15;TYPE_SFIXED64=16;"
        "TYPE_SINT32=17;TYPE_SINT64=18",
        "google_protobuf_Field_Cardinality": "CARDINALITY_UNKNOWN=0;"
        "CARDINALITY_OPTIONAL=1;CARDINALITY_REQUIRED=2;CARDINALITY_REPEATED=3",
        "google_protobuf_Syntax": "SYNTAX_PROTO2=0;SYNTAX_PROTO3=1;SYNTAX_EDITIONS=2",
    }
    classes = {
        "google_protobuf_Field_Kind": type_pb2.Field.Kind,
        "google_protobuf_Field_Cardinality": type_pb2.Field.Cardinality,
        "google_protobuf_Syntax": type_pb2.Syntax,
    }
    for name, items in registered.items():
        numbers = [int(item.split("=")[1]) for item in items.split(";")]
        names = [item.split("=")[0] for item in items.split(";")]
        assert classes[name].values() == numbers
        assert [classes[name].Name(n) for n in numbers] == names
        assert find_proto_enum(name) is not None


def test_a_number_the_enum_does_not_hold_is_the_librarys_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, True, "google_protobuf_Syntax", 9)
    assert result.error.code == -2003
    assert result.lv == 0
    assert result.id_out == 7


def test_a_position_past_the_list_is_the_librarys_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _convert(tmp_path, monkeypatch, False, "google_protobuf_Syntax", 3)
    assert result.error.code == -2003
    assert result.proto == 0


def _write_session_pb2(folder: Path) -> None:
    """``session_pb2`` as NI's ni.grpcdevice.v1.proto builds it: a file descriptor
    in the default pool, its enum wrapped in an ``EnumTypeWrapper``."""
    (folder / "session_pb2.py").write_text(
        "from google.protobuf import descriptor_pb2, descriptor_pool\n"
        "from google.protobuf.internal import enum_type_wrapper\n"
        "f = descriptor_pb2.FileDescriptorProto(\n"
        "    name='lvkit_test_session.proto', package='nidevice_grpc',\n"
        "    syntax='proto3')\n"
        "e = f.enum_type.add(name='SessionInitializationBehavior')\n"
        "for n, v in (('SESSION_INITIALIZATION_BEHAVIOR_UNSPECIFIED', 0),\n"
        "             ('SESSION_INITIALIZATION_BEHAVIOR_INITIALIZE_NEW', 1),\n"
        "             ('SESSION_INITIALIZATION_BEHAVIOR_ATTACH_TO_EXISTING', 2)):\n"
        "    e.value.add(name=n, number=v)\n"
        "pool = descriptor_pool.Default()\n"
        "pool.Add(f)\n"
        "SessionInitializationBehavior = enum_type_wrapper.EnumTypeWrapper(\n"
        "    pool.FindEnumTypeByName('nidevice_grpc.SessionInitializationBehavior'))\n",
        encoding="utf-8",
    )


def test_a_nidevice_enum_is_converted_on_the_session_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    _write_session_pb2(modules)
    monkeypatch.syspath_prepend(str(modules))
    name = "nidevice_grpc_SessionInitializationBehavior"
    try:
        assert _convert(tmp_path, monkeypatch, True, name, 2).lv == 2
        assert _convert(tmp_path, monkeypatch, False, name, 1).proto == 1
    finally:
        sys.modules.pop("session_pb2", None)


def _write_sparse_pb2(folder: Path) -> None:
    (folder / "sparse_pb2.py").write_text(
        "from google.protobuf import descriptor_pb2, descriptor_pool\n"
        "from google.protobuf.internal import enum_type_wrapper\n"
        "f = descriptor_pb2.FileDescriptorProto(\n"
        "    name='lvkit_test_sparse.proto', package='lvkit_test', syntax='proto3')\n"
        "e = f.enum_type.add(name='Sparse')\n"
        "e.options.allow_alias = True\n"
        "for n, v in (('ZERO', 0), ('TEN', 10), ('TWENTY', 20), ('TEN_TOO', 10)):\n"
        "    e.value.add(name=n, number=v)\n"
        "pool = descriptor_pool.Default()\n"
        "pool.Add(f)\n"
        "Sparse = enum_type_wrapper.EnumTypeWrapper(\n"
        "    pool.FindEnumTypeByName('lvkit_test.Sparse'))\n",
        encoding="utf-8",
    )


def test_sparse_numbers_map_by_position_and_an_alias_keeps_its_first_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = tmp_path / "modules"
    modules.mkdir()
    _write_sparse_pb2(modules)
    monkeypatch.syspath_prepend(str(modules))
    monkeypatch.setattr(
        "lvkit.codegen.nodes.grpc_enum.find_proto_enum",
        lambda name: ProtoEnum(
            proto_name="lvkit_test.Sparse",
            distribution="lvkit-test",
            module="sparse_pb2",
            enum="Sparse",
        ),
    )
    try:
        assert _convert(tmp_path, monkeypatch, True, "any", 0).lv == 0
        assert _convert(tmp_path, monkeypatch, True, "any", 10).lv == 1
        assert _convert(tmp_path, monkeypatch, True, "any", 20).lv == 2
        assert _convert(tmp_path, monkeypatch, False, "any", 2).proto == 20
        assert _convert(tmp_path, monkeypatch, False, "any", 3).proto == 10
        assert _convert(tmp_path, monkeypatch, True, "any", 5).error.code == -2003
    finally:
        sys.modules.pop("sparse_pb2", None)


def test_an_enum_name_computed_at_run_time_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _convert(
            tmp_path,
            monkeypatch,
            True,
            "google_protobuf_Syntax",
            1,
            gaps=gaps,
            name_is_constant=False,
        )
    assert len(gaps) == 1
    assert "not a constant" in gaps[0].reason


@pytest.mark.parametrize(
    ("name", "lv", "proto"),
    [
        ("google_protobuf_Syntax_Flat", 2, 1),
        ("google_protobuf_Field_Kind_Flat", 18, 17),
        ("google_protobuf_Field_Cardinality_Flat", 3, 2),
    ],
)
def test_a_flat_typedef_name_converts_on_the_enum_of_the_plain_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, lv: int, proto: int
) -> None:
    assert _convert(tmp_path, monkeypatch, True, name, lv).lv == lv
    assert _convert(tmp_path, monkeypatch, False, name, proto).proto == proto
    assert find_proto_enum(name) == find_proto_enum(name.removesuffix("_Flat"))


def test_a_flat_name_of_an_enum_the_table_does_not_list_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _convert(tmp_path, monkeypatch, False, "my_own_proto_Color_Flat", 1, gaps=gaps)
    assert "'my_own_proto_Color_Flat'" in gaps[0].reason


def test_an_enum_the_table_does_not_list_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _convert(tmp_path, monkeypatch, False, "my_own_proto_Color", 1, gaps=gaps)
    assert len(gaps) == 1
    assert "'my_own_proto_Color'" in gaps[0].reason
    assert "proto_enums.json" in gaps[0].reason


def test_a_neighbour_gRPC_library_vi_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [
                "<vilib>",
                "gRPC",
                "LabVIEW gRPC Library",
                "Server API",
                "Message Requests",
                "Complete Call.vi",
            ],
            [(0, "output", ERROR_CLUSTER), (8, "input", ERROR_CLUSTER)],
            {"error_in": (8, ERROR_CLUSTER)},
            {"error": (0, ERROR_CLUSTER)},
            {"error_in": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1
    assert "Complete Call" in gaps[0].name
