const vscode = require('vscode');
const cp = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

// Minimum bundled lvkit library this extension build accepts. Since 0.6.0 the
// extension version is kept in lockstep with the library (both bumped together
// to the same number), but this floor still guards against pairing a build with
// too old a library.
const MIN_LVKIT = "0.5.3";

// ---- config ----------------------------------------------------------------
function cfg() { return vscode.workspace.getConfiguration('lvkit'); }
function extraSearchPaths() { return cfg().get('searchPaths', []); }
// Theme for the DIAGRAM SVG only (the viewer chrome always follows the editor
// via prefers-color-scheme). Persisted as the `lvkit.diagramTheme` setting. The
// diagram defaults to light — what LabVIEW users expect — independent of chrome.
function diagramTheme() {
  const t = cfg().get('diagramTheme', 'light');
  return ['light', 'dark'].includes(t) ? t : 'light';
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

// Set in activate() so the resolver can locate the bundled binary shipped
// inside the extension's own directory.
let _extensionPath = null;

// The standalone lvkit binary shipped with the extension, if present for this
// platform — a PyInstaller onedir build at bin/lvkit/lvkit[.exe] (built by
// build/build-binary.sh, signed on Windows in CI). This is what makes the
// extension work out-of-the-box for a LabVIEW user who has no Python/lvkit
// installed, with no first-run download and no per-user state.
function bundledLvkit() {
  if (!_extensionPath) return null;
  const exe = process.platform === 'win32' ? 'lvkit.exe' : 'lvkit';
  const p = path.join(_extensionPath, 'bin', 'lvkit', exe);
  return fileExists(p) ? p : null;
}

// Resolve a ready-to-exec lvkit command PREFIX for a repo `root`. The prefix may
// be interpolated raw — never wrap the whole prefix in quotes as if it were a
// single path. Order (SAME as mcpServerSpec, so the extension's CLI half and MCP
// half can never land on different lvkit versions):
//   1. an explicit `lvkit.path` override (the literal default "lvkit" counts as
//      unset) — quoted as one path. IGNORED in an untrusted workspace (running an
//      arbitrary workspace-configured binary is what Workspace Trust guards).
//   2. the BUNDLED, signed standalone binary shipped with the extension — the
//      DEFAULT. The extension ALWAYS uses its own bundled lvkit, NEVER a repo
//      `.venv`/`uv run`: a workspace `.venv` can hold a DIFFERENT lvkit version,
//      which split the CLI half (this) from the MCP half (mcpServerSpec) onto two
//      versions writing one shared ~/.lvkit/cache → each treats the other's
//      entries as foreign and re-extracts the whole corpus. To develop against
//      live code, point `lvkit.path` at your checkout's binary explicitly.
//   3. a global `lvkit` on PATH (only when no bundle is present).
// We NEVER write a global lvkit.path from here.
function lvkitCmd() {
  if (vscode.workspace.isTrusted) {
    const configured = cfg().get('path', 'lvkit');
    if (configured && configured !== 'lvkit') return `"${configured}"`;
  }
  const bundled = bundledLvkit();
  if (bundled) return `"${bundled}"`;
  return 'lvkit';
}

// Resolve the lvkit MCP server as {command, args} for McpStdioServerDefinition,
// which needs the executable and its args SPLIT (not a shell prefix string).
// SAME resolution as lvkitCmd, so the MCP half and the CLI half always run the
// EXACT SAME lvkit build — an explicit `lvkit.path` override (trusted only),
// then the BUNDLED signed binary (the default), then `lvkit` on PATH. No repo
// `.venv`/`uv run`: the MCP server, the render viewer, and the diff viewer must
// all agree on one lvkit, or they write conflicting entries into one shared
// ~/.lvkit/cache and re-do each other's work.
function mcpServerSpec() {
  if (vscode.workspace.isTrusted) {
    const configured = cfg().get('path', 'lvkit');
    if (configured && configured !== 'lvkit') return { command: configured, args: ['mcp'] };
  }
  const bundled = bundledLvkit();
  if (bundled) return { command: bundled, args: ['mcp'] };
  return { command: 'lvkit', args: ['mcp'] };
}

// Auto-register the bundled `lvkit mcp` server so VS Code agent mode gets the
// lvkit tools with ZERO user config. The MCP server-definition provider API is
// stable in VS Code >= 1.101; feature-detect it so the extension still loads on
// older versions (render/diff are unaffected — the registration simply no-ops).
function registerMcpProvider(context) {
  if (typeof vscode.lm?.registerMcpServerDefinitionProvider !== 'function'
      || typeof vscode.McpStdioServerDefinition !== 'function') {
    return;
  }
  const provider = {
    provideMcpServerDefinitions() {
      const root = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || _extensionPath;
      const spec = mcpServerSpec(root);
      // Don't offer a server we can't launch: a bare `lvkit` with nothing on
      // PATH and no bundle would just error in the MCP panel.
      if (spec.command === 'lvkit' && !onPath('lvkit')) return [];
      let version;
      try {
        version = cp.execSync(`"${spec.command}" --version`)
          .toString().match(/(\d+\.\d+\.\d+)/)?.[1];
      } catch (_) { /* version is optional */ }
      return [new vscode.McpStdioServerDefinition({
        label: 'LVKit', command: spec.command, args: spec.args, version,
      })];
    },
  };
  context.subscriptions.push(
    vscode.lm.registerMcpServerDefinitionProvider('lvkit', provider)
  );
}

function run(cmd, opts) {
  try {
    return cp.execSync(cmd, { maxBuffer: 1e9, ...opts });
  } catch (e) {
    const msg = String(e && e.message);
    const stderr = e && e.stderr ? e.stderr.toString() : '';
    const blob = msg + stderr;
    if ((e && e.code === 'ENOENT') || /ENOENT|not found|No such file|is not recognized/i.test(blob)) {
      throw new Error(
        'lvkit executable not found. Set "lvkit.path" in Settings, or install lvkit '
        + 'so it is on your PATH / in the repo\'s .venv.'
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
      // Resolve which lvkit to run (bundled binary, repo .venv/uv, or a
      // `lvkit.path` override). Ready-to-run — nothing to assemble.
      const cmd = lvkitCmd(root);
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
      run(`${cmd} render "${file}" ${searchArgs(root)} --format html${refArg} -o "${out}"`, { cwd: root });
      const html = injectSubVIClickNav(injectInitialTheme(fs.readFileSync(out, 'utf8'), diagramTheme()));
      panel.webview.html = withNonceCsp(html);
      wireThemePersistence(panel.webview);
      wireSubVINavigation(panel.webview, document);
    } catch (e) {
      panel.webview.html = errorHtml('lvkit render failed', e.message);
    }
  }
}

// ---- right-click: before/after Visual Diff --------------------------------
// Resolve the two versions to diff, each as {ref: <git rev>} or {file: <path>}
// (the working copy). Verified against real VS Code behavior (the LVKit Diff
// Diagnostics run on #19):
//   - A .vi renders via our CUSTOM editor, so a native diff does NOT populate
//     `tab.input` — there are no original/modified sides to read. The command
//     instead receives, as `arg`, the git: URI of ONE side: the commit being
//     viewed (ref R).
//   - So for a committed version we diff R against its PARENT R^ — exactly what
//     a Timeline "this commit" entry shows (a commit vs its previous). (The old
//     code diffed HEAD vs the on-disk file; on a clean tree HEAD == R == the
//     same commit, so it rendered ONE VI twice — #19.)
//   - A file: arg is the working copy -> diff it against HEAD.
//   - If a real diff editor ever DOES expose two sides (a plain text diff rather
//     than the custom editor), honour them.
function diffSources(target) {
  const input = vscode.window.tabGroups
    && vscode.window.tabGroups.activeTabGroup
    && vscode.window.tabGroups.activeTabGroup.activeTab
    && vscode.window.tabGroups.activeTabGroup.activeTab.input;
  if (input && input.original && input.modified && input.modified.fsPath
      && input.modified.fsPath.toLowerCase().endsWith('.vi')
      && (input.modified.fsPath === target.fsPath || input.original.fsPath === target.fsPath)) {
    return { before: sideOf(input.original), after: sideOf(input.modified) };
  }
  if (target.scheme !== 'file') {
    const ref = gitRawRef(target);
    // `~1` (first parent), NOT `^`: on Windows cmd.exe `^` is the escape char,
    // so `git show <sha>^:file` silently drops the caret -> the SAME commit on
    // both sides. `~1` is identical to `^` here and shell-safe everywhere.
    return { before: { ref: ref + '~1' }, after: { ref } };   // commit vs its parent
  }
  return { before: { ref: 'HEAD' }, after: { file: target.fsPath } };  // working tree vs HEAD
}
function sideOf(uri) {
  return uri.scheme === 'file' ? { file: uri.fsPath } : { ref: gitRawRef(uri) };
}

// Raw git ref from a VS Code git: URI query ({path, ref}); empty/absent -> HEAD.
function gitRawRef(uri) {
  try { const r = JSON.parse(uri.query || '{}').ref; return r ? r : 'HEAD'; }
  catch (_) { return 'HEAD'; }
}

// Materialize one side ({ref} or {file}) to a real .vi path lvkit can read + a
// short label. A {file} side is the working copy (no ref label). A {ref} side is
// extracted from git (`git show <ref>:<rel>`); rel uses POSIX separators — git
// reads a backslash path as a literal filename ("exists on disk, but not in
// 'HEAD'") on Windows.
function materialize(src, rel, root, tmp, tag) {
  if (src.file) return { viPath: src.file, ref: null };
  const dest = path.join(tmp, `${tag}.vi`);
  fs.writeFileSync(dest, run(`git show ${src.ref}:"${rel}"`, { cwd: root }));
  let label = src.ref;
  try { label = cp.execSync(`git rev-parse --short "${src.ref}"`, { cwd: root }).toString().trim(); }
  catch (_) { /* keep the raw ref */ }
  return { viPath: dest, ref: label };
}

async function diffVI(arg) {
  const target = arg && (arg.resourceUri || arg);
  if (!target || !target.fsPath) { vscode.window.showErrorMessage('lvkit: no .vi selected.'); return; }
  const file = target.fsPath;
  // The Source Control menu can't filter by file type (resourceExtname isn't an
  // scm/resourceState/context key), so the .vi check has to happen here.
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
        const cmd = lvkitCmd(root);
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'lvkit-'));
        const rel = path.relative(root, file).split(path.sep).join('/');
        const src = diffSources(target);
        const before = materialize(src.before, rel, root, tmp, 'before');
        const after = materialize(src.after, rel, root, tmp, 'after');
        const out = path.join(tmp, 'diff.html');
        // Label each side with its ref so the viewer titles read "…@ <sha>";
        // a null-ref side is lvkit's default "working copy".
        const refArgs = [
          before.ref ? `--before-ref "${before.ref}"` : '',
          after.ref ? `--after-ref "${after.ref}"` : '',
        ].join(' ').trim();
        // --theme auto: the diff viewer chrome is prefers-color-scheme adaptive;
        // auto makes the embedded before/after diagrams follow the same signal.
        run(`${cmd} diff "${before.viPath}" "${after.viPath}" ${searchArgs(root)} --format html --theme auto ${refArgs} -o "${out}"`, { cwd: root });
        const panel = vscode.window.createWebviewPanel(
          'lvkitDiff', `VI Diff: ${path.basename(file)}`, vscode.ViewColumn.Active,
          { enableScripts: true, retainContextWhenHidden: true }
        );
        const html = injectInitialTheme(fs.readFileSync(out, 'utf8'), diagramTheme());
        panel.webview.html = withNonceCsp(html);
        wireThemePersistence(panel.webview);
      } catch (e) {
        // Common expected case: the FIRST commit has no parent for `~1`.
        const first = /unknown revision|ambiguous argument|bad revision|does not have any commits/i.test(String(e.message));
        const msg = first
          ? 'no earlier revision to compare — this looks like the first commit of this VI.'
          : 'diff failed: ' + e.message;
        vscode.window.showErrorMessage('lvkit: ' + msg);
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
  registerMcpProvider(context);
}
function deactivate() {}
module.exports = { activate, deactivate };
