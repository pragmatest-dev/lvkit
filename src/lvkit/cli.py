"""Command-line interface for lvkit."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .graph import InMemoryVIGraph
    from .render import ThemeMode

# Module scope stays LIGHT so a cache-HIT render/diff starts without the graph /
# parser / pylabview stack (~230 ms). ``__version__`` is a plain string;
# ``LoadMode`` comes from the dependency-free ``load_mode`` leaf (not
# ``graph.loading``, which pulls networkx); ``project_store`` is stdlib-only. The
# engine imports (graph, structure, lv_detect, the resolvers) are deferred into
# the specific commands that build/analyze — see the function-local imports.
from . import __version__
from .load_mode import LoadMode
from .project_store import (
    find_project_store,
    init_project_store,
    install_claude_skills,
    install_codex_skills,
    install_copilot_skills,
)


def _add_load_mode_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--load-mode {none,minimal,full}`` to a subparser. Each command
    picks its own default when it resolves the value (see _resolve_load_mode)."""
    parser.add_argument(
        "--load-mode",
        choices=[m.value for m in LoadMode],
        default=None,
        help=(
            "How deep to load dependencies: 'none' (this VI only), 'minimal' "
            "(this VI + direct SubVI connector panes + referenced-type fields; "
            "faithful render/diff), or 'full' (whole SubVI/class-method tree). "
            "Defaults per command."
        ),
    )


def _resolve_load_mode(
    args: argparse.Namespace,
    default: LoadMode,
) -> LoadMode:
    """Resolve the effective LoadMode for a command: an explicit ``--load-mode``
    wins, else the command's default."""
    chosen = getattr(args, "load_mode", None)
    return LoadMode(chosen) if chosen else default


def _add_theme_arg(parser: argparse.ArgumentParser) -> None:
    """Add ``--theme {light,dark,auto}`` to a subparser (render/diff)."""
    parser.add_argument(
        "--theme",
        choices=["light", "dark", "auto"],
        default="light",
        help=(
            "Color theme for the emitted SVG/HTML: 'light' (default, the "
            "faithful LabVIEW light diagram), 'dark' (a dark palette baked in "
            "unconditionally), or 'auto' (light by default, dark when the "
            "viewer's OS/editor prefers dark, via prefers-color-scheme)."
        ),
    )


def _add_project_root_arg(parser: argparse.ArgumentParser) -> None:
    """Add --project-root flag to a subparser."""
    parser.add_argument(
        "--project-root",
        default=None,
        metavar="DIR",
        help=(
            "Project root containing a .lvkit/ resolution store. "
            "Defaults to walking up from CWD looking for .lvkit/."
        ),
    )


def _add_library_root_args(parser: argparse.ArgumentParser) -> None:
    """Add --vilib, --userlib, and --no-auto-vilib flags to a subparser."""
    parser.add_argument(
        "--vilib",
        default=None,
        metavar="DIR",
        help=(
            "Path to LabVIEW's vi.lib directory on disk. When set, "
            "<vilib> dependency refs resolve to real .vi files, "
            "terminal layouts are captured automatically, and results "
            "are cached to .lvkit/vilib/ for future runs. When omitted, "
            "lvkit tries to auto-detect a local LabVIEW install and use its "
            "vi.lib/user.lib (see `lvkit detect`); pass --no-auto-vilib to "
            "disable that."
        ),
    )
    parser.add_argument(
        "--userlib",
        default=None,
        metavar="DIR",
        help=(
            "Path to LabVIEW's user.lib directory on disk. When set, "
            "<userlib> dependency refs resolve to real .vi files."
        ),
    )
    parser.add_argument(
        "--no-auto-vilib",
        action="store_true",
        help=(
            "Disable auto-detection of a locally installed LabVIEW's "
            "vi.lib/user.lib when --vilib is not given. Use this for "
            "reproducible/machine-independent runs (e.g. CI)."
        ),
    )


def _parse_library_roots(
    args: argparse.Namespace,
) -> tuple[Path | None, Path | None]:
    """Extract --vilib / --userlib from parsed args as Path objects.

    When --vilib is not given (and --no-auto-vilib was not passed), attempt a
    best-effort auto-detection of a locally installed LabVIEW and use its
    vi.lib / user.lib. Explicit flags always win; detection is non-fatal.
    """
    vilib_root = Path(args.vilib) if args.vilib else None
    userlib_root = Path(args.userlib) if args.userlib else None

    if vilib_root is None and not getattr(args, "no_auto_vilib", False):
        from .lv_detect import detect_labview

        detected = detect_labview()
        if detected is not None:
            vilib_root = detected.vilib_root
            if userlib_root is None:
                userlib_root = detected.userlib_root
            version = detected.version or "?"
            print(
                f"lvkit: auto-detected LabVIEW {version} vi.lib at "
                f"{detected.vilib_root} ({detected.source})",
                file=sys.stderr,
            )

    return vilib_root, userlib_root


def _configure_library_roots(graph: InMemoryVIGraph, args: argparse.Namespace) -> None:
    """Apply --vilib / --userlib from parsed args to the graph."""
    vilib_root, userlib_root = _parse_library_roots(args)
    if vilib_root or userlib_root:
        graph.set_library_roots(vilib_root=vilib_root, userlib_root=userlib_root)


def _configure_resolvers(args: argparse.Namespace) -> Path | None:
    """Discover the project store and reset resolver singletons.

    Must be called BEFORE any load_vi() so graph construction sees the
    project mappings (used for terminal-index disambiguation).

    Accepts --project-root in either form: the parent of .lvkit/ (the
    project root), or the .lvkit/ directory itself.

    Returns the project store directory if one was found, else None.
    """
    project_root = getattr(args, "project_root", None)
    store: Path | None
    if project_root:
        candidate = Path(project_root)
        # Accept both "project root" and ".lvkit/" itself
        if candidate.name == ".lvkit" and candidate.is_dir():
            store = candidate
        elif (candidate / ".lvkit").is_dir():
            store = candidate / ".lvkit"
        else:
            store = None
    else:
        store = find_project_store()

    from . import primitive_resolver, vilib_resolver

    primitive_resolver.reset_resolver(project_data_dir=store)
    vilib_resolver.reset_resolver(project_data_dir=store)
    return store


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="lvkit",
        description="Understand, convert, and document LabVIEW VI files.",
        epilog=(
            "lvkit is an independent, clean-room project, not affiliated with, "
            "authorized by, endorsed by, or sponsored by NI. LabVIEW, NI, and "
            "National Instruments are trademarks of National Instruments "
            "Corporation, used only to identify the file format lvkit reads."
        ),
    )
    parser.add_argument("--version", action="version", version=f"lvkit {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Structure command
    struct_parser = subparsers.add_parser(
        "structure", help="Analyze LabVIEW project structure"
    )
    struct_parser.add_argument(
        "input", help="Directory, .lvproj, .lvlib, or .lvclass file"
    )
    struct_parser.add_argument("--json", action="store_true", help="Output as JSON")
    struct_parser.add_argument(
        "--plan", action="store_true", help="Generate Python structure plan"
    )

    # MCP server command
    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Run MCP server for VI analysis",
    )
    mcp_parser.add_argument(
        "--selftest",
        action="store_true",
        help=(
            "Initialize the server and list its tools, then exit (non-zero on "
            "failure). A broken install (e.g. an incompatible mcp version) "
            "otherwise fails silently — the client just registers zero tools. "
            "Use in CI to catch it."
        ),
    )
    mcp_parser.add_argument(
        "dirs",
        nargs="*",
        metavar="DIR",
        help=(
            "Default search root(s) for clients that send no workspace root "
            "(notably Claude Desktop). One or more source-root folders to search "
            "for VIs. Omit when the client provides a workspace root, or to fall "
            "back to the cwd."
        ),
    )

    # Index command - build/refresh the code-understanding facts index
    index_parser = subparsers.add_parser(
        "index",
        help="Build/refresh the code-understanding index for a VI repo",
    )
    index_parser.add_argument(
        "input_path",
        help=(
            "Directory, .lvproj, .lvlib, .lvclass, or .vi file. A single-file "
            "target indexes its ENCLOSING project (nearest .lvkit/ or .git "
            "root), not just that file — the index always covers the whole "
            "repo."
        ),
    )
    index_parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "Incrementally refresh an existing index: rebuild only VIs whose "
            "content hash changed (or that were added), drop deleted ones, and "
            "leave the rest untouched. Falls back to a full build if the repo "
            "has never been indexed."
        ),
    )

    # Query command - read-only SQL over the facts index
    query_parser = subparsers.add_parser(
        "query",
        help="Run read-only SQL over a repo's code-understanding index",
    )
    query_parser.add_argument(
        "input_path",
        help=(
            "Directory, .lvproj, .lvlib, .lvclass, or .vi file — its enclosing "
            "project's index is queried. Build the index first with "
            "`lvkit index`."
        ),
    )
    query_parser.add_argument(
        "sql",
        nargs="?",
        help=(
            "A single read-only SELECT/WITH over the curated views "
            "(vi, terminal, constant, node, type_use, class_fact, lvproj). "
            'Omit when using --schema. Example: "SELECT name, COUNT(*) AS n '
            "FROM terminal WHERE type_descriptor='Error' AND direction='output' "
            'GROUP BY name ORDER BY n DESC".'
        ),
    )
    query_parser.add_argument(
        "--schema",
        action="store_true",
        help="List the queryable views and their columns, then exit.",
    )
    query_parser.add_argument(
        "--no-refresh",
        action="store_true",
        help=(
            "Query the stored index as-is without first refreshing it. Faster, "
            "but results may be stale if a VI changed since the last build."
        ),
    )
    query_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )

    # Graph-op commands - call graph & change impact over the index. The call
    # graph is the node spine's kind='vi' slice (each SubVI-call node's resolved
    # callee_path); these commands compute the transitive closure over it. There
    # is no MCP twin — an MCP client asks the same questions with `query` over
    # node.callee_path (direct hops) + a WITH RECURSIVE (transitive), or reads
    # the precomputed vi.callers_count / vi.impact_score columns.
    _add_graphop_parser(
        subparsers,
        "callers",
        "VIs that call the given VI (who depends on it directly)",
    )
    _add_graphop_parser(
        subparsers,
        "callees",
        "VIs the given VI calls (its direct dependencies)",
    )
    _add_graphop_parser(
        subparsers,
        "blast-radius",
        "Transitive dependents of the given VI — what breaks if you change it",
        depth=True,
    )

    # Describe command - human-readable VI description
    desc_parser = subparsers.add_parser(
        "describe",
        help="Describe a VI's purpose, signature, and structure",
    )
    desc_parser.add_argument(
        "input_path",
        help="Path to .vi file",
    )
    desc_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The VI's project "
            "root (nearest enclosing .lvkit/) is auto-detected and "
            "searched, so this is only needed for VIs outside a "
            "project store."
        ),
    )
    desc_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Include a full netlist section (see lvkit.graph.netlist)",
    )
    desc_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help=(
            "'text' (default) prints the human-readable description; 'json' "
            "emits the canonical netlist IR — the same structured payload the "
            "MCP read_vi tool returns — for a program to parse."
        ),
    )
    _add_project_root_arg(desc_parser)
    _add_load_mode_arg(desc_parser)
    _add_library_root_args(desc_parser)

    # Generate command - deterministic AST-based Python generation
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate Python from VI files using deterministic AST pipeline",
    )
    gen_parser.add_argument(
        "input_path",
        help="Path to .vi, .lvlib, .lvclass, or directory",
    )
    gen_parser.add_argument(
        "-o",
        "--output",
        default="outputs",
        help="Output directory",
    )
    gen_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The VI's project "
            "root (nearest enclosing .lvkit/) is auto-detected and "
            "searched, so this is only needed for VIs outside a "
            "project store."
        ),
    )
    # User-facing name is --placeholder-on-unresolved (descriptive of the
    # output the user sees in their generated Python). Internally this
    # flows to CodeGenContext.soft_unresolved (the codegen-time mode).
    gen_parser.add_argument(
        "--placeholder-on-unresolved",
        action="store_true",
        help=(
            "Don't fail on unknown primitives or vi.lib VIs. Instead emit "
            "an inline `raise PrimitiveResolutionNeeded(...)` / `raise "
            "VILibResolutionNeeded(...)` in the generated Python so the "
            "build succeeds and unresolved calls are visible at runtime."
        ),
    )
    _add_project_root_arg(gen_parser)
    _add_load_mode_arg(gen_parser)
    _add_library_root_args(gen_parser)

    # Docs command - generate HTML documentation
    docs_parser = subparsers.add_parser(
        "docs",
        help="Generate HTML documentation for VI files",
    )
    docs_parser.add_argument(
        "input_path",
        help="Path to .vi, .lvlib, .lvclass, or directory",
    )
    docs_parser.add_argument(
        "output_dir",
        help="Output directory for HTML files",
    )
    docs_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The VI's project "
            "root (nearest enclosing .lvkit/) is auto-detected and "
            "searched, so this is only needed for VIs outside a "
            "project store."
        ),
    )
    _add_project_root_arg(docs_parser)
    _add_load_mode_arg(docs_parser)
    _add_library_root_args(docs_parser)

    # Visualize command - interactive graph visualization
    viz_parser = subparsers.add_parser(
        "visualize",
        help="Generate an interactive VI graph (dataflow or dependency network)",
    )
    viz_parser.add_argument(
        "input_path",
        help="Path to .vi, .lvlib, .lvclass, or directory",
    )
    viz_parser.add_argument(
        "-o",
        "--output",
        default="outputs/graph.html",
        help="Output HTML file (default: outputs/graph.html)",
    )
    viz_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The VI's project "
            "root (nearest enclosing .lvkit/) is auto-detected and "
            "searched, so this is only needed for VIs outside a "
            "project store."
        ),
    )
    viz_parser.add_argument(
        "--open",
        action="store_true",
        help="Open in browser after generating",
    )
    viz_parser.add_argument(
        "--mode",
        default="dataflow",
        choices=["dataflow", "deps"],
        help="Graph type: dataflow (operations within VI) or deps (VI dependencies)",
    )
    _add_project_root_arg(viz_parser)
    _add_load_mode_arg(viz_parser)
    _add_library_root_args(viz_parser)

    # Diff command - compare two VIs
    diff_parser = subparsers.add_parser(
        "diff",
        help="Compare two versions of a VI",
    )
    diff_parser.add_argument(
        "vi_a",
        help="Path to first .vi file",
    )
    diff_parser.add_argument(
        "vi_b",
        help="Path to second .vi file",
    )
    diff_parser.add_argument(
        "--format",
        choices=["text", "json", "html"],
        default=None,
        help=(
            "Output format: 'text' (concise logical change summary, "
            "stdout/pipe/CI-friendly, default), 'json' (ChangeMap for "
            "scripts/agents/the VSCode extension), or 'html' (self-contained "
            "interactive viewer file)."
        ),
    )
    diff_parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help=(
            "Show the change summary in full depth (VI-interface Signature, "
            "containment expanded into a tree, old->new detail, an "
            "unchanged-node tally) instead of the concise default. Only "
            "affects --format text (a detail level, orthogonal to format)."
        ),
    )
    diff_parser.add_argument(
        "--long",
        action="store_true",
        help="Back-compat alias for --verbose.",
    )
    diff_parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="FILE",
        help=(
            "Output file path (used by --format html; default "
            "outputs/vi-diff/<stemA>__<stemB>.html). "
            "text/json print to stdout unless given."
        ),
    )
    diff_parser.add_argument(
        "--open",
        action="store_true",
        help="Render --format html and open it in a browser.",
    )
    diff_parser.add_argument(
        "--before-ref",
        default=None,
        metavar="REV",
        help=(
            "Git revision (or any short tag) for the BEFORE side, appended to "
            "the VI's resolved qualified name — e.g. --before-ref a1b2c3d gives "
            "'TestCase.lvclass:run.vi @ a1b2c3d'. lvkit resolves the name; the "
            "caller (the VS Code custom diff) supplies only the rev, which it "
            "can't infer from a git temp checkout."
        ),
    )
    diff_parser.add_argument(
        "--after-ref",
        default=None,
        metavar="REV",
        help="Git revision for the AFTER side (e.g. 'working tree'), appended "
        "to its qualified name.",
    )
    diff_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The project root of each "
            "VI (nearest enclosing .lvkit/) is auto-detected and searched, so "
            "this is only needed for VIs outside a project store."
        ),
    )
    diff_parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the output cache: rebuild the diff and refresh the slot.",
    )
    _add_theme_arg(diff_parser)
    _add_project_root_arg(diff_parser)
    _add_load_mode_arg(diff_parser)
    _add_library_root_args(diff_parser)

    # Setup command - install AI editor skills and create .lvkit/ store
    setup_parser = subparsers.add_parser(
        "setup",
        help="Install AI editor skills and create project-local .lvkit/ store",
    )
    setup_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory in which to create .lvkit/ (default: current directory)",
    )
    setup_parser.add_argument(
        "skills",
        nargs="?",
        choices=["claude", "copilot", "codex", "all"],
        default=None,
        help=(
            "AI agent to install skills for: claude, copilot, codex, or all. "
            "Omit to auto-detect from project layout (CLAUDE.md / .claude/ "
            "for Claude Code; .github/copilot-instructions.md / "
            ".github/instructions/ / .github/agents.md for Copilot; "
            "AGENTS.md / AGENTS.override.md / .agents/ / .codex/ for Codex)."
        ),
    )
    setup_parser.add_argument(
        "--no-skills",
        action="store_true",
        help=(
            "Create the .lvkit/ resolution store and README without installing "
            "any AI editor skills. Use this if you want to add primitive or "
            "vi.lib mappings manually."
        ),
    )
    setup_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing skill files even if they have local edits",
    )

    # Unresolved command - batch-collect every resolution gap
    unresolved_parser = subparsers.add_parser(
        "unresolved",
        help=(
            "List every unknown primitive / unmapped vi.lib VI in a VI, "
            "library, or project — in one pass, instead of one at a time"
        ),
    )
    unresolved_parser.add_argument(
        "input_path",
        help="Path to .vi, .lvlib, .lvclass, .llb, or directory",
    )
    unresolved_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Extra SubVI search path (repeatable). See `generate --help`.",
    )
    unresolved_parser.add_argument(
        "--json", action="store_true", help="Output the gaps as JSON"
    )
    _add_project_root_arg(unresolved_parser)
    _add_load_mode_arg(unresolved_parser)
    _add_library_root_args(unresolved_parser)

    # Detect command
    detect_parser = subparsers.add_parser(
        "detect",
        help="Detect a locally installed LabVIEW and its vi.lib/user.lib",
    )
    detect_parser.add_argument(
        "--json", action="store_true", help="Output detection result as JSON"
    )

    # Render command - faithful block-diagram SVG
    render_parser = subparsers.add_parser(
        "render",
        help="Render a VI's block diagram to a faithful SVG",
    )
    render_parser.add_argument(
        "input_path",
        help=(
            "Path to a .vi file (or _BDHb.xml heap), OR a directory — a "
            "directory renders every .vi under it into the cache (a fast "
            "'warm' pass; already-fresh VIs are skipped)."
        ),
    )
    render_parser.add_argument(
        "-o",
        "--output",
        default=None,
        metavar="PATH",
        help=(
            "Write the output to PATH. Without -o, the render still goes to the "
            "per-user cache (reported by path) — every render warms the cache; "
            "-o is the switch that also writes a file. For a directory input, "
            "-o is an output DIR that mirrors the tree."
        ),
    )
    render_parser.add_argument(
        "--no-cache",
        action="store_true",
        help=(
            "Bypass the output cache: rebuild from scratch and refresh the slot "
            "(use after editing lvkit's renderer without a version bump)."
        ),
    )
    render_parser.add_argument(
        "--format",
        choices=["svg", "html"],
        default="svg",
        help=(
            "Output format: 'svg' (default, the self-contained diagram) or "
            "'html' (an interactive single-VI viewer page with zoom/pan and a "
            "light/dark diagram-theme toggle)."
        ),
    )
    render_parser.add_argument(
        "--ref",
        default=None,
        metavar="REV",
        help=(
            "Git revision (or short tag) appended to the VI's qualified name in "
            "the --format html title — e.g. --ref a1b2c3d gives "
            "'TestCase.lvclass:run.vi @ a1b2c3d'. Lets a caller (VS Code's native "
            "side-by-side diff of two rendered VIs) keep the commit visible; "
            "lvkit resolves the name, the caller supplies only the rev."
        ),
    )
    render_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help=(
            "Extra SubVI search path (repeatable). The VI's project "
            "root (nearest enclosing .lvkit/) is auto-detected and "
            "searched, so this is only needed for VIs outside a "
            "project store."
        ),
    )
    _add_theme_arg(render_parser)
    _add_project_root_arg(render_parser)
    _add_load_mode_arg(render_parser)
    _add_library_root_args(render_parser)

    args = parser.parse_args()

    if args.command == "structure":
        return cmd_structure(args)
    elif args.command == "mcp":
        return cmd_mcp(args)
    elif args.command == "index":
        return cmd_index(args)
    elif args.command == "query":
        return cmd_query(args)
    elif args.command in ("callers", "callees", "blast-radius"):
        return cmd_graph_op(args)
    elif args.command == "describe":
        return cmd_describe(args)
    elif args.command == "generate":
        return cmd_generate(args)
    elif args.command == "docs":
        return cmd_docs(args)
    elif args.command == "visualize":
        return cmd_visualize(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "setup":
        return cmd_setup(args)
    elif args.command == "unresolved":
        return cmd_unresolved(args)
    elif args.command == "detect":
        return cmd_detect(args)
    elif args.command == "render":
        return cmd_render(args)
    else:
        parser.print_help()
        return 0


def cmd_structure(args: argparse.Namespace) -> int:
    """Handle the structure command."""
    from .structure import (
        discover_project_structure,
        discover_structure_from_lvproj,
        generate_python_structure_plan,
        parse_lvclass,
        parse_lvlib,
    )

    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    try:
        if input_path.suffix == ".lvclass":
            # Single class
            cls = parse_lvclass(input_path)
            if args.json:
                data = {
                    "name": cls.name,
                    "path": str(cls.path),
                    "parent_class": cls.parent_class,
                    "private_data": cls.private_data_ctl,
                    "methods": [
                        {
                            "name": m.name,
                            "scope": m.scope,
                            "is_static": m.is_static,
                            "vi_path": m.vi_path,
                        }
                        for m in cls.methods
                    ],
                }
                print(json.dumps(data, indent=2))
            else:
                print(f"Class: {cls.name}")
                if cls.parent_class:
                    print(f"  Inherits: {cls.parent_class}")
                if cls.private_data_ctl:
                    print(f"  Private Data: {cls.private_data_ctl}")
                if cls.methods:
                    print("  Methods:")
                    for m in cls.methods:
                        static = " [static]" if m.is_static else ""
                        print(f"    - {m.name} ({m.scope}){static}")

        elif input_path.suffix == ".lvlib":
            # Single library
            lib = parse_lvlib(input_path)
            if args.json:
                data = {
                    "name": lib.name,
                    "path": str(lib.path),
                    "version": lib.version,
                    "members": [
                        {"name": m.name, "type": m.member_type, "url": m.url}
                        for m in lib.members
                    ],
                }
                print(json.dumps(data, indent=2))
            else:
                print(f"Library: {lib.name}")
                if lib.version:
                    print(f"  Version: {lib.version}")
                if lib.members:
                    print(f"  Members ({len(lib.members)}):")
                    for m in lib.members:
                        print(f"    - {m.name} [{m.member_type}]")

        elif input_path.suffix == ".lvproj" or input_path.is_dir():
            # A .lvproj uses the project's explicit member list; a directory is
            # scanned. Both produce the same structure dict, rendered the same.
            if input_path.suffix == ".lvproj":
                structure = discover_structure_from_lvproj(input_path)
            else:
                structure = discover_project_structure(input_path)

            if args.plan:
                plan = generate_python_structure_plan(structure)
                print(plan)
            elif args.json:
                print(json.dumps(structure, indent=2))
            else:
                print(f"Project Structure: {input_path}")
                print(f"  Libraries: {len(structure['libraries'])}")
                print(f"  Classes: {len(structure['classes'])}")
                print(f"  Standalone VIs: {len(structure['standalone_vis'])}")
                print()
                if structure["classes"]:
                    print("Classes:")
                    for cls in structure["classes"]:
                        methods = len(cls["methods"])
                        print(f"  - {cls['name']} ({methods} methods)")

        else:
            print(f"Error: Unsupported file type: {input_path}", file=sys.stderr)
            return 1

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_mcp(args: argparse.Namespace) -> int:
    """Handle the mcp command - run MCP server (or --selftest)."""
    if getattr(args, "selftest", False):
        from .mcp.server import selftest

        try:
            n = selftest()
        except Exception as e:  # noqa: BLE001 — health check reports any failure
            print(f"MCP selftest FAILED: {e}", file=sys.stderr)
            return 1
        print(f"MCP selftest OK: server initialized, {n} tools listed.")
        return 0

    from .mcp.server import main as mcp_main

    try:
        print("Starting MCP server...", file=sys.stderr)
        mcp_main(getattr(args, "dirs", None) or None)
        return 0
    except KeyboardInterrupt:
        print("\nShutting down MCP server...", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_index(args: argparse.Namespace) -> int:
    """Handle the index command - build/refresh the facts index for a repo."""
    import time

    from .index.build import build_index, build_lvproj_membership, refresh_index
    from .index.project import resolve_project
    from .index.store import delete as delete_index
    from .index.store import load as load_index
    from .index.store import save, save_lvproj_members

    start = time.monotonic()
    project_root, vi_paths = resolve_project(Path(args.input_path))

    stored = load_index(project_root) if getattr(args, "refresh", False) else []
    if stored:
        rr, merged = refresh_index(project_root, vi_paths, stored)
        delete_index(project_root, rr.deleted)
        save(project_root, merged)
        save_lvproj_members(project_root, build_lvproj_membership(project_root))
        print(
            json.dumps(
                {
                    "rebuilt": len(rr.rebuilt),
                    "deleted": len(rr.deleted),
                    "total": rr.total,
                    "ms": round((time.monotonic() - start) * 1000),
                }
            )
        )
        return 0

    result = build_index(project_root, vi_paths)
    save(project_root, result.facts)
    save_lvproj_members(project_root, result.lvproj_members)
    print(
        json.dumps(
            {
                "vis": len(result.facts),
                "collisions": result.collisions,
                "ms": round((time.monotonic() - start) * 1000),
            }
        )
    )
    return 0


def _print_table(columns: list[str], rows: list[list[object]]) -> None:
    """Print a compact aligned text table (empty state = a header + no rows)."""
    cells = [[("" if v is None else str(v)) for v in row] for row in rows]
    widths = [len(c) for c in columns]
    for row in cells:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(c))
    print("  ".join(c.ljust(widths[i]) for i, c in enumerate(columns)))
    print("  ".join("-" * w for w in widths))
    for row in cells:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))


def _add_graphop_parser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
    *,
    depth: bool = False,
) -> None:
    """Add one call-graph/impact subcommand (callers/callees/blast-radius) over
    the node-spine call graph. They share ``vi``/``project`` positionals and the
    freshness + format flags; blast-radius also takes ``--depth``."""
    p = subparsers.add_parser(name, help=help_text)
    p.add_argument(
        "vi",
        help="The VI: a path, a qualified name, or an unambiguous bare name.",
    )
    p.add_argument(
        "project",
        help="Any path inside the repo; its enclosing project's index is used.",
    )
    if depth:
        p.add_argument(
            "--depth",
            type=int,
            default=None,
            help="Bound the search to N hops (default: unbounded).",
        )
    p.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table).",
    )
    p.add_argument(
        "--no-refresh",
        action="store_true",
        help="Use the stored index as-is without refreshing first (may be stale).",
    )


def cmd_graph_op(args: argparse.Namespace) -> int:
    """Handle callers/callees/blast-radius — typed call-graph ops over the
    node-spine call graph (no MCP twin; an MCP client uses `query` over
    node.callee_path + a recursive CTE, or the vi.callers_count/impact_score
    columns)."""
    from dataclasses import asdict

    from .index.build import ensure_fresh_index
    from .index.project import resolve_project
    from .index.query import blast_radius, get_callees, get_callers
    from .index.store import load as store_load

    project_root, vi_paths = resolve_project(Path(args.project))
    if not args.no_refresh:
        ensure_fresh_index(project_root, vi_paths)
    facts = store_load(project_root)
    if not facts:
        print(
            f"{args.command}: no index for {project_root} — build it first "
            "(e.g. `lvkit index`)",
            file=sys.stderr,
        )
        return 2

    if args.command == "blast-radius":
        br = blast_radius(facts, args.vi, depth=args.depth)
        if args.format == "json":
            print(json.dumps(asdict(br)))
        else:
            print(f"{br.impact_score} transitive dependent(s) of {br.vi_key}:")
            for path in br.dependents:
                print(f"  {path}")
        return 0

    op = get_callers if args.command == "callers" else get_callees
    paths = op(facts, args.vi)
    if args.format == "json":
        print(json.dumps(paths))
    else:
        for path in paths:
            print(path)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    """Handle the query command — read-only SQL over a repo's facts index."""
    from dataclasses import asdict

    from .index import sql as isql
    from .index.project import resolve_project

    project_root, vi_paths = resolve_project(Path(args.input_path))

    if args.schema:
        views = isql.describe_schema()
        if args.format == "json":
            print(json.dumps([asdict(v) for v in views], indent=2))
        else:
            for v in views:
                print(v.name)
                for col in v.columns:
                    print(f"  {col.name}: {col.description}")
        return 0

    if not args.sql:
        print("query: provide a SQL statement, or use --schema", file=sys.stderr)
        return 2

    # Freshness: build the index if cold, else incrementally refresh it, so a
    # query reflects the current files (same policy the MCP server applies).
    # `--no-refresh` skips this to query the stored index as-is (fast, but may
    # be stale if a VI changed since the last build).
    if not args.no_refresh:
        from .index.build import ensure_fresh_index

        ensure_fresh_index(project_root, vi_paths)

    try:
        res = isql.run_query(project_root, args.sql)
    except isql.QueryError as e:
        print(f"query error: {e}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(
            json.dumps(
                {
                    "columns": res.columns,
                    "rows": res.rows,
                    "row_count": res.row_count,
                    "truncated": res.truncated,
                }
            )
        )
    else:
        _print_table(res.columns, res.rows)
        if res.truncated:
            print(f"... (truncated at {res.row_count} rows)", file=sys.stderr)
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    """Handle the describe command - human-readable VI description."""
    from .graph.describe import describe_vi

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        from .graph import InMemoryVIGraph
        from .index.build import warm_all_loaded

        graph = InMemoryVIGraph()
        _configure_library_roots(graph, args)
        search_paths = _auto_search_paths(args.search_paths, input_path)
        graph.load_vi(
            str(input_path),
            _resolve_load_mode(args, LoadMode.MINIMAL),
            search_paths=search_paths,
        )

        # Disambiguate by parent dir when multiple loaded VIs share the
        # input's leaf name (e.g. TestCase.lvclass:run.vi vs TestSuite's)
        vis = graph.list_vis()
        vi_name = graph.resolve_vi_name(input_path.name)
        candidates = [v for v in vis if v.rsplit(":", 1)[-1] == input_path.name]
        if len(candidates) > 1:
            parent_dir = input_path.parent.name
            preferred = [
                c
                for c in candidates
                if c.startswith(f"{parent_dir}.lvclass:")
                or c.startswith(f"{parent_dir}.lvlib:")
            ]
            if preferred:
                vi_name = preferred[0]

        # Progressive index: every parse warms the store — describe parses this
        # VI (and its SubVIs under MINIMAL), so warm all of them.
        warm_all_loaded(graph)

        if getattr(args, "format", "text") == "json":
            # Same structured netlist IR the MCP read_vi tool returns — parity
            # so a non-MCP (CLI/CI/skill) consumer gets the structured read too.
            from .graph.netlist import build_netlist, netlist_to_dict

            print(
                json.dumps(
                    netlist_to_dict(build_netlist(graph, vi_name)), indent=2
                )
            )
        else:
            print(describe_vi(graph, vi_name, verbose=args.verbose))

        return 0
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def _detect_ai_editors(root: Path) -> list[str]:
    """Detect which AI editors are configured in the project."""
    editors = []
    if (root / ".claude").is_dir() or (root / "CLAUDE.md").is_file():
        editors.append("claude")
    if (
        (root / ".github" / "copilot-instructions.md").is_file()
        or (root / ".github" / "instructions").is_dir()
        or (root / ".github" / "agents.md").is_file()
    ):
        editors.append("copilot")
    if (
        (root / "AGENTS.md").is_file()
        or (root / "AGENTS.override.md").is_file()
        or (root / ".agents").is_dir()
        or (root / ".codex").is_dir()
    ):
        editors.append("codex")
    return editors


def cmd_setup(args: argparse.Namespace) -> int:
    """Handle the setup command — install AI skills and create .lvkit/ store."""
    # `directory` and `skills` are both optional positionals, so a lone
    # `lvkit setup copilot` binds "copilot" to `directory`. If the only
    # positional given is a skills choice, treat it as the skills target in
    # the current directory (the obvious intent).
    skill_choices = ("claude", "copilot", "codex", "all")
    if args.skills is None and args.directory in skill_choices:
        args.skills = args.directory
        args.directory = "."

    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"Error: Not a directory: {root}", file=sys.stderr)
        return 1

    store = init_project_store(root)
    print(f"Initialized .lvkit/ store at {store}")

    if args.no_skills:
        return 0

    # Resolve which editors to install for
    explicit = args.skills
    if explicit == "all":
        editors = ["claude", "copilot", "codex"]
    elif explicit in ("claude", "copilot", "codex"):
        editors = [explicit]
    else:
        # Auto-detect from project layout
        editors = _detect_ai_editors(root)
        if not editors:
            print(
                "No AI agent detected. If you add one later, run `lvkit setup` again. "
                "If you have one that wasn't detected, run `lvkit setup claude`, "
                "`lvkit setup copilot`, or `lvkit setup codex` to install skills "
                "explicitly."
            )
            return 0

    force = getattr(args, "force", False)
    if "claude" in editors:
        try:
            written = install_claude_skills(root, force=force)
        except FileExistsError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if written:
            print(f"Installed {len(written)} Claude Code skill(s):")
            for p in written:
                print(f"  {p}")
        else:
            print("Claude Code skills already up to date.")
    if "copilot" in editors:
        try:
            copilot_written = install_copilot_skills(root, force=force)
        except FileExistsError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if copilot_written:
            print(f"Installed {len(copilot_written)} Copilot file(s):")
            for p in copilot_written:
                print(f"  {p}")
        else:
            print("Copilot files already up to date.")
    if "codex" in editors:
        try:
            codex_written = install_codex_skills(root, force=force)
        except FileExistsError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        if codex_written:
            print(f"Installed {len(codex_written)} Codex skill(s):")
            for p in codex_written:
                print(f"  {p}")
        else:
            print("Codex skills already up to date.")

    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Handle the detect command — report the local LabVIEW install, if any.

    Diagnostic for confirming auto-vilib detection (especially on machines
    with LabVIEW that differ from where the code was written). Always exits 0
    so it can be used in scripts to probe for an install.
    """
    from .lv_detect import detect_labview

    detected = detect_labview()

    if getattr(args, "json", False):
        if detected is None:
            print(json.dumps({"detected": False}, indent=2))
        else:
            print(
                json.dumps(
                    {
                        "detected": True,
                        "version": detected.version,
                        "install_dir": str(detected.install_dir),
                        "vilib_root": str(detected.vilib_root),
                        "userlib_root": (
                            str(detected.userlib_root)
                            if detected.userlib_root
                            else None
                        ),
                        "source": detected.source,
                    },
                    indent=2,
                )
            )
        return 0

    if detected is None:
        print(
            "No local LabVIEW detected. Pass --vilib <path-to-vi.lib> "
            "explicitly to resolve <vilib> dependency refs."
        )
        return 0

    print(f"Detected LabVIEW {detected.version or '(unknown version)'}")
    print(f"  install dir : {detected.install_dir}")
    print(f"  vi.lib      : {detected.vilib_root}")
    print(f"  user.lib    : {detected.userlib_root or '(not found)'}")
    print(f"  source      : {detected.source}")
    return 0


def _render_options_tag(
    args: argparse.Namespace, theme_mode: str, ref: str | None
) -> str:
    """The output-cache options key: everything besides the VI content and the
    lvkit version that changes the rendered bytes (format, theme, title ref)."""
    return f"{args.format}|{theme_mode}|ref={ref or ''}"


def _theme_mode(args: argparse.Namespace) -> ThemeMode:
    """The render theme_mode: html always 'auto' (its viewer toggles live), svg
    honours --theme."""
    return cast("ThemeMode", "auto" if args.format == "html" else args.theme)


def _build_render_body(
    args: argparse.Namespace,
    input_path: Path,
    theme_mode: ThemeMode,
    ref: str | None,
) -> str | int:
    """Build the render output (svg string or html viewer). Returns the body, or
    an int exit code on failure. Imports the render/graph stack HERE — a cache
    hit never reaches this, so it never pays the ~250 ms import."""
    from .render import render_vi_file_titled
    from .render.render_viewer import build_render_viewer

    _configure_resolvers(args)
    vilib_root, userlib_root = _parse_library_roots(args)
    search_paths = _auto_search_paths(args.search_paths, input_path) or None
    try:
        # _titled also returns the VI's resolved (qualified) name for the title.
        svg, vi_title = render_vi_file_titled(
            input_path,
            search_paths=search_paths,
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            mode=_resolve_load_mode(args, LoadMode.MINIMAL),
            theme_mode=theme_mode,
        )
    except Exception as e:
        print(f"Error: render failed: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1
    if svg is None:
        print(
            "Error: render declined — required diagram geometry is missing "
            "(see logs for the missing ids)",
            file=sys.stderr,
        )
        return 1
    if args.format != "html":
        return svg
    stem = input_path.stem.replace("_BDHb", "")
    title = vi_title or stem
    if ref:  # qualified name + git rev, VS Code diff convention: "name (rev)"
        title = f"{title} ({ref})"
    return build_render_viewer(svg, title=title)


def _emit_render(args: argparse.Namespace, input_path: Path, body: str) -> int:
    """Deliver a render body: to ``-o`` if given; otherwise the render lives in
    the cache slot (always written by the caller) and we report its path — every
    render warms the cache, ``-o`` is the switch that also writes a file."""
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"Rendered {out}")
        return 0
    from .output_cache import render_slot

    stem = input_path.stem.replace("_BDHb", "")
    slot = render_slot(input_path, args.format)
    print(f"Rendered {stem} → cached ({slot}). Pass -o FILE to write a file.")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Handle the render command — faithful, graph-driven block-diagram SVG, or
    (``--format html``) a self-contained single-VI viewer page. A cached output
    for unchanged inputs is reused verbatim, skipping the build."""
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    if input_path.is_dir():
        return _cmd_render_dir(args, input_path)

    theme_mode = _theme_mode(args)
    options = _render_options_tag(args, theme_mode, args.ref)

    # Fast path: a fresh cached render is returned WITHOUT importing the
    # render/graph/pylabview stack.
    if not args.no_cache:
        from .output_cache import lookup_render

        cached = lookup_render(input_path, args.format, options, __version__)
        if cached is not None:
            return _emit_render(args, input_path, cached)

    body = _build_render_body(args, input_path, theme_mode, args.ref)
    if isinstance(body, int):
        return body
    # A fresh build ALWAYS refreshes the slot — including under --no-cache, whose
    # job is to ignore a (possibly stale) hit and rebuild, not to leave the stale
    # entry behind for the next run.
    from .output_cache import store_render

    store_render(input_path, args.format, options, __version__, body)
    return _emit_render(args, input_path, body)


def _cmd_render_dir(args: argparse.Namespace, root: Path) -> int:
    """Render every ``.vi`` under ``root`` into the cache (a 'warm' pass in one
    process — the ~250 ms import is paid once, not once per VI). Already-fresh
    slots are skipped. With -o, also export a mirrored HTML/SVG tree there."""
    from .output_cache import lookup_render, store_render

    vis = sorted(p for p in root.rglob("*.vi") if p.is_file())
    if not vis:
        print(f"No .vi files under {root}")
        return 0
    theme_mode = _theme_mode(args)
    options = _render_options_tag(args, theme_mode, None)  # no per-VI ref in batch
    ext = "html" if args.format == "html" else "svg"
    outdir = Path(args.output) if args.output else None

    rendered = fresh = failed = 0
    for vi in vis:
        body: str | None = None
        if not args.no_cache:
            body = lookup_render(vi, args.format, options, __version__)
        if body is not None:
            fresh += 1
        else:
            built = _build_render_body(args, vi, theme_mode, None)
            if isinstance(built, int):
                failed += 1
                continue
            body = built
            store_render(vi, args.format, options, __version__, body)
            rendered += 1
        if outdir is not None:
            dest = outdir / vi.relative_to(root).with_suffix(f".{ext}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")

    where = f" → {outdir}" if outdir is not None else " → cached"
    tail = f", {failed} failed" if failed else ""
    print(f"{len(vis)} VIs — {rendered} rendered, {fresh} already fresh{tail}{where}")
    return 1 if failed and rendered == 0 and fresh == 0 else 0


def _auto_search_paths(explicit: list[str], *inputs: Path) -> list[Path]:
    """Effective SubVI search paths: the explicit ``--search-path`` values,
    plus the auto-detected project root of each input.

    A VI's SubVIs commonly live in sibling subdirectories of the project root,
    not next to the VI itself (the VI's own directory is always searched via
    ``load_vi``'s ``source_dir``, so it never needs listing here). #36: rather
    than make the user pass ``--search-path``, walk up from each input to its
    enclosing ``.lvkit`` project store (``find_project_store`` — the same
    marker ``_configure_resolvers`` already uses for primitive/vi.lib data) and
    add that root. Shared by every command that resolves SubVIs
    (describe/generate/docs/visualize/render/diff), so ``lvkit <cmd> foo.vi``
    finds the project's SubVIs with no flags. Explicit paths still win/augment;
    inputs sharing one project resolve to the same root (added once).
    """
    paths: list[Path] = [Path(p) for p in explicit]
    seen = {p.resolve() for p in paths}
    for inp in inputs:
        start = inp if inp.is_dir() else inp.parent
        store = find_project_store(start=start)
        if store is None:
            continue
        root = store.parent  # project root is the parent of the .lvkit/ store
        if root.resolve() not in seen:
            paths.append(root)
            seen.add(root.resolve())
    return paths


def _diff_options_tag(args: argparse.Namespace, fmt: str, verbose: bool) -> str:
    """The output-cache options key for a diff: everything besides the two VIs'
    content and the lvkit version that changes the output bytes."""
    return (
        f"{fmt}|verbose={int(bool(verbose))}"
        f"|before={args.before_ref or ''}|after={args.after_ref or ''}"
    )


def _build_diff_body(
    args: argparse.Namespace, path_a: Path, path_b: Path, fmt: str, verbose: bool
) -> str | int:
    """Build the diff body (text/json/html) via the shared ``diff_vi_files``
    core. Returns the body, or an int exit code. Imports the render/graph stack
    HERE (via ``vi_diff``) — a cache hit never reaches it."""
    from .vi_diff import diff_vi_files

    vilib_root, userlib_root = _parse_library_roots(args)
    body = diff_vi_files(
        path_a,
        path_b,
        fmt=fmt,
        verbose=verbose,
        search_paths=_auto_search_paths(args.search_paths, path_a, path_b),
        before_ref=args.before_ref,
        after_ref=args.after_ref,
        mode=_resolve_load_mode(args, LoadMode.MINIMAL),
        vilib_root=vilib_root,
        userlib_root=userlib_root,
    )
    if body is None:
        print(
            "Error: render declined — required diagram geometry is missing "
            "(see logs for the missing ids)",
            file=sys.stderr,
        )
        return 1
    return body


def _emit_diff(
    args: argparse.Namespace, path_a: Path, path_b: Path, fmt: str, body: str
) -> int:
    """Deliver a diff body per format: print (text), -o-or-stdout (json), or
    write+optionally-open (html)."""
    if fmt == "text":
        print(body if body else "No changes detected.")
        if sys.stdout.isatty():
            print(
                "\nTip: lvkit diff … --format html --open  for a "
                "visual, navigable diff."
            )
        return 0
    if fmt == "json":
        if args.output:
            Path(args.output).write_text(body, encoding="utf-8")
        else:
            print(body)
        return 0
    # html
    out = (
        Path(args.output)
        if args.output
        else Path("outputs/vi-diff") / f"{path_a.stem}__{path_b.stem}.html"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    print(f"Wrote {out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Handle the diff command — compare two VI versions.

    Output is picked with ``--format {text,json,html}`` (the lvkit house
    convention — one flag for mutually-exclusive output projections, never a
    boolean per format). ``-v/--verbose`` (and its back-compat alias
    ``--long``) is the orthogonal DETAIL axis: both tiers of ``text`` project
    the SAME UID-keyed ``diff_uid`` ChangeMap (see ``format_diff``) that also
    backs ``--format json``/``html`` — ``--verbose`` only adds depth, never a
    different set of changes. A cached output for the same two VI versions is
    reused verbatim, skipping the load.
    """
    path_a = Path(args.vi_a)
    path_b = Path(args.vi_b)
    for p in (path_a, path_b):
        if not p.exists():
            print(f"Error: Path not found: {p}", file=sys.stderr)
            return 1

    fmt = args.format or ("html" if args.open else "text")
    if args.open and args.format in ("text", "json"):
        print("Error: --open requires --format html", file=sys.stderr)
        return 1
    verbose = bool(args.verbose or args.long)
    options = _diff_options_tag(args, fmt, verbose)

    # Fast path: a cached diff for the same (before, after) bytes is returned
    # WITHOUT importing the graph/render stack. path_a is BEFORE, path_b AFTER.
    if not args.no_cache:
        from .output_cache import lookup_diff

        cached = lookup_diff(path_a, path_b, fmt, options, __version__)
        if cached is not None:
            return _emit_diff(args, path_a, path_b, fmt, cached)

    _configure_resolvers(args)
    try:
        body = _build_diff_body(args, path_a, path_b, fmt, verbose)
        if isinstance(body, int):
            return body
        # A fresh build always refreshes the slot (see cmd_render) — --no-cache
        # forces the rebuild but still updates the cache.
        from .output_cache import store_diff

        store_diff(path_a, path_b, fmt, options, __version__, body)
        return _emit_diff(args, path_a, path_b, fmt, body)
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_generate(args: argparse.Namespace) -> int:
    """Handle the generate command - AST-based Python generation."""
    from .pipeline import generate_python

    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        sp = _auto_search_paths(args.search_paths, input_path) or None
        vilib_root, userlib_root = _parse_library_roots(args)
        result = generate_python(
            input_path,
            args.output,
            search_paths=sp,
            mode=_resolve_load_mode(args, LoadMode.FULL),
            soft_unresolved=args.placeholder_on_unresolved,
            vilib_root=vilib_root,
            userlib_root=userlib_root,
        )
        return 1 if result["error"] > 0 else 0

    except (ValueError, FileNotFoundError, KeyError, NotImplementedError) as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


def cmd_unresolved(args: argparse.Namespace) -> int:
    """Handle the unresolved command — batch-list every resolution gap."""
    import json

    from .unresolved import collect_unresolved, format_unresolved_report

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        sp = _auto_search_paths(args.search_paths, input_path) or None
        vilib_root, userlib_root = _parse_library_roots(args)
        items = collect_unresolved(
            input_path,
            search_paths=sp,
            mode=_resolve_load_mode(args, LoadMode.FULL),
            vilib_root=vilib_root,
            userlib_root=userlib_root,
        )
    except (ValueError, FileNotFoundError, KeyError, NotImplementedError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "kind": it.kind,
                        "identifier": it.identifier,
                        "name": it.name,
                        "count": it.count,
                        "vi_names": it.vi_names,
                    }
                    for it in items
                ],
                indent=2,
            )
        )
    else:
        print(format_unresolved_report(items, input_path.name))
    return 0


def cmd_docs(args: argparse.Namespace) -> int:
    """Handle the docs command - generate HTML documentation."""
    from .docs.generate import generate_documents

    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        vilib_root, userlib_root = _parse_library_roots(args)
        result = generate_documents(
            library_path=str(input_path),
            output_dir=args.output_dir,
            search_paths=[
                str(p) for p in _auto_search_paths(args.search_paths, input_path)
            ]
            or None,
            mode=_resolve_load_mode(args, LoadMode.FULL),
            vilib_root=vilib_root,
            userlib_root=userlib_root,
        )
        print("\n" + result)
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


def cmd_visualize(args: argparse.Namespace) -> int:
    """Handle the visualize command — interactive graph in browser."""
    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    from .graph import InMemoryVIGraph

    graph = InMemoryVIGraph()
    _configure_library_roots(graph, args)
    search_paths = _auto_search_paths(args.search_paths, input_path) or None
    vmode = _resolve_load_mode(args, LoadMode.FULL)

    suffix = input_path.suffix.lower()
    if suffix == ".lvclass":
        graph.load_lvclass(str(input_path), vmode, search_paths)
    elif suffix == ".lvlib":
        graph.load_lvlib(str(input_path), vmode, search_paths)
    elif input_path.is_dir():
        graph.load_directory(str(input_path), vmode, search_paths)
    else:
        graph.load_vi(str(input_path), vmode, search_paths)

    # Every command that parses warms the index (best-effort).
    from .index.build import warm_all_loaded

    warm_all_loaded(graph)

    output = Path(args.output)

    try:
        import pyvis  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print(
            "Error: pyvis not installed. Run: pip install pyvis",
            file=sys.stderr,
        )
        return 1
    if args.mode == "deps":
        _visualize_deps(graph, output)
    else:
        _visualize_dataflow(graph, output)

    print(f"Graph saved to {args.output}")

    if args.open:
        webbrowser.open(Path(args.output).resolve().as_uri())

    return 0


_GRAPH_OPTIONS = """
{
  "physics": {
    "barnesHut": {
      "gravitationalConstant": -8000,
      "centralGravity": 0.1,
      "springLength": 200,
      "springConstant": 0.04,
      "damping": 0.3
    }
  },
  "edges": {
    "arrows": {
      "to": {"enabled": true, "scaleFactor": 1.0, "type": "arrow"}
    },
    "color": {"color": "#555", "highlight": "#000"},
    "width": 2,
    "smooth": {"type": "curvedCW", "roundness": 0.15}
  },
  "nodes": {
    "font": {"size": 14, "face": "arial", "bold": {"face": "arial"}},
    "borderWidth": 2,
    "shadow": true
  },
  "interaction": {
    "hover": true,
    "tooltipDelay": 100
  }
}
"""

_PROPERTIES_PANEL = """
<div id="props" style="position:fixed;top:10px;left:10px;width:320px;
     background:white;border:1px solid #ccc;padding:12px;
     border-radius:8px;font-family:monospace;font-size:12px;
     z-index:1000;box-shadow:0 2px 8px rgba(0,0,0,0.15);
     max-height:80vh;overflow-y:auto">
  <b style="font-size:14px">Properties</b>
  <div id="propContent" style="margin-top:8px;color:#666">
    Click a node to see details
  </div>
</div>
<script>
  network.on("click", function(params) {
    if (params.nodes.length > 0) {
      var nodeId = params.nodes[0];
      var node = nodes.get(nodeId);
      var html = "<b>" + (node.label || nodeId) + "</b><br><br>";
      if (node.title) {
        html += node.title.replace(/\\n/g, "<br>");
      }
      document.getElementById("propContent").innerHTML = html;
    } else {
      document.getElementById("propContent").innerHTML =
        "Click a node to see details";
    }
  });
</script>
"""


def _build_legend(mode: str) -> str:
    """Build legend HTML for the graph."""
    if mode == "deps":
        return """
        <div style="position:fixed;top:10px;right:10px;background:white;
             border:1px solid #ccc;padding:12px;border-radius:8px;
             font-family:monospace;font-size:13px;z-index:1000;
             box-shadow:0 2px 8px rgba(0,0,0,0.15)">
          <b style="font-size:14px">Dependency Graph</b><br><br>
          <span style="color:#4CAF50">■</span> VI<br>
          <span style="color:#FF9800">■</span> Library<br>
          <span style="color:#2196F3">■</span> Class<br>
          <span style="color:#9C27B0">■</span> Typedef<br>
          <span style="color:#999">■</span> Stub (missing)<br>
          <br><span style="color:#888">→ depends on</span>
        </div>
        """
    return """
    <div style="position:fixed;top:10px;right:10px;background:white;
         border:1px solid #ccc;padding:12px;border-radius:8px;
         font-family:monospace;font-size:13px;z-index:1000;
         box-shadow:0 2px 8px rgba(0,0,0,0.15)">
      <b style="font-size:14px">Dataflow Graph</b><br><br>
      <span style="color:#4CAF50">■</span> SubVI call<br>
      <span style="color:#2196F3">■</span> Primitive operation<br>
      <span style="color:#FF9800">◆</span> Structure (case/loop)<br>
      <span style="color:#9C27B0">●</span> Constant<br>
      <br><span style="color:#888">→ data flow</span>
    </div>
    """


def _inject_extras(output: Path, mode: str) -> None:
    """Inject legend and properties panel into generated HTML."""
    html = output.read_text(encoding="utf-8")
    extras = _build_legend(mode) + _PROPERTIES_PANEL
    html = html.replace("</body>", extras + "</body>")
    output.write_text(html, encoding="utf-8")


def _visualize_dataflow(
    graph: InMemoryVIGraph,
    output: Path,
) -> None:
    """Visualize the dataflow graph for a single VI."""
    from pyvis.network import Network  # type: ignore[import-untyped]

    vis = list(graph.list_vis())
    if not vis:
        print("Error: No VIs loaded", file=sys.stderr)
        return
    primary_vi = vis[0]

    net = Network(
        height="800px",
        width="100%",
        directed=True,
        notebook=False,
    )
    net.set_options(_GRAPH_OPTIONS)

    node_styles = {
        "vi": {"color": "#4CAF50", "shape": "box"},
        "primitive": {"color": "#2196F3", "shape": "box"},
        "structure": {"color": "#FF9800", "shape": "diamond"},
        "constant": {"color": "#9C27B0", "shape": "ellipse"},
    }

    for nid in graph._vi_nodes.get(primary_vi, set()):
        gnode = graph._graph.nodes[nid].get("node")
        if not gnode or nid == primary_vi:
            continue

        kind = getattr(gnode, "kind", "unknown")
        style = node_styles.get(kind, {"color": "#666", "shape": "box"})
        label = _dataflow_label(gnode, kind)
        tooltip = _dataflow_tooltip(gnode, kind, nid)

        # Group by parent structure + frame for visual clustering
        group = None
        if gnode.parent and gnode.frame is not None:
            group = f"{gnode.parent}::{gnode.frame}"
        elif gnode.parent:
            group = gnode.parent

        net.add_node(
            nid,
            label=label,
            color=style["color"],
            shape=style.get("shape", "box"),
            title=tooltip,
            group=group,
        )

    added = {n["id"] for n in net.nodes}
    for nid in added:
        for _, dest, _, data in graph._graph.out_edges(
            nid,
            data=True,
            keys=True,
        ):
            if dest not in added:
                continue
            src_end = data.get("source")
            dst_end = data.get("dest")
            title = ""
            if src_end and dst_end:
                sn = src_end.name or ""
                dn = dst_end.name or ""
                if sn or dn:
                    title = f"{sn} → {dn}"
            net.add_edge(nid, dest, title=title)

    output.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(output))
    _inject_extras(output, "dataflow")


def _visualize_deps(
    graph: InMemoryVIGraph,
    output: Path,
) -> None:
    """Visualize the dependency graph across VIs."""
    from pyvis.network import Network  # type: ignore[import-untyped]

    net = Network(
        height="800px",
        width="100%",
        directed=True,
        notebook=False,
    )
    net.set_options(_GRAPH_OPTIONS)

    dep = graph._dep_graph
    stubs = graph._stubs

    for node_id in dep.nodes:
        attrs = dep.nodes[node_id]
        node_type = attrs.get("node_type", "vi")
        is_stub = node_id in stubs

        colors = {
            "vi": "#4CAF50",
            "library": "#FF9800",
            "class": "#2196F3",
            "typedef": "#9C27B0",
        }
        color = "#999" if is_stub else colors.get(node_type, "#666")

        label = node_id.split(":")[-1] if ":" in node_id else node_id
        tooltip = f"{node_type}: {node_id}"
        if is_stub:
            tooltip += "\n(missing/stub)"
        fields = attrs.get("fields")
        if fields:
            tooltip += f"\nFields: {len(fields)}"
            for i, f in enumerate(fields):
                tooltip += f"\n  [{i}] {f.name}"

        net.add_node(
            node_id,
            label=label,
            color=color,
            shape="box",
            title=tooltip,
            borderWidth=1 if is_stub else 2,
            font={"color": "#999"} if is_stub else {},
        )

    for src, dest in dep.edges:
        net.add_edge(src, dest)

    output.parent.mkdir(parents=True, exist_ok=True)
    net.save_graph(str(output))
    _inject_extras(output, "deps")


def _dataflow_label(gnode, kind: str) -> str:
    """Build readable label for a dataflow node."""
    name = gnode.name or ""
    if kind == "constant":
        val = getattr(gnode, "value", "")
        return f"{val}" if val is not None else "const"
    if kind == "structure":
        lt = getattr(gnode, "loop_type", None)
        frames = getattr(gnode, "frames", [])
        if lt:
            return "While Loop" if lt == "whileLoop" else "For Loop"
        if frames:
            return f"Case [{len(frames)} frames]"
        return name or "Structure"
    return name.replace(".vi", "") or "?"


def _dataflow_tooltip(gnode, kind: str, nid: str) -> str:
    """Build detailed tooltip for a dataflow node."""
    name = gnode.name or nid.split("::")[-1]
    lines = [f"<b>{kind}: {name}</b>", f"ID: {nid}"]

    prim_id = getattr(gnode, "prim_id", None)
    if prim_id:
        lines.append(f"primResID: {prim_id}")

    node_type = getattr(gnode, "node_type", None)
    if node_type:
        lines.append(f"XML class: {node_type}")

    terminals = getattr(gnode, "terminals", [])
    inputs = [t for t in terminals if t.direction == "input"]
    outputs = [t for t in terminals if t.direction == "output"]

    if inputs:
        lines.append("")
        lines.append("<b>Inputs:</b>")
        for t in inputs:
            tname = t.name or f"idx{t.index}"
            ttype = t.lv_type.type_descriptor() if t.lv_type else "Any"
            lines.append(f"  [{t.index}] {tname}: {ttype}")

    if outputs:
        lines.append("")
        lines.append("<b>Outputs:</b>")
        for t in outputs:
            tname = t.name or f"idx{t.index}"
            ttype = t.lv_type.type_descriptor() if t.lv_type else "Any"
            lines.append(f"  [{t.index}] {tname}: {ttype}")

    if kind == "constant":
        val = getattr(gnode, "value", None)
        raw = getattr(gnode, "raw_value", None)
        lv_type = getattr(gnode, "lv_type", None)
        lines.append(f"\\nValue: {val!r}")
        if raw:
            lines.append(f"Raw: {raw}")
        if lv_type:
            lines.append(f"Type: {lv_type.type_descriptor()}")

    if kind == "structure":
        frames = getattr(gnode, "frames", [])
        if frames:
            lines.append("")
            lines.append("<b>Frames:</b>")
            for f in frames:
                default = " (default)" if f.is_default else ""
                lines.append(f"  {f.selector_value}{default}")

    return "\\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
