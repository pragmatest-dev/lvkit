"""``lvkit setup --git-textconv`` attributes every VI file kind."""

import subprocess
from pathlib import Path

from lvkit.cli import _setup_git_textconv
from lvkit.vi_files import VI_EXTENSIONS


def test_textconv_attributes_each_vi_extension(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitattributes").write_text("*.png binary", encoding="utf-8")

    assert _setup_git_textconv(tmp_path) == 0
    assert _setup_git_textconv(tmp_path) == 0  # idempotent

    lines = (tmp_path / ".gitattributes").read_text(encoding="utf-8").splitlines()
    assert lines == ["*.png binary", *(f"*{e} diff=lvkit" for e in VI_EXTENSIONS)]
