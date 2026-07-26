"""Viewer-host hover: show the SVG's OWN connector-pane panel at a constant size.

The block-diagram SVG draws each node's connector-pane panel as a hidden
``<g class="lv-help" data-node="...">`` and, standalone, reveals it in place on
hover (``render/__init__.py``'s ``_HOVER_PANEL_JS``). Inside a scrolling/zooming
HTML viewer that in-place panel is a problem: it scales with the diagram zoom,
and — because the SCROLL lives in the HTML host, not the SVG — the SVG can't keep
it inside the *visible* window.

So the viewers hand placement to the host. This module supplies ONE blob
(a ``<style>`` + ``<script>``, same shared-injection pattern as
:mod:`lvkit.render.theme_control`) stapled in near end-of-body via the
``__HELP_TIP__`` placeholder. It:

- sets ``__lvSuppressPanel`` on every embedded ``<svg>`` so the SVG's own hover
  script does NOT reveal its in-place panel (a raw standalone ``.svg`` leaves the
  flag unset and still gets it);
- on hover of a ``.lv-node``, reads that node's panel **directly from the SVG**
  (clones its ``<g class="lv-help">`` — the exact icon + terminal geometry the
  renderer drew, never recomputed) into a fixed-position ``<svg>`` at a CONSTANT
  on-screen size, following the cursor and clamped to the viewport, so it can
  never land off-screen regardless of zoom/scroll. Nodes with no panel (e.g.
  constants) keep their native ``<title>`` tooltip.

Same ``read-from-the-SVG`` contract as the host's SubVI click-navigation.
"""

from __future__ import annotations

__all__ = ["HELP_TIP"]

# PANEL_SCALE: on-screen size = this * the panel's own drawn size. ~1.6 sits
# between the drawn size (small) and a 2x blow-up (too large).
HELP_TIP = """<style>
  /* A bare positioning container — the cloned connector-pane panel brings its
     own frame, so no border/background/shadow here (that would double it up).
     pointer-events:none so the card never steals the hover from the diagram. */
  .lv-tip{position:fixed;z-index:9999;left:0;top:0;pointer-events:none}
  .lv-tip[hidden]{display:none}
  /* The cloned .lv-help carries its own drop shadow from the diagram's global
     .lv-help CSS rule (_BASE_CSS). overflow:visible lets that shadow paint past
     the tight bbox-sized <svg> instead of being clipped — no re-declared style. */
  .lv-tip svg{display:block;overflow:visible}
</style>
<script>
(function(){
  try {
    var SCALE = 1.6, GAP = 14, EDGE = 8;
    var tip = document.createElement("div");
    tip.className = "lv-tip";
    tip.hidden = true;
    document.body.appendChild(tip);

    // Tell every embedded SVG's own hover script to stand down: WE place the
    // panel (the scroll lives here, so the host must own placement/clamping).
    var svgs = document.querySelectorAll("svg");
    for (var i = 0; i < svgs.length; i++) svgs[i].__lvSuppressPanel = true;

    // Read the node's panel straight out of the SVG (its <g class="lv-help">) and
    // wrap the clone in a fixed-size <svg> so it renders at a constant on-screen
    // size. Returns null for a node with no panel (constant) -> native <title>.
    function panelSvg(node){
      var root = node.ownerSVGElement, dn = node.getAttribute("data-node");
      if (!root || !dn) return null;
      var panel = root.querySelector('.lv-help[data-node="' + dn + '"]');
      if (!panel) return null;
      var bb; try { bb = panel.getBBox(); } catch (_){ return null; }
      if (!bb || !bb.width || !bb.height) return null;
      // viewBox = the panel's exact bbox (no padding), so the overlay ends right
      // at the panel's own frame -- no background-color margin around it.
      return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="' +
             bb.x + " " + bb.y + " " + bb.width + " " + bb.height + '" width="' +
             (bb.width * SCALE) + '" height="' + (bb.height * SCALE) + '">' +
             panel.outerHTML + "</svg>";
    }

    // Clamp to the diagram VIEW AREA (the .stage-wrap that clips/scrolls the
    // SVG), not the whole page: in the diff viewer that keeps the panel over the
    // diagram instead of drifting onto the changes list, and zoom is scoped to
    // this same area — so the help it explains belongs here too. .closest()
    // crosses the SVG->HTML boundary; falls back to the viewport (raw .svg).
    function viewRect(){
      var wrap = cur && cur.closest && cur.closest(".stage-wrap");
      return wrap ? wrap.getBoundingClientRect()
                  : {left: 0, top: 0, right: innerWidth, bottom: innerHeight};
    }
    // Fixed-position, so clamping is trivial screen-space math — no scroll/
    // user-coord conversion, and the panel can't land outside the view area.
    function position(x, y){
      var w = tip.offsetWidth, h = tip.offsetHeight, vr = viewRect();
      var px = x + GAP, py = y + GAP;
      if (px + w > vr.right - EDGE) px = x - GAP - w;    // flip to cursor's left
      if (px < vr.left + EDGE) px = vr.left + EDGE;
      if (py + h > vr.bottom - EDGE) py = y - GAP - h;   // flip above the cursor
      if (py < vr.top + EDGE) py = vr.top + EDGE;
      tip.style.left = px + "px";
      tip.style.top = py + "px";
    }

    var cur = null;
    function nodeAt(el){ return el && el.closest ? el.closest(".lv-node") : null; }
    document.addEventListener("pointerover", function(e){
      try {
        var node = nodeAt(e.target);
        if (node === cur) return;
        var html = node ? panelSvg(node) : null;
        if (html){
          cur = node;
          tip.innerHTML = html;
          // The diagram's global CSS hides .lv-help; the .lv-help-shown class (same
          // rule the in-place panel uses) reveals the clone.
          var cp = tip.querySelector(".lv-help");
          if (cp) cp.classList.add("lv-help-shown");
          tip.hidden = false;
          position(e.clientX, e.clientY);
        } else { cur = null; tip.hidden = true; }
      } catch (_){}
    });
    document.addEventListener("pointermove", function(e){
      try { if (cur && cur.contains(e.target)) position(e.clientX, e.clientY); }
      catch (_){}
    });
    document.addEventListener("pointerout", function(e){
      try {
        if (!cur) return;
        var to = e.relatedTarget;
        if (to && cur.contains(to)) return;   // still within the same node
        cur = null;
        tip.hidden = true;
      } catch (_){}
    });
  } catch (_){ /* hover is optional — never break the viewer */ }
})();
</script>"""
