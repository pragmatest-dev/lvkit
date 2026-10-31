"""Task #76: SubVI click-navigation identity attribute.

The renderer emits an INERT ``data-lv-vi-rel`` data attribute on a resolvable
SubVI node's SVG group — a relative (POSIX) path from the rendered VI's own
directory to the SubVI's source file. No links, no click JS, no VS Code
assumptions live in the renderer; the VS Code extension (editors/vscode/
extension.js) supplies the click behavior on top of this identity payload.

See ``lvkit.render.nodes.resolve_subvi_source`` (the shared resolution chain,
also used for SubVI icons) and ``lvkit.render.scene._subvi_rel_path`` (the
project-local relpath gate).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi

# Lives in the same directory as its SubVI calls (Get Tag Content.vi, Get
# Children.vi, Get Attributes.vi, Parse XML.vi, Draw Tree from XML.vi) — the
# paths it records for them resolve as project-local siblings.
RESOLVABLE_SUBVI_VI = Path(
    ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/"
    "test TCX read (installed 71).vi"
)
# Calls SubVIs (the VIPM API: "Apply VIPC File_vipm_api.vi", "Build VI
# Package_vipm_api.vi", "Test VIPC Apply Needed_vipm_api.vi") that aren't
# anywhere in the local sample corpus and have no configured vi.lib/user.lib
# root — genuinely unresolvable, the negative case.
UNRESOLVABLE_SUBVI_VI = Path(".lvkit/cache/samples/JKI-EasyXML/Build.vi")


def _render(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"sample VI not available: {path}")
    graph = InMemoryVIGraph()
    try:
        graph.load_vi(path, mode=LoadMode.NONE)
    except Exception as exc:  # pragma: no cover - corpus load gaps
        pytest.skip(f"failed to load {path}: {exc}")
    vi = graph.resolve_vi_name(path.name)
    svg = render_vi(graph, vi)
    if svg is None:
        pytest.skip("sample lacks required diagram geometry")
    return svg


@pytest.mark.needs_samples
def test_resolvable_subvi_emits_relative_posix_path():
    svg = _render(RESOLVABLE_SUBVI_VI)
    matches = re.findall(r'data-lv-vi-rel="([^"]*)"', svg)
    assert matches, "expected at least one data-lv-vi-rel attribute"
    assert "Parse XML.vi" in matches
    for rel in matches:
        # Relative, POSIX-separated, never an absolute anchor or drive letter
        # (task #76's portability requirement — a rendered SVG may be saved/
        # shared, so an absolute machine path would be wrong there).
        assert not rel.startswith("/")
        assert ":" not in rel
        assert "\\" not in rel
        # The real sibling file actually exists relative to the caller's dir.
        assert (RESOLVABLE_SUBVI_VI.parent / rel).is_file()


@pytest.mark.needs_samples
def test_subvi_link_through_a_symlinked_folder_stays_a_sibling(tmp_path: Path):
    """A project opened through a symlinked folder links each SubVI by its real
    location relative to the caller, not by climbing out of the symlink."""
    if not RESOLVABLE_SUBVI_VI.exists():
        pytest.skip(f"sample VI not available: {RESOLVABLE_SUBVI_VI}")
    link = tmp_path / "linked"
    try:
        link.symlink_to(RESOLVABLE_SUBVI_VI.parent.resolve(), target_is_directory=True)
    except OSError as e:  # Windows needs admin rights or Developer Mode
        pytest.skip(f"this OS refuses to create symlinks here: {e}")
    svg = _render(link / RESOLVABLE_SUBVI_VI.name)
    matches = re.findall(r'data-lv-vi-rel="([^"]*)"', svg)
    assert "Parse XML.vi" in matches
    for rel in matches:
        assert not rel.startswith("..")
        assert (link / rel).is_file()


@pytest.mark.needs_samples
def test_unresolvable_subvi_omits_the_attribute():
    svg = _render(UNRESOLVABLE_SUBVI_VI)
    assert "data-lv-vi-rel" not in svg


def test_caller_relative_subvi_resolves_same_library_local_ref(tmp_path: Path):
    """A SubVI call to a VI in the CALLER's OWN library/class is stored by
    LabVIEW as a bare LOCAL ref (``/Do.vi``), not a fully-qualified path. It must
    resolve to the caller's own sibling — not collide with a same-named VI in
    another library (#29). Cross-library refs (``///Lib1/Class/Do.vi``, whose
    leading empties are ``..`` hops from the caller file) still resolve too."""
    from types import SimpleNamespace
    from typing import cast

    from lvkit.graph.models import VINode
    from lvkit.render.nodes import _resolve_caller_relative_vi

    for top in ("Lib1", "Lib2"):
        (tmp_path / top / "Class").mkdir(parents=True)
        (tmp_path / top / "Class" / "Do.vi").write_bytes(b"")
    caller = tmp_path / "Lib2" / "Class" / "Test.vi"
    caller.write_bytes(b"")

    def node(qp: str) -> VINode:
        # a SubVI call node: only .qualified_path and .id (vi_key::uid) are read
        return cast(VINode, SimpleNamespace(qualified_path=qp, id=f"{caller}::42"))

    # same-class local ref -> the caller's OWN sibling (Lib2), NOT Lib1
    assert _resolve_caller_relative_vi(node("/Do.vi")) == tmp_path / "Lib2/Class/Do.vi"
    # cross-library ref: 3 '..' from the caller file reach the root, then descend
    assert (
        _resolve_caller_relative_vi(node("///Lib1/Class/Do.vi"))
        == tmp_path / "Lib1/Class/Do.vi"
    )
    # a <vilib> token is not caller-relative -> None (resolved elsewhere)
    assert _resolve_caller_relative_vi(node("<vilib>/Utility/error.llb/Foo.vi")) is None
