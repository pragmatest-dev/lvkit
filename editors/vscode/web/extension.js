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

// The lvkit + pylabview + networkx wheels shipped under media/wheels/, as webview
// URIs. Read from media/wheels/manifest.json (a KNOWN file, written by
// build-web-assets.sh) — NOT by listing the directory: a browser extension host
// can't enumerate the extension's own resources (vscode.workspace.fs.readDirectory
// throws EntryNotADirectory in the web worker), only fetch known files. The
// manifest is regenerated on a version bump, so this stays hard-coding-free.
async function wheelUris(webview, wheelsDir) {
  const raw = await vscode.workspace.fs.readFile(
    vscode.Uri.joinPath(wheelsDir, "manifest.json")
  );
  const names = JSON.parse(new TextDecoder().decode(raw));
  return names
    .filter((name) => typeof name === "string" && name.endsWith(".whl"))
    // lvkit LAST — it imports networkx + pylabview at import time, and
    // micropip.install(deps=False) does not resolve/order deps for us.
    .sort((a, b) => (a.startsWith("lvkit-") ? 1 : b.startsWith("lvkit-") ? -1 : 0))
    .map((name) =>
      webview.asWebviewUri(vscode.Uri.joinPath(wheelsDir, name)).toString()
    );
}

// Resolve the self-hosted Pyodide runtime + wheels for one webview.
async function pyodideAssets(webview, context) {
  const wheelsDir = vscode.Uri.joinPath(context.extensionUri, "media", "wheels");
  const pyodideDir = vscode.Uri.joinPath(context.extensionUri, "media", "pyodide");
  const wheels = await wheelUris(webview, wheelsDir);
  const pyodideBase = webview.asWebviewUri(pyodideDir).toString() + "/";
  return { wheels, pyodideBase };
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
    let assets;
    try {
      assets = await pyodideAssets(panel.webview, context);
    } catch (e) {
      panel.webview.html = errorHtml("LVKit web build is missing its wheels", String(e));
      logError("diff assets", e);
      return;
    }
    panel.webview.html = pyodideWebviewHtml(panel.webview, assets.wheels, assets.pyodideBase);
    panel.webview.onDidReceiveMessage((m) => {
      if (m.type === "ready") {
        panel.webview.postMessage({
          type: "diff",
          beforeB64: toBase64(beforeBytes),
          afterB64: toBase64(afterBytes),
          beforeRef: sides.before.ref || "",
          afterRef: sides.after.ref || "",
        });
      } else if (m.type === "log") {
        log(`  [diff webview] ${m.text}`);
      } else if (m.type === "error") {
        logError("diff webview", m.text);
      }
    });
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
      let assets;
      try {
        assets = await pyodideAssets(webview, context);
      } catch (e) {
        webview.html = errorHtml("LVKit web build is missing its wheels", String(e));
        logError("render assets", e);
        return;
      }
      if (assets.wheels.length === 0) {
        webview.html = errorHtml(
          "LVKit web build is missing its wheels",
          "No .whl files under media/wheels — run build-web-assets.sh."
        );
        log("render: no wheels under media/wheels");
        return;
      }
      webview.html = pyodideWebviewHtml(webview, assets.wheels, assets.pyodideBase);
      webview.onDidReceiveMessage(async (m) => {
        if (m.type === "ready") {
          try {
            const staged = await stageWorkspaceSubtree(document.uri);
            log(
              `render: staged ${staged.files.length} VI(s) under ${staged.root}` +
                `${staged.capped ? ` (CAPPED at ${MAX_STAGE_FILES} — some SubVI links may not resolve)` : ""}` +
                `, entry=${staged.renderRel}`
            );
            webview.postMessage({
              type: "render",
              files: staged.files,
              renderRel: staged.renderRel,
            });
          } catch (e) {
            logError("stage", e);
            // Degrade: render just the opened VI (no SubVI links) so the diagram
            // still shows even if staging the tree failed.
            const data = await vscode.workspace.fs.readFile(document.uri);
            const only = document.uri.path.split("/").pop();
            webview.postMessage({ type: "render", files: [{ rel: only, b64: toBase64(data) }], renderRel: only });
          }
        } else if (m.type === "lvkitOpenVI") {
          await openSubVI(document, m.rel);
        } else if (m.type === "log") {
          log(`  [webview] ${m.text}`);
        } else if (m.type === "error") {
          logError("render webview", m.text);
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
  context.subscriptions.push(
    vscode.commands.registerCommand("lvkit.diffVI", (arg) => diffVI(context, arg))
  );
}

function errorHtml(title, message) {
  const esc = (s) =>
    String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  return `<!doctype html><meta charset="utf-8"><body style="font:13px/1.5 var(--vscode-font-family,system-ui);padding:12px">
<h3 style="color:var(--vscode-errorForeground)">${esc(title)}</h3>
<pre style="white-space:pre-wrap;color:var(--vscode-descriptionForeground)">${esc(message)}</pre></body>`;
}

// The shared Pyodide boot page. Boots Pyodide + installs the wheels once, then
// waits for the host to post exactly one job: `render` (staged VI files + the
// entry path) or `diff` (two VIs' bytes + refs). The result HTML — lvkit's own
// render or diff viewer — is shown in a srcdoc <iframe> so its inline zoom/theme
// scripts run in a fresh browsing context while THIS frame keeps Pyodide alive.
// For a render, SubVI click-nav is injected: `data-lv-vi-rel` groups post
// `lvkitOpenVI` up to this frame, which relays to the host. Every phase + error
// is posted back to the host's Output channel via {type:'log'|'error'}.
function pyodideWebviewHtml(webview, wheelUrls, pyodideBase) {
  const csp = [
    "default-src 'none'",
    `script-src ${webview.cspSource} 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval'`,
    `connect-src ${webview.cspSource} blob: data:`,
    "style-src 'unsafe-inline'",
    `img-src ${webview.cspSource} data: blob:`,
    "worker-src blob:",
    `font-src ${webview.cspSource}`,
    "frame-src 'self'",
  ].join("; ");
  return `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<meta http-equiv="Content-Security-Policy" content="${csp}" />
<style>
  html, body { margin: 0; padding: 0; height: 100%; }
  body { font: 13px/1.5 var(--vscode-font-family, system-ui); }
  #status { padding: 10px; font-family: var(--vscode-editor-font-family, monospace);
            color: var(--vscode-descriptionForeground); white-space: pre-wrap; }
  #viewer { border: 0; width: 100%; height: 100vh; display: none; }
</style>
</head>
<body>
<div id="status">booting Pyodide…</div>
<iframe id="viewer" title="LVKit VI viewer"></iframe>
<script src="${pyodideBase}pyodide.js"></script>
<script>
const vscodeApi = acquireVsCodeApi();
const S = document.getElementById("status");
const log = m => { vscodeApi.postMessage({ type: "log", text: String(m) }); };
const err = m => { S.style.display = ""; S.textContent = String(m); vscodeApi.postMessage({ type: "error", text: String(m && m.stack ? m.stack : m) }); };
const status = m => { S.style.display = ""; S.textContent = m; log(m); };
const WHEELS = ${JSON.stringify(wheelUrls)};
let pyodide = null, renderFn = null, diffFn = null;

function u8(b64) {
  const bin = atob(b64);
  const a = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
  return a;
}
function writeFile(pth, data) {
  const dir = pth.slice(0, pth.lastIndexOf("/"));
  if (dir) pyodide.FS.mkdirTree(dir);
  pyodide.FS.writeFile(pth, data);
}
function showResult(html) {
  const f = document.getElementById("viewer");
  f.srcdoc = html;
  f.style.display = "block";
  S.style.display = "none";
}
// Inject SubVI click-navigation into a RENDER's viewer HTML (which starts
// <!doctype>\\n<meta charset='utf-8'>). Runs inside the iframe; posts up to this
// parent frame, which relays to the host.
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
from lvkit.render import render_vi_file_titled
from lvkit.render.render_viewer import build_render_viewer
from lvkit.vi_diff import diff_vi_files

def _render(vi_path):
    # SubVIs are staged next to the caller, so caller-relative resolution emits
    # data-lv-vi-rel. theme_mode="auto" lets the viewer's theme toggle work live.
    svg, name = render_vi_file_titled(Path(vi_path), theme_mode="auto")
    return build_render_viewer(svg or "", title=name or "")

def _diff(before, after, before_ref, after_ref):
    Path("/tmp/before.vi").write_bytes(bytes(before.to_py()))
    Path("/tmp/after.vi").write_bytes(bytes(after.to_py()))
    return diff_vi_files(
        Path("/tmp/before.vi"), Path("/tmp/after.vi"),
        fmt="html",
        before_ref=(before_ref or None),
        after_ref=(after_ref or None),
    ) or ""
\`);
    renderFn = pyodide.globals.get("_render");
    diffFn = pyodide.globals.get("_diff");
    status("ready — waiting for a VI…");
    vscodeApi.postMessage({ type: "ready" });
  } catch (e) { err(e); }
}

window.addEventListener("message", ev => {
  const m = ev.data;
  if (!m) return;
  if (m.type === "lvkitOpenVI") { vscodeApi.postMessage({ type: "lvkitOpenVI", rel: m.rel }); return; }
  try {
    if (m.type === "render" && renderFn) {
      const t0 = performance.now();
      for (const f of m.files) { writeFile("/proj/" + f.rel, u8(f.b64)); }
      const html = renderFn("/proj/" + m.renderRel);
      showResult(injectSubviNav(html));
      log("rendered via wasm in " + (performance.now() - t0).toFixed(0) + " ms");
    } else if (m.type === "diff" && diffFn) {
      const t0 = performance.now();
      const html = diffFn(u8(m.beforeB64), u8(m.afterB64), m.beforeRef || "", m.afterRef || "");
      showResult(html);
      log("diffed via wasm in " + (performance.now() - t0).toFixed(0) + " ms");
    }
  } catch (e) { err(e); }
});

boot();
</script>
</body>
</html>`;
}

exports.activate = activate;
exports.deactivate = function () {};
