"""Command-line interface for vipy."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import traceback
from pathlib import Path

from . import __version__
from .llm import LLMConfig, check_ollama_available, list_models
from .memory_graph import InMemoryVIGraph
from .structure import (
    discover_project_structure,
    generate_python_structure_plan,
    parse_lvclass,
    parse_lvlib,
)


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
    elif args.command == "generate":
        return cmd_generate(args)
    elif args.command == "docs":
        return cmd_docs(args)
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


def cmd_generate(args: argparse.Namespace) -> int:
    """Handle the generate command - AST-based Python generation."""
    from .pipeline import generate_python

    input_path = Path(args.input_path)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    try:
        sp = [Path(p) for p in args.search_paths] if args.search_paths else None
        result = generate_python(
            input_path,
            args.output,
            search_paths=sp,
            expand_subvis=not args.no_expand,
        )
        return 1 if result["error"] > 0 else 0

    except Exception as e:
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


if __name__ == "__main__":
    sys.exit(main())
