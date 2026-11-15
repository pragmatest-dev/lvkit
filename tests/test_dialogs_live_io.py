"""The dialog boxes (One, Two and Three Button Dialog, Simple and General Error
Handler) are ``live_io.prompt``: the generated VI takes ``live_io``, a
``LiveIO`` that answers gets the dialog and its answer comes back, and the base
class asks on the terminal. The generated code is run."""

from __future__ import annotations

import importlib
import io
import sys
from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.panel.live_io import LiveIO, PromptUnavailableError
from tests.live_io_recorder import PromptRecorder
from tests.primitive_caller import run_primitive
from tests.vilib_caller import BOOL, STRING, U32, primitive, run_catalog_call

ARRAY = primitive("Array")
ERROR_HANDLER = ["<vilib>", "Utility", "error.llb"]
THREE_BUTTON = [*ERROR_HANDLER, "Three Button Dialog.vi"]

# One Button Dialog: [0] true, [1] button name, [2] message.
ONE_BUTTON_PANE = [(0, "output", BOOL), (1, "input", STRING), (2, "input", STRING)]
# Two Button Dialog: [0] T button?, [1] F button name, [2] T button name,
# [3] message.
TWO_BUTTON_PANE = [
    (0, "output", BOOL),
    (1, "input", STRING),
    (2, "input", STRING),
    (3, "input", STRING),
]


def _one_button(recorder: LiveIO, button: bool) -> tuple[str, Any]:
    inputs: dict[str, Any] = {"message": (2, STRING)}
    arguments: dict[str, Any] = {"message": "Disk full", "live_io": recorder}
    if button:
        inputs["button"] = (1, STRING)
        arguments["button"] = "Continue"
    return run_primitive(
        1340, ONE_BUTTON_PANE, inputs, {"closed": (0, BOOL)}, arguments
    )


def test_one_button_dialog_shows_the_message_and_its_default_button() -> None:
    recorder = PromptRecorder()
    source, result = _one_button(recorder, button=False)
    assert recorder.dialogs == [("Disk full", ["OK"], "")]
    assert result.closed is True
    assert "lvkit" not in source


def test_one_button_dialog_shows_the_button_name_wired_to_it() -> None:
    recorder = PromptRecorder()
    _, result = _one_button(recorder, button=True)
    assert recorder.dialogs == [("Disk full", ["Continue"], "")]
    assert result.closed is True


def _two_button(answer: int) -> tuple[PromptRecorder, Any]:
    recorder = PromptRecorder(answer)
    _, result = run_primitive(
        1341,
        TWO_BUTTON_PANE,
        {"message": (3, STRING), "yes": (2, STRING), "no": (1, STRING)},
        {"t_button": (0, BOOL)},
        {"message": "Are you sure?", "yes": "Yes", "no": "No", "live_io": recorder},
    )
    return recorder, result


def test_two_button_dialog_is_true_when_the_first_button_is_chosen() -> None:
    recorder, result = _two_button(0)
    assert recorder.dialogs == [("Are you sure?", ["Yes", "No"], "")]
    assert result.t_button is True


def test_two_button_dialog_is_false_when_the_second_button_is_chosen() -> None:
    _, result = _two_button(1)
    assert result.t_button is False


# Three Button Dialog: [3] Which Button?, [4] Allow user to close window?,
# [5] Justify Message, [7] Window Title, [8] Right, [9] Center, [10] Left,
# [11] message.
THREE_BUTTON_PANE = [
    (3, "output", U32),
    (7, "input", STRING),
    (8, "input", STRING),
    (9, "input", STRING),
    (10, "input", STRING),
    (11, "input", STRING),
]


def _three_button(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recorder: LiveIO,
    **buttons: str,
) -> tuple[str, Any]:
    slots = {"left": 10, "center": 9, "right": 8, "title": 7}
    inputs = {"message": (11, STRING)}
    arguments: dict[str, Any] = {"message": "Close the unit?", "live_io": recorder}
    for label, text in buttons.items():
        inputs[label] = (slots[label], STRING)
        arguments[label] = text
    return run_catalog_call(
        tmp_path,
        monkeypatch,
        THREE_BUTTON,
        THREE_BUTTON_PANE,
        inputs,
        {"which": (3, U32)},
        arguments,
    )


def test_three_button_dialog_returns_the_position_of_the_chosen_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = PromptRecorder(2)
    source, result = _three_button(
        tmp_path, monkeypatch, recorder, left="Yes", center="No", right="Cancel"
    )
    assert recorder.dialogs == [("Close the unit?", ["Yes", "No", "Cancel"], "")]
    assert result.which == 2
    assert "lvkit" not in source


def test_three_button_dialog_has_the_documented_default_texts_and_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = PromptRecorder(1)
    _, result = _three_button(tmp_path, monkeypatch, recorder, title="Pico")
    assert recorder.dialogs == [("Close the unit?", ["Yes", "No", "Cancel"], "Pico")]
    assert result.which == 1


def test_a_button_with_empty_text_is_hidden_by_the_base_class(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "stdin", _Terminal("2\n"))
    chosen = LiveIO().prompt("Go on?", ["Yes", "", "Cancel"])
    assert chosen == 2
    shown = capsys.readouterr().out
    assert "1. Yes" in shown and "2. Cancel" in shown and "No" not in shown


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_the_base_class_asks_on_the_terminal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    answers = iter(["x", "9", "2"])
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert LiveIO().prompt("Save?", ["Yes", "No"], "Pico") == 1
    shown = capsys.readouterr().out
    assert "[Pico]" in shown and "Save?" in shown and "1. Yes" in shown


def test_the_base_class_with_one_button_waits_for_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked: list[str] = []
    monkeypatch.setattr(sys, "stdin", _Terminal())
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "")
    assert LiveIO().prompt("Done.", ["", ""]) == 0
    assert asked == ["[OK] press Enter to continue: "]


def test_with_no_terminal_the_base_class_names_the_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with pytest.raises(PromptUnavailableError, match="Disk full"):
        LiveIO().prompt("Disk full", ["OK"])


def test_a_dialog_with_no_live_io_raises_the_error_that_names_it() -> None:
    with pytest.raises(RuntimeError, match="Disk full") as raised:
        run_primitive(
            1340,
            ONE_BUTTON_PANE,
            {"message": (2, STRING)},
            {"closed": (0, BOOL)},
            {"message": "Disk full"},
        )
    assert type(raised.value).__name__ == "PromptUnavailableError"


def test_a_neighbour_dialog_vi_stays_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps: list[Gap] = []
    with pytest.raises(NotImplementedError):
        run_catalog_call(
            tmp_path,
            monkeypatch,
            ["<vilib>", "Utility", "error.llb", "Some Other Dialog.vi"],
            [(0, "input", STRING)],
            {"message": (0, STRING)},
            {},
            {"message": "x"},
            gaps=gaps,
            runtime_not_implemented=True,
        )
    assert len(gaps) == 1


@pytest.mark.needs_samples
def test_a_pico_error_handler_shows_its_three_button_dialog_and_acts_on_the_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from lvkit.pipeline import generate_python

    vi = Path(
        ".lvkit/cache/samples/picosdk-ni-labview-examples"
        "/picohrdl/PicohrdlLib/Picohrdlerrorhandler.vi"
    )
    result = generate_python(vi, tmp_path)
    written = Path(result["vi_paths"][str(vi.resolve())])
    assert not written.name.endswith(".error.py")
    package = written.parents[1]
    monkeypatch.syspath_prepend(str(package.parent))
    module = importlib.import_module(f"{package.name}.picohrdllib.{written.stem}")
    unit_info = SimpleNamespace(trimmed_string="none", handle_out=5, error_out=None)
    monkeypatch.setattr(module, "picohrdlunitinfosingle", lambda **_: unit_info)
    handler = module.picohrdlerrorhandler
    try:
        # Continue (the center button): the handle comes back.
        recorder = PromptRecorder(1)
        done = handler(return_=0, api_function="HRDLOpenUnit", live_io=recorder)
        message, buttons, title = recorder.dialogs[0]
        assert buttons == ["Close and Exit", "Continue", "Stop"]
        assert title == "Error HRDL API function call "
        assert "HRDLOpenUnit" in message and "HRDL_ERROR none" in message
        assert done.handle_out == 5
        # Stop (the right button): the run stops.
        with pytest.raises(SystemExit):
            handler(return_=0, api_function="x", live_io=PromptRecorder(2))
        # No error code: no dialog.
        quiet = PromptRecorder()
        handler(return_=1, live_io=quiet)
        assert quiet.dialogs == []
    finally:
        for name in [n for n in sys.modules if n.startswith(f"{package.name}")]:
            sys.modules.pop(name)
