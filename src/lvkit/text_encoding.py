"""Text encoding helpers for LabVIEW resource data."""

from __future__ import annotations

import locale
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
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_acp = kernel32.GetACP
        get_acp.restype = ctypes.c_uint
        return f"cp{get_acp()}"
    except (AttributeError, OSError):
        return "mbcs"


def labview_text_encoding() -> str:
    """Return the native text encoding used by LabVIEW on this platform."""
    if sys.platform == "win32":
        return _windows_ansi_encoding()
    if sys.platform == "darwin":
        return _PYLABVIEW_ENCODING
    return locale.getpreferredencoding(False) or "utf-8"


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
    normalized = "".join(
        _transcode_xml_token(token, target)
        for token in _XML_TOKEN_RE.split(text)
        if token
    )
    normalized = normalized.replace('Encoding="mac_roman"', f'Encoding="{target}"')

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(normalized, encoding="utf-8")
    os.replace(tmp, path)
