"""Running a test in a chosen time zone.

``time_zone(name)`` sets ``TZ`` and has the C library read it again
(``time.tzset``), and puts both back when the test ends, so no later test runs in
that zone. ``time.tzset`` exists on POSIX only, so a test that uses the fixture
carries ``needs_posix_tz``."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Iterator

import pytest

needs_posix_tz = pytest.mark.skipif(
    sys.platform == "win32", reason="time.tzset is POSIX only"
)


@pytest.fixture
def time_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    def use(name: str) -> None:
        monkeypatch.setenv("TZ", name)
        time.tzset()

    yield use
    monkeypatch.undo()
    time.tzset()
