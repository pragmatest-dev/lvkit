"""Command-line interface for vipy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, convert_vi, convert_xml, summarize_vi
from .cypher import from_blockdiagram as summarize_vi_cypher
from .cypher import from_directory, from_lvclass, from_lvlib, from_project, from_vi
from .graph import GraphConfig, VIGraph
from .llm import LLMConfig, check_ollama_available, list_models
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
    convert_parser.add_argument("input", help="VI file (.vi) or block diagram XML (*_BDHb.xml)")
    convert_parser.add_argument("-o", "--output", help="Output Python file")
    convert_parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model to use")
    convert_parser.add_argument("--main-xml", help="Main VI XML file (if using BDHb input)")
    convert_parser.add_argument("--fp-xml", help="Front panel XML file (if using BDHb input)")
    convert_parser.add_argument(
        "--mode",
        choices=["script", "gui"],
        default="script",
        help="Output mode: 'script' for single file, 'gui' for NiceGUI frontend/backend split",
    )
    convert_parser.add_argument(
        "--format",
        choices=["text", "cypher"],
        default="text",
        help="Summary format: 'text' (default) or 'cypher' (Neo4j graph format)",
    )

    # Summarize command (for debugging/inspection)
    summary_parser = subparsers.add_parser("summarize", help="Show VI summary without converting")
    summary_parser.add_argument("input", help="Block diagram XML (*_BDHb.xml)")
    summary_parser.add_argument("--main-xml", help="Main VI XML file")
    summary_parser.add_argument(
        "--format",
        choices=["text", "cypher"],
        default="text",
        help="Output format: 'text' (default) or 'cypher' (Neo4j graph format)",
    )

    # Check command
    check_parser = subparsers.add_parser("check", help="Check if dependencies are available")

    # Structure command
    struct_parser = subparsers.add_parser("structure", help="Analyze LabVIEW project structure")
    struct_parser.add_argument("input", help="Directory, .lvlib, or .lvclass file")
    struct_parser.add_argument("--json", action="store_true", help="Output as JSON")
    struct_parser.add_argument("--plan", action="store_true", help="Generate Python structure plan")

    # Graph command - load VIs into Neo4j
    graph_parser = subparsers.add_parser("graph", help="Load VIs into Neo4j graph database")
    graph_parser.add_argument("input", help="VI, directory, .lvlib, .lvclass, or .lvproj")
    graph_parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    graph_parser.add_argument("--user", default="neo4j", help="Neo4j username")
    graph_parser.add_argument("--password", default="vipy-password", help="Neo4j password")
    graph_parser.add_argument("--clear", action="store_true", help="Clear existing graph first")
    graph_parser.add_argument("--no-expand", action="store_true", help="Don't expand SubVIs")
    graph_parser.add_argument("--cypher", action="store_true", help="Output Cypher only, don't load to Neo4j")

    # Agent command - convert with validation loop
    agent_parser = subparsers.add_parser(
        "agent",
        help="Convert VIs to Python with validation loop",
    )
    agent_parser.add_argument("input", help="VI, directory, .lvlib, .lvclass, or .lvproj")
    agent_parser.add_argument("-o", "--output", required=True, help="Output directory")
    agent_parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    agent_parser.add_argument("--user", default="neo4j", help="Neo4j username")
    agent_parser.add_argument("--password", default="vipy-password", help="Neo4j password")
    agent_parser.add_argument("--max-retries", type=int, default=3, help="Max LLM retries per VI")
    agent_parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model")
    agent_parser.add_argument("--no-typecheck", action="store_true", help="Skip mypy type checking")
    agent_parser.add_argument("--generate-ui", action="store_true", help="Generate NiceGUI wrappers")
    agent_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        metavar="DIR",
        help="Additional directories to search for SubVIs (can be repeated)",
    )

    # Experiment command - compare conversion strategies
    exp_parser = subparsers.add_parser(
        "experiment",
        help="Compare conversion strategies on VI(s)",
    )
    exp_parser.add_argument("input", help="VI file or directory")
    exp_parser.add_argument("-o", "--output", default="/tmp/vipy-experiment", help="Output directory")
    exp_parser.add_argument(
        "--strategies",
        default="all",
        help="Strategies to run: 'all' or comma-separated (baseline,two_phase,constraint_fix)",
    )
    exp_parser.add_argument("--uri", default="bolt://localhost:7687", help="Neo4j URI")
    exp_parser.add_argument("--user", default="neo4j", help="Neo4j username")
    exp_parser.add_argument("--password", default="vipy-password", help="Neo4j password")
    exp_parser.add_argument("--max-attempts", type=int, default=3, help="Max retry attempts per strategy")
    exp_parser.add_argument("--model", default="qwen2.5-coder:7b", help="Ollama model")
    exp_parser.add_argument(
        "--search-path",
        action="append",
        dest="search_paths",
        metavar="DIR",
        help="Additional directories to search for SubVIs (can be repeated)",
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
    elif args.command == "experiment":
        return cmd_experiment(args)
    else:
        parser.print_help()
        return 0


def cmd_convert(args: argparse.Namespace) -> int:
    """Handle the convert command."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return 1

    config = LLMConfig(model=args.model)
    mode = args.mode

    try:
        if input_path.suffix == ".vi":
            result = convert_vi(input_path, args.output, llm_config=config, mode=mode)
        elif input_path.name.endswith("_BDHb.xml"):
            # Auto-detect front panel XML if not specified
            fp_xml = args.fp_xml
            if fp_xml is None and mode == "gui":
                fp_path = input_path.parent / input_path.name.replace("_BDHb.xml", "_FPHb.xml")
                if fp_path.exists():
                    fp_xml = str(fp_path)

            result = convert_xml(
                input_path,
                args.main_xml,
                fp_xml,
                args.output,
                llm_config=config,
                mode=mode,
                summary_format=args.format,
            )
        else:
            print(f"Error: Unsupported file type: {input_path.suffix}", file=sys.stderr)
            print("Expected .vi file or *_BDHb.xml", file=sys.stderr)
            return 1

        # Handle output based on mode
        if mode == "gui" and hasattr(result, "frontend_code"):
            if args.output:
                output_path = Path(args.output)
                print(f"Backend written to: {output_path.stem}_backend.py")
                print(f"Frontend written to: {output_path.stem}_frontend.py")
            else:
                print("# === BACKEND ===")
                print(result.backend_code)
                print("\n# === FRONTEND ===")
                print(result.frontend_code)
        else:
            if args.output:
                # Already written by convert function
                print(f"Written to {args.output}")
            else:
                print(result)

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_summarize(args: argparse.Namespace) -> int:
    """Handle the summarize command."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: File not found: {input_path}", file=sys.stderr)
        return 1

    try:
        if args.format == "cypher":
            summary = summarize_vi_cypher(input_path, args.main_xml)
        else:
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
            print("  No models installed. Run: ollama pull qwen2.5-coder:7b")
    else:
        print("✗ Ollama not found. Install from https://ollama.com")

    # Check pylabview
    try:
        import pylabview
        print("✓ pylabview is installed")
    except ImportError:
        print("✗ pylabview not installed. Run: pip install pylabview")

    return 0


def cmd_structure(args: argparse.Namespace) -> int:
    """Handle the structure command."""
    import json

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
    """Handle the graph command - load VIs into Neo4j."""
    input_path = Path(args.input)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    expand_subvis = not args.no_expand

    try:
        # Generate Cypher based on input type
        suffix = input_path.suffix.lower()

        if suffix == ".vi":
            from .cypher import extract_vi_xml
            bd_xml, fp_xml, main_xml = extract_vi_xml(input_path)
            cypher = from_vi(bd_xml, fp_xml, main_xml, expand_subvis=expand_subvis)
        elif suffix == ".lvlib":
            cypher = from_lvlib(input_path, expand_subvis=expand_subvis)
        elif suffix == ".lvclass":
            cypher = from_lvclass(input_path, expand_subvis=expand_subvis)
        elif suffix == ".lvproj":
            cypher = from_project(input_path, expand_subvis=expand_subvis)
        elif input_path.is_dir():
            cypher = from_directory(input_path, expand_subvis=expand_subvis)
        else:
            print(f"Error: Unsupported file type: {suffix}", file=sys.stderr)
            print("Supported: .vi, .lvlib, .lvclass, .lvproj, or directory", file=sys.stderr)
            return 1

        # Output Cypher only?
        if args.cypher:
            print(cypher)
            return 0

        # Load into Neo4j
        config = GraphConfig(
            uri=args.uri,
            username=args.user,
            password=args.password,
        )

        print(f"Connecting to Neo4j at {args.uri}...")
        graph = VIGraph(config)
        graph.connect()

        if args.clear:
            print("Clearing existing graph...")
            graph.clear()

        print("Loading VI graph...")
        graph._load_cypher(cypher)

        # Show summary
        vis = graph.list_vis()
        print(f"Loaded {len(vis)} VI(s) into Neo4j")
        for vi in vis[:10]:
            print(f"  - {vi}")
        if len(vis) > 10:
            print(f"  ... and {len(vis) - 10} more")

        # Show conversion order
        order = graph.get_conversion_order()
        if order:
            print("\nConversion order (leaves first):")
            for i, vi in enumerate(order, 1):
                print(f"  {i}. {vi}")

        # Report primitives discovered
        from .blockdiagram import get_primitives_seen
        prims = get_primitives_seen()
        if prims:
            print(f"\nPrimitives found ({len(prims)} unique):")
            for pid, info in list(prims.items())[:10]:
                vis = ", ".join(info["vi_names"][:3])
                print(f"  #{pid}: {info['count']}x in [{vis}]")
            if len(prims) > 10:
                print(f"  ... and {len(prims) - 10} more")

        graph.close()
        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_agent(args: argparse.Namespace) -> int:
    """Handle the agent command - convert with validation loop."""
    from .agent import ConversionAgent, ConversionConfig
    from .cypher import extract_vi_xml

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    try:
        # Connect to Neo4j
        graph_config = GraphConfig(
            uri=args.uri,
            username=args.user,
            password=args.password,
        )

        print(f"Connecting to Neo4j at {args.uri}...")
        graph = VIGraph(graph_config)
        graph.connect()

        # Clear existing graph data
        graph.clear()

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

        # Load VIs into graph based on input type
        suffix = input_path.suffix.lower()
        print(f"Loading VIs from {input_path}...")

        if suffix == ".vi":
            graph.load_vi(input_path, expand_subvis=True, search_paths=search_paths or None)
        elif suffix == ".lvlib":
            from .cypher import from_lvlib
            cypher = from_lvlib(input_path, expand_subvis=True)
            graph._load_cypher(cypher)
        elif suffix == ".lvclass":
            from .cypher import from_lvclass
            cypher = from_lvclass(input_path, expand_subvis=True)
            graph._load_cypher(cypher)
        elif suffix == ".lvproj":
            from .cypher import from_project
            cypher = from_project(input_path, expand_subvis=True)
            graph._load_cypher(cypher)
        elif input_path.is_dir():
            from .cypher import from_directory
            cypher = from_directory(input_path, expand_subvis=True)
            graph._load_cypher(cypher)
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
                    print(f"  - {r.vi_name}: {r.errors[0] if r.errors else 'Unknown error'}")

        graph.close()
        return 0 if failed == 0 else 1

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_experiment(args: argparse.Namespace) -> int:
    """Handle the experiment command - compare conversion strategies."""
    from .agent.experiment import run_experiment
    from .agent.strategies import list_strategies

    input_path = Path(args.input)
    output_dir = Path(args.output)

    if not input_path.exists():
        print(f"Error: Path not found: {input_path}", file=sys.stderr)
        return 1

    # Parse strategies
    if args.strategies == "all":
        strategies = list_strategies()
    else:
        strategies = [s.strip() for s in args.strategies.split(",")]

    # Validate strategies
    available = list_strategies()
    for s in strategies:
        if s not in available:
            print(f"Error: Unknown strategy '{s}'", file=sys.stderr)
            print(f"Available: {', '.join(available)}", file=sys.stderr)
            return 1

    # Build search paths
    search_paths: list[Path] = []
    if args.search_paths:
        for sp in args.search_paths:
            p = Path(sp)
            if p.exists():
                search_paths.append(p)
            else:
                print(f"Warning: Search path does not exist: {sp}")

    # Configure LLM
    llm_config = LLMConfig(model=args.model)

    print(f"Running experiment on: {input_path}")
    print(f"Strategies: {', '.join(strategies)}")
    print(f"Output: {output_dir}")
    print()

    try:
        results = run_experiment(
            vi_path=input_path,
            strategies=strategies,
            output_dir=output_dir,
            llm_config=llm_config,
            max_attempts=args.max_attempts,
            search_paths=search_paths or None,
        )

        # Return success if any strategy succeeded
        all_failed = all(
            not r.success
            for vi_result in results.vis
            for r in vi_result.results.values()
        )
        return 1 if all_failed else 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
