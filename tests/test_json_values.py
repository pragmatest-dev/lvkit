"""Flatten To JSON (24201) and Unflatten From JSON (24202) on Python's ``json``:
the text NI's pages show, round trips of each cluster and array shape, ``path``,
the null / strict / extensions inputs, the JSON error codes of NI's General
LabVIEW Error Codes, and the types NI does not accept as gaps."""

from __future__ import annotations

import pytest

from lvkit.codegen.plain_values import PlainErrors, PlainOptions, from_plain, to_plain
from lvkit.models import ClusterField, LVType, LVTypeKind
from tests.helpers import error_cluster_type
from tests.native_vi import Generated, array, generate, node, scalar

I32 = scalar("NumInt32")
U8 = scalar("NumUInt8")
DBL = scalar("NumFloat64")
BOOL = scalar("Boolean")
STR = scalar("String")
ERR = error_cluster_type()


def cluster(*fields: tuple[str, LVType], typedef: str | None = None) -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        typedef_name=typedef,
        fields=[ClusterField(name=n, type=t) for n, t in fields],
    )


def unlabelled(*types: LVType) -> LVType:
    return cluster(*((f"field_{i}", t) for i, t in enumerate(types)))


def flatten(source: LVType, extensions: bool | None = None) -> Generated:
    terminals = [(8, "output", STR), (0, "input", source)]
    wires = [("fp:x", "ft:0"), ("ft:8", "fp:r")]
    constants: dict[str, tuple[object, LVType]] = {}
    if extensions is not None:
        terminals.append((6, "input", BOOL))
        wires.append(("c6", "ft:6"))
        constants["c6"] = (str(extensions), BOOL)
    return generate(
        {"x": source},
        {"r": STR},
        [node("ft", 24201, terminals)],
        wires,
        constants=constants,
    )


def unflatten(
    target: LVType,
    path: bool = False,
    null_to_default: bool | None = None,
    strict: bool | None = None,
    extensions: bool | None = None,
) -> Generated:
    """A VI whose Unflatten From JSON reads ``text`` into ``target`` (its
    default is the ``defaults`` control); the error out is wired."""
    terminals = [
        (0, "input", STR),
        (1, "input", target),
        (3, "input", ERR),
        (8, "output", target),
        (11, "output", ERR),
    ]
    inputs = {"text": STR, "defaults": target, "error_in": ERR}
    wires = [
        ("fp:text", "uf:0"),
        ("fp:defaults", "uf:1"),
        ("fp:error_in", "uf:3"),
        ("uf:8", "fp:v"),
        ("uf:11", "fp:error_out"),
    ]
    constants: dict[str, tuple[object, LVType]] = {}
    if path:
        terminals.append((4, "input", array("String")))
        inputs["path"] = array("String")
        wires.append(("fp:path", "uf:4"))
    for index, value in ((5, null_to_default), (6, extensions), (7, strict)):
        if value is not None:
            terminals.append((index, "input", BOOL))
            wires.append((f"c{index}", f"uf:{index}"))
            constants[f"c{index}"] = (str(value), BOOL)
    return generate(
        inputs,
        {"v": target, "error_out": ERR},
        [node("uf", 24202, terminals)],
        wires,
        constants=constants,
    )


def run_flatten(generated: Generated, value: object) -> str:
    return generated.function(value).r


def run_unflatten(generated: Generated, text: str, default: object, *rest: object):
    return generated.function(text, default, None, *rest)


def code_of(result: object) -> int | None:
    error = result.error_out
    return None if error is None else error.code


LABELLED = cluster(("name", STR), ("count", I32), ("flag", BOOL))
ARRAYS = cluster(("values", array("NumFloat64")), ("tags", array("String")))


def test_catalog_entries_are_ops_with_the_documented_codes() -> None:
    from lvkit.primitive_resolver import get_resolver

    for prim_id, op in ((24201, "FLATTEN_TO_JSON"), (24202, "UNFLATTEN_FROM_JSON")):
        resolved = get_resolver().resolve(prim_id=prim_id)
        assert resolved is not None
        assert resolved.op == op
        assert resolved.gap_reason is None


def test_an_unlabelled_cluster_is_keyed_by_position_as_nis_example() -> None:
    """NI's Unflatten From JSON page: {"0":"abc","1":false,"2":[9,8,7]}."""
    target = unlabelled(STR, BOOL, array("NumFloat64"))
    generated = flatten(target)
    assert run_flatten(generated, ("abc", False, [9.0, 8.0, 7.0])) == (
        '{"0":"abc","1":false,"2":[9.0,8.0,7.0]}'
    )
    assert "lvkit" not in generated.source


def test_a_labelled_cluster_is_keyed_by_label_with_no_spaces() -> None:
    assert run_flatten(flatten(LABELLED), ("a", 3, True)) == (
        '{"name":"a","count":3,"flag":true}'
    )


def test_a_typedef_cluster_is_read_by_attribute() -> None:
    from types import SimpleNamespace

    typedef = cluster(("name", STR), ("count", I32), typedef="Item")
    assert run_flatten(flatten(typedef), SimpleNamespace(name="a", count=2)) == (
        '{"name":"a","count":2}'
    )
    reader = unflatten(typedef)
    result = run_unflatten(reader, '{"name":"b","count":5}', SimpleNamespace())
    assert (result.v.name, result.v.count) == ("b", 5)


@pytest.mark.parametrize(
    ("target", "value"),
    [
        (LABELLED, ("a", 3, True)),
        (unlabelled(STR, BOOL, array("NumFloat64")), ("abc", False, [1.5, 2.0])),
        (array(LABELLED), [("a", 1, True), ("b", 2, False)]),
        (ARRAYS, ([1.5, 2.5], ["x", "y"])),
        (
            cluster(("inner", LABELLED), ("n", I32)),
            (("a", 1, True), 9),
        ),
        (array("NumInt32", 2), [[1, 2], [3, 4]]),
        (array("String"), []),
    ],
)
def test_flatten_then_unflatten_gives_the_value_back(
    target: LVType, value: object
) -> None:
    text = run_flatten(flatten(target), value)
    reader = unflatten(target)
    result = run_unflatten(reader, text, value)
    assert result.v == value
    assert code_of(result) is None


def test_path_into_a_cluster_element_and_an_array_index() -> None:
    text = '{"0":"abc","1":false,"2":[9,8,7]}'
    assert run_unflatten(unflatten(STR, path=True), text, "", ["0"]).v == "abc"
    assert run_unflatten(unflatten(DBL, path=True), text, 0.0, ["2", "0"]).v == 9.0


def test_a_path_that_is_not_there_is_error_375004() -> None:
    result = run_unflatten(unflatten(STR, path=True), '{"a":"x"}', "", ["b"])
    assert code_of(result) == -375004
    assert result.v == ""


def test_invalid_json_is_error_375003() -> None:
    result = run_unflatten(unflatten(LABELLED), "{nope", ("", 0, False))
    assert code_of(result) == -375003


def test_a_json_value_of_the_wrong_type_is_error_375005() -> None:
    result = run_unflatten(
        unflatten(LABELLED), '{"name":1,"count":3,"flag":true}', ("", 0, False)
    )
    assert code_of(result) == -375005


def test_a_missing_cluster_element_is_error_375006() -> None:
    """forums.ni.com/t5/LabVIEW/unflatten-jason/td-p/4058219: -375006, "the
    cluster element is not found in the JSON string"; the element keeps no
    default (forums.ni.com/.../Unflatten-from-JSON-stops-on-missing-cluster-
    elements/td-p/4273868)."""
    result = run_unflatten(
        unflatten(LABELLED, null_to_default=True),
        '{"name":"a","flag":true}',
        ("", 0, False),
    )
    assert code_of(result) == -375006


@pytest.mark.parametrize(("strict", "code"), [(False, None), (True, -375007)])
def test_an_extra_item_is_an_error_only_with_strict_validation(
    strict: bool, code: int | None
) -> None:
    result = run_unflatten(
        unflatten(LABELLED, strict=strict),
        '{"name":"a","count":3,"flag":true,"extra":1}',
        ("", 0, False),
    )
    assert code_of(result) == code
    assert (result.v == ("a", 3, True)) == (code is None)


@pytest.mark.parametrize(("null_to_default", "ok"), [(True, True), (False, False)])
def test_null_takes_the_default_only_with_default_null_elements(
    null_to_default: bool, ok: bool
) -> None:
    result = run_unflatten(
        unflatten(LABELLED, null_to_default=null_to_default),
        '{"name":null,"count":3,"flag":true}',
        ("dflt", 0, False),
    )
    assert (code_of(result) is None) == ok
    if ok:
        assert result.v == ("dflt", 3, True)
    else:
        assert code_of(result) == 42


def test_an_integer_out_of_range_is_error_375012() -> None:
    target = cluster(("n", U8))
    assert run_unflatten(unflatten(target), '{"n":255}', (0,)).v == (255,)
    assert code_of(run_unflatten(unflatten(target), '{"n":256}', (0,))) == -375012


def test_a_ragged_array_is_error_375009() -> None:
    result = run_unflatten(unflatten(array("NumInt32", 2)), "[[1,2],[3]]", [])
    assert code_of(result) == -375009


@pytest.mark.parametrize(
    ("extensions", "flatten_code", "unflatten_code"),
    [(True, None, None), (False, -375010, -375010)],
)
def test_nan_needs_the_labview_extensions(
    extensions: bool, flatten_code: int | None, unflatten_code: int | None
) -> None:
    target = cluster(("x", DBL))
    written = flatten(target, extensions)
    reader = unflatten(target, extensions=extensions)
    nan = float("nan")
    if flatten_code is None:
        assert run_flatten(written, (nan,)) == '{"x":NaN}'
        assert run_unflatten(reader, '{"x":Infinity}', (0.0,)).v == (float("inf"),)
    else:
        with pytest.raises(Exception, match="NaN"):
            run_flatten(written, (nan,))
        assert code_of(run_unflatten(reader, '{"x":-Infinity}', (0.0,))) == -375011


@pytest.mark.parametrize(
    "source",
    [scalar("Path"), cluster(("e", LVType(kind=LVTypeKind.ENUM))), I32],
)
def test_a_type_nis_page_does_not_accept_is_a_gap(source: LVType) -> None:
    generated = flatten(source)
    with pytest.raises(NotImplementedError, match="Flatten To JSON does not handle"):
        generated.function(None)


def test_an_enum_in_the_unflatten_type_is_a_gap() -> None:
    target = cluster(("e", LVType(kind=LVTypeKind.ENUM)))
    generated = unflatten(target)
    with pytest.raises(NotImplementedError, match="Unflatten From JSON does not"):
        generated.function("{}", (0,), None)


class _Context:
    graph = None

    def __init__(self) -> None:
        self.names: set[str] = set()

    def make_output_var(self, base: str, node_id: str) -> str:
        n = 1
        name = base
        while name in self.names:
            n += 1
            name = f"{base}_{n}"
        self.names.add(name)
        return name


def test_to_plain_writes_dicts_lists_and_scalars_from_the_type() -> None:
    ctx = _Context()
    assert to_plain("v", LABELLED, ctx) == (  # type: ignore[arg-type]
        "{'name': v[0], 'count': v[1], 'flag': v[2]}"
    )
    assert to_plain("v", array(unlabelled(I32, STR)), ctx) == (  # type: ignore[arg-type]
        "[{'0': _item0[0], '1': _item0[1]} for _item0 in v]"
    )
    assert to_plain("v", array("NumInt8", 2), ctx) == (  # type: ignore[arg-type]
        "[[_item1 for _item1 in _item0] for _item0 in v]"
    )


def test_from_plain_statements_check_each_part() -> None:
    import ast

    errors = PlainErrors("TM", "EM", "EX", "OR", "DM", "RG")
    options = PlainOptions("False", "False", errors)
    statements = from_plain(
        "src", LABELLED, "dflt", "out", _Context(), "n", options  # type: ignore[arg-type]
    )
    text = ast.unparse(ast.Module(body=statements, type_ignores=[]))
    assert "raise EM(" in text and "raise TM(" in text and "EX" not in text
