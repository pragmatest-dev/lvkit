"""A name is never a lookup key inside the graph: a class or typedef is the file
its type's ``file_key`` names, a VI is its path, and a name a person types is
answered by the index (``index/query.py``), which returns the path."""

from __future__ import annotations

import re
from pathlib import Path

GRAPH = Path(__file__).resolve().parent.parent / "src" / "lvkit" / "graph"
FORBIDDEN = (
    "_name_to_keys",
    "_qname_to_keys",
    "_qualified_aliases",
    "resolve_vi_name",
    "key_for_user_input",
    "_dep_key_for_ref",
    "_pick_vi_key",
)


def test_no_name_lookup_exists_in_the_graph() -> None:
    offenders = [
        f"{path.relative_to(GRAPH).as_posix()}: {name}"
        for path in sorted(GRAPH.rglob("*.py"))
        for name in FORBIDDEN
        if re.search(rf"\b{name}\b", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
