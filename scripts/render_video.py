#!/usr/bin/env python3
"""Render the launch animatic skeleton to an actual .mp4.

The skeleton (.tmp/animatic/skeleton.html) holds the motion/timing/beat order the
video was iterated to: hero -> hook -> flash -> drop-to-dark (reveal "lvkit") ->
View, Describe, Document, Diff, Transpile -> outro mark, on a 30s loop with a
Ken-Burns camera over the VI board. This swaps the placeholder board for the REAL
lvkit light+dark renders (they flip with the stage theme at the drop), plays it in
headless Chrome, records real-time, and encodes to H.264 mp4.

    uv run python scripts/render_video.py \
        --light .tmp/animatic/md5_light.svg --dark .tmp/animatic/md5_dark.svg \
        --width 1280 --height 720 --seconds 30 --out .tmp/animatic/lvkit_launch.mp4
"""
from __future__ import annotations

import argparse
import base64
import html as _html
import re
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SKELETON = Path(".tmp/animatic/skeleton.html")
A = SKELETON.parent
FONT = Path("branding/selawik/selawkb.ttf")
WORDMARK = Path("branding/lvkit-wordmark-dark.svg")  # light text, for the dark outro

_KW = (
    r"\b(from|import|def|class|return|with|for|in|as|if|elif|else|and|or|not|"
    r"None|True|False|lambda|yield|while|try|except|finally|raise|await|async)\b"
)


def _svg(p: Path) -> str:
    return re.sub(r"^<\?xml[^>]*\?>\s*", "", p.read_text())


def _b64_png(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def _font_css() -> str:
    """Embed Selawik (Bold) so the video's display text uses the brand face."""
    b64 = base64.b64encode(FONT.read_bytes()).decode()
    return (
        "<style>@font-face{font-family:'Selawik';font-style:normal;"
        f"src:url(data:font/ttf;base64,{b64}) format('truetype')}}</style>"
    )


def _describe_html(p: Path) -> str:
    """Real `lvkit describe` output → coloured monospace card."""
    out = []
    for line in _html.escape(p.read_text().strip()).split("\n"):
        if line.startswith("# "):
            out.append(f'<span class="h">{line}</span>')
        elif line.startswith("## "):
            out.append(f'<span class="h2">{line}</span>')
        else:
            out.append(line)
    return "\n".join(out)


def _code_html(p: Path, maxlines: int = 48) -> str:
    """Real generated Python → keyword-highlighted card (escape first, then tag)."""
    body = "\n".join(p.read_text().strip().split("\n")[:maxlines])
    esc = _html.escape(body)
    return re.sub(_KW, r'<span class="kw">\1</span>', esc)


def _fmt_css(width: int, height: int) -> str:
    """Aspect-specific overrides. The skeleton is composed for 16:9; at 1:1 (or
    taller) the board and captions float in the extra height and the text-panel
    scrolls — tuned for a 720px window — run past their content. When the frame
    is square/portrait, tighten everything so content fills the frame."""
    if height < width:  # landscape master — no overrides
        return ""
    # Small-screen philosophy: zoom in HARD so text is legible when a 1080px
    # frame is shown at ~400px on a phone. Content bleeding off the sides is
    # fine — you don't need the full width to get the picture.
    return """<style>
  /* fill the square: bigger board + stronger intro push-in */
  .board svg{max-width:100% !important;max-height:99% !important}
  .stage[data-scene="hero"] .board{transform:scale(1.75)}
  .stage[data-scene="build"] .board{transform:scale(2.3) translate(2%,-1%)}
  .stage[data-scene="drop"] .board{transform:scale(2.2)}
  .stage[data-scene="view"] .board{transform:scale(2.7) translate(-6%,3%)}
  /* intro words — one line each; top sized to fit the full phrase, bottom bigger */
  .htop{top:0;padding:5vh 0 4vh;font-size:clamp(38px,6vw,64px);white-space:nowrap}
  .hbot{bottom:0;padding:4vh 0 5vh;font-size:clamp(44px,7.8vw,94px);white-space:nowrap}
  /* bigger action words (centered) */
  .cap .beat{font-size:clamp(64px,12.6vw,150px)}
  /* bigger mic-drop wordmark */
  .revmark svg{width:min(680px,86vw)}
  /* text panels: BIG mono for mobile legibility (lines bleed right, fine) */
  .ac-text .shot,.ac-code .shot{padding:5vh 5vw;
    font-size:clamp(20px,2.7vw,32px);line-height:1.55}
  .stage[data-scene="describe"] .ac-text .shot{transform:translateY(-120%)}
  .stage[data-scene="generate"] .ac-code .shot{transform:translateY(-90%) scale(1.02)}
  /* document: zoom in + anchor LEFT (bleed right), pan down — no run-out */
  .ac-doc .shot{width:134%;left:0;transform:translateY(0)}
  .stage[data-scene="document"] .ac-doc .shot{transform:translateY(-34%)}
  /* diff: START already filling the frame (base offset), then the SAME ~0.5
     zoom distance/pace as before — top-left anchored so the controls stay */
  .ac-diff .shot{width:100%;top:0;transform:scale(1.5)}
  .stage[data-scene="diff"] .ac-diff .shot{transform:scale(2.0)}
  /* ask: zoom hard, top-left anchored so the reading edge stays (bleed right) */
  .ac-ask .shot{width:100%;left:0;top:4%;transform-origin:0 0;transform:scale(1)}
  .stage[data-scene="ask"] .ac-ask .shot{transform:scale(1.42)}
  /* outro takes more of the frame */
  .outro{gap:40px}
  .outro .wmk{width:min(740px,90vw)}
  .outro .tag{font-size:48px;margin-top:28px}
  .outro .url{font-size:36px}
  .outro .legal{font-size:18px;max-width:42ch;margin-top:18px}
</style>"""


def build_play_html(
    light: Path, dark: Path, doc_png: Path, width: int, height: int
) -> Path:
    board = (
        '<div class="lite">' + _svg(light) + "</div>"
        '<div class="dark">' + _svg(dark) + "</div>"
    )
    swap_css = """<style>
  .board svg{width:auto !important;height:auto !important;max-width:88%;max-height:80%}
  .board .lite,.board .dark{position:absolute;inset:0;display:grid;place-items:center}
  .stage[data-theme="dark"] .board .lite{opacity:0}
  .stage[data-theme="light"] .board .dark{opacity:0}
  .board .lite,.board .dark{transition:opacity .12s}
</style>"""
    doc = SKELETON.read_text().replace("<!--BOARD-->", board)
    # Real product shots into the four capability beats.
    doc = doc.replace("<!--AC_DESCRIBE-->", _describe_html(A / "describe.txt"))
    doc = doc.replace("<!--AC_DOC-->", f'<img src="{_b64_png(doc_png)}" alt="">')
    doc = doc.replace(
        "<!--AC_DIFF-->", f'<img src="{_b64_png(A / "diff_viewer.png")}" alt="">'
    )
    gen_py = next(
        p for p in (A / "gen").rglob("*.py") if p.name != "__init__.py"
    )
    doc = doc.replace("<!--AC_CODE-->", _code_html(gen_py))
    doc = doc.replace(
        "<!--AC_ASK-->", f'<img src="{_b64_png(A / "ask_crop.png")}" alt="">'
    )
    # The real LVKit wordmark — at the beat-drop reveal and the outro.
    wordmark = _svg(WORDMARK)
    doc = doc.replace("<!--WORDMARK_DROP-->", wordmark)
    doc = doc.replace("<!--WORDMARK-->", wordmark)
    doc = doc.replace(
        '<div class="frame">',
        _font_css() + swap_css + _fmt_css(width, height) + '<div class="frame">',
        1,
    )
    out = SKELETON.with_name("play.html")
    out.write_text(doc)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--light", type=Path, default=Path(".tmp/animatic/md5_light.svg"))
    ap.add_argument("--dark", type=Path, default=Path(".tmp/animatic/md5_dark.svg"))
    ap.add_argument(
        "--doc-png", type=Path,
        default=Path(
            ".tmp/animatic/docs/OpenG/MD5_Message_Digest_Binary_String_ogtk.png"
        ),
    )
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--out", type=Path, default=Path(".tmp/animatic/lvkit_launch.mp4"))
    args = ap.parse_args()

    play = build_play_html(
        args.light, args.dark, args.doc_png, args.width, args.height
    )
    vdir = SKELETON.parent / "_rec"
    vdir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="chrome", headless=True,
            args=["--force-color-profile=srgb", "--hide-scrollbars"],
        )
        ctx = browser.new_context(
            viewport={"width": args.width, "height": args.height},
            device_scale_factor=1,
            record_video_dir=str(vdir),
            record_video_size={"width": args.width, "height": args.height},
        )
        page = ctx.new_page()
        page.goto(play.resolve().as_uri())
        page.wait_for_timeout(int((args.seconds + 1.2) * 1000))  # one full loop + lead
        webm = page.video.path()
        ctx.close()
        browser.close()

    print(f"captured {webm}", flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-i", webm, "-t", str(args.seconds),
         "-vf", f"scale={args.width}:{args.height}:flags=lanczos,format=yuv420p",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-movflags", "+faststart", str(args.out)],
        check=True, capture_output=True,
    )
    Path(webm).unlink(missing_ok=True)
    mb = args.out.stat().st_size / 1e6
    print(f"wrote {args.out}  ({mb:.1f} MB, {args.width}x{args.height}, {args.seconds:.0f}s)")


if __name__ == "__main__":
    main()
