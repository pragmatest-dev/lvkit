"""LinkSavePathRef symbolic roots resolve against their root, never the caller."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.parser.models import ParsedDependencyRef, is_symbolic_root

CALLER = Path("/proj/Providers/Item_OnPopupMenu.vi")


@pytest.mark.parametrize("root", ["<resource>", "<extravilib>", "<vilib>"])
def test_unconfigured_symbolic_root_never_joins_the_caller_path(root: str):
    ref = ParsedDependencyRef(
        name="mxLvGetTarget.vi",
        path_tokens=[root, "Framework", "Providers", "API", "mxLvGetTarget.vi"],
    )
    assert ref.resolve_against(CALLER) is None
    assert ref.get_relative_path() == "Framework/Providers/API/mxLvGetTarget.vi"


def test_configured_vilib_root_still_resolves(tmp_path: Path):
    ref = ParsedDependencyRef(name="A.vi", path_tokens=["<vilib>", "Utility", "A.vi"])
    assert ref.resolve_against(CALLER, vilib_root=tmp_path) == (
        tmp_path / "Utility" / "A.vi"
    ).resolve()


def test_caller_relative_path_is_unchanged():
    ref = ParsedDependencyRef(name="B.vi", path_tokens=["", "Support", "B.vi"])
    assert ref.resolve_against(CALLER) == Path("/proj/Providers/Support/B.vi")


@pytest.mark.parametrize(
    "token,expected",
    [("<resource>", True), ("<vilib>", True), ("", False), ("<>", False), ("a", False)],
)
def test_is_symbolic_root(token: str, expected: bool):
    assert is_symbolic_root(token) is expected
