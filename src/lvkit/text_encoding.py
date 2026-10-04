"""Text encoding helpers for LabVIEW resource data."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_PYLABVIEW_ENCODING = "mac_roman"
_XML_TOKEN_RE = re.compile(
    r"(<!\[CDATA\[.*?\]\]>|<!--.*?-->|<[^>]*>)",
    re.DOTALL,
)
_XML_QUOTED_RE = re.compile(r"""(["'])(.*?)\1""", re.DOTALL)


def _windows_ansi_encoding() -> str:
    import ctypes

    try:
        # ctypes.WinDLL exists only on Windows; this branch is Windows-only (the
        # AttributeError is even caught below), but pyright analyses it on every OS.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # pyright: ignore[reportAttributeAccessIssue]
        get_acp = kernel32.GetACP
        get_acp.restype = ctypes.c_uint
        return f"cp{get_acp()}"
    except (AttributeError, OSError):
        return "mbcs"


# A VI's own 'vers' resource Language field (2-byte enum, Mac Script.h-
# derived codes LabVIEW inherited) -- publicly documented values (the
# pylabview wiki's Blocks.md, "vers" section). Only the values actually seen
# are listed there; 0 ("English") is excluded on purpose -- the wiki notes a
# non-zero code has only been seen in LabVIEW 8.0.0f5 through 19.0.0f5, so a
# modern (20+) VI always writes 0 regardless of its real authoring locale,
# making 0 ambiguous rather than a real "English" signal. 1/3 (French/German)
# still fall under the Western-European single-byte codepage, same as English.
_VERS_LANGUAGE_CODEPAGE: dict[int, str] = {
    1: "cp1252",  # French
    3: "cp1252",  # German
    14: "cp932",  # Japanese
    23: "cp949",  # Korean
    33: "cp936",  # Chinese
}


def vers_language_encoding(language: int) -> str | None:
    """The codepage a VI's own ``vers`` resource ``Language`` value implies,
    or ``None`` when it is 0/unset/unrecognized (see
    :data:`_VERS_LANGUAGE_CODEPAGE`) -- the caller then falls back to the
    platform default."""
    return _VERS_LANGUAGE_CODEPAGE.get(language)


def labview_text_encoding(vers_language: int | None = None) -> str:
    """Return the native text encoding used by LabVIEW on this platform.

    ``vers_language`` is the VI's OWN ``vers`` resource ``Language`` value,
    when the caller has it (real, VI-recorded data, not a guess) -- checked
    before the platform default (but after the explicit env override, which
    is the reader's own, more specific instruction) since it names the actual
    save-time locale rather than assuming this reading machine's own locale
    matches it.
    """
    # Override for a VI saved in a different locale than the reader's machine.
    override = os.environ.get("LVKIT_TEXT_ENCODING")
    if override:
        return override
    if vers_language:
        detected = vers_language_encoding(vers_language)
        if detected:
            return detected
    if sys.platform == "win32":
        return _windows_ansi_encoding()
    if sys.platform == "darwin":
        return _PYLABVIEW_ENCODING
    # Neither Windows' own active codepage (the author's real locale) nor
    # pylabview's Mac Roman convention apply here, so there is no signal tying
    # this READING machine's own OS locale to the VI's actual save-time
    # encoding -- locale.getpreferredencoding() is pure coincidence (it broke
    # a real corpus VI's degree sign: saved under Windows-1252, read on a
    # UTF-8-locale Linux box). cp1252 is LabVIEW's own most common legacy
    # Windows default, matching the byte-preserving precedent in
    # parser/vi.py's string decode. A VI saved under a different single-byte
    # codepage still needs LVKIT_TEXT_ENCODING, same as today.
    return "cp1252"


def decode_labview_text(data: bytes, encoding: str | None = None) -> str:
    """Decode a LabVIEW byte string without discarding the surrounding data."""
    return data.decode(encoding or labview_text_encoding(), errors="replace")


def _transcode_fragment(text: str, target: str) -> str:
    raw = text.encode(_PYLABVIEW_ENCODING, errors="xmlcharrefreplace")
    return raw.decode(target, errors="replace")


def _transcode_xml_token(token: str, target: str) -> str:
    if token.startswith("<![CDATA["):
        return f"<![CDATA[{_transcode_fragment(token[9:-3], target)}]]>"
    if token.startswith("<!--"):
        return f"<!--{_transcode_fragment(token[4:-3], target)}-->"
    if token.startswith("<"):
        return _XML_QUOTED_RE.sub(
            lambda match: (
                match.group(1)
                + _transcode_fragment(match.group(2), target)
                + match.group(1)
            ),
            token,
        )
    return _transcode_fragment(token, target)


def normalize_extracted_xml(path: Path, encoding: str | None = None) -> None:
    """Convert pylabview's lossless Mac Roman byte mapping to native text."""
    target = encoding or labview_text_encoding()
    if target == _PYLABVIEW_ENCODING:
        return

    text = path.read_text(encoding="utf-8")
    # Pure ASCII is identical under mac_roman, nothing to transcode.
    if text.isascii():
        return
    normalized = "".join(
        _transcode_xml_token(token, target)
        for token in _XML_TOKEN_RE.split(text)
        if token
    )
    normalized = normalized.replace('Encoding="mac_roman"', f'Encoding="{target}"')

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(normalized, encoding="utf-8")
    os.replace(tmp, path)
