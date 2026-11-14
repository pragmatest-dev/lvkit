"""TCP Read and TCP Write as plain Python functions over a ``socket``.

``codegen/nodes/ops/tcp.py`` writes the source of each into the generated
module (``module_function.function_source``), so they are the generated code's
own functions and import nothing from lvkit. They need ``socket`` and ``time``
imported in that module.

NI, "TCP Read Function" and "TCP Write Function" (labview-api-ref,
``functions/tcp-read.html``, ``functions/tcp-write.html``): ``timeout ms`` is the
time the function waits before it reports a timeout (error 56), -1 waits
indefinitely. The read ``mode`` is 0 standard, 1 buffered, 2 CRLF, 3 immediate:

* standard waits until all ``count`` bytes arrive or the time runs out, and
  returns the bytes read so far, with a timeout error when fewer arrived;
* buffered waits for all ``count`` bytes and, when fewer arrive, returns none (the
  bytes that arrived stay in the connection);
* immediate waits for any bytes and returns those that have arrived, a timeout
  error only when none did.

A connection the peer closed is error 66, which Python raises as
``ConnectionResetError``. The bytes read or written so far are in ``received`` /
``sent`` whatever happens, so the node can return them with the error.
"""

from __future__ import annotations

import socket
import time

TCP_MODE_STANDARD = 0
TCP_MODE_BUFFERED = 1
TCP_MODE_IMMEDIATE = 3


def tcp_read(connection, count, mode, timeout, received):
    deadline = None if timeout < 0 else time.monotonic() + timeout / 1000

    def wait():
        if deadline is None:
            connection.settimeout(None)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("the network operation exceeded the time limit")
        connection.settimeout(remaining)

    def peer_closed():
        raise ConnectionResetError("the network connection was closed by the peer")

    if count <= 0:
        return
    if mode == 1:
        while True:
            wait()
            available = connection.recv(count, socket.MSG_PEEK)
            if not available:
                peer_closed()
            if len(available) >= count:
                break
            time.sleep(0.001)
        received += connection.recv(count)
    elif mode == 3:
        wait()
        chunk = connection.recv(count)
        if not chunk:
            peer_closed()
        received += chunk
    else:
        while len(received) < count:
            wait()
            chunk = connection.recv(count - len(received))
            if not chunk:
                peer_closed()
            received += chunk


def tcp_write(connection, data, timeout, sent):
    deadline = None if timeout < 0 else time.monotonic() + timeout / 1000
    while len(sent) < len(data):
        if deadline is None:
            connection.settimeout(None)
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("the network operation exceeded the time limit")
            connection.settimeout(remaining)
        start = len(sent)
        sent += data[start : start + connection.send(data[start:])]
