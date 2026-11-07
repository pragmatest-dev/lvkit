"""A process for ``tests/test_console.py``: set up its streams the way lvkit's
command line does, then print.

``utf8``: ``use_utf8_output``, then a non-ASCII line on stderr.
``lines``: ``write_whole_lines``, then three progress lines on stdout, each
followed by a warning on stderr, and a closing summary on stderr."""

from __future__ import annotations

import sys

from lvkit.console import use_utf8_output, write_whole_lines


def main(mode: str) -> None:
    if mode == "utf8":
        use_utf8_output()
        print("a — b", file=sys.stderr)
        return
    write_whole_lines()
    for n in (1, 2, 3):
        print(f"  [{n}/3] V{n}.vi")
        print(f"warning: V{n}.vi", file=sys.stderr)
    print("Partial: 3 problem(s)", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1])
