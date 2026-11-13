"""The characters of a Python name, the way Python reads them.

Python normalises every identifier with NFKC when it parses code (``µ`` U+00B5
is read as ``μ`` U+03BC, ``ﬁ`` as ``fi``, full-width letters as ASCII), and some
characters that ``str.isalnum()`` accepts are not allowed in a name at all
(``²``). Every Python name lvkit builds from LabVIEW text goes through
``name_characters`` first, so the name written is the name Python reads.
"""

from __future__ import annotations

import unicodedata


def name_characters(text: str) -> str:
    """``text`` normalised the way Python normalises names (NFKC), keeping only
    the characters a name may contain: letters, digits and ``_``. The result may
    start with a digit (see ``starts_a_name``) and may be empty."""
    normalised = unicodedata.normalize("NFKC", text)
    return "".join(c for c in normalised if ("_" + c).isidentifier())


def starts_a_name(text: str) -> bool:
    """True when ``text`` can begin a name (a letter or ``_``)."""
    return text[:1].isidentifier()
