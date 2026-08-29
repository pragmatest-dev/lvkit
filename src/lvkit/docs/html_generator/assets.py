"""Static asset mixin for HTMLDocGenerator.

Methods: _get_css.
"""

from __future__ import annotations

from pathlib import Path


class AssetsMixin:
    """Mixin providing static asset (CSS) loading."""

    def _get_css(self) -> str:
        """Return CSS stylesheet by reading from template file.

        ``template.css`` lives in ``lvkit/docs/`` (the package one level up
        from this ``html_generator/`` subpackage) — NOT alongside this file.
        """
        template_path = Path(__file__).parent.parent / "template.css"
        return template_path.read_text(encoding="utf-8")
