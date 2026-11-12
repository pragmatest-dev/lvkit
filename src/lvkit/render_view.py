"""``RenderView`` — which picture of a file ``render`` draws.

A standalone, dependency-free leaf module so the CLI's argparse setup and the
output cache can name the pictures WITHOUT importing the render stack. A VI has
two pictures, its block diagram and its front panel; a ``.ctl`` control has only
its front panel.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class RenderView(StrEnum):
    """One picture of a file."""

    BLOCK_DIAGRAM = "block-diagram"
    FRONT_PANEL = "front-panel"


# What ``--view`` / the MCP ``view`` argument accepts: either picture, or both.
BOTH = "both"
VIEW_CHOICES = (RenderView.BLOCK_DIAGRAM.value, RenderView.FRONT_PANEL.value, BOTH)


def is_control(path: Path) -> bool:
    """Whether ``path`` is a ``.ctl`` control, which has no block diagram."""
    return path.suffix.lower() == ".ctl"


def views_for(path: Path, requested: str | None) -> tuple[RenderView, ...]:
    """The pictures ``requested`` (``None`` for the default) asks of ``path``, in
    the order they are drawn. A VI's default is its block diagram; a control has
    only its front panel, so every request for it is that one picture. Raises
    ``ValueError`` for a request that is not one of ``VIEW_CHOICES``."""
    if requested is not None and requested not in VIEW_CHOICES:
        raise ValueError(
            f"view must be one of {', '.join(VIEW_CHOICES)}, not {requested!r}"
        )
    if is_control(path):
        return (RenderView.FRONT_PANEL,)
    if requested is None or requested == RenderView.BLOCK_DIAGRAM.value:
        return (RenderView.BLOCK_DIAGRAM,)
    if requested == RenderView.FRONT_PANEL.value:
        return (RenderView.FRONT_PANEL,)
    return (RenderView.BLOCK_DIAGRAM, RenderView.FRONT_PANEL)
