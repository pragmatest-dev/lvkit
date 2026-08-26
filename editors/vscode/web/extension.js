// LVKit — WEB (browser) extension host entry.
//
// The published extension is dual-entry: `main` (this file's desktop sibling
// ../extension.js) shells out to a native lvkit binary; `browser` (this file)
// runs lvkit in WebAssembly so hosted VS Codes (vscode.dev, GitHub Codespaces
// web, GitLab Web IDE, Cursor) — which have no local disk and no child_process —
// can still render AND diff .vi files. VS Code picks the entry per host.
//
// Architecture (proven by the pyodide spike): the extension host reads .vi bytes
// via workspace.fs.readFile (works on a virtual filesystem) and posts them to a
// WEBVIEW; the webview boots Pyodide + the lvkit / pylabview wheels and renders.
// The SAME boot page (pyodideWebviewHtml) serves two jobs — a single-VI render
// (the custom editor) and a two-VI visual diff (the lvkit.diffVI command).
//
// Everything is SELF-HOSTED under media/ (build-web-assets.sh): the Pyodide core
// + pruned package closure in media/pyodide, and the lvkit/pylabview/networkx
// wheels in media/wheels. Nothing is fetched from a CDN or PyPI at runtime.
//
// DIAGNOSTICS: several web-only paths (SubVI staging/resolution, the Git diff
// integration, the webview CSP/iframe) can't be verified headlessly, so this
// entry logs generously to the "LVKit (Web)" Output channel — boot phases, every
// render/diff with timing, SubVI staging counts + navigation, diff source
// resolution, and full error text. On any failure the channel is revealed. That
// channel is the first place to look when something misbehaves in a hosted
// editor.
const vscode = require("vscode");

let output = null;
function log(msg) {
  const line = `[${new Date().toISOString()}] ${msg}`;
  if (output) output.appendLine(line);
  console.log("[lvkit-web]", msg);
}
function logError(context, e) {
  const detail = e && e.stack ? e.stack : String(e);
  log(`ERROR (${context}): ${detail}`);
  if (output) output.show(true); // reveal (preserve focus) so users can copy it
}

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

// Pyodide runtime + wheels, loaded from public CDNs: jsDelivr for the runtime,
// PyPI for the pure-Python wheels (micropip installs each by NAME, deps:false;
// lvkit LAST since it imports networkx + pylabview at import time). Versions pinned
// to match the render (uv.lock) and lvkit's own release; keep in sync on a bump.
const CDN_PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.5/full/";
const CDN_WHEELS = ["networkx==3.4.2", "pylabview==0.1.2"]; // lvkit appended (its own version)

// The web extension runs ONLY in hosted editors (vscode.dev, GitLab Web IDE,
// Cursor, Gitpod, Codespaces) — all online — so we load Pyodide + wheels straight
// from the CDN rather than bundling ~13 MB of media/ in the VSIX. jsDelivr serves
// the Pyodide runtime far faster than the marketplace / webview-resource layer
// serves a self-hosted copy (measured: ~3 s vs. minutes on vscode.dev for the same
// VI), so this is smaller AND faster. No bundled fallback: an air-gapped browser
// IDE isn't a real host, and Open VSX can't serve bundled media/ anyway
// (eclipse-openvsx/openvsx#2099).
async function pyodideAssets(context) {
  const lvkitSpec = `lvkit==${context.extension.packageJSON.version}`;
  return { wheels: [...CDN_WHEELS, lvkitSpec], pyodideBase: CDN_PYODIDE };
}

// ── The shared Pyodide "engine" (a PANEL WebviewView, not an editor tab) ─────
// The engine runs Pyodide in a WebviewView contributed to the Panel (like Output/
// Terminal), kept alive with retainContextWhenHidden — so it never costs editor
// space. It boots Pyodide ONCE, holds the render cache (persistent MEMFS, so a
// re-render hits), and answers render/diff jobs. Each VI tab / diff is a thin
// DISPLAY that requests work through the host; boot progress is broadcast to their
// loaders. The view is revealed once to instantiate, then can be collapsed.
let _engine = null;
let _engineWaiters = [];
const _progressListeners = new Set();

function onEngineProgress(fn) {
  _progressListeners.add(fn);
  return () => _progressListeners.delete(fn);
}

function makeEngine(webview) {
  const jobs = new Map();
  let seq = 0;
  let resolveReady, rejectReady;
  const ready = new Promise((res, rej) => { resolveReady = res; rejectReady = rej; });
  return {
    webview,
    ready,
    jobs,
    resolveReady,
    rejectReady,
    run(job) {
      return new Promise((resolve, reject) => {
        const id = ++seq;
        jobs.set(id, { resolve, reject });
        webview.postMessage({ ...job, id });
      });
    },
  };
}

// Boots Pyodide when the engine view is (re)resolved, and wires the job protocol.
function engineViewProvider(context) {
  return {
    resolveWebviewView(view) {
      view.webview.options = {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      };
      const engine = makeEngine(view.webview);
      view.webview.onDidReceiveMessage((m) => {
        if (!m) return;
        if (m.type === "engineReady") engine.resolveReady();
        else if (m.type === "progress") _progressListeners.forEach((fn) => fn(m));
        else if (m.type === "result") { const j = engine.jobs.get(m.id); if (j) { engine.jobs.delete(m.id); j.resolve(m.html); } }
        else if (m.type === "jobError") { const j = engine.jobs.get(m.id); if (j) { engine.jobs.delete(m.id); j.reject(new Error(m.error)); } }
        else if (m.type === "log") log(`  [engine] ${m.text}`);
        else if (m.type === "error") logError("engine", m.text);
      });
      view.onDidDispose(() => {
        if (_engine === engine) _engine = null;
        engine.rejectReady(new Error("engine view disposed"));
        engine.jobs.forEach((j) => j.reject(new Error("engine view disposed")));
        engine.jobs.clear();
      });
      _engine = engine;
      const wake = () => { const waiters = _engineWaiters; _engineWaiters = []; waiters.forEach((r) => r(engine)); };
      pyodideAssets(context)
        .then((assets) => { view.webview.html = engineHtml(view.webview, assets.wheels, assets.pyodideBase); wake(); })
        .catch((e) => { view.webview.html = errorHtml("LVKit web build is missing its wheels", String(e)); logError("engine assets", e); engine.rejectReady(new Error(String(e))); wake(); });
    },
  };
}

// Get the engine, revealing the Panel view to instantiate it the first time.
function getEngine() {
  if (_engine) return Promise.resolve(_engine);
  const p = new Promise((resolve) => { _engineWaiters.push(resolve); });
  // Reveal the engine view just long enough to instantiate it, then collapse the
  // panel so the editor never stays split — retainContextWhenHidden keeps Pyodide
  // booting while the panel is hidden, and the engine is reused for every later
  // render (no re-reveal). The user can reopen the panel to watch it if they want.
  vscode.commands.executeCommand("lvkit.engine.focus").then(undefined, () => {});
  p.then(() => vscode.commands.executeCommand("workbench.action.closePanel").then(undefined, () => {}));
  return p;
}

// ---- SubVI staging ----------------------------------------------------------
// data-lv-vi-rel (the SubVI click-nav identity) is only emitted when the renderer
// can RESOLVE each SubVI to an on-disk file relative to its caller. Pyodide has
// no access to the workspace FS, so before a render we mirror the VI's
// workspace-folder subtree of .vi files into Pyodide under /proj, preserving
// relative layout — then render /proj/<the VI>. Bounded so opening one VI in a
// huge repo can't read the whole tree; the opened VI is always staged even if
// the cap trips (it just renders with fewer clickable SubVIs).
const MAX_STAGE_FILES = 400;
const MAX_STAGE_BYTES = 64 * 1024 * 1024;

async function stageWorkspaceSubtree(uri) {
  const folder = vscode.workspace.getWorkspaceFolder(uri);
  const root = folder ? folder.uri : uri.with({ path: uri.path.replace(/\/[^/]*$/, "") });
  const files = [];
  let bytes = 0;
  let capped = false;
  async function walk(dirUri, relDir) {
    if (capped) return;
    let entries;
    try {
      entries = await vscode.workspace.fs.readDirectory(dirUri);
    } catch (e) {
      log(`  stage: cannot list ${dirUri.toString()} — ${e}`);
      return;
    }
    for (const [name, kind] of entries) {
      if (capped) return;
      const rel = relDir ? `${relDir}/${name}` : name;
      const child = vscode.Uri.joinPath(dirUri, name);
      if (kind === vscode.FileType.Directory) {
        await walk(child, rel);
      } else if (kind === vscode.FileType.File && name.toLowerCase().endsWith(".vi")) {
        if (files.length >= MAX_STAGE_FILES || bytes >= MAX_STAGE_BYTES) {
          capped = true;
          return;
        }
        try {
          const data = await vscode.workspace.fs.readFile(child);
          bytes += data.length;
          files.push({ rel, b64: toBase64(data) });
        } catch (e) {
          log(`  stage: cannot read ${rel} — ${e}`);
        }
      }
    }
  }
  await walk(root, "");
  const renderRel = uri.path.startsWith(root.path)
    ? uri.path.slice(root.path.length).replace(/^\/+/, "")
    : uri.path.split("/").pop();
  // The opened VI must be present even if the walk capped out before reaching it.
  if (!files.some((f) => f.rel === renderRel)) {
    const data = await vscode.workspace.fs.readFile(uri);
    files.push({ rel: renderRel, b64: toBase64(data) });
  }
  return { files, renderRel, capped, root: root.toString() };
}

// ---- SubVI click-navigation -------------------------------------------------
// The injected iframe handler posts `lvkitOpenVI` up to the webview, which
// relays to the host; the host opens the SubVI in a new tab through the same
// custom editor. Mirrors desktop, minus the subprocess.
async function openSubVI(document, rel) {
  if (!rel || typeof rel !== "string") {
    log("openSubVI: message had no rel path");
    vscode.window.showWarningMessage("lvkit: SubVI navigation message had no path.");
    return;
  }
  // joinPath normalizes the ".." segments of the POSIX rel path against the
  // document URI — virtual-FS-safe. joinPath(<vi>, "..") steps out of the VI
  // file to its directory before applying rel.
  const target = vscode.Uri.joinPath(document.uri, "..", rel);
  log(`openSubVI: "${rel}" -> ${target.toString()}`);
  try {
    await vscode.workspace.fs.stat(target);
  } catch (_) {
    log(`openSubVI: not found — ${target.toString()}`);
    vscode.window.showWarningMessage(`lvkit: could not open SubVI "${rel}" (not found).`);
    return;
  }
  await vscode.commands.executeCommand("vscode.open", target);
}

// ---- Visual diff (lvkit.diffVI) --------------------------------------------
// Mirrors the desktop diff's source resolution, but every read goes through
// workspace.fs (virtual-FS-safe) instead of shelling `git show`.
function gitRawRef(uri) {
  try {
    const r = JSON.parse(uri.query || "{}").ref;
    return r || "HEAD";
  } catch (_) {
    return "HEAD";
  }
}
function gitUriWithRef(gitUri, ref) {
  let q = {};
  try {
    q = JSON.parse(gitUri.query || "{}");
  } catch (_) {
    /* empty query */
  }
  q.ref = ref;
  return gitUri.with({ query: JSON.stringify(q) });
}
function sideOf(uri) {
  return uri.scheme === "file" ? { uri, ref: null } : { uri, ref: gitRawRef(uri) };
}
// A working-tree file diffs against HEAD; the committed blob is read through a
// git: URI built by the Git extension API (binary-safe — repo.show returns text,
// which would corrupt a .vi).
async function headGitUri(fileUri) {
  const ext = vscode.extensions.getExtension("vscode.git");
  if (!ext) {
    log("diff: vscode.git extension not present");
    return null;
  }
  const exports = ext.isActive ? ext.exports : await ext.activate();
  const api = exports.getAPI(1);
  return typeof api.toGitUri === "function" ? api.toGitUri(fileUri, "HEAD") : null;
}
async function resolveDiffSides(target) {
  const tab =
    vscode.window.tabGroups &&
    vscode.window.tabGroups.activeTabGroup &&
    vscode.window.tabGroups.activeTabGroup.activeTab &&
    vscode.window.tabGroups.activeTabGroup.activeTab.input;
  if (
    tab &&
    tab.original &&
    tab.modified &&
    /\.vi$/i.test(tab.modified.path || "") &&
    (tab.modified.toString() === target.toString() ||
      tab.original.toString() === target.toString())
  ) {
    log("diff: two-sided diff editor input");
    return { before: sideOf(tab.original), after: sideOf(tab.modified) };
  }
  if (target.scheme !== "file") {
    const ref = gitRawRef(target);
    log(`diff: committed version (${target.scheme}:) ref=${ref} → compare vs ${ref}~1`);
    return {
      before: { uri: gitUriWithRef(target, ref + "~1"), ref: ref + "~1" },
      after: { uri: target, ref },
    };
  }
  const head = await headGitUri(target);
  if (!head) {
    throw new Error(
      "Git extension unavailable — open the committed version of this .vi to diff it."
    );
  }
  log("diff: working-tree file → compare vs HEAD");
  return { before: { uri: head, ref: "HEAD" }, after: { uri: target, ref: null } };
}

async function diffVI(context, arg) {
  const target = arg && (arg.resourceUri || arg);
  if (!target || !/\.vi$/i.test(target.path || "")) {
    vscode.window.showErrorMessage("lvkit: no .vi selected.");
    return;
  }
  const name = target.path.split("/").pop();
  log(`diffVI: ${target.toString()}`);
  try {
    const sides = await resolveDiffSides(target);
    let beforeBytes;
    try {
      beforeBytes = await vscode.workspace.fs.readFile(sides.before.uri);
    } catch (e) {
      log(`diff: no 'before' content (${sides.before.ref}) — ${e}`);
      vscode.window.showInformationMessage(
        `lvkit: no earlier revision to compare — "${name}" looks like its first commit.`
      );
      return;
    }
    if (!beforeBytes || beforeBytes.length === 0) {
      vscode.window.showInformationMessage(
        `lvkit: the "before" side of "${name}" has no committed content (newly added).`
      );
      return;
    }
    const afterBytes = await vscode.workspace.fs.readFile(sides.after.uri);
    log(
      `diff: before=${beforeBytes.length}B (${sides.before.ref}), ` +
        `after=${afterBytes.length}B (${sides.after.ref || "working copy"})`
    );
    const panel = vscode.window.createWebviewPanel(
      "lvkitDiff",
      `VI Diff: ${name}`,
      vscode.ViewColumn.Active,
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, "media")],
      }
    );
    const engine = await getEngine();
    const offProgress = onEngineProgress((m) =>
      panel.webview.postMessage({ type: "progress", label: m.label, pct: m.pct })
    );
    panel.onDidDispose(() => offProgress());
    panel.webview.onDidReceiveMessage(async (m) => {
      if (m.type === "ready") {
        try {
          await engine.ready;
          const html = await engine.run({
            type: "diff",
            beforeB64: toBase64(beforeBytes),
            afterB64: toBase64(afterBytes),
            beforeRef: sides.before.ref || "",
            afterRef: sides.after.ref || "",
          });
          // Empty body = lvkit declined (missing diagram geometry). Surface the
          // error card rather than a blank page, matching the desktop viewer.
          if (html && html.trim()) {
            panel.webview.postMessage({ type: "showResult", html, subvi: false });
          } else {
            panel.webview.postMessage({ type: "jobError", error: "Diff declined — required diagram geometry is missing." });
          }
        } catch (e) {
          panel.webview.postMessage({ type: "jobError", error: String(e && e.message ? e.message : e) });
          logError("diff", e);
        } finally {
          offProgress();
        }
      } else if (m.type === "log") {
        log(`  [diff display] ${m.text}`);
      } else if (m.type === "error") {
        logError("diff display", m.text);
      }
    });
    panel.webview.html = displayHtml(panel.webview);
  } catch (e) {
    logError("diffVI", e);
    vscode.window.showErrorMessage(
      "lvkit: diff failed — see the 'LVKit (Web)' Output channel for details."
    );
  }
}

function activate(context) {
  output = vscode.window.createOutputChannel("LVKit (Web)");
  context.subscriptions.push(output);
  log("LVKit web extension activated (Pyodide/wasm backend).");

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
      log(`open VI: ${document.uri.toString()}`);
      const engine = await getEngine();
      // Relay the engine's boot progress to THIS display's loader until it renders.
      const offProgress = onEngineProgress((m) =>
        webview.postMessage({ type: "progress", label: m.label, pct: m.pct })
      );
      panel.onDidDispose(() => offProgress());
      // Register BEFORE setting html so the display's "ready" is never missed.
      webview.onDidReceiveMessage(async (m) => {
        if (m.type === "ready") {
          try {
            await engine.ready;
            let job;
            try {
              const staged = await stageWorkspaceSubtree(document.uri);
              log(
                `render: staged ${staged.files.length} VI(s) under ${staged.root}` +
                  `${staged.capped ? ` (CAPPED at ${MAX_STAGE_FILES} — some SubVI links may not resolve)` : ""}` +
                  `, entry=${staged.renderRel}`
              );
              job = { type: "render", files: staged.files, renderRel: staged.renderRel };
            } catch (e) {
              logError("stage", e);
              // Degrade: render just the opened VI (no SubVI links).
              const data = await vscode.workspace.fs.readFile(document.uri);
              const only = document.uri.path.split("/").pop();
              job = { type: "render", files: [{ rel: only, b64: toBase64(data) }], renderRel: only };
            }
            const html = await engine.run(job);
            // Empty body = lvkit declined (missing diagram geometry). Surface the
            // error card rather than a blank page, matching the desktop viewer.
            if (html && html.trim()) {
              webview.postMessage({ type: "showResult", html, subvi: true });
            } else {
              webview.postMessage({ type: "jobError", error: "Render declined — required diagram geometry is missing." });
            }
          } catch (e) {
            webview.postMessage({ type: "jobError", error: String(e && e.message ? e.message : e) });
            logError("render", e);
          } finally {
            offProgress();
          }
        } else if (m.type === "lvkitOpenVI") {
          await openSubVI(document, m.rel);
        } else if (m.type === "log") {
          log(`  [display] ${m.text}`);
        } else if (m.type === "error") {
          logError("render display", m.text);
        }
      });
      webview.html = displayHtml(webview);
    },
  };
  context.subscriptions.push(
    vscode.window.registerCustomEditorProvider("lvkit.viPreview", provider, {
      webviewOptions: { retainContextWhenHidden: true },
      supportsMultipleEditorsPerDocument: false,
    })
  );
  context.subscriptions.push(
    vscode.commands.registerCommand("lvkit.diffVI", (arg) => diffVI(context, arg))
  );
  // The shared Pyodide engine lives in this Panel view (kept alive when hidden).
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider("lvkit.engine", engineViewProvider(context), {
      webviewOptions: { retainContextWhenHidden: true },
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

// Hosts the engine loads from (pyodideAssets): jsDelivr serves the Pyodide runtime
// (pyodide.js + wasm/data), and micropip resolves wheels via the PyPI JSON index
// (pypi.org) and downloads them (files.pythonhosted.org).
const CDN_HOSTS = "https://cdn.jsdelivr.net https://files.pythonhosted.org https://pypi.org";

function webviewCsp(webview) {
  return [
    "default-src 'none'",
    `script-src ${webview.cspSource} 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net`,
    `connect-src ${webview.cspSource} blob: data: ${CDN_HOSTS}`,
    "style-src 'unsafe-inline'",
    `img-src ${webview.cspSource} data: blob:`,
    "worker-src blob:",
    `font-src ${webview.cspSource}`,
    "frame-src 'self'",
  ].join("; ");
}

// The hidden ENGINE page: boots Pyodide + installs the wheels ONCE, keeps them (and
// the render cache under LVKIT_CACHE_DIR) alive, and answers render/diff jobs from
// the host — each job carries an `id`, the reply is {type:'result', id, html} or
// {type:'jobError', id, error}. It never shows a diagram; the DISPLAY tabs do. Boot
// phases are posted as {type:'progress'} so waiting displays' loaders reflect them.
function engineHtml(webview, wheelUrls, pyodideBase) {
  const csp = webviewCsp(webview);
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="${csp}" />
<style>
  body { margin: 0; padding: 12px; font: 12px/1.5 var(--vscode-font-family, system-ui);
         color: var(--vscode-descriptionForeground); background: var(--vscode-editor-background); }
</style>
</head>
<body>
<p id="s">Starting LVKit… the first time takes a few seconds.</p>
<script src="${pyodideBase}pyodide.js"></script>
<script>
const api = acquireVsCodeApi();
const S = document.getElementById("s");
const log = m => api.postMessage({ type: "log", text: String(m) });
const PHASES = [
  { re: /booting/i,            label: "Starting LVKit…",   pct: 8 },
  { re: /loading Pyodide/i,    label: "Setting up LVKit…", pct: 26 },
  { re: /pydantic|Pillow/i,    label: "Loading LVKit…",    pct: 50 },
  { re: /installing.*wheels/i, label: "Almost ready…",     pct: 74 },
  { re: /ready/i,              label: "Ready",             pct: 90 },
];
// The raw status string is a match key + a log line; the panel/loader show the plain label.
function status(m) { log(m); const p = PHASES.find(p => p.re.test(m)); const label = p ? p.label : String(m); S.textContent = label; api.postMessage({ type: "progress", label, pct: p ? p.pct : null }); }
let pyodide = null, renderFn = null, diffFn = null;
function u8(b64) { const bin = atob(b64); const a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); return a; }
function writeFile(pth, data) { const dir = pth.slice(0, pth.lastIndexOf("/")); if (dir) pyodide.FS.mkdirTree(dir); pyodide.FS.writeFile(pth, data); }
const WHEELS = ${JSON.stringify(wheelUrls)};

async function boot() {
  try {
    status("loading Pyodide (Python 3.14, wasm)…");
    pyodide = await loadPyodide({ indexURL: ${JSON.stringify(pyodideBase)} });
    status("loading pydantic / Pillow…");
    await pyodide.loadPackage(["micropip", "pydantic", "Pillow"]);
    const micropip = pyodide.pyimport("micropip");
    status("installing networkx + pylabview + lvkit wheels…");
    for (const w of WHEELS) { await micropip.install.callKwargs(w, { deps: false }); }
    pyodide.runPython(\`
import os
os.environ["LVKIT_CACHE_DIR"] = "/tmp/lvkitcache"
from pathlib import Path
from lvkit import __version__
from lvkit.output_cache import (
    cached_diff, cached_render, diff_options_tag, render_options_tag,
)

# Same shared cached cores the CLI/MCP use: look up first, build + refresh the
# slot on a miss. The cache lives in MEMFS (LVKIT_CACHE_DIR above), so repeat
# opens of a VI in this session are instant hits. theme_mode="auto" lets the
# viewer's light/dark toggle work live; SubVIs are staged next to the caller so
# caller-relative resolution still emits data-lv-vi-rel.
def _render(vi_path):
    return cached_render(
        Path(vi_path),
        fmt="html",
        options=render_options_tag("html", "auto", None),
        version=__version__,
        theme_mode="auto",
    ) or ""

def _diff(before, after, before_ref, after_ref):
    Path("/tmp/before.vi").write_bytes(bytes(before.to_py()))
    Path("/tmp/after.vi").write_bytes(bytes(after.to_py()))
    return cached_diff(
        Path("/tmp/before.vi"), Path("/tmp/after.vi"),
        fmt="html",
        options=diff_options_tag("html", False, before_ref or None, after_ref or None),
        version=__version__,
        before_ref=(before_ref or None),
        after_ref=(after_ref or None),
    ) or ""
\`);
    renderFn = pyodide.globals.get("_render");
    diffFn = pyodide.globals.get("_diff");
    status("ready");
    S.textContent = "LVKit runs here in the background to draw your VIs. You can hide this panel — it keeps working.";
    api.postMessage({ type: "engineReady" });
  } catch (e) { api.postMessage({ type: "error", text: String(e && e.stack ? e.stack : e) }); }
}

// Render/diff jobs from the host; reply by id.
window.addEventListener("message", ev => {
  const m = ev.data;
  if (!m || m.id == null) return;
  try {
    if (m.type === "render") {
      const t0 = performance.now();
      for (const f of m.files) { writeFile("/proj/" + f.rel, u8(f.b64)); }
      const html = renderFn("/proj/" + m.renderRel);
      log("rendered via wasm in " + (performance.now() - t0).toFixed(0) + " ms");
      api.postMessage({ type: "result", id: m.id, html });
    } else if (m.type === "diff") {
      const t0 = performance.now();
      const html = diffFn(u8(m.beforeB64), u8(m.afterB64), m.beforeRef || "", m.afterRef || "");
      log("diffed via wasm in " + (performance.now() - t0).toFixed(0) + " ms");
      api.postMessage({ type: "result", id: m.id, html });
    }
  } catch (e) { api.postMessage({ type: "jobError", id: m.id, error: String(e && e.message ? e.message : e) }); }
});

boot();
</script>
</body>
</html>`;
}

// A thin DISPLAY page (one per VI tab / diff): shows the loader (driven by the
// engine's boot {type:'progress'} relayed via the host), then swaps in the result
// HTML from {type:'showResult'} as a srcdoc <iframe> so its zoom/theme scripts run
// in a fresh context. SubVI clicks bubble up from the srcdoc and are relayed to the
// host as {type:'lvkitOpenVI'}. No Pyodide here.
function displayHtml(webview) {
  const csp = webviewCsp(webview);
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="${csp}" />
<style>
  html, body { margin: 0; padding: 0; height: 100%; }
  body { font: 13px/1.5 var(--vscode-font-family, system-ui);
         color: var(--vscode-foreground); background: var(--vscode-editor-background); }
  #viewer { border: 0; width: 100%; height: 100vh; display: none; }
  #loader { position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; }
  #loader.hidden { display: none; }
  .lv-card { width: min(340px, 76vw); text-align: center; padding: 8px 20px 22px; }
  .lv-mark { width: 46px; height: 46px; margin: 0 auto 14px; display: block; }
  .lv-mark .spin { transform-origin: 24px 24px; animation: lv-rot 1.05s linear infinite; }
  @keyframes lv-rot { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .lv-mark .spin { animation: none; } }
  .lv-title { font-size: 14px; font-weight: 600; margin: 0 0 3px; }
  .lv-phase { color: var(--vscode-descriptionForeground); min-height: 1.4em; font-size: 12.5px; margin: 0 0 14px; }
  .lv-bar { height: 4px; border-radius: 3px; overflow: hidden; background: rgba(127,127,127,.2); }
  .lv-fill { height: 100%; width: 6%; border-radius: 3px;
             background: var(--vscode-progressBar-background, #3794ff); transition: width .35s ease; }
  .lv-hint { color: var(--vscode-descriptionForeground); opacity: .7; font-size: 11px; margin: 12px 0 0; }
  .lv-err { color: var(--vscode-errorForeground); white-space: pre-wrap; text-align: left;
            font-family: var(--vscode-editor-font-family, monospace); font-size: 12px; margin-top: 10px; }
  .lv-err[hidden] { display: none; }
</style>
</head>
<body>
<div id="loader">
  <div class="lv-card">
    <svg class="lv-mark" viewBox="0 0 48 48" fill="none" aria-hidden="true">
      <circle cx="24" cy="24" r="18" stroke="var(--vscode-descriptionForeground)" stroke-opacity=".22" stroke-width="4"/>
      <path class="spin" d="M24 6a18 18 0 0 1 18 18" stroke="var(--vscode-progressBar-background,#3794ff)" stroke-width="4" stroke-linecap="round"/>
    </svg>
    <p class="lv-title" id="lvTitle">Rendering VI…</p>
    <p class="lv-phase" id="lvPhase" hidden>Starting…</p>
    <div class="lv-bar" id="lvBar" hidden><div class="lv-fill" id="lvFill"></div></div>
    <p class="lv-hint" id="lvHint" hidden>The first VI takes a few seconds to get started. After that they open quickly.</p>
    <pre class="lv-err" id="lvErr" hidden></pre>
  </div>
</div>
<iframe id="viewer" title="LVKit VI viewer"></iframe>
<script>
const api = acquireVsCodeApi();
const L = {
  loader: document.getElementById("loader"),
  title: document.getElementById("lvTitle"),
  phase: document.getElementById("lvPhase"),
  bar: document.getElementById("lvBar"),
  fill: document.getElementById("lvFill"),
  hint: document.getElementById("lvHint"),
  err: document.getElementById("lvErr"),
};
// Progress events flow only during the one-time cold boot (warm renders fire
// none) — so revealing the bar/phase/hint here shows the detailed loader for a
// cold start and leaves a warm open as just the spinner + title.
function setPhase(label, pct) {
  L.phase.hidden = false; L.bar.hidden = false; L.hint.hidden = false;
  if (label != null) L.phase.textContent = label; if (pct != null) L.fill.style.width = pct + "%";
}
// The viewer CHROME (page frame + buttons) follows the VS CODE theme, not the OS.
// VS Code stamps the kind on this webview's <body> (data-vscode-theme-kind /
// vscode-dark|light class); resolve it to light/dark and set data-viewer-theme on
// the srcdoc iframe's <html>, which the viewer CSS reads. The DIAGRAM stays
// independent (defaults light, its own ☀/☾ toggle) — this only drives chrome.
function vscodeThemeKind() {
  const k = document.body.getAttribute("data-vscode-theme-kind") || document.body.className || "";
  return /light/.test(k) ? "light" : "dark";
}
function applyChromeTheme() {
  const f = document.getElementById("viewer");
  const doc = f && f.contentDocument;
  if (doc && doc.documentElement) doc.documentElement.setAttribute("data-viewer-theme", vscodeThemeKind());
}
// Re-apply when the user switches VS Code theme (the class/attr on <body> mutates).
new MutationObserver(applyChromeTheme).observe(document.body, { attributes: true, attributeFilter: ["class", "data-vscode-theme-kind"] });
function showResult(html) {
  const f = document.getElementById("viewer");
  f.onload = applyChromeTheme;   // srcdoc loads async — theme it once its DOM exists
  f.srcdoc = html;
  f.style.display = "block";
  L.loader.classList.add("hidden");
}
function showError(msg) {
  L.loader.classList.remove("hidden");
  L.title.textContent = "Couldn’t render this VI";
  setPhase("", 100);
  L.fill.style.background = "var(--vscode-errorForeground)";
  L.err.hidden = false;
  L.err.textContent = String(msg);
}
// Inject SubVI click-navigation into a RENDER's viewer HTML (which starts
// <!doctype>\\n<meta charset='utf-8'>). Runs inside the srcdoc iframe; posts up to
// THIS frame, which relays to the host.
function injectSubviNav(html) {
  const script = "<scr" + "ipt>(function(){"
    + "function openVI(el){var rel=el.getAttribute('data-lv-vi-rel'); if(rel) window.parent.postMessage({type:'lvkitOpenVI', rel:rel}, '*');}"
    + "function wire(){var els=document.querySelectorAll('[data-lv-vi-rel]'); for(var i=0;i<els.length;i++){els[i].style.cursor='pointer'; els[i].addEventListener('click', function(ev){openVI(ev.currentTarget);});}}"
    + "if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', wire); else wire();"
    + "})();</scr" + "ipt>";
  return /<meta charset=['"]utf-8['"]>/i.test(html)
    ? html.replace(/<meta charset=['"]utf-8['"]>/i, m => m + script)
    : script + html;
}
window.addEventListener("message", ev => {
  const m = ev.data;
  if (!m) return;
  // Bubbled up from the srcdoc viewer (a SubVI click) -> relay to the host.
  if (m.type === "lvkitOpenVI") { api.postMessage({ type: "lvkitOpenVI", rel: m.rel }); return; }
  // From the host:
  if (m.type === "progress") { setPhase(m.label, m.pct); }
  else if (m.type === "showResult") { showResult(m.subvi ? injectSubviNav(m.html) : m.html); }
  else if (m.type === "jobError") { showError(m.error); }
});
api.postMessage({ type: "ready" });
</script>
</body>
</html>`;
}

exports.activate = activate;
exports.deactivate = function () {};
