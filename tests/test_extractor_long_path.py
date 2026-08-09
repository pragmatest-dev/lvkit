"""Windows MAX_PATH resilience in the extractor.

Two structural guards plus a sensor:
- extraction happens in a SHALLOW staging dir (``<cache>/.staging``), so the
  temp path is independent of how deep the project sits — a deep repo can't
  overflow it, and pylabview only ever writes short paths;
- a once-per-process warning fires when the FINAL cache path nears MAX_PATH;
- an over-length OSError is augmented with an actionable diagnosis.

The Windows-only helpers are exercised by faking ``sys.platform`` — pure logic,
no real Windows and no real extraction.
"""

from __future__ import annotations

from pathlib import Path

from lvkit import extractor

# ── shallow staging: temp path is independent of project depth ──────────────

def test_staging_root_is_shallow_and_outside_the_project_tree(
    monkeypatch, tmp_path: Path
):
    monkeypatch.setattr(extractor, "global_cache_root", lambda: tmp_path / "cache")
    staging = extractor._staging_root()
    assert staging == tmp_path / "cache" / ".staging"
    assert staging.is_dir()  # created on demand
    # It must NOT live under a projects/<slug>/… output dir — that's the whole
    # point (depth-independent temp path).
    assert "projects" not in staging.parts


# ── near-MAX_PATH sensor ────────────────────────────────────────────────────

def _reset_warn(monkeypatch):
    monkeypatch.setattr(extractor, "_warned_near_max_path", False)


def test_no_warning_off_windows_even_for_a_long_path(monkeypatch, caplog):
    monkeypatch.setattr(extractor.sys, "platform", "linux")
    _reset_warn(monkeypatch)
    with caplog.at_level("WARNING"):
        extractor._warn_if_near_windows_limit(Path("x" * 400))
    assert not caplog.records


def test_warns_once_on_windows_near_the_limit(monkeypatch, caplog):
    monkeypatch.setattr(extractor.sys, "platform", "win32")
    _reset_warn(monkeypatch)
    long_path = Path("C:/" + "d" * 250)  # >= _WIN_PATH_WARN_AT
    with caplog.at_level("WARNING"):
        extractor._warn_if_near_windows_limit(long_path)
        extractor._warn_if_near_windows_limit(long_path)  # second call: silent
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "MAX_PATH" in warnings[0].getMessage()


def test_no_warning_on_windows_for_a_short_path(monkeypatch, caplog):
    monkeypatch.setattr(extractor.sys, "platform", "win32")
    _reset_warn(monkeypatch)
    with caplog.at_level("WARNING"):
        extractor._warn_if_near_windows_limit(Path(r"C:\short\path.xml"))
    assert not caplog.records


# ── failure diagnosis ───────────────────────────────────────────────────────

def test_long_path_hint_empty_off_windows(monkeypatch):
    monkeypatch.setattr(extractor.sys, "platform", "linux")
    assert extractor._windows_long_path_hint(Path("x" * 400)) == ""


def test_long_path_hint_empty_for_short_windows_path(monkeypatch):
    monkeypatch.setattr(extractor.sys, "platform", "win32")
    assert extractor._windows_long_path_hint(Path(r"C:\short.xml")) == ""


def test_long_path_hint_explains_over_length_windows_path(monkeypatch):
    monkeypatch.setattr(extractor.sys, "platform", "win32")
    hint = extractor._windows_long_path_hint(Path("C:/" + "d" * 300))
    assert "MAX_PATH" in hint
    assert "LVKIT_CACHE_DIR" in hint
    assert str(303) in hint  # len("C:/" + "d"*300) == 303
