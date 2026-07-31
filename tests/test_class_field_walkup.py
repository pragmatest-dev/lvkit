"""Fix A: resolve a class's unbundle-by-name field names when the owning
`.lvclass` isn't on a search path (e.g. a member VI in a `protected/` subfolder,
class one dir up). Walk up to find it and INTERFACE-load its fields only — no
method VIs — so `get_type_fields` resolves without the expensive method tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import PrimitiveNode

# A class member VI whose .lvclass sits one directory up (subfolder case).
SUBFOLDER_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Ant Plugin/Source/"
    "TextTestRunner.Ant/protected/processResult.vi"
)


def test_walk_up_find_locates_ancestor(tmp_path: Path) -> None:
    """_walk_up_find climbs to an ancestor directory, is bounded, and misses
    cleanly — pure filesystem, no parsing."""
    g = InMemoryVIGraph()
    target = tmp_path / "Owner.lvclass"
    target.write_text("stub")
    deep = tmp_path / "sub" / "protected"
    deep.mkdir(parents=True)

    found = g._walk_up_find(deep, "Owner.lvclass")
    assert found is not None and found.resolve() == target.resolve()

    assert g._walk_up_find(deep, "Nope.lvclass") is None
    # Bounded: with too few levels it can't reach the ancestor.
    assert g._walk_up_find(deep, "Owner.lvclass", max_levels=1) is None


def test_subfolder_class_fields_resolve_without_methods() -> None:
    """Auto-load (search path defaults to vi.parent) of a subfolder member VI
    still resolves its class field names via walk-up, and pulls in NO method
    VIs (the field-only load is free)."""
    if not SUBFOLDER_VI.exists():
        pytest.skip(f"Sample VI not available: {SUBFOLDER_VI}")

    g = InMemoryVIGraph()
    g.load_vi(SUBFOLDER_VI, mode=LoadMode.FULL, search_paths=None)

    # The owning class resolves its private-data fields...
    fields = g.get_class_fields("TextTestRunner.JUnitXML.lvclass")
    assert fields is not None
    assert [f.name for f in fields] == ["Report Path"]

    # ...and the field-only load is a placeholder, not a full class load:
    node = g._dep_graph.nodes["TextTestRunner.JUnitXML.lvclass"]
    assert node.get("fields_only") is True
    # The ONLY method VI of that class in the graph is the target VI itself
    # (processResult.vi is a method) — the field-only load pulled in NONE of the
    # class's OTHER methods.
    class_method_vis = [
        n for n in g._dep_graph.nodes
        if n.startswith("TextTestRunner.JUnitXML.lvclass:")
    ]
    assert class_method_vis == ["TextTestRunner.JUnitXML.lvclass:processResult.vi"]

    # Every by-name node on the diagram that targets a class now resolves names.
    for nid in list(g._graph.nodes):
        n = g._graph.nodes[nid].get("node")
        if isinstance(n, PrimitiveNode) and n.node_type in ("nMux", "mux", "demux"):
            agg = next(
                (t for t in n.terminals if getattr(t, "nmux_role", None) == "agg"),
                None,
            )
            if agg and agg.lv_type and agg.lv_type.classname:
                assert g.get_type_fields(agg.lv_type), (
                    f"unresolved fields for {agg.lv_type.classname}"
                )


# Fix B: a class whose private data is a CONTROL (.ctl) typedef — NOT an inline
# "class private data" cluster in a method VI's VCTP — has its fields read
# straight from that control. The .ctl file may differ from the class's recorded
# logical name (LabVIEW logical "<Class>.ctl" vs on-disk "Data.ctl").
_MC_REPO = ".lvkit/cache/samples/measurement-plugin-labview"
_MC_DIR = (
    "Source/Runtime/MeasurementLink Measurement Server/Classes/MeasurementContext"
)
_MC_REF = "0577695d"
_MC_CLASS = (
    "MeasurementLink Measurement Server.lvlib:MeasurementContext.lvclass"
)
_MC_FIELDS = [
    "PinMapContext", "gRPCServerId", "IMeasurementService",
    "MeasurementPluginService", "reserved session infos",
]


def _extract_class_dir(dest: Path) -> Path | None:
    """git-extract the MeasurementContext class dir (VIs + .lvclass + Data.ctl)
    into ``dest`` — the sample repo may be git-only with no working tree."""
    if not (Path(_MC_REPO) / ".git").exists():
        return None
    listed = subprocess.run(
        ["git", "-C", _MC_REPO, "ls-tree", "--name-only", f"{_MC_REF}:{_MC_DIR}"],
        capture_output=True, text=True,
    )
    if listed.returncode != 0 or not listed.stdout.strip():
        return None
    cdir = dest / "MeasurementContext"
    cdir.mkdir()
    for name in listed.stdout.split("\n"):
        if not name or name.endswith("/"):
            continue
        blob = subprocess.run(
            ["git", "-C", _MC_REPO, "show", f"{_MC_REF}:{_MC_DIR}/{name}"],
            capture_output=True,
        )
        if blob.returncode == 0 and blob.stdout:
            (cdir / name).write_bytes(blob.stdout)
    return cdir


@pytest.mark.parametrize("mode", [LoadMode.MINIMAL, LoadMode.FULL])
def test_ctl_private_data_class_fields_resolve(tmp_path: Path, mode) -> None:
    cdir = _extract_class_dir(tmp_path)
    if cdir is None:
        pytest.skip("measurement-plugin-labview sample repo not present")

    g = InMemoryVIGraph()
    g.load_vi(cdir / "Create.vi", mode=mode)

    # The class's private data is the Data.ctl control — its fields resolve
    # (previously 0, because the inline-VCTP hunt found nothing).
    fields = g.get_class_fields(_MC_CLASS)
    assert fields is not None
    assert [f.name for f in fields] == _MC_FIELDS

    # And a Bundle/Unbundle-By-Name on that class object now resolves its type
    # fields (the nMux delta / wire label can name the field instead of [idx]).
    for nid in list(g._graph.nodes):
        n = g._graph.nodes[nid].get("node")
        if isinstance(n, PrimitiveNode) and n.node_type in ("nMux", "mux", "demux"):
            agg = next(
                (t for t in n.terminals if getattr(t, "nmux_role", None) == "agg"),
                None,
            )
            if agg and agg.lv_type and agg.lv_type.classname == _MC_CLASS:
                assert g.get_type_fields(agg.lv_type) == fields
