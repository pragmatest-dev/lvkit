"""A run reads the catalog of its own project: two MCP calls for projects with
different ``.lvkit/`` catalog additions never see each other's, and a catalog is
built once per ``.lvkit/`` folder."""

from __future__ import annotations

import asyncio
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from lvkit import catalogs
from lvkit.mcp import server as srv
from lvkit.mcp.server.problems import new_run
from lvkit.primitive_resolver import PrimitiveResolver
from lvkit.project_store import init_project_store
from lvkit.vilib_resolver import VILibResolver

_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Build Support"
    "/Package Builder Utilities"
    "/Error Cluster From Error Code__JKI_RIGHT_CLICK_BUILD_SUPPORT.vi"
)
# "Not Equal To 0?" in that VI.
_PRIM_ID = "1116"
_SHIPPED_NAME = "Not Equal To 0?"


def _project(root: Path, name: str) -> Path:
    """A project at ``root`` whose store renames primitive 1116 to ``name``, with
    a copy of the sample VI inside it. Returns the VI."""
    store = init_project_store(root)
    entry = dict(PrimitiveResolver().get_by_id(int(_PRIM_ID)) or {})
    entry["name"] = name
    (store / "primitives.json").write_text(
        json.dumps({"primitives": {_PRIM_ID: entry}}), encoding="utf-8"
    )
    vi = root / _VI.name
    shutil.copy(_VI, vi)
    return vi


def _read_vi(vi: Path) -> str:
    result: dict[str, Any] = asyncio.run(srv.read_vi(str(vi), format="lvnet"))
    return json.dumps(result)


@pytest.mark.needs_samples
@pytest.mark.cold_cache
def test_overlapping_calls_for_two_projects_each_read_their_own_catalog(
    tmp_path: Path,
) -> None:
    if not _VI.exists():
        pytest.skip("JKI-VI-Tester sample corpus not present")
    (tmp_path / "a" / ".git").mkdir(parents=True)
    (tmp_path / "b" / ".git").mkdir(parents=True)
    vi_a = _project(tmp_path / "a", "PROJECT A name")
    vi_b = _project(tmp_path / "b", "PROJECT B name")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(8):
            first = pool.submit(_read_vi, vi_a)
            second = pool.submit(_read_vi, vi_b)
            text_a, text_b = first.result(), second.result()
            assert "PROJECT A name" in text_a
            assert "PROJECT B name" not in text_a
            assert "PROJECT B name" in text_b
            assert "PROJECT A name" not in text_b
            assert _SHIPPED_NAME not in text_a + text_b


def test_a_catalog_is_built_once_per_project_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Path | None] = []
    original = VILibResolver.__init__

    def counting(self: VILibResolver, *args: Any, **kwargs: Any) -> None:
        built.append(kwargs.get("project_data_dir"))
        original(self, *args, **kwargs)

    monkeypatch.setattr(VILibResolver, "__init__", counting)
    monkeypatch.setattr(catalogs, "_vilib", {})
    store = init_project_store(tmp_path)

    first = catalogs.vilib_catalog_for(store)
    again = catalogs.vilib_catalog_for(store.resolve())
    other = catalogs.vilib_catalog_for(None)

    assert first is again
    assert other is not first
    assert built == [store.resolve(), None]


def test_two_mcp_calls_for_one_project_build_its_catalog_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[Path | None] = []
    original = VILibResolver.__init__

    def counting(self: VILibResolver, *args: Any, **kwargs: Any) -> None:
        built.append(kwargs.get("project_data_dir"))
        original(self, *args, **kwargs)

    monkeypatch.setattr(VILibResolver, "__init__", counting)
    monkeypatch.setattr(catalogs, "_vilib", {})
    (tmp_path / ".git").mkdir()
    store = init_project_store(tmp_path)
    vi = tmp_path / "Foo.vi"
    vi.write_text("not a VI")

    for _ in range(2):
        run = new_run(vi)
        assert run.settings.project_store == store
        assert catalogs.vilib_catalog_for(run.settings.project_store)
    assert built == [store.resolve()]


def test_a_store_edit_reaches_the_next_lookup_and_an_unchanged_store_is_kept(
    tmp_path: Path,
) -> None:
    """lvkit and the user write into ``.lvkit/`` during a session (a vi.lib VI's
    terminal layout, a fixed entry): the next lookup builds the catalog again, and
    a store whose files are unchanged keeps the catalog it has."""
    store = init_project_store(tmp_path)
    entries = store / "primitives.json"
    shipped = dict(PrimitiveResolver().get_by_id(int(_PRIM_ID)) or {})

    def write(name: str) -> None:
        entry = {**shipped, "name": name}
        entries.write_text(
            json.dumps({"primitives": {_PRIM_ID: entry}}), encoding="utf-8"
        )

    write("Before")
    first = catalogs.primitive_catalog_for(store)
    assert catalogs.primitive_catalog_for(store) is first
    assert (first.get_by_id(int(_PRIM_ID)) or {})["name"] == "Before"
    write("After, a longer name")
    second = catalogs.primitive_catalog_for(store)
    assert second is not first
    assert (second.get_by_id(int(_PRIM_ID)) or {})["name"] == "After, a longer name"
