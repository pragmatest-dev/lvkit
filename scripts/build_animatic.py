#!/usr/bin/env python3
"""Assemble the lvkit launch animatic (#58) — a ~28s, looping, self-contained HTML
you can play to feel pacing and screen-record for the LinkedIn cut.

Structure (one shared CSS timeline, so timing is tunable in the TIMELINE block):
  HOOK    a real VI, slow zoom, "What if you could read a VI"
  DROP    the beat-drop flip light->dark (crossfade the same VI's two renders) —
          the reveal that it was lvkit's render all along, not LabVIEW
  5 BEATS one-word capabilities, each framed by its real b-roll:
          describe · document · view · diff · generate
  MARK    the lvkit wordmark + trademark/unaffiliated disclaimer

Reproducible: swap the assets in .tmp/animatic/ (rebuild with scripts/render_*),
edit TIMELINE / TOKENS, re-run. Nothing here is hand-placed pixel art.
"""
from __future__ import annotations

import base64
import html
import re
from pathlib import Path

A = Path(".tmp/animatic")
CREAM, SLATE, ORANGE = "#faf7f2", "#2b3038", "#e8821e"

# ── TIMELINE (seconds) — tune freely ─────────────────────────────────────────
HOOK, DROP, BEAT = 5.5, 1.6, 3.7          # hook / flip / each capability beat
MARK = 2.2
BEATS = ["describe", "document", "view", "diff", "generate"]
TOTAL = HOOK + DROP + BEAT * len(BEATS) + MARK


def _svg(p: Path) -> str:
    return re.sub(r"^<\?xml[^>]*\?>\s*", "", p.read_text())


def _b64_png(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def _pct(t: float) -> float:
    return round(100 * t / TOTAL, 3)


def _window_keyframes(name: str, start: float, dur: float,
                      enter: float = 0.5, leave: float = 0.5) -> str:
    """A scene visible only within [start, start+dur] on the shared TOTAL loop,
    fading in over `enter`s and out over `leave`s."""
    s, e = _pct(start), _pct(start + dur)
    i, o = _pct(start + enter), _pct(start + dur - leave)
    return (f"@keyframes {name}{{"
            f"0%,{s}%{{opacity:0}}{i}%{{opacity:1}}{o}%{{opacity:1}}"
            f"{e}%,100%{{opacity:0}}}}")


def main() -> None:
    md5_light, md5_dark = _svg(A / "md5_light.svg"), _svg(A / "md5_dark.svg")
    view_svg, diff_svg = _svg(A / "view.svg"), _svg(A / "diff.svg")
    describe = html.escape((A / "describe.txt").read_text().strip())
    gen_py = html.escape(
        next(p for p in (A / "gen").rglob("*.py") if p.name != "__init__.py")
        .read_text().strip()[:900])
    icons = sorted((A.parent / "video-picks/docs_md5/icons").glob("*.png"))[:6]
    icon_tags = "".join(
        f'<img src="{_b64_png(p)}" alt="">' for p in icons)

    t = HOOK + DROP
    beat_kf = "".join(_window_keyframes(f"b{i}", t + i * BEAT, BEAT)
                      for i in range(len(BEATS)))
    beat_cap_kf = "".join(
        f"@keyframes cap{i}{{0%,{_pct(t + i * BEAT)}%"
        f"{{opacity:0;transform:translateY(12px)}}"
        f"{_pct(t + i * BEAT + 0.45)}%{{opacity:1;transform:none}}"
        f"{_pct(t + i * BEAT + BEAT - 0.4)}%{{opacity:1}}"
        f"{_pct(t + i * BEAT + BEAT)}%,100%{{opacity:0}}}}"
        for i in range(len(BEATS)))

    beats_html = "".join(
        f'<section class="scene beat" style="animation-name:b{i}">'
        f'<div class="stage">{content}</div>'
        f'<span class="cap" style="animation-name:cap{i}">{cap}</span></section>'
        for i, (cap, content) in enumerate(zip(BEATS, [
            f'<pre class="term">{describe}</pre>',
            f'<div class="docs">{icon_tags}</div>',
            f'<div class="art">{view_svg}</div>',
            f'<div class="art">{diff_svg}</div>',
            f'<pre class="code">{gen_py}</pre>',
        ])))

    doc = f"""<style>
:root{{--cream:{CREAM};--slate:{SLATE};--orange:{ORANGE};
  --loop:{TOTAL:.2f}s;color-scheme:dark;}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--slate);color:var(--cream);overflow:hidden;height:100vh;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;}}
.stage-wrap{{position:fixed;inset:0;display:grid;place-items:center;}}
.scene{{position:absolute;inset:0;display:grid;place-items:center;opacity:0;
  animation:var(--loop) linear infinite;padding:clamp(20px,5vw,80px);}}
/* HOOK + DROP share the VI stack */
.flip{{position:relative;width:min(1000px,92vw);}}
.flip svg{{display:block;width:100%;height:auto;
  border-radius:12px;box-shadow:0 30px 80px -30px #000;}}
.flip .lite{{position:absolute;inset:0;animation:var(--loop) linear infinite;
  animation-name:flip;}}
@keyframes flip{{0%{{opacity:1}}{_pct(HOOK)}%{{opacity:1}}
  {_pct(HOOK+DROP*0.7)}%{{opacity:0}}100%{{opacity:0}}}}
@keyframes zoom{{0%{{transform:scale(1.0)}}{_pct(HOOK+DROP)}%{{transform:scale(1.13)}}
  {_pct(HOOK+DROP)}%{{transform:scale(1.0)}}100%{{transform:scale(1.0)}}}}
.hookwrap{{animation:var(--loop) linear infinite;animation-name:hookvis}}
{_window_keyframes("hookvis", 0, HOOK+DROP, 0.4, 0.3)}
.hook-top,.hook-bot{{position:fixed;left:0;right:0;text-align:center;font-weight:700;
  letter-spacing:-.02em;text-wrap:balance;padding:0 6vw;
  animation:var(--loop) linear infinite;animation-name:hookvis}}
.hook-top{{top:7vh;font-size:clamp(1.3rem,3.4vw,2.5rem);color:#cbb8a4}}
.hook-bot{{bottom:8vh;font-size:clamp(1.5rem,4.2vw,3rem)}}
.hook-bot b{{color:var(--orange)}}
/* BEATS */
.beat .stage{{width:min(1000px,92vw);max-height:74vh;display:grid;place-items:center}}
.art{{width:100%}} .art svg{{width:100%;height:auto;max-height:72vh;
  border-radius:12px;box-shadow:0 30px 80px -30px #000}}
.term,.code{{font-family:ui-monospace,"Cascadia Code",monospace;
  font-size:clamp(.7rem,1.5vw,1rem);line-height:1.55;text-align:left;
  background:#20242b;border:1px solid #3a4049;border-radius:12px;
  padding:clamp(16px,2.5vw,28px);max-width:min(880px,92vw);white-space:pre;
  overflow:hidden;box-shadow:0 30px 80px -30px #000}}
.code{{color:#d7e0c8}}
.docs{{display:grid;grid-template-columns:repeat(3,1fr);gap:clamp(14px,2.5vw,30px)}}
.docs img{{width:clamp(90px,13vw,150px);height:auto;background:#20242b;
  border:1px solid #3a4049;border-radius:12px;padding:14px}}
.cap{{position:fixed;bottom:9vh;left:0;right:0;text-align:center;opacity:0;
  font-size:clamp(2.4rem,8vw,5.5rem);font-weight:800;letter-spacing:-.03em;
  color:var(--orange);animation:var(--loop) linear infinite;}}
/* MARK */
.mark{{animation-name:markvis}}
{_window_keyframes("markvis", HOOK+DROP+BEAT*len(BEATS), MARK, 0.4, 0.2)}
.mark .word{{font-size:clamp(3rem,11vw,7rem);font-weight:800;letter-spacing:-.04em}}
.mark .word b{{color:var(--orange)}}
.legal{{position:fixed;bottom:4vh;left:0;right:0;text-align:center;
  font-size:.62rem;color:#7d8592;max-width:60ch;margin:0 auto;padding:0 6vw;
  animation:var(--loop) linear infinite;animation-name:markvis}}
{_window_keyframes("hookvis2", 0, HOOK+DROP)}
{beat_kf}{beat_cap_kf}
@media (prefers-reduced-motion: reduce){{.scene,.cap,.hook-top,.hook-bot,.flip .lite
  {{animation-duration:.001s}}}}
</style>

<div class="stage-wrap">
  <section class="scene hookwrap" style="animation-name:hookvis">
    <div class="flip" style="animation:var(--loop) linear infinite;animation-name:zoom">
      <div class="dark">{md5_dark}</div>
      <div class="lite">{md5_light}</div>
    </div>
  </section>
  {beats_html}
  <section class="scene mark">
    <div class="word">LV<b>Kit</b></div>
  </section>
</div>

<div class="hook-top">What if you could read a VI</div>
<div class="hook-bot">— without installing <b>LabVIEW</b></div>

<p class="legal">LabVIEW™ is a trademark of National Instruments (NI). lvkit is an
independent, clean-room project, not affiliated with or endorsed by NI. Diagrams
reconstructed from the VI file; not NI artwork.</p>
"""
    out = A / "animatic.html"
    out.write_text(doc)
    print(f"wrote {out}  ({len(doc)//1024} KB, {TOTAL:.1f}s loop)")


if __name__ == "__main__":
    main()
