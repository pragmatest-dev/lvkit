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

from flask import Flask, Response, request, send_from_directory

from lvkit.render import render_vi_file

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
    return send_from_directory(_STATIC, "index.html")


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

    with tempfile.TemporaryDirectory() as d:
        vi_path = pathlib.Path(d) / "upload.vi"
        vi_path.write_bytes(data)
        try:
            # expand_subvis=False: a standalone upload has no sibling subVI
            # files to resolve, so this stays entirely off the filesystem for
            # dependencies and renders unknown subVIs as fallback boxes.
            svg = render_vi_file(vi_path, expand_subvis=False)
        except Exception as exc:  # noqa: BLE001 - report, never 500 opaquely
            return _cors(Response(f"render failed: {exc}", 422))

    if not svg:
        return _cors(Response("could not render this VI", 422))
    return _cors(Response(svg, mimetype="image/svg+xml"))


if __name__ == "__main__":
    # Local dev only; Cloud Run uses gunicorn (see Dockerfile).
    app.run(host="0.0.0.0", port=8080)  # noqa: S104
