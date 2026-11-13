"""Every Python name lvkit builds from LabVIEW text is the name Python reads
(``python_names.name_characters``): Python normalises identifiers with NFKC when
it parses, so a name written another way is a different name once read back."""

from __future__ import annotations

import ast
import builtins
import keyword
import unicodedata
from collections.abc import Callable

import pytest

from lvkit.codegen.ast_optimizer import eliminate_dead_code
from lvkit.codegen.ast_utils import module_part, parse_expr, to_var_name
from lvkit.codegen.expressions import ExpressionBuilder
from lvkit.codegen.nodes import vi_reference
from lvkit.python_names import name_characters
from lvkit.type_names import derive_python_name
from tests.error_diagrams import (
    I32,
    REQUIRED,
    Project,
    Vi,
    increment,
    project,  # noqa: F401
)

AWKWARD = ["µx", "ﬁle", "Ａbc", "x²", "ｉｆ", "Ünï", "ａ\u3000ｂ", "ａ＿ｂ"]

BUILDERS: dict[str, Callable[[str], str]] = {
    "to_var_name": to_var_name,
    "module_part": module_part,
    "derive_python_name": derive_python_name,
}


@pytest.mark.parametrize("builder", sorted(BUILDERS))
@pytest.mark.parametrize("text", AWKWARD)
def test_a_built_name_is_the_name_python_reads(builder: str, text: str) -> None:
    built = BUILDERS[builder](text)
    parsed = ast.parse(built).body[0]
    assert isinstance(parsed, ast.Expr)
    assert isinstance(parsed.value, ast.Name)
    assert parsed.value.id == built


@pytest.mark.parametrize(
    ("text", "var_name", "module", "class_name"),
    [
        ("µx", "μx", "μx", "Μx"),
        ("ﬁle", "file", "file", "File"),
        ("Ａbc", "abc", "abc", "Abc"),
        ("x²", "x2", "x2", "X2"),
        ("ｉｆ", "if_", "if_", "If"),
        # A full-width space or underscore is a word break like its ASCII form,
        # so a class name never gets an underscore inside it.
        ("ａ　ｂ", "a_b", "a_b", "AB"),
        ("ａ＿ｂ", "a_b", "a_b", "AB"),
    ],
)
def test_the_normalised_spelling_of_each_builder(
    text: str, var_name: str, module: str, class_name: str
) -> None:
    assert to_var_name(text) == var_name
    assert module_part(text) == module
    assert derive_python_name(text) == class_name


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("µx", "μx"),
        ("ﬁle", "file"),
        ("Ａbc", "abc"),
        ("x²", "x2"),
        ("Get Me", "get_me"),
    ],
)
def test_the_formula_terminal_key_is_normalised(text: str, key: str) -> None:
    builder = ExpressionBuilder.__new__(ExpressionBuilder)
    assert builder._to_var_name(text) == key  # noqa: SLF001


def test_a_name_without_unicode_is_built_as_before() -> None:
    assert to_var_name("Get Settings Path") == "get_settings_path"
    assert module_part("Get Settings Path") == "get_settings_path"
    assert derive_python_name("Get Settings Path.ctl") == "GetSettingsPath"
    assert to_var_name("1st-value") == "var_1st_value"
    assert to_var_name("class") == "class_"


@pytest.mark.parametrize("text", AWKWARD)
def test_the_name_written_into_generated_code_is_the_generators(text: str) -> None:
    namespace: dict[str, object] = {
        "keyword": keyword,
        "builtins": builtins,
        "unicodedata": unicodedata,
    }
    exec(vi_reference.MODULE_PART_SOURCE, namespace)  # noqa: S102
    assert namespace["_module_part"](text) == module_part(text)  # type: ignore[operator]


def test_name_characters_keeps_only_what_a_name_may_hold() -> None:
    assert name_characters("a b-c²_é!") == "abc2_é"
    assert name_characters("") == ""


def test_dead_code_keeps_an_assignment_read_through_the_parsed_expression() -> None:
    module = ast.Module(
        body=[
            ast.FunctionDef(
                name="f",
                args=ast.arguments(
                    posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]
                ),
                body=[
                    ast.Assign(
                        targets=[ast.Name(id=to_var_name("µx"), ctx=ast.Store())],
                        value=parse_expr("mod.sv"),
                    ),
                    ast.Return(value=parse_expr("µx.wait()")),
                ],
                decorator_list=[],
            )
        ],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    kept = ast.unparse(eliminate_dead_code(module))
    assert "mod.sv" in kept


def test_controls_and_indicators_named_with_a_micro_sign_run(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi("Micro.vi")
    x = vi.control("µx", I32, REQUIRED)
    out = vi.indicator("µy", I32)
    inc = increment(vi)
    vi.wire(x, inc["in"])
    vi.wire(inc["out"], out)
    project.add(vi)
    package = project.generate("Micro.vi")
    result = package.function(**{"μx": 4})
    assert getattr(result, "μy") == 5  # noqa: B009
