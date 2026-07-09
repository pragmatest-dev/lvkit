# lvkit on Cloud Run — VI → SVG service

A tiny HTTP service: `POST` a `.vi` file, get an SVG block-diagram render back.
A page can upload a VI and drop the returned SVG straight into the DOM.

## Why this works cleanly

lvkit needs **no LabVIEW and no native binary**. Extraction shells out to
`python -m pylabview.readRSRC` (pure Python, an lvkit dependency), and
`render_vi_file()` returns an SVG string. The container just needs Python +
lvkit + a writable temp dir — Cloud Run provides `/tmp` (in-memory).

Uploads are rendered with `expand_subvis=False`, which is effectively an
**offline mode**: a standalone VI has no sibling subVI files, so the render
never reaches the filesystem for dependencies (unresolved subVIs draw as
fallback boxes). It only reads lvkit's own **bundled** primitive/vilib JSON,
which ships inside the image. Nothing to configure.

## Files

| file | purpose |
|------|---------|
| `main.py` | Flask app: `GET /` demo page, `GET /health`, `POST /render` |
| `Dockerfile` | installs lvkit (this branch) + flask/gunicorn; **build context = repo root** |
| `cloudbuild.yaml` | build → push → deploy in one `gcloud builds submit` |
| `static/index.html` | drop-a-VI demo page served at `/` |
| `requirements.txt` | web deps for local dev |

## Deploy

From the **repo root** (not this folder — the Docker build needs the lvkit
source in context):

```bash
gcloud builds submit --config deploy/cloudrun/cloudbuild.yaml \
  --substitutions=_IMAGE=gcr.io/$(gcloud config get-value project)/lvkit-render,_REGION=us-central1 .
```

That builds `deploy/cloudrun/Dockerfile`, pushes the image, and deploys the
`lvkit-render` service (`--allow-unauthenticated`, 1Gi, 1 CPU, 120s, max 5
instances — tune in `cloudbuild.yaml`).

### Local test

```bash
pip install -e .            # from repo root (installs lvkit + pylabview)
pip install -r deploy/cloudrun/requirements.txt
cd deploy/cloudrun && python main.py          # http://localhost:8080
# or exactly like prod:
docker build -f deploy/cloudrun/Dockerfile -t lvkit-render .   # from repo root
docker run -p 8080:8080 -e PORT=8080 lvkit-render
```

## Calling it from a page

```js
const fd = new FormData();
fd.append('vi', fileInput.files[0]);
const svg = await (await fetch('https://YOUR-URL/render', { method:'POST', body: fd })).text();
document.getElementById('out').innerHTML = svg;   // static picture
```

### Heads-up: interactive frames

The SVG embeds an inline `<script>` for the case/stacked-sequence **frame
selector**. Browsers do **not** run scripts inserted via `innerHTML`, so a VI
rendered that way is a static image. To keep the frame toggles working, load the
SVG so its script executes — e.g. an `<iframe>`:

```js
const url = URL.createObjectURL(new Blob([svg], {type:'image/svg+xml'}));
iframe.src = url;   // scripts inside the SVG run here
```

## Notes / hardening

- **Untrusted input**: you're parsing arbitrary uploaded binaries. Cloud Run's
  per-request container is the isolation boundary; `main.py` caps size at 25 MB
  (Cloud Run's own request limit is 32 MB).
- **Memory**: `/tmp` is RAM on Cloud Run and holds the upload + extracted XML;
  1Gi is a safe start. Raise `--memory`/lower concurrency for large VIs.
- **Auth**: `--allow-unauthenticated` is for a public demo. Drop it (and add an
  API key / IAM) for anything real.
- **Cold start**: first request imports lvkit + pylabview (~1s); fine for a demo.
