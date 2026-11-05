"""The Error Ring glyph against the spec measured on NI's public Error Ring page
image (labview-api-ref, vi-lib/errorring/loc_ErrorRing.gif): the ring is 22
pixels high; a 2-pixel #b4ab00 border on a white body; the error / warning
toggle a 15-pixel round mark 3 pixels inside the outer left edge; the call-chain
toggle 5 pixels wide from 21 pixels in; the text from 28 pixels in; a 7-by-4
#a57b39 dropdown arrow whose right edge is 4 pixels inside the right border;
the error out terminal right of the body. Each number is checked on the drawing
calls the glyph makes for a ring the size of a real one (the 6005 ring of
actor-framework's Wire FP Controls to Accessor UnBundler.vi: <bounds> 254 x 22,
its error out termBounds x 245-254)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from lvkit.render.glyphs.nodes.error_ring import ErrorRingGlyph
from lvkit.render.style import DEFAULT_THEME


@dataclass
class _Recorder:
    """A backend that records each drawing call and its arguments."""

    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(
        default_factory=list
    )

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record

    def measure_text(self, text: str, size: float) -> float:
        return len(text) * size * 0.5

    def of(self, kind: str) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
        return [(a, k) for n, a, k in self.calls if n == kind]


LABEL = "6005: Attributes don't match VI prototy..."
BOUNDS = (100.0, 50.0, 354.0, 72.0)  # x1, y1, x2, y2: 254 x 22
BODY_RIGHT = 245 / 254


def _draw(*, is_warning: bool, call_chain: bool) -> _Recorder:
    recorder = _Recorder()
    ErrorRingGlyph(LABEL, is_warning, call_chain, BODY_RIGHT).draw(
        recorder,  # type: ignore[arg-type]
        BOUNDS,
        DEFAULT_THEME,
    )
    return recorder


def test_the_body_border_text_and_arrow_are_nis_measurements() -> None:
    drawn = _draw(is_warning=False, call_chain=True)
    [(body, style)] = drawn.of("rect")
    # The 2-pixel border, drawn on its centre line, inside the node's box up to
    # where the error out terminal begins (x 345).
    assert body == (101.0, 51.0, 344.0, 71.0)
    assert (style["stroke"], style["stroke_width"]) == ("#b4ab00", 2.0)
    assert style["fill"] == DEFAULT_THEME.const_fill
    [(text, text_style)] = drawn.of("text")
    assert text[0] == 128.0  # 28 pixels from the outer left edge
    assert text[2] == LABEL and text[3] == 9.0
    assert text_style["anchor"] == "start"
    [(arrow, arrow_style)] = drawn.of("polygon")
    # Right edge 4 pixels inside the 2-pixel border; 7 wide, 4 high, centred.
    assert arrow[0] == [(332.0, 59.0), (339.0, 59.0), (335.5, 63.0)]
    assert arrow_style["fill"] == "#a57b39"


def test_the_error_toggle_and_the_call_chain_mark() -> None:
    drawn = _draw(is_warning=False, call_chain=True)
    circles = drawn.of("circle")
    toggle, toggle_style = circles[0]
    # A 15-pixel round mark starting 3 pixels inside the outer left edge.
    assert toggle == (110.5, 61.0, 7.5)
    assert toggle_style["fill"] == DEFAULT_THEME.error_ring_error
    assert len(drawn.of("line")) == 2  # the cross
    # The call-chain mark: two rings centred 23.5 pixels in (21 + 5 / 2).
    assert [c[0][0] for c in circles[1:]] == [123.5, 123.5]


def test_a_warning_without_the_call_chain() -> None:
    drawn = _draw(is_warning=True, call_chain=False)
    circles = drawn.of("circle")
    assert circles[0][1]["fill"] == DEFAULT_THEME.error_ring_warning
    # The exclamation bar and dot; no call-chain rings.
    assert len(drawn.of("line")) == 1
    assert len(circles) == 2
