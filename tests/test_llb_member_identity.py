"""A VI inside an ``.llb`` is the file at ``<llb path>/<member>``, the path
LabVIEW records for it; its bytes are read from the extracted copy. Its key,
the folder its relative paths start from and every label a user sees are that
path, never the cache folder."""

from __future__ import annotations

from pathlib import Path

from lvkit.extractor import extract_llb
from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.graph.llb_members import file_is_present, llb_member_copy, vi_bytes_file
from lvkit.llb_origin import llb_container_of
from lvkit.run_report import RunSettings
from tests.test_llb_stream_lifetime import _write_archive

_VI = Path(__file__).parent / "corpus/issues/36/bundle-unbundle-names.vi"


def _archive(tmp_path: Path) -> Path:
    return _write_archive(tmp_path / "lib", "UCRF", {"member.vi": _VI.read_bytes()})


def test_a_member_is_keyed_by_its_path_inside_the_archive(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    graph = InMemoryVIGraph()
    graph.load_llb(archive, LoadMode.MINIMAL)
    identity = str((archive / "member.vi").resolve())
    assert graph.list_vis() == [identity]
    copy = extract_llb(archive) / "member.vi"
    assert graph._source_paths[identity] == copy
    assert graph.vi_label(identity) == "members.llb/member.vi"
    # Loading the archive again finds the same member, not a second one.
    graph.load_llb(archive, LoadMode.MINIMAL)
    assert graph.list_vis() == [identity]


def test_the_member_path_reads_the_extracted_copy(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    member = archive / "member.vi"
    copy = extract_llb(archive) / "member.vi"
    assert llb_member_copy(member) == copy
    assert vi_bytes_file(member) == copy
    assert file_is_present(member)
    assert llb_container_of(member) == archive
    assert llb_container_of(copy) == archive.resolve()
    assert llb_member_copy(archive / "absent.vi") is None
    assert not file_is_present(archive / "absent.vi")


def test_a_member_also_on_the_search_path_stays_two_files(tmp_path: Path) -> None:
    """The same VI saved beside the archive is another file: its own path is its
    key, the archive member keeps the archive path."""
    archive = _archive(tmp_path)
    loose = tmp_path / "lib" / "member.vi"
    loose.write_bytes(_VI.read_bytes())
    graph = InMemoryVIGraph()
    graph.load_llb(archive, LoadMode.MINIMAL)
    graph.load_vi(loose, LoadMode.MINIMAL)
    assert sorted(graph.list_vis()) == sorted(
        [str((archive / "member.vi").resolve()), str(loose.resolve())]
    )
    labels = RunSettings(root=tmp_path)
    assert labels.label(archive / "member.vi") == "lib/members.llb/member.vi"
    assert labels.label(loose) == "lib/member.vi"
