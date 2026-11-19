"""A Merge Signals node (class ``mergeSignal``) is a gap that says why: its dynamic
data output has no Python form, and NI does not say what start time a scalar
input is given."""

from __future__ import annotations

from tests.error_diagrams import (
    Project,
    Vi,
    project,  # noqa: F401
)

NAME = "Merge.vi"


def test_merge_signals_of_two_numbers_is_a_gap_with_its_reason(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    dynamic = vi.add_type('<TypeDesc Type="MeasureData" Flavor="Dynamicdata" />')
    terms, uids = vi.terminals(
        "mergeSignalDCO",
        [
            ("out", 0, dynamic, True),
            ("a", 1, dynamic, False),
            ("b", 2, dynamic, False),
        ],
        "unusedIndex",
    )
    uid = vi.next_uid()
    vi.scope.nodes.append(
        f'<SL__arrayElement class="mergeSignal" uid="{uid}">'
        f'<termList elements="3">{terms}</termList>'
        "<bounds>(0, 0, 30, 30)</bounds></SL__arrayElement>"
    )
    out = vi.indicator("out", dynamic, ddo_class="stdMeasureData")
    vi.wire(uids["out"], out)
    vi.wire(vi.int_constant(1), uids["a"])
    vi.wire(vi.int_constant(2), uids["b"])
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    message = str(package.gaps[0])
    assert "Merge Signals gives dynamic data" in message
    assert "does not say what start time and attributes a scalar input" in message
