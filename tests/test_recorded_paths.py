"""A dependency's recorded path is resolved once, against its caller's resolved
path, as it is loaded; placement and the vi.lib catalog read that one value, and
load order cannot change it."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.extract_pool import ExtractionPool
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.recorded_paths import RecordedPaths
from lvkit.load_mode import LoadMode
from lvkit.pipeline import generate_python
from tests.test_call_identity import _static, _Tree

SYSDIR = ["<vilib>", "Utility", "sysdir.llb", "Get System Directory.vi"]
SYSDIR_PATH = "/".join(SYSDIR)
LIB = "vilib/Utility/sysdir.llb"


def _tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str) -> _Tree:
    """A vi.lib VI ``sibling`` that the catalog does not describe, calling the
    catalog VI beside it by a path relative to itself, and three project callers
    that reach the two in each order."""
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    (built.root / "proj" / ".lvkit").mkdir(parents=True)
    other = ["<vilib>", "Utility", "sysdir.llb", sibling]
    built.vi(f"{LIB}/deep.vi", "deep.vi", [])
    built.vi(
        f"{LIB}/helper.vi", "helper.vi", [], [_static(["deep.vi"], ["", "deep.vi"])]
    )
    built.vi(
        f"{LIB}/Get System Directory.vi",
        "Get System Directory.vi",
        [],
        [_static(["helper.vi"], ["", "helper.vi"])],
    )
    built.vi(
        f"{LIB}/{sibling}",
        sibling,
        [],
        [_static(["Get System Directory.vi"], ["", "Get System Directory.vi"])],
    )
    built.vi("proj/only_sibling.vi", "only_sibling.vi", [], [_static([sibling], other)])
    built.vi(
        "proj/sysdir_first.vi",
        "sysdir_first.vi",
        [],
        [_static(["Get System Directory.vi"], SYSDIR), _static([sibling], other)],
    )
    built.vi(
        "proj/sibling_first.vi",
        "sibling_first.vi",
        [],
        [_static([sibling], other), _static(["Get System Directory.vi"], SYSDIR)],
    )
    return built


def _modules(tree: _Tree, tmp_path: Path, caller: str) -> list[str]:
    out = tmp_path / "gen" / caller
    generate_python(
        tree.root / "proj" / f"{caller}.vi",
        out,
        search_paths=[tree.root / "proj"],
        vilib_root=tree.root / "vilib",
    )
    return sorted(p.relative_to(out).as_posix() for p in out.rglob("*.py"))


@pytest.mark.parametrize("sibling", ["other.vi", "Aother.vi"])
@pytest.mark.parametrize("caller", ["only_sibling", "sysdir_first", "sibling_first"])
def test_a_sibling_call_of_a_catalog_vi_uses_the_catalog_whatever_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sibling: str, caller: str
) -> None:
    tree = _tree(tmp_path, monkeypatch, sibling)
    modules = _modules(tree, tmp_path, caller)
    assert any(m.endswith("get_system_directory.py") for m in modules)
    assert not [m for m in modules if m.endswith(("helper.py", "deep.py"))]


def test_every_order_gives_the_same_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path, monkeypatch, "Aother.vi")

    def library_modules(caller: str) -> list[str]:
        return [
            m.split("/", 1)[1]
            for m in _modules(tree, tmp_path, caller)
            if "/vilib/" in m
        ]

    assert library_modules("sysdir_first") == library_modules("sibling_first")
    assert library_modules("sysdir_first")


def test_an_absent_catalog_vi_beside_its_caller_uses_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    (built.root / "proj" / ".lvkit").mkdir(parents=True)
    other = ["<vilib>", "Utility", "sysdir.llb", "Aother.vi"]
    built.vi(
        f"{LIB}/Aother.vi",
        "Aother.vi",
        [],
        [_static(["Get System Directory.vi"], ["", "Get System Directory.vi"])],
    )
    built.vi("proj/caller.vi", "caller.vi", [], [_static(["Aother.vi"], other)])
    out = tmp_path / "gen"
    result = generate_python(
        built.root / "proj" / "caller.vi",
        out,
        search_paths=[built.root / "proj"],
        vilib_root=built.root / "vilib",
    )
    source = next(out.rglob("aother.py")).read_text()
    assert "NotImplementedError" not in source
    assert "get_system_directory" in source
    assert not result["gaps"]


def test_the_catalog_path_is_the_resolved_path_of_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _tree(tmp_path, monkeypatch, "Aother.vi")
    graph = InMemoryVIGraph()
    graph.set_library_roots(vilib_root=tree.root / "vilib")
    graph.load_vi(tree.root / "proj" / "only_sibling.vi", LoadMode.FULL)
    sysdir = next(k for k in graph.list_vis() if Path(k).name.startswith("Get System"))
    assert graph.recorded_path(sysdir) == SYSDIR_PATH


def _recorded(order: list[list[str]]) -> tuple[str | None, str | None]:
    """A file ``/x/A.vi`` recorded by two callers at different installation paths,
    and ``/x/z.vi`` that A records relative to itself."""
    recorded = RecordedPaths(_graph())
    recorded.record("/x/z.vi", "/x/A.vi", ["", "z.vi"])
    for tokens in order:
        recorded.record("/x/A.vi", None, tokens)
    return recorded.path("/x/A.vi"), recorded.path("/x/z.vi")


def _graph():
    import networkx as nx

    return nx.DiGraph()


def test_two_installation_paths_for_one_file_do_not_depend_on_order() -> None:
    first = ["<userlib>", "a", "A.vi"]
    second = ["<userlib>", "a", "C", "A.vi"]
    one = _recorded([first, second])
    other = _recorded([second, first])
    assert one == other == ("<userlib>/a/A.vi", "<userlib>/a/z.vi")


def test_a_gap_warning_shows_the_recorded_path_not_an_absolute_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    built = _Tree(tmp_path / "src")
    monkeypatch.setattr("lvkit.graph.loading.extract_vi_xml", built.extract)
    monkeypatch.setattr(ExtractionPool, "prefetch", lambda self, paths: None)
    (built.root / "proj" / ".lvkit").mkdir(parents=True)
    built.vi(
        f"{LIB}/Aother.vi",
        "Aother.vi",
        [],
        [_static(["Not There.vi"], ["", "Not There.vi"])],
    )
    other = ["<vilib>", "Utility", "sysdir.llb", "Aother.vi"]
    built.vi("proj/caller.vi", "caller.vi", [], [_static(["Aother.vi"], other)])
    generate_python(
        built.root / "proj" / "caller.vi",
        tmp_path / "gen",
        search_paths=[built.root / "proj"],
        vilib_root=built.root / "vilib",
    )
    warnings = [
        line
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("warning:")
    ]
    assert warnings
    assert warnings[0].startswith("warning: <vilib>/Utility/sysdir.llb/Aother.vi:")
    assert str(tmp_path) not in "\n".join(warnings)


def test_a_library_member_is_looked_up_in_the_catalog_by_its_own_file() -> None:
    from lvkit.graph.recorded_paths import member_tokens
    from lvkit.vilib_resolver import get_resolver

    recorded = ["<vilib>", "Utility", "sysdir.llb", "Sysdir.lvlib"]
    tokens = member_tokens(recorded, "Get System Directory.vi")
    assert tokens == SYSDIR
    assert get_resolver().has_python("/".join(tokens))
    assert not get_resolver().has_python("/".join(recorded))
