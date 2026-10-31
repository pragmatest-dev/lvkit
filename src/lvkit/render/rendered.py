"""The result of one body render: the output string, the rendered VI's own name,
and the files the render read besides the VI itself."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from ..run_report import Problem


class RenderedBody(NamedTuple):
    """One ``render_body_with_name`` result. ``dependencies`` are the on-disk
    paths of every SubVI/control the load reached (present or absent), so the
    output cache can tell when a render built from them is stale."""

    body: str
    name: str
    dependencies: tuple[Path, ...]
    # The problems recorded on the nodes of the VIs the render loaded.
    problems: tuple[Problem, ...] = ()
