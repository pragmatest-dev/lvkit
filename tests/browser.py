"""Where the browser tests find a headless Chrome or Chromium.

One lookup for every test that drives a browser: first the names Chrome and
Chromium go by on PATH, then the standard install folders Chrome documents for
each OS (Windows folders come from the environment, never a fixed drive letter).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

_PATH_NAMES = ("google-chrome", "chromium", "chromium-browser", "chrome")

_MACOS_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

# Windows: Chrome installs per machine under %ProgramFiles% or
# %ProgramFiles(x86)%, or per user under %LocalAppData%.
_WINDOWS_ENV_VARS = ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")
_WINDOWS_CHROME = Path("Google") / "Chrome" / "Application" / "chrome.exe"


def find_chrome() -> str | None:
    """The path of a Chrome or Chromium executable, or None when there is none."""
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    candidates = [_MACOS_CHROME]
    for var in _WINDOWS_ENV_VARS:
        root = os.environ.get(var)
        if root:
            candidates.append(Path(root) / _WINDOWS_CHROME)
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None
