#!/usr/bin/env python3
"""Build a shareable promo hero for a rendered VI — reproducible, so the VI,
taglines, and palette are all tweakable and re-buildable.

It renders the VI to a THEME-AUTO svg (so light/dark flips are real, not baked),
inlines it into an HTML frame with a top + bottom tagline, and wires a light/dark
toggle. Because the svg themes off ``:root[data-theme]`` and ``:root`` is the host
document when inlined, one toggle on <html> flips the page AND the VI in lockstep
— the "dark surprise". The svg is vector, so it stays crisp at any zoom.

Usage:
    uv run python scripts/build_hero.py \
        --vi ".lvkit/cache/samples/.../MD5 Message Digest (Binary String)__ogtk.vi" \
        --search-path .lvkit/cache/samples \
        --top "What if you could read a VI" \
        --bottom "without installing LabVIEW" \
        --out .tmp/hero.html

Tweak the TAGLINES via flags; tweak the LOOK via the TOKENS block below.
"""
from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# ── TOKENS (brand — edit here to restyle) ─────────────────────────────────────
CREAM = "#faf7f2"   # light ground / dark-mode ink
SLATE = "#2b3038"   # dark ground / light-mode ink
ORANGE = "#e8821e"  # LVKit accent — the LabVIEW DBL wire colour (locked)


def render_svg(vi: str, search_path: str) -> str:
    """Render the VI to a self-contained theme-auto svg and return its markup."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "vi.svg"
        subprocess.run(
            [sys.executable, "-m", "lvkit.cli", "render", vi, "-o", str(out),
             "--search-path", search_path, "--theme", "auto"],
            check=True, capture_output=True, text=True,
        )
        svg = out.read_text()
    # Drop the XML prolog so it inlines as an HTML element.
    return re.sub(r"^<\?xml[^>]*\?>\s*", "", svg)


def build_html(svg: str, top: str, bottom: str, vi_name: str) -> str:
    top_e = html.escape(top)
    bottom_e = html.escape(bottom)
    name_e = html.escape(vi_name)
    return f"""<style>
  :root {{
    --ground: {CREAM}; --ink: {SLATE}; --accent: {ORANGE};
    --muted: color-mix(in srgb, var(--ink) 55%, var(--ground));
    --frame: color-mix(in srgb, var(--ink) 12%, var(--ground));
    --edge:  color-mix(in srgb, var(--ink) 16%, var(--ground));
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme]) {{ --ground: {SLATE}; --ink: {CREAM}; }}
  }}
  :root[data-theme="dark"]  {{ --ground: {SLATE}; --ink: {CREAM}; }}
  :root[data-theme="light"] {{ --ground: {CREAM}; --ink: {SLATE}; }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; background: var(--ground); color: var(--ink);
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; gap: clamp(18px, 3.5vh, 40px);
    padding: clamp(20px, 5vw, 64px);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    transition: background .35s ease, color .35s ease;
  }}
  .tagline {{ text-align: center; text-wrap: balance; margin: 0; line-height: 1.08;
    letter-spacing: -0.02em; font-weight: 700; }}
  .top {{ font-size: clamp(1.4rem, 3.6vw, 2.7rem); color: var(--muted); }}
  .bottom {{ font-size: clamp(1.7rem, 4.6vw, 3.4rem); }}
  .bottom .accent {{ color: var(--accent); white-space: nowrap; }}

  .viewer {{
    width: min(1040px, 94vw); background: var(--frame);
    border: 1px solid var(--edge); border-radius: 14px;
    box-shadow: 0 24px 60px -28px rgba(0,0,0,.5);
    padding: clamp(14px, 2.2vw, 28px); overflow: auto;
    transition: background .35s ease, border-color .35s ease;
  }}
  .viewer svg {{ display: block; width: 100%; height: auto; }}
  .viewer::-webkit-scrollbar {{ height: 8px; }}
  .viewer::-webkit-scrollbar-thumb {{ background: var(--edge); border-radius: 8px; }}

  .meta {{ display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
    justify-content: center; font-size: .82rem; color: var(--muted); }}
  .meta code {{ font-family: ui-monospace, "Cascadia Code", monospace;
    background: var(--frame); border: 1px solid var(--edge);
    padding: 3px 8px; border-radius: 6px; color: var(--ink); }}
  .toggle {{ font: inherit; font-size: .82rem; cursor: pointer; color: var(--ink);
    background: var(--frame); border: 1px solid var(--edge);
    padding: 5px 12px; border-radius: 999px; }}
  .toggle:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .legal {{ max-width: 60ch; text-align: center; text-wrap: balance;
    font-size: .68rem; line-height: 1.5;
    color: color-mix(in srgb, var(--ink) 42%, var(--ground)); margin: 0; }}
  @media (prefers-reduced-motion: reduce) {{ * {{ transition: none !important; }} }}
</style>

<p class="tagline top">{top_e}</p>

<div class="viewer" role="img" aria-label="lvkit render of {name_e}">
{svg}
</div>

<p class="tagline bottom">— {bottom_e.replace(
    "LabVIEW", '<span class="accent">LabVIEW</span>')}</p>

<div class="meta">
  <code>lvkit render "{name_e}"</code>
  <span>vector SVG — zoom in, it stays crisp</span>
  <button class="toggle" type="button" onclick="
    var r = document.documentElement;
    var cur = r.getAttribute('data-theme');
    if (!cur) cur = matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark' : 'light';
    r.setAttribute('data-theme', cur === 'dark' ? 'light' : 'dark');
  ">◐ flip theme</button>
</div>

<p class="legal">LabVIEW™ is a trademark of National Instruments (NI). lvkit is an
independent, clean-room project and is not affiliated with, endorsed by, or
sponsored by NI. Diagram reconstructed from the VI file; not NI artwork.</p>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vi", required=True)
    ap.add_argument("--search-path", default=".lvkit/cache/samples")
    ap.add_argument("--top", default="What if you could read a VI")
    ap.add_argument("--bottom", default="without installing LabVIEW")
    ap.add_argument("--out", default=".tmp/hero.html")
    args = ap.parse_args()

    svg = render_svg(args.vi, args.search_path)
    doc = build_html(svg, args.top, args.bottom, Path(args.vi).name)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    print(f"wrote {out}  ({len(doc) // 1024} KB)")


if __name__ == "__main__":
    main()
