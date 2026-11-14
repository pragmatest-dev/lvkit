"""TCP Open Connection, Write, Read and Close run as Python on a socket."""

from __future__ import annotations

import socket
import threading
import time

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.helpers import error_cluster_type, install_generated_package
from tests.primitive_caller import run_primitive
from tests.vilib_caller import I32, STRING, primitive

ERROR = error_cluster_type()

U16 = primitive("NumUInt16")
CONNECTION = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="Refnum")
MODE = primitive("NumUInt16")


def _pair() -> tuple[socket.socket, socket.socket]:
    return socket.socketpair()


def _read(connection, count, timeout=1000, mode=None, gaps=None):
    pane = [
        (0, "output", ERROR),
        (2, "output", STRING),
        (3, "output", CONNECTION),
        (7, "input", MODE),
        (8, "input", ERROR),
        (9, "input", I32),
        (10, "input", I32),
        (11, "input", CONNECTION),
    ]
    inputs = {"connection": (11, CONNECTION), "count": (10, I32)}
    arguments = {"connection": connection, "count": count}
    if timeout is not None:
        inputs["timeout"] = (9, I32)
        arguments["timeout"] = timeout
    return run_primitive(
        1716,
        pane,
        inputs,
        {"data": (2, STRING), "error_out": (0, ERROR)},
        arguments,
        constants=None if mode is None else {7: (mode, MODE)},
        gaps=gaps,
    )


def test_read_standard_waits_for_every_byte() -> None:
    near, far = _pair()
    far.sendall(b"abcdef")
    source, result = _read(near, 4)
    assert result.data == "abcd"
    assert result.error_out is None
    assert "lvkit" not in source
    assert near.recv(10) == b"ef"


def test_read_standard_times_out_with_the_bytes_so_far() -> None:
    near, far = _pair()
    far.sendall(b"ab")
    _, result = _read(near, 5, timeout=100)
    assert result.data == "ab"
    assert result.error_out.code == 56
    assert result.error_out.source == "TCP Read"


def test_read_buffered_returns_nothing_on_a_timeout_and_keeps_the_bytes() -> None:
    near, far = _pair()
    far.sendall(b"ab")
    _, result = _read(near, 5, timeout=100, mode=1)
    assert result.data == ""
    assert result.error_out.code == 56
    far.sendall(b"cde")
    _, result = _read(near, 5, timeout=1000, mode=1)
    assert result.data == "abcde"
    assert result.error_out is None


def test_read_immediate_returns_what_has_arrived() -> None:
    near, far = _pair()
    far.sendall(b"ab")
    _, result = _read(near, 5, timeout=1000, mode=3)
    assert result.data == "ab"
    assert result.error_out is None


def test_read_immediate_times_out_when_nothing_arrives() -> None:
    near, _far = _pair()
    _, result = _read(near, 5, timeout=50, mode=3)
    assert result.data == ""
    assert result.error_out.code == 56


def test_read_a_closed_connection_is_error_66() -> None:
    near, far = _pair()
    far.sendall(b"xy")
    far.close()
    _, result = _read(near, 5)
    assert result.data == "xy"
    assert result.error_out.code == 66


def test_read_wait_indefinitely_with_minus_one() -> None:
    near, far = _pair()
    threading.Timer(0.1, lambda: far.sendall(b"late")).start()
    started = time.monotonic()
    _, result = _read(near, 4, timeout=-1)
    assert result.data == "late"
    assert time.monotonic() - started >= 0.09


def test_read_default_timeout_is_25_seconds() -> None:
    near, far = _pair()
    far.sendall(b"ab")
    source, result = _read(near, 2, timeout=None)
    assert "25000" in source
    assert result.data == "ab"


def test_read_in_crlf_mode_is_a_gap() -> None:
    near, _far = _pair()
    with pytest.raises(NotImplementedError, match="CRLF"):
        _read(near, 4, mode=2, gaps=[])


def _write(connection, data, timeout=None):
    pane = [
        (0, "output", ERROR),
        (2, "output", I32),
        (3, "output", CONNECTION),
        (8, "input", ERROR),
        (9, "input", I32),
        (10, "input", STRING),
        (11, "input", CONNECTION),
    ]
    inputs = {"connection": (11, CONNECTION), "data": (10, STRING)}
    arguments = {"connection": connection, "data": data}
    if timeout is not None:
        inputs["timeout"] = (9, I32)
        arguments["timeout"] = timeout
    return run_primitive(
        1714,
        pane,
        inputs,
        {"written": (2, I32), "error_out": (0, ERROR)},
        arguments,
    )


def test_write_sends_the_bytes_and_counts_them() -> None:
    near, far = _pair()
    source, result = _write(near, "h\xe9llo")
    assert result.written == 5
    assert result.error_out is None
    assert far.recv(10) == b"h\xe9llo"
    assert "lvkit" not in source


def _write_until_it_fails(near, chunk, timeout):
    """Write ``chunk`` again and again until a write reports an error: the operating
    system buffers some number of bytes first, and how many differs by platform."""
    for _ in range(500):
        _, result = _write(near, chunk, timeout=timeout)
        if result.error_out is not None:
            return result
        assert result.written == len(chunk)
    raise AssertionError("no write ever failed")


def test_write_that_times_out_returns_the_bytes_written_with_error_56() -> None:
    near, far = _pair()
    near.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    far.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    chunk = "x" * 1_000_000
    result = _write_until_it_fails(near, chunk, timeout=200)
    assert result.written < len(chunk)
    assert result.error_out.code == 56


def test_write_to_a_connection_the_peer_closed_is_error_66() -> None:
    near, far = _pair()
    far.close()
    result = _write_until_it_fails(near, "data", timeout=1000)
    assert result.error_out.code == 66


def _open(address, port, wire_address=True, timeout=None, port_type=U16, gaps=None):
    inputs = {"port": (10, port_type)}
    arguments = {"port": port}
    if wire_address:
        inputs["address"] = (11, STRING)
        arguments["address"] = address
    if timeout is not None:
        inputs["timeout"] = (9, I32)
        arguments["timeout"] = timeout
    pane = [
        (0, "output", ERROR),
        (3, "output", CONNECTION),
        (6, "input", U16),
        (8, "input", ERROR),
        (9, "input", I32),
        (10, "input", port_type),
        (11, "input", STRING),
    ]
    return run_primitive(
        1715,
        pane,
        inputs,
        {"connection": (3, CONNECTION), "error_out": (0, ERROR)},
        arguments,
        gaps=gaps,
    )


@pytest.fixture
def server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    yield listener
    listener.close()


def test_open_connects_to_the_address_and_port(server) -> None:
    source, result = _open("127.0.0.1", server.getsockname()[1])
    accepted, _ = server.accept()
    assert result.error_out is None
    result.connection.sendall(b"hi")
    assert accepted.recv(2) == b"hi"
    assert "lvkit" not in source
    result.connection.close()


def test_open_with_no_address_is_the_local_computer(server) -> None:
    _, result = _open("", server.getsockname()[1], wire_address=False)
    assert result.error_out is None
    server.accept()
    result.connection.close()


def test_open_a_port_nothing_listens_on_is_error_63() -> None:
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    # Windows retries a refused connect for about 2 s before it reports the
    # refusal, so the timeout must outlast that.
    _, result = _open("127.0.0.1", port, timeout=15000)
    assert result.error_out.code == 63


def test_open_port_zero_is_error_54() -> None:
    _, result = _open("127.0.0.1", 0)
    assert result.error_out.code == 54


def test_open_an_unknown_host_is_error_54() -> None:
    _, result = _open("no-such-host.invalid", 80, timeout=2000)
    assert result.error_out.code == 54


def test_open_with_a_service_name_is_a_gap() -> None:
    with pytest.raises(NotImplementedError, match="Service Locator"):
        _open("127.0.0.1", "http", port_type=STRING, gaps=[])


def _close(connection, error_in=None, wire_error=False):
    pane = [
        (0, "output", ERROR),
        (3, "output", CONNECTION),
        (8, "input", ERROR),
        (10, "input", primitive("Boolean")),
        (11, "input", CONNECTION),
    ]
    inputs = {"connection": (11, CONNECTION)}
    arguments = {"connection": connection}
    if wire_error:
        inputs["error_in"] = (8, ERROR)
        arguments["error_in"] = error_in
    return run_primitive(
        1713,
        pane,
        inputs,
        {"error_out": (0, ERROR)},
        arguments,
    )


def test_close_closes_the_socket() -> None:
    near, _far = _pair()
    source, result = _close(near)
    assert near.fileno() == -1
    assert result.error_out is None
    assert "lvkit" not in source


def test_close_runs_although_an_error_came_in() -> None:
    install_generated_package()
    from generated_package.errors import LabVIEWError

    near, _far = _pair()
    earlier = LabVIEWError(code=7, source="earlier", message="")
    _, result = _close(near, error_in=earlier, wire_error=True)
    assert near.fileno() == -1
    assert result.error_out is earlier


def test_a_neighbour_without_python_stays_a_gap() -> None:
    near, _far = _pair()
    with pytest.raises(NotImplementedError):
        run_primitive(
            1712,
            [(0, "output", ERROR), (8, "input", CONNECTION)],
            {"listener": (8, CONNECTION)},
            {"error_out": (0, ERROR)},
            {"listener": near},
            gaps=[],
        )
