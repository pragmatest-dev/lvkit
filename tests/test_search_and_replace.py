"""Search and Replace String (3914): each mode is generated on a one-node VI, its
source is pinned and it is run. The expected values are NI's: "Search and Replace
String Function" (labview-api-ref) and "Special Characters for Match Regular
Expression and Search and Replace String" (labview)."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.codegen.nodes.ops.search_and_replace import REGULAR_EXPRESSION_MODE
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from tests.native_vi import Generated, generate, node, scalar

STR = scalar("String")
I32 = scalar("NumInt32")
BOOL = scalar("Boolean")
_INPUT_TERMINAL = {"ra": 1, "ic": 2, "ml": 3, "search": 5, "repl": 7, "o": 9}
_OUTPUT_TERMINAL = {"r": 4, "n": 8, "p": 10}


def build(
    inputs: dict[str, object],
    regex: bool = False,
    outs: tuple[str, ...] = ("r",),
    gaps: list[Gap] | None = None,
    names: dict[str, str] | None = None,
) -> Generated:
    """``inputs`` maps a terminal ('ra', 'ic', 'ml', 'search', 'repl', 'o') to
    ``"ctl"`` (a control of that name) or a constant's value."""
    sar = node(
        "sr",
        3914,
        [
            (0, "input", STR),
            (1, "input", BOOL),
            (2, "input", BOOL),
            (3, "input", BOOL),
            (4, "output", STR),
            (5, "input", STR),
            (7, "input", STR),
            (8, "output", I32),
            (9, "input", I32),
            (10, "output", I32),
        ],
    )
    if regex:
        sar.obj_flags = REGULAR_EXPRESSION_MODE
    types = {"ra": BOOL, "ic": BOOL, "ml": BOOL, "o": I32, "search": STR, "repl": STR}
    controls: dict = {"s": STR}
    wires = [("fp:s", "sr:0")]
    constants: dict = {}
    for name, value in inputs.items():
        if value == "ctl":
            control = (names or {}).get(name, name)
            controls[control] = types[name]
            wires.append((f"fp:{control}", f"sr:{_INPUT_TERMINAL[name]}"))
        else:
            constants[f"c_{name}"] = (value, types[name])
            wires.append((f"c_{name}", f"sr:{_INPUT_TERMINAL[name]}"))
    outputs = {}
    for out in outs:
        outputs[out] = STR if out == "r" else I32
        wires.append((f"sr:{_OUTPUT_TERMINAL[out]}", f"fp:{out}"))
    return generate(controls, outputs, [sar], wires, constants, gaps=gaps)


def body(generated: Generated) -> str:
    text = generated.source[generated.source.index("def native_test") :]
    return "\n".join(text.splitlines()[1:])


class TestPlainMode:
    def test_replace_all_is_str_replace(self) -> None:
        run = build({"search": "a", "repl": "b", "ra": "True"})
        assert body(run) == (
            "    result_string = s.replace('a', 'b')\n"
            "    return NativeTestResult(r=result_string)"
        )
        assert run.function("banana").r == "bbnbnb"

    def test_replace_first_is_the_default(self) -> None:
        run = build({"search": "a", "repl": "b"})
        assert "    result_string = s.replace('a', 'b', 1)\n" in body(run)
        assert run.function("banana").r == "bbnana"

    def test_unwired_replace_string_deletes_the_search_string(self) -> None:
        run = build({"search": "an", "ra": "True"})
        assert "s.replace('an', '')" in body(run)
        assert run.function("banana").r == "ba"

    def test_empty_search_string(self) -> None:
        # NI: replace all inserts at the beginning, between every character and
        # at the end; replace first inserts at the beginning.
        every = build({"search": "", "repl": "-", "ra": "True"})
        assert every.function("abc").r == "-a-b-c-"
        first = build({"search": "", "repl": "-"})
        assert first.function("abc").r == "-abc"

    def test_inputs_computed_at_run_time(self) -> None:
        run = build({"search": "ctl", "repl": "ctl", "ra": "ctl"})
        assert "    result_string = s.replace(search, repl, -1 if ra else 1)\n" in body(
            run
        )
        assert run.function("banana", "a", "o", True).r == "bonono"
        assert run.function("banana", "a", "o", False).r == "bonana"

    def test_ignore_case_offset_and_count(self) -> None:
        run = build(
            {"search": "a", "repl": "x", "ic": "True", "ra": "True", "o": "ctl"},
            outs=("r", "n"),
        )
        assert body(run) == (
            "    search_start = max(o, 0)\n"
            "    replaced, number_of_replacements = re.subn('a', lambda match: 'x', "
            "s[search_start:], flags=re.ASCII | re.IGNORECASE)\n"
            "    result_string = s[:search_start] + replaced\n"
            "    return NativeTestResult(r=result_string, n=number_of_replacements)"
        )
        assert tuple(run.function("AaAa", 1)) == ("Axxx", 3)
        assert tuple(run.function("AaAa", -4)) == ("xxxx", 4)
        assert tuple(run.function("AaAa", 9)) == ("AaAa", 0)

    def test_replace_string_named_match_is_not_the_lambda_parameter(self) -> None:
        run = build(
            {"search": "a", "repl": "ctl", "o": "ctl"},
            outs=("r", "n"),
            names={"repl": "match"},
        )
        assert "lambda matched: match" in body(run)
        assert tuple(run.function("banana", "X", 0)) == ("bXnana", 1)

    def test_search_string_is_text_not_a_pattern(self) -> None:
        run = build({"search": "a.c", "repl": "-", "o": "ctl"}, outs=("r", "n"))
        assert tuple(run.function("abc a.c", 0)) == ("abc -", 1)
        # The replacement is inserted as it is: no backslash or $ reads.
        literal = build({"search": "a", "repl": "\\1$1", "o": "ctl"}, outs=("r", "n"))
        assert tuple(literal.function("ba", 0)) == ("b\\1$1", 1)

    def test_offset_past_replacement(self) -> None:
        run = build({"search": "a", "repl": "xx", "o": "ctl"}, outs=("r", "n", "p"))
        assert body(run) == (
            "    search_start = max(o, 0)\n"
            "    remainder = s[search_start:]\n"
            "    matches = list(re.finditer('a', remainder))[:1]\n"
            "    pieces = []\n"
            "    cursor = 0\n"
            "    for match in matches:\n"
            "        pieces.append(remainder[cursor:match.start()])\n"
            "        pieces.append('xx')\n"
            "        cursor = match.end()\n"
            "    replaced = ''.join(pieces)\n"
            "    result_string = s[:search_start] + replaced + remainder[cursor:]\n"
            "    number_of_replacements = len(matches)\n"
            "    offset_past_replacement = search_start + len(replaced) "
            "if matches else -1\n"
            "    return NativeTestResult(r=result_string, n=number_of_replacements, "
            "p=offset_past_replacement)"
        )
        # NI: the offset in result string just after the last replacement; the
        # next match, if any, is after it; -1 when nothing was found.
        assert tuple(run.function("a-a-a", 2)) == ("a-xx-a", 1, 4)
        assert tuple(run.function("a-a-a", 0)) == ("xx-a-a", 1, 2)
        assert tuple(run.function("bbb", 0)) == ("bbb", 0, -1)

    def test_offset_past_the_last_of_all_replacements(self) -> None:
        run = build({"search": "a", "repl": "xx", "ra": "True"}, outs=("p",))
        assert "if matches else -1" in body(run)
        assert run.function("a-a-a").p == 8
        assert run.function("b").p == -1

    def test_replace_all_computed_with_the_offset_past(self) -> None:
        run = build({"search": "a", "repl": "x", "ra": "ctl"}, outs=("r", "p"))
        assert "[:None if ra else 1]" in body(run)
        assert tuple(run.function("aaa", True)) == ("xxx", 3)
        assert tuple(run.function("aaa", False)) == ("xaa", 1)


class TestRegularExpressionMode:
    def test_constant_pattern_and_replacement_are_translated_when_generated(
        self,
    ) -> None:
        run = build(
            {"search": "(LabVIEW)", "repl": "to $1!"}, regex=True, outs=("r", "n")
        )
        assert body(run) == (
            "    result_string, number_of_replacements = re.subn('(LabVIEW)', "
            "'to \\\\g<1>!', s, count=1, flags=re.ASCII)\n"
            "    return NativeTestResult(r=result_string, n=number_of_replacements)"
        )
        assert "lvkit" not in run.source
        assert "_pcre_regex" not in run.source
        assert tuple(run.function("Welcome LabVIEW")) == ("Welcome to LabVIEW!", 1)

    def test_ni_examples_run(self) -> None:
        doc = build(
            {
                "search": "We(l)(co(m)e)( )(to)( )t(he) (Lab)(VIE(W)) He(lp)(!)",
                "repl": "$7${11}ful${12}",
            },
            regex=True,
        )
        assert doc.function("Welcome to the LabVIEW Help!").r == "helpful!"
        dollar = build({"search": "(\\d+)(per \\w*)?", "repl": "\\$$1$2"}, regex=True)
        assert dollar.function("total=123 per day").r == "total=$123 per day"
        backref = build(
            {"search": '(["*$])(\\w+)\\1\\2\\1', "repl": "$1$2value$1"}, regex=True
        )
        assert (
            backref.function('$value "TRUE"TRUE" *NULL').r == '$value "TRUEvalue" *NULL'
        )

    def test_multiline_and_ignore_case_flags(self) -> None:
        run = build(
            {"search": "^labview", "repl": "X", "ic": "True", "ml": "ctl"},
            regex=True,
        )
        assert "flags=re.ASCII | re.IGNORECASE | (re.MULTILINE if ml else 0)" in body(
            run
        )
        assert run.function("Hello\nLabVIEW", False).r == "Hello\nLabVIEW"
        assert run.function("Hello\nLabVIEW", True).r == "Hello\nX"

    def test_pattern_computed_at_run_time_writes_the_translators_once(self) -> None:
        run = build({"search": "ctl", "repl": "ctl", "ra": "True"}, regex=True)
        assert run.source.count("def _pcre_regex(pattern: str) -> str:") == 1
        assert run.source.count("def _pcre_template(replacement: str) -> str:") == 1
        assert "re.subn" not in run.source
        assert (
            "    result_string = re.sub(_pcre_regex(search), _pcre_template(repl), s, "
            "flags=re.ASCII)\n"
        ) in body(run)
        assert "lvkit" not in run.source
        assert run.function("a1b22", "(\\d+)", "<$1>").r == "a<1>b<22>"

    def test_regular_expression_with_the_offset_past(self) -> None:
        run = build(
            {"search": "(\\d)", "repl": "[$1]", "ra": "True", "o": "ctl"},
            regex=True,
            outs=("r", "n", "p"),
        )
        assert tuple(run.function("a1b2", 0)) == ("a[1]b[2]", 2, 8)
        assert tuple(run.function("a1b2c", 2)) == ("a1b[2]c", 1, 6)
        assert tuple(run.function("abc", 0)) == ("abc", 0, -1)

    def test_constant_pattern_with_a_construct_python_cannot_read_is_a_gap(
        self,
    ) -> None:
        gaps: list[Gap] = []
        run = build({"search": "a\\h", "repl": "x"}, regex=True, gaps=gaps)
        (gap,) = gaps
        assert "the escape \\h" in gap.reason
        with pytest.raises(NotImplementedError):
            run.function("a")

    def test_replacement_naming_a_missing_submatch_is_a_gap(self) -> None:
        gaps: list[Gap] = []
        build({"search": "(a)", "repl": "$2"}, regex=True, gaps=gaps)
        (gap,) = gaps
        assert "invalid group reference 2" in gap.reason


def test_unwired_outputs_make_no_code() -> None:
    run = build({"search": "a"}, outs=())
    assert "re." not in body(run) and "replace" not in body(run)


def _bd_xml(flags: str) -> str:
    def term(uid: int, index: int, output: bool = False) -> str:
        mark = "<objFlags>1</objFlags>" if output else ""
        return (
            f'<SL__arrayElement class="term" uid="{uid}">{mark}'
            f'<dco class="parm" uid="{uid + 1}">{mark}'
            f"<parmIndex>{index}</parmIndex></dco></SL__arrayElement>"
        )

    terms = "".join(
        [
            term(11, 0),
            term(13, 1),
            term(15, 2),
            term(17, 3),
            term(19, 4, True),
            term(21, 5),
            term(23, 7),
            term(25, 8, True),
            term(27, 9),
            term(29, 10, True),
        ]
    )
    return (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<SL__rootObject class="oHExt" uid="1"><root class="diag" uid="2">'
        '<zPlaneList elements="0" /><nodeList elements="1">'
        '<SL__arrayElement class="prim" uid="10"><primResID>3914</primResID>'
        f'{flags}<termList elements="10">{terms}</termList></SL__arrayElement>'
        '</nodeList><signalList elements="0" /></root></SL__rootObject>'
    )


@pytest.mark.parametrize(
    ("flags", "expected"),
    [("", 0), ("<objFlags>33554432</objFlags>", 33554432)],
)
def test_node_records_its_flags(tmp_path: Path, flags: str, expected: int) -> None:
    bd_xml = tmp_path / "SR_BDHb.xml"
    bd_xml.write_text(_bd_xml(flags))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    name = graph.list_vis()[0]
    (sar,) = [
        n for n in graph.top_level_nodes(name) if getattr(n, "prim_id", 0) == 3914
    ]
    assert sar.obj_flags == expected  # type: ignore[attr-defined]


def test_describe_and_lvnet_show_the_node_terminals(tmp_path: Path) -> None:
    bd_xml = tmp_path / "SR_BDHb.xml"
    bd_xml.write_text(_bd_xml("<objFlags>33554432</objFlags>"))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    name = graph.list_vis()[0]
    assert describe_vi(graph, name).endswith(
        "## Operations\nSearch and Replace String [prim 3914]"
    )
    assert render_lvnet(build_netlist_from_graph(graph, name)).endswith(
        "    function Search_and_Replace_String_10 : Search and Replace String\n"
        "      in   input string            : ? default\n"
        "      in   replace all?            : ? default\n"
        "      in   ignore case?            : ? default\n"
        "      in   multiline?              : ? default\n"
        "      in   search string           : ? default\n"
        "      in   replace string          : ? default\n"
        "      in   offset                  : ? default\n"
        "      out  result string           : ?\n"
        "      out  number of replacements  : ?\n"
        "      out  offset past replacement : ?"
    )
