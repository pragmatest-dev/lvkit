"""HTML documentation generator for LabVIEW VIs."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from lvkit.graph.models import (
    ClassFieldEntry,
    ClassHierarchyInfo,
    MethodAccessInfo,
    MethodOverrideInfo,
)


class HTMLDocGenerator:
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

    def _render_diagram(
        self,
        diagram_svg: str,
        subvi_nodes: dict[str, str],
        relative_link: Callable[[str], str],
    ) -> str:
        """Embed the SVG block diagram and attach click-to-navigate behaviour to
        its subVI nodes.

        Navigation lives HERE in the doc layer, keyed on the ``data-node`` ids
        the renderer already emits — the renderer itself stays navigation-free.
        Only subVIs that have their own documented page become links."""
        nav = {
            node_id: relative_link(target)
            for node_id, target in subvi_nodes.items()
            if target in self.all_vis
        }
        nav_script = ""
        if nav:
            nav_script = (
                "<script>/*<![CDATA[*/(function(){"
                f"var NAV={json.dumps(nav)};"
                "document.querySelectorAll('.diagram-container [data-node]')"
                ".forEach(function(g){"
                "var u=NAV[g.getAttribute('data-node')];if(!u)return;"
                "g.style.cursor='pointer';g.setAttribute('tabindex','0');"
                "g.addEventListener('click',function(){location.href=u;});"
                "});})();/*]]>*/</script>"
            )
        return (
            '<div class="diagram-container" style="overflow:auto">'
            f"{diagram_svg}</div>{nav_script}"
        )

    def _render_vi_page(self, vi_data: dict[str, Any]) -> str:
        """Render VI page HTML."""
        vi_name = vi_data["vi_name"]
        controls = vi_data.get("controls", [])
        indicators = vi_data.get("indicators", [])
        dependencies = vi_data.get("dependencies", {})
        callers = vi_data.get("callers", [])
        is_poly = vi_data.get("is_polymorphic", False)
        variant_params = vi_data.get("variant_params", [])
        icon_path = vi_data.get("icon_path")
        owning_class = vi_data.get("owning_class")
        method_access = vi_data.get("method_access")
        method_overrides = vi_data.get("method_overrides")

        # Create a relative link function for this VI's directory
        current_lib = self._extract_library_group(vi_name)

        def relative_link(target_vi_name: str) -> str:
            target_path = self._vi_name_to_filename(target_vi_name)
            target_lib = self._extract_library_group(target_vi_name)
            # If same library, use just the filename
            if target_lib == current_lib:
                return (
                    target_path.split("/", 1)[1]
                    if "/" in target_path
                    else target_path
                )
            # Otherwise use relative path from subdirectory
            return "../" + target_path

        # Build sections with relative links
        controls_html = self._render_controls_table(controls)
        indicators_html = self._render_indicators_table(indicators)
        dependencies_html = self._render_dependencies_section(
            dependencies, relative_link
        )
        callers_html = self._render_callers_section(callers, relative_link)
        diagram_svg = vi_data.get("diagram_svg")
        if diagram_svg:
            dataflow_html = self._render_diagram(
                diagram_svg, vi_data.get("subvi_nodes", {}), relative_link
            )
        else:
            dataflow_html = (
                '<div class="diagram-note">Block diagram unavailable — '
                "no diagram geometry for this VI.</div>"
            )

        # Polymorphic section if applicable
        poly_html = ""
        if is_poly and variant_params:
            poly_html = self._render_polymorphic_section(variant_params, relative_link)

        # Class hierarchy: breadcrumb link back to the owning class, an
        # access-scope badge, and an Overrides/Overridden-by section.
        breadcrumb_class_html = ""
        display_vi_name = vi_name
        if owning_class:
            class_href = "../" + self._class_name_to_filename(owning_class)
            breadcrumb_class_html = f'<a href="{class_href}">{owning_class}</a> / '
            display_vi_name = vi_name.rsplit(":", 1)[-1]
        access_badge_html = self._render_access_badge(method_access)
        overrides_html = self._render_method_overrides_section(
            method_overrides, relative_link
        )

        # Pre-compute values used inside the HTML f-string
        icon_html = (
            f'<img src="{icon_path}" alt="VI Icon" class="vi-icon">'
            if icon_path
            else ""
        )
        vi_type_label = (
            f"{'Polymorphic ' if is_poly else ''}{self.doc_type.capitalize()}"
        )
        if is_poly:
            summary_text = (
                f"Polymorphic VI with {len(variant_params)} variant(s)"
            )
        else:
            summary_text = (
                f"Takes {len(controls)} input(s), returns {len(indicators)} output(s)"
            )

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{vi_name} - {self.doc_title}</title>
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <nav class="breadcrumb">
        <a href="../index.html">{self.doc_title}</a> /
        {breadcrumb_class_html}<span>{display_vi_name}</span>
    </nav>

    <header>
        <div class="vi-header">
            {icon_html}
            <div class="vi-header-text">
                <h1>{vi_name}{access_badge_html}</h1>
                <p class="vi-type">{vi_type_label}</p>
            </div>
        </div>
    </header>

    <main>
        <section id="summary">
            <h2>Summary</h2>
            <p>{summary_text}</p>
        </section>

        {poly_html}

        {overrides_html}

        <section id="inputs">
            <h2>Inputs (Controls)</h2>
            {controls_html}
        </section>

        <section id="outputs">
            <h2>Outputs (Indicators)</h2>
            {indicators_html}
        </section>

        <section id="dataflow">
            <h2>Block Diagram (Dataflow)</h2>
            {dataflow_html}
        </section>

        <section id="dependencies">
            <h2>Dependencies (Calls)</h2>
            {dependencies_html}
        </section>

        <section id="callers">
            <h2>Used By</h2>
            {callers_html}
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

    def _render_controls_table(self, controls: list[dict[str, Any]]) -> str:
        """Render inputs table."""
        if not controls:
            return "<p>No inputs</p>"

        rows = []
        for ctrl in controls:
            default_val = ctrl.get("default_value")
            if default_val is None:
                default_val = "—"
            rows.append(
                f"""
            <tr>
                <td>{ctrl['name']}</td>
                <td><code>{ctrl['type']}</code></td>
                <td>{default_val}</td>
            </tr>
            """
            )

        return f"""
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Type</th>
                    <th>Default</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        """

    def _render_indicators_table(self, indicators: list[dict[str, Any]]) -> str:
        """Render outputs table."""
        if not indicators:
            return "<p>No outputs</p>"

        rows = []
        for ind in indicators:
            rows.append(
                f"""
            <tr>
                <td>{ind['name']}</td>
                <td><code>{ind['type']}</code></td>
            </tr>
            """
            )

        return f"""
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Type</th>
                </tr>
            </thead>
            <tbody>
                {''.join(rows)}
            </tbody>
        </table>
        """

    def _render_dependencies_section(
        self, dependencies: dict[str, str], link_fn: Callable[[str], str]
    ) -> str:
        """Render dependencies with links.

        Args:
            dependencies: Dict mapping QUALIFIED VI names to descriptions
            link_fn: Function to generate link paths from qualified names
        """
        if not dependencies:
            return "<p>No SubVI calls</p>"

        items = []
        for qualified_name, description in dependencies.items():
            # Display the short name but link using qualified name
            display_name = (
                qualified_name.split(":")[-1]
                if ":" in qualified_name
                else qualified_name
            )

            # Only create link if this VI has a documentation page
            if qualified_name in self.all_vis:
                link = link_fn(qualified_name)
                items.append(
                    f"""
            <li>
                <a href="{link}"><code>{display_name}</code></a> - {description}
            </li>
            """
                )
            else:
                # Just show the name without a link for vilib VIs or external deps
                items.append(
                    f"""
            <li>
                <code>{display_name}</code> - {description}
            </li>
            """
                )

        return f"""
        <ul class="dependency-list">
            {''.join(items)}
        </ul>
        """

    def _render_callers_section(
        self, callers: list[str], link_fn: Callable[[str], str]
    ) -> str:
        """Render reverse links (who calls this VI).

        Args:
            callers: List of QUALIFIED VI names that call this VI
            link_fn: Function to generate link paths from qualified names
        """
        if not callers:
            return "<p>Not called by any VI in this documentation</p>"

        items = []
        for qualified_name in callers:
            # Display the short name but link using qualified name
            display_name = (
                qualified_name.split(":")[-1]
                if ":" in qualified_name
                else qualified_name
            )
            link = link_fn(qualified_name)
            items.append(
                f"""
            <li><a href="{link}"><code>{display_name}</code></a></li>
            """
            )

        return f"""
        <ul class="caller-list">
            {''.join(items)}
        </ul>
        """

    def _render_polymorphic_section(
        self, variant_params: list[dict], link_fn: Callable[[str], str]
    ) -> str:
        """Render polymorphic variants section with parameter comparison.

        Args:
            variant_params: List of dicts with variant info (name, inputs, outputs)
            link_fn: Function to generate links to other VIs
        """
        if not variant_params:
            return ""

        # Collect all parameter names across all variants
        all_input_names = set()
        all_output_names = set()
        for variant in variant_params:
            for inp in variant["inputs"]:
                all_input_names.add(inp["name"])
            for out in variant["outputs"]:
                all_output_names.add(out["name"])

        # Check which params are common to ALL variants
        common_inputs = set(all_input_names)
        common_outputs = set(all_output_names)
        for variant in variant_params:
            variant_input_names = {inp["name"] for inp in variant["inputs"]}
            variant_output_names = {out["name"] for out in variant["outputs"]}
            common_inputs &= variant_input_names
            common_outputs &= variant_output_names

        # Build variant links
        variant_links = []
        for variant in variant_params:
            link = link_fn(variant["name"])
            variant_links.append(
                f'<li><a href="{link}"><code>{variant["name"]}</code></a></li>'
            )

        # Build parameter comparison table
        param_rows = []

        # Input parameters
        for param_name in sorted(all_input_names):
            is_common = param_name in common_inputs
            present_in = []
            for variant in variant_params:
                if any(inp["name"] == param_name for inp in variant["inputs"]):
                    present_in.append("✓")
                else:
                    present_in.append("—")

            common_badge = (
                '<span class="param-common">All</span>'
                if is_common
                else '<span class="param-some">Some</span>'
            )
            cells = "".join(f"<td>{mark}</td>" for mark in present_in)
            param_rows.append(
                f"<tr><td><strong>{param_name}</strong> (input)</td>"
                f"<td>{common_badge}</td>{cells}</tr>"
            )

        # Output parameters
        for param_name in sorted(all_output_names):
            is_common = param_name in common_outputs
            present_in = []
            for variant in variant_params:
                if any(out["name"] == param_name for out in variant["outputs"]):
                    present_in.append("✓")
                else:
                    present_in.append("—")

            common_badge = (
                '<span class="param-common">All</span>'
                if is_common
                else '<span class="param-some">Some</span>'
            )
            cells = "".join(f"<td>{mark}</td>" for mark in present_in)
            param_rows.append(
                f"<tr><td><strong>{param_name}</strong> (output)</td>"
                f"<td>{common_badge}</td>{cells}</tr>"
            )

        # Build table header with variant names
        variant_headers = "".join(
            f"<th>{v['name'].split(':')[-1] if ':' in v['name'] else v['name']}</th>"
            for v in variant_params
        )

        return f"""
        <section id="polymorphic-variants" class="poly-section">
            <h2>⚡ Polymorphic Variants</h2>
            <p>This VI has {len(variant_params)} implementation variant(s):</p>
            <ul class="variant-list">
                {''.join(variant_links)}
            </ul>

            <h3>Parameter Comparison</h3>
            <table class="param-comparison">
                <thead>
                    <tr>
                        <th>Parameter</th>
                        <th>Availability</th>
                        {variant_headers}
                    </tr>
                </thead>
                <tbody>
                    {''.join(param_rows)}
                </tbody>
            </table>
        </section>
        """

    def _render_access_badge(self, access: MethodAccessInfo | None) -> str:
        """Render a small access-scope badge (+ accessor kind, if any)."""
        if access is None or not access.scope:
            return ""
        html = f' <span class="scope-badge scope-{access.scope}">{access.scope}</span>'
        if access.is_accessor and access.accessor_type:
            html += (
                f' <span class="accessor-badge">{access.accessor_type}</span>'
            )
        return html

    def _render_method_overrides_section(
        self,
        overrides: MethodOverrideInfo | None,
        link_fn: Callable[[str], str],
    ) -> str:
        """Render the Overrides / Overridden-by hierarchy section for a method page.

        Returns "" when there is no override relationship to show.
        """
        if overrides is None:
            return ""

        parts: list[str] = []
        if overrides.overrides:
            parts.append(
                f'<p><strong>Overrides:</strong> '
                f'<a href="{link_fn(overrides.overrides)}">'
                f'<code>{overrides.overrides}</code></a></p>'
            )
        if overrides.overridden_by:
            items = "".join(
                f'<li><a href="{link_fn(v)}"><code>{v}</code></a></li>'
                for v in overrides.overridden_by
            )
            parts.append(
                f'<p><strong>Overridden by:</strong></p>'
                f'<ul class="override-list">{items}</ul>'
            )

        if not parts:
            return ""

        return f"""
        <section id="hierarchy">
            <h2>Class Hierarchy</h2>
            {''.join(parts)}
        </section>
        """

    def _class_name_to_filename(self, classname: str) -> str:
        """Convert a class qualified name to its landing-page path.

        Lives in the same per-class subdirectory as its method pages
        (``_vi_name_to_filename`` groups a method VI's page under its
        owning class's sanitized qualified name), so intra-class links
        need no "../" prefix. The basename is the raw qualified class name
        with ".html" appended — this can never collide with a method page's
        basename, since those always carry a ":<method>.vi" suffix baked in
        before sanitizing.
        """
        safe_lib = (
            classname.replace(".", "_").replace(":", "_").replace("/", "_")
        )
        return f"{safe_lib}/{classname}.html"

    def _render_class_hierarchy_section(
        self,
        hierarchy: ClassHierarchyInfo,
        class_link: Callable[[str], str],
    ) -> str:
        """Render the Inherits-from / Subclasses block for a class page."""
        parts: list[str] = []
        if hierarchy.parent_class:
            parts.append(
                f'<p><strong>Inherits from:</strong> '
                f'<a href="{class_link(hierarchy.parent_class)}">'
                f'<code>{hierarchy.parent_class}</code></a></p>'
            )
        if hierarchy.child_classes:
            items = "".join(
                f'<li><a href="{class_link(c)}"><code>{c}</code></a></li>'
                for c in hierarchy.child_classes
            )
            parts.append(
                f'<p><strong>Subclasses:</strong></p>'
                f'<ul class="subclass-list">{items}</ul>'
            )
        if not parts:
            return (
                "<p>Root class — no parent or subclasses in this "
                "documentation set.</p>"
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
                {''.join(rows)}
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
                f'<code>{display_name}</code></a>{badge_html}</li>'
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

    def _extract_library_group(self, vi_name: str) -> str:
        """Extract library/group name from VI name for grouping.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi"
                -> "GraphicalTestRunner.lvlib"
            "Build Path__ogtk.vi" -> "OpenG"
            "Get System Directory.vi" -> "vi.lib"
        """
        # Check for library-qualified name (Library.lvlib:VI.vi or lvclass:VI.vi)
        if ".lvlib:" in vi_name:
            return vi_name.split(":")[0]
        if ".lvclass:" in vi_name:
            return vi_name.split(":")[0]

        # Check for OpenG naming convention (__ogtk)
        if "__ogtk" in vi_name:
            return "OpenG"

        # Default to vi.lib for system VIs
        return "vi.lib"

    def _extract_display_name(self, vi_name: str) -> str:
        """Extract display name from full VI name.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi" -> "Get Settings Path"
            "Build Path__ogtk.vi" -> "Build Path"
            "Get System Directory.vi" -> "Get System Directory"
        """
        # Handle library-qualified names
        if ":" in vi_name:
            name = vi_name.split(":")[-1]
        else:
            name = vi_name

        # Remove .vi extension
        name = name.replace(".vi", "").replace(".VI", "")

        # Remove __ogtk suffix
        name = name.replace("__ogtk", "")

        return name.strip()

    def _render_index_page(self, all_vis: list[str]) -> str:
        """Render index page with table of contents, grouped by library."""
        # Group VIs by library
        grouped_vis: dict[str, list[str]] = {}
        for vi_name in all_vis:
            library = self._extract_library_group(vi_name)
            if library not in grouped_vis:
                grouped_vis[library] = []
            grouped_vis[library].append(vi_name)

        # Sort libraries and VIs within each library
        sorted_libraries = sorted(grouped_vis.keys())

        # Build grouped sections as accordions
        library_sections = []
        for library in sorted_libraries:
            vis_in_library = sorted(grouped_vis[library])
            vi_links = []
            for vi_name in vis_in_library:
                link = self._vi_name_to_filename(vi_name)
                display_name = self._extract_display_name(vi_name)
                # Get icon path (adjust from VI page relative to index relative)
                icon_html = ""
                if vi_name in self.icon_map:
                    # icon_map has "../icons/..." for VI pages; index needs "icons/..."
                    icon_path = self.icon_map[vi_name].replace("../", "")
                    icon_html = (
                        f'<img src="{icon_path}" alt="" class="vi-icon-small">'
                    )
                vi_links.append(
                    f'<li>{icon_html}<a href="{link}">{display_name}</a></li>'
                )

            vi_count = len(vis_in_library)
            vi_plural = "s" if vi_count != 1 else ""
            # Class group headers link to the class's landing page.
            if library in self.class_pages:
                library_name_html = (
                    f'<a href="{self.class_pages[library]}">{library}</a>'
                )
            else:
                library_name_html = library
            library_sections.append(f"""
            <details class="library-accordion" open>
                <summary class="library-header">
                    <div class="library-header-content">
                        <span class="library-name">{library_name_html}</span>
                        <span class="library-count">{vi_count} VI{vi_plural}</span>
                    </div>
                </summary>
                <ul class="vi-list">
                    {''.join(vi_links)}
                </ul>
            </details>
            """)

        lib_count = len(sorted_libraries)
        lib_word = "library" if lib_count == 1 else "libraries"
        toc_summary = f"Total VIs: {len(all_vis)} across {lib_count} {lib_word}"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{self.doc_title} - Documentation</title>
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <header>
        <h1>{self.doc_title}</h1>
        <p class="subtitle">{self.doc_type.capitalize()} Documentation</p>
    </header>

    <main>
        <section id="toc">
            <h2>Table of Contents</h2>
            <p>{toc_summary}</p>
            {''.join(library_sections)}
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

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """Convert VI name to safe HTML filename with library subdirectory.

        Returns path like "OpenG/Build_Path_ogtk.html"
        or "vi.lib/Get_System_Directory.html"
        """
        # Get library group for subdirectory
        library_group = self._extract_library_group(vi_name)

        # Sanitize library group name for filesystem
        safe_lib = library_group.replace(".", "_").replace(":", "_").replace("/", "_")

        # Handle qualified names (Library.lvlib:VI.vi)
        safe_name = vi_name.replace(":", "_").replace("/", "_").replace("\\", "_")
        # Remove .vi extension if present
        safe_name = safe_name.replace(".vi", "").replace(".VI", "")
        # Replace spaces and other unsafe characters with underscores
        safe_name = safe_name.replace(" ", "_").replace("(", "_").replace(")", "_")
        safe_name = (
            safe_name.replace("[", "_").replace("]", "_")
            .replace("{", "_").replace("}", "_")
        )
        safe_name = safe_name.replace("<", "_").replace(">", "_").replace("|", "_")
        safe_name = safe_name.replace("?", "_").replace("*", "_").replace('"', "_")
        # Remove any consecutive underscores
        while "__" in safe_name:
            safe_name = safe_name.replace("__", "_")

        return f"{safe_lib}/{safe_name}.html"

    def _get_css(self) -> str:
        """Return CSS stylesheet by reading from template file."""
        template_path = Path(__file__).parent / "template.css"
        return template_path.read_text(encoding="utf-8")
