"""Claude API agent for VI-to-Python conversion.

Calls Anthropic's Claude API to generate Python code from VI context.
Used by the /convert skill for each VI in dependency order.

Supports two modes:
1. Simple prompting (default): Claude generates code from context
2. Tool-use mode: Claude can call analyze_vi/generate_python tools
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from ..types import TypeInfo


class TypeInfoEncoder(json.JSONEncoder):
    """JSON encoder that handles TypeInfo objects and dataclasses.

    This is the JSON serialization boundary - dataclasses are converted
    to dicts only here, at the actual json.dumps call.
    """

    def default(self, obj):
        if isinstance(obj, TypeInfo):
            return obj.to_dict()
        # Handle dataclasses at JSON boundary
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        return super().default(obj)

try:
    import anthropic
except ImportError:
    anthropic = None


# Tool definitions - import from shared schemas
from ..mcp.schemas import get_all_tool_schemas

VIPY_TOOLS = get_all_tool_schemas()


class GraphToolExecutor:
    """Execute tools against a loaded VI graph."""

    def __init__(self, graph):
        """Initialize with an InMemoryVIGraph instance."""
        self.graph = graph

    def execute(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool and return the result as a string."""
        from pathlib import Path

        try:
            # Stateless MCP tools
            if name == "analyze_vi":
                from ..mcp.tools import analyze_vi
                result = analyze_vi(
                    vi_path=args["vi_path"],
                    search_paths=args.get("search_paths"),
                    expand_subvis=args.get("expand_subvis", True),
                )
                return json.dumps({
                    "vi_name": result.vi_name,
                    "summary": result.summary,
                    "controls": [{"name": c.name, "type": c.type} for c in result.controls],
                    "indicators": [{"name": i.name, "type": i.type} for i in result.indicators],
                    "dependencies": result.dependencies,
                }, indent=2)

            elif name == "generate_documents":
                from ..mcp.tools import generate_documents
                result = generate_documents(
                    library_path=args["library_path"],
                    output_dir=args["output_dir"],
                    search_paths=args.get("search_paths"),
                )
                return result

            elif name == "generate_python":
                from ..mcp.tools import generate_python
                result = generate_python(
                    vi_path=args["vi_path"],
                    output_dir=args["output_dir"],
                    search_paths=args.get("search_paths"),
                )
                return json.dumps({
                    "success": result.success,
                    "files": result.files,
                    "errors": result.errors,
                    "needs_review": result.needs_review,
                }, indent=2)

            # Graph-based tools
            elif name == "load_vi":
                # Graph is typically pre-loaded, but support loading more VIs
                from pathlib import Path as P
                vi_path = P(args["vi_path"])
                search_paths = [P(p) for p in args.get("search_paths", [])] or None
                self.graph.load_vi(vi_path, expand_subvis=args.get("expand_subvis", True), search_paths=search_paths)
                return json.dumps({"loaded_vis": list(self.graph.get_all_vi_names())}, indent=2)

            elif name == "list_loaded_vis":
                return json.dumps({"loaded_vis": list(self.graph.get_all_vi_names())}, indent=2)

            elif name == "generate_ast_code":
                vi_name = args["vi_name"]
                context = self.graph.get_vi_context(vi_name)
                if not context:
                    return f"VI not found: {vi_name}"
                # Use AST code generator
                from .codegen import build_module
                try:
                    code = build_module(context, vi_name)
                    return code
                except Exception as e:
                    return f"AST generation failed: {e}"

            elif name == "get_vi_context":
                vi_name = args["vi_name"]
                context = self.graph.get_vi_context(vi_name)
                if not context:
                    return f"VI not found: {vi_name}"
                # Serialize context (TypeInfoEncoder handles dataclasses)
                return json.dumps(context, indent=2, cls=TypeInfoEncoder)

            elif name == "read_file":
                path = Path(args["file_path"])
                if not path.exists():
                    return f"Error: File not found: {path}"
                content = path.read_text()
                if len(content) > 10000:
                    content = content[:10000] + "\n... (truncated)"
                return content

            elif name == "write_file":
                path = Path(args["file_path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(args["content"])
                return f"Successfully wrote {len(args['content'])} bytes to {path}"

            else:
                return f"Unknown tool: {name}"

        except Exception as e:
            return f"Error executing {name}: {e}"


@dataclass
class ConversionResult:
    """Result from Claude agent conversion."""

    success: bool
    code: str
    time_seconds: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None


SYSTEM_PROMPT = """You are a code generator that converts LabVIEW VIs to Python functions.

You receive structured context from a Neo4j graph database and output Python code.

## Rules

1. Output ONLY valid Python code - no explanations, no markdown
2. Use exact function names from SubVI signatures provided
3. Use exact import statements provided
4. Follow data_flow for execution order
5. Use constants with their actual values
6. Type annotate all parameters and return type
7. Include `from __future__ import annotations` at top
8. Include `from typing import Any` if using Any
9. Include `from pathlib import Path` if using Path
"""


def build_prompt(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
) -> str:
    """Build the prompt for Claude from VI context."""

    # Clean VI name for function
    func_name = vi_name.replace(".vi", "").replace(".VI", "")
    if ":" in func_name:
        func_name = func_name.split(":")[-1]
    func_name = func_name.lower().replace(" ", "_").replace("-", "_")
    func_name = "".join(c for c in func_name if c.isalnum() or c == "_")

    # Build imports section
    imports = []
    if subvi_imports:
        imports.extend(subvi_imports)
    if primitive_imports:
        imports.extend(primitive_imports)

    imports_text = "\n".join(imports) if imports else "# No SubVI or primitive imports needed"

    prompt = f"""Convert this LabVIEW VI to a Python function.

## VI: {vi_name}
## Function name: {func_name}

## Available Imports
{imports_text}

## VI Context (from graph database)
```json
{json.dumps(vi_context, indent=2, cls=TypeInfoEncoder)}
```

## Instructions
- Create function `{func_name}` with typed parameters from `inputs`
- Return values from `outputs`
- Follow `data_flow` for execution order
- Use `operations` to determine what to call
- Use constant values from `constants`

Output ONLY the Python code."""

    return prompt


def convert_vi(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 4096,
) -> ConversionResult:
    """Convert a VI to Python using Claude API.

    Args:
        vi_name: Name of the VI
        vi_context: Context from graph.get_vi_context()
        subvi_imports: List of SubVI import statements
        primitive_imports: List of primitive import statements
        model: Claude model to use
        max_tokens: Max tokens for response

    Returns:
        ConversionResult with generated code
    """
    if anthropic is None:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="anthropic package not installed. Run: pip install anthropic",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="ANTHROPIC_API_KEY environment variable not set",
        )

    client = anthropic.Anthropic(api_key=api_key)

    prompt = build_prompt(vi_name, vi_context, subvi_imports, primitive_imports)

    start_time = time.time()

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        elapsed = time.time() - start_time

        # Extract code from response
        code = response.content[0].text

        # Strip markdown code blocks if present
        if "```python" in code:
            start = code.find("```python") + 9
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()
        elif "```" in code:
            start = code.find("```") + 3
            end = code.find("```", start)
            if end > start:
                code = code[start:end].strip()

        return ConversionResult(
            success=True,
            code=code.strip(),
            time_seconds=elapsed,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

    except Exception as e:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=time.time() - start_time,
            model=model,
            error=str(e),
        )


def convert_with_retry(
    vi_name: str,
    vi_context: dict[str, Any],
    subvi_imports: list[str],
    primitive_imports: list[str],
    validator,
    max_attempts: int = 3,
    model: str = "claude-sonnet-4-20250514",
) -> ConversionResult:
    """Convert VI with validation retry loop.

    Args:
        vi_name: Name of the VI
        vi_context: Context from graph
        subvi_imports: SubVI import statements
        primitive_imports: Primitive import statements
        validator: CodeValidator instance
        max_attempts: Max retry attempts
        model: Claude model to use

    Returns:
        ConversionResult with final code
    """
    total_time = 0
    total_input_tokens = 0
    total_output_tokens = 0

    # Build expected SubVIs for completeness check
    expected_subvis = [
        op["name"]
        for op in vi_context.get("operations", [])
        if "SubVI" in op.get("labels", []) and op.get("name")
    ]

    for attempt in range(1, max_attempts + 1):
        result = convert_vi(
            vi_name, vi_context, subvi_imports, primitive_imports, model
        )

        total_time += result.time_seconds
        total_input_tokens += result.input_tokens
        total_output_tokens += result.output_tokens

        if not result.success:
            return result

        # Validate
        validation = validator.validate(result.code, vi_name, [], expected_subvis)

        if validation.is_valid:
            return ConversionResult(
                success=True,
                code=result.code,
                time_seconds=total_time,
                model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )

        # Build error context for retry
        errors = [e.message for e in validation.errors]

        # Update context with errors for next attempt
        vi_context = {
            **vi_context,
            "_previous_code": result.code,
            "_errors": errors,
            "_attempt": attempt,
        }

    # Max attempts exceeded
    return ConversionResult(
        success=False,
        code=result.code,
        time_seconds=total_time,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        error=f"Validation failed after {max_attempts} attempts: {errors[0] if errors else 'unknown'}",
    )


TOOL_USE_SYSTEM_PROMPT = """You are an expert LabVIEW to Python converter.

You have access to tools that query a loaded VI graph:

1. `get_vi_context` - Get full VI context including inputs, outputs, operations, wires
2. `get_primitive_info` - Look up a primitive by ID to get its Python implementation
3. `get_vilib_info` - Look up a vi.lib VI to get terminal mappings and Python code
4. `list_operations` - List all operations in a VI with resolved primitive info
5. `get_execution_order` - Get topological execution order for a VI
6. `read_file` - Read a file
7. `write_file` - Write Python code to a file

## Workflow

1. Use `get_vi_context` to understand the VI structure
2. Use `get_execution_order` to know the operation sequence
3. For unknown primitives, use `get_primitive_info` to look them up
4. For vilib VIs, use `get_vilib_info` to get implementation hints
5. Write the Python code using `write_file`

## Python Code Requirements

- Use `from __future__ import annotations`
- Type annotate all parameters and return types
- Use NamedTuple for multiple outputs
- Follow dataflow order from the graph
- Handle error clusters appropriately

When done, respond with a summary of the conversion.
"""


def convert_with_tools(
    graph,
    vi_name: str,
    output_path: str,
    model: str = "claude-sonnet-4-20250514",
    max_tokens: int = 8192,
    max_turns: int = 10,
) -> ConversionResult:
    """Convert a VI to Python using Claude with tool calling.

    This gives Claude access to graph query tools. Claude will:
    1. Query the graph to understand the VI
    2. Look up primitives and vilib VIs
    3. Write Python code based on the dataflow

    Args:
        graph: Loaded InMemoryVIGraph with the VI
        vi_name: Name of the VI to convert
        output_path: Path to write the generated Python file
        model: Claude model to use
        max_tokens: Max tokens per response
        max_turns: Max tool-use turns

    Returns:
        ConversionResult with generated code
    """
    if anthropic is None:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="anthropic package not installed. Run: pip install anthropic",
        )

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return ConversionResult(
            success=False,
            code="",
            time_seconds=0,
            model=model,
            error="ANTHROPIC_API_KEY environment variable not set",
        )

    client = anthropic.Anthropic(api_key=api_key)
    executor = GraphToolExecutor(graph)
    start_time = time.time()
    total_input_tokens = 0
    total_output_tokens = 0

    # Build initial prompt
    initial_prompt = f"""Convert this LabVIEW VI to Python:

VI name: {vi_name}
Output file: {output_path}

Start by using get_vi_context to understand the VI, then write the Python code.
"""

    messages = [{"role": "user", "content": initial_prompt}]

    for turn in range(max_turns):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=TOOL_USE_SYSTEM_PROMPT,
                tools=VIPY_TOOLS,
                messages=messages,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            # Check if we're done (no more tool use)
            if response.stop_reason == "end_turn":
                # Extract final message
                final_text = ""
                for block in response.content:
                    if hasattr(block, "text"):
                        final_text += block.text

                # Read the generated code if it was written
                from pathlib import Path
                code = ""
                if Path(output_path).exists():
                    code = Path(output_path).read_text()

                return ConversionResult(
                    success=True,
                    code=code,
                    time_seconds=time.time() - start_time,
                    model=model,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                )

            # Process tool calls
            tool_results = []
            assistant_content = []

            for block in response.content:
                if block.type == "tool_use":
                    # Execute the tool using graph executor
                    result = executor.execute(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                    assistant_content.append(block)
                elif hasattr(block, "text"):
                    assistant_content.append(block)

            # Add assistant message and tool results to conversation
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        except Exception as e:
            return ConversionResult(
                success=False,
                code="",
                time_seconds=time.time() - start_time,
                model=model,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                error=str(e),
            )

    # Max turns exceeded
    return ConversionResult(
        success=False,
        code="",
        time_seconds=time.time() - start_time,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        error=f"Max turns ({max_turns}) exceeded without completion",
    )
