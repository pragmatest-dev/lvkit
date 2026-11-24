"""The front-panel objects of the generated ``panels.py``: ``GObject``,
``Decoration``, ``Pane`` and ``PanelLayout``.

``GObject`` is what a control, a decoration and a pane have in common (NI's GObject
class): the rectangle the file stores for it (Position and Bounds), the VI that owns
it, and, for a cluster, the decorations and all objects in it. ``Control`` of
``panels.py`` is one; a ``Decoration`` is a decoration or a free label on the panel
or in a cluster; a ``Pane`` is one pane of the panel's hierarchy with the controls
on it and its scroll Origin. ``PanelLayout`` is the part of ``PanelValues`` that
holds them, built from the ``layout`` the ``Panel`` is given
(``codegen/panel_layout.py`` describes it) and answers Panes[], Decorations[] and All
Objects[] of the panel.

NI's pages: Position, Position:Left and Position:Top
(``generic/gobject/position.html``, ``left.html``, ``top.html``: "Position of the top
left corner of the bounding rectangle of the object on the front panel window", read
and write, settable while the VI runs; for most objects relative to the origin of the
owning pane); Bounds, Bounds:Area Width and
Bounds:Area Height (``bounds.html``, ``areawidth.html``, ``areaheight.html``: "Maximum
bounding area of the object", read only). The file stores one rectangle for an object,
without its label, so Bounds is that rectangle's width and height (the maximum
bounding area NI describes also holds the label and legend, which the file does not
record apart from it). Origin of a pane (``pane/origin.html``): "the vertical and
horizontal coordinates of the upper left corner of the pane", read and write, which
starts at (0, 0), a pane that is not scrolled. Owning VI (``generic/owningvi.html``):
"a reference to the VI that owns this object". Decorations[] and All Objects[] of the
panel and of a cluster, Controls[] of a pane (``panel/decorations.html``,
``panel/allobjects.html``, ``panel/panes.html``, ``cluster/decorations.html``,
``cluster/allobjects.html``, ``pane/controls.html``), all read only. A free label is
a decoration (NI's class hierarchy lists Text under Decoration).
"""

from __future__ import annotations

PANEL_OBJECTS_NAME = "GObject"

PANEL_OBJECTS_SOURCE = '''
class GObject:
    """What every object of a front panel has: the rectangle the file stores for it
    (``(top, left, bottom, right)`` in the space of its pane), the VI that owns it
    and, for a cluster, what is in it."""

    _rect: tuple[int, int, int, int] | None = None
    _decorations: list[Decoration] | None = None
    _members: list[GObject] | None = None
    _owner: GObject | None = None

    def _named(self) -> str:
        return self.name

    @property
    def owner(self) -> Any:
        """The Owner property: the cluster that holds the object when it is an
        element of a cluster, else the pane it is on."""
        if self._owner is None:
            raise NotImplementedError(
                f"Owner of {self._named()!r}: the file records no pane hierarchy "
                "that holds it"
            )
        return self._owner

    def _rectangle(self, what: str) -> tuple[int, int, int, int]:
        if self._rect is None:
            raise NotImplementedError(
                f"{what} of {self._named()!r}: the file stores no rectangle for it"
            )
        return self._rect

    @property
    def owning_vi(self) -> Any:
        """The Owning VI property: the reference to the VI whose panel holds the
        object, which is its function."""
        function = getattr(self.panel, "function", None)
        if function is None:
            raise NotImplementedError(
                f"Owning VI of {self._named()!r}: its panel belongs to a Global VI, "
                "which has no function to refer to"
            )
        return function

    @property
    def position(self) -> tuple[int, int]:
        """The Position property: ``(left, top)``."""
        top, left, _, _ = self._rectangle("Position")
        return (left, top)

    @position.setter
    def position(self, value: Sequence[int]) -> None:
        left, top = value
        old_top, old_left, bottom, right = self._rectangle("Position")
        self._rect = (top, left, top + bottom - old_top, left + right - old_left)
        self.panel._tell("geometry_changed", self.name, "position", (left, top))

    @property
    def left(self) -> int:
        return self.position[0]

    @left.setter
    def left(self, value: int) -> None:
        self.position = (value, self.position[1])

    @property
    def top(self) -> int:
        return self.position[1]

    @top.setter
    def top(self, value: int) -> None:
        self.position = (self.position[0], value)

    @property
    def bounds(self) -> tuple[int, int]:
        """The Bounds property: ``(width, height)`` of the stored rectangle."""
        top, left, bottom, right = self._rectangle("Bounds")
        return (right - left, bottom - top)

    @property
    def area_width(self) -> int:
        return self.bounds[0]

    @property
    def area_height(self) -> int:
        return self.bounds[1]

    @property
    def origin(self) -> tuple[int, int]:
        raise PanelError(
            code=1026,
            source="GObject.origin",
            message="Pane Reference is invalid.",
        )

    @property
    def decorations(self) -> list[Decoration]:
        """The Decos[] of a cluster: a reference to each decoration in it, the same
        object each time; error 1026 for an object that is not a cluster."""
        if self._decorations is None:
            raise PanelError(
                code=1026,
                source="GObject.decorations",
                message="Control Reference is invalid.",
            )
        return list(self._decorations)

    @property
    def all_objects(self) -> list[GObject]:
        """The AllObjs[] of a cluster: a reference to each field and decoration in
        it in the order the file stores them (first = topmost); error 1026 for an
        object that is not a cluster."""
        if self._members is None:
            raise PanelError(
                code=1026,
                source="GObject.all_objects",
                message="Control Reference is invalid.",
            )
        return list(self._members)

    def _attach_cluster(self, spec: Sequence[Any]) -> None:
        """Give the cluster the objects of its pane (``panel_layout``): its fields
        get their rectangle and the decorations are made."""
        items, specs = spec
        fields = getattr(self, "_fields", None) or []
        self._decorations = []
        self._members = []
        for item in items:
            if item[0] == "f":
                if item[1] < len(fields):
                    fields[item[1]]._rect = tuple(item[2:6])
                    fields[item[1]]._owner = self
                    self._members.append(fields[item[1]])
            else:
                decoration = Decoration(
                    self.panel,
                    f"{self.name} decoration {len(self._decorations)}",
                    tuple(item[1:5]),
                    item[5],
                )
                decoration._owner = self
                self._decorations.append(decoration)
                self._members.append(decoration)
        for field, sub in zip(fields, specs):
            if sub is not None:
                field._attach_cluster(sub)


class Decoration(GObject):
    """A decoration or free label of a panel or cluster. Its Visible property starts
    as the file stores it and a write is held and told to the views."""

    def __init__(
        self,
        panel: PanelValues,
        name: str,
        rect: tuple[int, int, int, int],
        hidden: bool,
    ) -> None:
        self.panel = panel
        self.name = name
        self._rect = rect
        self._state: dict[str, Any] = {"visible": not hidden}

    def get_property(self, prop: str) -> Any:
        if prop not in self._state:
            raise PanelError(
                code=1026,
                source="Decoration.get_property",
                message=f'"{self.name}" has no {prop} property.',
            )
        return self._state[prop]

    def set_property(self, prop: str, value: Any) -> None:
        self.get_property(prop)
        self._state[prop] = value
        self.panel._tell("property_changed", self.name, prop, value)


class Pane(GObject):
    """One pane of the panel's hierarchy: the controls on it and its Origin, the
    scroll position of its upper left corner, which starts at (0, 0)."""

    def __init__(self, panel: PanelValues, name: str, objects: list[GObject]) -> None:
        self.panel = panel
        self.name = name
        self._objects = objects
        self._origin = (0, 0)
        for obj in objects:
            obj._owner = self

    @property
    def owner(self) -> Any:
        """The Owner of a pane: the front panel, which owns every object it
        contains."""
        return self.panel

    @property
    def controls(self) -> list[Control]:
        """The Controls[] of the pane: a reference to each control and indicator on
        it, in the order the file stores them (first = topmost)."""
        return [obj for obj in self._objects if isinstance(obj, Control)]

    @property
    def origin(self) -> tuple[int, int]:
        """The Origin of the pane: ``(horizontal, vertical)``."""
        return self._origin

    @origin.setter
    def origin(self, value: Sequence[int]) -> None:
        horizontal, vertical = value
        self._origin = (horizontal, vertical)
        self.panel._tell("geometry_changed", self.name, "origin", self._origin)


class PanelLayout:
    """The objects of a panel (``layout`` of ``Panel``): its panes, with the
    controls and decorations on them in the order the file stores them."""

    _panes: list[Pane] | None = None
    _all_decorations: list[Decoration]

    @property
    def owner(self) -> None:
        """The Owner of the panel: it is the top-level object, so Not a Refnum."""
        return None

    def _build_layout(self, layout: Mapping[str, Any] | None) -> None:
        self._all_decorations = []
        if layout is None:
            return
        panes = []
        for index, pane_items in enumerate(layout["panes"]):
            objects: list[GObject] = []
            for item in pane_items:
                if item[0] == "c":
                    control = self._refs.get(item[1])
                    if control is not None:
                        control._rect = tuple(item[2:6])
                        objects.append(control)
                else:
                    decoration = Decoration(
                        self,
                        f"decoration {len(self._all_decorations)}",
                        tuple(item[1:5]),
                        item[5],
                    )
                    self._all_decorations.append(decoration)
                    objects.append(decoration)
            panes.append(Pane(self, f"pane {index}", objects))
        self._panes = panes
        for name, spec in layout["clusters"].items():
            if name in self._refs:
                self._refs[name]._attach_cluster(spec)

    def _need_layout(self, what: str) -> list[Pane]:
        if self._panes is None:
            raise NotImplementedError(
                f"{what} of the panel: the file records no pane hierarchy for it"
            )
        return self._panes

    @property
    def panes(self) -> list[Pane]:
        """The Panes[] of the panel: a reference to each pane of its hierarchy, the
        same object each time."""
        return list(self._need_layout("Panes[]"))

    @property
    def decorations(self) -> list[Decoration]:
        """The Decorations[] of the panel: a reference to each decoration and free
        label on it, the same object each time."""
        self._need_layout("Decorations[]")
        return list(self._all_decorations)

    @property
    def all_objects(self) -> list[GObject]:
        """The All Objects[] of the panel: a reference to each control, indicator
        and decoration on it, pane by pane in the order the file stores them."""
        return [
            obj for pane in self._need_layout("All Objects[]") for obj in pane._objects
        ]
'''
