"""A typedef whose cluster field is another typedef is generated with the field
typed as that typedef's class, imported from the file its own records tie it to."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.type_module import build_type_module
from lvkit.graph import InMemoryVIGraph
from lvkit.pipeline import create_import_resolver

from .conftest import SAMPLES_ROOT

_RPC = (
    SAMPLES_ROOT
    / "measurement-plugin-labview/Source/gRPC/Generated APIs/ni/measurementlink"
    / "sessionmanagement/v1/RPC Messages"
)
_PREFIX = "ni_measurementlink_sessionmanagement_v1_"
_RESPONSE = _RPC / f"{_PREFIX}ReserveAllRegisteredSessionsResponse.ctl"
_INFORMATION = _RPC / f"{_PREFIX}SessionInformation.ctl"


@pytest.mark.needs_samples
@pytest.mark.slow
@pytest.mark.skipif(not _RESPONSE.exists(), reason="measurement-plugin sample absent")
def test_a_typedef_field_typedef_is_typed_and_imported(tmp_path: Path) -> None:
    graph = InMemoryVIGraph()
    key = graph.load_typedef(_RESPONSE)
    info = graph.get_typedef(key)
    (field,) = info.root_type.fields
    assert field.type is not None and field.type.element_type is not None
    element = field.type.element_type
    assert element.file_key == str(_INFORMATION.resolve())

    out = tmp_path / "pkg"
    paths = {k: out / f"{Path(k).stem.lower()}.py" for k in graph.list_typedefs()}
    resolver = create_import_resolver(
        "pkg", out, paths, paths[key], caller_key=key, graph=graph
    )
    source = build_type_module(graph, key, resolver)
    assert (
        "sessions: list[NiMeasurementlinkSessionmanagementV1SessionInformation]"
        in source
    )
    assert "import NiMeasurementlinkSessionmanagementV1SessionInformation" in source
