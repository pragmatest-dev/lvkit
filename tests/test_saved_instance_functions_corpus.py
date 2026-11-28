"""The functions written from the instances saved in real callers: generated, then
imported and run, and the ``uses :`` lines of verbose lvnet that name the frame
each copy runs. Real callers from the sample corpus."""

from __future__ import annotations

import importlib
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from lvkit.pipeline import generate_python
from tests.corpus_graphs import OPENG_USERLIB, load_corpus_vi
from tests.test_saved_instances_corpus import _M_CONNECTIONS, _ONE_CONNECTION

pytestmark = pytest.mark.needs_samples

_SAMPLES = Path(".lvkit/cache/samples").resolve()
_JSON = _SAMPLES / "JSONtext"
_SEARCH = [str(_JSON), str(_SAMPLES / "JDP-Common-Utilities")]
_RUNTIME = _SAMPLES / "measurement-plugin-labview/Source/Runtime"
_TEMPLATES = (
    _SAMPLES
    / "actor-framework/Providers/MessageMakerProvider/_Message Maker/_templates"
)
_PICO_EXAMPLE = (
    _SAMPLES / "picosdk-ni-labview-examples/usbtc08/USBTC08StreamingExample.vi"
)
_UNPACK_FIELDS = _SAMPLES / "grpc-labview/tests/Deprecated/Protobuf/TestUnpackFields.vi"


@pytest.fixture
def generated_modules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[Path]:
    """``tmp_path`` as the folder generated packages are imported from; every
    module imported from it is forgotten afterwards."""
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    before = set(sys.modules)
    yield tmp_path
    for name in set(sys.modules) - before:
        del sys.modules[name]


def _import(summary: dict, file: Path) -> ModuleType:
    root = Path(summary["output_dir"])
    dotted = ".".join(file.relative_to(root).with_suffix("").parts)
    return importlib.import_module(f"{root.name}.{dotted}")


def test_the_copy_of_a_vim_is_a_function_of_the_vims_own_module(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _JSON / "Examples/Example of JOBJ Tag.vi",
        generated_modules,
        search_path=_SEARCH,
        userlib_root=OPENG_USERLIB,
    )

    root = Path(summary["output_dir"])
    # No module per copy: the folder named for the caller's file holds none.
    assert not (root / "examples" / "example_of_jobj_tagvi").exists()
    vim_file = root / "from_json_text_vim.py"
    assert vim_file.read_text(encoding="utf-8").count("\ndef from_json_text(") == 1
    caller = (root / "examples" / "example_of_jobj_tag.py").read_text(encoding="utf-8")
    assert "from ..from_json_text_vim import from_json_text\n" in caller

    # The copy takes the caller's cluster type: what comes out is a plain
    # cluster, not a Variant.
    result = _import(summary, vim_file).from_json_text(
        json_='{"Config":{"A1":{"A":"x","B":0}},"Other Stuff":1}',
        data_type_and_default_value=([], 0.0),
    )
    assert isinstance(result.data_out, tuple)
    assert result.data_out[1] == 1.0
    assert result.error_out is None


def test_two_calls_of_one_vim_with_one_copy_body_share_a_function_and_run(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _RUNTIME / "Measurements/Helpers/Add JSON Name Value Pair.vim",
        generated_modules,
        search_path=[str(_RUNTIME)],
        userlib_root=OPENG_USERLIB,
    )

    root = Path(summary["output_dir"])
    [encode_file] = sorted(root.rglob("encode_to_json_string_vim.py"))
    assert encode_file.read_text(encoding="utf-8").count("\ndef encode_") == 1
    [adder_file] = sorted(root.rglob("add_json_name_value_pair_vim.py"))
    adder = adder_file.read_text(encoding="utf-8")
    # Both calls (name and value) call the one function.
    assert adder.count("= encode_to_json_string(") == 2
    assert "encode_to_json_string_2" not in adder
    result = _import(summary, adder_file).add_json_name_value_pair(
        name="a", value="b", json_string_in="{"
    )
    assert result.json_string_out == '{"a":"b",'
    assert result.error_out is None


def test_callers_with_one_copy_body_share_a_function(generated_modules: Path) -> None:
    summary = generate_python(
        _TEMPLATES, generated_modules, search_path=[str(_SAMPLES / "actor-framework")]
    )

    root = Path(summary["output_dir"])
    [casting_file] = sorted(root.rglob("casting_utility_for_actors_vim.py"))
    assert (
        casting_file.read_text(encoding="utf-8").count(
            "\ndef casting_utility_for_actors("
        )
        == 1
    )
    importing = [
        path.name
        for path in sorted(root.rglob("*message_template.py"))
        if "casting_utility_for_actors_vim import casting_utility_for_actors\n"
        in path.read_text(encoding="utf-8")
    ]
    # Both callers (the Message Template's class and the Concrete one's) call
    # the one function.
    assert importing == ["concrete_message_template.py", "message_template.py"]


def test_copies_with_other_types_are_numbered_functions_of_the_vim_module(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _UNPACK_FIELDS, generated_modules, search_path=[str(_SAMPLES / "grpc-labview")]
    )

    root = Path(summary["output_dir"])
    [vim_file] = sorted(root.rglob("getunpackedfield_vim.py"))
    text = vim_file.read_text(encoding="utf-8")
    names = ["getunpackedfield"] + [f"getunpackedfield_{n}" for n in range(2, 7)]
    for name in names:
        assert f"\ndef {name}(" in text, name
    compile(text, str(vim_file), "exec")
    [caller] = sorted(root.rglob("testunpackfields.py"))
    source = caller.read_text(encoding="utf-8")
    # Each caller call imports the function its own copy maps to.
    for name in names:
        assert f"getunpackedfield_vim import {name}\n" in source, name


def test_verbose_lvnet_names_the_frame_each_copy_runs() -> None:
    graph_one, key_one = load_corpus_vi(_ONE_CONNECTION.resolve())
    graph_m, key_m = load_corpus_vi(_M_CONNECTIONS.resolve())
    one = render_lvnet(
        build_netlist_from_graph(graph_one, key_one), display_name="x", verbose=True
    )
    many = render_lvnet(
        build_netlist_from_graph(graph_m, key_m), display_name="x", verbose=True
    )
    terse = render_lvnet(build_netlist_from_graph(graph_one, key_one), display_name="x")

    lines_one = one.splitlines()
    [spec_one] = [ln for ln in lines_one if "type-specialization (" in ln]
    [spec_m] = [ln for ln in many.splitlines() if "type-specialization (" in ln]
    # The line closes the copy's entry, under its in/out lines.
    assert spec_one == "      type-specialization (frame=1) (id 2171)"
    assert spec_m == "      type-specialization (frame=0) (id 2171)"
    assert lines_one[lines_one.index(spec_one) - 1].startswith("      out  error out")
    # No instance marker anywhere, and terse has none of these lines.
    assert "Instance:" not in one + many
    assert "type-specialization (" not in terse


def test_the_frame_lines_round_trip_through_the_lvnet_reader() -> None:
    graph, key = load_corpus_vi(_ONE_CONNECTION.resolve())
    text = render_lvnet(
        build_netlist_from_graph(graph, key), display_name="x", verbose=True
    )

    parsed = parse_lvnet(text)
    rebuilt = reconstruct_module(parsed)

    [entry] = [d for d in parsed.uses if d.type_specializations]
    assert [(s.frame, s.uid) for s in entry.type_specializations] == [(1, "2171")]
    assert [
        (s.frame, s.uid) for d in rebuilt.dependencies for s in d.type_specializations
    ] == [(1, "2171")]
    again = render_lvnet(rebuilt, display_name="x", verbose=True)
    assert again.count("      type-specialization (frame=1) (id 2171)") == 1


def test_json_puts_the_frame_in_the_entrys_settings() -> None:
    graph, key = load_corpus_vi(_M_CONNECTIONS.resolve())
    data = netlist_to_dict(build_netlist_from_graph(graph, key), verbose=True)

    [entry] = [d for d in data["dependencies"] if "settings" in d]
    assert entry["settings"] == [{"frame": 0, "id": "2171"}]
    assert entry["qualified"].endswith("Assert Connections.vim")


_GUID = re.compile(r"[0-9a-f]{8}[-_][0-9a-f]{4}[-_][0-9a-f]{4}[-_][0-9a-f]{4}")


def _no_generated_name(root: Path) -> None:
    """No generated file names a copy by the GUID LabVIEW gave it."""
    for file in sorted(root.rglob("*.py")):
        match = _GUID.search(file.read_text(encoding="utf-8"))
        assert match is None, f"{file.relative_to(root)}: {match and match.group()}"


def test_every_function_of_a_vim_is_that_vim_to_the_program(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _UNPACK_FIELDS, generated_modules, search_path=[str(_SAMPLES / "grpc-labview")]
    )

    root = Path(summary["output_dir"])
    [vim_file] = sorted(root.rglob("getunpackedfield_vim.py"))
    text = vim_file.read_text(encoding="utf-8")
    names = ["getunpackedfield"] + [f"getunpackedfield_{n}" for n in range(2, 7)]
    vim = "grpc-lvsupport.lvlib:GetUnpackedField.vim"
    for name in names:
        assert f"\n{name}.vi_name = {vim!r}\n" in text, name
        assert f"\n{name}.window_title = 'GetUnpackedField.vim'\n" in text, name
    _no_generated_name(root)
    # All VIs In Memory lists the .vim once, whatever the number of its copies.
    _import(summary, vim_file)
    assert _import(summary, root / "panels.py").loaded_vis().count(vim) == 1


def test_a_vim_copy_carries_the_vims_own_window_and_name(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _JSON / "Examples/Example of JOBJ Tag.vi",
        generated_modules,
        search_path=_SEARCH,
        userlib_root=OPENG_USERLIB,
    )

    root = Path(summary["output_dir"])
    text = (root / "from_json_text_vim.py").read_text(encoding="utf-8")
    assert "\nfrom_json_text.vi_name = 'JSONtext.lvlib:From JSON Text.vim'\n" in text
    assert "\nfrom_json_text.window_title = 'From JSON Text.vim'\n" in text
    _no_generated_name(root)


def test_an_express_vi_copy_is_named_as_its_caller_records_it(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _PICO_EXAMPLE, generated_modules, userlib_root=OPENG_USERLIB
    )

    root = Path(summary["output_dir"])
    names = set()
    for file in sorted(root.rglob("convert_to_dynamic_data*.py")):
        [line] = [
            ln
            for ln in file.read_text(encoding="utf-8").splitlines()
            if ".vi_name = " in ln
        ]
        names.add(line.split(" = ", 1)[1])
    assert names == {
        f"'USBTC08StreamingExample.vi:Instance:{n}'"
        for n in (0, 1, 2, 3, 4, 5, 6, 7, 8)
    }
    _no_generated_name(root)


def test_a_copy_is_no_file_a_name_or_a_pane_finds() -> None:
    graph, key = load_corpus_vi(
        _JSON.joinpath("Examples/Example of JOBJ Tag.vi").resolve()
    )

    copies = [k for k in graph.list_vis() if graph.instance_holder(k) is not None]
    assert len(copies) == 2
    for copy in copies:
        assert graph.vi_display_name(copy) == graph.instance_vim(copy)
        assert _GUID.search(graph.key_label(copy)) is None
        assert graph.key_label(copy).startswith("Examples/Example of JOBJ Tag.vi/")
        assert graph.attributes_key(copy) == graph.instance_file(copy)
    files = set(graph.owned_vi_files().values()) | set(
        graph.unowned_vi_files().values()
    )
    assert files.isdisjoint(copies)
    assert graph.attributes_key(key) == key


def test_a_copy_saved_in_a_copy_is_placed_and_named_as_its_vim(
    generated_modules: Path,
) -> None:
    summary = generate_python(
        _ONE_CONNECTION,
        generated_modules,
        search_path=[str(_SAMPLES / "measurement-plugin-labview/Source")],
        userlib_root=OPENG_USERLIB,
    )

    # The copy of Get Connections from Pins.vim that a saved copy holds is a module
    # in the folder of the file that holds both.
    root = Path(summary["output_dir"])
    [nested] = root.rglob(
        "get_connections___1pin_mconnvi/get_connections_from_pins_vim.py"
    )
    assert nested.parent.name.endswith("vi")
    # No folder, module, message or attribute names a copy by its GUID.
    assert not [p for p in root.rglob("*") if _GUID.search(p.name.replace("_", "-"))]
    _no_generated_name(root)
