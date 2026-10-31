"""The bundled vi.lib catalog is read-only at runtime (issue #164).

Terminals a caller wires that the catalog has no index for are learned in
memory, per resolver instance, so generated code uses them -- and nothing
under the data directory is written.
"""

from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest

from lvkit import vilib_resolver
from lvkit._data import data_dir
from lvkit.codegen.builder import build_module
from lvkit.graph.models import Constant, VIContext, VINode, Wire
from lvkit.models import LVType, LVTypeKind, Terminal
from lvkit.vilib_resolver import VILibResolver, get_resolver, own_resolver
from tests.helpers import build_graph

_CATALOG_ENTRY = {
    "name": "Learn Me",
    "doc_url": "https://example.invalid/learn-me.html",
    "category": "Test",
    "description": "Café © description kept verbatim.",
    "status": "docs_enriched",
    "terminals": [
        {
            "name": "Path",
            "index": None,
            "direction": "in",
            "python_param": "file_path",
            "description": "Path is the file to open.",
        },
        {
            "name": "Mode",
            "index": 1,
            "direction": "in",
            "python_param": "mode",
        },
    ],
}


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _set_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(stat.S_IRUSR | (stat.S_IXUSR if path.is_dir() else 0))
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)


def _set_writable(root: Path) -> None:
    root.chmod(stat.S_IRWXU)
    for path in root.rglob("*"):
        path.chmod(stat.S_IRWXU)


@pytest.fixture
def read_only_catalog(tmp_path: Path) -> Iterator[Path]:
    """A small catalog on disk whose files and directories are read-only."""
    root = tmp_path / "data"
    vilib = root / "vilib"
    vilib.mkdir(parents=True)
    (vilib / "_index.json").write_text(
        json.dumps({"categories": {"Test": "test.json"}}), encoding="utf-8"
    )
    (vilib / "test.json").write_text(
        json.dumps({"entries": [_CATALOG_ENTRY]}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _set_read_only(root)
    yield root
    _set_writable(root)


def _caller(
    wired: Terminal, value: str | float, vi: str = "Learn Me.vi"
) -> tuple[VIContext, VINode]:
    node = VINode(
        id="op:1",
        vi_path="Caller.vi",
        name=vi,
        qualified_path=f"<vilib>/Test/{vi}",
        terminals=[wired],
    )
    context = VIContext(
        name="Caller.vi",
        constants=[Constant(id="const:1", value=value)],
        data_flow=[
            Wire.from_terminals(
                from_terminal_id="const:1",
                to_terminal_id=wired.id,
                from_parent_kind="constant",
                to_parent_kind="vi",
            )
        ],
    )
    return context, node


def _generate(
    resolver: VILibResolver,
    wired: Terminal,
    value: str | float,
    vi: str = "Learn Me.vi",
) -> str:
    vilib_resolver._resolver = resolver
    context, node = _caller(wired, value, vi)
    graph = build_graph(context, context.name, [node])
    return build_module(context, context.name, graph=graph)


@pytest.fixture(autouse=True)
def _restore_resolver() -> Iterator[None]:
    saved = vilib_resolver._resolver
    yield
    vilib_resolver._resolver = saved


def test_learned_terminal_is_used_and_nothing_is_written(
    read_only_catalog: Path,
) -> None:
    before = _tree_digest(read_only_catalog)
    wired = Terminal(id="t:path", index=4, direction="input", name="Path")
    resolver = VILibResolver(data_dir=read_only_catalog)

    source = _generate(resolver, wired, "a.txt")

    assert "file_path='a.txt'" in source
    learned = resolver.resolve_by_name("Learn Me.vi")
    assert learned is not None
    assert [t.index for t in learned.terminals] == [4, 1]
    assert learned.terminals[0].direction == "in"
    assert _tree_digest(read_only_catalog) == before


def test_catalog_on_disk_keeps_docs_and_encoding(read_only_catalog: Path) -> None:
    category = read_only_catalog / "vilib" / "test.json"
    before = category.read_bytes()
    wired = Terminal(id="t:path", index=4, direction="input", name="Path")

    _generate(VILibResolver(data_dir=read_only_catalog), wired, "a.txt")

    assert category.read_bytes() == before
    entry = json.loads(before.decode("utf-8"))["entries"][0]
    assert entry["doc_url"] == _CATALOG_ENTRY["doc_url"]
    assert entry["terminals"][0]["direction"] == "in"
    assert entry["terminals"][0]["description"] == "Path is the file to open."
    assert "©" in before.decode("utf-8")


def test_conflicting_observation_becomes_a_variant_in_memory(
    read_only_catalog: Path,
) -> None:
    before = _tree_digest(read_only_catalog)
    resolver = VILibResolver(data_dir=read_only_catalog)
    other_name = Terminal(id="t:mode", index=1, direction="input", name="Open Mode")

    resolver.auto_update_terminals("Learn Me.vi", [other_name], caller_vi="Caller.vi")

    match = resolver.find_matching_variant(
        "Learn Me.vi", {1: {"name": "Open Mode", "direction": "in", "type": None}}
    )
    assert match is not None
    assert match.is_variant
    assert [(t.index, t.name) for t in match.terminals] == [(1, "Open Mode")]
    assert _tree_digest(read_only_catalog) == before


def test_learned_typedef_is_registered_in_memory_only(
    read_only_catalog: Path,
) -> None:
    before = _tree_digest(read_only_catalog)
    resolver = VILibResolver(data_dir=read_only_catalog)
    typedef = LVType(
        kind=LVTypeKind.TYPEDEF_REF, typedef_path="vi.lib/Test/Learned Type.ctl"
    )
    wired = Terminal(
        id="t:path", index=4, direction="input", name="Path", lv_type=typedef
    )

    resolver.auto_update_terminals("Learn Me.vi", [wired])

    assert resolver.resolve_type("vi.lib/Test/Learned Type.ctl") is not None
    assert _tree_digest(read_only_catalog) == before


def test_learning_stays_inside_one_resolver_instance(read_only_catalog: Path) -> None:
    wired = Terminal(id="t:path", index=4, direction="input", name="Path")
    learner = VILibResolver(data_dir=read_only_catalog)
    learner.auto_update_terminals("Learn Me.vi", [wired])

    fresh = VILibResolver(data_dir=read_only_catalog)
    entry = fresh.resolve_by_name("Learn Me.vi")

    assert entry is not None
    assert entry.terminals[0].index is None


def test_bundled_catalog_is_untouched_by_generation() -> None:
    bundled = data_dir()
    before = _tree_digest(bundled)
    wired = Terminal(id="t:size", index=7, direction="input", name="Size")

    source = _generate(VILibResolver(), wired, 12.0, vi="Get Text Rect.vi")

    assert "size=12.0" in source
    assert _tree_digest(bundled) == before


def test_what_generation_learns_stays_in_its_run() -> None:
    outside = get_resolver()
    entry = outside.resolve_by_name("Inverse FFT.vi")
    assert entry is not None
    assert [t.index for t in entry.terminals if t.name == "shift?"] == [None]
    wired = Terminal(id="t:s", index=2, direction="input", name="shift?")
    with own_resolver() as run:
        assert get_resolver() is run
        learned = run.auto_update_terminals("Inverse FFT.vi", [wired])
        assert [t.index for t in learned.terminals if t.name == "shift?"] == [2]
    assert get_resolver() is outside
    assert [t.index for t in entry.terminals if t.name == "shift?"] == [None]


def test_an_unnamed_terminal_number_is_not_guessed(read_only_catalog: Path) -> None:
    resolver = VILibResolver(data_dir=read_only_catalog)
    wired = Terminal(id="t:x", index=9, direction="input", name="Other Name")
    entry = resolver.auto_update_terminals("Learn Me.vi", [wired])
    assert 9 not in [t.index for t in entry.terminals]
