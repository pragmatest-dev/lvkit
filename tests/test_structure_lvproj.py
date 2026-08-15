"""`lvkit structure` accepting a .lvproj (#32).

Two layers: a corpus-backed case (skipped when the local-only samples aren't
present) that exercises real .lvclass parsing through the project's member
list, and a self-contained case that builds a synthetic .lvproj to pin down
the membership rules (explicit list, external/alias members excluded,
class-directory VIs not double-counted as standalone) without needing any
LabVIEW binaries.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.structure import discover_structure_from_lvproj

EXAMPLE_LVPROJ = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Examples/VI Tester Example.lvproj"
)


def test_lvproj_resolves_member_classes_from_corpus() -> None:
    if not EXAMPLE_LVPROJ.exists():
        pytest.skip(f"Sample not available: {EXAMPLE_LVPROJ}")

    structure = discover_structure_from_lvproj(EXAMPLE_LVPROJ)

    class_names = {c["name"] for c in structure["classes"]}
    assert class_names == {"Merge Errors TestCase", "Queue TestCase"}
    # Each class's methods are resolved from its .lvclass (not left empty).
    assert all(c["methods"] for c in structure["classes"])
    # The class method VIs live under the class dirs -> not standalone.
    assert structure["standalone_vis"] == []


def _write_lvproj(path: Path, items: str) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Project Name="P" Type="Project" LVVersion="20008000">\n'
        '<Item Name="My Computer" Type="My Computer">\n'
        f"{items}\n"
        "</Item>\n"
        "</Project>\n",
        encoding="utf-8",
    )


class TestSyntheticLvproj:
    def test_only_on_disk_vis_are_included(self, tmp_path: Path) -> None:
        # A real VI file next to the project, plus a vi.lib alias member that
        # has no file on disk -- the alias is an external dependency and must
        # be excluded, exactly as a directory scan would exclude it.
        (tmp_path / "Top.vi").write_bytes(b"")
        proj = tmp_path / "P.lvproj"
        _write_lvproj(
            proj,
            '<Item Name="Top.vi" Type="VI" URL="Top.vi"/>\n'
            '<Item Name="Dep.vi" Type="VI" URL="/&lt;vilib&gt;/dep/Dep.vi"/>',
        )

        structure = discover_structure_from_lvproj(proj)

        assert structure["standalone_vis"] == ["Top.vi"]
        assert structure["libraries"] == []
        assert structure["classes"] == []

    def test_vi_in_subfolder_keeps_relative_path(self, tmp_path: Path) -> None:
        sub = tmp_path / "Sub"
        sub.mkdir()
        (sub / "Inner.vi").write_bytes(b"")
        proj = tmp_path / "P.lvproj"
        _write_lvproj(proj, '<Item Name="Inner.vi" Type="VI" URL="Sub/Inner.vi"/>')

        structure = discover_structure_from_lvproj(proj)

        assert structure["standalone_vis"] == [str(Path("Sub") / "Inner.vi")]
