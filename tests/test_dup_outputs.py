"""A node whose output NI labels as a pass-through of an input ("dup refnum",
"refnum out", "VISA resource name out", "connection ID out", "queue out") still
gives that input's value when the node fails: its error is the error out and
nothing else about the pass-through changes.

NI's wording, from the public function pages: "returns ... unchanged" (File/Directory
Info, Set Permissions, Get File Position, queue and notifier functions, Recursive
File List), "returns the same value as connection ID" (TCP Read, Write), "a copy of
the VISA resource name" (VISA Read, Write, Flush I/O Buffer), "returns refnum" (Set
File Position), "is the refnum of the file" (the file functions), "dup refnum" (Read
and Write File+) and "returns the open zip file" (Add File to Zip).

Each test builds a tiny diagram, generates its Python and runs it.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from lvkit.primitive_resolver import PrimitiveEntry
from lvkit.vilib_resolver import VIEntry
from tests.helpers import error_cluster_type
from tests.primitive_caller import run_primitive
from tests.test_catalog_files_strings_arrays import (
    ERROR_CLUSTER,
    ZIP,
    _file_list,
)
from tests.test_catalog_visa_primitives import (  # noqa: F401  (the fixture)
    FakeResource,
    fake_pyvisa,
)
from tests.vilib_caller import BOOL, I32, PATH, STRING, U32, primitive, run_catalog_call

ERROR = error_cluster_type()
REF = primitive("Refnum")
DATA = Path(__file__).parent.parent / "src" / "lvkit" / "data"


def test_a_failed_visa_write_still_gives_the_resource_name_out() -> None:
    resource = FakeResource(fail=-1073807339)
    _, result = run_primitive(
        1926,
        [
            (0, "output", ERROR),
            (2, "output", U32),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", STRING),
            (11, "input", REF),
        ],
        {"resource": (11, REF), "data": (10, STRING)},
        {"count": (2, U32), "resource_out": (3, REF), "error": (0, ERROR)},
        {"resource": resource, "data": "x"},
    )
    assert result.error.code == -1073807339
    assert result.resource_out is resource
    assert result.count == 0


def test_a_failed_visa_read_still_gives_the_resource_name_out() -> None:
    resource = FakeResource(fail=-1073807339)
    _, result = run_primitive(
        1925,
        [
            (0, "output", ERROR),
            (1, "output", U32),
            (2, "output", STRING),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", U32),
            (11, "input", REF),
        ],
        {"resource": (11, REF), "count": (10, U32)},
        {"data": (2, STRING), "resource_out": (3, REF), "error": (0, ERROR)},
        {"resource": resource, "count": 4},
    )
    assert result.error.code == -1073807339
    assert result.resource_out is resource
    assert result.data == ""


def _read_text(file_type: object, value: object):
    return run_primitive(
        8070,
        [
            (0, "output", ERROR),
            (1, "output", BOOL),
            (2, "output", STRING),
            (3, "output", REF),
            (8, "input", ERROR),
            (10, "input", I32),
            (11, "input", file_type),
        ],
        {"file": (11, file_type)},
        {"text": (2, STRING), "refnum_out": (3, REF), "error": (0, ERROR)},
        {"file": value},
    )


def test_a_failed_read_from_text_file_still_gives_the_refnum_out(
    tmp_path: Path,
) -> None:
    with (tmp_path / "w.txt").open("w") as handle:
        _, result = _read_text(REF, handle)
        assert result.error.code == 8
        assert result.refnum_out is handle
        assert result.text == ""


def test_a_failed_recursive_file_list_still_gives_the_folder_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing"
    result = _file_list(tmp_path, monkeypatch, missing, continue_on_error=False)
    assert result.error.code == 7
    assert result.folder == missing


def test_a_failed_add_file_to_zip_still_gives_the_zip_file_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with zipfile.ZipFile(tmp_path / "out.zip", "w") as archive:
        _, result = run_catalog_call(
            tmp_path,
            monkeypatch,
            [*ZIP, "Add File to Zip.vi"],
            [
                (0, "output", ERROR_CLUSTER),
                (3, "output", I32),
                (8, "input", ERROR_CLUSTER),
                (9, "input", PATH),
                (10, "input", PATH),
                (11, "input", I32),
            ],
            {"archive": (11, I32), "source": (10, PATH), "inside": (9, PATH)},
            {"zip_out": (3, I32), "error": (0, ERROR_CLUSTER)},
            {
                "archive": archive,
                "source": tmp_path / "missing.txt",
                "inside": Path("a.txt"),
            },
        )
    assert result.error.code == 7
    assert result.zip_out is archive


def test_every_pass_through_names_an_input_of_its_own_entry() -> None:
    """Each ``passes_input`` of the catalogs names an input terminal of its entry."""
    seen = 0
    for path in sorted((DATA / "primitives").glob("*.json")):
        for entry in (
            json.loads(path.read_text(encoding="utf-8")).get("primitives", {}).values()
        ):
            terminals = PrimitiveEntry.model_validate(entry).terminals
            for term in terminals:
                if term.passes_input is None:
                    continue
                seen += 1
                [source] = [t for t in terminals if t.index == term.passes_input]
                assert source.direction == "input", (entry["name"], term.name)
    for path in sorted((DATA / "vilib").glob("*.json")):
        for entry in json.loads(path.read_text(encoding="utf-8")).get("entries", []):
            terminals = VIEntry.model_validate(entry).terminals
            for term in terminals:
                if term.passes_input is None:
                    continue
                seen += 1
                [source] = [t for t in terminals if t.index == term.passes_input]
                assert source.direction == "input", (entry["name"], term.name)
    assert seen >= 20
