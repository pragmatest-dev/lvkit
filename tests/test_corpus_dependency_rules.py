"""Dependency rules checked on real corpus VIs, in every text view.

1. Every class and typedef the VI's link table records is in its ``uses :``.
2. Every subVI call names a ``uses :`` entry, and one name is one VI.
3. No type reads ``None`` in describe or lvnet.

The VIs: the ones each rule broke on before, plus a fixed sample of every
corpus repository (sorted, evenly spaced).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lvkit.graph.describe import describe_vi
from lvkit.graph.models import VINode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.parser.vi import parse_vi
from tests.corpus_graphs import load_corpus_vi

pytestmark = [pytest.mark.slow, pytest.mark.needs_samples]

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
PER_REPO = 8

_REPORTED = [
    "DCAF-DAQModule/source/editor node/Add Channel to Objects.vi",
    "ni-labview-icon-editor/resource/plugins/NIIconEditor/History/"
    "Add Data to History.vi",
    "JKI-EasyXML/Source/Support/Low Level/XML Get Node Data by Variant.vi",
    "JKI-VI-Tester/source/Utilities/Create TestCase Template Instance.vi",
]


def _sampled() -> list[Path]:
    if not SAMPLES.is_dir():
        return []
    picked = [SAMPLES / rel for rel in _REPORTED]
    for repo in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        vis = sorted(repo.rglob("*.vi"))
        step = max(1, len(vis) // PER_REPO)
        picked.extend(vis[::step][:PER_REPO])
    return [p for p in dict.fromkeys(picked) if p.is_file()]


_VIS = _sampled()
_TYPE_FILES = (".ctl", ".lvclass")
# A port typed ``None``: ``name: None`` in a describe signature, ``: None`` at
# the end of an lvnet terminal line. A stored value's ``'field': None`` is a
# value, not a type.
_NONE_TYPE = re.compile(r"(?<!['\"]): None(?=[,)]|$)", re.MULTILINE)


def _id(path: Path) -> str:
    return path.relative_to(SAMPLES).as_posix()


@pytest.mark.parametrize("vi_path", _VIS, ids=_id)
def test_dependency_rules(vi_path: Path) -> None:
    graph, key = load_corpus_vi(vi_path)
    if key not in graph.list_vis():
        pytest.skip("the VI itself does not load; its problem is reported")
    module = build_netlist_from_graph(graph, key)
    uses = module.dependencies

    # 1. Every class and typedef record is listed, by its file.
    used_files = {
        name.casefold()
        for dep in uses
        for name in (
            dep.qualified.rsplit(":", 1)[-1],
            (dep.path or "").rsplit("/", 1)[-1],
        )
    }
    refs = parse_vi(vi_path).metadata.link_path_refs
    own = {tuple(r.path_tokens) for r in refs if r.record == "VILB"}
    missing = sorted(
        {
            r.name
            for r in refs
            if r.record != "VILB"
            and tuple(r.path_tokens) not in own
            and r.name.lower().endswith(_TYPE_FILES)
            and r.name.casefold() not in used_files
        }
    )
    assert not missing, f"link records missing from uses : {missing}"

    # 2. Each call names a uses : entry, and one name is one VI.
    names = {dep.qualified for dep in uses}
    by_name: dict[str, set[str]] = {}
    for node in graph.iter_nodes(key):
        if not isinstance(node, VINode) or node.interface_key is None:
            continue
        if node.interface_key == key:
            continue  # a VI that calls itself
        name = graph.call_qualified_name(node)
        assert name in names, f"call {node.id} names {name}, not in uses :"
        by_name.setdefault(name, set()).add(node.interface_key)
    shared = {n: keys for n, keys in by_name.items() if len(keys) > 1}
    assert not shared, f"one name for several VIs: {shared}"

    # 3. No type reads None.
    for view, text in (
        ("describe", describe_vi(graph, key)),
        ("lvnet", render_lvnet(module, verbose=True)),
    ):
        hits = [line for line in text.splitlines() if _NONE_TYPE.search(line)]
        assert not hits, f"{view} shows a None type: {hits[:3]}"
