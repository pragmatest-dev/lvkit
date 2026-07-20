"""Cloud Run service: POST a .vi file, get an SVG block-diagram render back.

lvkit's extraction is pure Python (it shells out to `python -m pylabview.readRSRC`
— no LabVIEW, no native binary), so this containerises cleanly. The only runtime
need is a writable temp dir, which Cloud Run provides at /tmp (in-memory).

Endpoints:
  GET  /            -> the demo upload page (static/index.html)
  GET  /health      -> "ok"
  POST /render      -> multipart field "vi" (or raw body) => image/svg+xml
"""

from __future__ import annotations

import pathlib
import tempfile

from flask import Flask, Response, request

from lvkit.graph.loading import LoadMode
from lvkit.render import render_vi_file
from lvkit.render.render_viewer import build_render_viewer
from lvkit.render.style import css_var_theme
from lvkit.render.theme_web import (
    THEME_TOGGLE_BUTTON,
    THEME_TOGGLE_SCRIPT,
    theme_style_block,
)

app = Flask(__name__)
_STATIC = pathlib.Path(__file__).parent / "static"

# Reject oversized uploads early (Cloud Run caps requests at 32 MB anyway).
MAX_VI_BYTES = 25 * 1024 * 1024


def _cors(resp: Response) -> Response:
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return resp


@app.get("/")
def index() -> Response:
    # Inject the image-only theme (CSS + toggle button + script) into the
    # static page — the diagram area toggles dark/light, the page chrome
    # (defined once in index.html's own <style>, no data-theme overrides)
    # never does. See lvkit.render.theme_web for why this is scoped to
    # svg[data-lv-theme="dark"].
    page = (_STATIC / "index.html").read_text()
    page = page.replace("/* {{THEME_VARS}} */", theme_style_block())
    page = page.replace("{{THEME_TOGGLE_BUTTON}}", THEME_TOGGLE_BUTTON)
    page = page.replace("{{THEME_TOGGLE_SCRIPT}}", THEME_TOGGLE_SCRIPT)
    return Response(page, mimetype="text/html")


@app.get("/health")
def health() -> Response:
    return Response("ok", mimetype="text/plain")


@app.route("/render", methods=["POST", "OPTIONS"])
def render() -> Response:
    if request.method == "OPTIONS":
        return _cors(Response(status=204))

    upload = request.files.get("vi")
    data = upload.read() if upload else request.get_data()
    if not data:
        return _cors(Response("no VI uploaded (field 'vi' or raw body)", 400))
    if len(data) > MAX_VI_BYTES:
        return _cors(Response("VI too large", 413))

    # ?format=svg (default) -> bare SVG picture; ?format=html -> the full
    # self-contained interactive viewer (same one `lvkit render --format html`
    # emits, and the one the pragmatest /lvkit/render page embeds).
    fmt = (request.args.get("format") or "svg").lower()

    with tempfile.TemporaryDirectory() as d:
        vi_path = pathlib.Path(d) / "upload.vi"
        vi_path.write_bytes(data)
        try:
            # mode=LoadMode.NONE: a standalone upload has no sibling subVI files
            # to resolve, so render this VI's own diagram only (unknown subVIs
            # draw as fallback boxes) — entirely off the filesystem.
            if fmt == "html":
                # The standalone viewer carries its OWN light/dark toggle, so
                # its SVG is rendered theme_mode="auto" (no css-var injection).
                svg = render_vi_file(
                    vi_path, mode=LoadMode.NONE, theme_mode="auto",
                )
            else:
                # theme=css_var_theme(): every hex color becomes
                # var(--lv-<role>, <light-hex>) so the served demo page's
                # injected theme_style_block() CSS can recolor the diagram;
                # with no such CSS present the var() falls back to the same
                # light hex, so this is a no-op for any other caller.
                svg = render_vi_file(
                    vi_path, mode=LoadMode.NONE, theme=css_var_theme(),
                )
        except Exception as exc:  # noqa: BLE001 - report, never 500 opaquely
            return _cors(Response(f"render failed: {exc}", 422))

    if not svg:
        return _cors(Response("could not render this VI", 422))
    if fmt == "html":
        page = build_render_viewer(svg, title="Your VI")
        return _cors(Response(page, mimetype="text/html"))
    return _cors(Response(svg, mimetype="image/svg+xml"))


if __name__ == "__main__":
    # Local dev only; Cloud Run uses gunicorn (see Dockerfile).
    app.run(host="0.0.0.0", port=8080)  # noqa: S104
