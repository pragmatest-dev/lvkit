"""Corpus VIs that read the type a variant records, generated through the real
pipeline and RUN on variants whose type strings ``type_string.encode`` writes.

OpenG's lvdata VIs that only read a type descriptor (Get TDEnum from Data, Get
Strings from Enum, No of Elements in Cluster, Get Array Element TD) generate
through their own diagrams: Variant To Flattened String hands them the type string
the variant records. JSONtext's Variant to JSON Text calls Get Array / Numeric
Information and generates their reads of the type; its Get Refnum Information and
Get Waveform Information calls stay gaps (exit 3)."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from lvkit import type_string
from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from tests.corpus_graphs import OPENG_USERLIB

pytestmark = [pytest.mark.functional, pytest.mark.needs_samples]

SAMPLES = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
GENERATE = Path(__file__).resolve().parent.parent / "scripts" / "generate_python.py"
_LVDATA = OPENG_USERLIB / "_OpenG.lib/lvdata/lvdata.llb"
_LVDATA_MODULE = "userlib._openglib.lvdata.lvdatallb"


def _scalar(name: str) -> LVType:
    return LVType(LVTypeKind.PRIMITIVE, underlying_type=name)


_ENUM = LVType(
    LVTypeKind.ENUM,
    underlying_type="UnitUInt8",
    values={label: EnumValue(value=i) for i, label in enumerate(("am", "fm", "fm 2"))},
)
_CLUSTER = LVType(
    LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField("a", _scalar("NumInt32")),
        ClusterField("b", _scalar("String")),
    ],
)
_ARRAY = LVType(
    LVTypeKind.ARRAY,
    underlying_type="Array",
    element_type=_scalar("NumFloat64"),
    dimensions=2,
)


def _generate(vi: Path, out: Path, *, exit_codes: tuple[int, ...] = (0,)) -> Path:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    done = subprocess.run(
        [sys.executable, str(GENERATE), str(vi), "-o", str(out),
         "--userlib", str(OPENG_USERLIB)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode in exit_codes, f"{done.stdout}\n{done.stderr}"
    return next(p for p in out.iterdir() if p.is_dir())


def _run(name: str, function: str, lv_type: LVType, code: int, tmp_path: Path,
         monkeypatch: pytest.MonkeyPatch):
    package = _generate(_LVDATA / f"{name}__ogtk.vi", tmp_path)
    monkeypatch.syspath_prepend(str(package.parent))
    try:
        module = importlib.import_module(
            f"{package.name}.{_LVDATA_MODULE}.{function}__ogtk"
        )
        variants = importlib.import_module(f"{package.name}.variants")
        given = variants.Variant(
            None, variants.LVType(code, "x", tuple(type_string.encode(lv_type)))
        )
        return getattr(module, f"{function}__ogtk")(given)
    finally:
        for loaded in [m for m in sys.modules if m.split(".")[0] == package.name]:
            del sys.modules[loaded]


def test_get_tdenum_from_data_is_the_code_of_the_variants_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "Get TDEnum from Data", "get_tdenum_from_data", _ARRAY, 0x40, tmp_path,
        monkeypatch,
    )
    assert result.type_descriptor_enumeration == 0x40


def test_get_strings_from_enum_reads_the_items_from_the_type_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "Get Strings from Enum", "get_strings_from_enum", _ENUM, 0x15, tmp_path,
        monkeypatch,
    )
    assert result.enum_strings == ["am", "fm", "fm 2"]
    assert result.string_value == "am"


def test_no_of_elements_in_cluster_counts_the_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "No of Elements in Cluster", "no_of_elements_in_cluster", _CLUSTER, 0x50,
        tmp_path, monkeypatch,
    )
    assert result.number_of_elements == 2


def test_get_array_element_td_is_the_element_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "Get Array Element TD", "get_array_element_td", _ARRAY, 0x40, tmp_path,
        monkeypatch,
    )
    assert result.type_descriptor == type_string.encode(_scalar("NumFloat64"))


def test_variant_to_json_text_reads_the_type_and_keeps_the_refnum_and_waveform_gaps(
    tmp_path: Path,
) -> None:
    package = _generate(
        SAMPLES / "JSONtext/Variant to JSON Text.vi", tmp_path, exit_codes=(0, 3)
    )
    source = next(package.rglob("variant_to_json_text.py")).read_text()
    assert "read_type(" in source
    assert "number_of_dimensions" in source
    assert "Get Refnum Information cannot read" in source


_LABELLED_CLUSTER = LVType(
    LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField("count", _scalar("NumInt32")),
        ClusterField("text", _scalar("String")),
    ],
)
_UNLABELLED_CLUSTER = LVType(
    LVTypeKind.CLUSTER,
    underlying_type="Cluster",
    fields=[
        ClusterField("a", _scalar("NumInt32"), labelled=False),
        ClusterField("b", _scalar("String"), labelled=False),
    ],
)


def test_get_cluster_element_names_returns_the_labels_the_type_string_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "Get Cluster Element Names", "get_cluster_element_names",
        _LABELLED_CLUSTER, 0x50, tmp_path, monkeypatch,
    )
    assert result.element_names == ["count", "text"]


def test_get_cluster_element_names_of_an_unlabelled_cluster_is_empty_per_element(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(
        "Get Cluster Element Names", "get_cluster_element_names",
        _UNLABELLED_CLUSTER, 0x50, tmp_path, monkeypatch,
    )
    assert result.element_names == ["", ""]
