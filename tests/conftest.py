"""Shared test fixtures for lvkit tests."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest

from lvkit.codegen.context import CodeGenContext
from lvkit.codegen.errors_module import ERRORS_MODULE_SOURCE

from .clfn_generated import generated_vi  # noqa: F401  (a fixture)
from .fake_kernel32 import kernel32  # noqa: F401  (a fixture)
from .helpers import make_ctx, make_graph_with_terminals

# The sample VI corpus is local-only (gitignored, pulled via
# scripts/pull_samples.sh) — never committed and absent on a fresh clone or a CI
# runner that hasn't fetched it. Tests marked `needs_samples` read from it.
SAMPLES_ROOT = Path(__file__).resolve().parent.parent / ".lvkit" / "cache" / "samples"
_HAVE_SAMPLES = SAMPLES_ROOT.is_dir() and any(SAMPLES_ROOT.iterdir())


def pytest_collection_modifyitems(config, items):
    """Skip `needs_samples` tests when the sample corpus is absent, so a fresh
    clone reports skips rather than dozens of failures. CI pulls the corpus (see
    .github/workflows/ci.yml), so those tests run there and locally."""
    if _HAVE_SAMPLES:
        return
    skip = pytest.mark.skip(reason="sample corpus absent — run scripts/pull_samples.sh")
    for item in items:
        if "needs_samples" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _sanitize_git_env(monkeypatch):
    """Strip git's repo-locating env vars so a test's ``git -C <sample repo>``
    subprocess resolves THAT repo, not whatever the ambient environment points at.

    Under the pre-commit hook the suite runs with ``GIT_DIR`` / ``GIT_WORK_TREE`` /
    ``GIT_INDEX_FILE`` (and friends) exported, pointing at the lvkit repo. A child
    ``git -C <other repo> show`` then INHERITS them, they take precedence over
    ``-C``, git looks the ref up in the wrong repo, and the call fails with exit
    128 (yielding an empty / unreadable VI). That flake appears ONLY under
    pre-commit — never in a bare ``pytest`` run — so it is invisible until commit
    time. Clearing these makes ``-C`` authoritative for every git-extracting test
    (test_diff, test_class_field_walkup, …) in one place."""
    for var in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(scope="session")
def _corpus_cache_dir(tmp_path_factory):
    """One cache per test worker, shared by its corpus tests. Extraction is
    keyed by each VI's content hash, so a VI one test extracted is reused by the
    next instead of re-running pylabview."""
    return tmp_path_factory.mktemp("lvkit-corpus-cache")


@pytest.fixture(autouse=True)
def _hermetic_cache(request, tmp_path_factory, monkeypatch):
    """Point the cache at a tmp dir so no test ever writes to the real
    ``~/.cache/lvkit`` (or a repo's ``.lvkit/cache``). A corpus test
    (``needs_samples``) shares its worker's cache; every other test, and a
    corpus test marked ``cold_cache``, starts with an empty one.
    ``monkeypatch.setenv`` mutates ``os.environ`` in process, so subprocess-based
    CLI tests inherit ``LVKIT_CACHE_DIR`` too. Also clears the module-level
    extraction roots (mirrors a fresh process)."""
    node = request.node
    shared = node.get_closest_marker("needs_samples") and not node.get_closest_marker(
        "cold_cache"
    )
    cache_dir = (
        request.getfixturevalue("_corpus_cache_dir")
        if shared
        else tmp_path_factory.mktemp("lvkit-cache")
    )
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(cache_dir))

    from lvkit import cache_paths, staging

    cache_paths.clear_extraction_roots()
    yield
    cache_paths.clear_extraction_roots()
    staging.release_all()


@pytest.fixture
def graph_factory():
    """Fixture providing graph construction helpers."""
    return make_graph_with_terminals


@pytest.fixture
def ctx_with_terminals():
    """Fixture: create a CodeGenContext with a graph that has the given terminals.

    Usage: ctx = ctx_with_terminals("t1", "t2", "t3")
    """

    def _factory(*terminal_ids: str) -> CodeGenContext:
        return make_ctx(*terminal_ids)

    return _factory


# Two revisions of one VI from the JKI-VI-Tester sample (BSD-3-Clause): the diff
# tests compare the older one (``_DIFF_BASE_REF``) with the sample's current file.
_DIFF_REPO = SAMPLES_ROOT / "JKI-VI-Tester"
_DIFF_VI = "source/Classes/TestCase/run.vi"
_DIFF_BASE_REF = "92be264"


@dataclass(frozen=True)
class ViDiffPair:
    """``base`` -> ``head`` copies in a temporary folder; ``root`` holds both."""

    base: Path
    head: Path
    root: Path


@pytest.fixture(scope="session")
def vi_diff_pair(tmp_path_factory: pytest.TempPathFactory) -> ViDiffPair:
    """An older and the current revision of ``TestCase/run.vi``, copied into a
    temporary folder, so a diff test reads nothing from (and writes nothing to)
    the repository."""
    head_src = _DIFF_REPO / _DIFF_VI
    if not head_src.is_file():
        pytest.skip(f"{head_src} not present on this machine")
    shown = subprocess.run(
        ["git", "-C", str(_DIFF_REPO), "show", f"{_DIFF_BASE_REF}:{_DIFF_VI}"],
        capture_output=True,
    )
    if shown.returncode != 0:
        pytest.skip(f"{_DIFF_REPO} has no revision {_DIFF_BASE_REF}")
    root = tmp_path_factory.mktemp("vi_diff_pair")
    base = root / "run_base.vi"
    base.write_bytes(shown.stdout)
    head = root / "run_head.vi"
    shutil.copyfile(head_src, head)
    return ViDiffPair(base=base, head=head, root=root)


@pytest.fixture
def package(tmp_path: Path) -> Iterator[Callable[..., ModuleType]]:
    """Write a generated module beside stub subVI modules and the package's
    ``errors`` module, and import it."""
    loaded: list[str] = []

    def load(source: str, stubs: dict[str, str]) -> ModuleType:
        name = f"errpkg_{abs(hash(tmp_path))}_{len(loaded)}"
        root = tmp_path / name
        root.mkdir()
        (root / "__init__.py").write_text("")
        (root / "errors.py").write_text(ERRORS_MODULE_SOURCE)
        (root / "caller.py").write_text(source)
        for module, body in stubs.items():
            (root / f"{module}.py").write_text(textwrap.dedent(body))
        sys.path.insert(0, str(tmp_path))
        importlib.invalidate_caches()
        loaded.append(name)
        return importlib.import_module(f"{name}.caller")

    yield load
    while str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))
    for key in [k for k in sys.modules if k.split(".")[0] in loaded]:
        del sys.modules[key]
