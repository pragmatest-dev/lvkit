"""``use_utf8_output``: a process whose default stream encoding is not UTF-8
still writes UTF-8, so a reader that decodes its output as UTF-8 (pytest's
capture, a pipe) never meets a code-page byte."""

from __future__ import annotations

import os
import subprocess
import sys


def test_use_utf8_output_writes_utf8_under_a_code_page_default():
    code = (
        "from lvkit.console import use_utf8_output; import sys; "
        "use_utf8_output(); print('a — b', file=sys.stderr)"
    )
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, check=True
    )
    assert result.stderr.decode("utf-8").strip() == "a — b"
