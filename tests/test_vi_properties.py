"""Tests for VI-Properties parsing (Protection/Execution) from the main XML's
``<LVSR>`` block -- ``graph.models.VIProperties``/``LockState``.

``TestParseLvsrProperties`` exercises the pure XML-derivation logic
(``parser.metadata._parse_lvsr_properties``) against synthetic XML, matching
the pattern in ``TestParseViMetadata`` (test_parser.py) -- no corpus needed,
covers the lock_state tri-state edge cases precisely.

``TestVIPropertiesCorpus`` loads real sample VIs end-to-end (through
``InMemoryVIGraph`` -> ``VIContext.properties``) and asserts against
ground-truth lock states verified directly in the extracted XML:

- password-protected: JKI-VI-Tester's ``VITester_Item_Init.vi`` (Built
  Project Integration) -- ``<Library Protected="1">`` + a real (non-empty,
  non-placeholder) ``<Password Hash>``.
- locked (no password): LabVIEW-OOP-Classes' ``Database_UUT_New.vi`` --
  ``<Library Protected="1">`` with no ``<Password>`` element at all.
- unlocked: lv-flex-channel-examples' ``DAQmx AO/DAQ AO.vi`` -- no
  ``Protected`` library section.

Skipped (not failed) when the relevant sample isn't present on disk,
consistent with other sample-backed tests in this repo (e.g.
test_class_parent_linkinfo.py).
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.graph.models import LockState
from lvkit.parser.metadata import _parse_lvsr_properties


class TestParseLvsrProperties:
    """Unit tests for the pure ``<LVSR>`` -> VI-Properties derivation."""

    def _root(self, xml: str) -> ET.Element:
        return ET.fromstring(xml)

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

    def test_execution_fields(self) -> None:
        root = self._root(
            "<RSRC><LVSR><Section>"
            '<Execution IsReentrant="1" Priority="2" PrefExecSyst="-1"/>'
            '<Execution2 SystemVI="1"/>'
            '<Instrument Type="Control"/>'
            "</Section></LVSR></RSRC>"
        )
        result = _parse_lvsr_properties(root)
        assert result["reentrant"] is True
        assert result["execution_priority"] == 2
        assert result["preferred_exec_system"] == -1
        assert result["is_system_vi"] is True
        assert result["vi_type"] == "Control"

    def test_defaults_when_lvsr_absent(self) -> None:
        root = self._root("<RSRC/>")
        result = _parse_lvsr_properties(root)
        assert result["lock_state"] == LockState.UNLOCKED.value
        assert result["reentrant"] is False
        assert result["execution_priority"] is None
        assert result["preferred_exec_system"] is None
        assert result["is_system_vi"] is False
        assert result["vi_type"] is None
        assert result["lv_version"] is None


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


def _skip_if_missing(path: Path) -> None:
    if not path.exists():
        pytest.skip(f"Sample not available: {path}")


def _properties_for(path: Path):
    g = InMemoryVIGraph()
    g.load_vi(str(path), mode=LoadMode.NONE)
    vi_name = g.resolve_vi_name(path.name)
    return g.get_vi_context(vi_name).properties


class TestVIPropertiesCorpus:
    def test_password_protected_vi(self) -> None:
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        props = _properties_for(PASSWORD_PROTECTED_VI)
        assert props.lock_state == LockState.PASSWORD_PROTECTED

    def test_locked_vi_no_password(self) -> None:
        _skip_if_missing(LOCKED_VI)
        props = _properties_for(LOCKED_VI)
        assert props.lock_state == LockState.LOCKED

    def test_unlocked_vi(self) -> None:
        _skip_if_missing(UNLOCKED_VI)
        props = _properties_for(UNLOCKED_VI)
        assert props.lock_state == LockState.UNLOCKED

    def test_reentrant_and_lv_version_parse(self) -> None:
        """Regression guard: reentrant + lv_version parse to real, non-default
        values on a real sample VI (not just the dataclass default)."""
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        props = _properties_for(PASSWORD_PROTECTED_VI)
        assert props.reentrant is True
        assert props.lv_version == "9.0.0"

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
        assert "lock: password_protected" in text

    def test_get_context_json_shows_properties(self) -> None:
        """netlist_to_dict (the MCP get_context tool's JSON shape) carries
        properties through."""
        _skip_if_missing(PASSWORD_PROTECTED_VI)
        from lvkit.graph.netlist import build_netlist, netlist_to_dict

        g = InMemoryVIGraph()
        g.load_vi(str(PASSWORD_PROTECTED_VI), mode=LoadMode.NONE)
        vi_name = g.resolve_vi_name(PASSWORD_PROTECTED_VI.name)
        d = netlist_to_dict(build_netlist(g, vi_name))
        assert d["properties"]["lock_state"] == "password_protected"
        assert d["properties"]["reentrant"] is True
