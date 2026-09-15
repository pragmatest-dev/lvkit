"""Shared test fixtures for lvkit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.context import CodeGenContext

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


@pytest.fixture(autouse=True)
def _hermetic_cache(tmp_path_factory, monkeypatch):
    """Point the extraction cache at a per-test tmp dir so no test ever writes
    to the real ``~/.cache/lvkit`` (or a repo's ``.lvkit/cache``), and each test
    starts with a cold cache. ``monkeypatch.setenv`` mutates ``os.environ`` in
    process, so subprocess-based CLI tests inherit ``LVKIT_CACHE_DIR`` too.
    Also clears the module-level extraction roots (mirrors a fresh process)."""
    cache_dir = tmp_path_factory.mktemp("lvkit-cache")
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(cache_dir))

    from lvkit import cache_paths

    cache_paths.clear_extraction_roots()
    yield
    cache_paths.clear_extraction_roots()


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
