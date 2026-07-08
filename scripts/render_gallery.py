#!/usr/bin/env python3
"""Render a fixed corpus of sample VIs with the block-diagram renderer and
build a single self-contained HTML gallery for visual review.

Run it:

    uv run python scripts/render_gallery.py

Output (printed at the end as an absolute path):

    outputs/gallery/index.html   <- open this in a browser
    outputs/gallery/<name>.svg   <- one SVG per VI (also open-able directly)

The gallery embeds each SVG inline, so there is **no rasterizer dependency**
(no cairosvg / Pillow) — browsers and most editors render SVG natively. Pass
``--out DIR`` to write somewhere else.

The corpus lives in ``CORPUS`` below. Sample ``.vi`` files are external test
data under ``samples/`` (gitignored); any VI that is missing locally is simply
skipped, and any that fails to extract is reported — neither aborts the run.
"""

from __future__ import annotations

import argparse
import html
from dataclasses import dataclass
from pathlib import Path

from lvkit.render import render_vi_file

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Sample:
    name: str
    path: str
    note: str


# Curated to exercise the renderer's breadth: structures, wire types, icons,
# and — for the error/variant work — mustard error wires + purple variants.
CORPUS: list[Sample] = [
    Sample("array_average", "experiments/lv-renderer/gallery/array average 1.vi",
           "For-Loop · arithmetic · auto-index array · coercion dot — reference VI"),
    Sample("daqmx_out", "samples/DAQmx-Digital-IO/Out.vi",
           "While loop · SubVI chain · mustard error-cluster wire · typed constants"),
    Sample("daqmx_in", "samples/DAQmx-Digital-IO/In.vi",
           "Flat sequence · SubVI chain · error propagation"),
    Sample("easyxml_build", "samples/JKI-EasyXML/Build.vi",
           "Case structures · many SubVIs · build script"),
    Sample("easy_parse_xml", "samples/JKI-EasyXML/Source/Easy Parse XML.vi",
           "Dense dataflow · nested cases · SubVIs · clusters"),
    Sample("xml_loop_recursion",
           "samples/JKI-EasyXML/Source/Fast Parser/XML Loop Stack Recursion.vi",
           "Nested cases · recursion · purple variant + mustard error wires"),
    Sample("cluster_to_array",
           "samples/JKI-EasyXML/Source/OpenG Derivative/"
           "Cluster to Array of VData--EasyXML.vi",
           "Arrays + clusters · purple Variant terminals + solid-purple constant"),
    Sample("vitester_build", "samples/JKI-VI-Tester/source/build.vi",
           "LabVIEW class / project-plugin (OOP) dataflow"),
    Sample(
        "stacked_sequence",
        "samples/OpenG/extracted/File Group 0/user.lib/_OpenG.lib/string/"
        "string.llb/Number to Proper Engl Text__ogtk.vi",
        "Stacked sequence (3 frames) · click the ◄ index ► selector to flip",
    ),
]


def _card(sample: Sample, svg: str) -> str:
    return (
        '<figure class="card">'
        f'<figcaption><b>{html.escape(sample.name)}</b>'
        f'<span>{html.escape(sample.note)}</span></figcaption>'
        f'<div class="stage">{svg}</div>'
        '</figure>'
    )


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>lvkit renderer — VI gallery</title>
<style>
:root {{ color-scheme: light dark; --bg:#f4f2ec; --panel:#fff; --ink:#23211c;
  --muted:#6f6a5d; --line:#e0dccf; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1b1a17; --panel:#232220; --ink:#e9e6dd; --muted:#9c968a;
    --line:#3a382f; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink);
  font:15px/1.5 system-ui,sans-serif; }}
header {{ padding:24px 28px 8px; }}
header h1 {{ margin:0 0 4px; font-size:20px; }}
header p {{ margin:0; color:var(--muted); font-size:13px; }}
header code {{ background:var(--panel); border:1px solid var(--line);
  padding:1px 5px; border-radius:4px; }}
.grid {{ display:flex; flex-direction:column; gap:20px; padding:20px 28px 48px; }}
.card {{ margin:0; background:var(--panel); border:1px solid var(--line);
  border-radius:8px; overflow:hidden; }}
figcaption {{ padding:10px 14px; border-bottom:1px solid var(--line);
  display:flex; gap:12px; align-items:baseline; }}
figcaption b {{ font-size:14px; }}
figcaption span {{ color:var(--muted); font-size:12px; }}
.stage {{ padding:14px; overflow-x:auto; }}
.stage svg {{ max-width:100%; height:auto; display:block; }}
.fail {{ color:#c0392b; padding:10px 14px; font-size:13px; }}
</style>
<header>
  <h1>lvkit renderer — VI gallery</h1>
  <p>{count} VIs · regenerate with
  <code>uv run python scripts/render_gallery.py</code>
  · click a case's <code>◄ value ▼ ►</code> selector to flip frames</p>
</header>
<div class="grid">
{cards}
</div>
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "outputs" / "gallery",
                    help="output directory (default: outputs/gallery)")
    args = ap.parse_args()

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    cards: list[str] = []
    ok = skipped = failed = 0
    for s in CORPUS:
        vi = REPO_ROOT / s.path
        if not vi.exists():
            print(f"  SKIP  {s.name:22} (missing: {s.path})")
            skipped += 1
            continue
        try:
            svg = render_vi_file(vi, expand_subvis=False)
        except Exception as e:  # noqa: BLE001 — report, never abort the run
            print(f"  FAIL  {s.name:22} {type(e).__name__}: {e}")
            cards.append(
                f'<figure class="card"><figcaption><b>{html.escape(s.name)}'
                f'</b></figcaption><div class="fail">render failed: '
                f'{html.escape(type(e).__name__)}</div></figure>')
            failed += 1
            continue
        if not svg:
            print(f"  NONE  {s.name:22} (renderer declined — no geometry)")
            failed += 1
            continue
        (out / f"{s.name}.svg").write_text(svg)
        cards.append(_card(s, svg))
        print(f"  OK    {s.name:22} ({len(svg)} bytes)")
        ok += 1

    index = out / "index.html"
    index.write_text(_PAGE.format(count=ok, cards="\n".join(cards)))

    print(f"\n{ok} rendered, {skipped} skipped, {failed} failed")
    print(f"\nOpen: {index.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
