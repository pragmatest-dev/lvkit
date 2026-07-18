const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// ---- config ----------------------------------------------------------------
function cfg() { return vscode.workspace.getConfiguration('lvkit'); }
function lvkitBin() { return cfg().get('path', 'lvkit'); }
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

function run(cmd, opts) {
  try {
    return cp.execSync(cmd, { maxBuffer: 1e9, ...opts });
  } catch (e) {
    const msg = String(e && e.message);
    if ((e && e.code === 'ENOENT') || /ENOENT|not found|No such file/i.test(msg)) {
      throw new Error(`lvkit executable not found (tried "${lvkitBin()}"). Set "lvkit.path" in Settings.`);
    }
    throw new Error(e && e.stderr ? e.stderr.toString() : msg);
  }
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
function wrapSvg(svg) {
  // `color-scheme: light dark` makes the webview report the editor theme via
  // prefers-color-scheme to the inline SVG, which is rendered with `--theme
  // auto` (see the render invocation below) so its colors follow the editor.
  // The page backdrop matches the editor so light/dark chrome never clashes.
  // CSP + <script> nonces are added by withNonceCsp() at the call site.
  return `<!doctype html><html><head><style>html,body{margin:0;height:100%;` +
    `color-scheme:light dark;background:var(--vscode-editor-background,#fff)}` +
    `#wrap{height:100vh;overflow:auto;display:flex;justify-content:center;align-items:flex-start}` +
    `svg{max-width:100%;height:auto}</style></head><body><div id="wrap">${svg}</div></body></html>`;
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
    const file = document.uri.fsPath;
    try {
      const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
      const svg = path.join(tmp, 'render.svg');
      const root = gitRootOr(path.dirname(file));
      // --theme auto: the SVG follows prefers-color-scheme, which the webview
      // reports from the editor theme (see wrapSvg's `color-scheme: light dark`).
      run(`"${lvkitBin()}" render "${file}" ${searchArgs(root)} --theme auto -o "${svg}"`);
      panel.webview.html = withNonceCsp(wrapSvg(fs.readFileSync(svg, 'utf8')));
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
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: `lvkit: diffing ${path.basename(file)}…` },
    async () => {
      try {
        const root = gitRootOr(path.dirname(file));
        const rel = path.relative(root, file);
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
        const oldVi = path.join(tmp, path.basename(file)); // keep .vi suffix (lvkit needs it)
        fs.writeFileSync(oldVi, run(`git show HEAD:"${rel}"`, { cwd: root }));
        const out = path.join(tmp, 'diff.html');
        // --theme auto: the diff viewer chrome is already prefers-color-scheme
        // adaptive; auto makes the embedded before/after diagrams follow the
        // same signal, which the webview reports from the editor theme. (No need
        // to read activeColorTheme.kind — prefers-color-scheme handles it.)
        run(`"${lvkitBin()}" diff "${oldVi}" "${file}" ${searchArgs(root)} --format html --theme auto -o "${out}"`, { cwd: root });
        const panel = vscode.window.createWebviewPanel(
          'lvkitDiff', `VI Diff: ${path.basename(file)}`, vscode.ViewColumn.Active,
          { enableScripts: true, retainContextWhenHidden: true }
        );
        panel.webview.html = withNonceCsp(fs.readFileSync(out, 'utf8'));
      } catch (e) {
        vscode.window.showErrorMessage('lvkit diff failed: ' + e.message);
      }
    }
  );
}

function activate(context) {
  context.subscriptions.push(vscode.commands.registerCommand('lvkit.diffVI', diffVI));
  context.subscriptions.push(vscode.window.registerCustomEditorProvider(
    'lvkit.viPreview', new ViPreviewProvider(),
    { webviewOptions: { retainContextWhenHidden: true }, supportsMultipleEditorsPerDocument: false }
  ));
}
function deactivate() {}
module.exports = { activate, deactivate };
