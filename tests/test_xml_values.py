"""Flatten To XML (8400, 8402) and Unflatten From XML (8401) on
``xml.etree.ElementTree``: the text public sources show LabVIEW writing, round
trips of each cluster and array shape, escaping, numbers of each kind, the XML
error codes of NI's General LabVIEW Error Codes, and the types with no XML
written as gaps."""

from __future__ import annotations

import pytest

from lvkit.models import LVType, LVTypeKind
from tests.helpers import error_cluster_type
from tests.native_vi import Generated, array, generate, node, scalar
from tests.test_json_values import cluster, unlabelled

I32 = scalar("NumInt32")
U8 = scalar("NumUInt8")
I64 = scalar("NumInt64")
DBL = scalar("NumFloat64")
SGL = scalar("NumFloat32")
BOOL = scalar("Boolean")
STR = scalar("String")
ERR = error_cluster_type()


def flatten(
    source: LVType, prim_id: int = 8400, label: str = "x"
) -> Generated:
    return generate(
        {label: source},
        {"r": STR},
        [node("ft", prim_id, [(2, "output", STR), (6, "input", source)])],
        [(f"fp:{label}", "ft:6"), ("ft:2", "fp:r")],
    )


def unflatten(target: LVType) -> Generated:
    return generate(
        {"text": STR, "defaults": target, "error_in": ERR},
        {"v": target, "error_out": ERR},
        [
            node(
                "uf",
                8401,
                [
                    (4, "input", ERR),
                    (5, "input", target),
                    (6, "input", STR),
                    (0, "output", ERR),
                    (1, "output", target),
                ],
            )
        ],
        [
            ("fp:text", "uf:6"),
            ("fp:defaults", "uf:5"),
            ("fp:error_in", "uf:4"),
            ("uf:1", "fp:v"),
            ("uf:0", "fp:error_out"),
        ],
    )


def run_flatten(generated: Generated, value: object) -> str:
    return generated.function(value).r


def run_unflatten(generated: Generated, text: str, default: object):
    return generated.function(text, default, None)


def code_of(result: object) -> int | None:
    error = result.error_out
    return None if error is None else error.code


def lines(*text: str) -> str:
    return "\r\n".join(text)


LABELLED = cluster(("name", STR), ("count", I32), ("flag", BOOL))


@pytest.mark.parametrize("prim_id", [8400, 8402])
def test_catalog_entries_are_ops(prim_id: int) -> None:
    from lvkit.primitive_resolver import get_resolver

    resolved = get_resolver().resolve(prim_id=prim_id)
    assert resolved is not None
    assert resolved.op == "FLATTEN_TO_XML"
    assert resolved.gap_reason is None


def test_a_dbl_is_the_text_labview_writes_for_it() -> None:
    """forums.ni.com/t5/LabVIEW/save-and-read-control-value-using-xml/m-p/
    1063145: <DBL> / <Name>Numeric</Name> / <Val>1.00000000000000</Val> / </DBL>
    (the name is the label of the control wired in)."""
    generated = flatten(DBL, label="Numeric")
    text = run_flatten(generated, 1.0)
    assert text == lines(
        "<DBL>", "<Name>Numeric</Name>", "<Val>1.00000000000000</Val>", "</DBL>"
    )
    assert "lvkit" not in generated.source


def test_a_constant_with_no_label_gives_an_empty_name() -> None:
    generated = generate(
        {},
        {"r": STR},
        [node("ft", 8400, [(2, "output", STR), (6, "input", I32)])],
        [("c", "ft:6"), ("ft:2", "fp:r")],
        constants={"c": (7, I32)},
    )
    assert generated.function().r == lines(
        "<I32>", "<Name></Name>", "<Val>7</Val>", "</I32>"
    )


def test_a_labelled_cluster_control_names_the_root_and_keeps_element_names() -> None:
    target = cluster(("a", I32))
    text = run_flatten(flatten(target, label="Settings"), (3,))
    assert text == lines(
        "<Cluster>",
        "<Name>Settings</Name>",
        "<NumElts>1</NumElts>",
        "<I32>",
        "<Name>a</Name>",
        "<Val>3</Val>",
        "</I32>",
        "</Cluster>",
    )


def test_an_unlabelled_i32_cluster_is_the_text_of_the_timestamp_thread() -> None:
    """forums.ni.com/t5/LabVIEW/XML-Timestamp-Interpretation/td-p/445511: a
    cluster with an empty <Name>, <NumElts> 4 and an I32 with an empty <Name>
    each."""
    target = unlabelled(I32, I32)
    assert run_flatten(flatten(target), (0, -1047981475)) == lines(
        "<Cluster>",
        "<Name>x</Name>",
        "<NumElts>2</NumElts>",
        "<I32>",
        "<Name></Name>",
        "<Val>0</Val>",
        "</I32>",
        "<I32>",
        "<Name></Name>",
        "<Val>-1047981475</Val>",
        "</I32>",
        "</Cluster>",
    )


def test_a_labelled_cluster_names_its_elements_and_writes_a_boolean_as_1_or_0() -> None:
    target = cluster(("Enable Controller", BOOL), ("Proportional Feedback", DBL))
    assert run_flatten(flatten(target), (True, 5.4)) == lines(
        "<Cluster>",
        "<Name>x</Name>",
        "<NumElts>2</NumElts>",
        "<Boolean>",
        "<Name>Enable Controller</Name>",
        "<Val>1</Val>",
        "</Boolean>",
        "<DBL>",
        "<Name>Proportional Feedback</Name>",
        "<Val>5.40000000000000</Val>",
        "</DBL>",
        "</Cluster>",
    )


def test_an_array_has_a_dimsize_per_dimension_and_its_elements_in_row_order() -> None:
    text = run_flatten(flatten(array("NumInt32", 2)), [[1, 2], [3, 4]])
    assert text == lines(
        "<Array>",
        "<Name>x</Name>",
        "<Dimsize>2</Dimsize>",
        "<Dimsize>2</Dimsize>",
        *(
            line
            for v in "1234"
            for line in ("<I32>", "<Name></Name>", f"<Val>{v}</Val>", "</I32>")
        ),
        "</Array>",
    )


def test_an_empty_array_has_dimsize_0_and_one_default_element() -> None:
    assert run_flatten(flatten(array("String")), []) == lines(
        "<Array>",
        "<Name>x</Name>",
        "<Dimsize>0</Dimsize>",
        "<String>",
        "<Name></Name>",
        "<Val></Val>",
        "</String>",
        "</Array>",
    )


def test_ampersand_less_than_and_greater_than_are_escaped() -> None:
    """NI's Flatten To XML page: <, > and & become &lt;, &gt; and &amp;."""
    text = run_flatten(flatten(STR), "a<b>&\"c'")
    assert "<Val>a&lt;b&gt;&amp;\"c'</Val>" in text


@pytest.mark.parametrize(
    ("target", "value"),
    [
        (LABELLED, ("a", 3, True)),
        (unlabelled(STR, BOOL, array("NumFloat64")), ("abc", False, [1.5, 2.0])),
        (array(LABELLED), [("a", 1, True), ("b", 2, False)]),
        (
            cluster(("values", array("NumFloat64")), ("tags", array("String"))),
            ([1.5, 2.5], ["x", "y"]),
        ),
        (cluster(("inner", LABELLED), ("n", I32)), (("a", 1, True), 9)),
        (array("NumInt32", 2), [[1, 2], [3, 4]]),
        (array("NumInt32", 2), [[1, 2, 3], [4, 5, 6]]),
        (array("NumInt32", 3), [[[1, 2], [3, 4]], [[5, 6], [7, 8]]]),
        (array("String"), []),
        (array("NumInt32", 2), []),
        (cluster(("s", STR)), ("a<b>&amp; \"q\" 'r'",)),
        (STR, "plain"),
        (I32, -5),
        (
            cluster(
                ("i8", scalar("NumInt8")),
                ("u8", U8),
                ("i16", scalar("NumInt16")),
                ("u16", scalar("NumUInt16")),
                ("u32", scalar("NumUInt32")),
                ("i64", I64),
                ("u64", scalar("NumUInt64")),
                ("d", DBL),
            ),
            (-128, 255, -32768, 65535, 4294967295, -(2**63), 2**64 - 1, 0.25),
        ),
    ],
)
def test_flatten_then_unflatten_gives_the_value_back(
    target: LVType, value: object
) -> None:
    text = run_flatten(flatten(target), value)
    result = run_unflatten(unflatten(target), text, value)
    assert code_of(result) is None
    assert result.v == value


def test_a_double_comes_back_to_15_significant_digits() -> None:
    target = cluster(("x", DBL))
    text = run_flatten(flatten(target), (0.1,))
    assert "<Val>0.100000000000000</Val>" in text
    assert run_unflatten(unflatten(target), text, (0.0,)).v == (0.1,)


def test_nan_and_the_infinities_are_nan_inf_and_minus_inf() -> None:
    target = cluster(("a", DBL), ("b", DBL), ("c", DBL))
    text = run_flatten(flatten(target), (float("nan"), float("inf"), float("-inf")))
    assert "<Val>NaN</Val>" in text
    assert "<Val>Inf</Val>" in text
    assert "<Val>-Inf</Val>" in text
    value = run_unflatten(unflatten(target), text, (0.0, 0.0, 0.0)).v
    assert value[0] != value[0]
    assert value[1:] == (float("inf"), float("-inf"))


def test_the_text_may_be_indented_as_in_nis_book_example() -> None:
    text = """<Cluster>
      <Name>Program Settings</Name>
      <NumElts>2</NumElts>
      <Boolean>
        <Name>Enable Controller</Name>
        <Val>1</Val>
      </Boolean>
      <DBL>
        <Name>Proportional Feedback</Name>
        <Val>5.40000</Val>
      </DBL>
    </Cluster>"""
    target = cluster(("Enable Controller", BOOL), ("Proportional Feedback", DBL))
    result = run_unflatten(unflatten(target), text, (False, 0.0))
    assert result.v == (True, 5.4)


def test_an_empty_string_is_error_1102() -> None:
    assert code_of(run_unflatten(unflatten(LABELLED), "  ", ("", 0, False))) == 1102


def test_an_unknown_tag_is_error_1103() -> None:
    text = "<Frob><Name></Name><Val>1</Val></Frob>"
    assert code_of(run_unflatten(unflatten(I32), text, 0)) == 1103


def test_a_start_tag_with_no_end_tag_is_error_1104() -> None:
    text = "<I32><Name></Name><Val>1</Val>"
    assert code_of(run_unflatten(unflatten(I32), text, 0)) == 1104


def test_a_wrong_number_of_elements_is_error_1105() -> None:
    text = lines(
        "<Cluster>", "<Name></Name>", "<NumElts>1</NumElts>",
        "<I32>", "<Name></Name>", "<Val>1</Val>", "</I32>", "</Cluster>",
    )  # fmt: skip
    target = unlabelled(I32, I32)
    result = run_unflatten(unflatten(target), text, (0, 0))
    assert code_of(result) == 1105
    assert result.v == (0, 0)


def test_a_tag_that_is_not_the_wired_type_is_error_1106() -> None:
    text = "<DBL><Name></Name><Val>1</Val></DBL>"
    assert code_of(run_unflatten(unflatten(I32), text, 0)) == 1106


@pytest.mark.parametrize("text", ["abc", "1.5", "", "1k"])
def test_value_text_that_is_not_an_integer_is_error_1108(text: str) -> None:
    xml = f"<I32><Name></Name><Val>{text}</Val></I32>"
    assert code_of(run_unflatten(unflatten(I32), xml, 0)) == 1108


def test_an_integer_out_of_range_is_error_1108() -> None:
    xml = "<U8><Name></Name><Val>256</Val></U8>"
    assert code_of(run_unflatten(unflatten(U8), xml, 0)) == 1108
    ok = "<U8><Name></Name><Val>255</Val></U8>"
    assert run_unflatten(unflatten(U8), ok, 0).v == 255


@pytest.mark.parametrize(
    "source",
    [SGL, scalar("Path"), cluster(("e", LVType(kind=LVTypeKind.ENUM)))],
)
def test_a_type_with_no_xml_written_is_a_gap(source: LVType) -> None:
    generated = flatten(source)
    with pytest.raises(NotImplementedError, match="Flatten To XML does not handle"):
        generated.function(None)


def test_an_enum_in_the_unflatten_type_is_a_gap() -> None:
    target = cluster(("e", LVType(kind=LVTypeKind.ENUM)))
    generated = unflatten(target)
    with pytest.raises(NotImplementedError, match="Unflatten From XML does not"):
        generated.function("<x/>", (0,), None)
