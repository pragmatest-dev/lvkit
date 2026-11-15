"""The gRPC library's server VIs write Python on the package's ``grpc_server``
module: Create Server, Start Server, Stop Server and Create And Register Server
Event make and run the server; Read Call Request, Write Call Response, Set Call
Status, Is Call Cancelled and Complete Call work on a call.

Every VI runs as generated code (``run_catalog_call``) against a REAL grpcio client
on localhost. The Event Structure that handles a call is played by the test: it
holds a queue the registered user event delivers the call to, as a registration of
the event does, and hands the call in the event's data to the generated VIs."""

from __future__ import annotations

import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent import futures
from pathlib import Path
from typing import Any

import grpc
import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import LVType, LVTypeKind
from tests import test_grpc_client
from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER
from tests.test_grpc_client import REQUEST, RESPONSE, cluster
from tests.vilib_caller import BOOL, I32, PATH, STRING, run_catalog_call

messages = test_grpc_client.messages

U64 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt64")
CALL_CLUSTER = cluster(("gRPCid", U64))
USER_EVENT = LVType(
    kind=LVTypeKind.PRIMITIVE,
    underlying_type="Refnum",
    ref_type="UserEvent",
    element_type=CALL_CLUSTER,
)
STATUS_CODE = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumInt32")
REQUEST_DEFAULT = ("", ("", 0))
RESPONSE_DEFAULT = ("", 0, ("", 0), 0.0)

SERVER_API = ["<vilib>", "gRPC", "LabVIEW gRPC Library", "Server API"]
CREATE = [*SERVER_API, "Server", "Create Server.vi"]
START = [*SERVER_API, "Server", "Start Server.vi"]
STOP = [*SERVER_API, "Server", "Stop Server.vi"]
REGISTER = [*SERVER_API, "Server", "Create And Register Server Event.vim"]
READ = [*SERVER_API, "Message Requests", "Read Call Request.vim"]
WRITE = [*SERVER_API, "Message Requests", "Write Call Response.vim"]
COMPLETE = [*SERVER_API, "Message Requests", "Complete Call.vi"]
CANCELLED = [*SERVER_API, "Message Requests", "Is Call Cancelled.vi"]
STATUS = [*SERVER_API, "Set Call Status.vi"]
ECHO = "/lvk.Echo/Call"


class Harness:
    """The generated VIs of one test, run in its package folder."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.tmp_path = tmp_path
        self.monkeypatch = monkeypatch
        # The caller module is rewritten for each VI, and may keep its size.
        monkeypatch.setattr(sys, "dont_write_bytecode", True)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return run_catalog_call(self.tmp_path, self.monkeypatch, *args, **kwargs)[1]

    def create(self) -> Any:
        return self._run(
            CREATE,
            [(0, "output", ERROR_CLUSTER), (3, "output", U64)],
            {},
            {"server": (3, U64), "error": (0, ERROR_CLUSTER)},
            {},
        ).server

    def start(
        self,
        server: Any,
        address: str = "127.0.0.1:0",
        certificate: Path | None = None,
    ) -> Any:
        pane = [
            (0, "output", ERROR_CLUSTER),
            (2, "output", I32),
            (3, "output", U64),
            (5, "input", PATH),
            (7, "input", PATH),
            (10, "input", U64),
            (11, "input", STRING),
        ]
        inputs: dict[str, Any] = {"server": (10, U64), "address": (11, STRING)}
        arguments: dict[str, Any] = {"server": server, "address": address}
        if certificate is not None:
            inputs["certificate"] = (7, PATH)
            arguments["certificate"] = certificate
        return self._run(
            START,
            pane,
            inputs,
            {
                "port": (2, I32),
                "server_out": (3, U64),
                "error": (0, ERROR_CLUSTER),
            },
            arguments,
        )

    def stop(self, server: Any) -> Any:
        return self._run(
            STOP,
            [(0, "output", ERROR_CLUSTER), (11, "input", U64)],
            {"server": (11, U64)},
            {"error": (0, ERROR_CLUSTER)},
            {"server": server},
        )

    def register(
        self,
        server: Any,
        method: str = ECHO,
        request: str = "lvk_Request",
        response: str = "lvk_Response",
        gaps: list[Gap] | None = None,
    ) -> Any:
        return self._run(
            REGISTER,
            [
                (0, "output", ERROR_CLUSTER),
                (2, "output", USER_EVENT),
                (3, "output", U64),
                (7, "input", STRING),
                (8, "input", STRING),
                (9, "input", STRING),
                (10, "input", CALL_CLUSTER),
                (11, "input", U64),
            ],
            {"server": (11, U64)},
            {
                "event": (2, USER_EVENT),
                "server_out": (3, U64),
                "error": (0, ERROR_CLUSTER),
            },
            {"server": server},
            constants={
                7: (method, STRING),
                8: (response, STRING),
                9: (request, STRING),
            },
            gaps=gaps,
            runtime_not_implemented=gaps is not None,
        )

    def read(self, call: Any) -> Any:
        return self._run(
            READ,
            [
                (0, "output", ERROR_CLUSTER),
                (2, "output", REQUEST),
                (3, "output", U64),
                (10, "input", REQUEST),
                (11, "input", U64),
            ],
            {"call": (11, U64), "request_type": (10, REQUEST)},
            {
                "request": (2, REQUEST),
                "call_out": (3, U64),
                "error": (0, ERROR_CLUSTER),
            },
            {"call": call, "request_type": REQUEST_DEFAULT},
        )

    def write(self, call: Any, response: Any) -> Any:
        return self._run(
            WRITE,
            [
                (0, "output", ERROR_CLUSTER),
                (3, "output", U64),
                (10, "input", RESPONSE),
                (11, "input", U64),
            ],
            {"call": (11, U64), "response": (10, RESPONSE)},
            {"call_out": (3, U64), "error": (0, ERROR_CLUSTER)},
            {"call": call, "response": response},
        )

    def complete(self, call: Any) -> Any:
        return self._run(
            COMPLETE,
            [
                (0, "input", U64),
                (10, "output", ERROR_CLUSTER),
            ],
            {"call": (0, U64)},
            {"error": (10, ERROR_CLUSTER)},
            {"call": call},
        )

    def set_status(self, call: Any, code: int, details: str) -> Any:
        return self._run(
            STATUS,
            [
                (0, "output", ERROR_CLUSTER),
                (3, "output", U64),
                (9, "input", STRING),
                (10, "input", STATUS_CODE),
                (11, "input", U64),
            ],
            {
                "call": (11, U64),
                "code": (10, STATUS_CODE),
                "details": (9, STRING),
            },
            {"call_out": (3, U64), "error": (0, ERROR_CLUSTER)},
            {"call": call, "code": code, "details": details},
        )

    def cancelled(self, call: Any) -> Any:
        return self._run(
            CANCELLED,
            [
                (0, "output", ERROR_CLUSTER),
                (2, "output", BOOL),
                (3, "output", U64),
                (11, "input", U64),
            ],
            {"call": (11, U64)},
            {
                "cancelled": (2, BOOL),
                "call_out": (3, U64),
                "error": (0, ERROR_CLUSTER),
            },
            {"call": call},
        )


class Running:
    """A started server with ``/lvk.Echo/Call`` registered, its client channel,
    and the queue the user event delivers the calls to."""

    def __init__(self, harness: Harness, messages: Any, method: str = ECHO) -> None:
        self.harness = harness
        self.messages = messages
        self.server = harness.create()
        started = harness.start(self.server)
        assert started.error is None
        assert started.server_out is self.server
        assert started.port > 0
        self.event = harness.register(self.server, method).event
        assert self.event == []
        self.arrivals: queue.Queue[Any] = queue.Queue()
        self.event.append(self.arrivals)
        self.channel = grpc.insecure_channel(f"127.0.0.1:{started.port}")
        self.pool = futures.ThreadPoolExecutor(max_workers=4)

    def unary(self, method: str = ECHO, text: str = "hello", big: int = 3) -> Any:
        stub = self.channel.unary_unary(
            method,
            request_serializer=self.messages.Request.SerializeToString,
            response_deserializer=self.messages.Response.FromString,
        )
        request = self.messages.Request(
            text=text, inner=self.messages.Inner(name="n", big=big)
        )
        return self.pool.submit(stub, request, timeout=10)

    def streaming(self, text: str = "hello") -> Any:
        stub = self.channel.unary_stream(
            ECHO,
            request_serializer=self.messages.Request.SerializeToString,
            response_deserializer=self.messages.Response.FromString,
        )
        return stub(self.messages.Request(text=text), timeout=10)

    def arrival(self) -> Any:
        """The call the user event delivered: the id in the event's data."""
        event, data, kind, old = self.arrivals.get(timeout=10)
        assert event is self.event
        assert (kind, old) == ("User Event", None)
        (call,) = data
        return call

    def close(self) -> None:
        self.channel.close()
        self.pool.shutdown(wait=False)
        self.harness.stop(self.server)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(tmp_path, monkeypatch)


@pytest.fixture
def running(harness: Harness, messages: Any) -> Iterator[Running]:
    state = Running(harness, messages)
    yield state
    state.close()


def _response(text: str, total: int = 0) -> Any:
    return (text, total, ("", 0), 0.0)


def _until(condition: Callable[[], bool], seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_a_unary_call_is_read_answered_and_completed(running: Running) -> None:
    pending = running.unary(text="hello", big=3)
    call = running.arrival()
    read = running.harness.read(call)
    assert read.error is None
    assert read.call_out is call
    assert read.request == ("hello", ("n", 3))
    answered = running.harness.write(call, _response("HELLO", 6))
    assert answered.error is None
    assert answered.call_out is call
    assert running.harness.complete(call).error is None
    reply = pending.result(timeout=10)
    assert (reply.echo, reply.total) == ("HELLO", 6)


def test_a_second_read_of_a_unary_call_is_error_minus_2(running: Running) -> None:
    pending = running.unary()
    call = running.arrival()
    assert running.harness.read(call).error is None
    again = running.harness.read(call)
    # The request stream is over, the call is neither cancelled nor given a
    # status: the library's code for that is -2, with its text for the code.
    assert again.error.code == -2
    assert again.error.message == "Invalid Argument 2"
    assert again.request == REQUEST_DEFAULT
    running.harness.write(call, _response("x"))
    running.harness.complete(call)
    pending.result(timeout=10)


def test_a_server_streaming_call_gets_every_response_written(
    running: Running,
) -> None:
    stream = running.streaming()
    call = running.arrival()
    assert running.harness.read(call).request[0] == "hello"
    for number in (1, 2, 3):
        assert (
            running.harness.write(call, _response(f"#{number}", number)).error is None
        )
    assert running.harness.complete(call).error is None
    assert [(r.echo, r.total) for r in stream] == [("#1", 1), ("#2", 2), ("#3", 3)]


def test_complete_call_is_idempotent_and_a_write_after_it_is_error_minus_2(
    running: Running,
) -> None:
    pending = running.unary()
    call = running.arrival()
    running.harness.write(call, _response("one", 1))
    assert running.harness.complete(call).error is None
    assert running.harness.complete(call).error is None
    late = running.harness.write(call, _response("two", 2))
    assert late.error.code == -2
    assert pending.result(timeout=10).echo == "one"


def test_a_call_status_ends_the_call_and_fails_later_writes_with_its_code(
    running: Running,
) -> None:
    pending = running.unary()
    call = running.arrival()
    assert running.harness.set_status(call, 5, "nothing here").call_out is call
    # The status alone does not end the call: a write still goes through until
    # Complete Call, then a write is -(1000 + NOT_FOUND), with the library's text.
    assert running.harness.write(call, _response("x")).error is None
    assert running.harness.complete(call).error is None
    failed = running.harness.write(call, _response("y"))
    assert failed.error.code == -1005
    assert failed.error.message == "Requested entity was not found"
    with pytest.raises(grpc.RpcError) as raised:
        pending.result(timeout=10)
    assert raised.value.code() == grpc.StatusCode.NOT_FOUND
    assert raised.value.details() == "nothing here"


def test_an_ok_call_status_with_details_is_not_an_error(running: Running) -> None:
    pending = running.unary()
    call = running.arrival()
    running.harness.write(call, _response("fine"))
    running.harness.set_status(call, 0, "all good")
    assert running.harness.complete(call).error is None
    assert pending.result(timeout=10).echo == "fine"


def test_a_client_that_cancels_makes_the_call_cancelled(running: Running) -> None:
    stream = running.streaming()
    call = running.arrival()
    assert running.harness.cancelled(call).cancelled is False
    stream.cancel()
    assert _until(lambda: running.harness.cancelled(call).cancelled is True)
    written = running.harness.write(call, _response("late"))
    assert written.error.code == -1001
    assert written.error.message == "The operation was cancelled"
    read = running.harness.read(call)
    assert read.error.code == -1001
    # Completing a call that is over does nothing.
    assert running.harness.complete(call).error is None


def test_a_method_registered_twice_keeps_the_first_registration(
    running: Running,
) -> None:
    second = running.harness.register(running.server).event
    other: queue.Queue[Any] = queue.Queue()
    second.append(other)
    pending = running.unary()
    call = running.arrival()
    running.harness.write(call, _response("first"))
    running.harness.complete(call)
    assert pending.result(timeout=10).echo == "first"
    assert other.empty()


def test_the_same_call_goes_to_every_queue_of_the_event(running: Running) -> None:
    other: queue.Queue[Any] = queue.Queue()
    running.event.append(other)
    pending = running.unary()
    first = running.arrival()
    second = other.get(timeout=10)[1][0]
    assert second is first
    running.harness.write(first, _response("shared"))
    running.harness.complete(first)
    assert pending.result(timeout=10).echo == "shared"


def test_a_method_nothing_registered_is_unimplemented(running: Running) -> None:
    pending = running.unary(method="/lvk.Echo/Nothing")
    with pytest.raises(grpc.RpcError) as raised:
        pending.result(timeout=10)
    assert raised.value.code() == grpc.StatusCode.UNIMPLEMENTED
    assert running.arrivals.empty()


def test_a_method_registered_after_start_is_served(running: Running) -> None:
    late = running.harness.register(running.server, method="/lvk.Echo/Late").event
    arrivals: queue.Queue[Any] = queue.Queue()
    late.append(arrivals)
    pending = running.unary(method="/lvk.Echo/Late")
    (call,) = arrivals.get(timeout=10)[1]
    running.harness.write(call, _response("late"))
    running.harness.complete(call)
    assert pending.result(timeout=10).echo == "late"


def test_a_call_never_completed_ends_with_stop_server(
    harness: Harness, messages: Any
) -> None:
    running = Running(harness, messages)
    before = threading.active_count()
    pending = running.unary()
    call = running.arrival()
    assert harness.cancelled(call).cancelled is False
    with futures.ThreadPoolExecutor(max_workers=1) as pool:
        stopped = pool.submit(harness.stop, running.server)
        assert stopped.result(timeout=15).error is None
    with pytest.raises(grpc.RpcError):
        pending.result(timeout=15)
    assert harness.cancelled(call).cancelled is True
    # The call's handler thread ends: no thread is left waiting for the call.
    assert _until(lambda: threading.active_count() <= before)
    running.channel.close()
    running.pool.shutdown(wait=False)


def test_stop_server_on_a_server_that_never_started_does_nothing(
    harness: Harness,
) -> None:
    assert harness.stop(harness.create()).error is None


def test_a_server_that_cannot_start_is_error_minus_8001(harness: Harness) -> None:
    started = harness.start(harness.create(), address="no such host:not a port")
    assert started.error.code == -8001
    assert started.error.message == (
        "Failed to start the gRPC server.  Ensure the specified port is not in use."
    )
    assert started.port == 0


def test_a_certificate_that_cannot_be_read_is_error_minus_8001(
    harness: Harness, tmp_path: Path
) -> None:
    started = harness.start(harness.create(), certificate=tmp_path / "missing.crt")
    assert started.error.code == -8001


def test_a_message_the_table_does_not_list_is_a_gap(
    harness: Harness, messages: Any
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        harness.register(harness.create(), request="lvk_Unlisted", gaps=gaps)
    assert len(gaps) == 1
    assert "lvk_Unlisted" in gaps[0].reason
