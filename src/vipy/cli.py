"""Command-line interface for vipy."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, convert_vi, convert_xml, summarize_vi
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

    # Summarize command (for debugging/inspection)
    summary_parser = subparsers.add_parser("summarize", help="Show VI summary without converting")
    summary_parser.add_argument("input", help="Block diagram XML (*_BDHb.xml)")
    summary_parser.add_argument("--main-xml", help="Main VI XML file")

    # Check command
    check_parser = subparsers.add_parser("check", help="Check if dependencies are available")

    # Structure command
    struct_parser = subparsers.add_parser("structure", help="Analyze LabVIEW project structure")
    struct_parser.add_argument("input", help="Directory, .lvlib, or .lvclass file")
    struct_parser.add_argument("--json", action="store_true", help="Output as JSON")
    struct_parser.add_argument("--plan", action="store_true", help="Generate Python structure plan")

    args = parser.parse_args()

    if args.command == "convert":
        return cmd_convert(args)
    elif args.command == "summarize":
        return cmd_summarize(args)
    elif args.command == "check":
        return cmd_check(args)
    elif args.command == "structure":
        return cmd_structure(args)
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

    try:
        if input_path.suffix == ".vi":
            code = convert_vi(input_path, llm_config=config)
        elif input_path.name.endswith("_BDHb.xml"):
            code = convert_xml(input_path, args.main_xml, llm_config=config)
        else:
            print(f"Error: Unsupported file type: {input_path.suffix}", file=sys.stderr)
            print("Expected .vi file or *_BDHb.xml", file=sys.stderr)
            return 1

        if args.output:
            Path(args.output).write_text(code)
            print(f"Written to {args.output}")
        else:
            print(code)

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


if __name__ == "__main__":
    sys.exit(main())
