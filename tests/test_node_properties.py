"""Tests for the three decoded loop/tunnel node properties (vi-node-properties
branch): tunnel aggregation mode (``Tunnel.mode``), shift-register
init/stacked-depth (``Tunnel.sr_initialized`` / ``Tunnel.sr_stack_depth``),
and for-loop parallelism (``LoopOperation.parallel`` /
``.parallel_static_workers``).

Corpus-backed, mirroring the ``InMemoryVIGraph().load_vi(...)`` pattern in
test_vi_properties.py: every VI here is the SAME example cited in the parsed
XML while building this feature, spot-checked against the raw ``*_BDHb.xml``
directly (grep/ElementTree) before writing these assertions. Corpus tests
skip (not fail) when the sample isn't present on disk.

One synthetic-XML unit test (``TestLpTunModeSynthetic``) covers PASSTHROUGH,
which has no maintainer-cited corpus example -- ``extract_tunnel_mapping``
exercised directly against a hand-built ``dco`` element, same pattern as
``TestParseLvsrProperties`` in test_vi_properties.py.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.netlist import build_netlist, netlist_to_dict
from lvkit.models import LoopOperation, Operation, Tunnel, TunnelMode
from lvkit.parser.nodes.base import extract_tunnel_mapping

# ---------------------------------------------------------------------------
# Corpus VI paths (all under .lvkit/cache/samples/, verified against the
# extracted *_BDHb.xml before writing the assertions below)
# ---------------------------------------------------------------------------

BUILD_VI = Path(".lvkit/cache/samples/JKI-VI-Tester/source/build.vi")
RUN_DIFF_VI = Path(
    ".lvkit/cache/samples/measurement-plugin-labview/Source/Tools/run_diff.vi"
)
CREATE_TEST_CONFIG_VI = Path(
    ".lvkit/cache/samples/DCAF-DAQModule/source/testing/Create Test Configuration.vi"
)
CONFIG_VI = Path(".lvkit/cache/samples/DCAF-DAQModule/source/testing/Config.vi")
FILTER_MPA_VI = Path(
    ".lvkit/cache/samples/LabVIEW-OOP-Classes/DAQ/Analog Input/AI_class/"
    "utils/Filter - Moving Point Average.vi"
)
RUN_SERVICE_VI = Path(
    ".lvkit/cache/samples/measurement-plugin-labview/Source/Runtime/"
    "Measurements/Run Service.vi"
)


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"Sample not available: {path}")


def _load(path: Path) -> tuple[InMemoryVIGraph, str]:
    g = InMemoryVIGraph()
    g.load_vi(str(path), mode=LoadMode.NONE)
    vi_name = g.resolve_vi_name(path.name)
    return g, vi_name


def _find_op(operations: list[Operation], uid: str) -> Operation | None:
    """Recursively find an operation by its trailing node uid (matches
    ``op.id``'s ``"<vi>::<uid>"`` suffix), searching inner_nodes and every
    frame-bearing structure's frame bodies -- same shape as
    describe.py::_find_operation, re-derived here to keep this test file
    self-contained."""
    for op in operations:
        if op.id == uid or op.id.endswith(f"::{uid}"):
            return op
        found = _find_op(op.inner_nodes, uid)
        if found is not None:
            return found
        for frame in getattr(op, "frames", []):
            found = _find_op(frame.operations, uid)
            if found is not None:
                return found
    return None


def _loop_op(path: Path, uid: str) -> LoopOperation:
    g, vi_name = _load(path)
    op = _find_op(g.get_operations(vi_name), uid)
    assert isinstance(op, LoopOperation), f"expected a LoopOperation, got {op!r}"
    return op


def _tunnel_by_outer(op: LoopOperation, outer_uid_suffix: str) -> Tunnel:
    matches = [
        t for t in op.tunnels if t.outer_terminal_uid.endswith(f"::{outer_uid_suffix}")
    ]
    assert len(matches) == 1, (
        f"expected exactly one tunnel with outer uid {outer_uid_suffix},"
        f" got {[t.outer_terminal_uid for t in matches]}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# (1) Tunnel aggregation mode
# ---------------------------------------------------------------------------


class TestTunnelMode:
    def test_indexing(self) -> None:
        """build.vi forLoop 287, lpTun dco uid 1457 (outer terminal uid
        1460, inner 1459) -- INDEXING: outer array[1] String, inner
        String."""
        _skip_if_missing(BUILD_VI)
        op = _loop_op(BUILD_VI, "287")
        tunnel = _tunnel_by_outer(op, "1460")
        assert tunnel.tunnel_type == "lpTun"
        assert tunnel.mode == TunnelMode.INDEXING

    def test_input_no_indexing_is_passthrough(self) -> None:
        """run_diff.vi forLoop 1694 -- its lpTun tunnel (dco uid 1866, outer
        terminal uid 1896) is an INPUT tunnel with indexing OFF: outer==inner
        Path, whole value passes straight in. Input tunnels are only
        auto-index or no-indexing, so this is PASSTHROUGH -- NOT LAST_VALUE
        (a last-value is an output-only mode; the graph re-labels the parser's
        indexing-off default by direction, see construction.py)."""
        _skip_if_missing(RUN_DIFF_VI)
        op = _loop_op(RUN_DIFF_VI, "1694")
        tunnel = _tunnel_by_outer(op, "1896")
        assert tunnel.tunnel_type == "lpTun"
        assert tunnel.mode == TunnelMode.PASSTHROUGH
        assert tunnel.conditional is False

    def test_concatenating(self) -> None:
        """DCAF-DAQModule 'Create Test Configuration.vi' forLoop 1094 --
        MEDIUM confidence (rare in corpus): a bare-dcoFiller-less lpTun with
        <TunnelType>02</TunnelType> (dco uid 5548, outer terminal uid 5565)."""
        _skip_if_missing(CREATE_TEST_CONFIG_VI)
        op = _loop_op(CREATE_TEST_CONFIG_VI, "1094")
        tunnel = _tunnel_by_outer(op, "5565")
        assert tunnel.tunnel_type == "lpTun"
        assert tunnel.mode == TunnelMode.CONCATENATING

    def test_conditional(self) -> None:
        """DCAF-DAQModule testing/Config.vi forLoop 167 -- two lpTun tunnels
        (dco uid 180 and 186, outer terminal uids 179 and 185) carry
        <IsConditional>True</IsConditional> + a sibling <LpTunConditionDCO>.
        Conditional is an ORTHOGONAL modifier, not a mode: the base mode stays
        INDEXING (element into array), with conditional=True layered on
        (conditionally index each iteration's element)."""
        _skip_if_missing(CONFIG_VI)
        op = _loop_op(CONFIG_VI, "167")
        for outer_uid in ("179", "185"):
            tunnel = _tunnel_by_outer(op, outer_uid)
            assert tunnel.tunnel_type == "lpTun"
            assert tunnel.mode == TunnelMode.INDEXING
            assert tunnel.conditional is True

    def test_describe_shows_tunnel_mode(self) -> None:
        """describe_structure's per-loop tunnel line surfaces ``mode=``
        terse text -- the CLI/MCP-visible text path (``describe_structure``,
        NOT describe_vi's top-level page -- loop tunnel detail is a
        per-operation drill-down, see describe.py::describe_structure)."""
        _skip_if_missing(BUILD_VI)
        from lvkit.graph.describe import describe_structure

        g, vi_name = _load(BUILD_VI)
        op = _find_op(g.get_operations(vi_name), "287")
        assert op is not None
        text = describe_structure(g, vi_name, op.id)
        assert "mode=INDEXING" in text

    def test_get_context_json_shows_tunnel_mode(self) -> None:
        """netlist_to_dict (the MCP get_context tool's JSON shape) carries
        the tunnel mode through as the enum's string value."""
        _skip_if_missing(BUILD_VI)
        g, vi_name = _load(BUILD_VI)
        d = netlist_to_dict(build_netlist(g, vi_name))
        scope = _find_scope(d["body"], "287")
        assert scope is not None
        modes = {t["tunnel_type"]: t["mode"] for t in scope["tunnels"]}
        assert modes["lpTun"] == "INDEXING"


class TestLpTunModeSynthetic:
    """PASSTHROUGH has no maintainer-cited corpus example -- exercise
    ``extract_tunnel_mapping`` directly against a hand-built ``dco`` element
    matching the corpus shape found while implementing this feature (a bare
    ``<dcoFiller>``, no ``<innerLpTunDCO>``, no ``<TunnelType>``)."""

    def test_passthrough(self) -> None:
        dco = ET.fromstring(
            """
            <dco class="lpTun" uid="1">
              <objFlags>2048</objFlags>
              <termList elements="2">
                <SL__arrayElement uid="2" />
                <SL__arrayElement uid="3" />
                </termList>
              <typeDesc>TypeID(1)</typeDesc>
              <dcoFiller>512</dcoFiller>
              <termBounds>(0, 0, 9, 9)</termBounds>
              </dco>
            """
        )
        tunnels = extract_tunnel_mapping(dco, "lpTun")
        assert len(tunnels) == 1
        assert tunnels[0].mode == TunnelMode.PASSTHROUGH

    @staticmethod
    def _lp_tun(
        inner_objflags: str | None,
        *,
        tunnel_type: str | None = None,
        conditional: bool = False,
    ) -> ET.Element:
        """A lpTun dco with an innerLpTunDCO carrying the given objFlags (the
        auto-index flag lives in bit 0x400000 there). Optional <TunnelType>
        (older explicit index encoding) and <IsConditional>."""
        of = (
            f"<objFlags>{inner_objflags}</objFlags>"
            if inner_objflags is not None
            else ""
        )
        tt = f"<TunnelType>{tunnel_type}</TunnelType>" if tunnel_type else ""
        cond = "<IsConditional>True</IsConditional>" if conditional else ""
        return ET.fromstring(
            f"""
            <dco class="lpTun" uid="1">
              <objFlags>2049</objFlags>
              <termList elements="2">
                <SL__arrayElement uid="2" />
                <SL__arrayElement uid="3" />
                </termList>
              <typeDesc>TypeID(1)</typeDesc>
              {tt}{cond}
              <innerLpTunDCO class="innerLpTun" uid="4">
                {of}
                <termList elements="2">
                  <SL__arrayElement uid="2" />
                  <SL__arrayElement uid="3" />
                  </termList>
                <typeDesc>TypeID(2)</typeDesc>
                <lpTunDCO uid="1" />
                </innerLpTunDCO>
              </dco>
            """
        )

    def test_indexing_from_flag_bit(self) -> None:
        """The auto-index flag is innerLpTunDCO objFlags bit 0x400000 -- set,
        with NO <TunnelType>, still decodes to INDEXING (the 153-tunnel case
        the old <TunnelType>-only rule missed)."""
        t = extract_tunnel_mapping(self._lp_tun("4194304"), "lpTun")[0]
        assert t.mode == TunnelMode.INDEXING
        assert t.conditional is False

    def test_indexing_from_tunnel_type(self) -> None:
        """The older explicit encoding (<TunnelType>, flag bit clear) also
        decodes to INDEXING."""
        t = extract_tunnel_mapping(self._lp_tun("16777216", tunnel_type="01"), "lpTun")[
            0
        ]
        assert t.mode == TunnelMode.INDEXING

    def test_last_value_flag_clear(self) -> None:
        """innerLpTunDCO present, index bit clear, no <TunnelType> -> the base
        mode is LAST_VALUE (an output tunnel passing the final value; the graph
        re-labels an input tunnel here as PASSTHROUGH by direction)."""
        t = extract_tunnel_mapping(self._lp_tun("0"), "lpTun")[0]
        assert t.mode == TunnelMode.LAST_VALUE
        assert t.conditional is False

    def test_conditional_is_orthogonal_to_base_mode(self) -> None:
        """<IsConditional>True with the index flag set -> base INDEXING +
        conditional=True (the modifier layers on, never replaces the mode)."""
        t = extract_tunnel_mapping(self._lp_tun("4194304", conditional=True), "lpTun")[
            0
        ]
        assert t.mode == TunnelMode.INDEXING
        assert t.conditional is True

    def test_non_lp_tun_never_gets_a_mode(self) -> None:
        """mode is an lpTun-only concept -- lSR/rSR/lMax/... always None,
        even though they share the same extract_tunnel_mapping codepath."""
        dco = ET.fromstring(
            """
            <dco class="lMax" uid="1">
              <termList elements="2">
                <SL__arrayElement uid="2" />
                <SL__arrayElement uid="3" />
                </termList>
              <dcoFiller>512</dcoFiller>
              </dco>
            """
        )
        tunnels = extract_tunnel_mapping(dco, "lMax")
        assert len(tunnels) == 1
        assert tunnels[0].mode is None


# ---------------------------------------------------------------------------
# (2) Shift register init / stacked
# ---------------------------------------------------------------------------


class TestShiftRegister:
    def test_lsr_initialized(self) -> None:
        """build.vi forLoop 287, lSR uid 1513 -- its outer terminal is wired
        from outside the loop (a signal in the enclosing diagram's
        signalList) -- initialized."""
        _skip_if_missing(BUILD_VI)
        op = _loop_op(BUILD_VI, "287")
        tunnel = _tunnel_by_outer(op, "1513")
        assert tunnel.tunnel_type == "lSR"
        assert tunnel.sr_initialized is True

    def test_rsr_normal_stack_depth(self) -> None:
        """build.vi forLoop 287's rSR (uid 269, outer terminal uid 1510) is a
        normal, unstacked shift register -- lsrDCOList has exactly 1 entry."""
        _skip_if_missing(BUILD_VI)
        op = _loop_op(BUILD_VI, "287")
        tunnel = _tunnel_by_outer(op, "1510")
        assert tunnel.tunnel_type == "rSR"
        assert tunnel.sr_stack_depth == 1

    def test_stacked_shift_register_20_deep(self) -> None:
        """LabVIEW-OOP-Classes 'Filter - Moving Point Average.vi' -- rSR uid
        536 (outer terminal uid 550) lists 20 lSR uids in its lsrDCOList."""
        _skip_if_missing(FILTER_MPA_VI)
        g, vi_name = _load(FILTER_MPA_VI)
        ops = g.get_operations(vi_name)
        loops: list[LoopOperation] = []
        _collect_loops(ops, loops)
        rsr_tunnels = [
            t
            for lo in loops
            for t in lo.tunnels
            if t.tunnel_type == "rSR" and t.outer_terminal_uid.endswith("::550")
        ]
        assert len(rsr_tunnels) == 1
        assert rsr_tunnels[0].sr_stack_depth == 20

    def test_stacked_shift_register_uninitialized_lsrs(self) -> None:
        """Same VI/loop: every lSR in the 20-deep stack is uninitialized --
        none of their outer terminals carry an external wire (spot-checked
        against uid 553, the first lSR in the stack)."""
        _skip_if_missing(FILTER_MPA_VI)
        g, vi_name = _load(FILTER_MPA_VI)
        ops = g.get_operations(vi_name)
        loops: list[LoopOperation] = []
        _collect_loops(ops, loops)
        lsr_tunnels = [
            t
            for lo in loops
            for t in lo.tunnels
            if t.tunnel_type == "lSR" and t.outer_terminal_uid.endswith("::553")
        ]
        assert len(lsr_tunnels) == 1
        assert lsr_tunnels[0].sr_initialized is False
        # Every lSR feeding this rSR's stack is uninitialized (not just the
        # one spot-checked above).
        all_lsr = [t for lo in loops for t in lo.tunnels if t.tunnel_type == "lSR"]
        assert len(all_lsr) == 20
        assert all(t.sr_initialized is False for t in all_lsr)

    def test_describe_shows_sr_facts(self) -> None:
        """describe_structure's per-loop tunnel line surfaces
        ``initialized=`` / ``stack_depth=`` terse text (stack_depth only
        when != 1)."""
        _skip_if_missing(FILTER_MPA_VI)
        from lvkit.graph.describe import describe_structure

        g, vi_name = _load(FILTER_MPA_VI)
        ops = g.get_operations(vi_name)
        loops: list[LoopOperation] = []
        _collect_loops(ops, loops)
        assert len(loops) == 1
        text = describe_structure(g, vi_name, loops[0].id)
        assert "initialized=False" in text
        assert "stack_depth=20" in text

    def test_get_context_json_shows_sr_facts(self) -> None:
        _skip_if_missing(FILTER_MPA_VI)
        g, vi_name = _load(FILTER_MPA_VI)
        d = netlist_to_dict(build_netlist(g, vi_name))
        rsr_entries = [
            t
            for scope in _iter_scopes(d["body"])
            for t in scope.get("tunnels", [])
            if t["tunnel_type"] == "rSR"
        ]
        assert any(t["sr_stack_depth"] == 20 for t in rsr_entries)


def _collect_loops(operations: list[Operation], acc: list[LoopOperation]) -> None:
    for op in operations:
        if isinstance(op, LoopOperation):
            acc.append(op)
        _collect_loops(op.inner_nodes, acc)
        for frame in getattr(op, "frames", []):
            _collect_loops(frame.operations, acc)


# ---------------------------------------------------------------------------
# (3) For-loop parallelism
# ---------------------------------------------------------------------------


class TestForLoopParallelism:
    def test_parallel_with_static_workers(self) -> None:
        """measurement-plugin-labview 'Run Service.vi' forLoop 2090 --
        <ParForWorkers> present + <ParForNumStaticWorkers>08</...> -> 8."""
        _skip_if_missing(RUN_SERVICE_VI)
        op = _loop_op(RUN_SERVICE_VI, "2090")
        assert op.parallel is True
        assert op.parallel_static_workers == 8

    def test_non_parallel_for_loop(self) -> None:
        """A for-loop with no <ParForWorkers> child (the overwhelming common
        case -- build.vi forLoop 287 is a plain serial for-loop) parses
        parallel=False / parallel_static_workers=None, never a guessed
        default like 1 worker."""
        _skip_if_missing(BUILD_VI)
        op = _loop_op(BUILD_VI, "287")
        assert op.parallel is False
        assert op.parallel_static_workers is None

    def test_describe_shows_parallel(self) -> None:
        _skip_if_missing(RUN_SERVICE_VI)
        from lvkit.graph.describe import describe_structure

        g, vi_name = _load(RUN_SERVICE_VI)
        op = _find_op(g.get_operations(vi_name), "2090")
        assert op is not None
        text = describe_structure(g, vi_name, op.id)
        assert "(parallel, 8 workers)" in text

    def test_get_context_json_shows_parallel(self) -> None:
        _skip_if_missing(RUN_SERVICE_VI)
        g, vi_name = _load(RUN_SERVICE_VI)
        d = netlist_to_dict(build_netlist(g, vi_name))
        scope = _find_scope(d["body"], "2090")
        assert scope is not None
        assert scope["parallel"] is True
        assert scope["parallel_static_workers"] == 8


# ---------------------------------------------------------------------------
# netlist_to_dict JSON-tree search helpers
# ---------------------------------------------------------------------------


def _iter_scopes(items: list[dict]) -> list[dict]:
    """Every scope dict anywhere in a netlist body tree, recursively."""
    found: list[dict] = []
    for item in items:
        if item["kind"] != "scope":
            continue
        found.append(item)
        for frame in item["frames"]:
            found.extend(_iter_scopes(frame["body"]))
    return found


def _find_scope(items: list[dict], uid: str) -> dict | None:
    for scope in _iter_scopes(items):
        if scope["uid"] == uid:
            return scope
    return None
