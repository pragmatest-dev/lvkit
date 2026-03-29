"""Autonomous LLM pipeline for idiomatic Python generation.

Loads VIs into the graph, presents them to an LLM via describe functions,
and collects idiomatic Python output. Falls back to AST reference on failure.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .agent.codegen import build_module
from .graph.core import InMemoryVIGraph
from .graph.describe import describe_constants, describe_operations, describe_vi
from .llm_provider import LLMConfig, LLMResponse, generate

SYSTEM_PROMPT = """\
You are converting LabVIEW VIs to idiomatic Python. You receive a description
of a VI's graph (inputs, outputs, operations, control flow) and a reference
translation that is mechanically correct but not idiomatic.

Your job: write Python that does the SAME thing but reads like a human wrote it.

Rules:
- Same function signature (inputs/outputs) as the reference
- Same behavior — if you're unsure, match the reference exactly
- Use Python idioms: list comprehensions, context managers, exceptions
- No LabVIEW patterns: no "held error" variables, no unnecessary parallelism
- Error clusters become exceptions — don't pass error dicts around
- Keep it simple — if the VI does something simple, write simple code
- Output ONLY the Python code, no explanation
- Include all necessary imports
"""


@dataclass
class ConversionResult:
    """Result of converting a single VI."""

    vi_name: str
    python_code: str
    source: str  # "llm" or "ast_fallback"
    error: str | None = None
    llm_response: LLMResponse | None = None


@dataclass
class PipelineResult:
    """Result of the full pipeline run."""

    results: list[ConversionResult] = field(default_factory=list)
    total: int = 0
    llm_generated: int = 0
    ast_fallback: int = 0
    errors: int = 0


def run_pipeline(
    input_path: Path,
    output_dir: Path,
    search_paths: list[Path] | None = None,
    config: LLMConfig | None = None,
    include_reference: bool = True,
    vi_filter: list[str] | None = None,
) -> PipelineResult:
    """Run the LLM generation pipeline.

    Args:
        input_path: Path to .vi, .lvclass, .lvlib, or directory
        output_dir: Where to write generated Python
        search_paths: SubVI search directories
        config: LLM configuration
        include_reference: Include AST reference in the prompt
        vi_filter: Only convert these VI names (None = all)

    Returns:
        PipelineResult with per-VI results
    """
    if config is None:
        config = LLMConfig()

    graph = InMemoryVIGraph()
    search_path_list = search_paths or [input_path.parent]

    # Load
    suffix = input_path.suffix.lower()
    if suffix == ".lvclass":
        graph.load_lvclass(str(input_path), search_paths=search_path_list)
    elif suffix == ".lvlib":
        graph.load_lvlib(str(input_path), search_paths=search_path_list)
    elif input_path.is_dir():
        graph.load_directory(str(input_path), search_paths=search_path_list)
    else:
        graph.load_vi(str(input_path), search_paths=search_path_list)

    order = graph.get_conversion_order()
    if vi_filter:
        order = [v for v in order if v in vi_filter]

    result = PipelineResult(total=len(order))
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, vi_name in enumerate(order):
        # Skip non-VI entries (ctls, polymorphic wrappers)
        if vi_name.endswith(".ctl"):
            continue

        print(f"  [{i + 1}/{len(order)}] {vi_name}", file=sys.stderr)

        conversion = _convert_vi(
            graph, vi_name, config, include_reference,
        )
        result.results.append(conversion)

        if conversion.source == "llm":
            result.llm_generated += 1
        elif conversion.source == "ast_fallback":
            result.ast_fallback += 1

        if conversion.error:
            result.errors += 1
            print(f"         -> ERROR: {conversion.error}", file=sys.stderr)
        else:
            print(
                f"         -> {conversion.source}: OK",
                file=sys.stderr,
            )

        # Write output
        if conversion.python_code:
            _write_output(output_dir, vi_name, conversion.python_code)

    return result


def _convert_vi(
    graph: InMemoryVIGraph,
    vi_name: str,
    config: LLMConfig,
    include_reference: bool,
) -> ConversionResult:
    """Convert a single VI using the LLM."""
    # Build the prompt from graph description
    try:
        description = describe_vi(graph, vi_name)
        operations = describe_operations(graph, vi_name)
        constants = describe_constants(graph, vi_name)
    except Exception as e:
        return ConversionResult(
            vi_name=vi_name,
            python_code="",
            source="ast_fallback",
            error=f"Graph describe failed: {e}",
        )

    prompt_parts = [
        "## VI Description\n",
        description,
        "\n\n## Operations\n",
        operations,
        "\n\n## Constants\n",
        constants,
    ]

    # Generate AST reference
    reference_code = None
    if include_reference:
        try:
            ctx = graph.get_vi_context(vi_name)
            reference_code = build_module(ctx, vi_name, graph=graph)
            prompt_parts.append(
                "\n\n## Reference Translation"
                " (correct but mechanical)\n"
            )
            prompt_parts.append(f"```python\n{reference_code}\n```")
        except Exception:
            pass  # No reference available — LLM works from description only

    prompt = "\n".join(prompt_parts)

    # Call LLM
    try:
        response = generate(prompt, system=SYSTEM_PROMPT, config=config)
    except Exception as e:
        # LLM failed — fall back to AST
        if reference_code:
            return ConversionResult(
                vi_name=vi_name,
                python_code=reference_code,
                source="ast_fallback",
                error=f"LLM failed: {e}",
            )
        return ConversionResult(
            vi_name=vi_name,
            python_code="",
            source="ast_fallback",
            error=f"LLM failed and no reference: {e}",
        )

    # Extract Python code from response
    code = _extract_python(response.text)

    # Validate syntax
    try:
        ast.parse(code)
    except SyntaxError as e:
        # Invalid syntax — fall back to AST reference
        if reference_code:
            return ConversionResult(
                vi_name=vi_name,
                python_code=reference_code,
                source="ast_fallback",
                error=f"LLM produced invalid syntax: {e}",
                llm_response=response,
            )
        return ConversionResult(
            vi_name=vi_name,
            python_code=code,
            source="llm",
            error=f"Invalid syntax (no fallback): {e}",
            llm_response=response,
        )

    return ConversionResult(
        vi_name=vi_name,
        python_code=code,
        source="llm",
        llm_response=response,
    )


def _extract_python(text: str) -> str:
    """Extract Python code from LLM response.

    Handles responses wrapped in ```python ... ``` blocks.
    """
    # Look for fenced code block
    if "```python" in text:
        start = text.index("```python") + len("```python")
        end = text.index("```", start)
        return text[start:end].strip()

    if "```" in text:
        start = text.index("```") + 3
        # Skip optional language tag on same line
        newline = text.index("\n", start)
        start = newline + 1
        end = text.index("```", start)
        return text[start:end].strip()

    # No code block — assume entire response is code
    return text.strip()


def _write_output(
    output_dir: Path, vi_name: str, code: str,
) -> None:
    """Write generated Python to output directory."""
    # Convert VI name to Python filename
    name = vi_name.replace(".vi", "").replace(".lvclass:", "_")
    name = name.replace(" ", "_").replace(".", "_").lower()
    name = "".join(c for c in name if c.isalnum() or c == "_")

    filepath = output_dir / f"{name}.py"
    filepath.write_text(code)
