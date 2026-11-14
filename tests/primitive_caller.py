"""``run_primitive``: one catalog primitive in a hand-made VI (``tests/prim_vi``),
generated and run; returns the generated source and the result object."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from lvkit.codegen.gaps import Gap
from tests.prim_vi import Pin, generate
from tests.vilib_caller import Slot


def run_primitive(
    prim_id: int,
    pane: list[Slot],
    inputs: Mapping[str, tuple[int, Any]],
    outputs: Mapping[str, tuple[int, Any]],
    arguments: Mapping[str, Any],
    gaps: list[Gap] | None = None,
    name: str = "Primitive",
    constants: Mapping[int, tuple[Any, Any]] | None = None,
) -> tuple[str, Any]:
    """The generated VI's source and the result of running it.

    ``pane`` is every terminal of the primitive node as the caller's diagram
    saves them, ``inputs`` and ``outputs`` the VI's own controls and indicators
    by name with the node terminal index each is wired to, ``arguments`` the
    controls' values. ``constants`` maps an input index to the ``(value, type)``
    of a constant wired to it. A pane terminal no control, constant or indicator
    names is unwired.
    """
    source_of = {index: label for label, (index, _) in inputs.items()}
    sink_of = {index: label for label, (index, _) in outputs.items()}
    pins = [
        Pin(
            index,
            direction,
            lv_type,
            source=source_of.get(index) if direction == "input" else None,
            sink=sink_of.get(index) if direction == "output" else None,
            constant=(constants or {}).get(index, (None,))[0]
            if direction == "input"
            else None,
        )
        for index, direction, lv_type in pane
    ]
    source, function = generate(
        prim_id,
        name,
        pins,
        {label: lv_type for label, (_, lv_type) in inputs.items()},
        {label: lv_type for label, (_, lv_type) in outputs.items()},
        vi_name="Caller.vi",
        gaps=gaps,
    )
    if os.environ.get("DUMP"):
        print(source)
    return source, function(**arguments)
