"""The LabVIEW path-control analog: an outlined text field with a real
server-filesystem Browse (adopt-first -- a maintained ``ui.dialog``/``ui.aggrid``,
not a bespoke file tree).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

from ._util import _jsonable

# Clean-room folder / file glyphs for the picker rows (inline SVG, our own
# shapes -- robust where an emoji font is absent, unlike 📁/📄). Also used by
# arraygrid's path-field Browse column, so a path cell has the same glyph
# inside the grid as it does outside it.
_SVG_FOLDER = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3l1.2 1.4H13.5a1 1 0 0 1 1 1v7'
    'a1 1 0 0 1-1 1H2.5a1 1 0 0 1-1-1z" fill="#f6c344" stroke="#d9a520" '
    'stroke-width=".7"/></svg>'
)
_SVG_FILE = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M4 1.5h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.5a1 1 0 0 1 1-1z" '
    'fill="#e9edf2" stroke="#9aa5b1" stroke-width=".7"/>'
    '<path d="M9 1.5v3h3" fill="none" stroke="#9aa5b1" stroke-width=".7"/></svg>'
)


class _FilePicker(ui.dialog):
    """A minimal server-filesystem browser dialog (adapted from NiceGUI's
    local_file_picker example): a grid of the current directory's entries,
    double-click a folder to descend / ``..`` to go up, pick a file/folder with
    Select. ``submit(path)`` resolves the ``await``. Backs the path control's
    real Browse -- the LabVIEW path-browse analog, adopt-first."""

    def __init__(self, directory: str) -> None:
        super().__init__()
        start = Path(directory).expanduser()
        self._dir = start if start.is_dir() else Path.home()
        with self, ui.card().style("min-width:440px"):
            self._grid = (
                ui.aggrid(
                    {
                        "columnDefs": [{"field": "name", "headerName": ""}],
                        "rowSelection": "single",
                        "rowData": [],
                    },
                    html_columns=[0],
                )
                .classes("w-full h-64")
                .on("cellDoubleClicked", self._descend)
            )
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Cancel", on_click=self.close).props("flat")
                ui.button("Select", on_click=self._ok)
        self._refresh()

    def _refresh(self) -> None:
        try:
            entries = sorted(
                (p for p in self._dir.iterdir() if not p.name.startswith(".")),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            entries = []
        def _cell(icon: str, text: str) -> str:
            # Inline-flex keeps the glyph beside the name on one row (a bare SVG
            # + text wraps in an AG Grid html cell).
            return (
                '<span style="display:inline-flex;align-items:center;gap:6px">'
                f"{icon}<span>{text}</span></span>"
            )

        rows = [
            {"name": _cell(_SVG_FOLDER, ".."), "path": str(self._dir.parent)}
        ]
        for p in entries:
            icon = _SVG_FOLDER if p.is_dir() else _SVG_FILE
            rows.append({"name": _cell(icon, p.name), "path": str(p)})
        self._grid.options["rowData"] = rows
        self._grid.options["columnDefs"][0]["headerName"] = str(self._dir)
        self._grid.update()

    async def _descend(self, e: Any) -> None:
        p = Path(e.args["data"]["path"])
        if p.is_dir():
            self._dir = p
            self._refresh()
        else:
            self.submit(str(p))

    async def _ok(self) -> None:
        rows = await self._grid.get_selected_rows()
        self.submit(rows[0]["path"] if rows else str(self._dir))


def path_control(
    state: Any, field: str, *, readonly: bool = False, label: str = ""
) -> None:
    """A LabVIEW path control: an outlined text field bound to ``state.<field>``,
    with a real Browse (a server-filesystem picker dialog) on an input. A
    read-only indicator shows the path without a browse button."""
    inp = ui.input(label).props("outlined dense").classes("w-full")

    def _text(v: Any) -> str:
        return _jsonable(v) or ""

    # A path is often longer than the field; a tooltip shows it in full on hover
    # (hidden while empty so there's no blank bubble).
    with inp:
        tip = ui.tooltip().bind_text_from(state, field, backward=_text)
        tip.bind_visibility_from(state, field, backward=bool)

    if readonly:
        # A path indicator holds a Path (from the logic); show it as text so
        # NiceGUI can serialize it (see _jsonable).
        inp.bind_value_from(state, field, backward=_text)
        inp.disable()
        return
    inp.bind_value(state, field)

    async def _browse() -> None:
        picked = await _FilePicker(str(getattr(state, field) or Path.home()))
        if picked:
            setattr(state, field, picked)

    with inp.add_slot("append"):
        ui.button(icon="folder_open", on_click=_browse).props(
            "flat dense round size=sm"
        ).classes("text-gray-500")
        ui.tooltip("Browse…")
