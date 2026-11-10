"""A server-side file picker: a dialog that lists the folder it is in, where a
double-click opens a folder and Select returns the picked file or folder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nicegui import ui

_FOLDER_GLYPH = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3l1.2 1.4H13.5a1 1 0 0 1 1 1v7'
    'a1 1 0 0 1-1 1H2.5a1 1 0 0 1-1-1z" fill="#f6c344" stroke="#d9a520" '
    'stroke-width=".7"/></svg>'
)
_FILE_GLYPH = (
    '<svg width="15" height="15" viewBox="0 0 16 16" style="vertical-align:-2px">'
    '<path d="M4 1.5h5l3 3v9a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V2.5a1 1 0 0 1 1-1z" '
    'fill="#e9edf2" stroke="#9aa5b1" stroke-width=".7"/>'
    '<path d="M9 1.5v3h3" fill="none" stroke="#9aa5b1" stroke-width=".7"/></svg>'
)


def _row(glyph: str, text: str, path: Path) -> dict[str, str]:
    cell = (
        '<span style="display:inline-flex;align-items:center;gap:6px">'
        f"{glyph}<span>{text}</span></span>"
    )
    return {"name": cell, "path": str(path)}


class FilePicker(ui.dialog):
    """Awaiting the dialog gives the picked path as text, or None when it was
    cancelled."""

    def __init__(self, directory: str) -> None:
        super().__init__()
        start = Path(directory).expanduser()
        self._folder = start if start.is_dir() else Path.home()
        with self, ui.card().style("min-width:440px"):
            self.grid = (
                ui.aggrid(
                    {
                        "columnDefs": [{"field": "name", "headerName": ""}],
                        "rowSelection": "single",
                        "rowData": [],
                    },
                    html_columns=[0],
                )
                .classes("w-full h-64")
                .on("cellDoubleClicked", self._open)
            )
            with ui.row().classes("justify-end w-full gap-2"):
                ui.button("Cancel", on_click=self.close).props("flat")
                ui.button("Select", on_click=self._select)
        self.list_folder()

    def list_folder(self) -> None:
        try:
            entries = sorted(
                (p for p in self._folder.iterdir() if not p.name.startswith(".")),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            entries = []
        rows = [_row(_FOLDER_GLYPH, "..", self._folder.parent)]
        rows += [
            _row(_FOLDER_GLYPH if p.is_dir() else _FILE_GLYPH, p.name, p)
            for p in entries
        ]
        self.grid.options["rowData"] = rows
        self.grid.options["columnDefs"][0]["headerName"] = str(self._folder)
        self.grid.update()

    async def _open(self, event: Any) -> None:
        picked = Path(event.args["data"]["path"])
        if picked.is_dir():
            self._folder = picked
            self.list_folder()
        else:
            self.submit(str(picked))

    async def _select(self) -> None:
        rows = await self.grid.get_selected_rows()
        self.submit(rows[0]["path"] if rows else str(self._folder))
