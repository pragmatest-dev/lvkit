"""The one ``path -> output body`` render entry: a ``.ctl`` control renders its
front panel, anything else renders as a VI's block diagram. The output cache
(``output_cache.cached_render``) and every frontend go through this, so the
choice of view by file kind is made in exactly one place."""

from __future__ import annotations

from pathlib import Path

from ..load_mode import LoadMode
from ..run_report import RunSettings
from . import ThemeMode, render_vi_body
from . import render_vi_body_with_name as _render_vi_body_with_name
from .ctl import render_ctl_body
from .ctl import render_ctl_body_with_name as _render_ctl_body_with_name
from .rendered import RenderedBody


def render_body(
    path: Path,
    *,
    fmt: str = "html",
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    instrlib_root: Path | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    original_path: Path | None = None,
    theme_mode: ThemeMode = "auto",
    ref: str | None = None,
) -> str | None:
    """Render ``path`` to ``fmt`` (``"svg"`` or ``"html"``). The library roots
    shape the dependency load of a VI and of a control; ``mode`` applies to a
    VI only."""
    if path.suffix.lower() == ".ctl":
        return render_ctl_body(
            path,
            fmt=fmt,
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            instrlib_root=instrlib_root,
            theme_mode=theme_mode,
            ref=ref,
        )
    return render_vi_body(
        path,
        fmt=fmt,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        instrlib_root=instrlib_root,
        mode=mode,
        original_path=original_path,
        theme_mode=theme_mode,
        ref=ref,
    )


def render_body_with_name(
    path: Path,
    *,
    fmt: str = "html",
    vilib_root: Path | None = None,
    userlib_root: Path | None = None,
    instrlib_root: Path | None = None,
    mode: LoadMode = LoadMode.MINIMAL,
    original_path: Path | None = None,
    theme_mode: ThemeMode = "auto",
    ref: str | None = None,
    run_settings: RunSettings | None = None,
) -> RenderedBody | None:
    """Like :func:`render_body`, but also returns the rendered VI/control's own
    name (qualified for a VI, the typedef name for a ``.ctl``) — ALREADY
    resolved internally regardless of ``fmt``, so this costs nothing beyond
    :func:`render_body` itself. Used by the output cache to persist identity
    alongside the body (#114's follow-up) without a second graph load to
    recover it later. Kept as a SEPARATE dispatcher from :func:`render_body`
    (rather than one calling the other) so each keeps dispatching to its own
    ``*_with_name``/plain renderer pair — ``render_body``'s own dispatch is
    monkeypatched directly in tests and must keep calling exactly those two
    names."""
    if path.suffix.lower() == ".ctl":
        return _render_ctl_body_with_name(
            path,
            fmt=fmt,
            vilib_root=vilib_root,
            userlib_root=userlib_root,
            instrlib_root=instrlib_root,
            theme_mode=theme_mode,
            ref=ref,
        )
    return _render_vi_body_with_name(
        path,
        fmt=fmt,
        vilib_root=vilib_root,
        userlib_root=userlib_root,
        instrlib_root=instrlib_root,
        mode=mode,
        original_path=original_path,
        theme_mode=theme_mode,
        ref=ref,
        run_settings=run_settings,
    )
