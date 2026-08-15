"""Regression: a partial (progressive-warm) re-save must never downgrade a
full build's container facts.

``store.save()`` is delete-then-reinsert by path. Progressive warming
(``build.warm_index_for_vi`` / ``warm_all_loaded``) persists facts from a
single-VI graph that never loaded the class hierarchy, so ``library`` and
``class_fact`` come back ``None`` even when a prior full build resolved them.
Without coalescing, that partial save clobbers the good row with NULLs.

Pure store-layer test — hand-built ``VIFacts``/``ClassFact``, no sample corpus,
no VI parsing.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.index.model import ClassFact, VIFacts
from lvkit.index.store import load as load_index
from lvkit.index.store import save as save_index


def _facts(
    path: Path,
    *,
    content_sha: str,
    library: str | None,
    class_fact: ClassFact | None,
) -> VIFacts:
    return VIFacts(
        path=str(path),
        name=path.name,
        content_sha=content_sha,
        library=library,
        class_fact=class_fact,
    )


def test_same_sha_partial_resave_preserves_container_facts(tmp_path: Path):
    """A same-content partial re-save (library=None, class_fact=None) must not
    clobber a prior full build's library/owning_class/parent/scope."""
    vi_path = tmp_path / "Method.vi"

    full = _facts(
        vi_path,
        content_sha="abc",
        library="MyClass.lvclass",
        class_fact=ClassFact(
            owning_class="MyClass.lvclass",
            parent="Base.lvclass",
            scope="public",
        ),
    )
    save_index(tmp_path, [full])

    partial = _facts(
        vi_path,
        content_sha="abc",
        library=None,
        class_fact=None,
    )
    save_index(tmp_path, [partial])

    [loaded] = [f for f in load_index(tmp_path) if f.path == str(vi_path)]
    assert loaded.library == "MyClass.lvclass"
    assert loaded.class_fact is not None
    assert loaded.class_fact.owning_class == "MyClass.lvclass"
    assert loaded.class_fact.parent == "Base.lvclass"
    assert loaded.class_fact.scope == "public"


def test_changed_sha_resave_clears_container_facts(tmp_path: Path):
    """A DIFFERENT content_sha means the VI actually changed — the incoming
    (NULL) facts are trusted fully, clearing the stale prior row."""
    vi_path = tmp_path / "Method.vi"

    full = _facts(
        vi_path,
        content_sha="abc",
        library="MyClass.lvclass",
        class_fact=ClassFact(
            owning_class="MyClass.lvclass",
            parent="Base.lvclass",
            scope="public",
        ),
    )
    save_index(tmp_path, [full])

    changed = _facts(
        vi_path,
        content_sha="def",
        library=None,
        class_fact=None,
    )
    save_index(tmp_path, [changed])

    [loaded] = [f for f in load_index(tmp_path) if f.path == str(vi_path)]
    assert loaded.content_sha == "def"
    assert loaded.library is None
    assert loaded.class_fact is None
