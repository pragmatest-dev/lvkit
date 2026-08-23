// LVKit — WEB (browser) extension host entry.
//
// The published extension is dual-entry: `main` (this file's desktop sibling
// ../extension.js) shells out to a native lvkit binary; `browser` (this file)
// runs lvkit in WebAssembly so hosted VS Codes (vscode.dev, GitHub Codespaces
// web, GitLab Web IDE, Cursor) — which have no local disk and no child_process —
// can still render .vi files. VS Code picks the entry per host.
//
// Architecture (proven by the pyodide spike): the extension host reads the .vi
// bytes via workspace.fs.readFile (works on a virtual filesystem) and posts them
// to a WEBVIEW; the webview (a real browser iframe) boots Pyodide + the lvkit /
// pylabview wheels and renders the SVG. This mirrors the desktop custom editor,
// swapping "spawn lvkit" for "render in-webview via wasm."
const vscode = require("vscode");

// Pyodide (Python 3.14 wasm) and every wheel are SELF-HOSTED under media/ —
// assembled by build/build-web-assets.sh (core + pruned package closure in
// media/pyodide, the lvkit/pylabview/networkx wheels in media/wheels). Nothing
// is fetched from a CDN or PyPI at runtime, so the extension renders under a
// strict CSP and works fully offline / air-gapped. The base URIs are resolved
// per-webview via webview.asWebviewUri (below), so no absolute host appears here.

class ViDocument {
  constructor(uri) {
    this.uri = uri;
  }
  dispose() {}
}

// Chunked base64 so a large VI's bytes don't blow the call stack through
// String.fromCharCode.apply(null, hugeArray).
function toBase64(bytes) {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) {
    s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  }
  return btoa(s);
}

// The lvkit + pylabview wheels shipped under media/wheels/, as webview URIs.
// Discovered (not hard-coded) so a version bump needs no code change; micropip
// parses the version from the wheel filename, so the real names are preserved.
async function wheelUris(webview, wheelsDir) {
  const entries = await vscode.workspace.fs.readDirectory(wheelsDir);
  const whls = entries
    .filter(([name, kind]) => kind === vscode.FileType.File && name.endsWith(".whl"))
    // lvkit LAST — it imports networkx + pylabview at import time, and
    // micropip.install(deps=False) does not resolve/order deps for us.
    .sort((a, b) => (a[0].startsWith("lvkit-") ? 1 : b[0].startsWith("lvkit-") ? -1 : 0))
    .map(([name]) =>
      webview.asWebviewUri(vscode.Uri.joinPath(wheelsDir, name)).toString()
    );
  return whls;
}

function activate(context) {
  const wheelsDir = vscode.Uri.joinPath(context.extensionUri, "media", "wheels");
  const pyodideDir = vscode.Uri.joinPath(context.extensionUri, "media", "pyodide");
  const provider = {
    openCustomDocument(uri) {
      return new ViDocument(uri);
    },
    async resolveCustomEditor(document, panel) {
      const webview = panel.webview;
      webview.options = {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      };
      let wheels;
      try {
        wheels = await wheelUris(webview, wheelsDir);
      } catch (e) {
        webview.html = errorHtml("LVKit web build is missing its wheels", String(e));
        return;
      }
      if (wheels.length === 0) {
        webview.html = errorHtml(
          "LVKit web build is missing its wheels",
          "No .whl files under media/wheels — run build-web-assets.sh."
        );
        return;
      }
      const pyodideBase = webview.asWebviewUri(pyodideDir).toString() + "/";
      webview.html = viewerHtml(webview, wheels, pyodideBase);
      webview.onDidReceiveMessage(async (m) => {
        if (m.type === "ready") {
          // THE hosted read path: virtual-FS-safe, no disk, no child_process.
          const bytes = await vscode.workspace.fs.readFile(document.uri);
          webview.postMessage({
            type: "vi",
            name: document.uri.path.split("/").pop(),
            b64: toBase64(bytes),
          });
        } else if (m.type === "log") {
          console.log("[lvkit-web]", m.text);
        }
      });
    },
  };
  context.subscriptions.push(
    vscode.window.registerCustomEditorProvider("lvkit.viPreview", provider, {
      webviewOptions: { retainContextWhenHidden: true },
      supportsMultipleEditorsPerDocument: false,
    })
  );
}

function errorHtml(title, message) {
  const esc = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  return `<!doctype html><meta charset="utf-8"><body style="font:13px/1.5 var(--vscode-font-family,system-ui);padding:12px">
<h3 style="color:var(--vscode-errorForeground)">${esc(title)}</h3>
<pre style="white-space:pre-wrap;color:var(--vscode-descriptionForeground)">${esc(message)}</pre></body>`;
}

function viewerHtml(webview, wheelUrls, pyodideBase) {
  // Strict CSP: everything (loader, wasm, wheels, fonts, images) is served from
  // the webview's own asset origin — no CDN, no remote host. Pyodide needs
  // 'unsafe-eval' (it compiles Python → JS) + 'wasm-unsafe-eval'; it runs its
  // interpreter in a blob: web worker.
  const csp = [
    "default-src 'none'",
    `script-src ${webview.cspSource} 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'`,
    `connect-src ${webview.cspSource} blob: data:`,
    "style-src 'unsafe-inline'",
    `img-src ${webview.cspSource} data: blob:`,
    "worker-src blob:",
    `font-src ${webview.cspSource}`,
  ].join("; ");
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="${csp}" />
<style>
  body { font: 13px/1.5 var(--vscode-font-family, system-ui); margin: 0; padding: 10px; }
  #status { font-family: var(--vscode-editor-font-family, monospace);
            color: var(--vscode-descriptionForeground); white-space: pre-wrap; }
  #view { margin-top: 10px; }
  #view svg { max-width: 100%; height: auto; }
  .meta { color: var(--vscode-descriptionForeground); margin-top: 8px; font-size: 12px; }
</style>
</head>
<body>
<div id="status">booting Pyodide…</div>
<div id="view"></div>
<div class="meta" id="meta"></div>
<script src="${pyodideBase}pyodide.js"></script>
<script>
const vscodeApi = acquireVsCodeApi();
const S = document.getElementById("status");
const log = m => { S.textContent = m; vscodeApi.postMessage({ type: "log", text: m }); };
const WHEELS = ${JSON.stringify(wheelUrls)};
let render = null;

async function boot() {
  try {
    log("loading Pyodide (Python 3.14, wasm)…");
    const pyodide = await loadPyodide({ indexURL: ${JSON.stringify(pyodideBase)} });
    log("loading pydantic / Pillow…");
    // Only the binary (emscripten) packages come from Pyodide: pydantic_core and
    // Pillow. networkx is a PURE-Python wheel installed below (deps=False) — NOT
    // Pyodide's networkx, which would drag in matplotlib+numpy (~25 MB) lvkit
    // never imports.
    await pyodide.loadPackage(["micropip", "pydantic", "Pillow"]);
    const micropip = pyodide.pyimport("micropip");
    log("installing networkx + pylabview + lvkit wheels…");
    // callKwargs so deps=False reaches Python as a kwarg (a plain JS object is a
    // positional dict, silently ignored → micropip tries PyPI → fails offline).
    // Everything lvkit imports is already present, so deps=False never fetches.
    // WHEELS is ordered lvkit-last (it imports networkx + pylabview at import).
    for (const w of WHEELS) { await micropip.install.callKwargs(w, { deps: false }); }
    render = pyodide.runPython(\`
import os
os.environ["LVKIT_CACHE_DIR"] = "/tmp/lvkitcache"
from pathlib import Path
from lvkit.render import render_vi_file_titled
def _render(data):
    Path("/tmp/in.vi").write_bytes(bytes(data.to_py()))
    svg, name = render_vi_file_titled(Path("/tmp/in.vi"))
    return [svg or "", name or ""]
_render
\`);
    log("ready — waiting for VI bytes…");
    vscodeApi.postMessage({ type: "ready" });
  } catch (e) { log("BOOT FAILED: " + e); }
}

window.addEventListener("message", ev => {
  const m = ev.data;
  if (m.type !== "vi" || !render) return;
  try {
    const bin = atob(m.b64);
    const u8 = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    const t0 = performance.now();
    const res = render(u8);
    const [svg, name] = res.toJs();
    res.destroy();
    document.getElementById("view").innerHTML = svg;
    document.getElementById("meta").textContent =
      (name || m.name) + "  ·  " + svg.length + " chars  ·  " +
      (performance.now() - t0).toFixed(0) + " ms";
    log("rendered via wasm ✓");
  } catch (e) { log("RENDER FAILED: " + e); }
});

boot();
</script>
</body>
</html>`;
}

exports.activate = activate;
exports.deactivate = function () {};
