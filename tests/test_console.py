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


def test_write_whole_lines_keeps_stdout_and_stderr_in_order_in_one_pipe():
    """Progress on stdout and warnings on stderr, sent to one pipe, come out in
    the order they were printed, each on its own line: no warning glued to the
    end of a progress line, no closing summary in the middle of the progress."""
    code = (
        "from lvkit.console import write_whole_lines; import sys; "
        "write_whole_lines(); "
        "[(print(f'  [{n}/3] V{n}.vi'), print(f'warning: V{n}.vi', file=sys.stderr))"
        " for n in (1, 2, 3)]; "
        "print('Partial: 3 problem(s)', file=sys.stderr)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    assert result.stdout.decode().splitlines() == [
        "  [1/3] V1.vi",
        "warning: V1.vi",
        "  [2/3] V2.vi",
        "warning: V2.vi",
        "  [3/3] V3.vi",
        "warning: V3.vi",
        "Partial: 3 problem(s)",
    ]
