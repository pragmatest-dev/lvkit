"""Tests for the VI-Properties/VI-Structure graph facets, parsed from the
main XML's ``<LVSR>`` block -- ``graph.models.VIProperties``/``VIStructure``/
``LockState``.

VIProperties and VIStructure are SEPARATE, sibling graph facets
(``InMemoryVIGraph._vi_properties`` / ``._vi_structure``, surfaced as
``VIContext.properties`` / ``.structure``) -- neither is nested inside the
other, and neither lives on ``VIMetadata`` (identity only: library/
qualified_name/owning_libraries/description).

``TestParseLvsrProperties`` exercises the pure XML-derivation logic
(``parser.metadata._parse_lvsr_properties``) against synthetic XML, matching
the pattern in ``TestParseViMetadata`` (test_parser.py) -- no corpus needed,
covers the lock_state tri-state edge cases and every sub-group precisely.

The corpus test classes below load real sample VIs end-to-end (through
``InMemoryVIGraph`` -> ``VIContext.properties``/``.structure``) and assert
against ground-truth flags verified directly in the extracted XML:

- password-protected + reentrant + lv_version: JKI-VI-Tester's
  ``VITester_Item_Init.vi`` -- ``<Library Protected="1">`` + a real
  (non-empty, non-placeholder) ``<Password Hash>``, ``IsReentrant="1"``,
  ``<Version Major="9" .../>``.
- locked (no password): LabVIEW-OOP-Classes' ``Database_UUT_New.vi`` --
  ``<Library Protected="1">`` with no ``<Password>`` element at all.
- unlocked: lv-flex-channel-examples' ``DAQmx AO/DAQ AO.vi`` -- no
  ``Protected`` library section.
- subroutine: JKI-EasyXML's ``Fast Parser/Get Children.vi`` --
  ``IsSubroutine="1"`` (also all 3 toolbar buttons hidden, a subroutine
  side effect).
- hidden toolbar buttons (Abort/Free Run, Run NOT hidden): JKI-VI-Tester's
  ``Project API/Launch VI Tester.vi``.

NOT found anywhere in the pulled sample corpus (3708 extracted VIs, verified
by grepping every extracted main .xml for ``HasNoBD="1"``/
``SizeToScreen="1"`` -- zero hits): a real VI with ``has_no_block_diagram``
or ``size_to_screen`` set. Real LabVIEW type-definitions (``TypeDefVI="1"``)
are likewise never saved as ``.vi`` -- only as ``.ctl``, which
``InMemoryVIGraph.load_vi()`` does not accept (only ``load_typedef()``,
which does not populate ``_vi_properties``/``_vi_structure``). Per lvkit's
no-guessing rule, these are covered by the synthetic-XML unit tests below
instead of being force-fit onto real data that doesn't exhibit them.

Corpus tests skip (not fail) when the relevant sample isn't present on disk,
consistent with other sample-backed tests in this repo (e.g.
test_class_parent_linkinfo.py).
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import (
    ExecutionProps,
    InstanceProps,
    LockState,
    ToolbarProps,
    VIContext,
    VIProperties,
    VIStructure,
    WindowProps,
)
from lvkit.parser.metadata import _parse_lvsr_properties


class TestParseLvsrProperties:
    """Unit tests for the pure ``<LVSR>`` -> VI-Properties/VIStructure
    derivation. ``_parse_lvsr_properties`` returns a PLAIN nested
    dict-of-dicts (the parser cannot import graph models) keyed exactly
    like the graph dataclasses: top-level ``lv_version``/``vi_type``/
    ``lock_state``, plus nested ``execution``/``window``/``toolbar``/
    ``instance``/``code`` dicts (``"code"`` feeds ``VIStructure`` -- the
    parser's own key name, unchanged by the graph-layer facet split).
    """

    def _root(self, xml: str) -> ET.Element:
        return ET.fromstring(xml)

    # -- lock_state tri-state -------------------------------------------

    def test_unprotected_is_unlocked(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Library Protected="0" PasswordHash="d41d8cd98f00b204e9800998ecf8427e"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.UNLOCKED.value

    def test_no_library_section_is_unlocked(self) -> None:
        root = self._root("<RSRC><LVSR><Section/></LVSR></RSRC>")
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.UNLOCKED.value

    def test_protected_no_password_element_is_locked(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Library Protected="1" PasswordHash="d41d8cd98f00b204e9800998ecf8427e"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.LOCKED.value

    def test_protected_empty_md5_password_is_locked(self) -> None:
        """A <Password Hash> that IS present but is the MD5-of-empty
        placeholder is a stubbed/no-password case, not a real password."""
        root = self._root(
            "<RSRC>"
            "<LVSR><Section>"
            '<Library Protected="1" PasswordHash="d41d8cd98f00b204e9800998ecf8427e"/>'
            "</Section></LVSR>"
            '<BDPW><Section><Password Hash="d41d8cd98f00b204e9800998ecf8427e"/>'
            "</Section></BDPW>"
            "</RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.LOCKED.value

    def test_protected_all_zero_password_is_locked(self) -> None:
        root = self._root(
            "<RSRC>"
            "<LVSR><Section>"
            '<Library Protected="1" PasswordHash="00000000000000000000000000000000"/>'
            "</Section></LVSR>"
            '<BDPW><Section><Password Hash="00000000000000000000000000000000"/>'
            "</Section></BDPW>"
            "</RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.LOCKED.value

    def test_protected_real_password_is_password_protected(self) -> None:
        root = self._root(
            "<RSRC>"
            "<LVSR><Section>"
            '<Library Protected="1" PasswordHash="d41d8cd98f00b204e9800998ecf8427e"/>'
            "</Section></LVSR>"
            '<BDPW><Section><Password Hash="2a1788d8f1f44bf0703a15d16332b4df"/>'
            "</Section></BDPW>"
            "</RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.PASSWORD_PROTECTED.value

    def test_libn_library_never_mistaken_for_lvsr_library(self) -> None:
        """The <LIBN> <Library> (owning-library NAME text, no Protected
        attribute) must never be mistaken for the LVSR Protection element."""
        root = self._root(
            "<RSRC>"
            "<LIBN><Section><Library>SomeLib.lvlib</Library></Section></LIBN>"
            "<LVSR><Section/></LVSR>"
            "</RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.UNLOCKED.value

    # -- top-level scalars -------------------------------------------

    def test_version_format(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Version Major="21" Minor="0" Bugfix="0" Stage="release" Build="0"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["lv_version"] == "21.0.0"

    def test_no_version_is_none(self) -> None:
        root = self._root("<RSRC><LVSR><Section/></LVSR></RSRC>")
        result = _parse_lvsr_properties(root)
        assert result["lv_version"] is None

    def test_defaults_when_lvsr_absent(self) -> None:
        root = self._root("<RSRC/>")
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.UNLOCKED.value
        assert result["lv_version"] is None
        assert result["vi_type"] is None
        assert result["execution"]["reentrant"] is False
        assert result["execution"]["priority"] is None
        assert result["execution"]["preferred_system"] is None
        assert result["instance"]["is_system_vi"] is False

    # -- execution / execution2 / instrument --------------------------

    def test_execution_fields(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Execution IsReentrant="1" Priority="2" PrefExecSyst="-1"/>'
            '<Execution2 SystemVI="1"/>'
            '<Instrument Type="Control"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["execution"]["reentrant"] is True
        assert result["execution"]["priority"] == 2
        assert result["execution"]["preferred_system"] == -1
        assert result["instance"]["is_system_vi"] is True
        assert result["vi_type"] == "Control"

    def test_execution2_and_instrument_extra_fields(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Execution PooledReentrancy="1" IsSubroutine="1" RunOnOpen="1" '
            'ShowFPOnLoad="1" ShowFPOnCall="1" CloseAfterCall="1" '
            'AllowAutoPrealloc="1"/>'
            '<Execution2 ShouldInline="1" InlinableDiagram="1" '
            'DefaultErrorHandling="1" AlwaysCallsParent="1" '
            'ShowPolySelector="1" HideInstanceVICaption="1" '
            'DrawInstanceIcon="1" RemotePanel="1" SourceOnly="1" '
            'InstanceVI="1"/>'
            '<Instrument DebugCapable="1" PrintAfterExec="1"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        execution = result["execution"]
        assert execution["reentrancy_pooled"] is True
        assert execution["is_subroutine"] is True
        assert execution["run_when_opened"] is True
        assert execution["show_fp_when_loaded"] is True
        assert execution["show_fp_when_called"] is True
        assert execution["close_fp_after_call"] is True
        assert execution["auto_preallocate_arrays"] is True
        assert execution["inline"] is True
        assert execution["inlinable"] is True
        assert execution["auto_error_handling"] is True
        assert execution["always_calls_parent"] is True
        assert execution["allow_debugging"] is True
        assert execution["print_after_exec"] is True
        instance = result["instance"]
        assert instance["show_poly_selector"] is True
        assert instance["hide_instance_caption"] is True
        assert instance["draw_instance_icon"] is True
        assert instance["remote_panel"] is True
        code = result["code"]
        assert code["source_only"] is True
        assert code["is_instance_vi"] is True

    # -- window / toolbar ----------------------------------------------

    def test_window_and_toolbar_fields(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<FrontPanel ShowTitleBar="1" ShowMenuBar="1" ToolBarVisible="1" '
            'ShowScrollBar="3" AutoCenter="1" SizeToScreen="1" '
            'NoRuntimePopUp="1" ScaleProportn="1" MarkReturnBtn="1"/>'
            '<Flags0C AutoHndlMenus="1"/>'
            '<Flags12 WndCanClose="0" WndCanResize="0" WndCanMinimize="0" '
            'WndTransparent="1"/>'
            '<ButtonsHidden RunButton="1" AbortButton="1" FreeRunButton="1" '
            'ViBhBit1="1"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        window = result["window"]
        assert window["show_title_bar"] is True
        assert window["show_menu_bar"] is True
        assert window["show_toolbar"] is True
        assert window["show_scrollbar"] == 3
        assert window["auto_center"] is True
        assert window["size_to_screen"] is True
        assert window["no_runtime_popup_menu"] is True
        assert window["scale_with_window"] is True
        assert window["mark_return_button"] is True
        assert window["auto_handle_menus"] is True
        assert window["can_close"] is False
        assert window["can_resize"] is False
        assert window["can_minimize"] is False
        assert window["transparent"] is True
        toolbar = result["toolbar"]
        assert toolbar["hide_run_button"] is True
        assert toolbar["hide_abort_button"] is True
        assert toolbar["hide_free_run_button"] is True
        # Only the 3 named attrs -- ViBhBit1 (an unnamed heap bit) and the
        # other ViBhBitN's are deliberately never captured.
        assert set(toolbar) == {
            "hide_run_button", "hide_abort_button", "hide_free_run_button",
        }

    def test_window_defaults_when_front_panel_absent(self) -> None:
        root = self._root("<RSRC><LVSR><Section/></LVSR></RSRC>")
        result = _parse_lvsr_properties(root)
        assert result["window"]["show_scrollbar"] is None
        assert result["window"]["show_title_bar"] is False

    # -- code / structure (VIStructure) ---------------------------------

    def test_structure_kind_and_health_fields(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Execution TypeDefVI="1" StrictTypeDefVI="1" DynamicDispatch="1" '
            'HasNoBD="1" BadNode="1" BadSubVI="1" BadSubVILink="1" '
            'BadCompile="1" BrokenPolyVI="1"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        code = result["code"]
        assert code["is_typedef"] is True
        assert code["is_strict_typedef"] is True
        assert code["dynamic_dispatch"] is True
        assert code["has_no_block_diagram"] is True
        assert code["bad_node"] is True
        assert code["bad_subvi"] is True
        assert code["bad_subvi_link"] is True
        assert code["bad_compile"] is True
        assert code["broken_poly"] is True

    def test_structure_defaults_when_execution_absent(self) -> None:
        root = self._root("<RSRC><LVSR><Section/></LVSR></RSRC>")
        result = _parse_lvsr_properties(root)
        code = result["code"]
        assert code["is_typedef"] is False
        assert code["has_no_block_diagram"] is False
        assert code["bad_node"] is False

    def test_vistructure_is_broken_from_synthetic_dict(self) -> None:
        """VIStructure.is_broken (a derived property, not a parser field)
        against the parser's plain dict, built the same way graph.loading
        does -- see _build_vi_structure."""
        root = self._root(
            '<RSRC><LVSR><Section><Execution BadCompile="1"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        struct = VIStructure(**result["code"])
        assert struct.bad_compile is True
        assert struct.is_broken is True

        clean_root = self._root("<RSRC><LVSR><Section/></LVSR></RSRC>")
        clean_struct = VIStructure(**_parse_lvsr_properties(clean_root)["code"])
        assert clean_struct.is_broken is False


class TestParserGraphKeyDrift:
    """Drift guard: ``graph.loading._build_vi_properties``/
    ``_build_vi_structure`` splat ``_parse_lvsr_properties``'s nested dicts
    straight into ``ExecutionProps(**execution)``/``WindowProps(**window)``/
    ``ToolbarProps(**toolbar)``/``InstanceProps(**instance)``/
    ``VIStructure(**code)`` -- an unknown key raises ``TypeError``, but only
    for whichever real VI happens to populate the renamed key, so a parser-
    side rename (or a dataclass-side rename with the parser left behind) can
    sit undetected until some corpus VI trips it. Assert the KEY SETS line up
    directly, so a rename fails HERE, on an empty/default-only XML, every
    run -- not only when a real VI's XML happens to populate the drifted
    field.
    """

    def _groups(self) -> dict[str, Any]:
        root = ET.fromstring("<RSRC><LVSR><Section/></LVSR></RSRC>")
        return _parse_lvsr_properties(root)

    def test_execution_keys_match_executionprops_fields(self) -> None:
        result = self._groups()
        assert set(result["execution"]) <= {f.name for f in fields(ExecutionProps)}

    def test_window_keys_match_windowprops_fields(self) -> None:
        result = self._groups()
        assert set(result["window"]) <= {f.name for f in fields(WindowProps)}

    def test_toolbar_keys_match_toolbarprops_fields(self) -> None:
        result = self._groups()
        assert set(result["toolbar"]) <= {f.name for f in fields(ToolbarProps)}

    def test_instance_keys_match_instanceprops_fields(self) -> None:
        result = self._groups()
        assert set(result["instance"]) <= {f.name for f in fields(InstanceProps)}

    def test_code_keys_match_vistructure_fields(self) -> None:
        result = self._groups()
        assert set(result["code"]) <= {f.name for f in fields(VIStructure)}


# ---------------------------------------------------------------------------
# describe.py rendering -- synthetic VIContext (no corpus/graph needed: the
# render functions only read ctx.properties / ctx.structure).
# ---------------------------------------------------------------------------


class TestDescribeRendering:
    def test_describe_properties_groups_and_omits_defaults(self) -> None:
        from lvkit.graph.describe import _describe_properties

        ctx = VIContext(
            name="x.vi",
            properties=VIProperties(
                lv_version="21.0.0",
                vi_type="Control",
                lock_state=LockState.LOCKED,
            ),
        )
        lines = _describe_properties(ctx)
        text = "\n".join(lines)
        assert "## Properties" in text
        assert "lock_state: locked" in text
        assert "lv_version: 21.0.0" in text
        # No group header for execution/window/toolbar/instance -- all-default.
        assert "execution:" not in text
        assert "window:" not in text

    def test_describe_properties_shows_only_set_flags(self) -> None:
        from lvkit.graph.describe import _describe_properties

        ctx = VIContext(
            name="x.vi",
            properties=VIProperties(
                execution=ExecutionProps(reentrant=True, priority=1),
            ),
        )
        lines = _describe_properties(ctx)
        text = "\n".join(lines)
        assert "execution:" in text
        assert "reentrant: True" in text
        assert "priority: 1" in text
        # reentrancy_pooled defaults False -- must not be dumped.
        assert "reentrancy_pooled" not in text

    def test_describe_structure_shows_broken_and_is_broken(self) -> None:
        from lvkit.graph.describe import _describe_structure

        ctx = VIContext(
            name="x.vi",
            structure=VIStructure(bad_compile=True, is_typedef=True),
        )
        lines = _describe_structure(ctx)
        text = "\n".join(lines)
        assert "## Structure" in text
        assert "is_typedef: True" in text
        assert "bad_compile: True" in text
        assert "is_broken: True" in text

    def test_describe_structure_none_when_all_default(self) -> None:
        from lvkit.graph.describe import _describe_structure

        ctx = VIContext(name="x.vi")
        lines = _describe_structure(ctx)
        text = "\n".join(lines)
        assert "## Structure" in text
        assert "(none)" in text
        assert "is_broken" not in text


# ---------------------------------------------------------------------------
# Real-corpus end-to-end tests
# ---------------------------------------------------------------------------

PASSWORD_PROTECTED_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Built Project Integration/"
    "VITester_Item_Init.vi"
)
LOCKED_VI = Path(
    ".lvkit/cache/samples/LabVIEW-OOP-Classes/Utility/Database/"
    "Database_UUT_class/utils/Database_UUT_New.vi"
)
UNLOCKED_VI = Path(
    ".lvkit/cache/samples/lv-flex-channel-examples/DAQmx AO/DAQ AO.vi"
)
SUBROUTINE_VI = Path(
    ".lvkit/cache/samples/JKI-EasyXML/Source/Fast Parser/Get Children.vi"
)
HIDDEN_TOOLBAR_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Project API/Launch VI Tester.vi"
)


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"Sample not available: {path}")


def _context_for(path: Path) -> VIContext:
    g = InMemoryVIGraph()
    g.load_vi(str(path), mode=LoadMode.NONE)
    vi_name = g.resolve_vi_name(path.name)
    return g.get_vi_context(vi_name)


class TestVIPropertiesCorpus:
    def test_password_protected_vi(self) -> None:
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        props = _context_for(PASSWORD_PROTECTED_VI).properties
        assert props.lock_state == LockState.PASSWORD_PROTECTED

    def test_locked_vi_no_password(self) -> None:
        _skip_if_missing(LOCKED_VI)
        props = _context_for(LOCKED_VI).properties
        assert props.lock_state == LockState.LOCKED

    def test_unlocked_vi(self) -> None:
        _skip_if_missing(UNLOCKED_VI)
        props = _context_for(UNLOCKED_VI).properties
        assert props.lock_state == LockState.UNLOCKED

    def test_reentrant_and_lv_version_parse(self) -> None:
        """Regression guard: reentrant + lv_version parse to real, non-default
        values on a real sample VI (not just the dataclass default)."""
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        props = _context_for(PASSWORD_PROTECTED_VI).properties
        assert props.execution.reentrant is True
        assert props.lv_version == "9.0.0"
        assert props.vi_type == "Control"

    def test_subroutine_vi(self) -> None:
        """A real subroutine VI -- VI Properties -> Execution priority ==
        'subroutine' (LVSR IsSubroutine="1")."""
        _skip_if_missing(SUBROUTINE_VI)
        props = _context_for(SUBROUTINE_VI).properties
        assert props.execution.is_subroutine is True

    def test_hidden_toolbar_buttons_vi(self) -> None:
        """A real VI with Abort/Free-Run hidden but Run NOT hidden --
        exercises per-button granularity, not just 'any flag set'."""
        _skip_if_missing(HIDDEN_TOOLBAR_VI)
        props = _context_for(HIDDEN_TOOLBAR_VI).properties
        assert props.toolbar.hide_abort_button is True
        assert props.toolbar.hide_free_run_button is True
        assert props.toolbar.hide_run_button is False

    def test_describe_shows_properties(self) -> None:
        """describe_vi's ## Properties section faithfully surfaces lock_state
        -- the CLI/MCP-visible text path (graph/describe.py)."""
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        from lvkit.graph.describe import describe_vi

        g = InMemoryVIGraph()
        g.load_vi(str(PASSWORD_PROTECTED_VI), mode=LoadMode.NONE)
        vi_name = g.resolve_vi_name(PASSWORD_PROTECTED_VI.name)
        text = describe_vi(g, vi_name)
        assert "## Properties" in text
        assert "lock_state: password_protected" in text
        assert "## Structure" in text

    def test_get_context_json_shows_properties_and_structure(self) -> None:
        """netlist_to_dict (the MCP get_context tool's JSON shape) carries
        the FULL nested properties structure through, plus a SEPARATE
        top-level ``structure`` key (VIStructure is a sibling facet, never
        nested under ``properties``)."""
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        from lvkit.graph.netlist import build_netlist, netlist_to_dict

        g = InMemoryVIGraph()
        g.load_vi(str(PASSWORD_PROTECTED_VI), mode=LoadMode.NONE)
        vi_name = g.resolve_vi_name(PASSWORD_PROTECTED_VI.name)
        d = netlist_to_dict(build_netlist(g, vi_name))
        assert d["properties"]["lock_state"] == "password_protected"
        assert d["properties"]["execution"]["reentrant"] is True
        assert "window" in d["properties"]
        assert "toolbar" in d["properties"]
        assert "instance" in d["properties"]
        assert "code" not in d["properties"]  # moved to the structure facet
        assert "structure" in d
        assert "is_broken" in d["structure"]
        assert d["structure"]["is_broken"] is False
