"""A compact, host-agnostic diagram-theme control shared by the HTML viewers.

Both the diff viewer (``templates/diff_viewer.html``) and the single-VI render
viewer (``templates/render_viewer.html``) embed their block-diagram SVGs with
``--theme auto`` — i.e. the SVG carries the ``@media (prefers-color-scheme:
dark)`` / ``:root[data-theme="dark"]`` palette that
``theme_web.embedded_dark_css("auto")`` emits. Because an inline SVG's
``:root`` resolves to the PAGE's ``<html>`` element, flipping
``document.documentElement.dataset.theme`` re-themes EVERY embedded diagram on
the page at once, with no re-render.

This module supplies the ONE small toolbar button (and its JS) that drives that
flip. It is deliberately UNOBTRUSIVE — a single ~28px icon button that toggles
``light <-> dark`` on click, NOT a segmented control — so the theme control never
becomes a large part of the viewer chrome. The diagram theme is INDEPENDENT of
the host chrome (which follows the OS/browser/VS Code); it defaults to light —
what LabVIEW users expect — regardless of the surrounding environment.

The JS is CSP-safe (listeners attached in JS, no inline ``on*`` attributes) and
dependency-free. It:

- sets ``document.documentElement.dataset.theme`` to ``'light'`` or ``'dark'``
  (always an explicit palette — never removed);
- persists the choice to ``localStorage['lvkitDiagramTheme']``;
- restores the initial mode from ``window.__lvkitInitialTheme`` (a host may
  inject one), else ``localStorage``, else ``'light'``;
- if running inside VS Code (``typeof acquireVsCodeApi === 'function'``),
  acquires the API ONCE (VS Code throws on a second ``acquireVsCodeApi()`` call)
  and ``postMessage({type:'lvkitDiagramTheme', value})`` on each change, so a
  host extension can persist/sync the choice. The acquired handle is stashed
  on ``window.__lvkitVsCodeApi`` so another inline script the host injects
  into the same page (e.g. the VS Code extension's SubVI click-navigation
  script, task #76) can reuse it instead of acquiring its own;
- keeps the button glyph + tooltip in sync with the current mode.
"""

from __future__ import annotations

__all__ = ["THEME_CONTROL_BUTTON", "THEME_CONTROL_SCRIPT", "THEME_CONTROL_BTN_ID"]

# The single element id the button and its script agree on. Kept here so both
# emitters (button HTML + script) stay in lockstep and a viewer template never
# has to hardcode it.
THEME_CONTROL_BTN_ID = "lvkitThemeBtn"

# One small icon button. Reuses the viewers' shared ``button`` styling (border/
# bg/color) INCLUDING inherited font metrics — no line-height/font-size pinning,
# so its height matches every other toolbar button exactly; its icon-button
# min-width/padding come from each viewer's ``.lvkit-theme-btn`` CSS rule (no
# inline style here). The glyph/title start on the 'auto' defaults and are
# overwritten by the script once it reads the persisted/injected mode. A no-JS
# viewer still sees a labelled, inert button.
THEME_CONTROL_BUTTON = (
    f'<button id="{THEME_CONTROL_BTN_ID}" type="button" class="lvkit-theme-btn" '
    'title="Diagram theme: Light (click to toggle)" '
    'aria-label="Toggle diagram theme">☀</button>'
)

# ☀ light, ☾ dark. The diagram theme is deliberately INDEPENDENT of the host
# chrome (which follows the OS/browser/VS Code) — it defaults to light (what
# LabVIEW users expect) and the button toggles light<->dark. Clean minimal glyphs
# that read at ~28px in both light and dark chrome.
THEME_CONTROL_SCRIPT = """<script>
(function(){
  var KEY = "lvkitDiagramTheme";
  var MODES = ["light", "dark"];
  var GLYPH = {light: "☀", dark: "☾"};
  var LABEL = {light: "Light", dark: "Dark"};
  // VS Code throws if acquireVsCodeApi() is called twice, so grab it ONCE here
  // and reuse the handle for every postMessage below. Stashed on `window` so
  // any OTHER inline script the host injects into this same page (e.g. the
  // VS Code extension's SubVI click-navigation script, task #76) can reuse
  // this one handle too, instead of acquiring (and crashing on) its own.
  var vscode = window.__lvkitVsCodeApi || null;
  if (!vscode && typeof acquireVsCodeApi === "function") {
    try { vscode = acquireVsCodeApi(); } catch (e) { vscode = null; }
  }
  if (vscode) window.__lvkitVsCodeApi = vscode;
  function initialMode(){
    if (typeof window.__lvkitInitialTheme === "string")
      return window.__lvkitInitialTheme;
    try { var v = localStorage.getItem(KEY); if (v) return v; } catch (e) {}
    return "light";
  }
  var mode = initialMode();
  if (MODES.indexOf(mode) < 0) mode = "light";  // ignore a stale "auto" value
  var btn = document.getElementById("__BTN_ID__");
  function apply(){
    var root = document.documentElement;
    // The diagram is always an explicit light/dark palette (default light),
    // independent of the host chrome — so data-theme is always set.
    root.setAttribute("data-theme", mode);
    if (btn) {
      btn.textContent = GLYPH[mode];
      btn.title = "Diagram theme: " + LABEL[mode] + " (click to cycle)";
    }
    if (vscode) vscode.postMessage({type: "lvkitDiagramTheme", value: mode});
  }
  if (btn) btn.addEventListener("click", function(){
    mode = MODES[(MODES.indexOf(mode) + 1) % MODES.length];
    try { localStorage.setItem(KEY, mode); } catch (e) {}
    apply();
  });
  apply();
})();
</script>""".replace("__BTN_ID__", THEME_CONTROL_BTN_ID)
