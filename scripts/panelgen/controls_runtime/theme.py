"""Styling: ONE place that restyles the whole panel. Because we own the
mapping (not the widgets), every control + the toolbar read from the active
Theme, so a single switch re-skins everything -- a modern look or a classic
LabVIEW one -- WITHOUT touching any behavior. Styling only: colors/fonts/
density, never geometry or wiring.
"""

from __future__ import annotations

from dataclasses import dataclass

# The scalar-control caption label's classes. A generated panel imports this
# (instead of inlining the literal) so a widget's caption and controls' own
# captions (e.g. the array caption -- see arraygrid.CAPTION_CLS's sibling
# constant there) stay in step from one shared source.
CAPTION_CLS = "text-xs font-semibold text-gray-500 truncate leading-none"


@dataclass(frozen=True)
class Theme:
    """Styling knobs for a panel. ``grid_class`` picks the AG Grid base theme;
    ``grid_vars`` are its ``--ag-*`` design tokens; ``toolbar_classes`` styles the
    VI toolbar row."""

    name: str
    grid_class: str
    grid_vars: str
    toolbar_classes: str


MODERN = Theme(
    name="modern",
    grid_class="ag-theme-quartz",
    grid_vars=(
        "font-size:12px;--ag-grid-size:4px;--ag-cell-horizontal-padding:6px;"
        "--ag-row-hover-color:#eff6ff;--ag-border-color:#e5e7eb"
    ),
    toolbar_classes=(
        "w-full items-center gap-1 px-2 py-1 border-b bg-gray-50 "
        "dark:bg-neutral-800 dark:border-neutral-700"
    ),
)

# A nod to classic LabVIEW / Win-9x: compact, boxy, warm-grey, Tahoma.
CLASSIC = Theme(
    name="classic",
    grid_class="ag-theme-balham",
    grid_vars=(
        "font-size:11px;--ag-grid-size:3px;--ag-cell-horizontal-padding:4px;"
        "--ag-borders:solid 1px;--ag-border-color:#808080;"
        "--ag-background-color:#ece9d8;--ag-header-background-color:#d4d0c8;"
        "--ag-odd-row-background-color:#f5f4ec;--ag-row-hover-color:#e8e6d5;"
        "--ag-font-family:Tahoma,Geneva,sans-serif"
    ),
    toolbar_classes=(
        "w-full items-center gap-1 px-2 py-1 border-b "
        "bg-[#d4d0c8] border-[#808080]"
    ),
)

_ACTIVE_THEME = MODERN


def set_theme(new_theme: Theme) -> None:
    """Switch the active theme (call once, before ``build_panel()``). Restyles
    every control + the toolbar; behavior is unaffected."""
    global _ACTIVE_THEME
    _ACTIVE_THEME = new_theme


def theme() -> Theme:
    return _ACTIVE_THEME
