"""The defaults NI's pages state for inputs of the catalog (#169): each one listed
here is recorded on its catalog terminal, in the one place a reviewer reads them.

The primitive rows are shown in lvnet and used by the generated code. A vi.lib
terminal that has no pane index yet records its default but shows nothing until
the index is known (``unwired_default._catalog_terminal`` places a terminal by index).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.primitive_resolver import get_resolver as get_primitive_resolver
from lvkit.terminal_default import DefaultBehavior
from lvkit.vilib_resolver import get_resolver as get_vilib_resolver
from tests.native_vi import generate, node, scalar
from tests.structure_diagrams import (
    BOOL,
    I32,
    STR,
    Views,
    load,
    primitive,
    root_diagram,
)

# (primResID, input, documented default)
PRIMITIVE_DEFAULTS = [
    ("8053", "overwrite", False),
    ("8053", "prompt", "Choose or enter target path for copy"),
    ("8055", "prompt", "Create Folder"),
    ("8080", "prompt", "Choose or enter file path"),
    ("1538", "offset", 0),
    ("1510", "string", ""),
    ("24202", "default null elements? (F)", False),
    ("24202", "enable LabVIEW extensions? (T)", True),
    ("24202", "strict validation? (F)", False),
    ("8073", "offset", 0),
    ("1713", "abort (F)", False),
    ("8100", "utc_format", False),
    ("8101", "allow empty tokens? (F)", False),
    ("8101", "use cached delim/oper data? (F)", False),
    ("2308", "access mode", 0),
    ("2308", "duplicate session (F)", False),
    ("2308", "timeout (0)", 0),
    ("1187", "offset", 0),
    ("1714", "timeout ms (25000)", 25000),
    ("2302", "mask (16)", 16),
    ("8068", "overwrite", False),
    ("8068", "prompt", "Choose or enter target path for move"),
    ("9110", "return_elements", False),
    ("8058", "default_name", ""),
    ("8058", "pattern_label", "Custom Pattern"),
    ("8078", "prepend_size", True),
    ("9006", "enabled", DefaultBehavior("unchanged")),
    ("1512", 'string ("")', ""),
    ("1715", "local port", 0),
    ("24201", "enable LabVIEW extensions? (T)", True),
    ("9108", "name", ""),
    ("8050", "access", 0),
    ("8050", "operation", 0),
    ("8014", "application refnum", DefaultBehavior("application of the calling VI")),
    ("8014", "options", 0),
    (
        "8014",
        "type specifier VI Refnum (for type only)",
        DefaultBehavior("generic VI reference"),
    ),
    ("8014", "vi type (standard vi)", 1),
    ("9105", "ignore_previous", False),
    ("1341", "f_button_name", "Cancel"),
    ("1341", "t_button_name", "OK"),
    ("8012", 'machine name ("":  open local reference)', ""),
    ("8012", "port number or service name (3363)", 3363),
    ("8012", "timeout ms (60000)", 60000),
    ("1086", "ms timeout (10000)", 10000),
    ("1086", "wait for updated value (T)", True),
    ("2401", "?(T)", True),
    ("1716", "mode (standard)", 0),
    ("1716", "timeout ms (25000)", 25000),
    ("1701", "ignore_previous", True),
]

# (VI name, input, documented default)
VILIB_DEFAULTS = [
    ("Write PNG File", "compression (default)", -1),
    ("Three Button Dialog", "Allow user to close window? (TRUE)", True),
    ("Three Button Dialog", 'Center Button Text ("No")', "No"),
    ("Three Button Dialog", 'Left Button Text ("Yes")', "Yes"),
    ("Three Button Dialog", 'Right Button Text ("Cancel")', "Cancel"),
    ("Three Button Dialog", 'Window Title ("")', ""),
    ("Set Busy", "disable click? (T)", True),
    ("Get Text Rect", "desired font(Application)", 1),
    ("Get Text Rect", "text orientation", 0),
    ("Draw Rectangle", "Style", 0),
    ("Draw Rectangle", "fill?(F)", False),
    ("Open Registry Key", "registry view", 0),
    ("Search and Replace Pattern", "offset (0)", 0),
    ("Search and Replace Pattern", 'regular expression ("")', ""),
    ("Search and Replace Pattern", "replace all? (F)", False),
    ("Search and Replace Pattern", 'replace string ("")', ""),
    ("Get LV Class Default Value", "options", 0),
    ("Generate Temporary File Path", "Temporary File Extension (tmp)", "tmp"),
    ("Write to System Log", "severity (Error)", 0),
    ("Picture to Pixmap", "depth", 24),
    ("General Error Handler", "[error code] (0)", 0),
    ("General Error Handler", '[error source] (" ")', " "),
    ("General Error Handler", "[exception action] (none:0)", 0),
    ("General Error Handler", "[exception code]", 0),
    ("General Error Handler", "[exception source]", ""),
    ("General Error Handler", "search ni.com link visible?(T)", True),
    ("General Error Handler", "type of dialog (OK msg:1)", 1),
    ("Draw Text at Point", "desired font(Application)", 1),
    ("Draw Text at Point", "text orientation", 0),
    ("New Zip File", "confirm overwrite? (F)", False),
    ("Write JPEG File", "quality (80)", 80),
    ("Draw Line", "Style", 0),
    ("Draw Line", "absolute coordinates?(T)", True),
    ("Move Pen", "absolute coordinates?(T)", True),
    ("Create Mask", "1-Bit Mask Value (T)", True),
    ("Create Mask", "Combine Masks? (F)", False),
    ("Recursive File List", "Continue Recursing on Error? (F)", False),
    ("Recursive File List", "Treat LLBs as Folders?", False),
    ("Traverse for GObjects", "Traverse Generated Code (F)", False),
    ("Enum Registry Values Simple", "retrieve data? (F)", False),
    ("System Exec", "expected output size (4096)", 4096),
    ("System Exec", "run minimized? (F)", False),
    ("System Exec", "wait until completion? (T)", True),
    ("Draw Oval", "fill?(F)", False),
    ("Open Config Data", "create file if necessary (T)", True),
    ("Close Config Data", "write file if changed (T)", True),
    ("Read JPEG File", "use 8 bit color table", False),
]

# NI states a behaviour or a context-dependent value for these (not stored):
# they stay the bare `default`.
STAYS_BARE = [
    ("8070", "count"),  # one line or the whole file, by a menu setting
    ("8073", "from"),  # depends on whether `offset` is wired
    ("8051", "count"),  # NI describes a single element
    ("2135", "include static events? (T)"),  # depends on the event case
    ("8058", "start_path"),  # the last directory viewed
    ("8058", "prompt"),  # the dialog title differs between Windows/Linux and macOS
    ("8051", "byte_order"),  # a String on the calls in the corpus, not an item value
    ("8078", "byte_order"),
]


def _terminal(entry, name: str):
    return next(t for t in entry.terminals if t.name == name)


@pytest.mark.parametrize(("prim_id", "name", "expected"), PRIMITIVE_DEFAULTS)
def test_a_primitive_input_records_the_documented_default(
    prim_id: str, name: str, expected: object
) -> None:
    entry = get_primitive_resolver().resolve(prim_id=prim_id)
    assert entry is not None
    terminal = _terminal(entry, name)
    if isinstance(expected, DefaultBehavior):
        assert terminal.default_behavior == expected
        assert terminal.default is None
    else:
        assert terminal.default == expected
        assert type(terminal.default) is type(expected)
        assert terminal.default_behavior is None
    assert entry.doc_url


@pytest.mark.parametrize(("vi_name", "name", "expected"), VILIB_DEFAULTS)
def test_a_vilib_input_records_the_documented_default(
    vi_name: str, name: str, expected: object
) -> None:
    entry = get_vilib_resolver().resolve_by_name(f"{vi_name}.vi")
    assert entry is not None
    terminal = _terminal(entry, name)
    assert terminal.default == expected
    assert type(terminal.default) is type(expected)
    assert entry.doc_url


@pytest.mark.parametrize(("prim_id", "name"), STAYS_BARE)
def test_a_behaviour_or_context_dependent_default_is_not_stored(
    prim_id: str, name: str
) -> None:
    entry = get_primitive_resolver().resolve(prim_id=prim_id)
    assert entry is not None
    terminal = _terminal(entry, name)
    assert terminal.default is None
    assert terminal.default_behavior is None


def _views(tmp_path: Path, node_xml: str) -> Views:
    return load(tmp_path, root_diagram([], [node_xml], []))


def test_move_shows_overwrite_and_prompt(tmp_path: Path) -> None:
    views = _views(tmp_path, primitive(10, 8068, [(9, BOOL), (7, STR)], out_type=BOOL))
    assert (
        '      in   prompt    : String default "Choose or enter target path for move"'
        in views.lvnet
    )
    assert "      in   overwrite : TF     default False\n" in views.lvnet


def test_obtain_queue_shows_the_unnamed_queue(tmp_path: Path) -> None:
    views = _views(tmp_path, primitive(10, 9108, [(0, STR)], out_type=I32))
    assert '      in   0 : String default ""\n' in views.lvnet


def test_search_split_shows_offset_zero(tmp_path: Path) -> None:
    views = _views(tmp_path, primitive(10, 1538, [(3, I32)], out_type=I32))
    assert "      in   offset          : I32 default 0\n" in views.lvnet


I32_T = scalar("NumInt32")
STRING_T = scalar("String")


def test_search_split_with_offset_unwired_starts_at_zero() -> None:
    search = node(
        "ss",
        1538,
        [
            (0, "output", I32_T),
            (3, "input", I32_T),
            (4, "input", STRING_T),
            (5, "input", STRING_T),
        ],
    )
    gaps: list = []
    generated = generate(
        {"needle": STRING_T, "text": STRING_T},
        {"where": I32_T},
        [search],
        [("fp:needle", "ss:4"), ("fp:text", "ss:5"), ("ss:0", "fp:where")],
        gaps=gaps,
    )
    assert gaps == []
    assert generated.function("b", "abcb").where == 1  # type: ignore[operator]


def test_pick_line_with_string_unwired_appends_nothing() -> None:
    pick = node(
        "pl",
        1510,
        [
            (0, "output", STRING_T),
            (1, "input", I32_T),
            (2, "input", STRING_T),
            (3, "input", None),
        ],
    )
    gaps: list = []
    generated = generate(
        {"index": I32_T, "text": STRING_T},
        {"line": STRING_T},
        [pick],
        [("fp:index", "pl:1"), ("fp:text", "pl:2"), ("pl:0", "fp:line")],
        gaps=gaps,
    )
    assert gaps == []
    assert generated.function(1, "a\nb\nc").line == "b"  # type: ignore[operator]
