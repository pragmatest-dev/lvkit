"""The diff viewer and the rendered diagram's own frame selector, driven in a
real headless Chrome: a frame is addressed by position, the other pane follows
the frame matching, and a person reads labels. Skipped on a machine without
Chrome (``google-chrome``, ``chromium`` or ``chromium-browser`` on PATH)."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from lvkit.graph import InMemoryVIGraph
from lvkit.render import render_vi
from lvkit.vi_diff import diff_vi_files
from tests.diff_viewer_cases import write_versions

_CHROME = next(
    (
        found
        for name in ("google-chrome", "chromium", "chromium-browser")
        if (found := shutil.which(name))
    ),
    None,
)
pytestmark = pytest.mark.skipif(_CHROME is None, reason="no headless Chrome")

# Appended to the page: runs once the viewer's own scripts have, records what
# each step leaves selected and shown, and writes it into the page as JSON.
_REPORT_JS = """
function lvkitReport(out){
  const pre=document.createElement("pre");
  pre.id="lvkit-result";
  pre.textContent=JSON.stringify(out);
  document.body.appendChild(pre);
}
"""

_SNAPSHOT_JS = _REPORT_JS + """
function lvkitSnap(){
  const shown=p=>[...p.querySelectorAll('.lv-frame:not(.lv-label)')]
    .filter(g=>!g.classList.contains('lv-frame-hidden')).map(g=>g.dataset.path);
  const labels=p=>[...p.querySelectorAll('.lv-frame.lv-label')]
    .filter(g=>!g.classList.contains('lv-frame-hidden'))
    .map(g=>g.querySelector('text').textContent);
  return {before:{...PST.before.sel}, after:{...PST.after.sel},
          beforeShown:shown(beforePane), afterShown:shown(afterPane),
          beforeLabels:labels(beforePane), afterLabels:labels(afterPane)};
}
"""

_DIFF_STEPS_JS = """
(function(){
  const out={initial:lvkitSnap()};
  const option=beforePane.querySelector(
    '.lv-menu[data-lv-struct="600"] .lv-option[data-lv-value="0"]');
  out.optionValue=option ? option.dataset.lvValue : null;
  setFrame('600', option.dataset.lvValue, true); out.pickBefore0=lvkitSnap();
  setFrame('600', '1', true); out.pickBefore1=lvkitSnap();
  setFrame('600', '0', false); out.pickAfterAdded=lvkitSnap();
  stepFrame(afterPane, '600', 1); out.stepAfter=lvkitSnap();
  setFrame('600', '1', true);
  setFrame('650', '0', true); out.nestedBefore=lvkitSnap();
  setFrame('655', '1', false); out.nestedAfter=lvkitSnap();
  jump(CHANGES.findIndex(c=>c.uid==='680')); out.reveal=lvkitSnap();
  lvkitReport(out);
})();
"""

_DEEP_LINK_JS = """
(function(){
  lvkitReport({deep:lvkitSnap()});
})();
"""


def _run(page: Path, fragment: str = "") -> dict:
    assert _CHROME is not None
    done = subprocess.run(
        [
            _CHROME,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--window-size=1600,1200",
            "--virtual-time-budget=10000",
            "--dump-dom",
            page.as_uri() + fragment,
        ],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    found = re.search(r'<pre id="lvkit-result">(.*?)</pre>', done.stdout, re.S)
    assert found, done.stderr[-2000:]
    return json.loads(html.unescape(found.group(1)))


def _viewer(tmp_path: Path, script: str) -> Path:
    before, after = write_versions(tmp_path)
    body = diff_vi_files(before, after, fmt="html", warm_index=False)
    assert body is not None
    page = tmp_path / "viewer.html"
    page.write_text(f"{body.body}<script>{_SNAPSHOT_JS}{script}</script>")
    return page


@pytest.fixture(scope="module")
def steps(tmp_path_factory: pytest.TempPathFactory) -> dict:
    return _run(_viewer(tmp_path_factory.mktemp("viewer"), _DIFF_STEPS_JS))


def test_the_panes_open_on_their_first_frames(steps: dict) -> None:
    assert steps["initial"]["before"] == {"600": "0", "650": "0"}
    assert steps["initial"]["after"] == {"600": "0", "655": "0"}
    assert steps["optionValue"] == "0"


def test_a_frame_picked_in_one_pane_shows_its_partner_after_an_inserted_frame(
    steps: dict,
) -> None:
    # Before frame "1" (position 0) is after frame "1" (position 1): the after
    # version has a new frame "5" in front of it.
    assert steps["pickBefore0"]["before"]["600"] == "0"
    assert steps["pickBefore0"]["after"]["600"] == "1"
    assert "1" in steps["pickBefore0"]["beforeLabels"]
    assert "1" in steps["pickBefore0"]["afterLabels"]


def test_a_renamed_frame_shows_its_partner(steps: dict) -> None:
    # Before frame "2" (position 1) was renamed "3" (after position 2).
    assert steps["pickBefore1"]["before"]["600"] == "1"
    assert steps["pickBefore1"]["after"]["600"] == "2"
    assert "2" in steps["pickBefore1"]["beforeLabels"]
    assert "3" in steps["pickBefore1"]["afterLabels"]


def test_an_added_frame_leaves_the_other_pane_where_it_is(steps: dict) -> None:
    assert steps["pickAfterAdded"]["after"]["600"] == "0"
    assert steps["pickAfterAdded"]["before"]["600"] == "1"
    assert "5" in steps["pickAfterAdded"]["afterLabels"]
    # Stepping the after pane on to its frame "1" brings before back to "1".
    assert steps["stepAfter"]["after"]["600"] == "1"
    assert steps["stepAfter"]["before"]["600"] == "0"


def test_a_re_keyed_structure_syncs_both_ways(steps: dict) -> None:
    # The nested case is 650 in the before version and 655 in the after
    # version; the diff matched it, so each pane addresses it by its own uid.
    assert steps["nestedBefore"]["before"]["650"] == "0"
    assert steps["nestedBefore"]["after"]["655"] == "0"
    assert steps["nestedAfter"]["after"]["655"] == "1"
    assert steps["nestedAfter"]["before"]["650"] == "1"
    assert "7" in steps["nestedBefore"]["afterLabels"]
    assert "8" in steps["nestedAfter"]["beforeLabels"]


def test_revealing_a_change_in_a_nested_case_opens_both_panes(steps: dict) -> None:
    reveal = steps["reveal"]
    assert (reveal["after"]["600"], reveal["after"]["655"]) == ("2", "1")
    assert (reveal["before"]["600"], reveal["before"]["650"]) == ("1", "1")
    assert "600:2;655:1" in reveal["afterShown"]
    assert "600:1;650:1" in reveal["beforeShown"]
    assert {"3", "8"} <= set(reveal["afterLabels"])
    assert {"2", "8"} <= set(reveal["beforeLabels"])


def test_a_deep_link_frame_is_addressed_in_the_after_pane(tmp_path: Path) -> None:
    deep = _run(_viewer(tmp_path, _DEEP_LINK_JS), "#frame=600:2")["deep"]
    assert deep["after"]["600"] == "2"
    assert deep["before"]["600"] == "1"


_RENDER_STEPS_JS = """
(function(){
  const root=document.querySelector('svg');
  const shown=()=>[...root.querySelectorAll('.lv-frame:not(.lv-label)')]
    .filter(g=>!g.classList.contains('lv-frame-hidden')).map(g=>g.dataset.path);
  const labels=()=>[...root.querySelectorAll('.lv-frame.lv-label')]
    .filter(g=>!g.classList.contains('lv-frame-hidden'))
    .map(g=>g.querySelector('text').textContent);
  const click=el=>el.dispatchEvent(new MouseEvent('click', {bubbles:true}));
  const out={initial:{shown:shown(), labels:labels()}};
  click(root.querySelector('[data-lv-action="next"][data-lv-struct="600"]'));
  out.next={shown:shown(), labels:labels()};
  click(root.querySelector(
    '.lv-option[data-lv-struct="600"][data-lv-value="0"]'));
  out.option={shown:shown(), labels:labels()};
  lvkitReport(out);
})();
"""


def test_the_rendered_selector_steps_frames_and_shows_their_labels(
    tmp_path: Path,
) -> None:
    before, _ = write_versions(tmp_path)
    graph = InMemoryVIGraph()
    graph.load_vi(before, layout=True)
    svg = render_vi(graph, graph.list_vis()[0])
    assert svg is not None
    page = tmp_path / "render.html"
    page.write_text(
        f"<html><body>{svg}<script>{_REPORT_JS}{_RENDER_STEPS_JS}</script></body></html>"
    )
    out = _run(page)
    assert "600:0" in out["initial"]["shown"] and "1" in out["initial"]["labels"]
    assert "600:1" in out["next"]["shown"] and "2" in out["next"]["labels"]
    assert "600:1" not in out["option"]["shown"]
    assert "600:0" in out["option"]["shown"]
