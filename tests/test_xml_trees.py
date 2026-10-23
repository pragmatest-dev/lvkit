"""Each of a VI's XML files is parsed once per ``parse_vi``."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import pytest

from lvkit.parser import parse_vi, xml_trees
from lvkit.parser.xml_trees import parsed_xml, xml_scope
from tests.case_structure_fixture import FrameSpec, case_structure_views


@pytest.fixture
def parse_counts(monkeypatch: pytest.MonkeyPatch) -> Counter[Path]:
    """How many times ``ET.parse`` read each file."""
    counts: Counter[Path] = Counter()
    real_parse = ET.parse

    def counting_parse(source, parser=None):
        counts[Path(source).resolve()] += 1
        return real_parse(source, parser)

    monkeypatch.setattr(xml_trees.ET, "parse", counting_parse)
    return counts


def test_parse_vi_parses_each_xml_file_once(
    tmp_path: Path, parse_counts: Counter[Path]
) -> None:
    case_structure_views(
        tmp_path, "integer", [FrameSpec(ranges=((1, 1),))], default_frame=0
    )
    parse_counts.clear()
    parse_vi(bd_xml=tmp_path / "Case_BDHb.xml", main_xml=tmp_path / "Case.xml")
    assert parse_counts == {
        (tmp_path / "Case_BDHb.xml").resolve(): 1,
        (tmp_path / "Case.xml").resolve(): 1,
    }


def test_a_scope_shares_one_root_across_spellings_of_a_path(tmp_path: Path) -> None:
    xml = tmp_path / "a.xml"
    xml.write_text("<root />")
    with xml_scope():
        assert parsed_xml(xml) is parsed_xml(tmp_path / "." / "a.xml")


def test_outside_a_scope_each_call_parses_again(
    tmp_path: Path, parse_counts: Counter[Path]
) -> None:
    xml = tmp_path / "a.xml"
    xml.write_text("<root />")
    assert parsed_xml(xml) is not parsed_xml(xml)
    assert parse_counts[xml.resolve()] == 2


def test_a_scope_ends_with_its_block(
    tmp_path: Path, parse_counts: Counter[Path]
) -> None:
    xml = tmp_path / "a.xml"
    xml.write_text("<root />")
    with xml_scope():
        first = parsed_xml(xml)
    with xml_scope():
        assert parsed_xml(xml) is not first
    assert parse_counts[xml.resolve()] == 2
