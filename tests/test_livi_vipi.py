"""Tests for ``parse_vipi_from_livi`` — the ``_LIvi.bin`` (VI-level link
identity) parser that recovers EVERY subVI callee, including the
dynamic-dispatch calls that leave no ``IUVI`` in ``_LIbd.bin``."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lvkit.parser.metadata import parse_vipi_from_livi

SAMPLES_ROOT = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"


def _plain(name: str) -> bytes:
    """A plain pascal string ``[len][text]`` — how a class name is stored."""
    b = name.encode("ascii")
    return bytes([len(b)]) + b


def _wrapped_method(name: str) -> bytes:
    """A method name pascal string ``[outer_len][0x01][inner_len][text]``."""
    b = name.encode("ascii")
    return bytes([2 + len(b), 0x01, len(b)]) + b


def _pth0(tokens: list[str]) -> bytes:
    comp = b"".join(bytes([len(t)]) + t.encode("ascii") for t in tokens)
    return (
        b"PTH0"
        + struct.pack(">I", len(comp) + 4)  # total (ignored by the parser)
        + struct.pack(">H", 1)  # path type
        + struct.pack(">H", len(tokens))  # ncomp
        + comp
    )


def _vipi(count: int, names: bytes, pth0: bytes) -> bytes:
    return b"VIPI" + struct.pack(">I", count) + names + pth0


def test_parse_vipi_static_and_dynamic_callees(tmp_path: Path) -> None:
    """A count=0 own-context record is skipped; a count=1 record yields a bare
    method name; a count=2 record yields the method name too (the class hint
    is not decoded — see the function docstring on why it's unreliable)."""
    own_ctx = _vipi(0, b"", _pth0(["", "Icon Framework.lvclass"]))
    rec1 = _vipi(
        1,
        _wrapped_method("GET_IconTextClass.vi"),
        _pth0(["", "", "Icon", "Icon.lvclass"]),
    )
    rec2 = _vipi(
        2,
        _plain("Icon Framework.lvclass") + _wrapped_method("SET_BodyText.vi"),
        _pth0(["", "Icon Framework.lvclass"]),
    )
    path = tmp_path / "sample_LIvi.bin"
    path.write_bytes(own_ctx + rec1 + rec2)

    names = set(parse_vipi_from_livi(path))
    assert names == {"GET_IconTextClass.vi", "SET_BodyText.vi"}


def test_parse_vipi_missing_file_returns_empty(tmp_path: Path) -> None:
    assert parse_vipi_from_livi(tmp_path / "nope_LIvi.bin") == []


@pytest.mark.needs_samples
def test_parse_vipi_real_apply_body_text() -> None:
    """The real ``Apply Body Text.vi`` calls 5 class methods via dynamic
    dispatch — NONE of which appear in ``_LIbd.bin``. They are all in
    ``_LIvi.bin``, which is exactly what we recover here."""
    from lvkit.extractor import extract_vi_xml

    vi = (
        SAMPLES_ROOT
        / "ni-labview-icon-editor/vi.lib/LabVIEW Icon API/lv_icon/Classes"
        / "Icon Framework/Apply Body Text.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample VI absent: {vi}")

    _, _, main_xml = extract_vi_xml(vi)
    assert main_xml is not None
    livi = main_xml.with_name(main_xml.stem + "_LIvi.bin")
    if not livi.exists():
        pytest.skip("no _LIvi.bin extracted for this VI")

    names = set(parse_vipi_from_livi(livi))
    assert {
        "GET_IconTextClass.vi",
        "SET_IconTextClass.vi",
        "SET_BodyText.vi",
        "GET_LayerData.vi",
        "SET_Layer_Data.vi",
    } <= names


@pytest.mark.needs_samples
def test_minimal_staging_resolves_class_method_subvis() -> None:
    """The MINIMAL dependency closure the web extension stages
    (``get_dependency_paths`` — mirrored by ``tests.helpers.transitive_closure``)
    must include EVERY class-method SubVI the diagram calls, so their icons +
    interfaces render instead of bare boxes. Before this work it returned only
    the 8 class/typedef deps and ZERO SubVIs.

    ``Apply Body Text`` calls 6 class methods. Four are declared on classes it
    path-records (``Icon``, ``Icon Framework``). The other two — ``GET_LayerData``
    / ``SET_Layer_Data`` — are ``Layer`` methods it calls by INHERITANCE
    (``Icon Framework`` extends ``Layer``); they resolve by following
    ``Icon Framework``'s recorded parent URL to ``Layer`` and walking the ancestor
    chain. Every hop is a recorded path — identical on web and desktop, no scan.
    """
    from lvkit.graph import InMemoryVIGraph
    from lvkit.load_mode import LoadMode

    root = SAMPLES_ROOT / "ni-labview-icon-editor"
    vi = (
        root
        / "vi.lib/LabVIEW Icon API/lv_icon/Classes/Icon Framework/Apply Body Text.vi"
    )
    if not vi.exists():
        pytest.skip(f"sample VI absent: {vi}")

    g = InMemoryVIGraph()
    name = g.load_vi(vi, LoadMode.MINIMAL, search_paths=[root])
    assert name is not None
    staged = {p.name for p in g.get_dependency_paths(name)}

    # All 6 SubVI callees resolve by path — including the 2 inherited Layer
    # methods dispatched through an Icon Framework object.
    assert {
        "GET_IconTextClass.vi",
        "SET_IconTextClass.vi",
        "SET_BodyText.vi",
        "CreateBodyText.vi",
        "GET_LayerData.vi",
        "SET_Layer_Data.vi",
    } <= staged
    # The owning classes — including the INHERITED parent Layer, reached via
    # Icon Framework's recorded parent URL — come along.
    assert {"Icon.lvclass", "Icon Framework.lvclass", "Layer.lvclass"} <= staged

    # PATH-DRIVEN / WEB PARITY: with NO search path at all (the loader cannot
    # name-search — the web can only see recorded paths), the same SubVIs and
    # classes still resolve, because every one has a recorded path (subVIs via
    # the _LIvi VIPI + inheritance; classes/typedefs via the _LIvi/_LIbd/_LIfp
    # PTH0 link tables). Only a nested typedef with no file link (LayerType.ctl)
    # falls out — its type is already inline, so it needs no staging.
    g2 = InMemoryVIGraph()
    name2 = g2.load_vi(vi, LoadMode.MINIMAL, search_paths=[])
    assert name2 is not None
    by_path = {p.name for p in g2.get_dependency_paths(name2)}
    assert {
        "GET_IconTextClass.vi",
        "SET_IconTextClass.vi",
        "SET_BodyText.vi",
        "CreateBodyText.vi",
        "GET_LayerData.vi",
        "SET_Layer_Data.vi",
        "Icon.lvclass",
        "Icon Framework.lvclass",
        "Layer.lvclass",
    } <= by_path
