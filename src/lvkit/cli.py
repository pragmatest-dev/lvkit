"""Command-line interface for lvkit."""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import webbrowser
from pathlib import Path

from . import __version__, primitive_resolver, vilib_resolver
from .graph import InMemoryVIGraph
from .graph.loading import LoadMode
from .lv_detect import detect_labview
from .project_store import (
    find_project_store,
    init_project_store,
    install_claude_skills,
    install_copilot_skills,
)
from .structure import (
    discover_project_structure,
    generate_python_structure_plan,
    parse_lvclass,
    parse_lvlib,
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
    args: argparse.Namespace, default: LoadMode,
) -> LoadMode:
    """Resolve the effective LoadMode for a command: an explicit ``--load-mode``
    wins, else the command's default."""
    chosen = getattr(args, "load_mode", None)
    return LoadMode(chosen) if chosen else default


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


def _configure_library_roots(
    graph: InMemoryVIGraph, args: argparse.Namespace
) -> None:
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

    primitive_resolver.reset_resolver(project_data_dir=store)
    vilib_resolver.reset_resolver(project_data_dir=store)
    return store


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="lvkit",
        description="Understand, convert, and document LabVIEW VI files.",
    )
    parser.add_argument("--version", action="version", version=f"lvkit {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Structure command
    struct_parser = subparsers.add_parser(
        "structure", help="Analyze LabVIEW project structure"
    )
    struct_parser.add_argument("input", help="Directory, .lvlib, or .lvclass file")
    struct_parser.add_argument("--json", action="store_true", help="Output as JSON")
    struct_parser.add_argument(
        "--plan", action="store_true", help="Generate Python structure plan"
    )

    # MCP server command
    subparsers.add_parser(
        "mcp",
        help="Run MCP server for VI analysis",
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
        help="Search paths for SubVI resolution (can be repeated)",
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
        "-o", "--output", default="outputs",
        help="Output directory",
    )
    gen_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
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
        "output_dir", help="Output directory for HTML files",
    )
    docs_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
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
        "-o", "--output",
        default="outputs/graph.html",
        help="Output HTML file (default: outputs/graph.html)",
    )
    viz_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
    )
    viz_parser.add_argument(
        "--open", action="store_true",
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
            "Output format: 'text' (unified diff, stdout/pipe/CI-friendly, "
            "default), 'json' (ChangeMap for scripts/agents/the VSCode "
            "extension), or 'html' (self-contained interactive viewer file)."
        ),
    )
    diff_parser.add_argument(
        "-v", "--verbose", action="store_true",
        help=(
            "Show the full structured change report instead of the compact "
            "unified diff. Only affects --format text (a detail level, "
            "orthogonal to format)."
        ),
    )
    diff_parser.add_argument(
        "--long", action="store_true",
        help="Back-compat alias for --verbose.",
    )
    diff_parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help=(
            "Output file path (used by --format html; default "
            "outputs/vi-diff/<stemA>__<stemB>.html). "
            "text/json print to stdout unless given."
        ),
    )
    diff_parser.add_argument(
        "--open", action="store_true",
        help="Render --format html and open it in a browser.",
    )
    diff_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
    )
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
        choices=["claude", "copilot", "all"],
        default=None,
        help=(
            "AI agent to install skills for: claude, copilot, or all. "
            "Omit to auto-detect from project layout (CLAUDE.md / .claude/ "
            "for Claude Code; .github/copilot-instructions.md / "
            ".github/instructions/ / .github/agents.md for Copilot)."
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
    render_parser.add_argument("input_path", help="Path to .vi file or _BDHb.xml heap")
    render_parser.add_argument(
        "-o", "--output", default=None, metavar="FILE",
        help="Output SVG path (default: <vi-stem>.svg next to the input)",
    )
    render_parser.add_argument(
        "--search-path", action="append", dest="search_paths", default=[],
        help="Search paths for SubVI resolution (can be repeated)",
    )
    _add_project_root_arg(render_parser)
    _add_load_mode_arg(render_parser)
    _add_library_root_args(render_parser)

    args = parser.parse_args()

    if args.command == "structure":
        return cmd_structure(args)
    elif args.command == "mcp":
        return cmd_mcp(args)
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
    elif args.command == "detect":
        return cmd_detect(args)
    elif args.command == "render":
        return cmd_render(args)
    else:
        parser.print_help()
        return 0


def cmd_structure(args: argparse.Namespace) -> int:
    """Handle the structure command."""
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

        elif input_path.is_dir():
            # Directory - discover full project
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
                if structure['classes']:
                    print("Classes:")
                    for cls in structure['classes']:
                        methods = len(cls['methods'])
                        print(f"  - {cls['name']} ({methods} methods)")

        else:
            print(f"Error: Unsupported file type: {input_path}", file=sys.stderr)
            return 1

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_mcp(_args: argparse.Namespace) -> int:
    """Handle the mcp command - run MCP server."""
    from .mcp.server import main as mcp_main

    try:
        print("Starting MCP server...", file=sys.stderr)
        mcp_main()
        return 0
    except KeyboardInterrupt:
        print("\nShutting down MCP server...", file=sys.stderr)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_describe(args: argparse.Namespace) -> int:
    """Handle the describe command - human-readable VI description."""
    from .graph.describe import describe_vi

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        graph = InMemoryVIGraph()
        _configure_library_roots(graph, args)
        search_paths = [Path(p) for p in args.search_paths]
        graph.load_vi(
            str(input_path), _resolve_load_mode(args, LoadMode.MINIMAL),
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
                c for c in candidates
                if c.startswith(f"{parent_dir}.lvclass:")
                or c.startswith(f"{parent_dir}.lvlib:")
            ]
            if preferred:
                vi_name = preferred[0]

        print(describe_vi(graph, vi_name))

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
    return editors


def cmd_setup(args: argparse.Namespace) -> int:
    """Handle the setup command — install AI skills and create .lvkit/ store."""
    # `directory` and `skills` are both optional positionals, so a lone
    # `lvkit setup copilot` binds "copilot" to `directory`. If the only
    # positional given is a skills choice, treat it as the skills target in
    # the current directory (the obvious intent).
    if args.skills is None and args.directory in ("claude", "copilot", "all"):
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
        editors = ["claude", "copilot"]
    elif explicit in ("claude", "copilot"):
        editors = [explicit]
    else:
        # Auto-detect from project layout
        editors = _detect_ai_editors(root)
        if not editors:
            print(
                "No AI agent detected. If you add one later, run `lvkit setup` again. "
                "If you have one that wasn't detected, run `lvkit setup claude` or "
                "`lvkit setup copilot` to install skills explicitly."
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

    return 0


def cmd_detect(args: argparse.Namespace) -> int:
    """Handle the detect command — report the local LabVIEW install, if any.

    Diagnostic for confirming auto-vilib detection (especially on machines
    with LabVIEW that differ from where the code was written). Always exits 0
    so it can be used in scripts to probe for an install.
    """
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


def cmd_render(args: argparse.Namespace) -> int:
    """Handle the render command — faithful, graph-driven block-diagram SVG."""
    from .render import render_vi_file

    input_path = Path(args.input_path)
    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)
    vilib_root, userlib_root = _parse_library_roots(args)
    search_paths = [Path(p) for p in args.search_paths] if args.search_paths else None

    try:
        svg = render_vi_file(
            input_path,
            search_paths=search_paths,
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            mode=_resolve_load_mode(args, LoadMode.MINIMAL),
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

    if args.output:
        out = Path(args.output)
    else:
        stem = input_path.stem.replace("_BDHb", "")
        out = input_path.with_name(f"{stem}.svg")
    out.write_text(svg)
    print(f"Rendered {out}")
    return 0


def _load_diff_graphs(
    args: argparse.Namespace, path_a: Path, path_b: Path, *, layout: bool,
) -> tuple[InMemoryVIGraph, str, InMemoryVIGraph, str]:
    """Load both sides of a diff pair with a shared load mode/search paths."""
    search_paths = [Path(p) for p in args.search_paths]
    diff_mode = _resolve_load_mode(args, LoadMode.MINIMAL)

    graph_a = InMemoryVIGraph()
    _configure_library_roots(graph_a, args)
    graph_a.load_vi(
        str(path_a), diff_mode, search_paths=search_paths, layout=layout,
    )
    vi_name_a = graph_a.resolve_vi_name(path_a.name)

    graph_b = InMemoryVIGraph()
    _configure_library_roots(graph_b, args)
    graph_b.load_vi(
        str(path_b), diff_mode, search_paths=search_paths, layout=layout,
    )
    vi_name_b = graph_b.resolve_vi_name(path_b.name)

    return graph_a, vi_name_a, graph_b, vi_name_b


def cmd_diff(args: argparse.Namespace) -> int:
    """Handle the diff command — compare two VI versions.

    Output is picked with ``--format {text,json,html}`` (the lvkit house
    convention — one flag for mutually-exclusive output projections, never a
    boolean per format). ``-v/--verbose`` (and its back-compat alias
    ``--long``) is the orthogonal DETAIL axis: it only changes how much
    ``text`` shows.
    """
    from .graph.diff import diff_structured, diff_text, diff_uid

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
    verbose = args.verbose or args.long

    _configure_resolvers(args)

    try:
        if fmt == "text":
            graph_a, vi_name_a, graph_b, vi_name_b = _load_diff_graphs(
                args, path_a, path_b, layout=False,
            )
            if verbose:
                report = diff_structured(graph_a, graph_b, vi_name_a, vi_name_b)
                if report.is_empty():
                    print("No changes detected.")
                else:
                    print(report.format())
            else:
                result = diff_text(
                    graph_a, graph_b, vi_name_a, vi_name_b,
                    label_a=str(path_a), label_b=str(path_b),
                )
                if result:
                    print(result)
                else:
                    print("No changes detected.")

            if sys.stdout.isatty():
                print(
                    "\nTip: lvkit diff … --format html --open  for a "
                    "visual, navigable diff."
                )
            return 0

        graph_a, vi_name_a, graph_b, vi_name_b = _load_diff_graphs(
            args, path_a, path_b, layout=True,
        )

        if fmt == "json":
            cmap = diff_uid(graph_a, graph_b, vi_name_a, vi_name_b)
            text = json.dumps(cmap.to_dict(), indent=2)
            if args.output:
                Path(args.output).write_text(text)
            else:
                print(text)
            return 0

        # fmt == "html"
        from .render import render_vi
        from .render.diff_viewer import build_diff_viewer

        before_svg = render_vi(graph_a, vi_name_a, interactive=False)
        after_svg = render_vi(graph_b, vi_name_b, interactive=False)
        if before_svg is None or after_svg is None:
            print(
                "Error: render declined — required diagram geometry is "
                "missing (see logs for the missing ids)",
                file=sys.stderr,
            )
            return 1

        cmap = diff_uid(graph_a, graph_b, vi_name_a, vi_name_b)
        html = build_diff_viewer(
            cmap, before_svg, after_svg,
            title=path_a.name,
            before_label=path_a.stem,
            after_label=path_b.stem,
        )

        out = (
            Path(args.output) if args.output
            else Path("outputs/vi-diff") / f"{path_a.stem}__{path_b.stem}.html"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html)
        print(f"Wrote {out}")

        if args.open:
            webbrowser.open(out.resolve().as_uri())

        return 0
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
        sp = [Path(p) for p in args.search_paths] if args.search_paths else None
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
            search_paths=args.search_paths if args.search_paths else None,
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

    graph = InMemoryVIGraph()
    _configure_library_roots(graph, args)
    search_paths = (
        [Path(p) for p in args.search_paths] if args.search_paths else None
    )
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
        import webbrowser
        webbrowser.open(f"file://{Path(args.output).resolve()}")

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
    graph: InMemoryVIGraph, output: Path,
) -> None:
    """Visualize the dataflow graph for a single VI."""
    from pyvis.network import Network  # type: ignore[import-untyped]

    vis = list(graph.list_vis())
    if not vis:
        print("Error: No VIs loaded", file=sys.stderr)
        return
    primary_vi = vis[0]

    net = Network(
        height="800px", width="100%", directed=True, notebook=False,
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
            nid, label=label,
            color=style["color"],
            shape=style.get("shape", "box"),
            title=tooltip,
            group=group,
        )

    added = {n["id"] for n in net.nodes}
    for nid in added:
        for _, dest, _, data in graph._graph.out_edges(
            nid, data=True, keys=True,
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
    graph: InMemoryVIGraph, output: Path,
) -> None:
    """Visualize the dependency graph across VIs."""
    from pyvis.network import Network  # type: ignore[import-untyped]

    net = Network(
        height="800px", width="100%", directed=True, notebook=False,
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
            node_id, label=label, color=color,
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
    inputs = [
        t for t in terminals
        if t.direction == "input" and not t.is_error_cluster
    ]
    outputs = [
        t for t in terminals
        if t.direction == "output" and not t.is_error_cluster
    ]

    if inputs:
        lines.append("")
        lines.append("<b>Inputs:</b>")
        for t in inputs:
            tname = t.name or f"idx{t.index}"
            ttype = t.python_type()
            lines.append(f"  [{t.index}] {tname}: {ttype}")

    if outputs:
        lines.append("")
        lines.append("<b>Outputs:</b>")
        for t in outputs:
            tname = t.name or f"idx{t.index}"
            ttype = t.python_type()
            lines.append(f"  [{t.index}] {tname}: {ttype}")

    if kind == "constant":
        val = getattr(gnode, "value", None)
        raw = getattr(gnode, "raw_value", None)
        lv_type = getattr(gnode, "lv_type", None)
        lines.append(f"\\nValue: {val!r}")
        if raw:
            lines.append(f"Raw: {raw}")
        if lv_type:
            lines.append(f"Type: {lv_type.to_python()}")

    if kind == "structure":
        frames = getattr(gnode, "frames", [])
        if frames:
            lines.append("")
            lines.append("<b>Frames:</b>")
            for f in frames:
                default = " (default)" if f.is_default else ""
                lines.append(
                    f"  {f.selector_value}{default}"
                )

    return "\\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
