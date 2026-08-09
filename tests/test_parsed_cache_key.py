"""parsed_cache.compute_key — the content-identity spine ([H]).

The parse cache keys off the SHARED extraction content-id (the `.vi` sha in the
`meta.json` sidecar), so a `.vi` change invalidates it exactly as it invalidates
every other cache — with no redundant re-hash of the XML. When there's no
sidecar (ad-hoc/temp extraction) it falls back to hashing the XML bytes. Pure
unit tests: no sample corpus, no real parse.
"""

from __future__ import annotations

import json
from pathlib import Path

from lvkit.parser import parsed_cache


def _fake_extract(tmp_path: Path, sha: str | None) -> Path:
    """A fake extraction-cache dir: a `_BDHb.xml` plus, optionally, the
    `meta.json` sidecar the extractor writes (carrying the `.vi` content sha)."""
    bd = tmp_path / "Foo_BDHb.xml"
    bd.write_bytes(b"<BD/>")
    if sha is not None:
        (tmp_path / "Foo.meta.json").write_text(
            json.dumps({"sha256": sha}), encoding="utf-8"
        )
    return bd


def test_key_reuses_extraction_content_id(tmp_path: Path):
    """The key comes from the meta.json sha (the spine), NOT a re-hash of the
    XML bytes: changing bd_xml while the sidecar sha is unchanged → same key."""
    bd = _fake_extract(tmp_path, "aaaa")
    k = parsed_cache.compute_key(bd, None, None)
    assert k
    bd.write_bytes(b"<BD>different-bytes-but-meta-sha-unchanged</BD>")
    assert parsed_cache.compute_key(bd, None, None) == k


def test_content_change_invalidates(tmp_path: Path):
    """A `.vi` content change rewrites meta.json with a new sha → a different
    parse-cache key → the stale entry is never served."""
    bd = _fake_extract(tmp_path, "aaaa")
    k1 = parsed_cache.compute_key(bd, None, None)
    (tmp_path / "Foo.meta.json").write_text(
        json.dumps({"sha256": "bbbb"}), encoding="utf-8"
    )
    k2 = parsed_cache.compute_key(bd, None, None)
    assert k2 and k2 != k1


def test_falls_back_to_xml_hash_without_sidecar(tmp_path: Path):
    """No meta.json (ad-hoc/temp extraction) → still content-keyed, via the
    XML-bytes fallback, which tracks the actual bytes."""
    bd = _fake_extract(tmp_path, None)
    k = parsed_cache.compute_key(bd, None, None)
    assert k
    bd.write_bytes(b"<BD>changed</BD>")
    assert parsed_cache.compute_key(bd, None, None) != k
