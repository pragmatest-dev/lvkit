"""Tests for best-effort LabVIEW detection (lvkit.lv_detect).

These run on any machine (no LabVIEW required). The Windows registry path is
exercised via a small in-memory fake `winreg` injected into sys.modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from lvkit import lv_detect
from lvkit.lv_detect import (
    DetectedLabVIEW,
    _candidate_from_install_dir,
    _detect_from_globs,
    _version_sort_key,
    detect_labview,
)


def _make_install(root: Path, name: str, *, vilib: bool, userlib: bool) -> Path:
    """Create a fake LabVIEW install dir; optionally with vi.lib / user.lib."""
    install = root / name
    install.mkdir(parents=True, exist_ok=True)
    if vilib:
        (install / "vi.lib").mkdir()
    if userlib:
        (install / "user.lib").mkdir()
    return install


# --------------------------------------------------------------------------- #
# Non-fatal contract + candidate validation
# --------------------------------------------------------------------------- #


def test_detect_never_raises_and_is_none_without_labview():
    # On the dev machine there's no LabVIEW; must return None, not raise.
    assert detect_labview() is None


def test_candidate_requires_vilib(tmp_path):
    install = _make_install(tmp_path, "LabVIEW 2021", vilib=False, userlib=False)
    assert _candidate_from_install_dir(install, "2021", "test") is None


def test_candidate_with_vilib_and_userlib(tmp_path):
    install = _make_install(tmp_path, "LabVIEW 2021", vilib=True, userlib=True)
    cand = _candidate_from_install_dir(install, "2021", "test")
    assert cand is not None
    assert cand.vilib_root == install / "vi.lib"
    assert cand.userlib_root == install / "user.lib"


def test_candidate_userlib_optional(tmp_path):
    install = _make_install(tmp_path, "LabVIEW 2021", vilib=True, userlib=False)
    cand = _candidate_from_install_dir(install, "2021", "test")
    assert cand is not None
    assert cand.userlib_root is None


# --------------------------------------------------------------------------- #
# Version sorting
# --------------------------------------------------------------------------- #


def test_version_sort_key_orders_numerically():
    keys = [_version_sort_key(v) for v in ("8.6", "20.0", "21.0", "2021")]
    assert keys == sorted(keys)  # already ascending
    assert _version_sort_key("21.0") > _version_sort_key("20.0")
    assert _version_sort_key(None) < _version_sort_key("8.0")


# --------------------------------------------------------------------------- #
# Glob detection (macOS/Linux shape)
# --------------------------------------------------------------------------- #


def test_glob_detection_picks_highest_version(tmp_path, monkeypatch):
    _make_install(tmp_path, "LabVIEW 2019", vilib=True, userlib=False)
    _make_install(tmp_path, "LabVIEW 2021", vilib=True, userlib=False)
    _make_install(tmp_path, "LabVIEW 2020", vilib=False, userlib=False)  # skipped

    # Redirect the glob root to tmp_path by patching Path("/").
    real_path = lv_detect.Path

    def fake_path(arg="."):
        if arg == "/":
            return tmp_path
        return real_path(arg)

    monkeypatch.setattr(lv_detect, "Path", fake_path)
    result = _detect_from_globs(["/LabVIEW */"], "test-glob")
    assert result is not None
    assert result.install_dir.name == "LabVIEW 2021"
    assert result.source == "test-glob"


# --------------------------------------------------------------------------- #
# Windows registry via a fake winreg
# --------------------------------------------------------------------------- #


class _FakeKey:
    """A registry key node: named subkeys + string values. Context manager."""

    def __init__(self, values=None, subkeys=None):
        self.values = values or {}
        self.subkeys = subkeys or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeWinreg:
    HKEY_LOCAL_MACHINE = "HKLM"
    KEY_READ = 1
    KEY_WOW64_64KEY = 2
    KEY_WOW64_32KEY = 4

    def __init__(self, tree: _FakeKey):
        # tree is the node at HKLM\SOFTWARE\National Instruments\LabVIEW
        self._tree = tree

    def OpenKey(self, root, subkey, _reserved, _access):
        if root == self.HKEY_LOCAL_MACHINE:
            # Only the LabVIEW subkey exists in this fake.
            if subkey.endswith("LabVIEW"):
                return self._tree
            raise OSError("no such key")
        # root is a _FakeKey; descend into a named subkey
        if subkey in root.subkeys:
            return root.subkeys[subkey]
        raise OSError("no such subkey")

    def QueryValueEx(self, key, name):
        if name in key.values:
            return key.values[name], 1
        raise OSError("no such value")

    def EnumKey(self, key, i):
        names = list(key.subkeys.keys())
        if i < len(names):
            return names[i]
        raise OSError("no more")


def _install_fake_winreg(monkeypatch, tree):
    # _detect_windows early-returns off-Windows; pretend we're on win32.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winreg", _FakeWinreg(tree))


def test_windows_prefers_current_version(tmp_path, monkeypatch):
    cur = _make_install(tmp_path, "LV Current", vilib=True, userlib=True)
    old = _make_install(tmp_path, "LV 2019", vilib=True, userlib=False)
    tree = _FakeKey(
        subkeys={
            "CurrentVersion": _FakeKey(
                values={"Path": str(cur), "Version": "21.0"}
            ),
            "19.0": _FakeKey(values={"Path": str(old), "Version": "19.0"}),
        }
    )
    _install_fake_winreg(monkeypatch, tree)
    result = lv_detect._detect_windows()
    assert result is not None
    assert result.install_dir == cur
    assert result.source == "windows-registry:CurrentVersion"
    assert result.version == "21.0"


def test_windows_falls_back_to_highest_when_current_stale(tmp_path, monkeypatch):
    # CurrentVersion points at an uninstalled dir (no vi.lib) -> skipped.
    stale = tmp_path / "gone"
    v20 = _make_install(tmp_path, "LV 2020", vilib=True, userlib=False)
    v21 = _make_install(tmp_path, "LV 2021", vilib=True, userlib=False)
    tree = _FakeKey(
        subkeys={
            "CurrentVersion": _FakeKey(values={"Path": str(stale)}),
            "20.0": _FakeKey(values={"Path": str(v20), "Version": "20.0"}),
            "21.0": _FakeKey(values={"Path": str(v21), "Version": "21.0"}),
        }
    )
    _install_fake_winreg(monkeypatch, tree)
    result = lv_detect._detect_windows()
    assert result is not None
    assert result.install_dir == v21
    assert result.source == "windows-registry:highest"


def test_windows_returns_none_when_nothing_valid(tmp_path, monkeypatch):
    tree = _FakeKey(
        subkeys={
            "20.0": _FakeKey(values={"Path": str(tmp_path / "missing")}),
        }
    )
    _install_fake_winreg(monkeypatch, tree)
    assert lv_detect._detect_windows() is None


# --------------------------------------------------------------------------- #
# Dataclass sanity
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# CLI wiring: _parse_library_roots auto-detect behavior
# --------------------------------------------------------------------------- #


def _ns(**kw):
    import argparse

    defaults = {"vilib": None, "userlib": None, "no_auto_vilib": False}
    defaults.update(kw)
    return argparse.Namespace(**defaults)


def _patch_detected(monkeypatch, detected):
    monkeypatch.setattr(lv_detect, "detect_labview", lambda: detected)


def test_cli_uses_detected_when_vilib_absent(tmp_path, monkeypatch):
    from lvkit import cli

    detected = DetectedLabVIEW(
        install_dir=tmp_path,
        vilib_root=tmp_path / "vi.lib",
        userlib_root=tmp_path / "user.lib",
        version="21.0",
        source="test",
    )
    _patch_detected(monkeypatch, detected)
    vilib, userlib = cli._parse_library_roots(_ns())
    assert vilib == tmp_path / "vi.lib"
    assert userlib == tmp_path / "user.lib"


def test_cli_explicit_vilib_overrides_detection(tmp_path, monkeypatch):
    from lvkit import cli

    # Detection must not run when --vilib is explicit.
    def _boom():
        raise AssertionError("detection should not run")

    monkeypatch.setattr(lv_detect, "detect_labview", _boom)
    vilib, userlib = cli._parse_library_roots(_ns(vilib="/explicit/vi.lib"))
    assert vilib == Path("/explicit/vi.lib")
    assert userlib is None


def test_cli_no_auto_vilib_suppresses_detection(monkeypatch):
    from lvkit import cli

    def _boom():
        raise AssertionError("detection should not run")

    monkeypatch.setattr(lv_detect, "detect_labview", _boom)
    vilib, userlib = cli._parse_library_roots(_ns(no_auto_vilib=True))
    assert vilib is None
    assert userlib is None


def test_cli_explicit_userlib_kept_with_detected_vilib(tmp_path, monkeypatch):
    from lvkit import cli

    detected = DetectedLabVIEW(
        install_dir=tmp_path,
        vilib_root=tmp_path / "vi.lib",
        userlib_root=tmp_path / "user.lib",
        version="21.0",
        source="test",
    )
    _patch_detected(monkeypatch, detected)
    vilib, userlib = cli._parse_library_roots(_ns(userlib="/my/user.lib"))
    assert vilib == tmp_path / "vi.lib"
    assert userlib == Path("/my/user.lib")  # explicit userlib preserved


def test_detected_dataclass_is_frozen(tmp_path):
    d = DetectedLabVIEW(
        install_dir=tmp_path,
        vilib_root=tmp_path / "vi.lib",
        userlib_root=None,
        version="21.0",
        source="test",
    )
    with pytest.raises(Exception):
        d.version = "22.0"  # type: ignore[misc]
