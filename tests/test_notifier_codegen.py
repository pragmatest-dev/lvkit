"""Notifier Operations generate code that calls the package's ``Notifier``: tiny
handmade VIs (Obtain, Send, Wait and Release Notifier on a diagram) are generated
and run, and a package with no notifier has no ``notifiers.py``."""

from __future__ import annotations

from lvkit.codegen.nodes import notifier_ops
from lvkit.codegen.notifiers_module import NOTIFIERS_MODULE_SOURCE
from tests.error_diagrams import (
    BOOL,
    ERR,
    I32,
    NOTIFIER,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Err.vi"
OBTAIN, RELEASE, SEND, WAIT = 9101, 9102, 9104, 9105


def string_hex(text: str) -> str:
    return f"{len(text):08X}" + text.encode().hex().upper()


def test_dispatch_set_is_the_notifier_prim_ids() -> None:
    assert {int(p) for p in notifier_ops.NOTIFIER_PRIM_IDS} == {
        OBTAIN,
        RELEASE,
        SEND,
        WAIT,
    }


# -- generated VIs ------------------------------------------------------------


def notifier_vi(
    *,
    name: str | None = None,
    timeout: int = 0,
    release_first: bool = False,
    ignore_previous: bool | None = None,
) -> Vi:
    """``value`` -> Obtain Notifier -> Send Notification -> Wait on Notification
    -> Release Notifier, the error wire running through all four. The results
    are ``result`` and ``timed_out``; with ``release_first`` the notifier is
    released before the Wait."""
    vi = Vi(NAME)
    value = vi.control("value", I32, REQUIRED)
    ein = vi.error_in()
    result = vi.indicator("result", I32)
    timed_out = vi.indicator("timed_out", BOOL)
    created = vi.indicator("created", BOOL)
    eout = vi.error_out()
    obtain = vi.primitive(
        OBTAIN,
        [
            ("name", 0, STR, False),
            ("error in", 3, ERR, False),
            ("notifier out", 8, NOTIFIER, True),
            ("created new?", 10, BOOL, True),
            ("error out", 11, ERR, True),
        ],
    )
    send = vi.primitive(
        SEND,
        [
            ("notifier", 0, NOTIFIER, False),
            ("notification", 1, I32, False),
            ("error in", 3, ERR, False),
            ("notifier out", 8, NOTIFIER, True),
            ("error out", 11, ERR, True),
        ],
    )
    wait = vi.primitive(
        WAIT,
        [
            ("notifier", 0, NOTIFIER, False),
            ("ignore previous", 1, BOOL, False),
            ("timeout in ms", 2, I32, False),
            ("error in", 3, ERR, False),
            ("notifier out", 8, NOTIFIER, True),
            ("notification", 9, I32, True),
            ("timed out?", 10, BOOL, True),
            ("error out", 11, ERR, True),
        ],
    )
    release = vi.primitive(
        RELEASE,
        [
            ("notifier", 0, NOTIFIER, False),
            ("error in", 3, ERR, False),
            ("notifier name", 8, STR, True),
            ("error out", 11, ERR, True),
        ],
    )
    if name is not None:
        vi.wire(vi.constant(STR, string_hex(name)), obtain["name"])
    vi.wire(ein, obtain["error in"])
    vi.wire(obtain["created new?"], created)
    vi.wire(obtain["notifier out"], send["notifier"])
    vi.wire(value, send["notification"])
    vi.wire(obtain["error out"], send["error in"])
    if release_first:
        vi.wire(send["notifier out"], release["notifier"])
        vi.wire(send["error out"], release["error in"])
        vi.wire(release["error out"], wait["error in"])
        vi.wire(obtain["notifier out"], wait["notifier"])
        vi.wire(wait["error out"], eout)
    else:
        vi.wire(send["notifier out"], wait["notifier"])
        vi.wire(send["error out"], wait["error in"])
        vi.wire(wait["notifier out"], release["notifier"])
        vi.wire(wait["error out"], release["error in"])
        vi.wire(release["error out"], eout)
    vi.wire(vi.int_constant(timeout), wait["timeout in ms"])
    if ignore_previous is not None:
        vi.wire(vi.bool_constant(ignore_previous), wait["ignore previous"])
    vi.wire(wait["notification"], result)
    vi.wire(wait["timed out?"], timed_out)
    return vi


def generated(project: Project, vi: Vi):  # noqa: F811
    project.add(vi)
    return project.generate(NAME)


def test_an_unnamed_notifier_sent_to_and_waited_on(project: Project) -> None:  # noqa: F811
    package = generated(project, notifier_vi())
    assert "lvkit" not in package.source
    assert "from .notifiers import Notifier\n" in package.source
    assert "notifier = Notifier()\n" in package.source
    assert "created = True\n" in package.source
    assert "notifier.send(value)\n" in package.source
    assert (
        "notification, timed_out = notifier.wait('Err.vi::" in package.source
        and ", timeout_ms=0, default=0)\n" in package.source
    )
    assert "if notifier is not None:\n        notifier.release()\n" in package.source
    result = package.function(value=7)
    assert (result.result, result.timed_out, result.created) == (7, False, True)
    assert result.error_out is None
    assert (package.root / "notifiers.py").read_text() == NOTIFIERS_MODULE_SOURCE


def test_a_wait_that_times_out_gives_the_default_and_no_error(
    project: Project,  # noqa: F811
) -> None:
    """Wait on Notification with the timeout of 0 and nothing sent: ``timed out?``
    is TRUE and the notification is the type's default (NI)."""
    vi = notifier_vi(ignore_previous=True)
    package = generated(project, vi)
    assert "ignore_previous=True" in package.source
    result = package.function(value=7)
    assert (result.result, result.timed_out) == (0, True)
    assert result.error_out is None


def test_a_named_notifier_is_found_by_its_name(project: Project) -> None:  # noqa: F811
    package = generated(project, notifier_vi(name="Shared Notifier"))
    assert "Notifier.obtain('Shared Notifier')" in package.source
    first = package.function(value=1)
    second = package.function(value=2)
    assert (first.created, second.created) == (True, True)  # released each time
    assert (first.result, second.result) == (1, 2)


def test_waiting_on_a_released_notifier_is_error_1122(
    project: Project,  # noqa: F811
) -> None:
    """NI: a notifier reference that became invalid stops the wait with error
    1122; ``timed out?`` is TRUE when an error occurs."""
    package = generated(project, notifier_vi(release_first=True))
    result = package.function(value=7)
    assert (result.result, result.timed_out) == (0, True)
    assert (result.error_out.code, result.error_out.status) == (1122, True)
    assert result.error_out.source == "Wait on Notification"


def test_release_runs_when_an_error_came_in(project: Project) -> None:  # noqa: F811
    package = generated(project, notifier_vi())
    result = package.function(value=7, error_in=package.error(5))
    assert result.error_out.code == 5


def test_a_package_without_a_notifier_has_no_notifiers_py(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    x = vi.control("x", I32, REQUIRED)
    out = vi.indicator("out", I32)
    vi.wire(x, out)
    package = generated(project, vi)
    assert not (package.root / "notifiers.py").exists()
