"""Regression: ``render_vi_file_titled`` must render/title the VI at the given
PATH — identified by the ``vi_key`` that ``load_vi`` returns — never re-resolved
by bare filename.

Every JKI VI Tester class ships its own ``run.vi``, and opening one loads several
into the same graph (opening ``TestRunner/run.vi`` transitively pulls in
``TestCase/run.vi`` and ``TestSuite/run.vi``). A bare-name
``resolve_vi_name("run.vi")`` then collapses all of them to ONE candidate
(TestCase's), so opening TestRunner's or TestSuite's run.vi used to render and
title TestCase's instead.
"""

from pathlib import Path

import pytest

from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi_file_titled

pytestmark = pytest.mark.needs_samples

_SRC = Path(".lvkit/cache/samples/JKI-VI-Tester/source")


@pytest.mark.parametrize(
    "cls", ["TestCase", "TestSuite", "TestRunner", "TextTestRunner"]
)
def test_titled_render_uses_the_opened_vis_own_identity(cls: str) -> None:
    vi = _SRC / "Classes" / cls / "run.vi"
    svg, title = render_vi_file_titled(vi, search_paths=[_SRC], mode=LoadMode.MINIMAL)
    assert svg, "expected a rendered diagram"
    # The title is the qualified name of the VI actually opened — this class's
    # own run.vi, never another class's run.vi picked by a bare-name collision.
    assert title == f"{cls}.lvclass:run.vi", (
        f"opened {cls}/run.vi but titled {title!r} — bare-name collision regression"
    )
