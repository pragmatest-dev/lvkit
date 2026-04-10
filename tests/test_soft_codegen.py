"""Tests for soft codegen mode and qualified-path diagnostics (Part 2)."""

from __future__ import annotations

import ast

import pytest

from vipy.agent.codegen.builder import build_module
from vipy.agent.codegen.context import CodeGenContext
from vipy.agent.codegen.nodes.primitive import _emit_unknown
from vipy.agent.codegen.nodes.subvi import _emit_vilib_resolution
from vipy.graph_types import (
    Operation,
    PrimitiveOperation,
    SubVIOperation,
    Terminal,
    VIContext,
    VINode,
)
from vipy.primitive_resolver import PrimitiveResolutionNeeded
from vipy.vilib_resolver import (
    ResolutionContext,
    VILibResolutionNeeded,
)

# ============================================================
# Diagnostic enrichment: qualified_path on exceptions
# ============================================================


def test_primitive_resolution_needed_carries_qualified_vi_name() -> None:
    """PrimitiveResolutionNeeded captures and formats qualified_vi_name."""
    exc = PrimitiveResolutionNeeded(
        prim_id=9999,
        prim_name="Imaginary Primitive",
        terminals=[
            {"index": 0, "direction": "in", "name": "x", "type": "DBL"},
        ],
        vi_name="Foo.vi",
        qualified_vi_name="MyLib.lvlib:Bar.lvclass:Foo.vi",
    )
    assert exc.qualified_vi_name == "MyLib.lvlib:Bar.lvclass:Foo.vi"
    assert "MyLib.lvlib:Bar.lvclass:Foo.vi" in str(exc)
    # Hint mentions both .vipy/ and data/ paths
    assert ".vipy/primitives.json" in str(exc)
    assert "data/primitives.json" in str(exc)


def test_resolution_context_carries_qualified_path() -> None:
    """ResolutionContext captures qualified_path and caller_qualified_name."""
    ctx = ResolutionContext(
        caller_vi="Caller.vi",
        caller_qualified_name="MyProj.lvlib:Caller.vi",
        qualified_path="<vilib>/Utility/error.llb/Some VI.vi",
        wire_types=["idx_0 (in, Boolean, wired)"],
    )
    exc = VILibResolutionNeeded("Some VI.vi", context=ctx)
    msg = str(exc)
    assert "<vilib>/Utility/error.llb/Some VI.vi" in msg
    assert "MyProj.lvlib:Caller.vi" in msg
    assert ".vipy/vilib/" in msg
    assert "data/vilib/" in msg


# ============================================================
# qualified_path populated from parser path_tokens
# ============================================================


def test_vinode_qualified_path_field_exists() -> None:
    """VINode accepts and stores qualified_path."""
    node = VINode(
        id="vi_uid_42",
        vi="Caller.vi",
        name="Foo.vi",
        node_type="iUse",
        terminals=[],
        qualified_path="<vilib>/Foo/bar.vi",
    )
    assert node.qualified_path == "<vilib>/Foo/bar.vi"


def test_operation_qualified_path_field_exists() -> None:
    """Operation accepts and stores qualified_path."""
    op = Operation(
        id="op_1",
        name="something",
        labels=["SubVI"],
        qualified_path="<vilib>/Utility/foo.llb/Bar.vi",
    )
    assert op.qualified_path == "<vilib>/Utility/foo.llb/Bar.vi"


# ============================================================
# Soft mode: primitive emits inline raise
# ============================================================


def test_soft_mode_primitive_emits_raise_statement() -> None:
    """Soft mode emits `raise PrimitiveResolutionNeeded(...)` for unknown prims."""
    node = PrimitiveOperation(
        id="prim_unknown_1",
        name="Mystery",
        labels=["Primitive"],
        terminals=[
            Terminal(
                id="t0", index=0, direction="output", name="result",
            ),
        ],
        primResID=99999,
    )
    ctx = CodeGenContext(soft_unresolved=True, vi_name="Caller.vi")
    fragment = _emit_unknown(node, prim_id=99999, ctx=ctx)

    # Statements pre-bind output then raise
    assert len(fragment.statements) >= 2
    # Last statement is the raise
    last = fragment.statements[-1]
    assert isinstance(last, ast.Raise)

    # Imports include the exception class
    assert any(
        "PrimitiveResolutionNeeded" in imp for imp in fragment.imports
    )

    # Generated AST is parseable as a complete module
    module = ast.Module(body=fragment.statements, type_ignores=[])
    ast.fix_missing_locations(module)
    src = ast.unparse(module)
    assert "raise PrimitiveResolutionNeeded" in src
    assert "prim_id=99999" in src
    assert "Mystery" in src
    # Output var should be pre-bound to None
    assert "result = None" in src or "result_" in src


def test_hard_mode_primitive_still_raises() -> None:
    """Default mode (soft_unresolved=False) still raises immediately."""
    node = PrimitiveOperation(
        id="prim_unknown_2",
        name="Mystery",
        labels=["Primitive"],
        terminals=[],
        primResID=99999,
    )
    ctx = CodeGenContext(soft_unresolved=False, vi_name="Caller.vi")
    with pytest.raises(PrimitiveResolutionNeeded) as exc_info:
        _emit_unknown(node, prim_id=99999, ctx=ctx)
    assert exc_info.value.prim_id == "99999"


# ============================================================
# Soft mode: SubVI emits inline raise
# ============================================================


def test_soft_mode_vilib_emits_raise_statement() -> None:
    """Soft mode emits `raise VILibResolutionNeeded(...)` for unknown vi.lib VIs."""
    node = SubVIOperation(
        id="subvi_1",
        name="Imaginary VI.vi",
        labels=["SubVI"],
        terminals=[
            Terminal(
                id="t1", index=0, direction="input", name="in1",
            ),
            Terminal(
                id="t2", index=1, direction="output", name="out1",
            ),
        ],
        node_type="iUse",
        qualified_path="<vilib>/Imaginary/foo.llb/Imaginary VI.vi",
    )
    ctx = CodeGenContext(soft_unresolved=True, vi_name="Caller.vi")
    fragment = _emit_vilib_resolution(node, ctx, vilib_vi=None)

    # Last statement is the raise
    assert len(fragment.statements) >= 2
    last = fragment.statements[-1]
    assert isinstance(last, ast.Raise)

    # Imports include both classes
    imports_str = " ".join(fragment.imports)
    assert "VILibResolutionNeeded" in imports_str
    assert "ResolutionContext" in imports_str

    # Generated source is sane
    module = ast.Module(body=fragment.statements, type_ignores=[])
    ast.fix_missing_locations(module)
    src = ast.unparse(module)
    assert "raise VILibResolutionNeeded" in src
    assert "Imaginary VI.vi" in src
    # Qualified path is threaded through
    assert "<vilib>/Imaginary/foo.llb/Imaginary VI.vi" in src


def test_hard_mode_vilib_still_raises() -> None:
    """Default mode raises VILibResolutionNeeded immediately."""
    node = SubVIOperation(
        id="subvi_2",
        name="Imaginary VI.vi",
        labels=["SubVI"],
        terminals=[],
        node_type="iUse",
    )
    ctx = CodeGenContext(soft_unresolved=False, vi_name="Caller.vi")
    with pytest.raises(VILibResolutionNeeded) as exc_info:
        _emit_vilib_resolution(node, ctx, vilib_vi=None)
    assert exc_info.value.vi_name == "Imaginary VI.vi"


# ============================================================
# Runtime semantics: generated raise actually raises at runtime
# ============================================================


def test_soft_mode_generated_code_runs_and_raises() -> None:
    """Generated code with soft-mode raise actually raises at runtime."""
    node = PrimitiveOperation(
        id="prim_runtime",
        name="Mystery",
        labels=["Primitive"],
        terminals=[
            Terminal(
                id="t_out", index=0, direction="output", name="result",
            ),
        ],
        primResID=88888,
    )
    ctx = CodeGenContext(
        soft_unresolved=True,
        vi_name="Caller.vi",
        qualified_vi_name="MyLib.lvlib:Caller.vi",
    )
    fragment = _emit_unknown(node, prim_id=88888, ctx=ctx)

    # Wrap in a function and execute
    src_lines = ["from vipy.primitive_resolver import PrimitiveResolutionNeeded"]
    src_lines.append("def f():")
    body_module = ast.Module(body=fragment.statements, type_ignores=[])
    ast.fix_missing_locations(body_module)
    body_src = ast.unparse(body_module)
    for line in body_src.splitlines():
        src_lines.append(f"    {line}")
    src = "\n".join(src_lines)

    # Verify it parses
    ast.parse(src)

    # Execute and verify it raises with the right exception type and message
    namespace: dict[str, object] = {}
    exec(src, namespace)
    f = namespace["f"]
    assert callable(f)

    with pytest.raises(PrimitiveResolutionNeeded) as exc_info:
        f()
    assert exc_info.value.prim_id == "88888"
    assert exc_info.value.qualified_vi_name == "MyLib.lvlib:Caller.vi"


# ============================================================
# build_module accepts soft_unresolved
# ============================================================


def test_build_module_accepts_soft_unresolved() -> None:
    """build_module's soft_unresolved kwarg flows to CodeGenContext."""
    # Empty VI context (no operations) — verify no crash and the flag is set.
    vi_ctx = VIContext(
        name="empty_vi",
        operations=[],
        inputs=[],
        outputs=[],
    )
    code = build_module(vi_ctx, vi_name="empty_vi", soft_unresolved=True)
    # Just confirm we got valid Python back
    ast.parse(code)
    assert "def" in code  # Generated a function


# ============================================================
# Defensive: emit_soft_unresolved guards against non-literal kwargs
# ============================================================


def test_emit_soft_unresolved_rejects_non_literal_kwarg() -> None:
    """emit_soft_unresolved raises TypeError if a literal_kwarg isn't JSON-shaped.

    Repr-then-parse only works for builtin literal types. A custom object
    sneaking into kwargs would produce invalid Python source. The guard
    catches this at codegen time instead of at SyntaxError time.
    """
    from vipy.agent.codegen.unresolved import emit_soft_unresolved

    class NotALiteral:
        pass

    node = PrimitiveOperation(
        id="prim_guard",
        name="X",
        labels=["Primitive"],
        terminals=[],
        primResID=1,
    )
    ctx = CodeGenContext(soft_unresolved=True, vi_name="Foo.vi")

    with pytest.raises(TypeError, match="unsupported type"):
        emit_soft_unresolved(
            node=node,
            ctx=ctx,
            exception_module="x",
            exception_class="X",
            literal_kwargs={"bad": NotALiteral()},  # type: ignore[dict-item]
        )


def test_emit_soft_unresolved_rejects_non_literal_positional_arg() -> None:
    """The same guard applies to positional_args."""
    from vipy.agent.codegen.unresolved import emit_soft_unresolved

    class NotALiteral:
        pass

    node = PrimitiveOperation(
        id="prim_guard_pos",
        name="X",
        labels=["Primitive"],
        terminals=[],
        primResID=1,
    )
    ctx = CodeGenContext(soft_unresolved=True, vi_name="Foo.vi")

    with pytest.raises(TypeError, match="unsupported type"):
        emit_soft_unresolved(
            node=node,
            ctx=ctx,
            exception_module="x",
            exception_class="X",
            positional_args=[NotALiteral()],
        )


def test_codegen_context_child_propagates_soft_mode() -> None:
    """child() must propagate soft_unresolved and qualified_vi_name.

    Otherwise, an unknown primitive inside a loop or case structure
    would silently fall back to hard mode (raising at codegen time)
    even when the parent context requested soft mode.
    """
    parent = CodeGenContext(
        vi_name="Caller.vi",
        qualified_vi_name="MyLib.lvlib:Caller.vi",
        soft_unresolved=True,
    )
    child = parent.child()
    assert child.soft_unresolved is True
    assert child.qualified_vi_name == "MyLib.lvlib:Caller.vi"
    # Loop-depth child also preserves these
    loop_child = parent.child(increment_loop_depth=True)
    assert loop_child.soft_unresolved is True
    assert loop_child.qualified_vi_name == "MyLib.lvlib:Caller.vi"
    assert loop_child.loop_depth == 1


def test_emit_soft_unresolved_source_kwargs_unchecked() -> None:
    """source_kwargs accept raw Python source — not JSON-safety-checked.

    They exist for non-literal values like dataclass constructors. The
    helper inserts them verbatim into the generated raise expression.
    """
    from vipy.agent.codegen.unresolved import emit_soft_unresolved

    node = PrimitiveOperation(
        id="prim_src_kw",
        name="X",
        labels=["Primitive"],
        terminals=[],
        primResID=1,
    )
    ctx = CodeGenContext(soft_unresolved=True, vi_name="Foo.vi")

    # Pass a raw source expression — would fail JSON-safety if checked.
    fragment = emit_soft_unresolved(
        node=node,
        ctx=ctx,
        exception_module="vipy.vilib_resolver",
        exception_class="VILibResolutionNeeded",
        positional_args=["VI Name.vi"],
        source_kwargs={"context": "ResolutionContext(caller_vi='Foo.vi')"},
    )

    # Verify the raw source landed in the generated raise statement.
    module = ast.Module(body=fragment.statements, type_ignores=[])
    ast.fix_missing_locations(module)
    src = ast.unparse(module)
    assert "ResolutionContext(caller_vi='Foo.vi')" in src
    assert "raise VILibResolutionNeeded" in src


def test_primitive_operation_has_no_qualified_path() -> None:
    """Primitives don't have file paths — qualified_path stays None.

    They're identified by primResID, not by an on-disk source file.
    The qualified_path field exists on the base Operation class because
    SubVI operations DO have it, but primitives never set it.
    """
    op = PrimitiveOperation(
        id="prim_no_path",
        name="Add",
        labels=["Primitive"],
        terminals=[],
        primResID=1419,
    )
    assert op.qualified_path is None
