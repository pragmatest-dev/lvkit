"""The result of one body render: the output string, the rendered VI's own name,
and the files the render read besides the VI itself."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from ..catalog_stamp import CatalogKey
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
    # The catalog entries the VI looks up (see ``catalog_keys``): the output
    # cache records what the project's ``.lvkit/`` says for them.
    catalog_keys: frozenset[CatalogKey] = frozenset()
