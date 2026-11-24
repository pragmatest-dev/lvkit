"""The ``TreeItems`` class of the generated ``panels.py``: a Tree control's items.

A Tree control keeps a list of items in display order, each with a unique tag, an
indent level, the text of each cell and the state the properties of the Active Item
change. The class is written once into the package's ``panels.py`` (it imports
nothing from lvkit) and a ``Control`` of a Tree holds one as ``control.tree``,
started from what the VI file saves (``parser/fp_starting_properties.py``: the
items, their tags and indent levels, the cell widths and the active column). Every
property and method of a Tree reference is a member of it
(``control_properties.json``: the properties whose ``holder`` is ``tree``;
``codegen/nodes/tree_methods.py``: the methods), as
``as_control(reference).tree.<member>``.

A parent is the nearest item above with a smaller indent level, so the list is the
tree: an item's children follow it with a greater indent level. The text of an item
is the list of its cells, the leftmost first.

NI's page for each member is quoted in ``TREE_ITEMS_SOURCE``'s comments; the errors
are NI's (General LabVIEW Error Codes,
``docs-be.ni.com/api/bundle/labview-api-ref/page/errors/general-labview-error-codes.html``):
1135 "The tree control's active item is not valid for this property.", 1136 "You wired
an invalid item tag to a tree control property or method." and 1146 "You attempted an
operation that would change a child-only item into a parent item."
"""

from __future__ import annotations

TREE_ITEMS_NAME = "TreeItems"

TREE_ITEMS_SOURCE = '''
TREE_ALL_ROWS = "TREE_ALL_ROWS"
TREE_COLUMN_HEADERS = "TREE_COLUMN_HEADERS"
_TREE_RESERVED = "TREE_"


class _TreeItem:
    """One item of a tree: its tag, indent level, the text of each cell (leftmost
    first) and the state the Active Item properties read and write."""

    def __init__(
        self,
        tag: str,
        indent: int,
        texts: Sequence[str] = (),
        child_only: bool = False,
        symbol: int = 0,
    ) -> None:
        self.tag = tag
        self.indent = indent
        self.texts = list(texts)
        self.open = False
        self.child_only = child_only
        self.symbol = symbol


def _tree_error(code: int, source: str, message: str) -> PanelError:
    return PanelError(code=code, source=source, message=message)


class TreeItems:
    """The items of a Tree control. ``items`` are the ``(tag, indent, texts)`` the
    file saves, ``widths`` its ``(column, width)`` cell sizes (column -2 the size of
    every column), ``column`` the active column the file saves. The Active Item is
    the item later Active Item and Active Cell properties apply to: the item whose
    tag was last written, else the control's value (the selected tag). The state of
    a tree with no control (the default Tree object) is its own."""

    def __init__(
        self,
        control: Any = None,
        items: Sequence[Any] = (),
        widths: Sequence[Any] = (),
        column: int = 0,
    ) -> None:
        self._control = control
        self._items = [_TreeItem(tag, indent, texts) for tag, indent, texts in items]
        self._widths: dict[int, int] = {int(c): int(w) for c, w in widths}
        self._headers: dict[int, str] = {}
        self._active: str | None = None
        self._column = int(column)
        self.symbols: list[Any] | None = None

    # ---- the structure of the list -------------------------------------------------

    def _changed(self) -> None:
        """Tell the views of the panel the items changed."""
        if self._control is not None:
            self._control.panel.set_property(
                self._control.name,
                "tree_items",
                [[i.tag, i.indent, list(i.texts), i.open] for i in self._items],
            )

    def _index(self, tag: str) -> int:
        """The position of the item with ``tag``; error 1136 for a tag no item has
        (the empty tag included)."""
        for position, item in enumerate(self._items):
            if item.tag == tag and tag != "":
                return position
        raise _tree_error(
            1136,
            "TreeItems",
            f"You wired an invalid item tag to a tree control property or method: "
            f"{tag!r}.",
        )

    def _parent(self, position: int) -> int | None:
        """The position of the parent of the item at ``position``: the nearest item
        above with a smaller indent level; None for a top-level item."""
        indent = self._items[position].indent
        for above in range(position - 1, -1, -1):
            if self._items[above].indent < indent:
                return above
        return None

    def _end(self, position: int) -> int:
        """The position after the item at ``position`` and its descendants."""
        indent = self._items[position].indent
        end = position + 1
        while end < len(self._items) and self._items[end].indent > indent:
            end += 1
        return end

    def _children(self, parent: int | None) -> list[int]:
        """The positions of the children of the item at ``parent`` (None: the top
        level), in order."""
        start = 0 if parent is None else parent + 1
        stop = len(self._items) if parent is None else self._end(parent)
        return [p for p in range(start, stop) if self._parent(p) == parent]

    def _clean_tag(self, tag: str) -> str:
        """NI (Add Item, Set Tag): "You cannot create a tag that begins with TREE_ ...
        If the tag you wire to the method begins with TREE_, LabVIEW prepends an
        underscore (_) ... If the tag you wire to the method begins or ends with white
        space or non-printing characters, such as a tab or new line, LabVIEW removes
        the white space or non-printing characters." """
        tag = tag.strip()
        return "_" + tag if tag.startswith(_TREE_RESERVED) else tag

    def _unique_tag(self, tag: str) -> str:
        """NI (Add Item, Child Tag): "If an existing tag already uses that string,
        LabVIEW appends a number to the string to create a unique tag for the new
        item." (NI gives no numbering: the first number, from 1, that makes it
        unique; the empty tag is the root level's, so it takes a number too.)"""
        tags = {item.tag for item in self._items}
        if tag and tag not in tags:
            return tag
        number = 1
        while f"{tag}{number}" in tags:
            number += 1
        return f"{tag}{number}"

    def _insertion(self, parent_tag: str, position: int) -> tuple[int, int, int | None]:
        """Where a new or moved item goes: ``(list position, indent level, parent)``
        for the child ``position`` of ``parent_tag``. NI (Add Item, Child Position):
        "Wire a value of 0 (default) to place the new item directly under the parent
        item. Wire a value of 1 to place the new item under the first child item, a
        value of 2 ... under the second child item, and so on. Wire a value of -1 to
        place the new item under the last child item. If the value you wire to this
        input is greater than the number of child items under the parent item, the
        method places the new item under the last child item." Under a child is after
        the child and its descendants."""
        parent = None if parent_tag == "" else self._index(parent_tag)
        if parent is not None and self._items[parent].child_only:
            raise _tree_error(
                1146,
                "TreeItems",
                "You attempted an operation that would change a child-only item "
                "into a parent item.",
            )
        children = self._children(parent)
        indent = 0 if parent is None else self._items[parent].indent + 1
        if position == 0 or not children:
            return (0 if parent is None else parent + 1), indent, parent
        last = len(children) if position < 0 else min(position, len(children))
        return self._end(children[last - 1]), indent, parent

    # ---- Edit Tree Items -----------------------------------------------------------

    def add_item(
        self,
        parent_tag: str = "",
        position: int = 0,
        left: str = "",
        child_text: Sequence[str] = (),
        child_tag: str | None = None,
        child_only: bool = False,
    ) -> str:
        """Edit Tree Items:Add Item: add an item and give its unique tag (NI: "This
        method returns a unique tag for the new item."; Child Tag "The default is the
        string you wire to Left Cell String")."""
        place, indent, _ = self._insertion(parent_tag, position)
        wanted = left if child_tag is None else child_tag
        tag = self._unique_tag(self._clean_tag(wanted))
        self._items.insert(
            place, _TreeItem(tag, indent, [left, *child_text], child_only)
        )
        self._changed()
        return tag

    def add_items_to_end(self, items: Sequence[Any] = ()) -> None:
        """Edit Tree Items:Add Multiple Items to End: each item is the cluster
        ``(Left Cell String, Child Text, Child Tag, Item Indent, Child Only?, Glyph
        Index)``. NI: "This method returns an error if you set an item to an indent
        level that has no parent item." (error 1135, the active item not valid: NI
        gives no code); an item under a child-only item is error 1146."""
        for item in items:
            if not isinstance(item, tuple):
                item = tuple(getattr(item, f.name) for f in dataclasses.fields(item))
            left, texts, tag, indent, child_only, glyph = (
                tuple(item) + ("", (), "", 0, False, 0)[len(item) :]
            )
            above = self._items[-1] if self._items else None
            if indent < 0 or indent > (0 if above is None else above.indent + 1):
                raise _tree_error(
                    1135,
                    "TreeItems.add_items_to_end",
                    "The tree control's active item is not valid for this "
                    f"property: indent level {indent} has no parent item.",
                )
            if above is not None and indent > above.indent and above.child_only:
                raise _tree_error(
                    1146,
                    "TreeItems.add_items_to_end",
                    "You attempted an operation that would change a child-only item "
                    "into a parent item.",
                )
            tag = self._unique_tag(self._clean_tag(tag or left))
            self._items.append(
                _TreeItem(tag, indent, [left, *texts], child_only, glyph)
            )
        self._changed()

    def delete_item(self, tag: str = "", delete_children: bool = True) -> None:
        """Edit Tree Items:Delete Item. NI: "Wire an empty string to tag and a value
        of TRUE to Delete Children to delete all items in the tree control."; Delete
        Children "If TRUE (default), the method deletes all the child items under the
        item you want to delete. If FALSE, the method moves all the child items to
        level in the hierarchy of the item you want to delete before deleting the
        item." """
        if tag == "":
            if delete_children:
                self._items.clear()
                self._active = None
        else:
            position = self._index(tag)
            if delete_children:
                del self._items[position : self._end(position)]
            else:
                for child in self._items[position + 1 : self._end(position)]:
                    child.indent -= 1
                del self._items[position]
        self._changed()

    def move_item(
        self,
        tag: str = "",
        new_parent: str = "",
        position: int = 0,
        carry_children: bool = False,
    ) -> None:
        """Edit Tree Items:Move Item. NI: New Position "Wire a value of 0 (default) to
        place the item above the first child item, wire a value of 1 to place the
        item above the second child item, and so on. Wire a value of -1 to place the
        item under the last child item. If the value ... is greater than the number
        of child items under the parent item, the method places the item under the
        last child item."; Carry Children? "If TRUE, the method moves any child items
        with the item you want to move. If FALSE (default), the method moves the
        child items under the parent item of the item you want to move." """
        start = self._index(tag)
        end = self._end(start)
        inside = (
            new_parent != "" and start < self._index(new_parent) < end
        ) and carry_children
        if new_parent == tag or inside:
            raise _tree_error(
                1136,
                "TreeItems.move_item",
                "You wired an invalid item tag to a tree control property or method: "
                f"{new_parent!r} is under {tag!r}.",
            )
        moved = self._items[start:end] if carry_children else [self._items[start]]
        stay = [] if carry_children else self._items[start + 1 : end]
        for child in stay:
            child.indent -= 1
        old = self._items[start].indent
        del self._items[start:end]
        self._items[start:start] = stay
        parent = None if new_parent == "" else self._index(new_parent)
        indent = 0 if parent is None else self._items[parent].indent + 1
        children = self._children(parent)
        if self._items and parent is not None and self._items[parent].child_only:
            raise _tree_error(
                1146,
                "TreeItems.move_item",
                "You attempted an operation that would change a child-only item "
                "into a parent item.",
            )
        if position == 0 or not children:
            place = 0 if parent is None else parent + 1
        elif position < 0 or position >= len(children):
            place = self._end(children[-1])
        else:
            place = children[position]
        for item in moved:
            item.indent += indent - old
        self._items[place:place] = moved
        self._changed()

    def set_tag(self, tag: str = "", new_tag: str = "") -> str:
        """Edit Tree Items:Set Tag: give the item ``tag`` the tag ``new_tag`` (cleaned
        as NI says, see ``_clean_tag``) and give the tag it has. A tag another item
        has, or the empty tag, is an invalid item tag (error 1136)."""
        position = self._index(tag)
        cleaned = self._clean_tag(new_tag)
        if cleaned != tag and (
            cleaned == "" or any(item.tag == cleaned for item in self._items)
        ):
            raise _tree_error(
                1136,
                "TreeItems.set_tag",
                "You wired an invalid item tag to a tree control property or method: "
                f"{new_tag!r} is not a unique tag.",
            )
        self._items[position].tag = cleaned
        if self._active == tag:
            self._active = cleaned
        self._changed()
        return cleaned

    def sort_children(
        self,
        tag: str = "",
        column: int = 0,
        case: int = 0,
        recursive: bool = False,
        reverse: bool = False,
    ) -> None:
        """Edit Tree Items:Sort Children: sort the children of ``tag`` by the text of
        ``column``. NI: Case Sensitivity "0 System Default (default) 1 Case Sensitive
        2 Case Insensitive"; the system default is the collation of the locale the
        program runs in (``locale.strxfrm``)."""
        keys = {
            0: locale.strxfrm,
            1: lambda text: text,
            2: str.casefold,
        }
        key = keys.get(case)
        if key is None:
            raise _tree_error(
                1136,
                "TreeItems.sort_children",
                f"Case Sensitivity {case} is not 0 System Default, 1 Case Sensitive "
                "or 2 Case Insensitive.",
            )
        parent = None if tag == "" else self._index(tag)
        self._sort(parent, column, key, recursive, reverse)
        self._changed()

    def _sort(
        self,
        parent: int | None,
        column: int,
        key: Callable[[str], Any],
        recursive: bool,
        reverse: bool,
    ) -> None:
        children = self._children(parent)
        blocks = [self._items[p : self._end(p)] for p in children]
        blocks.sort(
            key=lambda block: key(
                block[0].texts[column] if column < len(block[0].texts) else ""
            ),
            reverse=reverse,
        )
        if children:
            start, stop = children[0], self._end(children[-1])
            self._items[start:stop] = [item for block in blocks for item in block]
        if recursive:
            for child in self._children(parent):
                self._sort(child, column, key, recursive, reverse)

    # ---- Navigate Tree -------------------------------------------------------------

    def get_child(self, tag: str = "") -> str:
        """Navigate Tree:Get Child: the tag of the first child of ``tag`` (NI: "If the
        item has no child items, the method returns an empty string."; Tag "The
        default is an empty string, which returns the topmost item in the tree
        control. If the tree control contains no items, the method returns an empty
        string.")."""
        if tag == "":
            return self._items[0].tag if self._items else ""
        children = self._children(self._index(tag))
        return self._items[children[0]].tag if children else ""

    def get_next(self, tag: str = "") -> str:
        """Navigate Tree:Get Next Item: the tag of the next item at the same level
        (NI: "If no items exist after the item at the same level in the hierarchy,
        the method returns an empty string."; the default tag returns the topmost
        item)."""
        if tag == "":
            return self._items[0].tag if self._items else ""
        position = self._index(tag)
        siblings = self._children(self._parent(position))
        later = [p for p in siblings if p > position]
        return self._items[later[0]].tag if later else ""

    def get_parent(self, tag: str = "") -> str:
        """Navigate Tree:Get Parent Item: the tag of the parent (NI: "If the item is
        the topmost item in the tree control, the method returns an empty string.";
        "If the tree control contains no items, the method returns an error.")."""
        parent = self._parent(self._index(tag))
        return "" if parent is None else self._items[parent].tag

    def get_path(self, tag: str = "") -> list[str]:
        """Navigate Tree:Get Path: the tags from the top level to the item, the item
        last (NI: "If the tree control contains no items, the method returns an empty
        array.")."""
        if not self._items:
            return []
        path: list[str] = []
        position: int | None = self._index(tag)
        while position is not None:
            path.append(self._items[position].tag)
            position = self._parent(position)
        return path[::-1]

    # ---- Open/Close ----------------------------------------------------------------

    def set_open(self, tag: str = "", opened: bool = False) -> None:
        """Open/Close:Item: expand (``opened``) or close the item (NI: "If the tree
        control contains no items, the method returns an error.", error 1136)."""
        self._items[self._index(tag)].open = opened
        self._changed()

    def open_all(self, opened: bool = False) -> None:
        """Open/Close:All: expand or close every item."""
        for item in self._items:
            item.open = opened
        self._changed()

    def ensure_visible(self, tag: str = "") -> None:
        """Open/Close:Ensure Visible: expand the items above ``tag`` (NI: "Scrolls to
        an item and if necessary, expands nodes in the tree control to make the item
        visible."); the scroll is the view's."""
        position = self._parent(self._index(tag))
        while position is not None:
            self._items[position].open = True
            position = self._parent(position)
        self._changed()
        if self._control is not None:
            self._control.panel.set_property(self._control.name, "tree_visible", tag)

    # ---- Custom Item Symbols -------------------------------------------------------

    def set_symbol_array(self, symbols: Sequence[Any] = ()) -> None:
        """Custom Item Symbols:Set To Custom Symbol Array: hold the pictures (NI:
        "Sets the images of all item symbols."); a view draws them."""
        self.symbols = list(symbols)
        self._changed()

    def revert_symbols(self) -> None:
        """Custom Item Symbols:Revert All To Built In Symbols: "Resets all item
        symbols to the default symbols." """
        self.symbols = None
        self._changed()

    # ---- Active Item and Active Cell -----------------------------------------------

    def _invalid_active(self, prop: str) -> PanelError:
        return _tree_error(
            1135,
            f"TreeItems.{prop}",
            "The tree control's active item is not valid for this property.",
        )

    @property
    def active_item_tag(self) -> str:
        """Active Item:Tag: NI "The tag of the item(s) to which you want to apply
        subsequent property changes ... TREE_COLUMN_HEADERS Selects all column
        headers. TREE_ALL_ROWS Selects all rows. Wire the tag of a specific item to
        select a specific item." Before a write it is the control's value."""
        if self._active is not None:
            return self._active
        if self._control is None:
            return ""
        return str(self._control.value or "")

    @active_item_tag.setter
    def active_item_tag(self, tag: str) -> None:
        if tag not in (TREE_ALL_ROWS, TREE_COLUMN_HEADERS):
            self._index(tag)
        self._active = tag

    def _active_position(self, prop: str, rows_ok: bool = False) -> int | None:
        """The position of the active item; None for all rows when ``rows_ok``; the
        error 1135 for the column headers, for all rows otherwise and for a tag that
        is no item."""
        tag = self.active_item_tag
        if tag == TREE_ALL_ROWS and rows_ok:
            return None
        if tag in (TREE_ALL_ROWS, TREE_COLUMN_HEADERS):
            raise self._invalid_active(prop)
        for position, item in enumerate(self._items):
            if item.tag == tag and tag != "":
                return position
        raise self._invalid_active(prop)

    @property
    def displayed_items(self) -> list[str]:
        """Displayed Items: NI "Returns the unique tags of all visible items in the
        tree control in display order. If an item is indented under another item and
        the parent item is not expanded, this property does not return the tags of
        any items indented under that parent item." """
        shown: list[str] = []
        closed: int | None = None
        for item in self._items:
            if closed is not None and item.indent > closed:
                continue
            closed = None if item.open else item.indent
            shown.append(item.tag)
        return shown

    @property
    def active_item_row_number(self) -> int:
        """Active Item:Row Number: NI "Wire a value of -1 to this property to select
        the column headers. Wire a value of -2 to select all rows. When you read this
        property, this property returns the row number of the active item ... This
        property returns an error if you specify an invalid row number." The row is
        the item's place in ``displayed_items``."""
        tag = self.active_item_tag
        if tag == TREE_ALL_ROWS:
            return -2
        if tag == TREE_COLUMN_HEADERS:
            return -1
        shown = self.displayed_items
        if tag not in shown:
            raise self._invalid_active("active_item_row_number")
        return shown.index(tag)

    @active_item_row_number.setter
    def active_item_row_number(self, row: int) -> None:
        shown = self.displayed_items
        if row == -2:
            self._active = TREE_ALL_ROWS
        elif row == -1:
            self._active = TREE_COLUMN_HEADERS
        elif 0 <= row < len(shown):
            self._active = shown[row]
        else:
            raise self._invalid_active("active_item_row_number")

    @property
    def active_open(self) -> bool:
        """Active Item:Open?: NI "If TRUE, the active item(s) appears expanded ... If
        the active item is the column headers or all rows, this property returns an
        error." An item the file does not save open is closed (FALSE)."""
        position = self._active_position("active_open")
        assert position is not None
        return self._items[position].open

    @active_open.setter
    def active_open(self, opened: bool) -> None:
        position = self._active_position("active_open")
        assert position is not None
        self._items[position].open = bool(opened)
        self._changed()

    @property
    def indent_level(self) -> int:
        """Active Item:Indent Level: NI "The level at which the active item(s) appears
        in the hierarchy. Wire a value of 0 to this property to move the item to the
        highest level in the hierarchy ... If you wire a value to this property that
        is more than one level greater than the indent level of the item above the
        item you want to indent, this property returns an error." (1135: the item is
        not valid for the level.)"""
        position = self._active_position("indent_level")
        assert position is not None
        return self._items[position].indent

    @indent_level.setter
    def indent_level(self, level: int) -> None:
        position = self._active_position("indent_level")
        assert position is not None
        above = self._items[position - 1] if position else None
        if level < 0 or level > (0 if above is None else above.indent + 1):
            raise self._invalid_active("indent_level")
        if above is not None and level > above.indent and above.child_only:
            raise _tree_error(
                1146,
                "TreeItems.indent_level",
                "You attempted an operation that would change a child-only item "
                "into a parent item.",
            )
        self._items[position].indent = int(level)
        self._changed()

    @property
    def child_only(self) -> bool:
        """Active Item:Child-Only?: NI "If TRUE, you cannot indent other items under
        the active item(s). If the active item is the column headers or if the active
        item has other items indented under it, this property returns an error. If the
        active item is all rows, the property returns an error." (1135)"""
        position = self._active_position("child_only")
        assert position is not None
        return self._items[position].child_only

    @child_only.setter
    def child_only(self, value: bool) -> None:
        position = self._active_position("child_only")
        assert position is not None
        if value and self._end(position) > position + 1:
            raise self._invalid_active("child_only")
        self._items[position].child_only = bool(value)
        self._changed()

    @property
    def symbol_index(self) -> int:
        """Active Item:Symbol Index: NI "The index of the symbol for the active
        item(s) ... If the active item is the column headers, this property returns an
        error. If the active item is all rows and you read this property, the property
        returns an error." Writing it with all rows sets every item."""
        position = self._active_position("symbol_index")
        assert position is not None
        return self._items[position].symbol

    @symbol_index.setter
    def symbol_index(self, index: int) -> None:
        position = self._active_position("symbol_index", rows_ok=True)
        for item in self._items if position is None else [self._items[position]]:
            item.symbol = int(index)
        self._changed()

    @property
    def active_column_number(self) -> int:
        """Active Cell:Active Column Number: NI "-2 Selects all columns. -1 Selects
        all column headers. 0 Selects the first column." The file saves it."""
        return self._column

    @active_column_number.setter
    def active_column_number(self, column: int) -> None:
        self._column = int(column)

    @property
    def cell_string(self) -> str:
        """Active Cell:String: NI "The string in the active cell(s). If the active cell
        is all columns or all rows, this property returns an error." (1135) A column
        header is the string of the column's header."""
        if self._column == -2 or self.active_item_tag == TREE_ALL_ROWS:
            raise self._invalid_active("cell_string")
        if self._column == -1 or self.active_item_tag == TREE_COLUMN_HEADERS:
            return self._headers.get(max(self._column, 0), "")
        position = self._active_position("cell_string")
        assert position is not None
        texts = self._items[position].texts
        return texts[self._column] if self._column < len(texts) else ""

    @cell_string.setter
    def cell_string(self, text: str) -> None:
        if self._column == -2 or self.active_item_tag == TREE_ALL_ROWS:
            raise self._invalid_active("cell_string")
        if self._column == -1 or self.active_item_tag == TREE_COLUMN_HEADERS:
            self._headers[max(self._column, 0)] = text
        else:
            position = self._active_position("cell_string")
            assert position is not None
            texts = self._items[position].texts
            texts.extend([""] * (self._column + 1 - len(texts)))
            texts[self._column] = text
        self._changed()

    @property
    def cell_width(self) -> int:
        """Active Cell:Cell Size:Width: "The width of the active cell(s) in pixels."
        A column the file saves no width for has the width of every column; a tree
        whose file saves neither has none to read before a write. All columns (-2)
        cannot be read (1135)."""
        if self._column == -2:
            raise self._invalid_active("cell_width")
        column = max(self._column, 0)
        if column in self._widths:
            return self._widths[column]
        if -2 in self._widths:
            return self._widths[-2]
        raise NotImplementedError(
            f"Cell Size:Width of column {column} of a tree read before any write: "
            "the file saves no width for it"
        )

    @cell_width.setter
    def cell_width(self, width: int) -> None:
        if self._column == -2:
            self._widths = {column: int(width) for column in {*self._widths, -2}}
        else:
            self._widths[max(self._column, 0)] = int(width)
        self._changed()
'''
