"""The module a generated package holds for the server of LabVIEW's gRPC library.

The library's server (``grpc-labview``, ``src/grpc_server.cc``, ``event_data.cc``,
``grpc_interop.cc``) has no Python equivalent in grpcio's own API: a call arrives
as a user event carrying a gRPC id, and the VI that handles the event reads the
request, writes the responses and completes the call by that id. This module
holds the two objects that keep that structure on grpcio, written once into the
package root as ``grpc_server.py`` when a generated module imports it, so a
generated package imports nothing from lvkit. It needs ``grpcio`` and nothing
else.

``Server`` is the server's gRPC id and ``Call`` is a call's. The rules, each from
the library's C++ (file:line of the ``grpc-labview`` sources the module follows):

- A method is registered with its user event, the wrap that makes the event's data
  from the call, and its request and response message classes; the first
  registration of a name wins, as ``std::map::insert`` keeps the first
  (``grpc_server.cc:37``).
- One ``grpc.GenericRpcHandler`` serves every method and finds the method in the
  table for each call, so a method registered after ``start`` is served
  (``grpc_server.cc:50-64``, ``event_data.cc:160-162``). A method nothing
  registered ends with UNIMPLEMENTED and no details (``event_data.cc:174-177``).
- The call's user event is fired with the same ``Call`` for every queue that holds
  it (``grpc_server.cc:61``, ``grpc_interop.cc:20-23``).
- Every method is a stream-stream handler: Read Call Request is the next request
  message, so a unary and a client-streaming method are read by the same rule
  (``event_data.cc:83-104``). A read that gets none, or a read or a write on a
  call that is finished or cancelled, fails with the code ``-(1000 + the call's
  status)`` when a status was set, ``-1001`` when the call was cancelled, else
  ``-2`` (``grpc_interop.cc:364-376``).
- Write Call Response sends one response message (``event_data.cc:107-127``).
- Set Call Status stores the status the call ends with (``event_data.cc:55-58``);
  Complete Call ends the call with it, and ends nothing twice
  (``event_data.cc:199-212``). A status that is not OK ends the call as the RPC's
  status.
- A call that is cancelled, times out or is stopped with the server is cancelled
  (``event_data.cc:216-232``, ``Is Cancelled`` is ``IsCancelled``,
  ``grpc_interop.cc:467-479``) and its handler thread ends at once.
- ``start`` listens on ``address``, ``0.0.0.0:50051`` when it is empty, with TLS
  from the certificate and key files when the certificate path is longer than one
  character, and no limit on a message's size; it returns the port it listens on
  (``grpc_server.cc:172-231``). A server that cannot start is the error -8001 of
  Start Server's own diagram. ``stop`` cancels the calls in progress at once
  (``grpc_server.cc:239-258``).

Differences: the library also serves gRPC's health check and server reflection
(``grpc_server.cc:188-189``); this server does neither. A status code outside the
seventeen of gRPC (``Set Call Status`` takes any integer) ends the call as UNKNOWN.
A call whose handler thread would be the 257th at once waits for a free one.
"""

from __future__ import annotations

GRPC_SERVER_MODULE_NAME = "grpc_server"

GRPC_SERVER_MODULE_SOURCE = '''"""The server of LabVIEW's gRPC library, on grpcio."""

from __future__ import annotations

import concurrent.futures
import functools
import pathlib
import queue
import threading
from collections.abc import Callable, Iterator
from typing import Any

import grpc

_END = object()
_WORKERS = 256
_STATUS_OF = {status.value[0]: status for status in grpc.StatusCode}
# The text TranslateGrpcError gives an error code that comes with no message.
_MESSAGES = {
    -1: "Invalid Argument 1",
    -2: "Invalid Argument 2",
    -3: "Invalid Argument 3",
    -4: "Invalid Argument 4",
    -5: "Invalid Argument 5",
    -1001: "The operation was cancelled",
    -1002: "Unknown error",
    -1003: "The client specified an invalid argument",
    -1004: "The deadline expired before the operation could complete",
    -1005: "Requested entity was not found",
    -1006: "Entity that a client attempted to create already exists",
    -1007: "Permission Denied",
    -1008: "Some resource has been exhausted",
    -1009: "Failed Precondition",
    -1010: "The operation was aborted",
    -1011: "The operation was attempted past the valid range",
    -1012: "The operation is not implemented",
    -1013: "\\tInternal error",
    -1014: "The service is currently unavailable",
    -1015: "Unrecoverable data loss or corruption",
    -1016: "The request does not have valid authentication credentials",
}


class CallError(grpc.RpcError):
    """A gRPC library VI's failure: ``labview_code`` is its LabVIEW error code and
    the text its message (the library's own text for the code when none is given)."""

    def __init__(self, labview_code: int, message: str = "") -> None:
        super().__init__(message or _MESSAGES.get(labview_code, "Unknown Error"))
        self.labview_code = labview_code


class _Method:
    """A registered method: its user event, the wrap that makes the event's data
    from a call, and its message classes."""

    def __init__(
        self,
        event: list[Any],
        wrap: Callable[[Call], Any],
        request_class: Any,
        response_class: Any,
    ) -> None:
        self.event = event
        self.wrap = wrap
        self.request_class = request_class
        self.response_class = response_class

    def fire(self, call: Call) -> None:
        """Send the call to every queue that holds the user event: the same call
        to each, in the message a Generate User Event sends."""
        data = self.wrap(call)
        for registered in list(self.event):
            registered.put((self.event, data, "User Event", None))


class Call:
    """A call, as the gRPC id the user event carries."""

    def __init__(
        self,
        request_class: Any,
        response_class: Any,
        requests: Iterator[Any],
        context: grpc.ServicerContext,
    ) -> None:
        self.request_class = request_class
        self.response_class = response_class
        self._requests = requests
        self._context = context
        self._responses: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._code = 0
        self._details = ""
        self._completed = False
        self._cancelled = False

    def __copy__(self) -> Call:
        return self

    def __deepcopy__(self, memo: dict[int, Any]) -> Call:
        return self

    def is_cancelled(self) -> bool:
        """Whether the call ended before Complete Call: the client cancelled it,
        its deadline passed, or the server stopped."""
        with self._lock:
            return self._cancelled or (
                not self._completed and not self._context.is_active()
            )

    def _is_active(self) -> bool:
        with self._lock:
            finished = self._completed
        return not finished and not self.is_cancelled()

    def _failure(self) -> CallError:
        if self._code != 0:
            return CallError(-(1000 + self._code))
        if self.is_cancelled():
            return CallError(-(1000 + grpc.StatusCode.CANCELLED.value[0]))
        return CallError(-2)

    def read(self) -> Any:
        """The next request message; ``CallError`` when there is none."""
        with self._read_lock:
            if not self._is_active():
                raise self._failure()
            try:
                return next(self._requests)
            except (StopIteration, grpc.RpcError):
                raise self._failure() from None

    def write(self, message: Any) -> None:
        """Send a response message; ``CallError`` when the call is over."""
        with self._write_lock:
            if not self._is_active():
                raise self._failure()
            self._responses.put(message)

    def set_status(self, code: int, details: str = "") -> None:
        """The status the call ends with."""
        self._code = int(code)
        self._details = details

    def complete(self) -> None:
        """End the call with its status; a call that is over stays as it is."""
        with self._lock:
            if self._completed or self._cancelled:
                return
            self._completed = True
        self._responses.put(_END)

    def _terminated(self) -> None:
        """The RPC ended: a call not completed by then was cancelled."""
        with self._lock:
            if not self._completed:
                self._cancelled = True
        self._responses.put(_END)

    def _finish(self, context: grpc.ServicerContext) -> None:
        status = _STATUS_OF.get(self._code, grpc.StatusCode.UNKNOWN)
        if self._cancelled or not context.is_active():
            return
        if status != grpc.StatusCode.OK:
            context.abort(status, self._details)
        if self._details:
            context.set_details(self._details)


class Server:
    """The gRPC id of a server."""

    def __init__(self) -> None:
        self.port = 0
        self._methods: dict[str, _Method] = {}
        self._calls: set[Call] = set()
        self._lock = threading.Lock()
        self._server: grpc.Server | None = None
        self._executor: concurrent.futures.ThreadPoolExecutor | None = None

    def register(
        self,
        name: str,
        event: list[Any],
        wrap: Callable[[Call], Any],
        request_class: Any,
        response_class: Any,
    ) -> None:
        """Serve the method ``name`` by firing ``event`` for each call; the first
        registration of a name is the one kept."""
        with self._lock:
            self._methods.setdefault(
                name, _Method(event, wrap, request_class, response_class)
            )

    def start(
        self, address: str = "", certificate_path: str = "", key_path: str = ""
    ) -> int:
        """Listen on ``address`` and return the port; ``CallError`` -8001 when the
        server cannot start."""
        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=_WORKERS, thread_name_prefix="grpc-server"
        )
        server = grpc.server(
            executor,
            handlers=[_Handler(self)],
            options=[
                ("grpc.max_receive_message_length", -1),
                ("grpc.max_send_message_length", -1),
            ],
        )
        try:
            if len(certificate_path) > 1:
                pair = (
                    pathlib.Path(key_path).read_bytes(),
                    pathlib.Path(certificate_path).read_bytes(),
                )
                port = server.add_secure_port(
                    address or "0.0.0.0:50051", grpc.ssl_server_credentials([pair])
                )
            else:
                port = server.add_insecure_port(address or "0.0.0.0:50051")
            if not port:
                raise RuntimeError("the server has no port")
            server.start()
        except (OSError, RuntimeError, ValueError):
            executor.shutdown(wait=False)
            raise CallError(
                -8001,
                "Failed to start the gRPC server.  "
                "Ensure the specified port is not in use.",
            ) from None
        with self._lock:
            self._server = server
            self._executor = executor
        self.port = port
        return port

    def stop(self) -> None:
        """Stop listening and cancel the calls in progress at once."""
        with self._lock:
            server, self._server = self._server, None
            executor, self._executor = self._executor, None
            calls = list(self._calls)
        if server is None:
            return
        server.stop(0).wait()
        for call in calls:
            call._terminated()
        if executor is not None:
            executor.shutdown(wait=False)

    def _find(self, name: str) -> _Method | None:
        with self._lock:
            return self._methods.get(name)

    def _track(self, call: Call, add: bool) -> None:
        with self._lock:
            if add:
                self._calls.add(call)
            else:
                self._calls.discard(call)


class _Handler(grpc.GenericRpcHandler):
    """The handler of every method: it finds the method for each call."""

    def __init__(self, server: Server) -> None:
        self._server = server

    def service(
        self, handler_call_details: grpc.HandlerCallDetails
    ) -> grpc.RpcMethodHandler:
        method = self._server._find(handler_call_details.method)
        if method is None:
            return grpc.stream_stream_rpc_method_handler(_unimplemented)
        return grpc.stream_stream_rpc_method_handler(
            functools.partial(self._serve, method),
            request_deserializer=method.request_class.FromString,
            response_serializer=lambda message: message.SerializeToString(),
        )

    def _serve(
        self, method: _Method, requests: Iterator[Any], context: grpc.ServicerContext
    ) -> Iterator[Any]:
        call = Call(method.request_class, method.response_class, requests, context)
        self._server._track(call, True)
        try:
            if not context.add_callback(call._terminated):
                call._terminated()
            method.fire(call)
            while True:
                response = call._responses.get()
                if response is _END:
                    break
                yield response
        finally:
            self._server._track(call, False)
        call._finish(context)


def _unimplemented(requests: Iterator[Any], context: grpc.ServicerContext) -> None:
    context.abort(grpc.StatusCode.UNIMPLEMENTED, "")
'''
