"""Every way to start lvkit writes its text as UTF-8, whatever the process's
default stream encoding (a Windows pipe uses the ANSI code page), so a reader that
decodes the output as UTF-8 (the VS Code extension, pytest's capture) never meets a
code-page byte."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

GENERATE = Path(__file__).resolve().parent.parent / "scripts" / "generate_python.py"

# A missing input is reported with its path, so a dash in the name reaches the
# error text.
_MISSING = "no—such.vi"


def _run(command: list[str]) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    return subprocess.run(command, env=env, capture_output=True, check=False)


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-m", "lvkit", "describe", _MISSING],
        [sys.executable, str(GENERATE), _MISSING, "-o", "unused"],
    ],
    ids=["python -m lvkit", "scripts/generate_python.py"],
)
def test_entry_point_writes_utf8_under_a_code_page_default(command):
    result = _run(command)
    text = (result.stdout + result.stderr).decode("utf-8")
    assert _MISSING in text
