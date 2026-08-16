"""LoadMode.MINIMAL: the minimum set to faithfully render a VI — its own
diagram, its direct SubVIs' connector panes (for param-name hovers), and the
field definitions of referenced classes/typedefs — WITHOUT the transitive SubVI
tree. Its render must be byte-identical to a FULL load, at a fraction of the
dependency cost.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi

# A framework VI: ~1 direct SubVI call that transitively pulls ~170 VIs under
# FULL. MINIMAL should collapse that to the shallow direct fan-out.
DEEP_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Programmatic API/"
    "Run Tests (TestCase Object).vi"
)
# A VI with a class unbundle (field names) in a subfolder.
CLASS_VI = Path(
    ".lvkit/cache/samples/JKI-VI-Tester/source/Ant Plugin/Source/"
    "TextTestRunner.Ant/protected/processResult.vi"
)


def _render(vi: Path, mode: LoadMode) -> tuple[str, int]:
    g = InMemoryVIGraph()
    g.load_vi(vi, search_paths=None, layout=True, mode=mode)
    n_vis = sum(
        1
        for _, d in g._dep_graph.nodes(data=True)
        if d.get("node_type") not in ("class", "typedef", "library")
    )
    return render_vi(g, g.resolve_vi_name(vi.name)) or "", n_vis


@pytest.mark.parametrize("vi", [DEEP_VI, CLASS_VI])
def test_minimal_render_is_identical_to_full(vi: Path) -> None:
    if not vi.exists():
        pytest.skip(f"Sample VI not available: {vi}")
    full_svg, _ = _render(vi, LoadMode.FULL)
    min_svg, _ = _render(vi, LoadMode.MINIMAL)
    assert min_svg == full_svg  # byte-for-byte — same pixels AND same hovers


def test_minimal_truncates_the_transitive_tree() -> None:
    """The whole point: MINIMAL loads far fewer VIs than FULL on a deep tree."""
    if not DEEP_VI.exists():
        pytest.skip(f"Sample VI not available: {DEEP_VI}")
    _, full_vis = _render(DEEP_VI, LoadMode.FULL)
    _, min_vis = _render(DEEP_VI, LoadMode.MINIMAL)
    assert full_vis > 20  # the transitive tree is deep
    assert min_vis <= 5  # MINIMAL collapses to the shallow direct fan-out
