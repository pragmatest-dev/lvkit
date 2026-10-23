"""LabVIEW's String To IP and IP To String functions (TCP palette).

Both follow NI's public pages (labview-api-ref ``functions/string-to-ip.html``
and ``functions/ip-to-string.html``). A net address is the unsigned 32-bit
number for an IPv4 address (``128.0.0.25`` -> ``0x80000019``).
"""

from __future__ import annotations

import ipaddress
import socket


def string_to_ip(name: str) -> int:
    """The net address of ``name`` (a host name or dot notation), as the
    operating system's resolver returns it first. An empty name is the current
    machine. A name that does not resolve is 0."""
    host = name or socket.gethostname()
    try:
        return int(ipaddress.IPv4Address(socket.gethostbyname(host)))
    except (OSError, ValueError):
        return 0


def ip_to_string(net_address: int, dot_notation: bool = False) -> str:
    """``net_address`` as text: dot notation (``128.0.0.25``) when
    ``dot_notation`` is true, else the host name from a reverse lookup. When the
    reverse lookup finds no name, the dot notation is returned."""
    dotted = str(ipaddress.IPv4Address(net_address & 0xFFFFFFFF))
    if dot_notation:
        return dotted
    try:
        return socket.gethostbyaddr(dotted)[0]
    except OSError:
        return dotted
