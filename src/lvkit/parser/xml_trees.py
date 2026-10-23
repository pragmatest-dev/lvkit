"""Parse each of a VI's XML files once while that VI is being parsed.

A VI's block diagram, front panel and main XML are each read by several parser
steps. Inside ``xml_scope()`` (``parse_vi`` opens one per VI) ``parsed_xml``
parses a file the first time it is asked for and hands every later step the
same root; outside a scope it parses the file every time. A root handed out
here is shared, so callers only read it.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_roots: ContextVar[dict[Path, ET.Element] | None] = ContextVar(
    "lvkit_xml_roots", default=None
)


@contextmanager
def xml_scope() -> Iterator[None]:
    """Share each XML file's parse for the duration of the block. A nested scope
    reuses the outer one's parses."""
    if _roots.get() is not None:
        yield
        return
    token = _roots.set({})
    try:
        yield
    finally:
        _roots.reset(token)


def parsed_xml(path: Path | str) -> ET.Element:
    """The root element of the XML file at ``path``."""
    roots = _roots.get()
    if roots is None:
        return ET.parse(path).getroot()
    key = Path(path).resolve()
    root = roots.get(key)
    if root is None:
        root = roots[key] = ET.parse(key).getroot()
    return root
