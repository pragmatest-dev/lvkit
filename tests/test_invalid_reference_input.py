"""A reference input that holds a number (what a control of an id type holds when
nothing was opened: 0) is the library's own error on the error out, not an
``AttributeError`` where the generated code calls a method on it. The gRPC
library answers -1 for an id that is not the object it expects
(``grpc_client.cc``: ``CloseClient``, ``ClientCancelCall`` and the others return -1
when ``CastTo`` finds nothing); an invalid refnum into a file function is error 1,
"An input parameter is invalid" (as Close File)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_catalog_files_strings_arrays import ERROR_CLUSTER, ZIP
from tests.test_grpc_client import CLOSE, GRPC_ID
from tests.test_grpc_streaming import ANY, CANCEL
from tests.vilib_caller import I32, PATH, STRING, run_catalog_call


def test_close_client_of_a_number_is_error_minus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        CLOSE,
        [
            (0, "output", ERROR_CLUSTER),
            (8, "input", ERROR_CLUSTER),
            (11, "input", GRPC_ID),
        ],
        {"client": (11, GRPC_ID)},
        {"error": (0, ERROR_CLUSTER)},
        {"client": 0},
    )
    assert result.error.code == -1
    assert result.error.source == "Close Client"
    assert "0" in result.error.message


def test_cancel_call_of_a_number_is_error_minus_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        CANCEL,
        [
            (0, "output", ERROR_CLUSTER),
            (1, "output", ANY),
            (3, "output", GRPC_ID),
            (8, "input", ERROR_CLUSTER),
            (11, "input", GRPC_ID),
        ],
        {"call": (11, GRPC_ID)},
        {"call_out": (3, GRPC_ID), "details": (1, ANY), "error": (0, ERROR_CLUSTER)},
        {"call": 0},
    )
    assert result.error.code == -1


def test_close_zip_file_of_a_number_is_error_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, result = run_catalog_call(
        tmp_path,
        monkeypatch,
        [*ZIP, "Close Zip File.vi"],
        [
            (0, "output", ERROR_CLUSTER),
            (4, "input", ERROR_CLUSTER),
            (6, "input", STRING),
            (7, "input", I32),
        ],
        {"archive": (7, I32), "comments": (6, STRING)},
        {"error": (0, ERROR_CLUSTER)},
        {"archive": 0, "comments": "note"},
    )
    assert result.error.code == 1
    assert result.error.source == "Close Zip File"
    assert "0" in result.error.message


def test_add_file_to_zip_of_a_number_is_error_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        {"error": (0, ERROR_CLUSTER)},
        {"archive": 0, "source": tmp_path / "a.txt", "inside": Path("a.txt")},
    )
    assert result.error.code == 1
