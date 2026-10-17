"""`lvkit structure` accepting a .lvproj (#32).

Two layers: a corpus-backed case (skipped when the local-only samples aren't
present) that exercises real .lvclass parsing through the project's member
list, and a self-contained case that builds a synthetic .lvproj to pin down
the membership rules (explicit list, external/alias members excluded,
class-directory VIs not double-counted as standalone) without needing any
LabVIEW binaries.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PureWindowsPath

import pytest

from lvkit.structure import (
    discover_structure_from_lvproj,
    resolve_item_url,
    structure_vi_paths,
)

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


def _write_project(path: Path, targets: str) -> None:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Project Type="Project" LVVersion="20008000">\n'
        f"{targets}\n"
        "</Project>\n",
        encoding="utf-8",
    )


class TestTargetNesting:
    """VIs declared under a non-My-Computer target (RT CompactRIO, FPGA Target,
    nested chassis/folders) are project members like any other (#131)."""

    def test_vis_under_rt_and_fpga_targets_are_listed(self, tmp_path: Path) -> None:
        for rel in ("rt/Main.vi", "fpga/Top.vi", "fpga/Sub/Helper.vi", "pc/Host.vi"):
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_bytes(b"")
        proj = tmp_path / "P.lvproj"
        _write_project(
            proj,
            '<Item Name="My Computer" Type="My Computer">\n'
            '<Item Name="Host.vi" Type="VI" URL="pc/Host.vi"/>\n'
            "</Item>\n"
            '<Item Name="cRIO-9030" Type="RT CompactRIO">\n'
            '<Property Name="alias.name" Type="Str">cRIO-9030</Property>\n'
            '<Item Name="Main.vi" Type="VI" URL="rt/Main.vi"/>\n'
            '<Item Name="Chassis" Type="RT Chassis">\n'
            '<Item Name="FPGA Target" Type="FPGA Target">\n'
            '<Item Name="Top.vi" Type="VI" URL="fpga/Top.vi"/>\n'
            '<Item Name="Helpers" Type="Folder">\n'
            '<Item Name="Helper.vi" Type="VI" URL="fpga/Sub/Helper.vi"/>\n'
            "</Item>\n</Item>\n</Item>\n</Item>",
        )

        structure = discover_structure_from_lvproj(proj)

        assert sorted(structure["standalone_vis"]) == sorted(
            [
                str(Path("pc/Host.vi")),
                str(Path("rt/Main.vi")),
                str(Path("fpga/Top.vi")),
                str(Path("fpga/Sub/Helper.vi")),
            ]
        )

    def test_classes_and_libraries_under_a_target_are_listed(
        self, tmp_path: Path
    ) -> None:
        lib = tmp_path / "Lib.lvlib"
        lib.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Library LVVersion="20008000"></Library>\n',
            encoding="utf-8",
        )
        proj = tmp_path / "P.lvproj"
        _write_project(
            proj,
            '<Item Name="RT" Type="RT CompactRIO">\n'
            '<Item Name="Lib.lvlib" Type="Library" URL="Lib.lvlib"/>\n'
            "</Item>",
        )

        structure = discover_structure_from_lvproj(proj)

        assert [lb["path"] for lb in structure["libraries"]] == ["Lib.lvlib"]


class TestLibraryAndClassOnlyProject:
    """A project that declares only a library and a class under a target still
    counts and lists every VI those files contain (#131)."""

    def _project(self, tmp_path: Path) -> Path:
        (tmp_path / "lib").mkdir()
        (tmp_path / "cls").mkdir()
        for rel in ("lib/a.vi", "lib/b.vi", "cls/m.vi"):
            (tmp_path / rel).write_bytes(b"")
        (tmp_path / "lib" / "Lib.lvlib").write_text(
            '<?xml version="1.0"?><Library>'
            '<Item Name="a.vi" Type="VI" URL="a.vi"/>'
            '<Item Name="Private" Type="Folder">'
            '<Item Name="b.vi" Type="VI" URL="b.vi"/></Item></Library>',
            encoding="utf-8",
        )
        (tmp_path / "cls" / "C.lvclass").write_text(
            '<?xml version="1.0"?><LVClass>'
            '<Item Name="m.vi" Type="VI" URL="m.vi"/></LVClass>',
            encoding="utf-8",
        )
        proj = tmp_path / "P.lvproj"
        _write_project(
            proj,
            '<Item Name="cRIO" Type="RT CompactRIO">\n'
            '<Item Name="Lib.lvlib" Type="Library" URL="lib/Lib.lvlib"/>\n'
            '<Item Name="C.lvclass" Type="LVClass" URL="cls/C.lvclass"/>\n'
            "</Item>",
        )
        return proj

    def test_member_vis_are_counted_and_listed(self, tmp_path: Path) -> None:
        structure = discover_structure_from_lvproj(self._project(tmp_path))
        assert structure["standalone_vis"] == []
        assert structure_vi_paths(structure) == ["cls/m.vi", "lib/a.vi", "lib/b.vi"]

    def test_cli_text_and_json_report_the_total(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lvkit.cli import main

        def run(*args: str) -> int:
            monkeypatch.setattr(sys, "argv", ["lvkit", *args])
            return main()

        proj = self._project(tmp_path)
        assert run("structure", str(proj)) == 0
        text = capsys.readouterr().out
        assert "Libraries: 1 (2 VIs)" in text
        assert "Classes: 1 (1 VIs)" in text
        assert "Total VIs: 3" in text

        assert run("structure", str(proj), "--json") == 0
        data = json.loads(capsys.readouterr().out)
        assert data["vi_count"] == 3
        assert data["vis"] == ["cls/m.vi", "lib/a.vi", "lib/b.vi"]

    def test_directory_scan_agrees_with_the_project_file(self, tmp_path: Path) -> None:
        from lvkit.structure import discover_project_structure

        proj = self._project(tmp_path)
        from_proj = structure_vi_paths(discover_structure_from_lvproj(proj))
        from_dir = structure_vi_paths(discover_project_structure(tmp_path))
        assert from_proj == from_dir


class TestAbsoluteItemUrls:
    def test_windows_drive_url_names_the_drive_path(self) -> None:
        proj_dir = Path("C:/work/proj")
        resolved = resolve_item_url(proj_dir, "/D:/Shared/x.vi", windows=True)
        assert resolved.as_posix() == "D:/Shared/x.vi"
        # Joining it onto the project folder is what built a non-existent path.
        assert str(PureWindowsPath("C:/work/proj") / "/D:/Shared/x.vi") == (
            "C:\\D:\\Shared\\x.vi"
        )

    def test_relative_url_is_relative_to_the_project(self) -> None:
        proj_dir = Path("/work/proj")
        assert resolve_item_url(proj_dir, "../lib/x.vi", windows=True) == (
            proj_dir / "../lib/x.vi"
        )

    def test_off_windows_a_drive_url_is_left_rooted(self) -> None:
        proj_dir = Path("/work/proj")
        resolved = resolve_item_url(proj_dir, "/D:/Shared/x.vi", windows=False)
        assert resolved.as_posix() == "/D:/Shared/x.vi"
        assert not resolved.exists()

    def test_vilib_alias_is_not_a_drive(self) -> None:
        proj_dir = Path("/work/proj")
        url = "/<vilib>/dep/Dep.vi"
        assert resolve_item_url(proj_dir, url, windows=True) == proj_dir / url
