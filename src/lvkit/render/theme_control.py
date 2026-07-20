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
flip. It is deliberately UNOBTRUSIVE — a single ~28px icon button that cycles
``auto → light → dark → auto`` on click, NOT a segmented control — so the theme
control never becomes a large part of the viewer chrome.

The JS is CSP-safe (listeners attached in JS, no inline ``on*`` attributes) and
dependency-free. It:

- sets ``document.documentElement.dataset.theme`` to ``''`` (auto →
  ``removeAttribute``), ``'light'``, or ``'dark'``;
- persists the choice to ``localStorage['lvkitDiagramTheme']``;
- restores the initial mode from ``window.__lvkitInitialTheme`` (a host may
  inject one), else ``localStorage``, else ``'auto'``;
- if running inside VS Code (``typeof acquireVsCodeApi === 'function'``),
  acquires the API ONCE (VS Code throws on a second ``acquireVsCodeApi()`` call)
  and ``postMessage({type:'lvkitDiagramTheme', value})`` on each change, so a
  host extension can persist/sync the choice;
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
# so its height matches every other toolbar button exactly. The glyph/title
# start on the 'auto' defaults and are overwritten by the script once it reads
# the persisted/injected mode. A no-JS viewer still sees a labelled, inert
# button.
THEME_CONTROL_BUTTON = (
    f'<button id="{THEME_CONTROL_BTN_ID}" type="button" class="lvkit-theme-btn" '
    'style="min-width:30px;padding:5px 9px" '
    'title="Diagram theme: Auto (click to cycle)" '
    'aria-label="Cycle diagram theme">◐</button>'
)

# ◐ auto (half-filled — follows the editor), ☀ light, ☾ dark. Clean minimal
# glyphs that read at ~28px in both light and dark chrome.
THEME_CONTROL_SCRIPT = """<script>
(function(){
  var KEY = "lvkitDiagramTheme";
  var MODES = ["auto", "light", "dark"];
  var GLYPH = {auto: "◐", light: "☀", dark: "☾"};
  var LABEL = {auto: "Auto (follows editor)", light: "Light", dark: "Dark"};
  // VS Code throws if acquireVsCodeApi() is called twice, so grab it ONCE here
  // and reuse the handle for every postMessage below.
  var vscode = null;
  if (typeof acquireVsCodeApi === "function") {
    try { vscode = acquireVsCodeApi(); } catch (e) { vscode = null; }
  }
  function initialMode(){
    if (typeof window.__lvkitInitialTheme === "string")
      return window.__lvkitInitialTheme;
    try { var v = localStorage.getItem(KEY); if (v) return v; } catch (e) {}
    return "auto";
  }
  var mode = initialMode();
  if (MODES.indexOf(mode) < 0) mode = "auto";
  var btn = document.getElementById("__BTN_ID__");
  function apply(){
    var root = document.documentElement;
    // auto = no override -> the SVG's own @media(prefers-color-scheme) decides.
    if (mode === "auto") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
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
