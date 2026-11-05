"""An XNode is shown by its library (the ``.xnode`` file its ``XNXI`` link record
names: ``Match Regular Expression``) with the terminal names its xTunnels record
(``<id>``), in describe, lvnet (terse and verbose) and JSON. The Error Ring's
views are checked in ``test_error_ring.py``."""

from __future__ import annotations

from tests.error_diagrams import (
    REQUIRED,
    STR,
    Project,
    Vi,
    json_body,
    project,  # noqa: F401  (the fixture)
)

NAME = "Regex.vi"
MATCH_REGULAR_EXPRESSION = ["<vilib>", "regexp", "Match Regular Expression.xnode"]


def test_a_match_regular_expression_xnode_is_named_by_its_library(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    text = vi.control("text", STR, REQUIRED)
    whole = vi.indicator("whole", STR)
    node = vi.xnode(
        MATCH_REGULAR_EXPRESSION,
        [
            ("input string", STR, False),
            ("regular expression", STR, False),
            ("whole match", STR, True),
        ],
    )
    vi.wire(text, node["input string"])
    vi.wire(vi.constant(STR, "000000015E"), node["regular expression"])
    vi.wire(node["whole match"], whole)
    project.add(vi)
    views = project.views(NAME)
    block = (
        "    function Match_Regular_Expression_112 : Match Regular Expression\n"
        "      in   input string       : String = text\n"
        "      in   regular expression : String = \"'^'\"\n"
        "      out  whole match        : String\n"
        "    whole = Match_Regular_Expression_112::whole match"
    )
    assert views.diagram == block
    assert views.terse == (
        "vi Regex.vi :\n"
        "  front-panel :\n"
        "    pattern : 4800\n"
        "    in   text  : String @0\n"
        "    out  whole : String @1\n"
        "  block-diagram :\n" + block
    )
    assert views.describe_body() == (
        "\n  regex(text: String) -> whole: String\n\n"
        "## Properties\n  lock_state: unlocked\n\n"
        "## Inputs\n  text: String (required)\n\n"
        "## Outputs\n  whole: String\n\n"
        "## Constants\n  (unnamed): String = '^'\n\n"
        "## Operations\nMatch Regular Expression"
    )
    assert json_body(views) == (
        '[{"inputs":[{"net":{"bare":"text","terminal":"text"},'
        '"terminal":"input string"},{"net":{"bare":"\'^\'","terminal":"\'^\'"},'
        '"terminal":"regular expression"}],"kind":"instance",'
        '"name":"Match Regular Expression","outputs":[{"bare":"whole match",'
        '"node":"Match Regular Expression","terminal":"whole match"}],'
        '"qualified_name":"Match Regular Expression","uid":"112"}]'
    )


def test_an_xnode_of_another_library_is_not_named_by_its_ids(
    project: Project,  # noqa: F811
) -> None:
    """Only the libraries checked against the corpus (Error Ring, Match Regular
    Expression) name a terminal by its xTunnel <id>; any other XNode, an FPGA
    Interface node among them, keeps #107's names (here, none: the index)."""
    vi = Vi(NAME)
    text = vi.control("text", STR, REQUIRED)
    node = vi.xnode(
        ["<vilib>", "FPGA", "Invoke Method.xnode"],
        [("input string", STR, False)],
    )
    vi.wire(text, node["input string"])
    project.add(vi)
    views = project.views(NAME)
    assert "input string" not in views.diagram
    assert "function Invoke_Method_" in views.diagram
