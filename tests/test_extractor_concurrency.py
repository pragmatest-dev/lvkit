"""Concurrent extraction must never corrupt the shared cache.

Regression for the extraction-cache race: two+ lvkit processes/threads
extracting the SAME VI at once used to write straight into the shared cache
dir and interleave into the same file, so a reader could see partial/corrupt
XML. Extraction now writes into a private temp dir and publishes each artifact
via atomic os.replace (meta.json last), so every reader sees a complete file.
"""

import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lvkit.extractor import extract_vi_xml

pytestmark = pytest.mark.needs_samples

_VI = (
    Path(__file__).resolve().parent.parent
    / ".lvkit"
    / "cache"
    / "samples"
    / "JKI-VI-Tester"
    / "source"
    / "Classes"
    / "TestCase"
    / "run.vi"
)


def test_concurrent_extraction_never_corrupts_cache():
    if not _VI.exists():
        pytest.skip("sample VI absent")

    def _extract_and_parse(_: int) -> int:
        # force=True so every worker hits the write/publish path simultaneously.
        bd, fp, main = extract_vi_xml(_VI, force=True)
        ET.parse(bd)  # raises on a partial/corrupt (interleaved) write
        if fp:
            ET.parse(fp)
        if main:
            ET.parse(main)
        return bd.stat().st_size

    n = 12
    with ThreadPoolExecutor(max_workers=n) as ex:
        sizes = list(ex.map(_extract_and_parse, range(n)))

    assert all(s > 0 for s in sizes)
    assert len(set(sizes)) == 1  # every worker saw the same complete file size
    # and the committed cache is valid afterwards
    bd, _, _ = extract_vi_xml(_VI)
    ET.parse(bd)
