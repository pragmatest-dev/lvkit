"""The gRPC library's client VIs write Python on ``grpcio`` and ``protobuf``:
Create Client is a channel, Client Unary Call a ``unary_unary`` call on the
message classes of a ``_pb2`` module, and the metadata registration VIs pass
their id on. The generated callers are run against an in-process server on
localhost, with message classes built the way NI's ``_pb2`` packages build them
(a file descriptor in the default pool)."""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from concurrent import futures
from pathlib import Path
from types import ModuleType
from typing import Any

import grpc
import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.proto_messages import ProtoMessage, find_proto_message
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.vilib_caller import BOOL, DBL, I32, STRING, run_catalog_call

GRPC_ID = I32
I64 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt64")
CLIENT_API = ["<vilib>", "gRPC", "LabVIEW gRPC Library", "Client API"]
SERVER = ["<vilib>", "gRPC", "LabVIEW gRPC Library", "Server API"]
CREATE = [*CLIENT_API, "Create Client.vi"]
CLOSE = [*CLIENT_API, "Close Client.vi"]
UNARY = [*CLIENT_API, "Client Unary Call.vim"]


def cluster(*fields: tuple[str, LVType]) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        fields=[ClusterField(name=n, type=t) for n, t in fields],
    )


INNER = cluster(("name", STRING), ("big", I64))
REQUEST = cluster(("text", STRING), ("inner", INNER))
RESPONSE = cluster(("echo", STRING), ("total", I64), ("inner", INNER), ("ratio", DBL))
RESPONSE_DEFAULT = ("", 0, ("", 0), 0.0)

_PB2 = """\
from google.protobuf import descriptor_pb2, descriptor_pool, message_factory

f = descriptor_pb2.FileDescriptorProto(
    name='lvkit_test_client.proto', package='lvk', syntax='proto3')
inner = f.message_type.add(name='Inner')
inner.field.add(name='name', number=1, type=9, label=1)
inner.field.add(name='big', number=2, type=3, label=1)
request = f.message_type.add(name='Request')
request.field.add(name='text', number=1, type=9, label=1)
request.field.add(name='inner', number=2, type=11, label=1,
                  type_name='.lvk.Inner')
response = f.message_type.add(name='Response')
response.field.add(name='echo', number=1, type=9, label=1)
response.field.add(name='total', number=2, type=3, label=1)
response.field.add(name='inner', number=3, type=11, label=1,
                   type_name='.lvk.Inner')
response.field.add(name='ratio', number=4, type=1, label=1)
pool = descriptor_pool.Default()
pool.Add(f)
Inner = message_factory.GetMessageClass(pool.FindMessageTypeByName('lvk.Inner'))
Request = message_factory.GetMessageClass(pool.FindMessageTypeByName('lvk.Request'))
Response = message_factory.GetMessageClass(
    pool.FindMessageTypeByName('lvk.Response'))
"""


@pytest.fixture
def messages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    """The ``lvkit_test_client_pb2`` module, listed in the table of messages."""
    modules = tmp_path / "modules"
    modules.mkdir()
    (modules / "lvkit_test_client_pb2.py").write_text(_PB2, encoding="utf-8")
    monkeypatch.syspath_prepend(str(modules))
    rows = {
        f"lvk_{name}": ProtoMessage(
            proto_name=f"lvk.{name}",
            distribution="lvkit-test",
            module="lvkit_test_client_pb2",
            message=name,
        )
        for name in ("Request", "Response", "Inner")
    }
    monkeypatch.setattr(
        "lvkit.codegen.nodes.grpc_client.find_proto_message",
        lambda name: rows.get(name) or find_proto_message(name),
    )
    import lvkit_test_client_pb2  # type: ignore[import-not-found]

    yield lvkit_test_client_pb2
    sys.modules.pop("lvkit_test_client_pb2", None)


@pytest.fixture
def address(messages: Any) -> Iterator[str]:
    """A localhost server with the methods ``/lvk.Echo/Call``, ``Fail`` and
    ``Slow``."""

    def call(request: Any, context: grpc.ServicerContext) -> Any:
        return messages.Response(
            echo=request.text.upper(),
            total=request.inner.big * 2,
            inner=request.inner,
            ratio=0.5,
        )

    def fail(request: Any, context: grpc.ServicerContext) -> Any:
        context.abort(grpc.StatusCode.NOT_FOUND, "no such thing")

    def slow(request: Any, context: grpc.ServicerContext) -> Any:
        time.sleep(1.0)
        return messages.Response(echo="late")

    def handler(function: Any) -> grpc.RpcMethodHandler:
        return grpc.unary_unary_rpc_method_handler(
            function,
            request_deserializer=messages.Request.FromString,
            response_serializer=messages.Response.SerializeToString,
        )

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=4))
    server.add_generic_rpc_handlers(
        (
            grpc.method_handlers_generic_handler(
                "lvk.Echo",
                {"Call": handler(call), "Fail": handler(fail), "Slow": handler(slow)},
            ),
        )
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    yield f"127.0.0.1:{port}"
    server.stop(0)


def _unary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channel: Any,
    method: str,
    request: Any = ("hello", ("n", 3)),
    timeout: int | None = None,
    gaps: list[Gap] | None = None,
    constants: dict[int, tuple[str, LVType]] | None = None,
    context_id: Any = None,
    method_is_constant: bool = True,
) -> Any:
    pane = [
        (0, "input", GRPC_ID),
        (1, "input", RESPONSE),
        (3, "input", I32),
        (4, "output", GRPC_ID),
        (5, "input", STRING),
        (6, "output", RESPONSE),
        (7, "input", GRPC_ID),
        (9, "input", REQUEST),
        (11, "input", ERROR_CLUSTER),
        (12, "input", STRING),
        (14, "input", STRING),
        (15, "output", ERROR_CLUSTER),
    ]
    inputs: dict[str, Any] = {
        "client": (0, GRPC_ID),
        "response_type": (1, RESPONSE),
        "request": (9, REQUEST),
    }
    arguments: dict[str, Any] = {
        "client": channel,
        "response_type": RESPONSE_DEFAULT,
        "request": request,
    }
    if timeout is not None:
        inputs["timeout"] = (3, I32)
        arguments["timeout"] = timeout
    if context_id is not None:
        inputs["context"] = (7, GRPC_ID)
        arguments["context"] = context_id
    fixed: dict[int, tuple[str, LVType]] = {
        12: ("lvk_Request", STRING),
        14: ("lvk_Response", STRING),
    }
    if method_is_constant:
        fixed[5] = (method, STRING)
    else:
        inputs["method"] = (5, STRING)
        arguments["method"] = method
    fixed.update(constants or {})
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        UNARY,
        pane,
        inputs,
        {
            "id_out": (4, GRPC_ID),
            "response": (6, RESPONSE),
            "error": (15, ERROR_CLUSTER),
        },
        arguments,
        gaps=gaps,
        constants=fixed,
        runtime_not_implemented=gaps is not None or context_id is not None,
    )
    return result


def test_a_unary_call_sends_the_nested_request_and_builds_the_response_cluster(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    big = 2**40
    result = _unary(
        tmp_path, monkeypatch, channel, "/lvk.Echo/Call", ("hello", ("n", big))
    )
    assert result.response == ("HELLO", big * 2, ("n", big), 0.5)
    assert result.id_out is channel
    assert result.error is None
    channel.close()


def test_a_field_the_response_leaves_out_keeps_the_types_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    result = _unary(tmp_path, monkeypatch, channel, "/lvk.Echo/Call", ("", ("", 0)))
    assert result.response == ("", 0, ("", 0), 0.5)
    channel.close()


def test_a_call_the_server_aborts_is_the_error_out_with_its_details(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    result = _unary(tmp_path, monkeypatch, channel, "/lvk.Echo/Fail")
    # -(1000 + the gRPC status code): NOT_FOUND is 5.
    assert result.error.code == -1005
    assert result.error.message == "no such thing"
    assert result.response == RESPONSE_DEFAULT
    channel.close()


def test_a_timeout_above_zero_is_the_deadline_and_below_it_there_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    late = _unary(tmp_path, monkeypatch, channel, "/lvk.Echo/Slow", timeout=100)
    # DEADLINE_EXCEEDED is 4.
    assert late.error.code == -1004
    for no_deadline in (-1, 0):
        done = _unary(
            tmp_path, monkeypatch, channel, "/lvk.Echo/Slow", timeout=no_deadline
        )
        assert done.error is None
        assert done.response[0] == "late"
    channel.close()


def test_a_client_context_that_is_not_the_default_is_refused_when_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    ok = _unary(tmp_path, monkeypatch, channel, "/lvk.Echo/Call", context_id=0)
    assert ok.response[0] == "HELLO"
    with pytest.raises(NotImplementedError, match=r"client context \(contextId 5\)"):
        _unary(tmp_path, monkeypatch, channel, "/lvk.Echo/Call", context_id=5)
    channel.close()


def test_a_method_name_computed_at_run_time_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: Any
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _unary(
            tmp_path,
            monkeypatch,
            None,
            "/lvk.Echo/Call",
            gaps=gaps,
            method_is_constant=False,
        )
    assert len(gaps) == 1
    assert "method name is not a constant" in gaps[0].reason


def test_a_message_the_table_does_not_list_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: Any
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _unary(
            tmp_path,
            monkeypatch,
            None,
            "/lvk.Echo/Call",
            gaps=gaps,
            constants={12: ("my_own_proto_Request", STRING)},
        )
    assert len(gaps) == 1
    assert "'my_own_proto_Request'" in gaps[0].reason
    assert "grpcio-tools" in gaps[0].reason


def test_create_client_is_a_channel_to_the_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    pane = [
        (0, "output", ERROR_CLUSTER),
        (3, "output", GRPC_ID),
        (7, "input", BOOL),
        (8, "input", ERROR_CLUSTER),
        (10, "input", STRING),
        (11, "input", STRING),
    ]
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        CREATE,
        pane,
        {"address": (11, STRING), "certificate": (10, STRING)},
        {"client": (3, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        {"address": address, "certificate": ""},
    )
    assert isinstance(result.client, grpc.Channel)
    grpc.channel_ready_future(result.client).result(timeout=5)
    assert result.error is None
    result.client.close()


def test_a_certificate_file_that_is_not_there_is_the_error_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pane = [
        (0, "output", ERROR_CLUSTER),
        (3, "output", GRPC_ID),
        (10, "input", STRING),
        (11, "input", STRING),
    ]
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        CREATE,
        pane,
        {"address": (11, STRING), "certificate": (10, STRING)},
        {"client": (3, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        {"address": "localhost:1", "certificate": str(tmp_path / "missing.pem")},
    )
    assert result.error.code == 42
    assert "missing.pem" in result.error.message
    assert result.client == 0


def test_close_client_closes_the_channel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        CLOSE,
        [
            (0, "output", ERROR_CLUSTER),
            (8, "input", ERROR_CLUSTER),
            (11, "input", GRPC_ID),
        ],
        {"client": (11, GRPC_ID)},
        {"error": (0, ERROR_CLUSTER)},
        {"client": channel},
    )
    assert result.error is None
    with pytest.raises(ValueError, match="closed channel"):
        channel.unary_unary("/lvk.Echo/Call")(b"")


@pytest.mark.parametrize(
    ("recorded", "id_in", "id_out"),
    [
        ("Server/Register Message Metadata.vi", 11, 3),
        ("Server/Register Enum Metadata.vi", 11, 3),
        ("CompleteMetadataRegistration.vi", 11, 3),
    ],
)
def test_the_metadata_registration_vis_pass_their_id_on(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: str,
    id_in: int,
    id_out: int,
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*SERVER, *recorded.split("/")],
        [
            (0, "output", ERROR_CLUSTER),
            (id_out, "output", GRPC_ID),
            (8, "input", ERROR_CLUSTER),
            (id_in, "input", GRPC_ID),
        ],
        {"grpc_id": (id_in, GRPC_ID)},
        {"id_out": (id_out, GRPC_ID), "error": (0, ERROR_CLUSTER)},
        {"grpc_id": 7},
    )
    assert result.id_out == 7
    assert result.error is None


def test_a_neighbour_server_vi_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*SERVER, "Message Requests", "Complete Call.vi"],
            [(0, "output", ERROR_CLUSTER), (8, "input", ERROR_CLUSTER)],
            {"error_in": (8, ERROR_CLUSTER)},
            {"error": (0, ERROR_CLUSTER)},
            {"error_in": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1
    assert "Complete Call" in gaps[0].name
