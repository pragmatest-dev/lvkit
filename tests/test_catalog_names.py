"""A primitive's Python names come from its catalog terminals' labels, or their
``python_name`` (``codegen/catalog_names.py``)."""

from __future__ import annotations

from lvkit.codegen.catalog_names import catalog_python_names
from lvkit.models import TerminalDirection
from lvkit.primitive_resolver.models import PrimitiveTerminal

IN = TerminalDirection.INPUT
OUT = TerminalDirection.OUTPUT


def _terminal(
    index: int,
    direction: TerminalDirection,
    name: str,
    type_: str = "polymorphic",
    python_name: str | None = None,
) -> PrimitiveTerminal:
    return PrimitiveTerminal(
        index=index,
        direction=direction,
        name=name,
        type=type_,
        python_name=python_name,
    )


def test_a_label_becomes_a_python_name() -> None:
    names = catalog_python_names(
        [
            _terminal(8, IN, "error in (no error)", "Error"),
            _terminal(9, IN, "byte order (0:big-endian, network order)", "UnitUInt16"),
            _terminal(0, OUT, "error out", "Error"),
            _terminal(3, OUT, "rest of the binary string", "String"),
        ]
    )
    assert names == {
        (IN, 8): "error_in",
        (IN, 9): "byte_order",
        (OUT, 0): "error_out",
        (OUT, 3): "rest_of_the_binary_string",
    }


def test_a_python_name_is_used_as_written() -> None:
    names = catalog_python_names(
        [
            _terminal(1, IN, "x"),
            _terminal(2, IN, "y"),
            _terminal(0, OUT, "x*y", python_name="product"),
        ]
    )
    assert names[(OUT, 0)] == "product"
    assert names[(IN, 1)] == "x"


def test_names_of_one_direction_are_distinct() -> None:
    names = catalog_python_names(
        [
            _terminal(1, IN, "x"),
            _terminal(2, IN, "x"),
            _terminal(0, OUT, "x"),
        ]
    )
    assert [names[(IN, 1)], names[(IN, 2)]] == ["x", "x_2"]
    # the other direction is its own scope
    assert names[(OUT, 0)] == "x"


def test_a_terminal_without_a_name_has_no_python_name() -> None:
    assert catalog_python_names([_terminal(4, OUT, "")]) == {}


# ---- the shipped catalog -------------------------------------------------------

import ast  # noqa: E402
import builtins  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402

import pytest  # noqa: E402

from lvkit._data import load_primitives  # noqa: E402
from lvkit.codegen.ast_utils import to_var_name  # noqa: E402
from lvkit.codegen.label_names import catalog_python_label  # noqa: E402

_CATALOG = load_primitives()
_ENTRIES = [
    (f"{section}:{pid}", entry)
    for section in ("primitives", "node_types")
    for pid, entry in _CATALOG[section].items()
    if entry.get("terminals")
]


def _terminals(entry: dict) -> list[PrimitiveTerminal]:
    return [PrimitiveTerminal.model_validate(t) for t in entry["terminals"]]


@pytest.mark.parametrize(("key", "entry"), _ENTRIES, ids=[k for k, _ in _ENTRIES])
def test_distinct_labels_derive_distinct_python_names(key: str, entry: dict) -> None:
    """Within one direction of one entry two different labels never give the same
    Python name, and every name is an identifier. A label a terminal repeats (the
    copies of an expandable terminal) is one name, numbered when generated."""
    for direction in TerminalDirection:
        by_label: dict[str, str] = {}
        for terminal in _terminals(entry):
            if terminal.direction != direction or not terminal.name:
                continue
            name = terminal.python_name or to_var_name(
                catalog_python_label(terminal) or ""
            )
            assert name.isidentifier(), (key, terminal.name)
            # a label with no letter or digit (``?``) has nothing to name it by
            letters = re.search(
                r"[A-Za-z0-9]", catalog_python_label(terminal) or ""
            )
            assert name != "var" or not letters, (key, terminal.name)
            first = by_label.setdefault(terminal.name, name)
            assert first == name, (key, terminal.name)
        names = list(by_label.values())
        assert len(set(names)) == len(names), (key, direction, by_label)


@pytest.mark.parametrize(("key", "entry"), _ENTRIES, ids=[k for k, _ in _ENTRIES])
def test_a_python_name_is_set_only_where_it_differs_from_the_label(
    key: str, entry: dict
) -> None:
    for terminal in _terminals(entry):
        if terminal.python_name:
            derived = to_var_name(catalog_python_label(terminal) or "")
            assert terminal.python_name != derived, (key, terminal.name)
            assert terminal.python_name.isidentifier()
            assert terminal.python_name == to_var_name(terminal.python_name)


@pytest.mark.parametrize(
    ("section", "pid"),
    [
        (section, pid)
        for section in ("primitives", "node_types")
        for pid, entry in _CATALOG[section].items()
        if isinstance(entry.get("python_code"), dict)
    ],
)
def test_every_dict_python_code_key_is_an_output_python_name(
    section: str, pid: str
) -> None:
    entry = _CATALOG[section][pid]
    names = {
        name
        for (direction, _), name in catalog_python_names(_terminals(entry)).items()
        if direction == OUT
    }
    for key in entry["python_code"]:
        if key not in ("_body", "_import"):
            assert key in names, (section, pid, key, sorted(names))


def _template_names(template: str) -> set[str]:
    """The free names an expression template reads: not ``in_N``, a builtin, a
    module the entry imports, a comprehension variable or an attribute."""
    text = re.sub(r"\{[a-z_]+\}", "_placeholder", template)
    try:
        tree = ast.parse(text.strip(), mode="eval")
    except SyntaxError:
        return set()
    bound = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.comprehension)
        for target in ast.walk(node.target)
        if isinstance(target, ast.Name)
    } | {
        arg.arg for node in ast.walk(tree) if isinstance(node, ast.Lambda)
        for arg in node.args.args
    }
    return {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and node.id not in bound
        and not re.fullmatch(r"in_\d+", node.id)
        and node.id != "_placeholder"
        and not hasattr(builtins, node.id)
    }


@pytest.mark.parametrize(("key", "entry"), _ENTRIES, ids=[k for k, _ in _ENTRIES])
def test_a_template_names_only_input_python_names(key: str, entry: dict) -> None:
    code = entry.get("python_code")
    templates = [code] if isinstance(code, str) else list((code or {}).values())
    modules = {
        word
        for imp in entry.get("imports", [])
        for word in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", imp)
    }
    inputs = {
        name
        for (direction, _), name in catalog_python_names(_terminals(entry)).items()
        if direction == IN
    }
    for template in templates:
        if not isinstance(template, str):
            continue
        stray = {
            name
            for name in _template_names(template) - inputs - modules
            if name not in sys.stdlib_module_names
            and name not in ("Path", "__file__")
            and not name.startswith("_")
        }
        assert not stray, (key, template, stray, sorted(inputs))
