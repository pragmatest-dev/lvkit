"""Best-effort detection of a locally installed LabVIEW and its ``vi.lib``.

LabVIEW versions install side-by-side. When a ``.vi`` is opened from the OS
file explorer, LabVIEW opens it with whichever install last registered itself
as the default. On Windows that "current/default" install is recorded in the
registry under ``HKLM\\SOFTWARE\\National Instruments\\LabVIEW\\CurrentVersion``.
lvkit mirrors that behavior so users don't have to pass ``--vilib`` manually.

**Everything here is best-effort and non-fatal.** ``detect_labview()`` NEVER
raises: on any error, an absent/stale registry entry, or nothing installed it
returns ``None`` and callers fall back to their existing behavior (no
auto-vilib). Detected candidates are always validated on disk (their ``vi.lib``
must actually exist), so an uninstalled-but-registry-leftover version is skipped
automatically.

This module has no lvkit dependencies so it can be imported and exercised in
isolation, including on machines without LabVIEW.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# LabVIEW registry root (relative to HKLM). The 32-bit install on 64-bit Windows
# lands under WOW6432Node, which we reach via the KEY_WOW64_32KEY access flag
# rather than a separate path.
_LV_REG_SUBKEY = r"SOFTWARE\National Instruments\LabVIEW"


@dataclass(frozen=True)
class DetectedLabVIEW:
    """A validated local LabVIEW install and its library roots."""

    install_dir: Path
    vilib_root: Path
    userlib_root: Path | None
    version: str | None
    source: str


def detect_labview() -> DetectedLabVIEW | None:
    """Detect the default local LabVIEW install, best-effort.

    Returns the most-recently-registered ("current/default") LabVIEW whose
    ``vi.lib`` exists on disk, or ``None`` if detection fails or nothing valid
    is found. NEVER raises — every failure mode falls back to ``None``.
    """
    try:
        if sys.platform.startswith("win"):
            return _detect_windows()
        if sys.platform == "darwin":
            return _detect_macos()
        if sys.platform.startswith("linux"):
            return _detect_linux()
    except Exception:
        # Best-effort by contract: never let detection crash a caller.
        return None
    return None


def _candidate_from_install_dir(
    install_dir: Path, version: str | None, source: str
) -> DetectedLabVIEW | None:
    """Build a validated candidate, or ``None`` if ``vi.lib`` is missing.

    The ``vi.lib`` check is what makes detection self-healing: a stale registry
    entry pointing at an uninstalled version has no ``vi.lib`` and is dropped.
    """
    try:
        vilib = install_dir / "vi.lib"
        if not vilib.is_dir():
            return None
        userlib = install_dir / "user.lib"
        return DetectedLabVIEW(
            install_dir=install_dir,
            vilib_root=vilib,
            userlib_root=userlib if userlib.is_dir() else None,
            version=version,
            source=source,
        )
    except Exception:
        return None


def _version_sort_key(version: str | None) -> tuple[int, ...]:
    """Sort key for LabVIEW version strings like ``"20.0"`` or ``"2021"``.

    Non-numeric or missing versions sort lowest so real versions win.
    """
    if not version:
        return (-1,)
    parts: list[int] = []
    for chunk in version.replace("_", ".").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else -1)
    return tuple(parts) if parts else (-1,)


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #


def _detect_windows() -> DetectedLabVIEW | None:
    """Detect via the Windows registry across both 32- and 64-bit views."""
    # The guard also lets the type checker narrow to the win32 platform so the
    # win32-only winreg members below are recognized off-Windows.
    if sys.platform != "win32":
        return None
    import winreg  # noqa: PLC0415 - Windows-only stdlib, guarded above

    # Read both registry views so we find LabVIEW regardless of its bitness or
    # the running Python's bitness.
    views = [
        (winreg.KEY_READ | winreg.KEY_WOW64_64KEY, "64"),
        (winreg.KEY_READ | winreg.KEY_WOW64_32KEY, "32"),
    ]

    fallback_candidates: list[tuple[tuple[int, ...], DetectedLabVIEW]] = []

    for access, _view in views:
        try:
            root = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _LV_REG_SUBKEY, 0, access
            )
        except OSError:
            continue

        with root:
            # 1) Primary signal: the "CurrentVersion" subkey is the default
            #    install that opens a .vi on double-click. Prefer it outright.
            current = _read_install_from_subkey(winreg, root, access, "CurrentVersion")
            if current is not None:
                install_dir, version = current
                cand = _candidate_from_install_dir(
                    install_dir, version, "windows-registry:CurrentVersion"
                )
                if cand is not None:
                    return cand

            # 2) Fallback: enumerate numeric version subkeys and keep the
            #    highest valid one across both views.
            for name in _enum_subkeys(winreg, root):
                if name.lower() == "currentversion":
                    continue
                if not name[:1].isdigit():
                    continue
                info = _read_install_from_subkey(winreg, root, access, name)
                if info is None:
                    continue
                install_dir, version = info
                cand = _candidate_from_install_dir(
                    install_dir, version or name, "windows-registry:highest"
                )
                if cand is not None:
                    fallback_candidates.append(
                        (_version_sort_key(cand.version), cand)
                    )

    if fallback_candidates:
        fallback_candidates.sort(key=lambda item: item[0])
        return fallback_candidates[-1][1]
    return None


def _enum_subkeys(winreg, key) -> list[str]:  # type: ignore[no-untyped-def]
    """Enumerate immediate subkey names of an open registry key."""
    names: list[str] = []
    i = 0
    while True:
        try:
            names.append(winreg.EnumKey(key, i))
        except OSError:
            break
        i += 1
    return names


def _read_install_from_subkey(
    winreg,  # type: ignore[no-untyped-def]
    root,  # noqa: ANN001
    access: int,
    subkey: str,
) -> tuple[Path, str | None] | None:
    """Read ``Path`` (and optional ``Version``) from a LabVIEW version subkey."""
    try:
        with winreg.OpenKey(root, subkey, 0, access) as key:
            path_value, _ = winreg.QueryValueEx(key, "Path")
            if not path_value:
                return None
            version: str | None = None
            try:
                version_value, _ = winreg.QueryValueEx(key, "Version")
                version = str(version_value) or None
            except OSError:
                version = None
            return Path(str(path_value)), version
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# macOS / Linux (best-effort globs)
# --------------------------------------------------------------------------- #


def _detect_macos() -> DetectedLabVIEW | None:
    """Best-effort macOS detection via known install locations."""
    patterns = [
        "/Applications/National Instruments/LabVIEW */",
        "/Applications/LabVIEW */",
    ]
    return _detect_from_globs(patterns, "macos-glob")


def _detect_linux() -> DetectedLabVIEW | None:
    """Best-effort Linux detection via known install locations."""
    patterns = [
        "/usr/local/natinst/LabVIEW-*/",
    ]
    return _detect_from_globs(patterns, "linux-glob")


def _detect_from_globs(patterns: list[str], source: str) -> DetectedLabVIEW | None:
    """Return the highest-versioned install matching any glob pattern."""
    candidates: list[tuple[tuple[int, ...], DetectedLabVIEW]] = []
    root = Path("/")
    for pattern in patterns:
        # Patterns are absolute; strip the leading slash for Path.glob.
        for match in root.glob(pattern.lstrip("/")):
            if not match.is_dir():
                continue
            version = _version_from_dir_name(match.name)
            cand = _candidate_from_install_dir(match, version, source)
            if cand is not None:
                candidates.append((_version_sort_key(cand.version), cand))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def _version_from_dir_name(name: str) -> str | None:
    """Extract a version token from a dir name like ``"LabVIEW 2021"``."""
    token = name.replace("LabVIEW", "").replace("-", " ").strip()
    return token or None
