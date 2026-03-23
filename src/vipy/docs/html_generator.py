"""HTML documentation generator for LabVIEW VIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class MermaidRenderer:
    """Renders graph data to Mermaid flowchart syntax."""

    all_vis: set[str] = field(default_factory=set)
    _lines: list[str] = field(default_factory=list)
    _node_ids: dict[str, str] = field(default_factory=dict)
    _node_styles: list[tuple[str, str]] = field(default_factory=list)
    _subvi_nodes: list[tuple[str, str]] = field(default_factory=list)
    _counter: int = 0

    def _next_id(self) -> str:
        nid = f"n{self._counter}"
        self._counter += 1
        return nid

    @staticmethod
    def _escape(s: str) -> str:
        """Escape special chars for Mermaid labels."""
        if s == "" or s == '""':
            return "''"
        return s.replace('"', "'")

    def _render_operation(self, op, indent: str = "    ") -> None:
        """Render an operation, recursively handling loops.

        Args:
            op: Operation dataclass instance
            indent: Indentation string for nested elements
        """
        nid = self._next_id()
        self._node_ids[op.id] = nid
        labels = op.labels
        name = op.name or ""
        inner_nodes = op.inner_nodes
        loop_type = op.loop_type

        if "Loop" in labels and inner_nodes:
            loop_label = "While Loop" if loop_type == "whileLoop" else "For Loop" if loop_type == "forLoop" else "Loop"
            self._lines.append(f'{indent}subgraph {nid}["{loop_label}"]')
            for inner_op in inner_nodes:
                self._render_operation(inner_op, indent + "    ")
            self._lines.append(f'{indent}end')
            self._node_styles.append((nid, "loopStyle"))
        elif "SubVI" in labels:
            label = self._escape(name or "SubVI")
            self._lines.append(f'{indent}{nid}["{label}"]')
            self._node_styles.append((nid, "subviStyle"))
            if name:
                self._subvi_nodes.append((nid, name))
        elif "Primitive" in labels:
            label = self._escape(name or f"prim_{op.primResID or '?'}")
            self._lines.append(f'{indent}{nid}["{label}"]')
            self._node_styles.append((nid, "primitiveStyle"))
        elif "Loop" in labels:
            loop_label = "While Loop" if loop_type == "whileLoop" else "For Loop" if loop_type == "forLoop" else "Loop"
            self._lines.append(f'{indent}{nid}(["{loop_label}"])')
            self._node_styles.append((nid, "loopStyle"))
        elif "CaseStructure" in labels:
            label = self._escape(name or "Case")
            self._lines.append(f'{indent}{nid}{{{{"{label}"}}}}')
            self._node_styles.append((nid, "caseStyle"))
        else:
            # Include operation type for compound arithmetic
            operation = op.operation
            if operation and name:
                label = self._escape(f"{name} ({operation.upper()})")
            else:
                label = self._escape(name or "Operation")
            self._lines.append(f'{indent}{nid}["{label}"]')
            self._node_styles.append((nid, "operationStyle"))

    def render(self, graph: dict[str, Any], vi_name_to_filename: Callable[[str], str]) -> str:
        """Render graph to Mermaid flowchart HTML."""
        inputs = graph.get("inputs", [])
        outputs = graph.get("outputs", [])
        operations = graph.get("operations", [])
        constants = graph.get("constants", [])
        data_flow = graph.get("data_flow", [])

        if not (inputs or outputs or operations or constants):
            return "<p>No dataflow graph available</p>"

        # Reset state
        self._lines = ["<pre class='mermaid'>", "flowchart LR"]
        self._node_ids = {}
        self._node_styles = []
        self._subvi_nodes = []
        self._counter = 0

        # Render inputs (controls)
        for inp in inputs:
            nid = self._next_id()
            self._node_ids[inp.id] = nid
            name = inp.name or "input"
            lv_type = inp.python_type()
            label = f"{name}: {lv_type}" if lv_type and lv_type != "Any" else name
            self._lines.append(f'    {nid}[/"{self._escape(label)}"/]')
            self._node_styles.append((nid, "controlStyle"))

        # Render outputs (indicators)
        for out in outputs:
            nid = self._next_id()
            self._node_ids[out.id] = nid
            name = out.name or "output"
            lv_type = out.python_type()
            label = f"{name}: {lv_type}" if lv_type and lv_type != "Any" else name
            self._lines.append(f'    {nid}[\\"{self._escape(label)}"\\]')
            self._node_styles.append((nid, "indicatorStyle"))

        # Render constants
        for const in constants:
            nid = self._next_id()
            self._node_ids[const.id] = nid
            value = const.value

            # Check if this is an enum constant - display member name instead of raw value
            if const.lv_type and const.lv_type.values and value is not None:
                try:
                    int_value = int(value)
                    for member_name, enum_val in const.lv_type.values.items():
                        if enum_val.value == int_value:
                            value_str = member_name
                            break
                    else:
                        value_str = str(value)
                except (ValueError, TypeError, AttributeError):
                    value_str = str(value)
            else:
                value_str = str(value) if value is not None else "None"

            if len(value_str) > 30:
                value_str = value_str[:27] + "..."
            self._lines.append(f'    {nid}[["{self._escape(value_str)}"]]')
            self._node_styles.append((nid, "constantStyle"))

        # Render operations
        for op in operations:
            self._render_operation(op)

        # Render edges
        for wire in data_flow:
            from_id = wire.from_parent_id
            if from_id not in self._node_ids:
                from_id = wire.from_terminal_id
            to_id = wire.to_parent_id
            if to_id not in self._node_ids:
                to_id = wire.to_terminal_id

            if from_id in self._node_ids and to_id in self._node_ids:
                self._lines.append(f'    {self._node_ids[from_id]} --> {self._node_ids[to_id]}')

        # Add click links for SubVIs
        for nid, vi_name in self._subvi_nodes:
            if vi_name in self.all_vis:
                link = vi_name_to_filename(vi_name)
                self._lines.append(f'    click {nid} href "{link}"')

        # Style definitions
        self._lines.append("    classDef controlStyle fill:#bbdefb,stroke:#1976d2,stroke-width:2px")
        self._lines.append("    classDef indicatorStyle fill:#fff3e0,stroke:#f57c00,stroke-width:2px")
        self._lines.append("    classDef constantStyle fill:#f3e5f5,stroke:#7b1fa2")
        self._lines.append("    classDef subviStyle fill:#e8f5e9,stroke:#388e3c")
        self._lines.append("    classDef primitiveStyle fill:#fffde7,stroke:#f9a825,stroke-width:2px")
        self._lines.append("    classDef operationStyle fill:#e0e0e0,stroke:#616161,stroke-width:2px")
        self._lines.append("    classDef loopStyle fill:#e1bee7,stroke:#8e24aa,stroke-width:2px")
        self._lines.append("    classDef caseStyle fill:#b2ebf2,stroke:#00838f,stroke-width:2px")

        # Apply styles
        for nid, style in self._node_styles:
            self._lines.append(f"    class {nid} {style}")

        self._lines.append("</pre>")
        return "\n".join(self._lines)


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
        self._mermaid = MermaidRenderer()
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

    def _render_vi_page(self, vi_data: dict[str, Any]) -> str:
        """Render VI page HTML."""
        vi_name = vi_data["vi_name"]
        controls = vi_data.get("controls", [])
        indicators = vi_data.get("indicators", [])
        dependencies = vi_data.get("dependencies", {})
        callers = vi_data.get("callers", [])
        graph = vi_data.get("graph", {})
        is_poly = vi_data.get("is_polymorphic", False)
        poly_variants = vi_data.get("poly_variants", [])
        variant_params = vi_data.get("variant_params", [])
        icon_path = vi_data.get("icon_path")

        # Create a relative link function for this VI's directory
        current_lib = self._extract_library_group(vi_name)

        def relative_link(target_vi_name: str) -> str:
            target_path = self._vi_name_to_filename(target_vi_name)
            target_lib = self._extract_library_group(target_vi_name)
            # If same library, use just the filename
            if target_lib == current_lib:
                return target_path.split("/", 1)[1] if "/" in target_path else target_path
            # Otherwise use relative path from subdirectory
            return "../" + target_path

        # Build sections with relative links
        controls_html = self._render_controls_table(controls)
        indicators_html = self._render_indicators_table(indicators)
        dependencies_html = self._render_dependencies_section(dependencies, relative_link)
        callers_html = self._render_callers_section(callers, relative_link)
        self._mermaid.all_vis = self.all_vis
        dataflow_html = self._mermaid.render(graph, relative_link)

        # Polymorphic section if applicable
        poly_html = ""
        if is_poly and variant_params:
            poly_html = self._render_polymorphic_section(variant_params, relative_link)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{vi_name} - {self.doc_title}</title>
    <link rel="stylesheet" href="../style.css">
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
        mermaid.initialize({{
            startOnLoad: true,
            theme: 'default',
            flowchart: {{ curve: 'monotoneX' }}
        }});
        await mermaid.contentLoaded();
    </script>
</head>
<body>
    <nav class="breadcrumb">
        <a href="../index.html">{self.doc_title}</a> / <span>{vi_name}</span>
    </nav>

    <header>
        <div class="vi-header">
            {f'<img src="{icon_path}" alt="VI Icon" class="vi-icon">' if icon_path else ''}
            <div class="vi-header-text">
                <h1>{vi_name}</h1>
                <p class="vi-type">{"Polymorphic " if is_poly else ""}{self.doc_type.capitalize()}</p>
            </div>
        </div>
    </header>

    <main>
        <section id="summary">
            <h2>Summary</h2>
            <p>{"⚡ Polymorphic VI with " + str(len(variant_params)) + " variant(s)" if is_poly else f"Takes {len(controls)} input(s), returns {len(indicators)} output(s)"}</p>
        </section>

        {poly_html}

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
        <p>Generated by vipy generate_documents</p>
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

    def _render_dependencies_section(self, dependencies: dict[str, str], link_fn: Callable[[str], str]) -> str:
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
            display_name = qualified_name.split(":")[-1] if ":" in qualified_name else qualified_name

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

    def _render_callers_section(self, callers: list[str], link_fn: Callable[[str], str]) -> str:
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
            display_name = qualified_name.split(":")[-1] if ":" in qualified_name else qualified_name
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

    def _render_polymorphic_section(self, variant_params: list[dict], link_fn: Callable[[str], str]) -> str:
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
            variant_links.append(f'<li><a href="{link}"><code>{variant["name"]}</code></a></li>')

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

            common_badge = '<span class="param-common">All</span>' if is_common else '<span class="param-some">Some</span>'
            cells = "".join(f"<td>{mark}</td>" for mark in present_in)
            param_rows.append(f"<tr><td><strong>{param_name}</strong> (input)</td><td>{common_badge}</td>{cells}</tr>")

        # Output parameters
        for param_name in sorted(all_output_names):
            is_common = param_name in common_outputs
            present_in = []
            for variant in variant_params:
                if any(out["name"] == param_name for out in variant["outputs"]):
                    present_in.append("✓")
                else:
                    present_in.append("—")

            common_badge = '<span class="param-common">All</span>' if is_common else '<span class="param-some">Some</span>'
            cells = "".join(f"<td>{mark}</td>" for mark in present_in)
            param_rows.append(f"<tr><td><strong>{param_name}</strong> (output)</td><td>{common_badge}</td>{cells}</tr>")

        # Build table header with variant names
        variant_headers = "".join(f"<th>{v['name'].split(':')[-1] if ':' in v['name'] else v['name']}</th>" for v in variant_params)

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

    def _extract_library_group(self, vi_name: str) -> str:
        """Extract library/group name from VI name for grouping.

        Examples:
            "GraphicalTestRunner.lvlib:Get Settings Path.vi" -> "GraphicalTestRunner.lvlib"
            "Build Path__ogtk.vi" -> "OpenG"
            "Get System Directory.vi" -> "vi.lib"
        """
        # Check for library-qualified name (Library.lvlib:VI.vi or Library.lvclass:VI.vi)
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
                    # icon_map paths are "../icons/..." for VI pages, need "icons/..." for index
                    icon_path = self.icon_map[vi_name].replace("../", "")
                    icon_html = f'<img src="{icon_path}" alt="" class="vi-icon-small">'
                vi_links.append(f'<li>{icon_html}<a href="{link}">{display_name}</a></li>')

            library_sections.append(f"""
            <details class="library-accordion" open>
                <summary class="library-header">
                    <div class="library-header-content">
                        <span class="library-name">{library}</span>
                        <span class="library-count">{len(vis_in_library)} VI{"s" if len(vis_in_library) != 1 else ""}</span>
                    </div>
                </summary>
                <ul class="vi-list">
                    {''.join(vi_links)}
                </ul>
            </details>
            """)

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
            <p>Total VIs: {len(all_vis)} across {len(sorted_libraries)} librar{"y" if len(sorted_libraries) == 1 else "ies"}</p>
            {''.join(library_sections)}
        </section>
    </main>

    <footer>
        <p>Generated by vipy generate_documents</p>
    </footer>
</body>
</html>
"""

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """Convert VI name to safe HTML filename with library subdirectory.

        Returns path like "OpenG/Build_Path_ogtk.html" or "vi.lib/Get_System_Directory.html"
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
        safe_name = safe_name.replace("[", "_").replace("]", "_").replace("{", "_").replace("}", "_")
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
