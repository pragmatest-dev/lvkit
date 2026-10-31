"""Generate a corpus VI to Python with the pipeline and import it, so a test can
run it."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from lvkit.pipeline import generate_python
from tests.corpus_graphs import OPENG_USERLIB


@pytest.fixture
def generated_vi(tmp_path: Path) -> Iterator[object]:
    """A function ``generated(vi_path)`` that returns the generated module of
    ``vi_path`` (the VI and every SubVI it needs, with the corpus's OpenG
    user.lib as the ``<userlib>`` root, written under the test's temporary
    directory)."""
    modules: list[str] = []
    sys.path.insert(0, str(tmp_path))

    def generated(vi_path: Path) -> ModuleType:
        result = generate_python(vi_path, tmp_path, userlib_root=OPENG_USERLIB)
        written = Path(result["vi_paths"][str(vi_path.resolve())])
        below = written.relative_to(tmp_path)
        name = ".".join(below.with_suffix("").parts)
        modules.append(name.split(".")[0])
        return importlib.import_module(name)

    yield generated

    sys.path.remove(str(tmp_path))
    for loaded in [m for m in sys.modules if m.split(".")[0] in modules]:
        del sys.modules[loaded]
