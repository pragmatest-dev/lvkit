"""LabVIEW Project Explorer - Run converted VIs.

This file is COPIED to the output directory during conversion.
Run with: python app.py (or python explorer.py)
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

# Set up package imports - add PARENT to path so this dir is a package
_pkg_dir = Path(__file__).parent
_pkg_name = _pkg_dir.name
if str(_pkg_dir.parent) not in sys.path:
    sys.path.insert(0, str(_pkg_dir.parent))

from nicegui import ui


class ProjectExplorer:
    """Main application with tree explorer and tabbed documents."""

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.open_tabs: dict[str, str] = {}
        self.tabs: Any = None
        self.panels: Any = None
        self.tree: Any = None
        self.ui_classes: dict[str, type] = {}
        self._ui_paths: dict[str, tuple[str, str | None]] = {}

    def scan_directory(self) -> list[dict]:
        """Scan directory for modules and build tree structure."""
        tree_items = []

        for ui_file in sorted(self.root_dir.glob("*_ui.py")):
            if ui_file.name.startswith("__"):
                continue
            module_name = ui_file.stem.replace("_ui", "")
            module_file = self.root_dir / f"{module_name}.py"
            if module_file.exists():
                label = self._to_vi_name(module_name)
                tree_items.append({"id": module_name, "label": label})
                self._ui_paths[module_name] = (ui_file.stem, None)

        for subdir in sorted(self.root_dir.iterdir()):
            if not subdir.is_dir():
                continue
            if subdir.name.startswith((".", "_", "primitives")):
                continue
            if not (subdir / "__init__.py").exists():
                continue

            lib_children = []
            for ui_file in sorted(subdir.glob("*_ui.py")):
                module_name = ui_file.stem.replace("_ui", "")
                module_file = subdir / f"{module_name}.py"
                if module_file.exists():
                    module_id = f"{subdir.name}/{module_name}"
                    label = self._to_vi_name(module_name)
                    lib_children.append({"id": module_id, "label": label})
                    self._ui_paths[module_id] = (ui_file.stem, subdir.name)

            if lib_children:
                tree_items.append({
                    "id": f"_lib_{subdir.name}",
                    "label": f"{subdir.name}/",
                    "children": lib_children,
                })

        return tree_items

    def _load_ui_class(self, module_id: str) -> type | None:
        """Load UI class on demand as package submodule (relative imports work)."""
        if module_id in self.ui_classes:
            return self.ui_classes[module_id]

        if module_id not in self._ui_paths:
            return None

        ui_module_name, library = self._ui_paths[module_id]

        try:
            # Import as package.module so relative imports work
            if library:
                mod = importlib.import_module(f"{_pkg_name}.{library}.{ui_module_name}")
            else:
                mod = importlib.import_module(f"{_pkg_name}.{ui_module_name}")

            for name in dir(mod):
                if name.endswith("UI") and not name.startswith("_"):
                    cls = getattr(mod, name)
                    if isinstance(cls, type):
                        self.ui_classes[module_id] = cls
                        return cls
        except Exception as e:
            print(f"Failed to load {module_id}: {e}")
            import traceback
            traceback.print_exc()

        return None

    def _to_vi_name(self, module_name: str) -> str:
        """Convert module name to VI display name."""
        parts = module_name.split("_")
        name = " ".join(p.capitalize() for p in parts if p)
        return f"{name}.vi"

    def build(self) -> None:
        """Build the main UI layout."""
        tree_data = self.scan_directory()

        if not tree_data:
            ui.label("No converted VIs found.").classes("text-xl p-4")
            ui.label(f"Directory: {self.root_dir}").classes("text-gray-500 p-4")
            return

        with ui.splitter(value=25).classes("w-full h-screen") as splitter:
            with splitter.before:
                with ui.column().classes("p-2 h-full"):
                    ui.label("Project Explorer").classes("text-lg font-bold mb-2")
                    ui.label(str(self.root_dir)).classes("text-xs text-gray-500 mb-2 truncate")
                    self.tree = ui.tree(
                        tree_data,
                        label_key="label",
                        on_select=lambda e: self.on_tree_select(e),
                    ).classes("w-full").props("dense")

            with splitter.after:
                with ui.column().classes("w-full h-full"):
                    self.tabs = ui.tabs().classes("w-full")
                    self.panels = ui.tab_panels(self.tabs).classes("w-full flex-grow")
                    with self.panels:
                        with ui.tab_panel("_welcome").classes("p-8"):
                            ui.label("Select a VI from the tree.").classes("text-xl text-gray-500")

    def on_tree_select(self, e) -> None:
        """Handle tree node selection."""
        module_id = e.value
        if not module_id:
            return

        expanded = self.tree._props.get('expanded') or []

        # Toggle using tree's expand/collapse
        if module_id in expanded:
            self.tree.collapse([module_id])
        else:
            self.tree.expand([module_id])

        # Deselect so clicking same node again triggers on_select
        self.tree.deselect()

        # Open tab if it's a VI (not a folder)
        if not module_id.startswith("_lib_"):
            self.open_tab(module_id)

    def open_tab(self, module_id: str) -> None:
        """Open a VI in a new tab."""
        if module_id in self.open_tabs:
            self.tabs.value = module_id
            return

        ui_class = self._load_ui_class(module_id)
        if not ui_class:
            ui.notify(f"Could not load UI for {module_id}", type="warning")
            return

        label = self._to_vi_name(module_id.split("/")[-1])

        with self.tabs:
            with ui.tab(module_id).classes("pr-0"):
                ui.label(label).classes("mr-1")
                ui.button(
                    icon="close",
                    on_click=lambda _, mid=module_id: self.close_tab(mid),
                ).props("flat dense round size=xs").classes("ml-1")

        with self.panels:
            with ui.tab_panel(module_id).classes("p-4"):
                try:
                    ui_class().build()
                except Exception as e:
                    ui.label(f"Error: {e}").classes("text-red-500")

        self.open_tabs[module_id] = label
        self.tabs.value = module_id

    def close_tab(self, module_id: str) -> None:
        """Close a tab."""
        if module_id not in self.open_tabs:
            return
        del self.open_tabs[module_id]

        for tab in list(self.tabs):
            if hasattr(tab, "_props") and tab._props.get("name") == module_id:
                tab.delete()
                break

        for panel in list(self.panels):
            if hasattr(panel, "_props") and panel._props.get("name") == module_id:
                panel.delete()
                break

        self.tabs.value = next(iter(self.open_tabs)) if self.open_tabs else "_welcome"


@ui.page("/")
def main_page():
    explorer = ProjectExplorer(Path(__file__).parent)
    explorer.build()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(port=8080, title="LabVIEW Explorer", reload=False)
