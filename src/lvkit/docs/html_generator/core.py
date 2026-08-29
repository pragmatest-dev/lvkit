"""Core HTMLDocGenerator class definition.

Contains __init__, generate_vi_page, generate_class_page, generate_index_page,
write_assets — the public entry points that compose the per-responsibility
mixins into one generator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lvkit.graph.models import ClassHierarchyInfo, MethodAccessInfo

from .assets import AssetsMixin
from .class_page import ClassPageMixin
from .index_page import IndexPageMixin
from .naming import NamingMixin
from .vi_page import ViPageMixin


class HTMLDocGenerator(
    NamingMixin,
    ViPageMixin,
    ClassPageMixin,
    IndexPageMixin,
    AssetsMixin,
):
    """Generate static HTML documentation for VIs."""

    def __init__(self, output_dir: Path, doc_title: str, doc_type: str):
        """Initialize HTML generator.

        Args:
            output_dir: Directory to write HTML files
            doc_title: Title for the documentation (library/class name)
            doc_type: Type of documentation ("library", "class", "directory")
        """
        self.output_dir = output_dir
        self.doc_title = doc_title
        self.doc_type = doc_type
        self.all_vis: set[str] = set()  # Track which VIs have pages
        self.icon_map: dict[str, str] = {}  # VI name -> relative icon path
        # classname -> class landing page path (relative to output_dir root),
        # populated by generate_class_page(). Used by the index page to link
        # library group headers to their class landing page.
        self.class_pages: dict[str, str] = {}
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_vi_page(self, vi_data: dict[str, Any]) -> None:
        """Generate HTML page for a single VI.

        Args:
            vi_data: Dictionary with VI information (name, controls, indicators, etc.)
        """
        vi_name = vi_data["vi_name"]
        self.all_vis.add(vi_name)  # Track this VI
        html_filename = self._vi_name_to_filename(vi_name)
        html_path = self.output_dir / html_filename

        # Create subdirectory if needed
        html_path.parent.mkdir(parents=True, exist_ok=True)

        html = self._render_vi_page(vi_data)

        html_path.write_text(html, encoding="utf-8")

    def generate_class_page(
        self,
        hierarchy: ClassHierarchyInfo,
        method_access: dict[str, MethodAccessInfo],
    ) -> None:
        """Generate the landing page for one loaded LabVIEW class.

        Args:
            hierarchy: Parent/children/methods/fields for this class.
            method_access: Access-scope info for this class's own methods
                (keyed by qualified VI name), used to badge the Methods list.
        """
        filename = self._class_name_to_filename(hierarchy.classname)
        self.class_pages[hierarchy.classname] = filename
        html_path = self.output_dir / filename
        html_path.parent.mkdir(parents=True, exist_ok=True)

        html = self._render_class_page(hierarchy, method_access)

        html_path.write_text(html, encoding="utf-8")

    def generate_index_page(self, all_vis: list[str]) -> None:
        """Generate index.html with table of contents.

        Args:
            all_vis: List of all VI names
        """
        html = self._render_index_page(all_vis)
        index_path = self.output_dir / "index.html"
        index_path.write_text(html, encoding="utf-8")

    def write_assets(self) -> None:
        """Write CSS and other static assets."""
        css = self._get_css()
        css_path = self.output_dir / "style.css"
        css_path.write_text(css, encoding="utf-8")
