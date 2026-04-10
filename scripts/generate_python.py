#!/usr/bin/env python3
"""Generate code using AST builder without LLM.

Uses the new AST-based code generation (builder.py), not skeleton.
This script is a thin wrapper around lvpy.pipeline.generate_python().
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from lvpy.pipeline import generate_python, to_library_name


def main():
    parser = argparse.ArgumentParser(
        description="Generate code using AST builder (no LLM)",
    )
    parser.add_argument("input", help="VI file to convert")
    parser.add_argument(
        "-o", "--output", required=True, help="Output directory",
    )
    parser.add_argument(
        "--search-path", action="append", dest="search_paths",
        default=[], help="Additional search paths",
    )
    parser.add_argument(
        "--generate-ui", action="store_true",
        help="Generate NiceGUI wrappers",
    )
    parser.add_argument(
        "--ui-vilib", action="store_true",
        help="Generate UI for vilib VIs",
    )
    parser.add_argument(
        "--ui-primitives", action="store_true",
        help="Generate UI for primitives",
    )
    parser.add_argument(
        "--auto-update", action="store_true",
        help="Auto-update vilib registry with terminal info",
    )
    parser.add_argument(
        "--placeholder-on-unresolved", action="store_true",
        help=(
            "Don't fail on unknown primitives or vi.lib VIs. Instead emit "
            "an inline `raise PrimitiveResolutionNeeded(...)` / `raise "
            "VILibResolutionNeeded(...)` in the generated Python."
        ),
    )
    args = parser.parse_args()

    result = generate_python(
        args.input,
        args.output,
        search_paths=[Path(p) for p in args.search_paths],
        expand_subvis=True,
        soft_unresolved=args.placeholder_on_unresolved,
    )

    # Generate UI wrappers if requested
    if args.generate_ui:
        _generate_ui_wrappers(args, result)


def _generate_ui_wrappers(args, result: dict) -> None:
    """Generate NiceGUI wrappers from generation result (CLI-only feature)."""
    from lvpy.agent.codegen.ast_utils import to_function_name, to_module_name

    graph = result["graph"]
    vilib_resolver = result["vilib_resolver"]
    output_dir = result["output_dir"]
    generated = result["generated"]

    from lvpy.agent.context import ContextBuilder

    print("\nGenerating UI wrappers...")
    ui_count = 0
    for vi_name, path, status in generated:
        if not path:
            continue

        # Filter by status based on flags
        if status == "vilib" and not args.ui_vilib:
            continue
        if status not in ("vilib", "ast"):
            continue

        # Get inputs/outputs - use vilib resolver for vilib VIs
        ui_inputs = []
        ui_outputs = []

        if status == "vilib":
            # Get terminal info from vilib resolver
            vilib_ctx = vilib_resolver.get_context(vi_name)
            if vilib_ctx:
                for term in vilib_ctx.get("terminals", []):
                    name = term.get("name", "")
                    term_type = term.get("type") or "Any"
                    direction = term.get("direction", "")
                    if direction in ("in", "input"):
                        ui_inputs.append((name, term_type))
                    elif direction in ("out", "output"):
                        ui_outputs.append((name, term_type))
        else:
            # Get from graph context for AST-generated VIs
            vi_context = graph.get_vi_context(vi_name)
            for inp in vi_context.inputs:
                name = inp.name or "input"
                ctrl_type = inp.control_type or "Any"
                ui_inputs.append((name, ctrl_type))

            for out in vi_context.outputs:
                name = out.name or "output"
                ctrl_type = out.control_type or "Any"
                ui_outputs.append((name, ctrl_type))

        if ui_inputs or ui_outputs:
            module_name = to_module_name(vi_name)
            func_name = to_function_name(vi_name)
            library_name = to_library_name(
                vi_name, graph=graph, vilib_resolver=vilib_resolver,
            )

            # Get enum definitions for vilib VIs
            enums = {}
            if status == "vilib":
                vilib_ctx = vilib_resolver.get_context(vi_name)
                if vilib_ctx:
                    for term in vilib_ctx.get("terminals", []):
                        # Check for inline enum_values first
                        if term.get("enum_values"):
                            # Use display name as key
                            display_name = term.get("name", "")
                            enums[display_name] = term.get("enum_values")
                        # Fall back to named enum reference
                        elif term.get("enum"):
                            enum_name = term.get("enum")
                            all_enums = vilib_resolver.get_enums()
                            if enum_name in all_enums:
                                enum_def = all_enums[enum_name]
                                display_name = term.get("name", "")
                                enums[display_name] = [
                                    (v["value"], name)
                                    for name, v in enum_def.get("values", {}).items()
                                ]

            ui_code = ContextBuilder.build_ui_wrapper(
                vi_name=vi_name,
                module_name=module_name,
                function_name=func_name,
                inputs=ui_inputs,
                outputs=ui_outputs,
                enums=enums,
            )

            # Write UI file to same directory as module
            if library_name:
                lib_dir = output_dir / library_name
                lib_dir.mkdir(parents=True, exist_ok=True)
                ui_path = lib_dir / f"{module_name}_ui.py"
            else:
                ui_path = output_dir / f"{module_name}_ui.py"
            ui_path.write_text(ui_code)
            ui_count += 1

    print(f"  Generated {ui_count} UI wrappers")

    # Copy app.py template
    app_template = (
        Path(__file__).parent.parent / "src" / "lvpy" / "explorer.py"
    )
    if app_template.exists():
        shutil.copy(app_template, output_dir / "app.py")
        print(f"  Copied app.py - run with: python {output_dir}/app.py")


if __name__ == "__main__":
    main()
