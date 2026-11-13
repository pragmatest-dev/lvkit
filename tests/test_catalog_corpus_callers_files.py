"""Corpus VIs that call the file, path, string, array and zip catalog VIs,
generated through the real pipeline: the generated function is imported and run,
and what it returns is asserted.

A VI whose other nodes are gaps (the measurement plug-in SDK, a Windows command
line, front-panel properties) cannot run: its generated source is checked."""

from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from tests.test_catalog_corpus_callers import SAMPLES, _generate

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

MEASUREMENT = SAMPLES / "measurement-plugin-labview" / "Source"
RENAME_REFACTOR = MEASUREMENT / "Generator" / "_MeasurementService Utility"


def _function(
    vi: Path,
    out: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: str,
    name: str,
) -> Any:
    """The function ``name`` of the module ``module`` (below the package) of the
    VI ``vi`` generated into ``out``."""
    package = _generate(vi, out)
    monkeypatch.syspath_prepend(str(package.parent))
    imported = importlib.import_module(f"{package.name}.{module}")
    return getattr(imported, name)


@pytest.fixture(autouse=True)
def _forget_generated_packages() -> Any:
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        del sys.modules[name]


def test_begins_with_qualified_name_splits_and_joins_at_colons(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Delimited String to 1D String Array splits both names at ``:`` and 1D
    String Array to Delimited String joins what follows the shared beginning."""
    function = _function(
        RENAME_REFACTOR / "RenameRefactor" / "Begins with Qualified Name.vi",
        tmp_path,
        monkeypatch,
        "_measurementservice_utility.renamerefactor.begins_with_qualified_name",
        "begins_with_qualified_name",
    )
    found = function(name="A.lvlib:B.lvclass:C.vi", beginning_name="A.lvlib")
    assert found.begins_with_name is True
    assert found.ending_name == "B.lvclass:C.vi"
    other = function(name="A.lvlib:B.lvclass", beginning_name="X.lvlib")
    assert other.begins_with_name is False
    assert other.ending_name == ""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_calculate_relative_path_uses_compare_two_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The VI reports error 5001 unless Path 2 Relative to Path 1? is TRUE."""
    function = _function(
        RENAME_REFACTOR
        / "RenameRefactor"
        / "Calculate Relative Path from Directory.vi",
        tmp_path,
        monkeypatch,
        "_measurementservice_utility.renamerefactor."
        "calculate_relative_path_from_directory",
        "calculate_relative_path_from_directory",
    )
    below = function(directory=Path("/a/b"), path=Path("/a/b/c/d.vi"))
    assert below.relative_path == Path("c/d.vi")
    assert below.error_out is None
    outside = function(directory=Path("/a/b"), path=Path("/x/y/z/w.vi"))
    assert outside.relative_path == Path("../../x/y/z/w.vi")
    assert outside.error_out is not None
    assert outside.error_out.code == 5001


def test_simple_channel_mapping_splits_each_line_at_commas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    function = _function(
        MEASUREMENT
        / "Tests/Tests.Runtime/Measurement Server/Helpers"
        / "Simple Channel Mapping to Channel Mapping Cluster.vi",
        tmp_path,
        monkeypatch,
        "source.tests.testsruntime.measurement_server.helpers."
        "simple_channel_mapping_to_channel_mapping_cluster",
        "simple_channel_mapping_to_channel_mapping_cluster",
    )
    mapped = function(simple_channel_mappings=["PinA,3,ch0,DMM,session1"])
    [mapping] = mapped.channel_mappings
    assert (mapping.pin_or_relay_name, mapping.site, mapping.channel) == (
        "PinA",
        3,
        "ch0",
    )


def test_populate_zip_file_lists_a_folder_and_adds_each_file_below_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recursive File List lists the folder, Compare Two Paths gives each file's
    path relative to the folder's parent, and Add File to Zip stores it there."""
    function = _function(
        SAMPLES / "actor-framework/Core/Install Support/Populate Zip File.vi",
        tmp_path / "gen",
        monkeypatch,
        "install_support.populate_zip_file",
        "populate_zip_file",
    )
    source = tmp_path / "src"
    (source / "sub").mkdir(parents=True)
    (source / "a.txt").write_text("A", encoding="utf-8")
    (source / "sub" / "b.txt").write_text("B", encoding="utf-8")
    target = tmp_path / "out.zip"
    with zipfile.ZipFile(target, "w") as archive:
        result = function(zip_file_in=archive, source=source)
        assert result.error_out is None
    with zipfile.ZipFile(target) as written:
        assert sorted(written.namelist()) == ["src/a.txt", "src/sub/b.txt"]


def test_resolve_pin_groups_removes_duplicates_in_the_source(
    tmp_path: Path,
) -> None:
    """Resolve Pin Groups to Pins cannot run (its Look In Map is a gap); the
    Remove Duplicates From 1D Array it calls is written out as the catalog's
    order-keeping loop."""
    package = _generate(
        MEASUREMENT
        / "Runtime/Clients/Session Management V1/Session Reservation"
        / "Resolve Pin Groups to Pins.vi",
        tmp_path,
    )
    source = next(package.rglob("resolve_pin_groups_to_pins.py")).read_text(
        encoding="utf-8"
    )
    assert "_element == _kept" in source
