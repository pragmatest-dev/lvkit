"""General Error Handler, generated and run (``errors-as-values.md``, decision 5:
the error it reports, no dialog). Each test builds a tiny VI around the call, in
the style of the Simple Error Handler tests of ``test_errors_as_values``."""

from __future__ import annotations

import pytest

from tests.error_construct_diagrams import NAME, VILIB_ERROR
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    RECOMMENDED,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

# The connector pane the call records (the pane of every corpus caller): the
# index, direction and type of each terminal.
_SLOTS = [
    ("[error code] (0)", 0, I32, False),
    ("[user-defined codes]", 1, "codes", False),
    ("[user-defined descriptions]", 2, "descriptions", False),
    ('[error source] (" ")', 5, STR, False),
    ("search ni.com link visible?(T)", 7, BOOL, False),
    ("type of dialog (OK msg:1)", 9, I32, False),
    ("error in (no error)", 11, ERR, False),
    ("[exception action] (none:0)", 12, I32, False),
    ("[exception code]", 13, I32, False),
    ("[exception source]", 14, STR, False),
    ("error?", 4, BOOL, True),
    ("code out", 6, I32, True),
    ("source out", 8, STR, True),
    ("message", 10, STR, True),
    ("error out", 15, ERR, True),
]


def _array_of(element: int) -> str:
    return (
        f'<TypeDesc Type="Array"><Dimension /><TypeDesc TypeID="{element}" />'
        "</TypeDesc>"
    )


def general_error_handler(
    project: Project,  # noqa: F811
    *,
    code: bool = False,
    user_defined: bool = False,
    action: int | str | None = None,
    exception_code: bool = False,
    exception_source: bool = False,
    dialog: int | None = None,
) -> str:
    """General Error Handler on the error in, its outputs on the indicators
    ``failed``, ``code``, ``source`` and ``message``, its error out the VI's.
    ``code`` wires the control ``error code`` (and ``error source``) to its error
    code; ``user_defined`` the arrays ``codes`` and ``descriptions``; ``action``
    a constant exception action, or the control ``action`` for ``"control"``;
    ``exception_code`` and ``exception_source`` the controls of those names."""
    vi = Vi(NAME)
    ein = vi.error_in()
    failed = vi.indicator("failed", BOOL)
    code_out = vi.indicator("code", I32)
    source_out = vi.indicator("source", STR)
    message = vi.indicator("message", STR)
    eout = vi.error_out()
    array = {
        "codes": vi.add_type(_array_of(I32)),
        "descriptions": vi.add_type(_array_of(STR)),
    }
    slots = [
        (label, index, array.get(type_id, type_id), is_output)
        for label, index, type_id, is_output in _SLOTS
    ]
    node = vi.call("General Error Handler.vi", slots, vilib=VILIB_ERROR)
    vi.wire(ein, node["error in (no error)"])
    if code:
        vi.wire(vi.control("error code", I32, RECOMMENDED), node["[error code] (0)"])
        vi.wire(
            vi.control("error source", STR, RECOMMENDED), node['[error source] (" ")']
        )
    if user_defined:
        vi.wire(
            vi.control("codes", array["codes"], RECOMMENDED),
            node["[user-defined codes]"],
        )
        vi.wire(
            vi.control("descriptions", array["descriptions"], RECOMMENDED),
            node["[user-defined descriptions]"],
        )
    if action == "control":
        vi.wire(
            vi.control("action", I32, REQUIRED), node["[exception action] (none:0)"]
        )
    elif action is not None:
        vi.wire(vi.int_constant(action), node["[exception action] (none:0)"])
    if exception_code:
        vi.wire(
            vi.control("exception code", I32, RECOMMENDED), node["[exception code]"]
        )
    if exception_source:
        vi.wire(
            vi.control("exception source", STR, RECOMMENDED),
            node["[exception source]"],
        )
    if dialog is not None:
        vi.wire(vi.int_constant(dialog), node["type of dialog (OK msg:1)"])
    vi.wire(node["error?"], failed)
    vi.wire(node["code out"], code_out)
    vi.wire(node["source out"], source_out)
    vi.wire(node["message"], message)
    vi.wire(node["error out"], eout)
    project.add(vi)
    return NAME


FIELDS = ("failed", "code", "source", "message", "error_out")


def test_it_reports_the_error_in_without_a_dialog(
    project: Project,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    package = project.generate(general_error_handler(project, dialog=2))
    capsys.readouterr()
    clean = package.function()
    assert [(f, getattr(clean, f)) for f in FIELDS] == [
        ("failed", False),
        ("code", 0),
        ("source", ""),
        ("message", ""),
        ("error_out", None),
    ]
    error = package.error(7, source="step")
    result = package.function(error_in=error)
    assert (result.failed, result.code, result.source) == (True, 7, "step")
    assert result.error_out is error
    assert capsys.readouterr() == ("", "")
    assert "live_io" not in package.source
    assert "NotImplementedError" not in package.source


def test_it_makes_an_error_of_its_error_code_when_none_came_in(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, code=True))
    found = package.function(error_code=7, error_source="my step")
    assert (found.failed, found.code, found.source) == (True, 7, "my step")
    assert (found.error_out.status, found.error_out.code) == (True, 7)
    incoming = package.error(9)
    result = package.function(error_in=incoming, error_code=7, error_source="x")
    assert (result.failed, result.code, result.error_out) == (True, 9, incoming)


def test_a_warning_on_error_in_with_code_0_flows_on(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, code=True))
    warning = package.error(5, status=False)
    result = package.function(error_in=warning, error_code=0)
    assert (result.failed, result.code, result.error_out) == (False, 5, warning)


def test_an_unwired_error_code_writes_no_code_test(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project))
    assert "!= 0" not in package.source
    assert "LabVIEWError(" not in package.source


def test_a_user_defined_code_gives_its_description_as_the_message(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, user_defined=True))
    codes, descriptions = [5001, 5002], ["first", "second"]
    result = package.function(
        error_in=package.error(5002), codes=codes, descriptions=descriptions
    )
    assert (result.failed, result.code, result.message) == (True, 5002, "second")
    other = package.function(
        error_in=package.error(5003), codes=codes, descriptions=descriptions
    )
    assert other.message == ""
    short = package.function(
        error_in=package.error(5002), codes=codes, descriptions=descriptions[:1]
    )
    assert short.message == ""
    assert package.function(codes=codes, descriptions=descriptions).message == ""


def test_an_error_with_its_own_message_keeps_it_over_a_user_description(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, user_defined=True))
    error = package.error(5001)
    error.message = "internal"
    result = package.function(error_in=error, codes=[5001], descriptions=["mine"])
    assert result.message == "internal"


def test_a_user_defined_code_describes_an_error_made_of_the_error_code(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(
        general_error_handler(project, code=True, user_defined=True)
    )
    result = package.function(
        error_code=5002, error_source="s", codes=[5002], descriptions=["mine"]
    )
    assert (result.failed, result.code, result.message) == (True, 5002, "mine")


def test_cancel_error_on_match_treats_the_error_as_no_error(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(
        general_error_handler(project, action=1, exception_code=True)
    )
    result = package.function(error_in=package.error(7), exception_code=7)
    assert [(f, getattr(result, f)) for f in FIELDS] == [
        ("failed", False),
        ("code", 0),
        ("source", ""),
        ("message", ""),
        ("error_out", None),
    ]
    other = package.function(error_in=package.error(8), exception_code=7)
    assert (other.failed, other.code, other.error_out.code) == (True, 8, 8)
    warning = package.error(7, status=False)
    kept = package.function(error_in=warning, exception_code=7)
    assert (kept.failed, kept.code, kept.error_out) == (False, 7, warning)


def test_cancel_error_on_match_tests_the_exception_source_when_wired(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(
        general_error_handler(
            project, action=1, exception_code=True, exception_source=True
        )
    )
    error = package.error(7, source="here")
    cancelled = package.function(
        error_in=error, exception_code=7, exception_source="here"
    )
    assert (cancelled.failed, cancelled.error_out) == (False, None)
    kept = package.function(error_in=error, exception_code=7, exception_source="there")
    assert (kept.failed, kept.error_out) == (True, error)
    default_source = package.function(
        error_in=error, exception_code=7, exception_source=""
    )
    assert (default_source.failed, default_source.error_out) == (False, None)


def test_set_error_on_match_upgrades_a_warning_to_an_error(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(
        general_error_handler(project, action=2, exception_code=True)
    )
    warning = package.error(7, status=False, source="warn here")
    result = package.function(error_in=warning, exception_code=7)
    assert (result.failed, result.code, result.source) == (True, 7, "warn here")
    assert (result.error_out.status, result.error_out.code) == (True, 7)
    other = package.function(error_in=package.error(8, status=False), exception_code=7)
    assert (other.failed, other.code) == (False, 8)
    error = package.error(7)
    assert package.function(error_in=error, exception_code=7).error_out is error
    assert package.function(exception_code=7).error_out is None


def test_no_exception_performs_nothing_even_with_an_exception_code(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(
        general_error_handler(project, action=0, exception_code=True)
    )
    error = package.error(7)
    result = package.function(error_in=error, exception_code=7)
    assert (result.failed, result.error_out) == (True, error)
    assert "excepted_error" not in package.source


def test_a_value_of_exception_action_not_in_NI_page_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, action=3))
    (gap,) = package.gaps
    assert "exception action" in str(gap) and "3" in str(gap)
    with pytest.raises(NotImplementedError, match="exception action"):
        package.function()


def test_a_non_constant_exception_action_is_a_gap(
    project: Project,  # noqa: F811
) -> None:
    package = project.generate(general_error_handler(project, action="control"))
    (gap,) = package.gaps
    assert "exception action" in str(gap)
    with pytest.raises(NotImplementedError, match="exception action"):
        package.function(action=1)
