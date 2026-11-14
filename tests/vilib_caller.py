"""A hand-made caller diagram for one vi.lib catalog VI: ``run_catalog_call``
generates the caller from the catalog entry the recorded path names, runs it, and
returns the result object, so a test asserts what the catalog's Python does."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.errors_module import ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE
from lvkit.codegen.gaps import Gap
from lvkit.codegen.live_io_module import LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE
from lvkit.codegen.panels_module import PANELS_MODULE_NAME, PANELS_MODULE_SOURCE
from lvkit.codegen.variants_module import VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.models import VIContext, VINode, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind
from lvkit.pipeline import create_import_resolver
from tests.helpers import build_graph


def primitive(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


PATH = primitive("Path")
STRING = primitive("String")
BOOL = primitive("Boolean")
DBL = primitive("NumFloat64")
I32 = primitive("NumInt32")
U32 = primitive("NumUInt32")
ERROR = LVType(kind=LVTypeKind.CLUSTER)

# One terminal of the call node: ``(index, direction, type)``.
Slot = tuple[int, str, LVType]


def run_catalog_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorded: list[str],
    pane: list[Slot],
    inputs: Mapping[str, tuple[int, LVType]],
    outputs: Mapping[str, tuple[int, LVType]],
    arguments: Mapping[str, Any],
    gaps: list[Gap] | None = None,
    runtime_not_implemented: bool = False,
) -> tuple[str, Any]:
    """The generated caller's source and the result of running it.

    ``recorded`` is the path the caller records for the VI, ``pane`` every
    terminal of the call node (as the caller's diagram saves them), ``inputs``
    and ``outputs`` the caller's own controls and indicators by name with the
    call terminal index each is wired to, ``arguments`` the controls' values.
    ``runtime_not_implemented`` allows the VI's Python to raise
    ``NotImplementedError`` for a case it finds only when it runs (an LLB, a
    dialog): the generation itself still has no gap. A ``gaps`` list collects
    the gaps the generation finds, for a test that expects one.
    """
    key = "/" + "/".join(recorded)
    file_name = recorded[-1]
    graph = InMemoryVIGraph()
    graph._dep_graph.add_node(key, node_type="vi")  # noqa: SLF001
    graph._recorded.record(key, None, recorded)  # noqa: SLF001
    package = tmp_path / "pkg"
    package.mkdir(parents=True, exist_ok=True)
    resolver = create_import_resolver(
        "pkg",
        package,
        {key: package / "callee.py", "caller.vi": package / "caller.py"},
        package / "caller.py",
        caller_key="caller.vi",
        graph=graph,
    )
    call = VINode(
        id="call",
        vi_path="caller.vi",
        name=file_name,
        node_type="iUse",
        callee_key=key,
        terminals=[
            FPTerminal(
                id=f"k:{direction}{index}",
                index=index,
                direction=direction,  # type: ignore[arg-type]
                lv_type=lv_type,
            )
            for index, direction, lv_type in pane
        ],
    )
    controls = [
        FPTerminal(
            id=f"fp:{name}",
            index=position,
            direction="input",
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=lv_type,
        )
        for position, (name, (_index, lv_type)) in enumerate(inputs.items())
    ]
    indicators = [
        FPTerminal(
            id=f"fp:{name}",
            index=position,
            direction="output",
            name=name,
            is_public=True,
            is_indicator=True,
            lv_type=lv_type,
        )
        for position, (name, (_index, lv_type)) in enumerate(outputs.items())
    ]
    context = VIContext(
        name="caller.vi",
        inputs=controls,
        outputs=indicators,
        data_flow=[
            *(
                Wire.from_terminals(
                    from_terminal_id=f"fp:{name}",
                    to_terminal_id=f"k:input{index}",
                )
                for name, (index, _type) in inputs.items()
            ),
            *(
                Wire.from_terminals(
                    from_terminal_id=f"k:output{index}",
                    to_terminal_id=f"fp:{name}",
                )
                for name, (index, _type) in outputs.items()
            ),
        ],
    )
    built = build_graph(context, "caller.vi", [call], graph=graph)
    monkeypatch.setattr(built, "loaded_callee_key", lambda node: None)
    found = gaps if gaps is not None else []
    source = build_module(
        context, "caller.vi", import_resolver=resolver, graph=built, gaps=found
    )
    assert gaps is not None or found == []
    assert runtime_not_implemented or "NotImplementedError" not in source
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "caller.py").write_text(source, encoding="utf-8")
    for module, text in (
        (ERRORS_MODULE_NAME, ERRORS_MODULE_SOURCE),
        (VARIANTS_MODULE_NAME, VARIANTS_MODULE_SOURCE),
        (LIVE_IO_MODULE_NAME, LIVE_IO_MODULE_SOURCE),
        (PANELS_MODULE_NAME, PANELS_MODULE_SOURCE),
    ):
        (package / f"{module}.py").write_text(text, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        caller = importlib.import_module("pkg.caller")
        result = caller.caller(**arguments)
    finally:
        for name in (
            "pkg.caller",
            "pkg.panels",
            "pkg.live_io",
            "pkg.variants",
            "pkg.errors",
            "pkg",
        ):
            sys.modules.pop(name, None)
    return source, result
