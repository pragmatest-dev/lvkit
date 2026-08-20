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
# default search path (a VI's own parent directory — see
# graph/loading.py::load_vi) resolves them all as project-local siblings.
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
def test_unresolvable_subvi_omits_the_attribute():
    svg = _render(UNRESOLVABLE_SUBVI_VI)
    assert "data-lv-vi-rel" not in svg


def test_locate_vi_file_disambiguates_same_name_across_libraries(tmp_path: Path):
    """Two SubVIs sharing a bare filename in sibling libraries (Lib1/Do.vi vs
    Lib2/Do.vi) must EACH resolve to their own file via the caller's
    qualified_path. A bare-name lookup collides — both resolve to whichever the
    rglob saw first — which gave both nodes the SAME icon (the reported bug).
    Hermetic: locate_vi_file only rglobs the search paths, never parses, so empty
    marker files suffice."""
    for lib in ("Lib1", "Lib2"):
        (tmp_path / lib).mkdir()
        (tmp_path / lib / "Do.vi").write_bytes(b"")
    graph = InMemoryVIGraph()
    graph._search_paths = [tmp_path]

    # qualified_path picks the RIGHT library's file, per node.
    assert graph.locate_vi_file("Do.vi", "/Lib1/Do.vi") == tmp_path / "Lib1" / "Do.vi"
    assert graph.locate_vi_file("Do.vi", "/Lib2/Do.vi") == tmp_path / "Lib2" / "Do.vi"
    # Windows-style separators in the token resolve the same.
    assert graph.locate_vi_file("Do.vi", "\\Lib2\\Do.vi") == tmp_path / "Lib2" / "Do.vi"
    # No qualified_path: unchanged bare-name behavior (deterministic single hit).
    assert graph.locate_vi_file("Do.vi") in (
        tmp_path / "Lib1" / "Do.vi",
        tmp_path / "Lib2" / "Do.vi",
    )


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


def test_locate_vi_file_resolves_full_path_not_just_tail(tmp_path: Path):
    """Resolution keys on the WHOLE project-relative path, not a parent/name tail:
    two VIs sharing filename AND parent-dir name but different full paths
    (A/sub/Do.vi vs B/sub/Do.vi) each resolve exactly."""
    for top in ("A", "B"):
        (tmp_path / top / "sub").mkdir(parents=True)
        (tmp_path / top / "sub" / "Do.vi").write_bytes(b"")
    graph = InMemoryVIGraph()
    graph._search_paths = [tmp_path]
    assert graph.locate_vi_file("Do.vi", "/A/sub/Do.vi") == tmp_path / "A/sub/Do.vi"
    assert graph.locate_vi_file("Do.vi", "/B/sub/Do.vi") == tmp_path / "B/sub/Do.vi"
