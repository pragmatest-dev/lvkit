const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// The upcoming lvkit library release this extension version targets. The
// extension versions on its OWN track (package.json "version") — not lockstep
// with the library — so we assert a floor here rather than assuming a match.
const MIN_LVKIT = "0.5.3";

// ---- config ----------------------------------------------------------------
function cfg() { return vscode.workspace.getConfiguration('lvkit'); }
function extraSearchPaths() { return cfg().get('searchPaths', []); }
// Theme for the DIAGRAM SVG only (the viewer chrome always follows the editor
// via prefers-color-scheme). Persisted as the `lvkit.diagramTheme` setting.
function diagramTheme() {
  const t = cfg().get('diagramTheme', 'auto');
  return ['auto', 'light', 'dark'].includes(t) ? t : 'auto';
}

// ---- helpers ---------------------------------------------------------------
function gitRootOr(dir) {
  try { return cp.execSync('git rev-parse --show-toplevel', { cwd: dir }).toString().trim(); }
  catch (_) { return dir; }
}
// A short, readable label for the commit/rev a `git:` diff URI points at, so the
// rendered VI's title reads "…@ 3f9a1c2" instead of two identical qualified
// names. VS Code's git provider encodes {path, ref} as JSON in the URI query;
// ref is a sha, "HEAD", "~" (index), or "". lvkit resolves the NAME itself — we
// only supply this rev, which it can't infer from a temp checkout.
function gitRefLabel(uri, root) {
  let ref = null;
  try { ref = JSON.parse(uri.query || '{}').ref; } catch (_) { /* not a git uri */ }
  if (ref == null) return null;
  if (/^[0-9a-f]{7,40}$/i.test(ref)) return ref.slice(0, 8);   // a specific commit
  // "~" (index), "HEAD", or "" — the committed side of an unstaged-changes diff.
  // With nothing staged the index IS the last commit, so show that commit's
  // short hash (a real id) rather than the word "index".
  try { return cp.execSync('git rev-parse --short HEAD', { cwd: root }).toString().trim(); }
  catch (_) { return 'HEAD'; }
}
function esc(s) { return String(s).replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m])); }

function fileExists(p) { try { return fs.existsSync(p); } catch (_) { return false; } }
function onPath(exe) {
  const probe = process.platform === 'win32' ? `where ${exe}` : `command -v ${exe}`;
  try { cp.execSync(probe, { stdio: 'ignore' }); return true; } catch (_) { return false; }
}

// Set in activate() so lvkitCmd can locate the bundled uv binary.
let _extensionPath = null;

// The EXACT lvkit version this extension is built and tested against. uv fetches
// THIS version, so the extension's advertised behavior is guaranteed regardless
// of what (if anything) is installed on the user's machine. Bump whenever the
// extension depends on a newer lvkit — which must be published to PyPI first.
const LVKIT_PIN = '0.5.6';

// The bundled `uv` binary (Astral), shipped inside the extension. uv is a
// high-reputation, signed executable that RUNS under Windows Device Guard /
// Smart App Control — where our own unsigned PyInstaller `lvkit.exe` gets
// blocked (zero reputation). uv provisions a managed Python + the pinned lvkit
// on first use (cached afterwards), and we invoke lvkit as a MODULE
// (`python -m lvkit`) so no unsigned `lvkit.exe` is ever created or executed —
// which is the whole reason the exe was blocked.
function bundledUvPath() {
  if (!_extensionPath) return null;
  const exe = process.platform === 'win32' ? 'uv.exe' : 'uv';
  const p = path.join(_extensionPath, 'bin', 'uv', exe);
  return fileExists(p) ? p : null;
}

// Resolve a ready-to-exec lvkit command PREFIX. Multi-token (e.g.
// `"…\uv.exe" run …`), so callers interpolate it raw — never wrap the whole
// prefix in quotes as one path. Order:
//   1. an explicit `lvkit.path` override — a developer pointing at their OWN
//      build/checkout (the literal default "lvkit" counts as unset). This is the
//      only way an ambient/local lvkit is ever used; we never auto-discover one,
//      because the extension can only guarantee its behavior at the pinned version.
//   2. uv running the PINNED lvkit as a module — the guaranteed, no-Python,
//      Device-Guard-safe path (bundled uv, else a `uv` already on PATH).
//      `--no-project` stops uv from adopting any pyproject in the working dir,
//      so the pin can't be silently overridden.
//   3. a bare `lvkit` on PATH — last resort (may itself be blocked under SAC).
function lvkitCmd(_root) {
  const configured = cfg().get('path', 'lvkit');
  if (configured && configured !== 'lvkit') return `"${configured}"`;
  const uv = bundledUvPath() || (onPath('uv') ? 'uv' : null);
  if (uv) {
    const uvTok = uv.includes(path.sep) ? `"${uv}"` : uv;
    return `${uvTok} run --no-project --with lvkit==${LVKIT_PIN} python -m lvkit`;
  }
  return 'lvkit';
}

function run(cmd, opts) {
  try {
    return cp.execSync(cmd, { maxBuffer: 1e9, ...opts });
  } catch (e) {
    const msg = String(e && e.message);
    const stderr = e && e.stderr ? e.stderr.toString() : '';
    const blob = msg + stderr;
    // First run provisions a managed Python + lvkit via uv — that needs network.
    if (/offline|No such host|Could not connect|failed to (fetch|download)|dns error|timed? out|network|proxy/i.test(blob)) {
      throw new Error(
        "lvkit's runtime couldn't be downloaded — uv needs network access the first "
        + 'time it runs (it fetches a managed Python + lvkit, then caches them offline). '
        + 'Check your connection/proxy and retry.'
      );
    }
    // No uv available at all (bundle missing AND none on PATH) → lvkitCmd fell
    // back to a bare `lvkit`, which isn't there.
    if ((e && e.code === 'ENOENT') || /ENOENT|not found|No such file|is not recognized/i.test(blob)) {
      throw new Error(
        'Could not launch lvkit. The extension bundles uv to run it with no Python '
        + 'required; if that is missing, install uv (https://docs.astral.sh/uv/) or set '
        + '"lvkit.path" to your own lvkit.'
      );
    }
    throw new Error(stderr || msg);
  }
}

// Numeric semver compare (major.minor.patch). Returns <0 / 0 / >0.
function cmpSemver(a, b) {
  const pa = String(a).split('.').map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) { const x = pa[i] || 0, y = pb[i] || 0; if (x !== y) return x - y; }
  return 0;
}

// One-time, non-blocking version check. On the default path lvkit is PINNED
// (uv fetches LVKIT_PIN), so the version is guaranteed and there's nothing to
// check — skipping also avoids a redundant uv spawn (which would download the
// runtime just to print --version). We only verify when the user pointed us at
// their OWN lvkit via `lvkit.path`, where the version is unknown.
let _versionChecked = false;
function checkLvkitVersion(root) {
  if (_versionChecked) return;
  _versionChecked = true;
  const configured = cfg().get('path', 'lvkit');
  if (!(configured && configured !== 'lvkit')) return;  // pinned path — nothing to check
  try {
    const out = cp.execSync(`${lvkitCmd(root)} --version`, { cwd: root }).toString();
    const m = out.match(/(\d+\.\d+\.\d+)/);
    if (m && cmpSemver(m[1], MIN_LVKIT) < 0) {
      vscode.window.showWarningMessage(
        `This extension needs lvkit ≥ ${MIN_LVKIT}; found ${m[1]}. Update with 'pip install -U lvkit'.`
      );
    }
  } catch (_) { /* missing/broken override lvkit -> handled by the command's error path */ }
}

function searchArgs(root) {
  return [root, ...extraSearchPaths()].map((p) => `--search-path "${p}"`).join(' ');
}

// VS Code webviews are CSP-strict AND — unlike a normal browser — IGNORE
// `script-src 'unsafe-inline'` for scripts: an inline <script> runs ONLY when it
// carries a per-load nonce that the CSP allow-lists. Both our interactive render
// SVG (frame-toggle/hover JS) and the diff viewer ship inline <script>, so every
// page goes through withNonceCsp(): stamp a fresh nonce on each <script> and
// allow exactly that nonce. Styles and data: URIs stay inline (those DO honor
// 'unsafe-inline'). Without this, the page renders but no JS runs — static SVGs,
// no pan/zoom/change-list.
function nonce() {
  let t = '';
  const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  for (let i = 0; i < 32; i++) t += chars.charAt(Math.floor(Math.random() * chars.length));
  return t;
}
function cspMeta(n) {
  return `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; ` +
    `img-src data: https:; style-src 'unsafe-inline'; script-src 'nonce-${n}'; font-src data:;">`;
}
// Stamp a nonce on every <script> and inject the matching CSP (after the charset
// meta if present, else just inside <head>, else at the very top of the doc).
function withNonceCsp(html) {
  const n = nonce();
  const nonced = html.replace(/<script(\s|>)/g, `<script nonce="${n}"$1`);
  const inject = cspMeta(n);
  if (/<meta charset=['"]utf-8['"]>/i.test(nonced)) {
    return nonced.replace(/<meta charset=['"]utf-8['"]>/i, (m) => `${m}\n${inject}`);
  }
  if (/<head[^>]*>/i.test(nonced)) {
    return nonced.replace(/<head[^>]*>/i, (m) => `${m}${inject}`);
  }
  return inject + nonced;
}
// Seed the viewer's diagram-theme control with the host's `lvkit.diagramTheme`
// setting. The render/diff viewer reads `window.__lvkitInitialTheme` before its
// theme-control script runs (see render/theme_control.py), so inject the value
// right AFTER `<meta charset='utf-8'>` — that keeps it ahead of the control
// script AND inside the region withNonceCsp() nonces, so it actually executes.
// Call this BEFORE withNonceCsp() so the injected <script> receives a nonce.
function injectInitialTheme(html, mode) {
  const tag = `<script>window.__lvkitInitialTheme=${JSON.stringify(mode)};</script>`;
  if (/<meta charset=['"]utf-8['"]>/i.test(html)) {
    return html.replace(/<meta charset=['"]utf-8['"]>/i, (m) => `${m}\n${tag}`);
  }
  return tag + html;
}
// The viewer's theme control postMessages `{type:'lvkitDiagramTheme', value}`
// whenever the user cycles the diagram theme. Persist it so the choice sticks
// across previews/diffs and future sessions.
function wireThemePersistence(webview) {
  webview.onDidReceiveMessage((m) => {
    if (m && m.type === 'lvkitDiagramTheme') {
      vscode.workspace.getConfiguration('lvkit')
        .update('diagramTheme', m.value, vscode.ConfigurationTarget.Global);
    }
  });
}
// ---- SubVI click-navigation (task #76) --------------------------------------
// The renderer (lvkit render/scene.py + render/draw.py) emits ONLY an inert
// `data-lv-vi-rel` data attribute on a resolvable SubVI node's SVG group — a
// path RELATIVE to the rendered VI's own directory. No links, no click JS, no
// VS Code assumptions live in the renderer; a standalone .svg or a plain web
// page just ignores the attribute. This is the host half: turn that identity
// payload into a click that opens the SubVI in this same window.
//
// Injects one inline <script> that finds every `[data-lv-vi-rel]` element,
// gives it a pointer cursor, and postMessages `{type:'lvkitOpenVI', rel}` on
// click. Reuses the webview's single VS Code API handle instead of calling
// `acquireVsCodeApi()` again (VS Code throws on a second call): the shared
// diagram-theme control script every lvkit HTML viewer already embeds
// (render/theme_control.py) stashes its handle on `window.__lvkitVsCodeApi`
// for exactly this reason; this script falls back to acquiring (and
// stashing) its own only if that hasn't happened. The lookup + wiring is
// deferred to `DOMContentLoaded` (or run immediately if the DOM is already
// parsed) so it doesn't matter whether this script lands before or after the
// SVG content in the document.
//
// Call this BEFORE withNonceCsp() so the inline <script> receives a nonce —
// same requirement as injectInitialTheme() (see its comment above).
function injectSubVIClickNav(html) {
  const script = `<script>
(function(){
  function openVI(el){
    var rel = el.getAttribute('data-lv-vi-rel');
    if (!rel) return;
    var vscode = window.__lvkitVsCodeApi;
    if (!vscode && typeof acquireVsCodeApi === 'function') {
      try { vscode = acquireVsCodeApi(); window.__lvkitVsCodeApi = vscode; } catch (e) { vscode = null; }
    }
    if (vscode) vscode.postMessage({ type: 'lvkitOpenVI', rel: rel });
  }
  function wire(){
    var els = document.querySelectorAll('[data-lv-vi-rel]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      el.style.cursor = 'pointer';
      el.addEventListener('click', function (ev) { openVI(ev.currentTarget); });
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wire);
  else wire();
})();
</script>`;
  if (/<meta charset=['"]utf-8['"]>/i.test(html)) {
    return html.replace(/<meta charset=['"]utf-8['"]>/i, (m) => `${m}\n${script}`);
  }
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/<head[^>]*>/i, (m) => `${m}${script}`);
  }
  return script + html;
}
// The click-nav script's postMessage handler: resolves `rel` against the
// directory of the CURRENTLY-OPEN document (not the temp render output dir —
// see resolveCustomEditor's `origPath`/`document.uri` distinction) and opens
// it through the SAME custom editor (`vscode.open` routes a `.vi` back to
// `lvkit.viPreview`). Never destructive: a missing/non-.vi target just warns.
function wireSubVINavigation(webview, document) {
  webview.onDidReceiveMessage(async (m) => {
    if (!m || m.type !== 'lvkitOpenVI') return;
    const rel = m.rel;
    if (!rel || typeof rel !== 'string') {
      vscode.window.showWarningMessage('lvkit: SubVI navigation message had no path.');
      return;
    }
    const resolved = path.resolve(path.dirname(document.uri.fsPath), rel);
    if (!fileExists(resolved) || !resolved.toLowerCase().endsWith('.vi')) {
      vscode.window.showWarningMessage(`lvkit: could not open SubVI "${rel}" (not found).`);
      return;
    }
    // Match VS Code's go-to-definition behavior exactly: navigation targets the
    // shared PREVIEW slot, never the source permanent tab. Passing no options
    // (default preview, honoring workbench.editor.enablePreview) gives that for
    // free — clicking from a preview tab reuses it (replace); clicking from a
    // double-clicked permanent tab leaves it open and shows the SubVI in the
    // preview slot; and a user who disabled preview globally gets a new tab
    // everywhere, including here. The target activates (preserveFocus false).
    await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(resolved));
  });
}
function errorHtml(title, message) {
  return `<body style="color:#ddd;background:#1e1e1e;font:14px sans-serif;padding:24px">` +
    `<b>${esc(title)}</b><pre style="white-space:pre-wrap;color:#f88">${esc(message)}</pre></body>`;
}

// ---- read-only VI preview (replaces the "binary file" notice) ---------------
class ViPreviewProvider {
  async openCustomDocument(uri) { return { uri, dispose() {} }; }
  async resolveCustomEditor(document, panel) {
    panel.webview.options = { enableScripts: true };
    // `fsPath` is always the on-disk WORKING-TREE path, even when the URI is a
    // `git:` (or other provider) resource — the scheme/query are stripped. Use
    // it to root SubVI resolution (--search-path) and to name the temp file,
    // but NEVER render it directly for a non-file URI (see below).
    const origPath = document.uri.fsPath;
    const root = gitRootOr(path.dirname(origPath));
    checkLvkitVersion(root);
    try {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
      // In VS Code's native diff, THIS editor opens on both panes with different
      // URIs: the "after" pane is scheme `file` (on disk); the "before" pane is
      // scheme `git`, whose bytes are the committed blob living inside git — its
      // fsPath is the SAME on-disk path, so rendering fsPath would show the
      // working copy on BOTH sides. Read any non-file URI through VS Code's
      // provider (git's FileSystemProvider runs `git show`) and render a temp
      // copy of those bytes instead.
      let file = origPath;
      // Non-file scheme = the committed side of VS Code's native diff. Tag the
      // title with its rev so it's distinguishable from the working-tree pane
      // (which stays the plain qualified name); a standalone preview is `file`
      // and gets no ref, so it stays clean.
      let refArg = '';
      if (document.uri.scheme !== 'file') {
        const bytes = await vscode.workspace.fs.readFile(document.uri);
        if (!bytes || bytes.length === 0) {
          // e.g. the "before" side of a newly-added file — no committed blob.
          panel.webview.html = errorHtml(
            'No version to show',
            'This .vi has no committed content on this side (it is newly added).'
          );
          return;
        }
        file = path.join(tmp, path.basename(origPath)); // keep the .vi extension
        fs.writeFileSync(file, Buffer.from(bytes));
        const rl = gitRefLabel(document.uri, root);
        if (rl) refArg = ` --ref "${rl}"`;
      }
      // The working-tree pane (file:) and a standalone preview get no ref — the
      // plain qualified name reads as "the current version".
      const out = path.join(tmp, 'preview.html');
      // `render --format html` emits the self-contained interactive viewer
      // (zoom/pan + a light/dark diagram-theme toggle). The diagram itself is
      // internally `--theme auto` (switchable in-viewer), so the injected
      // initial theme + the in-viewer control govern light/dark — no --theme
      // needed on this call.
      run(`${lvkitCmd(root)} render "${file}" ${searchArgs(root)} --format html${refArg} -o "${out}"`, { cwd: root });
      const html = injectSubVIClickNav(injectInitialTheme(fs.readFileSync(out, 'utf8'), diagramTheme()));
      panel.webview.html = withNonceCsp(html);
      wireThemePersistence(panel.webview);
      wireSubVINavigation(panel.webview, document);
    } catch (e) {
      panel.webview.html = errorHtml('lvkit render failed', e.message);
    }
  }
}

// ---- right-click: unified before/after diff (working tree vs HEAD) ----------
async function diffVI(arg) {
  const uri = arg && arg.resourceUri ? arg.resourceUri : arg;
  if (!uri || !uri.fsPath) { vscode.window.showErrorMessage('lvkit: no .vi selected.'); return; }
  const file = uri.fsPath;
  // The Source Control menu can't filter by file type: `resourceExtname` is not
  // among the context keys available in scm/resourceState/context (only
  // scmProvider / scmResourceGroup / originalResourceScheme are), so gating the
  // menu on it silently hid this command from the git panel. The menu now uses
  // `scmProvider == git`, which means it shows for EVERY changed file — so the
  // .vi check has to happen here instead.
  if (!file.toLowerCase().endsWith('.vi')) {
    vscode.window.showErrorMessage(`lvkit: "${path.basename(file)}" is not a .vi file.`);
    return;
  }
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `lvkit: diffing ${path.basename(file)}…` },
    async () => {
      try {
        const root = gitRootOr(path.dirname(file));
        checkLvkitVersion(root);
        // git refs (HEAD:<path>) require POSIX separators; path.relative yields
        // backslashes on Windows, which git reads as a literal filename and
        // rejects ("exists on disk, but not in 'HEAD'"). Normalize to '/'.
        const rel = path.relative(root, file).split(path.sep).join('/');
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
        const oldVi = path.join(tmp, path.basename(file)); // keep .vi suffix (lvkit needs it)
        fs.writeFileSync(oldVi, run(`git show HEAD:"${rel}"`, { cwd: root }));
        let headRef = 'HEAD';
        try { headRef = cp.execSync('git rev-parse --short HEAD', { cwd: root }).toString().trim(); }
        catch (_) { /* keep 'HEAD' */ }
        const out = path.join(tmp, 'diff.html');
        // --theme auto: the diff viewer chrome is already prefers-color-scheme
        // adaptive; auto makes the embedded before/after diagrams follow the
        // same signal. The injected initial theme + in-viewer control then let
        // the user pin light/dark, persisted via wireThemePersistence().
        run(`${lvkitCmd(root)} diff "${oldVi}" "${file}" ${searchArgs(root)} --format html --theme auto --before-ref "${headRef}" -o "${out}"`, { cwd: root });
        const panel = vscode.window.createWebviewPanel(
          'lvkitDiff', `VI Diff: ${path.basename(file)}`, vscode.ViewColumn.Active,
          { enableScripts: true, retainContextWhenHidden: true }
        );
        const html = injectInitialTheme(fs.readFileSync(out, 'utf8'), diagramTheme());
        panel.webview.html = withNonceCsp(html);
        wireThemePersistence(panel.webview);
      } catch (e) {
        vscode.window.showErrorMessage('lvkit diff failed: ' + e.message);
      }
    }
  );
}

function activate(context) {
  _extensionPath = context.extensionPath;
  context.subscriptions.push(vscode.commands.registerCommand('lvkit.diffVI', diffVI));
  context.subscriptions.push(vscode.window.registerCustomEditorProvider(
    'lvkit.viPreview', new ViPreviewProvider(),
    { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }
  ));
}
function deactivate() {}
module.exports = { activate, deactivate };
