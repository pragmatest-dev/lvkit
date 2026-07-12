"""Tests for constant attribution to structure frames.

LabVIEW diagram constants wired inside a case/sequence frame carry an sRN
boundary node as their terminal parent. Graph construction stamps the
constant graph node with the containing structure + frame so describe and
diff can position it (instead of reporting an "(empty)" frame and a flat,
position-less "a constant appeared").
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.core import InMemoryVIGraph
from lvkit.graph.describe import (
    _const_type_str,
    _describe_constant_line,
    _format_error_cluster,
)
from lvkit.graph.diff import (
    _compare_frames,
    _frame_content_delta,
    _structure_content_summary,
)
from lvkit.graph.models import Constant
from lvkit.models import (
    CaseFrame,
    CaseOperation,
    ClusterField,
    LVType,
    PrimitiveOperation,
    Terminal,
)

IN_VI = Path("samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi")


def _error_cluster_type() -> LVType:
    return LVType(
        kind="cluster",
        fields=[
            ClusterField(name="status"),
            ClusterField(name="code"),
            ClusterField(name="source"),
        ],
    )


def _error_const(
    *, parent: str | None = None, frame: str | None = None,
    value: str = "{'status': True, 'code': 17, 'source': 'bad'}",
) -> Constant:
    return Constant(
        id="vi::261", value=value, lv_type=_error_cluster_type(),
        parent=parent, frame=frame,
    )


# ── Error-cluster formatting ─────────────────────────────────────────────


class TestErrorClusterFormatting:
    def test_type_label_is_error_cluster(self):
        assert _const_type_str(_error_const()) == "error cluster"

    def test_value_renders_code_and_source(self):
        out = _format_error_cluster(
            "{'status': True, 'code': 17, 'source': 'Mean should be positive'}"
        )
        assert out == 'code 17: "Mean should be positive"'

    def test_value_accepts_dict(self):
        out = _format_error_cluster(
            {"status": True, "code": 42, "source": "boom"}
        )
        assert out == 'code 42: "boom"'

    def test_no_error_value(self):
        out = _format_error_cluster({"status": False, "code": 0, "source": ""})
        assert out == "no error"

    def test_constant_line_has_no_raw_dict(self):
        line = _describe_constant_line(_error_const())
        assert "error cluster" in line
        assert "code 17" in line
        # The ugly raw dict repr must not leak through.
        assert "{'status'" not in line


# ── Real-VI construction: constants get attributed to frames ─────────────


class TestConstructionAttribution:
    def test_in_vi_has_frame_attributed_constants(self):
        graph = InMemoryVIGraph()
        graph.load_vi(str(IN_VI), expand_subvis=False)
        vi_name = graph.resolve_vi_name(IN_VI.name)

        consts = graph.get_constants(vi_name)
        attributed = [c for c in consts if c.parent is not None]

        assert attributed, "expected at least one frame-nested constant in In.vi"
        # Every attributed constant must name both a parent structure and a
        # frame key (so position is recoverable).
        for c in attributed:
            assert c.parent
            assert c.frame is not None


# ── Synthetic per-frame diff logic ───────────────────────────────────────


def _case_with_const() -> tuple[CaseOperation, list[Constant]]:
    """A boolean case whose True frame holds an error-cluster constant and
    whose False frame is a pass-through (no content, has an output tunnel)."""
    out_term = Terminal(id="vi::548", index=1, direction="output")
    op = CaseOperation(
        id="vi::174", name="error constant", labels=["CaseStructure"],
        terminals=[out_term],
        frames=[
            CaseFrame(selector_value="False"),
            CaseFrame(selector_value="True"),
        ],
    )
    return op, [_error_const(parent="vi::174", frame="True")]


class TestPerFrameDiff:
    def test_structure_summary_positions_constant(self):
        op, consts = _case_with_const()
        summary = _structure_content_summary(op, consts)
        assert summary == "frame True: error cluster"

    def test_added_constant_shows_in_frame_delta(self):
        delta = _frame_content_delta(
            [], [],
            [], [_error_const(parent="vi::174", frame="True")],
        )
        assert delta == ["+1 error cluster"]

    def test_removed_constant_shows_in_frame_delta(self):
        delta = _frame_content_delta(
            [], [_error_const(parent="vi::174", frame="True")],
            [], [],
        )
        assert delta == ["-1 error cluster"]

    def test_compare_frames_attributes_to_frame_key(self):
        a_frames = {"False": CaseFrame(selector_value="False"),
                    "True": CaseFrame(selector_value="True")}
        b_frames = {"False": CaseFrame(selector_value="False"),
                    "True": CaseFrame(selector_value="True")}
        const = _error_const(parent="vi::174", frame="True")
        detail = _compare_frames(
            a_frames, b_frames, {}, {"True": [const]},
        )
        assert detail == "frame True: +1 error cluster"

    def test_identical_frames_no_delta(self):
        a_frames = {"True": CaseFrame(selector_value="True")}
        const = _error_const(parent="vi::174", frame="True")
        detail = _compare_frames(
            a_frames, a_frames, {"True": [const]}, {"True": [const]},
        )
        assert detail is None

    def test_op_count_delta(self):
        prim = PrimitiveOperation(
            id="vi::9", name="Add", labels=["Primitive"], primResID=1050,
        )
        delta = _frame_content_delta([], [], [prim], [])
        assert delta == ["+1 Add"]
