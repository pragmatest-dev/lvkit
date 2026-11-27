"""Every method the generated ``Panel`` tells its views is one the NiceGUI view
(``PanelWidgets``) defines, and the calls that reach the window and the SubPanel
parts of the protocol leave a run and its page working."""

from __future__ import annotations

import ast
from types import SimpleNamespace

from lvkit.codegen.panel_objects_module import PANEL_OBJECTS_SOURCE
from lvkit.codegen.panels_module import PANELS_MODULE_SOURCE
from lvkit.panel.runtime.view import PanelWidgets
from tests.error_diagrams import Package, Project, project  # noqa: F401
from tests.helpers import generated_panels
from tests.panel_diagrams import NAME, counting_vi
from tests.panel_harness import panel_of, simulate, until
from tests.test_panel_run import STEM


def _told_methods() -> set[str]:
    """The names of the ``PanelView`` methods the generated panel modules mention as
    a string (each ``_tell`` call names its method that way)."""
    tree = ast.parse(PANELS_MODULE_SOURCE + PANEL_OBJECTS_SOURCE)
    strings = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    protocol = generated_panels().PanelView
    return {name for name in strings if callable(getattr(protocol, name, None))}


def test_the_panel_tells_a_view_only_methods_the_nicegui_view_defines() -> None:
    told = _told_methods()
    assert {"geometry_changed", "window_changed", "subpanel_changed"} <= told
    missing = sorted(n for n in told if not callable(getattr(PanelWidgets, n, None)))
    assert missing == []


def test_window_window_property_and_subpanel_calls_leave_the_page_and_run_working(
    project: Project,  # noqa: F811
) -> None:
    project.add(counting_vi())
    package: Package = project.generate(NAME, panel=True)
    panel = panel_of(package, STEM)
    vi_panel = panel.vi_function.panel

    async def scenario(user) -> None:  # noqa: ANN001
        vi_panel.open_window(False, 3)
        vi_panel.set_window_state(4)
        vi_panel.bring_to_front()
        vi_panel.close_window()
        vi_panel.set_window_value("panel_bounds", (0, 0, 100, 100))
        vi_panel.set_window_value("origin", (1, 2))
        other = type(vi_panel)(lambda **_: None)
        vi_panel.insert_vi("Sub", SimpleNamespace(panel=other))
        vi_panel.remove_vi("Sub")
        vi_panel.set("Stop", True)
        vi_panel.run(wait=False)
        await until(user, "done")

    simulate(panel.build_panel, scenario)
    assert vi_panel.window_state == 2
    assert vi_panel.window_value("origin", None) == (1, 2)
