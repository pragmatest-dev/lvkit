"""Class page rendering mixin for HTMLDocGenerator.

Methods: _render_class_hierarchy_section, _render_class_properties_table,
_render_class_methods_list, _render_class_page.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from lvkit.graph.models import ClassFieldEntry, ClassHierarchyInfo, MethodAccessInfo


class ClassPageMixin:
    """Mixin providing class landing page rendering methods."""

    # These attributes are defined on HTMLDocGenerator in core.py
    doc_title: str

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins, resolved via MRO
        def _vi_name_to_filename(self, vi_name: str) -> str: ...
        def _class_name_to_filename(self, classname: str) -> str: ...
        def _render_access_badge(self, access: MethodAccessInfo | None) -> str: ...

    def _render_class_hierarchy_section(
        self,
        hierarchy: ClassHierarchyInfo,
        class_link: Callable[[str], str],
    ) -> str:
        """Render the Inherits-from / Subclasses block for a class page."""
        parts: list[str] = []
        if hierarchy.parent_class:
            parts.append(
                f"<p><strong>Inherits from:</strong> "
                f'<a href="{class_link(hierarchy.parent_class)}">'
                f"<code>{hierarchy.parent_class}</code></a></p>"
            )
        if hierarchy.child_classes:
            items = "".join(
                f'<li><a href="{class_link(c)}"><code>{c}</code></a></li>'
                for c in hierarchy.child_classes
            )
            parts.append(
                f"<p><strong>Subclasses:</strong></p>"
                f'<ul class="subclass-list">{items}</ul>'
            )
        if not parts:
            return (
                "<p>Root class — no parent or subclasses in this documentation set.</p>"
            )
        return "".join(parts)

    def _render_class_properties_table(self, fields: list[ClassFieldEntry]) -> str:
        """Render the class's private-data fields, marking inherited vs own."""
        if not fields:
            return "<p>No private data fields</p>"

        rows = []
        for entry in fields:
            f = entry.field
            type_str = f.type.underlying_type if f.type else "Any"
            origin = "inherited" if entry.inherited else "own"
            origin_badge = (
                f'<span class="field-origin field-origin-{origin}">{origin}</span>'
            )
            rows.append(
                f"""
            <tr>
                <td>{f.name}</td>
                <td><code>{type_str}</code></td>
                <td>{origin_badge}</td>
            </tr>
            """
            )

        return f"""
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Origin</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
        """

    def _render_class_methods_list(
        self,
        methods: list[str],
        method_access: dict[str, MethodAccessInfo],
        method_link: Callable[[str], str],
    ) -> str:
        """Render the class's method list, each linked to its VI page."""
        if not methods:
            return "<p>No methods</p>"

        items = []
        for vi_name in methods:
            display_name = vi_name.rsplit(":", 1)[-1]
            badge_html = self._render_access_badge(method_access.get(vi_name))
            items.append(
                f'<li><a href="{method_link(vi_name)}">'
                f"<code>{display_name}</code></a>{badge_html}</li>"
            )

        return f'<ul class="method-list">{"".join(items)}</ul>'

    def _render_class_page(
        self,
        hierarchy: ClassHierarchyInfo,
        method_access: dict[str, MethodAccessInfo],
    ) -> str:
        """Render the landing page HTML for one loaded class."""
        classname = hierarchy.classname

        def method_link(vi_name: str) -> str:
            full = self._vi_name_to_filename(vi_name)
            return full.split("/", 1)[1] if "/" in full else full

        def class_link(target_classname: str) -> str:
            return "../" + self._class_name_to_filename(target_classname)

        hierarchy_html = self._render_class_hierarchy_section(hierarchy, class_link)
        properties_html = self._render_class_properties_table(hierarchy.fields)
        methods_html = self._render_class_methods_list(
            hierarchy.methods, method_access, method_link
        )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{classname} - {self.doc_title}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <nav class="breadcrumb">
        <a href="../index.html">{self.doc_title}</a> / <span>{classname}</span>
    </nav>

    <header>
        <div class="vi-header-text">
            <h1>{classname}</h1>
            <p class="vi-type">Class</p>
        </div>
    </header>

    <main>
        <section id="hierarchy">
            <h2>Hierarchy</h2>
            {hierarchy_html}
        </section>

        <section id="properties">
            <h2>Properties</h2>
            {properties_html}
        </section>

        <section id="methods">
            <h2>Methods</h2>
            {methods_html}
        </section>
    </main>

    <footer>
        <p>Generated by lvkit generate_documents</p>
        <p class="trademark">LabVIEW, NI, and National Instruments are trademarks of
        National Instruments Corporation. lvkit is an independent project, not
        affiliated with, authorized by, endorsed by, or sponsored by NI.</p>
    </footer>
</body>
</html>
"""
