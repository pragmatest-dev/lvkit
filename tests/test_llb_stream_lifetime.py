"""Exercise deferred reads with actual pylabview resource archives."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from lvkit import extractor


def _write_archive(
    directory: Path, block_ident: str, members: dict[str, bytes]
) -> Path:
    """Build a synthetic LLB; the payloads need not be executable VIs."""
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / "members.llb"
    root = ET.Element("RSRC", FormatVersion="3", Type="LVAR")
    block = ET.SubElement(root, block_ident)
    for index, (name, payload) in enumerate(members.items()):
        (directory / name).write_bytes(payload)
        ET.SubElement(
            block,
            "Section",
            Index=str(index),
            Name=name,
            Format="bin",
            File=name,
        )
    options = extractor._make_read_po(
        xml=str(directory / "source.xml"),
        rsrc=str(archive),
        filebase=archive.stem,
        keep_names=True,
        raw_connectors=False,
    )
    resource = extractor._lv_rsrc.VI(options, xml_root=root)
    with archive.open("wb") as output:
        resource.saveRSRC(output)
    return archive


_MEMBERS = {
    "first.vi": b"first resource payload",
    "second.vi": b"second\x00resource\xff" * 17,
    "third.vi": bytes(range(256)) * 3,
}


@pytest.mark.parametrize("block_ident", ["UCRF", "CPRF", "ZCRF"])
@pytest.mark.parametrize("section", [0, 1, 2])
def test_member_reads_after_open_returns(tmp_path, block_ident, section):
    archive = _write_archive(tmp_path, block_ident, _MEMBERS)
    resource = extractor._open_llb_vi(archive)

    # Section zero is eagerly loaded. Later sections require the source stream.
    block = resource.get(block_ident)
    assert block.getData(section_num=section).read() == list(_MEMBERS.values())[section]
    assert resource.src_fname == str(archive)


@pytest.mark.parametrize("block_ident", ["UCRF", "CPRF", "ZCRF"])
def test_two_open_archives_keep_independent_deferred_data(tmp_path, block_ident):
    first_path = _write_archive(tmp_path / "one", block_ident, _MEMBERS)
    second_members = {name: payload[::-1] for name, payload in _MEMBERS.items()}
    second_path = _write_archive(tmp_path / "two", block_ident, second_members)
    first = extractor._open_llb_vi(first_path)
    second = extractor._open_llb_vi(second_path)

    for index in (2, 0, 1):
        assert (
            second.get(block_ident).getData(section_num=index).read()
            == list(second_members.values())[index]
        )
        assert (
            first.get(block_ident).getData(section_num=index).read()
            == list(_MEMBERS.values())[index]
        )


@pytest.mark.parametrize("block_ident", ["UCRF", "CPRF", "ZCRF"])
def test_fresh_extraction_contains_every_member(tmp_path, block_ident):
    archive = _write_archive(tmp_path, block_ident, _MEMBERS)
    output = extractor.extract_llb(archive)

    assert {path.name: path.read_bytes() for path in output.glob("*.vi")} == _MEMBERS
    assert (output / ".extracted").is_file()
    assert extractor.extract_llb(archive) == output


def test_invalid_archive_retains_open_error(tmp_path):
    archive = tmp_path / "invalid.llb"
    archive.write_bytes(b"not a resource archive")

    with pytest.raises(RuntimeError, match="Failed to open LLB"):
        extractor.extract_llb(archive)
