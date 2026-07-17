const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// ---- config ----------------------------------------------------------------
function cfg() { return vscode.workspace.getConfiguration('lvkit'); }
function lvkitBin() { return cfg().get('path', 'lvkit'); }
function extraSearchPaths() { return cfg().get('searchPaths', []); }

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

// VS Code webviews are CSP-strict; our render/viewer is fully self-contained
// (inline <style>/<script>, data: icons), so allow only those + data URIs.
const CSP = `<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: https:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; font-src data:;">`;
function injectCsp(html) { return html.replace(/<meta charset=['"]utf-8['"]>/i, (m) => `${m}\n${CSP}`); }
function wrapSvg(svg) {
  return `<!doctype html><html><head>${CSP}<style>html,body{margin:0;height:100%;background:#fff}` +
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
      run(`"${lvkitBin()}" render "${file}" ${searchArgs(root)} -o "${svg}"`);
      panel.webview.html = wrapSvg(fs.readFileSync(svg, 'utf8'));
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
        run(`"${lvkitBin()}" diff "${oldVi}" "${file}" ${searchArgs(root)} --format html -o "${out}"`, { cwd: root });
        const panel = vscode.window.createWebviewPanel(
          'lvkitDiff', `VI Diff: ${path.basename(file)}`, vscode.ViewColumn.Active,
          { enableScripts: true, retainContextWhenHidden: true }
        );
        panel.webview.html = injectCsp(fs.readFileSync(out, 'utf8'));
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
