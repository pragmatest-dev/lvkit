"""Faithful LabVIEW block-diagram rendering to SVG.

Renders a VI's block diagram straight from the pylabview heap XML lvkit already
extracts — no LabVIEW install required. See ``experiments/lv-renderer/PLAN.md``.
"""

from __future__ import annotations

from pathlib import Path

from .heap_scene import DiagramScene, build_scene
from .svg import scene_to_svg

__all__ = ["DiagramScene", "build_scene", "render_vi_to_svg", "scene_to_svg"]


def render_vi_to_svg(vi_or_bd: Path) -> str:
    """Render a ``.vi`` file (or ``_BDHb.xml`` heap) to an SVG string."""
    return scene_to_svg(build_scene(vi_or_bd))
