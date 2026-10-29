"""Whether a function's code reads an input.

An input nothing reads needs no default; one the code reads and nothing records a
default for is a gap. A template names an input by ``in_<index>``, by its name, or
(a vi.lib template) by ``{param}``.
"""

from __future__ import annotations

import re


def template_reads(template: str, *identifiers: str) -> bool:
    """Whether ``template`` contains one of ``identifiers`` as a whole word."""
    return any(
        re.search(rf"(?<!\w){re.escape(name)}(?!\w)", template)
        for name in identifiers
        if name
    )
