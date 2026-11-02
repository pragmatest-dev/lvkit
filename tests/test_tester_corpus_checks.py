"""The corpus VIs an outside tester reported, each checked in the views the
report named: no stop on a class method stub of the VI's own name, calls named
by their callee's own qualified name, a class named by its file, the unused
connector-pane positions of a call left out of ``components``, catalog ports for
a catalog-only dependency, a literal's local never named from its value, a
private-data control's pylabview warnings captured, and ``.llb`` members placed
under the archive's path."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import render_lvnet
from lvkit.graph.netlist_build import build_netlist_from_graph
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.load_mode import LoadMode
from tests.corpus_graphs import load_corpus_vi

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples")


def _uses(path: Path) -> tuple[str, list[str]]:
    graph, key = load_corpus_vi(path.resolve())
    module = build_netlist_from_graph(graph, key)
    lvnet = render_lvnet(module, display_name=graph.vi_display_name(key))
    return lvnet, [d.qualified for d in module.dependencies]


def test_a_class_method_stub_of_the_vis_own_name_does_not_stop_describe() -> None:
    vi = _SAMPLES / "JKI-EasyXML/Source/Tests/EasyXMLTest/CleanUp.vi"
    graph, key = load_corpus_vi(vi.resolve())
    module = build_netlist_from_graph(graph, key)
    assert "TestCase.lvclass:CleanUp.vi" in [d.qualified for d in module.dependencies]
    assert netlist_to_dict(module, verbose=True)["dependencies"]
    assert "## Dependencies" in describe_vi(graph, key)


def test_a_call_keeps_its_class_in_uses() -> None:
    _, uses = _uses(
        _SAMPLES / "actor-framework/Core/AFDebug/Protected Actor Handle/Ping.vi"
    )
    assert "AF Debug.lvlib:Protected Actor Handle.lvclass" in uses
    assert "Actor Framework.lvlib:Protected Actor Handle.lvclass:Ping.vi" in uses
    assert "Actor Framework.lvlib:Protected Actor Handle.lvclass" not in uses
    _, uses = _uses(
        _SAMPLES
        / "ni-labview-icon-editor/resource/plugins/NIIconEditor/Body Text"
        / "Apply Body Text.vi"
    )
    assert "Icon.lvclass:Apply Body Text.vi" in uses


def test_a_member_is_named_by_its_class_file_in_every_line() -> None:
    folder = _SAMPLES / "DCAF-DAQModule/source/editor node"
    graph = InMemoryVIGraph()
    graph.load_lvclass(folder / "Daqmx Module editor node.lvclass", LoadMode.FULL)
    key = str((folder / "Main Editor UI.vi").resolve())
    lvnet = render_lvnet(
        build_netlist_from_graph(graph, key), display_name=graph.vi_display_name(key)
    )
    assert lvnet.startswith("vi Daqmx Module editor node.lvclass:Main Editor UI.vi :")
    assert "subVI   Daqmx Module editor node.lvclass:Add New Line.vi" in lvnet
    assert (
        "subVI Define_Columns_4311 : Daqmx Module editor node.lvclass:Define Columns.vi"
        in lvnet
    )
    assert "DAQmx Module editor node.lvclass" not in lvnet


def test_unused_pane_positions_are_not_component_ports() -> None:
    vi = _SAMPLES / "DCAF-DAQModule/source/module/execution/output.vi"
    graph, key = load_corpus_vi(vi.resolve())
    components = netlist_to_dict(build_netlist_from_graph(graph, key), verbose=True)[
        "components"
    ]
    (write,) = [
        c
        for c in components
        if c["name"] == "DAQmx Write (Analog 1D DBL NChan 1Samp).vi"
    ]
    assert [p["name"] for p in write["inputs"]] == [
        "task/channels in",
        "auto start",
        "data",
        "timeout",
        "error in",
    ]
    assert [p["name"] for p in write["outputs"]] == [
        "task out",
        "number of samples written per channel",
        "error out",
    ]


def test_a_catalog_only_dependency_shows_its_ports() -> None:
    vi = _SAMPLES / "lv-flex-channel-examples/DAQmx AO/DAQ AO.vi"
    graph, key = load_corpus_vi(vi.resolve())
    lines = describe_vi(graph, key).splitlines()
    assert (
        "  DAQmx Clear Task.vi: (task in: UsrDefndTag refnum{Tag}, error in: Error)"
        " -> (error out: Error) -- Clears (closes) the task."
    ) in lines
    assert (
        "  DAQmx Start Task.vi: (task in: UsrDefndTag refnum{Tag}, error in: Error)"
        " -> (task out: UsrDefndTag refnum{Tag}, error out: Error) -- Starts task."
    ) in lines


def _generate(vi: Path, out: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lvkit", "generate", str(vi), "-o", str(out), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_literal_local_is_named_by_where_it_flows(tmp_path: Path) -> None:
    vi = (
        _SAMPLES
        / "ni-labview-icon-editor/resource/plugins/NIIconEditor"
        / "Get Callers of Icon Editor Packed Library.vi"
    )
    run = _generate(vi, tmp_path)
    (module,) = tmp_path.rglob("get_callers_of_icon_editor_packed_library.py")
    source = module.read_text(encoding="utf-8")
    assert "    symbolic_paths = [Path(" in source
    assert "for i, symbolic_path in enumerate(symbolic_paths):" in source
    names = re.findall(r"^\s*(\w+) = ", source, re.M)
    assert names and max(len(name) for name in names) < 60
    assert "\n    len(" not in source
    assert "/home/" not in run.stdout


def test_private_data_warnings_are_captured() -> None:
    vi = (
        _SAMPLES
        / "Event-Source-Actor/Source/Using Template/Timed Loop Controller"
        / "Timed Loop Controller/Actor Core.vi"
    )
    run = subprocess.run(
        [sys.executable, "-m", "lvkit", "describe", str(vi), "--format", "lvnet"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0
    assert "Block b'" not in run.stderr
    assert "private_data.ctl" not in run.stderr


def test_an_llb_member_is_placed_under_the_archive(tmp_path: Path) -> None:
    root = _SAMPLES / "picosdk-ni-labview-examples"
    vi = root / "ps3000/32-bit-examples/PicoScope3000ExampleBlock.vi"
    run = _generate(vi, tmp_path, "--search-path", str(root), "--no-auto-vilib")
    placed = {
        p.relative_to(tmp_path).as_posix()
        for p in tmp_path.rglob("*.py")
        if p.name != "__init__.py"
    }
    package = "picoscope3000exampleblock/ps3000/var_32_bit_examples"
    assert f"{package}/picoscope3000llb/picoscope3000open.py" in placed
    assert not any("cache" in p for p in placed)
    assert (
        "  [3/4] ps3000/32-bit-examples/PicoScope3000.llb/PicoScope3000Open.vi"
        in run.stdout
    )


def _describe_cli(vi: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "lvkit", "describe", str(vi), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def test_an_unloaded_class_is_named_by_its_file() -> None:
    folder = _SAMPLES / "DCAF-DAQModule/source/editor node"
    run = _describe_cli(folder / "Read configuration.vi", "--format", "lvnet")
    assert run.stdout.startswith(
        "vi Daqmx Module editor node.lvclass:Read configuration.vi :"
    )
    caller = _describe_cli(folder / "Main Editor UI.vi")
    assert "  Daqmx Module editor node.lvclass:Add New Line.vi: (" in caller.stdout
    assert "DAQmx Module editor node.lvclass:Add New Line.vi" not in caller.stdout


def test_describe_shows_project_paths_and_notes_unlabeled_controls() -> None:
    vi = _SAMPLES / "DCAF-DAQModule/source/module/configuration/Delete Line.vi"
    run = _describe_cli(vi)
    assert run.returncode == 0
    assert run.stdout.startswith("# module/configuration/Delete Line.vi\n")
    assert "/home/" not in run.stdout
    assert "has no resolvable label" not in run.stderr
    assert "control(s) record no label and are named control_<uid>" in run.stderr


def test_get_tdenum_of_a_variant_is_a_gap(tmp_path: Path) -> None:
    vi = (
        _SAMPLES
        / "JKI-EasyXML/Source/OpenG Derivative/Format Variant Into String--EasyXML.vi"
    )
    run = _generate(vi, tmp_path)
    assert (
        "Get TDEnum from Data__ogtk reads the type of a variant, which is only "
        "known when the code runs"
    ) in run.stderr
    (module,) = tmp_path.rglob("format_variant_into_string__easyxml.py")
    assert "type(variant).__name__" not in module.read_text(encoding="utf-8")
