"""Reinit To Default of a control the Invoke Node is bound to writes the control's
default value, the way a Value write does (``codegen/nodes/reinit_default.py``):
the control's variable is assigned and a hooked VI calls ``live_io.write``.
Each case is a tiny diagram, generated and called."""

from __future__ import annotations

from tests.error_diagrams import (
    I32,
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.live_io_recorder import Recorder
from tests.panel_diagrams import REINIT_TO_DEFAULT, bound_method, value_property

NAME = "Reinit.vi"


def _string_hex(text: str) -> str:
    return f"{len(text):08X}" + text.encode().hex().upper()


def _text(vi: Vi, text: str) -> int:
    return vi.constant(STR, _string_hex(text))


def _saved_string(text: str) -> str:
    return "".join(f"&amp;#x{b:02X};" for b in bytes.fromhex(_string_hex(text)))


def _changed_then_reinit(default_data: str = "") -> Vi:
    """``s`` is set to "changed" (a Value write), then Reinit To Default runs on
    it; ``seen`` reads ``s``'s Value after: one frame of a Flat Sequence each."""
    vi = Vi(NAME)
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString", default_data=default_data)
    seen = vi.indicator("seen", STR, ddo_class="stdString")
    sequence = vi.flat_sequence([[], [], []])
    with vi.inside(sequence.scopes[0]):
        (write,) = value_property(vi, s, ["Value"])
        vi.wire(_text(vi, "changed"), write)
    with vi.inside(sequence.scopes[1]):
        bound_method(vi, s, "Reinit To Default", REINIT_TO_DEFAULT, "Str")
    with vi.inside(sequence.scopes[2]):
        (read,) = value_property(vi, s, ["Value"])
    vi.place_inside(seen, sequence.scopes[2])
    vi.wire(read, seen)
    return vi


def test_reinit_gives_back_the_type_default(
    project: Project,  # noqa: F811
) -> None:
    project.add(_changed_then_reinit())
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(s="given").seen == ""


def test_reinit_gives_back_the_saved_default(
    project: Project,  # noqa: F811
) -> None:
    project.add(_changed_then_reinit(_saved_string("start")))
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(s="given").seen == "start"


def test_reinit_is_reported_to_the_panel_hooks(
    project: Project,  # noqa: F811
) -> None:
    project.add(_changed_then_reinit(_saved_string("start")))
    package = project.generate(NAME)
    recorder = Recorder()
    package.function(s="given", live_io=recorder)
    assert [v for n, v in recorder.calls if n == "s"] == ["changed", "start"]


def test_reinit_of_a_numeric_control_is_zero(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    n = vi.control("n", I32, REQUIRED)
    seen = vi.indicator("seen", I32)
    bound_method(vi, n, "Reinit To Default", REINIT_TO_DEFAULT, "Num")
    (read,) = value_property(vi, n, ["Value"])
    vi.wire(read, seen)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(n=7).seen == 0


def test_another_method_on_a_bound_control_stays_a_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString")
    bound_method(vi, s, "Some Other Method", 104012801, "Str")
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "Some Other Method" in str(package.gaps[0])


def test_reinit_of_an_indicator_gives_back_its_saved_default(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    out = vi.indicator(
        "out", STR, ddo_class="stdString", default_data=_saved_string("start")
    )
    sequence = vi.flat_sequence([[], []])
    with vi.inside(sequence.scopes[0]):
        (write,) = value_property(vi, out, ["Value"])
        vi.wire(_text(vi, "changed"), write)
    with vi.inside(sequence.scopes[1]):
        bound_method(vi, out, "Reinit To Default", REINIT_TO_DEFAULT, "Str")
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    recorder = Recorder()
    assert package.function(live_io=recorder).out == "start"
    assert [v for n, v in recorder.calls if n == "out"] == ["changed", "start"]
