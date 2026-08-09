"""Parsed-representation cache (parser/parsed_cache.py): XML -> ParsedVI.

Stacked on top of the existing .vi -> XML extraction cache. These tests
guard the one thing that matters: caching must never change output. A cold
parse (cache miss, decodes the XML) and a warm parse (cache hit, unpickles a
prior result) must produce byte-identical `lvkit generate` output and
identical index `VIFacts` -- and a schema/tool version bump must invalidate
a stale-keyed entry rather than silently serving it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.parser import parsed_cache
from lvkit.parser.models import (
    ParsedBlockDiagram,
    ParsedFrontPanel,
    ParsedVI,
    ParsedVIMetadata,
)

pytestmark = pytest.mark.needs_samples

SAMPLE = Path(
    ".lvkit/cache/samples/LabVIEW-DAQ/Fiber Photometry/TrackDroppedFrames_FP.vi"
).resolve()


@pytest.fixture
def sample() -> Path:
    if not SAMPLE.exists():
        pytest.skip(f"sample VI absent: {SAMPLE}")
    return SAMPLE


def _read_tree(root: Path) -> dict[str, bytes]:
    """Every generated file under `root`, keyed by its relative path -- for a
    byte comparison across two separately-generated output directories."""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_generate_byte_identical_cold_vs_warm(sample: Path, tmp_path: Path):
    """`lvkit generate`'s output must be byte-identical whether the entry
    VI's (and its SubVIs') ParsedVI came from a fresh XML decode (cold --
    first run, populates the parse cache) or a pickled cache hit (warm --
    second run). The `_hermetic_cache` autouse fixture points both runs at
    the SAME fresh `$LVKIT_CACHE_DIR`, so the second call is a genuine warm
    hit off the first."""
    from lvkit.pipeline import generate_python

    out_cold = tmp_path / "out_cold"
    out_warm = tmp_path / "out_warm"

    generate_python(sample, str(out_cold))  # parse cache MISS: decodes + stores
    generate_python(sample, str(out_warm))  # parse cache HIT: unpickles

    cold_files = _read_tree(out_cold)
    warm_files = _read_tree(out_warm)
    assert cold_files.keys() == warm_files.keys()
    assert cold_files == warm_files, "generated output differs cold vs warm"


def test_facts_identical_cold_vs_warm(sample: Path):
    """Same guarantee for the index projection: `VIFacts` built from a cold
    parse must equal `VIFacts` built from a warm (cached) parse -- the parse
    cache must not change what the index records."""
    from lvkit.index.build import build_one_vi

    project_root = sample.parent
    cold = build_one_vi(project_root, sample)  # MISS: decodes + stores
    warm = build_one_vi(project_root, sample)  # HIT: unpickles

    assert cold == warm


def test_disable_switch_forces_cold_parse(sample: Path, monkeypatch):
    """LVKIT_NO_PARSE_CACHE=1 bypasses both read and write -- two parses of
    the same VI never share a cache entry, and no entry is written."""
    from lvkit.extractor import extract_vi_xml

    monkeypatch.setenv("LVKIT_NO_PARSE_CACHE", "1")
    bd_xml, fp_xml, main_xml = extract_vi_xml(sample)

    key = parsed_cache.compute_key(bd_xml, fp_xml, main_xml)
    assert key is not None
    assert parsed_cache.load(key) is None  # nothing was ever written

    from lvkit.parser import parse_vi

    parse_vi(bd_xml=bd_xml, fp_xml=fp_xml, main_xml=main_xml)
    assert parsed_cache.load(key) is None  # the parse above did not write one


def _minimal_parsed_vi() -> ParsedVI:
    """A tiny, cheap-to-construct ParsedVI for exercising the cache module
    directly, without a real VI parse."""
    return ParsedVI(
        metadata=ParsedVIMetadata(qualified_name="Fake.vi"),
        block_diagram=ParsedBlockDiagram(nodes=[], constants=[], wires=[]),
        front_panel=ParsedFrontPanel(controls=[], panel_bounds=(0, 0, 100, 100)),
    )


def test_version_bump_invalidates_cache(tmp_path: Path, monkeypatch):
    """Bumping SCHEMA_VERSION (or the running lvkit __version__) must change
    the computed key, so an entry stored under the OLD key is never served
    under the NEW one -- proves the key includes the version, not just XML
    content. This is what makes a schema/tool change an automatic miss
    instead of a silently-stale hit."""
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path))
    bd_xml = tmp_path / "Fake_BDHb.xml"
    bd_xml.write_text("<VI/>", encoding="utf-8")

    fake = _minimal_parsed_vi()

    key_before = parsed_cache.compute_key(bd_xml, None, None)
    assert key_before is not None
    parsed_cache.store(key_before, fake)
    assert parsed_cache.load(key_before) == fake

    # --- SCHEMA_VERSION bump ---
    monkeypatch.setattr(parsed_cache, "SCHEMA_VERSION", parsed_cache.SCHEMA_VERSION + 1)
    key_after_schema = parsed_cache.compute_key(bd_xml, None, None)
    assert key_after_schema != key_before
    assert parsed_cache.load(key_after_schema) is None
    monkeypatch.undo()

    # --- lvkit __version__ bump ---
    monkeypatch.setenv("LVKIT_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(parsed_cache, "__version__", "999.999.999")
    key_after_version = parsed_cache.compute_key(bd_xml, None, None)
    assert key_after_version != key_before
    assert parsed_cache.load(key_after_version) is None

    # The ORIGINAL key is still untouched by either bump -- it still serves
    # the original entry (bumping the version doesn't corrupt existing data,
    # it just stops NEW lookups from finding it under the OLD key).
    assert parsed_cache.load(key_before) == fake
