"""A Property Node's reference input that has no Python value is always a
defined name in the generated module: an unwired reference is the default
object of the node's class, and a reference fed by an unmapped primitive is
that primitive's placeholder output.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

from lvkit.codegen.ast_utils import to_function_name
from lvkit.codegen.builder import build_module
from lvkit.graph import load_vi_by_path
from lvkit.graph.loading import LoadMode
from lvkit.runtime import lv

pytestmark = pytest.mark.needs_samples

SAMPLES = Path(".lvkit/cache/samples")
CONFIG_VI = SAMPLES / "DCAF-DAQModule/source/testing/Config.vi"
CURRENT_VI_PATH_VI = (
    SAMPLES
    / "OpenG/extracted/File Group 0/user.lib/_OpenG.lib/file/file.llb"
    / "Current VI's Path__ogtk.vi"
)


def _generate(vi: Path) -> tuple[str, str]:
    if not vi.exists():
        pytest.skip(f"corpus VI missing: {vi}")
    graph, name = load_vi_by_path(str(vi), LoadMode.FULL)
    ctx = graph.get_vi_context(name)
    source = build_module(ctx, name, graph=graph, soft_unresolved=True)
    return source, to_function_name(ctx.name)


def _undefined_names(source: str) -> set[str]:
    """Names the module reads that no assignment, import, def, class or
    parameter binds anywhere in it, and that are not builtins."""
    tree = ast.parse(source)
    bound = set(dir(builtins))
    loaded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else bound).add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.Import | ast.ImportFrom):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
    return loaded - bound


def test_unwired_daqmx_references_are_default_objects():
    source, func = _generate(CONFIG_VI)
    assert "_lv.default_reference('DAQmx System'" in source
    assert "_lv.default_reference('DAQmx Device'" in source
    assert _undefined_names(source) == set()

    namespace: dict = {}
    exec(compile(source, "config.py", "exec"), namespace)  # noqa: S102
    with pytest.raises(lv.UnavailableReferenceMember, match="DAQmx System.*devnames"):
        namespace[func]()


def test_reference_from_an_unmapped_primitive_is_a_placeholder_output():
    source, func = _generate(CURRENT_VI_PATH_VI)
    assert "_lv.placeholder('Open VI Reference'" in source
    assert _undefined_names(source) == set()

    namespace: dict = {}
    exec(compile(source, "current_vis_path.py", "exec"), namespace)  # noqa: S102
    with pytest.raises(lv.UnresolvedOutput, match="Open VI Reference"):
        namespace[func]()
