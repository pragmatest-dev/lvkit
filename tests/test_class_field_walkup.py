"""Fix A: resolve a class's unbundle-by-name field names for a member VI in a
`protected/` subfolder (class one dir up). The VI records its class's path
(``VILB``), the class is found there and a MINIMAL load INTERFACE-loads its
fields only — no method VIs — so `get_type_fields` resolves without the
expensive method tree.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import type_label
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import PrimitiveNode
from tests.helpers import vi_key

# A class member VI whose .lvclass sits one directory up (subfolder case).
SUBFOLDER_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Ant Plugin/Source/"
    "TextTestRunner.Ant/protected/processResult.vi"
)


def test_subfolder_class_fields_resolve_without_methods() -> None:
    """A MINIMAL load of a subfolder member VI resolves its class field names
    through the class path the VI records, and pulls in NO method VIs (the
    field-only load is free)."""
    if not SUBFOLDER_VI.exists():
        pytest.skip(f"Sample VI not available: {SUBFOLDER_VI}")

    g = InMemoryVIGraph()
    g.load_vi(SUBFOLDER_VI, mode=LoadMode.MINIMAL)

    # The owning class resolves its private-data fields...
    class_key = vi_key(g, "TextTestRunner.JUnitXML.lvclass")
    fields = g.get_class_fields(class_key)
    assert fields is not None
    assert [f.name for f in fields] == ["Report Path"]

    # ...and the field-only load is a placeholder, not a full class load. The
    # class node is keyed by PATH now — resolve the qname to its key first.
    node = g._dep_graph.nodes[class_key]
    assert node.get("fields_only") is True
    # The ONLY method VI of that class in the graph is the target VI itself
    # (processResult.vi is a method) — the field-only load pulled in NONE of the
    # class's OTHER methods.
    # VIs are keyed by path (identity) now; find loaded methods of this class
    # via the qname reverse index (loaded-VI qnames) — same intent as the old
    # dep_graph qname-prefix scan.
    class_method_vis = [
        q
        for _key, q in g._dep_graph.nodes(data="qname")
        if q
        and q.startswith("TextTestRunner.JUnitXML.lvclass:") and q.endswith(".vi")
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


# A class whose private data is its CONTROL (``MeasurementContext.ctl``, kept
# inside the source-only ``.lvclass``) has its fields read from that control.
# Here the control holds one field, a Data Value Reference to ``Data.ctl``'s
# cluster: the class's own field is the reference, as a Bundle/Unbundle By Name
# on the class wire names it.
_MC_REPO = ".lvkit/cache/samples/measurement-plugin-labview"
_MC_DIR = "Source/Runtime/MeasurementLink Measurement Server/Classes/MeasurementContext"
_MC_REF = "0577695d"
_MC_CLASS = "MeasurementLink Measurement Server.lvlib:MeasurementContext.lvclass"
_MC_FIELDS = ["DataDVR"]


def _extract_class_dir(dest: Path, ref: str = _MC_REF) -> Path | None:
    """git-extract the MeasurementContext class dir (VIs + .lvclass + Data.ctl)
    into ``dest`` — the sample repo may be git-only with no working tree."""
    if not (Path(_MC_REPO) / ".git").exists():
        return None
    listed = subprocess.run(
        ["git", "-C", _MC_REPO, "ls-tree", "--name-only", f"{ref}:{_MC_DIR}"],
        capture_output=True,
        text=True,
    )
    if listed.returncode != 0 or not listed.stdout.strip():
        return None
    cdir = dest / "MeasurementContext"
    cdir.mkdir()
    for name in listed.stdout.split("\n"):
        if not name or name.endswith("/"):
            continue
        blob = subprocess.run(
            ["git", "-C", _MC_REPO, "show", f"{ref}:{_MC_DIR}/{name}"],
            capture_output=True,
        )
        if blob.returncode == 0 and blob.stdout:
            (cdir / name).write_bytes(blob.stdout)
    return cdir


# BEFORE the Data.ctl refactor (ref 7a8c33b8): the class private data lives
# ONLY as the flattened NI.LVClass.FlattenedPrivateDataCTL property in the XML
# source-only `.lvclass` — no method VI flattens the cluster into its VCTP, and
# there is no `.ctl` file on disk. These are the class's OWN (pre-refactor)
# fields, which genuinely differ from the post-refactor set above.
_MC_REF_BEFORE = "7a8c33b8"
_MC_FIELDS_BEFORE = ["pin map context", "gRPC server call"]


def test_source_only_lvclass_flattened_private_data(tmp_path: Path) -> None:
    """A source-only `.lvclass` whose private data survives only as the
    flattened CTL property resolves its (pre-refactor) fields — recovered
    straight from the class XML, with no `.ctl` file and no method VI carrying
    the cluster.  Guards the flattened-CTL fallback in `load_lvclass`.
    """
    cdir = _extract_class_dir(tmp_path, ref=_MC_REF_BEFORE)
    if cdir is None:
        pytest.skip("measurement-plugin-labview sample repo not present")
    assert not (cdir / "Data.ctl").exists()  # the whole point: no .ctl on disk

    g = InMemoryVIGraph()
    g.load_vi(cdir / "Create.vi", mode=LoadMode.MINIMAL)

    fields = g.get_class_fields(vi_key(g, _MC_CLASS))
    assert fields is not None
    assert [f.name for f in fields] == _MC_FIELDS_BEFORE

    # The class-object unbundle stamps real field names instead of '[0]': idx 0
    # is the whole 'pin map context' sub-cluster (its drawer's own type is that
    # cluster) and idx 3 is 'gRPC server call'.
    stamped = {
        t.display_name
        for nid in g._graph.nodes
        if isinstance((n := g._graph.nodes[nid].get("node")), PrimitiveNode)
        and n.node_type == "nMux"
        for t in n.terminals
        if getattr(t, "nmux_role", None) == "list" and t.display_name
    }
    assert {"pin map context", "gRPC server call"} <= stamped


def test_flattened_private_data_is_read_as_the_recorded_control(
    tmp_path: Path,
) -> None:
    """The private-data control a source-only `.lvclass` keeps inside itself is
    read as a control at its path inside the class (`<X.lvclass>/<url>`): the
    class is edged to that path (not to the cache copy that was read), and its
    fields carry the types the control's own type table gives them."""
    cdir = _extract_class_dir(tmp_path, ref=_MC_REF_BEFORE)
    if cdir is None:
        pytest.skip("measurement-plugin-labview sample repo not present")

    g = InMemoryVIGraph()
    g.load_vi(cdir / "Create.vi", mode=LoadMode.MINIMAL)

    class_key = vi_key(g, _MC_CLASS)
    controls = [
        t
        for _, t, e in g._dep_graph.out_edges(class_key, data=True)
        if e.get("rel") == "private_data"
    ]
    inside = cdir.resolve() / "MeasurementContext.lvclass" / "MeasurementContext.ctl"
    assert controls == [str(inside)]
    fields = g.get_class_fields(class_key)
    assert fields is not None
    assert [(f.name, type_label(f.type)) for f in fields] == [
        ("pin map context", "PinMapContext{pin_map_id, sites}"),
        ("gRPC server call", "U64"),
    ]


def test_single_vi_names_own_class_fields(tmp_path: Path) -> None:
    """SINGLE VI, nothing else attached (the online-viewer upload case): a VI
    whose class private-data cluster is labeled with the control's DISPLAY name
    ("measurement context data") rather than "class private data" still names
    its unbundle-by-name fields from its OWN VCTP — the wrapping TypeDef is
    identified by its owner Labels (the class + its .ctl), so no .lvclass is
    needed on the search path. Guards the owner-class + named-control identification in
    `_fields_from_xml`: without it these fields render as raw `[index]`.
    """
    if not (Path(_MC_REPO) / ".git").exists():
        pytest.skip("measurement-plugin-labview sample repo not present")
    vi = tmp_path / "Create.vi"
    blob = subprocess.run(
        ["git", "-C", _MC_REPO, "show", f"{_MC_REF_BEFORE}:{_MC_DIR}/Create.vi"],
        capture_output=True,
    )
    if blob.returncode != 0 or not blob.stdout:
        pytest.skip(f"commit {_MC_REF_BEFORE} not in the local sample clone")
    vi.write_bytes(blob.stdout)

    g = InMemoryVIGraph()
    g.load_vi(vi, mode=LoadMode.MINIMAL)  # single VI: no class dir, no search path
    stamped = {
        t.display_name
        for nid in g._graph.nodes
        if isinstance((n := g._graph.nodes[nid].get("node")), PrimitiveNode)
        and n.node_type == "nMux"
        for t in n.terminals
        if getattr(t, "nmux_role", None) == "list" and t.display_name
    }
    assert {"pin map context", "gRPC server call"} <= stamped


@pytest.mark.parametrize("mode", [LoadMode.MINIMAL, LoadMode.FULL])
def test_ctl_private_data_class_fields_resolve(tmp_path: Path, mode) -> None:
    cdir = _extract_class_dir(tmp_path)
    if cdir is None:
        pytest.skip("measurement-plugin-labview sample repo not present")

    g = InMemoryVIGraph()
    g.load_vi(cdir / "Create.vi", mode=mode)

    # The class's private data is its control's one field, the reference.
    fields = g.get_class_fields(vi_key(g, _MC_CLASS))
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
