"""The gRPC library's message-packing VIs: ``PackToAny`` and ``UnpackFromAny`` are
the message class of a ``_pb2`` module (the generated callers run on the classes
the client tests build), and the two metadata VIs have no Python content."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType, LVTypeKind
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.test_grpc_client import (
    GRPC_ID,
    REQUEST,
    SERVER,
    cluster,
    messages,  # noqa: F401
)
from tests.vilib_caller import STRING, run_catalog_call

pytestmark = pytest.mark.usefixtures("messages")

U8 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt8")
BYTES = LVType(kind=LVTypeKind.ARRAY, element_type=U8, dimensions=1)
ANY = cluster(("Type Url", STRING), ("Bytes", BYTES))
REQUESTS = [*SERVER, "Message Requests"]
PACK = [*REQUESTS, "PackToAny.vim"]
UNPACK = [*REQUESTS, "UnpackFromAny.vim"]
BIG = 2**40


def _pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: Any,
    name: str = "lvk_Request",
    gaps: list[Gap] | None = None,
    name_is_constant: bool = True,
) -> Any:
    inputs: dict[str, Any] = {"data": (9, REQUEST), "grpc_id": (11, GRPC_ID)}
    arguments: dict[str, Any] = {"data": request, "grpc_id": 3}
    constants: dict[int, tuple[str, LVType]] = {}
    if name_is_constant:
        constants[10] = (name, STRING)
    else:
        inputs["name"] = (10, STRING)
        arguments["name"] = name
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        PACK,
        [
            (0, "output", ERROR_CLUSTER),
            (2, "output", ANY),
            (3, "output", GRPC_ID),
            (8, "input", ERROR_CLUSTER),
            (9, "input", REQUEST),
            (10, "input", STRING),
            (11, "input", GRPC_ID),
        ],
        inputs,
        {"packed": (2, ANY), "id_out": (3, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        arguments,
        gaps=gaps,
        constants=constants,
        runtime_not_implemented=gaps is not None,
    )
    return result


def _unpack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    packed: Any,
    name: str = "lvk_Request",
) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        UNPACK,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", REQUEST),
            (3, "output", GRPC_ID),
            (7, "input", ANY),
            (8, "input", ERROR_CLUSTER),
            (9, "input", STRING),
            (10, "input", REQUEST),
            (11, "input", GRPC_ID),
        ],
        {"packed": (7, ANY), "default": (10, REQUEST), "grpc_id": (11, GRPC_ID)},
        {"value": (1, REQUEST), "id_out": (3, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        {"packed": packed, "default": ("", ("", 0)), "grpc_id": 4},
        constants={9: (name, STRING)},
    )
    return result


def test_pack_gives_the_type_url_and_the_serialized_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _pack(tmp_path, monkeypatch, ("hi", ("n", BIG)))
    import lvkit_test_client_pb2 as pb2  # type: ignore[import-not-found]

    expected = pb2.Request(
        text="hi", inner=pb2.Inner(name="n", big=BIG)
    ).SerializeToString()
    assert result.packed == ("/lvk.Request", list(expected))
    assert result.id_out == 3
    assert result.error is None


def test_unpack_reads_the_message_back_into_the_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packed = _pack(tmp_path, monkeypatch, ("hi", ("n", BIG))).packed
    result = _unpack(tmp_path / "unpack", monkeypatch, packed)
    assert result.value == ("hi", ("n", BIG))
    assert result.id_out == 4
    assert result.error is None


def test_unpack_does_not_read_the_type_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    packed = _pack(tmp_path, monkeypatch, ("hi", ("n", 3))).packed
    result = _unpack(tmp_path / "unpack", monkeypatch, ("/some.Other", packed[1]))
    assert result.value == ("hi", ("n", 3))
    assert result.error is None


def test_bytes_that_do_not_parse_are_error_minus_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A length-delimited field of 10 bytes with 1 byte after it.
    result = _unpack(tmp_path, monkeypatch, ("/lvk.Request", [0x0A, 10, 1]))
    assert result.error.code == -2
    assert result.error.message == "Invalid Argument 2"
    assert result.value == ("", ("", 0))


def test_a_message_type_computed_at_run_time_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _pack(
            tmp_path,
            monkeypatch,
            ("hi", ("n", 1)),
            gaps=gaps,
            name_is_constant=False,
        )
    assert len(gaps) == 1
    assert "Message Type is not a constant" in gaps[0].reason


def test_a_message_the_table_does_not_list_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _pack(
            tmp_path,
            monkeypatch,
            ("hi", ("n", 1)),
            name="my_own_proto_Request",
            gaps=gaps,
        )
    assert len(gaps) == 1
    assert "'my_own_proto_Request'" in gaps[0].reason


def test_cluster_to_message_has_empty_metadata_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrays = LVType(kind=LVTypeKind.ARRAY, element_type=ERROR_CLUSTER, dimensions=1)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [
            *SERVER,
            "Cluster to gRPC message",
            "Cluster to gRPC LabVIEW Message.vi",
        ],
        [
            (0, "output", ERROR_CLUSTER),
            (2, "output", arrays),
            (3, "output", arrays),
            (7, "input", STRING),
            (8, "input", ERROR_CLUSTER),
            (11, "input", REQUEST),
        ],
        {"package": (7, STRING), "cluster": (11, REQUEST)},
        {"enums": (2, arrays), "messages": (3, arrays), "error": (0, ERROR_CLUSTER)},
        {"package": "lvk", "cluster": ("", ("", 0))},
    )
    assert result.enums == []
    assert result.messages == []
    assert result.error is None


def test_register_reflection_descriptors_passes_its_id_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    strings = LVType(kind=LVTypeKind.ARRAY, element_type=STRING, dimensions=1)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*SERVER, "Server", "Register Reflection Descriptors.vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (3, "output", GRPC_ID),
            (8, "input", ERROR_CLUSTER),
            (10, "input", strings),
            (11, "input", GRPC_ID),
        ],
        {"descriptors": (10, strings), "grpc_id": (11, GRPC_ID)},
        {"id_out": (3, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        {"descriptors": ["a", "b"], "grpc_id": 7},
    )
    assert result.id_out == 7
    assert result.error is None
