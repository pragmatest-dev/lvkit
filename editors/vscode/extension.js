const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// The upcoming lvkit library release this extension version targets. The
// extension versions on its OWN track (package.json "version") — not lockstep
// with the library — so we assert a floor here rather than assuming a match.
const MIN_LVKIT = "0.5.0";

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
function esc(s) { return String(s).replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m])); }

function fileExists(p) { try { return fs.existsSync(p); } catch (_) { return false; } }
function onPath(exe) {
  const probe = process.platform === 'win32' ? `where ${exe}` : `command -v ${exe}`;
  try { cp.execSync(probe, { stdio: 'ignore' }); return true; } catch (_) { return false; }
}

// Set in activate() so lvkitCmd can find the bundled standalone binary shipped
// inside the extension (bin/lvkit/lvkit[.exe]) — a PyInstaller build that needs
// no Python on the user's machine.
let _extensionPath = null;

// The standalone lvkit binary shipped with the extension, if present for this
// platform. This is what makes the extension work out-of-the-box for a LabVIEW
// user who has no Python/lvkit installed.
function bundledLvkit() {
  if (!_extensionPath) return null;
  const exe = process.platform === 'win32' ? 'lvkit.exe' : 'lvkit';
  const p = path.join(_extensionPath, 'bin', 'lvkit', exe);
  return fileExists(p) ? p : null;
}

// Resolve a ready-to-exec lvkit command PREFIX for a repo `root`. The prefix may
// be MULTIPLE tokens (e.g. `uv run lvkit`), so callers must interpolate it raw —
// never wrap the whole prefix in quotes as if it were a single path. Order:
//   1. an explicit `lvkit.path` override (the literal default "lvkit" counts as
//      unset, so auto-resolution can still run) — quoted as one path;
//   2. the repo-local venv's lvkit on disk (developing inside a lvkit project);
//   3. `uv run lvkit` when the repo has a pyproject.toml/uv.lock and `uv` is on
//      PATH (runs lvkit inside the repo's own env — latest code when developing);
//   4. the BUNDLED standalone binary shipped with the extension (works with no
//      Python installed — the default for a normal end user);
//   5. a global `lvkit` on PATH.
// We NEVER write a global lvkit.path from here.
function lvkitCmd(root) {
  const configured = cfg().get('path', 'lvkit');
  if (configured && configured !== 'lvkit') return `"${configured}"`;
  const venv = process.platform === 'win32'
    ? path.join(root, '.venv', 'Scripts', 'lvkit.exe')
    : path.join(root, '.venv', 'bin', 'lvkit');
  if (fileExists(venv)) return `"${venv}"`;
  const hasProj = fileExists(path.join(root, 'pyproject.toml')) || fileExists(path.join(root, 'uv.lock'));
  if (hasProj && onPath('uv')) return 'uv run lvkit';
  const bundled = bundledLvkit();
  if (bundled) return `"${bundled}"`;
  return 'lvkit';
}

function run(cmd, opts) {
  try {
    return cp.execSync(cmd, { maxBuffer: 1e9, ...opts });
  } catch (e) {
    const msg = String(e && e.message);
    if ((e && e.code === 'ENOENT') || /ENOENT|not found|No such file/i.test(msg)) {
      throw new Error('lvkit executable not found. Set "lvkit.path" in Settings, or install lvkit so it is on your PATH / in the repo\'s .venv.');
    }
    throw new Error(e && e.stderr ? e.stderr.toString() : msg);
  }
}

// Numeric semver compare (major.minor.patch). Returns <0 / 0 / >0.
function cmpSemver(a, b) {
  const pa = String(a).split('.').map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split('.').map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) { const x = pa[i] || 0, y = pb[i] || 0; if (x !== y) return x - y; }
  return 0;
}

// One-time, non-blocking check that the resolved lvkit meets MIN_LVKIT. Called
// lazily on first command use. If `--version` fails (lvkit missing/misconfigured)
// we stay silent — the per-command ENOENT error path already guides the user.
let _versionChecked = false;
function checkLvkitVersion(root) {
  if (_versionChecked) return;
  _versionChecked = true;
  try {
    const out = cp.execSync(`${lvkitCmd(root)} --version`, { cwd: root }).toString();
    const m = out.match(/(\d+\.\d+\.\d+)/);
    if (m && cmpSemver(m[1], MIN_LVKIT) < 0) {
      vscode.window.showWarningMessage(
        `This extension needs lvkit ≥ ${MIN_LVKIT}; found ${m[1]}. Update with 'pip install -U lvkit'.`
      );
    }
  } catch (_) { /* missing/broken lvkit -> handled by the command's ENOENT path */ }
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
      }
      const out = path.join(tmp, 'preview.html');
      // `render --format html` emits the self-contained interactive viewer
      // (zoom/pan + a light/dark diagram-theme toggle). The diagram itself is
      // internally `--theme auto` (switchable in-viewer), so the injected
      // initial theme + the in-viewer control govern light/dark — no --theme
      // needed on this call.
      run(`${lvkitCmd(root)} render "${file}" ${searchArgs(root)} --format html -o "${out}"`, { cwd: root });
      const html = injectInitialTheme(fs.readFileSync(out, 'utf8'), diagramTheme());
      panel.webview.html = withNonceCsp(html);
      wireThemePersistence(panel.webview);
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
        const rel = path.relative(root, file);
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
        const oldVi = path.join(tmp, path.basename(file)); // keep .vi suffix (lvkit needs it)
        fs.writeFileSync(oldVi, run(`git show HEAD:"${rel}"`, { cwd: root }));
        const out = path.join(tmp, 'diff.html');
        // --theme auto: the diff viewer chrome is already prefers-color-scheme
        // adaptive; auto makes the embedded before/after diagrams follow the
        // same signal. The injected initial theme + in-viewer control then let
        // the user pin light/dark, persisted via wireThemePersistence().
        run(`${lvkitCmd(root)} diff "${oldVi}" "${file}" ${searchArgs(root)} --format html --theme auto -o "${out}"`, { cwd: root });
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
