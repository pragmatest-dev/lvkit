"""The gRPC library's server-streaming client VIs write Python on the ``grpcio``
call: Client Begin Server Streaming Call starts it, Client Read From Stream reads
its messages, Client Complete Streaming Call reports its status and Client Cancel
Call cancels it. The generated callers run against the in-process server of
``tests/test_grpc_client.py``; the call ID that one caller returns is the one the
next gets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import grpc
import pytest

from lvkit.codegen.gaps import Gap
from tests import test_grpc_client
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.test_grpc_client import (
    CLIENT_API,
    GRPC_ID,
    REQUEST,
    RESPONSE,
    RESPONSE_DEFAULT,
    cluster,
)
from tests.vilib_caller import BOOL, I32, STRING, run_catalog_call

# The fixtures of the client tests: the message classes and the server.
messages = test_grpc_client.messages
address = test_grpc_client.address

BEGIN = [*CLIENT_API, "Client Begin Server Streaming Call.vim"]
READ = [*CLIENT_API, "Client Read From Stream.vim"]
COMPLETE = [*CLIENT_API, "Client Complete Streaming Call.vi"]
CANCEL = [*CLIENT_API, "Client Cancel Call.vi"]
ANY = cluster(("Type Url", STRING))


def _begin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    channel: Any,
    method: str,
    count: int = 0,
    gaps: list[Gap] | None = None,
    method_is_constant: bool = True,
    context_id: Any = None,
) -> Any:
    pane = [
        (0, "output", ERROR_CLUSTER),
        (2, "output", GRPC_ID),
        (3, "output", GRPC_ID),
        (4, "input", GRPC_ID),
        (5, "input", STRING),
        (6, "input", I32),
        (7, "input", STRING),
        (8, "input", ERROR_CLUSTER),
        (9, "input", REQUEST),
        (10, "input", STRING),
        (11, "input", GRPC_ID),
    ]
    inputs: dict[str, Any] = {
        "client": (11, GRPC_ID),
        "request": (9, REQUEST),
    }
    arguments: dict[str, Any] = {
        "client": channel,
        "request": ("", ("", count)),
    }
    constants: dict[int, tuple[str, Any]] = {
        5: ("lvk_Response", STRING),
        7: ("lvk_Request", STRING),
    }
    if method_is_constant:
        constants[10] = (method, STRING)
    else:
        inputs["method"] = (10, STRING)
        arguments["method"] = method
    if context_id is not None:
        inputs["context"] = (4, GRPC_ID)
        arguments["context"] = context_id
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        BEGIN,
        pane,
        inputs,
        {
            "call": (2, GRPC_ID),
            "client_out": (3, GRPC_ID),
            "error": (0, ERROR_CLUSTER),
        },
        arguments,
        gaps=gaps,
        constants=constants,
        runtime_not_implemented=gaps is not None or context_id is not None,
    )
    return result


def _read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, call: Any) -> Any:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        READ,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", BOOL),
            (2, "output", RESPONSE),
            (3, "output", GRPC_ID),
            (7, "input", RESPONSE),
            (8, "input", ERROR_CLUSTER),
            (11, "input", GRPC_ID),
        ],
        {"call": (11, GRPC_ID), "response_type": (7, RESPONSE)},
        {
            "success": (1, BOOL),
            "response": (2, RESPONSE),
            "call_out": (3, GRPC_ID),
            "error": (0, ERROR_CLUSTER),
        },
        {"call": call, "response_type": RESPONSE_DEFAULT},
    )
    return result


def _complete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    call: Any,
    cancel: bool | None = None,
) -> Any:
    inputs: dict[str, Any] = {"call": (11, GRPC_ID)}
    arguments: dict[str, Any] = {"call": call}
    if cancel is not None:
        inputs["cancel"] = (10, BOOL)
        arguments["cancel"] = cancel
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        COMPLETE,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", ANY),
            (8, "input", ERROR_CLUSTER),
            (10, "input", BOOL),
            (11, "input", GRPC_ID),
        ],
        inputs,
        {"error": (0, ERROR_CLUSTER)},
        arguments,
    )
    return result


def test_a_stream_is_read_message_by_message_to_its_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    begun = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Count", count=3)
    assert begun.client_out is channel
    assert begun.error is None
    read = [_read(tmp_path, monkeypatch, begun.call) for _ in range(4)]
    assert [r.success for r in read] == [True, True, True, False]
    assert [r.response[:2] for r in read[:3]] == [("#1", 1), ("#2", 2), ("#3", 3)]
    # No message was read: Response Data is the Response Type.
    assert read[3].response == RESPONSE_DEFAULT
    assert all(r.call_out is begun.call and r.error is None for r in read)
    done = _complete(tmp_path, monkeypatch, begun.call)
    assert done.error is None
    channel.close()


def test_an_empty_stream_ends_at_the_first_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    begun = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Count", count=0)
    assert _read(tmp_path, monkeypatch, begun.call).success is False
    assert _complete(tmp_path, monkeypatch, begun.call).error is None
    channel.close()


def test_a_stream_that_fails_ends_the_reads_and_complete_reports_the_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    begun = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Broken")
    first = _read(tmp_path, monkeypatch, begun.call)
    assert (first.success, first.response[0]) == (True, "first")
    second = _read(tmp_path, monkeypatch, begun.call)
    assert second.success is False
    assert second.error is None
    done = _complete(tmp_path, monkeypatch, begun.call)
    # -(1000 + the gRPC status code): NOT_FOUND is 5.
    assert done.error.code == -1005
    assert done.error.message == "stream broke"
    channel.close()


def test_cancel_call_ends_an_endless_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    begun = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Endless")
    assert _read(tmp_path, monkeypatch, begun.call).response[0] == "tick"
    _, cancelled = run_catalog_call(
        tmp_path,
        monkeypatch,
        CANCEL,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", ANY),
            (3, "output", GRPC_ID),
            (8, "input", ERROR_CLUSTER),
            (11, "input", GRPC_ID),
        ],
        {"call": (11, GRPC_ID)},
        {"call_out": (3, GRPC_ID), "details": (1, ANY), "error": (0, ERROR_CLUSTER)},
        {"call": begun.call},
    )
    assert cancelled.error is None
    assert cancelled.call_out is begun.call
    assert begun.call.cancelled()
    assert begun.call.code() == grpc.StatusCode.CANCELLED
    assert _read(tmp_path, monkeypatch, begun.call).success is False
    channel.close()


def test_complete_with_cancel_all_reads_cancels_instead_of_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    begun = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Endless")
    assert _complete(tmp_path, monkeypatch, begun.call, cancel=True).error is None
    assert begun.call.cancelled()
    other = _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Broken")
    # Cancel All Reads False waits for the status like an unwired one; the status
    # reaches the client once its messages are read (the library's Finish too).
    while _read(tmp_path, monkeypatch, other.call).success:
        pass
    assert (
        _complete(tmp_path, monkeypatch, other.call, cancel=False).error.code == -1005
    )
    channel.close()


def test_a_context_that_is_not_the_default_is_refused_when_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, address: str
) -> None:
    channel = grpc.insecure_channel(address)
    with pytest.raises(NotImplementedError, match=r"client context \(contextId 5\)"):
        _begin(tmp_path, monkeypatch, channel, "/lvk.Echo/Count", context_id=5)
    channel.close()


def test_a_method_name_computed_at_run_time_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, messages: Any
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        _begin(
            tmp_path,
            monkeypatch,
            None,
            "/lvk.Echo/Count",
            gaps=gaps,
            method_is_constant=False,
        )
    assert len(gaps) == 1
    assert "method name is not a constant" in gaps[0].reason
    assert not gaps[0].reason.startswith("Client Begin")


def test_a_neighbour_client_streaming_vi_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            [*CLIENT_API, "Client Write To Stream.vim"],
            [(0, "output", ERROR_CLUSTER), (8, "input", ERROR_CLUSTER)],
            {"error_in": (8, ERROR_CLUSTER)},
            {"error": (0, ERROR_CLUSTER)},
            {"error_in": None},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1
    assert "Write To Stream" in gaps[0].name
