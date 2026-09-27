"""Typedef page rendering mixin for HTMLDocGenerator.

Methods: _render_typedef_fields_table, _render_typedef_values, _typedef_ref_href,
_render_typedef_refs, _render_typedef_page.
"""

from __future__ import annotations

from html import escape
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, NamedTuple

from lvkit.graph.node_kinds import NodeType
from lvkit.graph.typedef import (
    TypedefField,
    TypedefInfo,
    TypedefRef,
    field_default_literal,
    walk_typedef_fields,
)
from lvkit.models import LVType


def _type_text(lv_type: LVType | None) -> str:
    return escape(lv_type.type_descriptor()) if lv_type is not None else "unknown"


def _default_text(f: TypedefField) -> str:
    literal = field_default_literal(f)
    return "" if literal is None else escape(literal)


class TypedefPage(NamedTuple):
    """One typedef's page: its path relative to the output directory and its
    display name."""

    filename: str
    name: str


class TypedefPageMixin:
    """Mixin providing type-definition (``.ctl``) page rendering methods."""

    # These attributes are defined on HTMLDocGenerator in core.py
    doc_title: str
    all_vis: set[str]
    class_pages: dict[str, str]
    typedef_pages: dict[str, TypedefPage]

    if TYPE_CHECKING:
        # Stubs for methods defined on other mixins, resolved via MRO
        def _vi_name_to_filename(self, vi_name: str) -> str: ...

    def _render_typedef_fields_table(self, fields: tuple[TypedefField, ...]) -> str:
        """The cluster's fields (nested ones indented), each with its type and
        recorded default."""
        rows = [
            f'<tr><td style="padding-left:{1 + 1.5 * w.depth}em">'
            f"{escape(w.field.name)}</td>"
            f"<td><code>{_type_text(w.field.lv_type)}</code></td>"
            f"<td>{_default_text(w.field)}</td></tr>"
            for w in walk_typedef_fields(fields)
        ]
        return (
            "<table><thead><tr><th>Name</th><th>Type</th><th>Default</th></tr>"
            f"</thead><tbody>{''.join(rows)}</tbody></table>"
        )

    def _render_typedef_values(self, root: LVType) -> str:
        """An enum / ring's items, in value order."""
        items = sorted((root.values or {}).items(), key=lambda kv: kv[1].value)
        rows = "".join(
            f"<tr><td>{item.value}</td><td>{escape(name)}</td></tr>"
            for name, item in items
        )
        return (
            "<table><thead><tr><th>Value</th><th>Name</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>"
        )

    def _typedef_ref_href(self, ref: TypedefRef) -> str | None:
        """The page (relative to a typedef page) a reference has, or None.

        The pages are keyed by different identities: typedef and VI pages by the
        file's PATH key, class pages by the class's qualified name (as
        ``generate_class_page`` records them)."""
        if ref.node_type == NodeType.TYPEDEF and ref.path in self.typedef_pages:
            return PurePosixPath(self.typedef_pages[ref.path].filename).name
        if ref.node_type == NodeType.CLASS and ref.qualified in self.class_pages:
            return "../" + self.class_pages[ref.qualified]
        if ref.node_type == NodeType.VI and ref.path in self.all_vis:
            return "../" + self._vi_name_to_filename(ref.path)
        return None

    def _render_typedef_refs(self, refs: tuple[TypedefRef, ...], empty: str) -> str:
        if not refs:
            return f"<p>{empty}</p>"
        items = []
        for ref in refs:
            name = f"<code>{escape(ref.qualified)}</code>"
            href = self._typedef_ref_href(ref)
            link = f'<a href="{escape(href)}">{name}</a>' if href else name
            items.append(f"<li>{link} ({ref.node_type.value})</li>")
        return f'<ul class="dependency-list">{"".join(items)}</ul>'

    def _render_typedef_page(
        self,
        info: TypedefInfo,
        front_panel_svg: str | None,
        front_panel_note: str | None = None,
    ) -> str:
        """Render the page HTML for one loaded ``.ctl`` typedef. Without a front
        panel, ``front_panel_note`` (why it is missing) takes its place; with
        neither, the section is left out."""
        root = info.root_type
        name = escape(info.name)
        sections: list[str] = []
        if front_panel_svg:
            sections.append(
                '<section id="front-panel"><h2>Front Panel</h2>'
                f'<div class="diagram-container" style="overflow:auto">'
                f"{front_panel_svg}</div></section>"
            )
        elif front_panel_note:
            sections.append(
                '<section id="front-panel"><h2>Front Panel</h2>'
                f'<div class="diagram-note">{escape(front_panel_note)}</div>'
                "</section>"
            )
        type_line = f"<p><strong>Type:</strong> <code>{_type_text(root)}</code>"
        if info.default is not None:
            type_line += f" — default <code>{escape(str(info.default))}</code>"
        sections.append(f'<section id="type"><h2>Type</h2>{type_line}</p></section>')
        if root.values:
            sections.append(
                '<section id="values"><h2>Values</h2>'
                f"{self._render_typedef_values(root)}</section>"
            )
        if info.fields:
            sections.append(
                '<section id="fields"><h2>Fields</h2>'
                f"{self._render_typedef_fields_table(info.fields)}</section>"
            )
        sections.append(
            '<section id="uses"><h2>Uses</h2>'
            f"{self._render_typedef_refs(info.uses, 'References no other type.')}"
            "</section>"
        )
        if info.owned_by:
            sections.append(
                '<section id="owned-by"><h2>Owned By</h2>'
                f"{self._render_typedef_refs(info.owned_by, '')}</section>"
            )
        unused = "Not used by anything in this documentation."
        sections.append(
            '<section id="used-by"><h2>Used By</h2>'
            f"{self._render_typedef_refs(info.used_by, unused)}</section>"
        )
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{name} - {escape(self.doc_title)}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <nav class="breadcrumb">
        <a href="../index.html">{escape(self.doc_title)}</a> / <span>{name}</span>
    </nav>

    <header>
        <div class="vi-header-text">
            <h1>{name}</h1>
            <p class="vi-type">Type definition</p>
        </div>
    </header>

    <main>
        {"".join(sections)}
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
