"""Helpers that drive a generated panel headless with NiceGUI's user simulation."""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from types import ModuleType
from typing import Any

from nicegui import Client, ui
from nicegui.testing.user import User
from nicegui.testing.user_simulation import user_simulation

from tests.error_diagrams import Package


def panel_of(package: Package, stem: str) -> ModuleType:
    return importlib.import_module(f"{package.module.__package__}.{stem}_panel")


def controls_of(package: Package) -> ModuleType:
    return importlib.import_module(f"{package.module.__package__}.controls")


def simulate(
    build_panel: Callable[[], None], scenario: Callable[[User], Awaitable[None]]
) -> None:
    async def run() -> None:
        async with user_simulation(build_panel) as user:
            await user.open("/")
            await scenario(user)

    asyncio.run(run())


def found(user: User, kind: type) -> list[Any]:
    """The elements of ``kind`` on the page, in the order they were created."""
    return sorted(user.find(kind=kind).elements, key=lambda element: element.id)


def labels(user: User) -> str:
    return " ".join(str(e.text) for e in user.find(kind=ui.label).elements)


async def until(user: User, text: str, seconds: float = 4.0) -> None:
    """Wait until a label shows ``text``."""
    for _ in range(int(seconds / 0.05)):
        if text in labels(user):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{text!r} not shown; labels: {labels(user)}")


async def run_once(user: User) -> None:
    """Press Run and wait until the run is done."""
    user.find(marker="Run").click()
    await until(user, "done")


def build_in_a_page(build_panel: Callable[[], None]) -> None:
    """Build the panel once in a page of the simulation; what it raises while
    building comes out."""

    async def run() -> None:
        async with user_simulation():
            with Client(ui.page("/gap")):
                build_panel()

    asyncio.run(run())


def fire(element: Any, event_type: str, args: dict[str, Any]) -> None:
    """Deliver the browser event ``event_type`` with ``args`` to ``element``."""
    for listener_id, listener in list(element._event_listeners.items()):
        if listener.type == event_type:
            element._handle_event({"listener_id": listener_id, "args": args})
