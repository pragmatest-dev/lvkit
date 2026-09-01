"""Proxy for the web extension's PROGRESSIVE staging.

Desktop/native tests load a VI with every dependency already on disk, so they
NEVER exercise the loader's absent-file branches — the web-only logic where a dep
whose file isn't staged yet must still be NAMED by its recorded path AND EDGED to
its caller, with a stub key that matches the key a later present load produces.
That logic regresses silently (a fix that passed 1928 present-file tests broke
the web the moment it landed). These tests replay the actual absent->fetch loop
in pure Python and assert it reaches the SAME closure as a single load over the
full tree — the equality that catches under-staging.

The issue29 case is committed, so it runs in CI. The icon-editor case
(inheritance / cross-class dispatch — where the regression actually lived) needs
the local sample corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import progressive_closure, transitive_closure

_ISSUE29 = Path("tests/corpus/issues/29/Test LVKit")
_SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


def test_progressive_matches_single_pass_issue29(tmp_path: Path) -> None:
    """A class-member SubVI (``Do.vi``, called by ``Test.vi``) must be reached by
    the progressive absent->fetch loop exactly as a single full-tree load reaches
    it — committed corpus, so this guards the absent-path logic in CI."""
    entry = _ISSUE29 / "Lib2" / "Class" / "Test.vi"
    single = transitive_closure(entry, _ISSUE29)
    assert single, "issue29 closure should be non-empty"
    progressive = progressive_closure(entry, _ISSUE29, tmp_path / "proj")
    assert progressive == single, (
        f"progressive staging reached {sorted(p.name for p in progressive)} but a "
        f"single load reaches {sorted(p.name for p in single)} — the web "
        "absent-path logic understages vs desktop"
    )


@pytest.mark.needs_samples
def test_progressive_stages_inherited_class_methods(tmp_path: Path) -> None:
    """The case the regression actually lived in: Apply Body Text calls 6 class
    methods, two by INHERITANCE (Layer dispatch through an Icon Framework object).
    The progressive loop must fetch all 6 — not just resolve them when every file
    is already present."""
    root = _SAMPLES / "ni-labview-icon-editor"
    entry = (
        root
        / "vi.lib/LabVIEW Icon API/lv_icon/Classes/Icon Framework/Apply Body Text.vi"
    )
    if not entry.exists():
        pytest.skip(f"sample absent: {entry}")
    progressive = progressive_closure(entry, root, tmp_path / "proj")
    names = {p.name for p in progressive}
    # All 6 SubVIs (incl. the two INHERITED Layer dispatch methods) + their 3
    # classes must be reached by the absent->fetch loop. This is precisely what a
    # broken absent-path key / missing caller edge / .exists()-gate would drop —
    # the single-pass (desktop) load can't be the oracle here because it also
    # name-searches deps with no recorded path (e.g. the nested LayerType.ctl),
    # which the web can't and doesn't need.
    assert {
        "GET_IconTextClass.vi",
        "SET_IconTextClass.vi",
        "SET_BodyText.vi",
        "CreateBodyText.vi",
        "GET_LayerData.vi",
        "SET_Layer_Data.vi",
        "Icon.lvclass",
        "Icon Framework.lvclass",
        "Layer.lvclass",
    } <= names
