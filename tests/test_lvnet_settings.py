"""The ``(<settings>)`` group of a node or structure header (lvnet spec §3).

One writer and one reader serve every header kind: each setting of each kind
round-trips (defaults omitted, restored by the reader), a name that would end in a
settings-shaped group is quoted, a bad group is an error naming the allowed settings,
and the JSON and diff views show the same settings.
"""

from __future__ import annotations

import pytest

from lvkit.graph.lvnet_grammar import LVNET_SETTINGS_BY_KEYWORD
from lvkit.graph.lvnet_parse import LvnetParseError, parse_lvnet
from lvkit.graph.lvnet_settings import (
    ends_in_settings_group,
    lvnet_settings_group,
    read_header_settings,
    settings_absent,
    settings_changes,
    settings_to_json,
)
from lvkit.graph.netlist import (
    DefaultValue,
    NetlistFeedback,
    NetlistFrame,
    NetlistInstance,
    NetlistInstanceKind,
    NetlistOutput,
    NetlistScope,
    NetlistTerminalBinding,
    NetRef,
    netlist_to_dict,
    render_lvnet,
)
from lvkit.graph.netlist_diff_helpers import instance_line
from lvkit.graph.netlist_models import (
    CaseSettings,
    CompoundMode,
    FeedbackSettings,
    ForLoopSettings,
    FunctionSettings,
    LoopCondition,
    MuMerge,
    NodeSettings,
    SharedVariableSettings,
    ShiftRegisterSettings,
    WhileLoopSettings,
)

from .test_lvnet_identity import _assert_graph_identity
from .test_lvnet_structure_syntax import (
    _BOOLEAN,
    _control_ref,
    _frames,
    _module,
    _round_trip,
)

# ---------------------------------------------------------------------------
# The writer and the reader, one kind at a time
# ---------------------------------------------------------------------------

_SETTING_CASES: list[tuple[str, NodeSettings, str]] = [
    ("function", FunctionSettings(mode=CompoundMode.ADD), " (mode=add)"),
    ("function", FunctionSettings(mode=CompoundMode.MULTIPLY), " (mode=multiply)"),
    ("function", FunctionSettings(mode=CompoundMode.AND), " (mode=and)"),
    ("function", FunctionSettings(mode=CompoundMode.OR), " (mode=or)"),
    ("function", FunctionSettings(mode=CompoundMode.XOR), " (mode=xor)"),
    ("function", FunctionSettings(mode=None), " (mode=?)"),
    (
        "while-loop",
        WhileLoopSettings(stop=LoopCondition.STOP_IF_TRUE),
        " (stop=if-true)",
    ),
    (
        "while-loop",
        WhileLoopSettings(stop=LoopCondition.CONTINUE_IF_TRUE),
        " (stop=if-false)",
    ),
    ("for-loop", ForLoopSettings(), ""),
    ("for-loop", ForLoopSettings(stop=LoopCondition.STOP_IF_TRUE), " (stop=if-true)"),
    ("for-loop", ForLoopSettings(parallel=True), " (parallel=True)"),
    (
        "for-loop",
        ForLoopSettings(parallel_instances=4),
        " (parallel-instances=4)",
    ),
    (
        "for-loop",
        ForLoopSettings(
            stop=LoopCondition.CONTINUE_IF_TRUE, parallel=True, parallel_instances=4
        ),
        " (stop=if-false, parallel=True, parallel-instances=4)",
    ),
    ("case", CaseSettings(), ""),
    ("case", CaseSettings(case_insensitive=True), " (case-insensitive=True)"),
    ("feedback-node", FeedbackSettings(delay=1), " (delay=1)"),
    ("feedback-node", FeedbackSettings(delay=3), " (delay=3)"),
    ("feedback-node", FeedbackSettings(delay=None), " (delay=?)"),
    (
        "feedback-node",
        FeedbackSettings(delay=1, initialized=False),
        " (delay=1, initialized=False)",
    ),
    ("shift-register", ShiftRegisterSettings(), ""),
    (
        "shift-register",
        ShiftRegisterSettings(initialized=False),
        " (initialized=False)",
    ),
    ("shared-variable", SharedVariableSettings(), ""),
    (
        "shared-variable",
        SharedVariableSettings(path="\\\\host\\SV Sample\\Value"),
        ' (path="\\\\\\\\host\\\\SV Sample\\\\Value")',
    ),
]


def test_every_header_kind_with_settings_has_its_class_in_the_table() -> None:
    assert sorted(LVNET_SETTINGS_BY_KEYWORD) == sorted(
        {keyword for keyword, _, _ in _SETTING_CASES}
    )
    for keyword, settings, _ in _SETTING_CASES:
        assert type(settings) is LVNET_SETTINGS_BY_KEYWORD[keyword]


@pytest.mark.parametrize(("keyword", "settings", "group"), _SETTING_CASES)
def test_a_setting_is_written_and_read_back(
    keyword: str, settings: NodeSettings, group: str
) -> None:
    assert lvnet_settings_group(settings) == group
    head, read = read_header_settings(keyword, f"Name{group}", "header")
    assert head == "Name"
    if group:
        assert read == settings
    else:
        assert read is None
        assert settings_absent(type(settings)) == settings


def test_a_kind_with_a_setting_that_has_no_default_has_no_absent_settings() -> None:
    assert settings_absent(FunctionSettings) is None
    assert settings_absent(WhileLoopSettings) is None
    assert settings_absent(FeedbackSettings) is None


@pytest.mark.parametrize(
    ("keyword", "group", "message"),
    [
        ("function", "(speed=3)", "unknown setting 'speed'"),
        ("function", "(mode=or, mode=and)", "'mode' given twice"),
        ("function", "(mode=sometimes)", "'mode' cannot be 'sometimes'"),
        ("while-loop", "(stop=True)", "'stop' cannot be 'True'"),
        ("for-loop", "(parallel=yes)", "'parallel' cannot be 'yes'"),
        ("for-loop", "(parallel-instances=many)", "cannot be 'many'"),
        ("for-loop", "(parallel-instances=?)", "cannot be '?'"),
        ("feedback-node", "(delay=1, initialized=1)", "'initialized' cannot be '1'"),
        ("feedback-node", "(initialized=False)", "a required setting is missing"),
        ("shared-variable", "(path=x)", "'path' cannot be 'x'"),
        ("case", "(case-insensitive=maybe)", "cannot be 'maybe'"),
    ],
)
def test_a_bad_settings_group_is_an_error_naming_the_allowed_settings(
    keyword: str, group: str, message: str
) -> None:
    with pytest.raises(LvnetParseError, match=message) as error:
        read_header_settings(keyword, f"Name {group}", "line 3: 'Name'")
    allowed = [
        field
        for field in (
            "mode",
            "stop",
            "parallel",
            "parallel-instances",
            "case-insensitive",
            "delay",
            "initialized",
            "path",
        )
        if field in str(error.value).split("allows: ")[-1].split(", ")
    ]
    assert allowed, str(error.value)


def test_the_error_lists_exactly_the_settings_of_the_kind() -> None:
    with pytest.raises(LvnetParseError) as error:
        read_header_settings("for-loop", "for-loop (nope=1)", "line 1")
    assert str(error.value).endswith(
        "this header allows: stop, parallel, parallel-instances"
    )


# ---------------------------------------------------------------------------
# A trailing group is the settings slot by SHAPE
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "Tick Count (ms)",
        "Wait (ms)",
        "Random Number (0-1)",
        "TDMS Read (Data Ref)",
        "error in (no error)",
        "Value (x = 2)",
        "Empty ()",
        "Mixed (a=b, Mixed)",
    ],
)
def test_a_name_ending_in_an_ordinary_group_stays_bare(name: str) -> None:
    assert not ends_in_settings_group(name)
    assert read_header_settings("function", name, "header") == (name, None)


@pytest.mark.parametrize(
    "name", ["Foo (a=b)", "Foo (a=b, c-d=1)", 'Foo (path="a) (b")', "Foo (x=)"]
)
def test_a_name_ending_in_a_settings_shaped_group_would_be_read_as_settings(
    name: str,
) -> None:
    assert ends_in_settings_group(name)


def test_a_settings_group_after_a_quoted_name_is_found_outside_the_quotes() -> None:
    head, settings = read_header_settings("function", '"Foo (a=b)" (mode=or)', "header")
    assert head == '"Foo (a=b)"'
    assert settings == FunctionSettings(mode=CompoundMode.OR)


def test_a_path_with_parentheses_in_its_quotes_is_one_value() -> None:
    settings = SharedVariableSettings(path="a) (b=c")
    group = lvnet_settings_group(settings)
    assert read_header_settings("shared-variable", f"V{group}", "header") == (
        "V",
        settings,
    )


# ---------------------------------------------------------------------------
# Whole documents: render -> parse -> reconstruct -> render
# ---------------------------------------------------------------------------


def _function(name: str, settings: FunctionSettings | None, uid: str = "12"):
    ref = NetRef(
        node=name, terminal="0", occurrence=None, bare=f"{name}.0", producer_uid=uid
    )
    return NetlistInstance(
        uid=uid,
        name=name,
        occurrence=None,
        inputs=[
            NetlistTerminalBinding(
                terminal="1", type="TF", net=_control_ref("a"), lv_type=_BOOLEAN
            )
        ],
        outputs=[NetlistOutput(net=ref, type="TF", lv_type=_BOOLEAN)],
        kind=NetlistInstanceKind.FUNCTION,
        settings=settings,
    )


@pytest.mark.parametrize(
    "mode", [*CompoundMode, None], ids=lambda m: "unread" if m is None else m.value
)
def test_compound_arithmetic_mode_round_trips(mode: CompoundMode | None) -> None:
    module = _module(["a"], [_function("Compound Arithmetic", FunctionSettings(mode))])
    text, rebuilt = _round_trip(module)
    shown = "?" if mode is None else mode.value
    assert f": Compound Arithmetic (mode={shown})\n" in text
    instance = rebuilt.body[0]
    assert isinstance(instance, NetlistInstance)
    assert instance.settings == FunctionSettings(mode)
    _assert_graph_identity(module, rebuilt, "synthetic")


def test_a_function_without_settings_has_no_group() -> None:
    module = _module(["a"], [_function("Not", None)])
    text, rebuilt = _round_trip(module)
    assert ": Not\n" in text
    instance = rebuilt.body[0]
    assert isinstance(instance, NetlistInstance) and instance.settings is None


def test_a_function_named_with_an_ordinary_group_stays_bare_and_round_trips() -> None:
    module = _module(["a"], [_function("Tick Count (ms)", None)])
    text, rebuilt = _round_trip(module)
    assert ": Tick Count (ms)\n" in text
    _assert_graph_identity(module, rebuilt, "synthetic")


def test_a_function_named_with_a_settings_shaped_group_is_quoted() -> None:
    module = _module(
        ["a"],
        [
            _function("Foo (a=b)", None, "12"),
            _function("Bar (a=b)", FunctionSettings(CompoundMode.OR), "13"),
        ],
    )
    text, rebuilt = _round_trip(module)
    assert ': "Foo (a=b)"\n' in text
    assert ': "Bar (a=b)" (mode=or)\n' in text
    parsed = parse_lvnet(text)
    assert [getattr(item, "component") for item in parsed.body] == [
        '"Foo (a=b)"',
        '"Bar (a=b)"',
    ]
    assert [
        item.settings for item in rebuilt.body if isinstance(item, NetlistInstance)
    ] == [
        None,
        FunctionSettings(CompoundMode.OR),
    ]


def _loop(uid: str, kind: str, settings, outputs=None, count=None, selector=None):
    return NetlistScope(
        uid=uid,
        kind=kind,
        selector=selector,
        frames=[NetlistFrame(label="", value="", is_default=False, body=[])],
        outputs=outputs or [],
        settings=settings,
        count=count,
    )


@pytest.mark.parametrize(
    "stop", [LoopCondition.STOP_IF_TRUE, LoopCondition.CONTINUE_IF_TRUE]
)
def test_while_loop_stop_round_trips(stop: LoopCondition) -> None:
    loop = _loop("43", "while", WhileLoopSettings(stop), selector=_control_ref("s"))
    module = _module(["s"], [loop])
    text, rebuilt = _round_trip(module)
    assert f"while-loop (stop={stop.value}) (id 43) :" in text
    assert rebuilt.body[0].settings == WhileLoopSettings(stop)  # type: ignore[union-attr]
    _assert_graph_identity(module, rebuilt, "synthetic")


@pytest.mark.parametrize(
    ("settings", "header"),
    [
        (ForLoopSettings(), "for-loop (id 7) :"),
        (ForLoopSettings(parallel=True), "for-loop (parallel=True) (id 7) :"),
        (
            ForLoopSettings(parallel=True, parallel_instances=4),
            "for-loop (parallel=True, parallel-instances=4) (id 7) :",
        ),
        (
            ForLoopSettings(parallel_instances=8),
            "for-loop (parallel-instances=8) (id 7) :",
        ),
    ],
)
def test_for_loop_parallel_options_round_trip(
    settings: ForLoopSettings, header: str
) -> None:
    loop = _loop("7", "for", settings, count=_control_ref("n"))
    module = _module(["n"], [loop])
    text, rebuilt = _round_trip(module)
    assert header in text
    assert rebuilt.body[0].settings == settings  # type: ignore[union-attr]
    _assert_graph_identity(module, rebuilt, "synthetic")


def test_a_terse_for_loop_keeps_its_settings_without_an_id() -> None:
    loop = _loop(
        "7",
        "for",
        ForLoopSettings(parallel=True, parallel_instances=4),
        count=_control_ref("n"),
    )
    text = render_lvnet(_module(["n"], [loop]))
    assert "for-loop (parallel=True, parallel-instances=4) :" in text


@pytest.mark.parametrize("insensitive", [False, True])
def test_case_insensitive_round_trips(insensitive: bool) -> None:
    frames = _frames("A", "B")
    for frame in frames:
        frame.values = (frame.label,)
    scope = NetlistScope(
        uid="5",
        kind="case",
        settings=CaseSettings(case_insensitive=insensitive),
        selector=_control_ref("sel"),
        frames=frames,
    )
    module = _module(["sel"], [scope])
    text, rebuilt = _round_trip(module)
    header = (
        "case sel (case-insensitive=True) (id 5) :"
        if insensitive
        else ("case sel (id 5) :")
    )
    assert header in text
    assert rebuilt.body[0].settings == CaseSettings(insensitive)  # type: ignore[union-attr]
    _assert_graph_identity(module, rebuilt, "synthetic")


@pytest.mark.parametrize(
    ("delay", "initialized", "header"),
    [
        (1, True, "feedback-node fb0 (delay=1) :"),
        (3, True, "feedback-node fb0 (delay=3) :"),
        (None, True, "feedback-node fb0 (delay=?) :"),
        (1, False, "feedback-node fb0 (delay=1, initialized=False) :"),
        (None, False, "feedback-node fb0 (delay=?, initialized=False) :"),
    ],
)
def test_feedback_node_settings_round_trip(
    delay: int | None, initialized: bool, header: str
) -> None:
    feedback = NetlistFeedback(
        uid="12",
        net="fb0",
        init=_control_ref("seed") if initialized else DefaultValue("0.0", "DBL"),
        recur=_control_ref("x"),
        settings=FeedbackSettings(delay=delay, initialized=initialized),
    )
    module = _module(["seed", "x"], [feedback])
    text, rebuilt = _round_trip(module)
    assert header in text
    assert ("init = seed" in text) is initialized
    assert rebuilt.body[0].settings == FeedbackSettings(delay, initialized)  # type: ignore[union-attr]
    _assert_graph_identity(module, rebuilt, "synthetic")


def test_a_feedback_node_whose_initializer_disagrees_with_its_setting_is_refused() -> (
    None
):
    with pytest.raises(ValueError, match="initialized exactly when"):
        NetlistFeedback(
            uid="12",
            net="fb0",
            init=DefaultValue("0.0", "DBL"),
            recur=None,
            settings=FeedbackSettings(delay=1, initialized=True),
        )


@pytest.mark.parametrize("initialized", [True, False])
def test_shift_register_initialized_round_trips(initialized: bool) -> None:
    init = _control_ref("seed") if initialized else DefaultValue("0", "TF")
    register = MuMerge(net="loop_5.shift0", init=init, recur=_control_ref("x"))
    loop = _loop(
        "5",
        "while",
        WhileLoopSettings(LoopCondition.STOP_IF_TRUE),
        outputs=[register],
    )
    module = _module(["seed", "x"], [loop])
    text, rebuilt = _round_trip(module)
    header = "shift-register loop_5::shift0" + (
        "" if initialized else " (initialized=False)"
    )
    assert f"{header} :" in text
    scope = rebuilt.body[0]
    assert isinstance(scope, NetlistScope)
    merge = scope.outputs[0]
    assert isinstance(merge, MuMerge)
    assert merge.settings == ShiftRegisterSettings(initialized)


def _shared_variable(settings: SharedVariableSettings, unreadable: str | None = None):
    ref = NetRef(
        node="Value", terminal="Variable", occurrence=None, bare="v", producer_uid="9"
    )
    return NetlistInstance(
        uid="9",
        name="Value",
        occurrence=None,
        inputs=[],
        outputs=[NetlistOutput(net=ref, type="TF", lv_type=_BOOLEAN)],
        kind=NetlistInstanceKind.SHARED_VARIABLE,
        library=None if unreadable else "SV.lvlib",
        access="read",
        library_unreadable=unreadable,
        settings=settings,
    )


@pytest.mark.parametrize(
    ("settings", "unreadable", "tail"),
    [
        (SharedVariableSettings(), None, "SV.lvlib::Value : read\n"),
        (
            SharedVariableSettings(path="\\\\host\\SV\\Value"),
            None,
            'SV.lvlib::Value : read (path="\\\\\\\\host\\\\SV\\\\Value")\n',
        ),
        (
            SharedVariableSettings(),
            "bad record",
            'Value : read (unreadable "bad record")\n',
        ),
    ],
)
def test_shared_variable_path_and_unreadable_mark_round_trip(
    settings: SharedVariableSettings, unreadable: str | None, tail: str
) -> None:
    module = _module([], [_shared_variable(settings, unreadable)])
    text, rebuilt = _round_trip(module)
    assert text.count(tail) == 1
    instance = rebuilt.body[0]
    assert isinstance(instance, NetlistInstance)
    assert instance.settings == settings
    assert instance.library_unreadable == unreadable
    _assert_graph_identity(module, rebuilt, "synthetic")


# ---------------------------------------------------------------------------
# The views that are not text
# ---------------------------------------------------------------------------


def test_json_carries_every_setting_by_its_lvnet_name() -> None:
    feedback = NetlistFeedback(
        uid="12",
        net="fb0",
        init=_control_ref("seed"),
        recur=None,
        settings=FeedbackSettings(delay=2),
    )
    loop = _loop(
        "7", "for", ForLoopSettings(parallel=True, parallel_instances=4), count=None
    )
    module = _module(
        ["seed"],
        [
            _function("Compound Arithmetic", FunctionSettings(CompoundMode.XOR)),
            _function("Not", None, "13"),
            _shared_variable(SharedVariableSettings(path="p")),
            feedback,
            loop,
        ],
    )
    body = netlist_to_dict(module)["body"]
    assert [item["settings"] for item in body] == [
        {"mode": "xor"},
        {},
        {"path": "p"},
        {"delay": 2, "initialized": True},
        {"stop": None, "parallel": True, "parallel-instances": 4},
    ]
    assert settings_to_json(None) == {}
    assert settings_to_json(CaseSettings()) == {"case-insensitive": False}
    assert settings_to_json(FunctionSettings(None)) == {"mode": None}


def test_a_shift_register_in_json_carries_its_settings() -> None:
    register = MuMerge(
        net="loop_5.shift0", init=DefaultValue("0", "TF"), recur=_control_ref("x")
    )
    loop = _loop(
        "5", "while", WhileLoopSettings(LoopCondition.STOP_IF_TRUE), [register]
    )
    scope = netlist_to_dict(_module(["x"], [loop]))["body"][0]
    assert scope["settings"] == {"stop": "if-true"}
    assert scope["outputs"][0]["settings"] == {"initialized": False}


def test_the_diff_shows_the_settings_and_what_changed() -> None:
    before = FunctionSettings(CompoundMode.AND)
    after = FunctionSettings(CompoundMode.OR)
    line = instance_line(_function("Compound Arithmetic", after), set())
    assert line.startswith("Compound Arithmetic [mode: or](1=a)")
    assert settings_changes(before, after) == ["mode: and -> or"]
    assert settings_changes(after, after) == []
    assert settings_changes(
        ForLoopSettings(), ForLoopSettings(parallel=True, parallel_instances=4)
    ) == ["parallel: False -> True", "parallel-instances: ? -> 4"]
