"""The encoding lvkit writes to the terminal and to pipes."""

from __future__ import annotations

import io
import sys


def use_utf8_output() -> None:
    """Write UTF-8 to stdout/stderr whatever the platform's default encoding.

    lvkit prints non-ASCII symbols (arrows, ticks, dashes); a Windows pipe
    defaults to the ANSI code page, which cannot encode them and would crash the
    command, and a reader that expects UTF-8 would fail on the bytes it does write.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
