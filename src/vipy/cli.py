"""Command-line interface for vipy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

from . import __version__, primitive_resolver, vilib_resolver
from .llm import LLMConfig, check_ollama_available, list_models
from .memory_graph import InMemoryVIGraph
from .project_store import find_project_store, init_project_store
from .structure import (
    discover_project_structure,
    generate_python_structure_plan,
    parse_lvclass,
    parse_lvlib,
)


def _add_project_root_arg(parser: argparse.ArgumentParser) -> None:
    """Add --project-root flag to a subparser."""
    parser.add_argument(
        "--project-root",
        default=None,
        metavar="DIR",
        help=(
            "Project root containing a .vipy/ resolution store. "
            "Defaults to walking up from CWD looking for .vipy/."
        ),
    )


def _configure_resolvers(args: argparse.Namespace) -> Path | None:
    """Discover the project store and reset resolver singletons.

    Must be called BEFORE any load_vi() so graph construction sees the
    project mappings (used for terminal-index disambiguation).

    Accepts --project-root in either form: the parent of .vipy/ (the
    project root), or the .vipy/ directory itself.

    Returns the project store directory if one was found, else None.
    """
    project_root = getattr(args, "project_root", None)
    store: Path | None
    if project_root:
        candidate = Path(project_root)
        # Accept both "project root" and ".vipy/" itself
        if candidate.name == ".vipy" and candidate.is_dir():
            store = candidate
        elif (candidate / ".vipy").is_dir():
            store = candidate / ".vipy"
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
        prog="vipy",
        description="Convert LabVIEW VIs to Python code using AI",
    )
    parser.add_argument("--version", action="version", version=f"vipy {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Convert command
    convert_parser = subparsers.add_parser("convert", help="Convert a VI to Python")
    convert_parser.add_argument(
        "input", help="VI file (.vi) or block diagram XML (*_BDHb.xml)"
    )
    # Summarize command (for debugging/inspection)
    summary_parser = subparsers.add_parser(
        "summarize", help="Show VI summary without converting"
    )
    summary_parser.add_argument("input", help="Block diagram XML (*_BDHb.xml)")
    summary_parser.add_argument("--main-xml", help="Main VI XML file")

    # Check command (no additional arguments needed)
    subparsers.add_parser("check", help="Check if dependencies are available")

    # Structure command
    struct_parser = subparsers.add_parser(
        "structure", help="Analyze LabVIEW project structure"
    )
    struct_parser.add_argument("input", help="Directory, .lvlib, or .lvclass file")
    struct_parser.add_argument("--json", action="store_true", help="Output as JSON")
    struct_parser.add_argument(
        "--plan", action="store_true", help="Generate Python structure plan"
    )

    # Agent command - convert with validation loop
    agent_parser = subparsers.add_parser(
        "agent",
        help="Convert VIs to Python with validation loop",
    )
    agent_parser.add_argument(
        "input", help="VI, directory, .lvlib, .lvclass, or .lvproj"
    )
    agent_parser.add_argument("-o", "--output", required=True, help="Output directory")
    agent_parser.add_argument(
        "--max-retries", type=int, default=3, help="Max LLM retries per VI"
    )
    agent_parser.add_argument(
        "--model", default="qwen2.5-coder:14b", help="Ollama model"
    )
    agent_parser.add_argument(
        "--no-typecheck", action="store_true", help="Skip mypy type checking"
    )
    agent_parser.add_argument(
        "--generate-ui", action="store_true", help="Generate NiceGUI wrappers"
    )
    agent_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        metavar="DIR",
        help="Additional directories to search for SubVIs (can be repeated)",
    )
    _add_project_root_arg(agent_parser)

    # Explore command - run NiceGUI project explorer
    explore_parser = subparsers.add_parser(
        "explore",
        help="Run NiceGUI project explorer for converted VIs",
    )
    explore_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory containing converted VIs (default: current directory)",
    )
    explore_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to run the server on (default: 8080)",
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
    desc_parser.add_argument(
        "--chart", action="store_true",
        help="Include Mermaid flowchart diagram",
    )
    _add_project_root_arg(desc_parser)

    # Generate command - AST-based Python generation (replaces convert)
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
    gen_parser.add_argument(
        "--no-expand", action="store_true",
        help="Don't expand SubVIs",
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
            "VILibResolutionNeeded(...)` in the generated Python so a "
            "downstream LLM can fix it contextually."
        ),
    )
    _add_project_root_arg(gen_parser)

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
    docs_parser.add_argument(
        "--no-expand", action="store_true",
        help="Don't expand SubVIs",
    )
    _add_project_root_arg(docs_parser)

    # Visualize command - interactive graph visualization
    viz_parser = subparsers.add_parser(
        "visualize",
        help="Interactive graph visualization in browser",
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
        "--no-expand", action="store_true",
        help="Don't expand SubVIs",
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
    viz_parser.add_argument(
        "--format",
        default=None,
        choices=["interactive", "flowchart"],
        help="Output format: flowchart (Mermaid, default for dataflow) "
        "or interactive (pyvis, default for deps)",
    )
    _add_project_root_arg(viz_parser)

    # LLM generate command - idiomatic Python via LLM
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
        "--long", action="store_true",
        help="Show structured change report instead of unified diff",
    )
    diff_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
    )
    _add_project_root_arg(diff_parser)

    llm_parser = subparsers.add_parser(
        "llm-generate",
        help="Generate idiomatic Python using an LLM",
    )
    llm_parser.add_argument(
        "input_path",
        help="Path to .vi, .lvlib, .lvclass, or directory",
    )
    llm_parser.add_argument(
        "-o", "--output",
        default="outputs",
        help="Output directory",
    )
    llm_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        default=[],
        help="Search paths for SubVI resolution (can be repeated)",
    )
    llm_parser.add_argument(
        "--provider",
        default="anthropic",
        choices=["anthropic", "ollama"],
        help="LLM provider (default: anthropic)",
    )
    llm_parser.add_argument(
        "--model",
        default=None,
        help="Model name (default: provider-specific)",
    )
    llm_parser.add_argument(
        "--no-reference",
        action="store_true",
        help="Don't include AST reference in prompt",
    )
    _add_project_root_arg(llm_parser)

    # Init command - create .vipy/ project store
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize a project-local .vipy/ resolution store",
    )
    init_parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="Directory in which to create .vipy/ (default: current directory)",
    )

    args = parser.parse_args()

    if args.command == "convert":
        return cmd_convert(args)
    elif args.command == "summarize":
        return cmd_summarize(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "structure":
        return cmd_structure(args)
    elif args.command == "graph":
        return cmd_graph(args)
    elif args.command == "agent":
        return cmd_agent(args)
    elif args.command in ("experiment", "claude"):
        print(
            f"Error: 'vipy {args.command}' has been removed."
            " Use 'vipy generate' instead.",
            file=sys.stderr,
        )
        return 1
    elif args.command == "explore":
        return cmd_explore(args)
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
    elif args.command == "llm-generate":
        return cmd_llm_generate(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "init":
        return cmd_init(args)
    else:
        parser.print_help()
        return 0


def cmd_convert(args: argparse.Namespace) -> int:
    """Handle the convert command — deprecated, use 'vipy generate'."""
    print(
        "Error: 'vipy convert' has been removed. Use 'vipy generate' instead.",
        file=sys.stderr,
    )
    return 1


def cmd_summarize(args: argparse.Namespace) -> int:
    """Handle the summarize command."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return 1

    try:
        from .blockdiagram import summarize_vi

        summary = summarize_vi(input_path, args.main_xml)
        print(summary)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_check(args: argparse.Namespace) -> int:
    """Handle the check command."""
    print("Checking dependencies...")
    print()

    # Check Ollama
    if check_ollama_available():
        print("✓ Ollama is available")
        models = list_models()
        if models:
            print(f"  Available models: {', '.join(models)}")
        else:
            print("  No models installed. Run: ollama pull qwen2.5-coder:14b")
    else:
        print("✗ Ollama not found. Install from https://ollama.com")

    # Check pylabview
    if importlib.util.find_spec("pylabview") is not None:
        print("✓ pylabview is installed")
    else:
        print("✗ pylabview not installed. Run: pip install pylabview")

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


def cmd_graph(args: argparse.Namespace) -> int:
    """Handle the graph command — deprecated, use 'vipy generate'."""
    print(
        "Error: 'vipy graph' has been removed. Use 'vipy generate' instead.",
        file=sys.stderr,
    )
    return 1


def cmd_agent(args: argparse.Namespace) -> int:
    """Handle the agent command - convert with validation loop."""
    from .agent import ConversionAgent, ConversionConfig

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)

    try:
        # Build search paths
        search_paths: list[Path] = []
        if args.search_paths:
            for sp in args.search_paths:
                p = Path(sp)
                if p.exists():
                    search_paths.append(p)
                    print(f"Added search path: {p}")
                else:
                    print(f"Warning: Search path does not exist: {sp}")

        # Use in-memory graph (no Neo4j required)
        graph = InMemoryVIGraph()

        # Load VIs into graph based on input type
        suffix = input_path.suffix.lower()
        print(f"Loading VIs from {input_path}...")

        if suffix == ".vi" or suffix == ".xml":
            graph.load_vi(
                input_path, expand_subvis=True, search_paths=search_paths or None
            )
        elif suffix == ".lvlib":
            graph.load_lvlib(
                input_path, expand_subvis=True, search_paths=search_paths or None
            )
        elif suffix == ".lvclass":
            graph.load_lvclass(
                input_path, expand_subvis=True, search_paths=search_paths or None
            )
        elif suffix == ".lvproj":
            graph.load_lvproj(
                input_path, expand_subvis=True, search_paths=search_paths or None
            )
        elif input_path.is_dir():
            graph.load_directory(
                input_path, expand_subvis=True, search_paths=search_paths or None
            )
        else:
            print(f"Error: Unsupported file type: {suffix}", file=sys.stderr)
            return 1

        # Show what's loaded
        vis = graph.list_vis()
        print(f"Found {len(vis)} VI(s) to convert")

        # Configure conversion agent
        llm_config = LLMConfig(model=args.model)
        agent_config = ConversionConfig(
            output_dir=output_dir,
            max_retries=args.max_retries,
            generate_ui=args.generate_ui,
            llm_config=llm_config,
            validate_types=not args.no_typecheck,
        )

        # Run conversion
        agent = ConversionAgent(graph, agent_config)
        results = agent.convert_all()

        # Summary
        succeeded = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)

        print(f"\nOutput written to: {output_dir}")
        print(f"  Succeeded: {succeeded}")
        print(f"  Failed: {failed}")

        if failed > 0:
            print("\nFailed VIs:")
            for r in results:
                if not r.success:
                    err = r.errors[0] if r.errors else "Unknown error"
                    print(f"  - {r.vi_name}: {err}")

        return 0 if failed == 0 else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_explore(args: argparse.Namespace) -> int:
    """Handle the explore command - run NiceGUI project explorer."""
    from .explorer import run_explorer

    try:
        run_explorer(args.directory, args.port)
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_mcp(args: argparse.Namespace) -> int:
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
        search_paths = [Path(p) for p in args.search_paths]
        graph.load_vi(str(input_path), search_paths=search_paths)

        vi_name = graph.resolve_vi_name(input_path.name)

        print(describe_vi(graph, vi_name))

        if args.chart:
            from .graph.flowchart import flowchart

            print()
            print("## Dataflow Chart")
            print()
            print("```mermaid")
            print(flowchart(graph, vi_name))
            print("```")

        return 0
    except (ValueError, FileNotFoundError, KeyError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Handle the init command — create a project-local .vipy/ store."""
    root = Path(args.directory).resolve()
    if not root.is_dir():
        print(f"Error: Not a directory: {root}", file=sys.stderr)
        return 1

    store = init_project_store(root)
    print(f"Initialized project store at {store}")
    print(f"  README: {store / 'README.md'}")
    print()
    print("Next steps:")
    print(
        "  - Create .vipy/primitives.json to override primitive mappings"
        " (use vipy's shipped data/primitives.json as a reference)"
    )
    print(
        "  - Add vi.lib mappings to .vipy/vilib/<category>.json and register them"
        " in .vipy/vilib/_index.json"
    )
    print("  - vipy will check .vipy/ before its shipped data when resolving.")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Handle the diff command — compare two VI versions."""
    from .graph.diff import diff_structured, diff_text

    path_a = Path(args.vi_a)
    path_b = Path(args.vi_b)

    for p in (path_a, path_b):
        if not p.exists():
            print(f"Error: Path not found: {p}", file=sys.stderr)
            return 1

    _configure_resolvers(args)
    search_paths = [Path(p) for p in args.search_paths]

    try:
        graph_a = InMemoryVIGraph()
        graph_a.load_vi(str(path_a), search_paths=search_paths)
        vi_name_a = graph_a.resolve_vi_name(path_a.name)

        graph_b = InMemoryVIGraph()
        graph_b.load_vi(str(path_b), search_paths=search_paths)
        vi_name_b = graph_b.resolve_vi_name(path_b.name)

        if args.long:
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
        result = generate_python(
            input_path,
            args.output,
            search_paths=sp,
            expand_subvis=not args.no_expand,
            soft_unresolved=args.placeholder_on_unresolved,
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
        result = generate_documents(
            library_path=str(input_path),
            output_dir=args.output_dir,
            search_paths=args.search_paths if args.search_paths else None,
            expand_subvis=not args.no_expand,
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

    try:
        import pyvis  # type: ignore[import-untyped]  # noqa: F401
    except ImportError:
        print(
            "Error: pyvis not installed. Run: pip install pyvis",
            file=sys.stderr,
        )
        return 1

    graph = InMemoryVIGraph()
    search_paths = (
        [Path(p) for p in args.search_paths] if args.search_paths else None
    )
    expand = not args.no_expand

    suffix = input_path.suffix.lower()
    if suffix == ".lvclass":
        graph.load_lvclass(str(input_path), expand, search_paths)
    elif suffix == ".lvlib":
        graph.load_lvlib(str(input_path), expand, search_paths)
    elif input_path.is_dir():
        graph.load_directory(str(input_path), expand, search_paths)
    else:
        graph.load_vi(str(input_path), expand, search_paths)

    output = Path(args.output)

    # Default format: flowchart for dataflow, interactive for deps
    fmt = args.format or ("interactive" if args.mode == "deps" else "flowchart")

    if fmt == "flowchart":
        from .graph.flowchart import flowchart_html

        vis = list(graph.list_vis())
        primary_vi = vis[0] if vis else ""
        html = flowchart_html(graph, primary_vi)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(html)
    elif args.mode == "deps":
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
    html = output.read_text()
    extras = _build_legend(mode) + _PROPERTIES_PANEL
    html = html.replace("</body>", extras + "</body>")
    output.write_text(html)


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


def cmd_llm_generate(args: argparse.Namespace) -> int:
    """Handle the llm-generate command — idiomatic Python via LLM."""
    from .llm_pipeline import run_pipeline
    from .llm_provider import LLMConfig

    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    _configure_resolvers(args)
    output_dir = Path(args.output)

    # Build config
    config = LLMConfig(provider=args.provider)
    if args.model:
        config.model = args.model
    elif args.provider == "ollama":
        config.model = "qwen2.5-coder:14b"

    search_paths = [Path(p) for p in args.search_paths] if args.search_paths else None

    print(f"Loading: {input_path}")
    print(f"Provider: {config.provider} ({config.model})")
    print()

    try:
        result = run_pipeline(
            input_path=input_path,
            output_dir=output_dir,
            search_paths=search_paths,
            config=config,
            include_reference=not args.no_reference,
        )

        print("\nResults:")
        print(f"  Total VIs: {result.total}")
        print(f"  LLM generated: {result.llm_generated}")
        print(f"  AST fallback: {result.ast_fallback}")
        print(f"  Errors: {result.errors}")
        print(f"\nOutput: {output_dir}")

        return 0 if result.errors == 0 else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
