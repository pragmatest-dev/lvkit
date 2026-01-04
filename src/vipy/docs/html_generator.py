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
            lv_type = inp.type or ""
            label = f"{name}: {lv_type}" if lv_type and lv_type != "Any" else name
            self._lines.append(f'    {nid}[/"{self._escape(label)}"/]')
            self._node_styles.append((nid, "controlStyle"))

        # Render outputs (indicators)
        for out in outputs:
            nid = self._next_id()
            self._node_ids[out.id] = nid
            name = out.name or "output"
            lv_type = out.type or ""
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

        # Build sections
        controls_html = self._render_controls_table(controls)
        indicators_html = self._render_indicators_table(indicators)
        dependencies_html = self._render_dependencies_section(dependencies)
        callers_html = self._render_callers_section(callers)
        self._mermaid.all_vis = self.all_vis
        dataflow_html = self._mermaid.render(graph, self._vi_name_to_filename)

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{vi_name} - {self.doc_title}</title>
    <link rel="stylesheet" href="style.css">
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
        <a href="index.html">{self.doc_title}</a> / <span>{vi_name}</span>
    </nav>

    <header>
        <h1>{vi_name}</h1>
        <p class="vi-type">{self.doc_type.capitalize()}</p>
    </header>

    <main>
        <section id="summary">
            <h2>Summary</h2>
            <p>Takes {len(controls)} input(s), returns {len(indicators)} output(s)</p>
        </section>

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

    def _render_dependencies_section(self, dependencies: dict[str, str]) -> str:
        """Render dependencies with links."""
        if not dependencies:
            return "<p>No SubVI calls</p>"

        items = []
        for vi_name, description in dependencies.items():
            # Only create link if this VI has a documentation page
            if vi_name in self.all_vis:
                link = self._vi_name_to_filename(vi_name)
                items.append(
                    f"""
            <li>
                <a href="{link}"><code>{vi_name}</code></a> - {description}
            </li>
            """
                )
            else:
                # Just show the name without a link for vilib VIs
                items.append(
                    f"""
            <li>
                <code>{vi_name}</code> - {description}
            </li>
            """
                )

        return f"""
        <ul class="dependency-list">
            {''.join(items)}
        </ul>
        """

    def _render_callers_section(self, callers: list[str]) -> str:
        """Render reverse links (who calls this VI)."""
        if not callers:
            return "<p>Not called by any VI in this documentation</p>"

        items = []
        for caller_name in callers:
            link = self._vi_name_to_filename(caller_name)
            items.append(
                f"""
            <li><a href="{link}"><code>{caller_name}</code></a></li>
            """
            )

        return f"""
        <ul class="caller-list">
            {''.join(items)}
        </ul>
        """

    def _render_index_page(self, all_vis: list[str]) -> str:
        """Render index page with table of contents."""
        vi_links = []
        for vi_name in sorted(all_vis):
            link = self._vi_name_to_filename(vi_name)
            vi_links.append(
                f"""
            <li><a href="{link}">{vi_name}</a></li>
            """
            )

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
            <p>Total VIs: {len(all_vis)}</p>
            <ul class="vi-list">
                {''.join(vi_links)}
            </ul>
        </section>
    </main>

    <footer>
        <p>Generated by vipy generate_documents</p>
    </footer>
</body>
</html>
"""

    def _vi_name_to_filename(self, vi_name: str) -> str:
        """Convert VI name to safe HTML filename."""
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
        return f"{safe_name}.html"

    def _get_css(self) -> str:
        """Return CSS stylesheet."""
        return """/* vipy documentation stylesheet */

:root {
    --primary-color: #2c3e50;
    --secondary-color: #3498db;
    --bg-color: #ecf0f1;
    --text-color: #2c3e50;
    --border-color: #bdc3c7;
    --code-bg: #f8f9fa;
}

* {
    margin: 0;
    padding: 0;
    box-sizing: border-box;
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
    line-height: 1.6;
    color: var(--text-color);
    background: var(--bg-color);
    padding: 20px;
}

.breadcrumb {
    background: white;
    padding: 10px 20px;
    margin-bottom: 20px;
    border-radius: 5px;
    font-size: 14px;
}

.breadcrumb a {
    color: var(--secondary-color);
    text-decoration: none;
}

.breadcrumb a:hover {
    text-decoration: underline;
}

header {
    background: white;
    padding: 30px;
    border-radius: 5px;
    margin-bottom: 20px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

h1 {
    color: var(--primary-color);
    margin-bottom: 10px;
}

h2 {
    color: var(--primary-color);
    margin-top: 20px;
    margin-bottom: 10px;
    border-bottom: 2px solid var(--border-color);
    padding-bottom: 5px;
}

main {
    background: white;
    padding: 30px;
    border-radius: 5px;
    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}

section {
    margin-bottom: 30px;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 15px 0;
}

table th,
table td {
    text-align: left;
    padding: 12px;
    border-bottom: 1px solid var(--border-color);
}

table th {
    background: var(--code-bg);
    font-weight: 600;
}

code {
    background: var(--code-bg);
    padding: 2px 6px;
    border-radius: 3px;
    font-family: "SF Mono", Monaco, "Courier New", monospace;
    font-size: 0.9em;
}

pre {
    background: var(--code-bg);
    padding: 15px;
    border-radius: 5px;
    overflow-x: auto;
    font-family: "SF Mono", Monaco, "Courier New", monospace;
}

ul, ol {
    margin-left: 30px;
    margin-top: 10px;
}

li {
    margin-bottom: 8px;
}

a {
    color: var(--secondary-color);
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

.vi-type {
    color: #7f8c8d;
    font-size: 14px;
}

.subtitle {
    color: #7f8c8d;
    font-size: 18px;
}

footer {
    text-align: center;
    margin-top: 40px;
    color: #7f8c8d;
    font-size: 14px;
}

.dataflow {
    font-family: monospace;
    line-height: 1.4;
}

.dependency-list,
.caller-list,
.vi-list {
    list-style: none;
    margin-left: 0;
}

.dependency-list li,
.caller-list li,
.vi-list li {
    padding: 8px 0;
    border-bottom: 1px solid var(--border-color);
}

.dependency-list li:last-child,
.caller-list li:last-child,
.vi-list li:last-child {
    border-bottom: none;
}
"""
