"""Python module for the named queues of a generated package.

Every Obtain Queue whose name is a constant refers to the one queue of that name.
The package gets a module, ``queues``, that defines each as a module-level
``queue.Queue``; a VI that obtains the queue imports the module and reads the
attribute, so every VI that names the same queue reaches the same object. A queue
whose name is computed at run time is an entry of the module's ``by_name`` dict.

The module is written from ``InMemoryVIGraph.obtain_queue_uses`` over the whole
package, with the queues in the order of their names, so it does not depend on the
order the VIs are generated in.

Releasing a queue shuts it down (``queue.ShutDown``), which needs Python 3.13.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable
from dataclasses import dataclass

from lvkit.graph.models import ConstantNode
from lvkit.graph.package_queries import ObtainQueueUse

from .ast_utils import to_var_name, unique_names
from .context import _format_constant
from .errors import CodeGenError

QUEUES_MODULE = "queues"

_DOCSTRING = (
    "Named queues shared by the package. Releasing a queue shuts it down "
    "(queue.ShutDown), which needs Python 3.13 or later."
)


@dataclass(frozen=True)
class QueueUse:
    """How one Obtain Queue node names its queue: ``name`` is the constant name
    ('' for none), ``computed`` is true for a name computed at run time, and
    ``attribute`` is the queue's name in the ``queues`` module (set only for a
    constant name). ``max_size`` is the queue's bound for a constant name (0 is
    unbounded). ``error`` is why the node cannot be generated."""

    name: str
    computed: bool
    attribute: str | None
    max_size: int | None
    created_used: bool
    error: str | None = None


def _constant_value(constant: ConstantNode) -> object:
    """The Python value of a constant, as generated code would write it."""
    try:
        return ast.literal_eval(_format_constant(constant))
    except (ValueError, SyntaxError):
        return None


@dataclass(frozen=True)
class PackageQueues:
    """The queues of one package, from its Obtain Queue nodes: each node's use,
    and the module that defines the named ones."""

    uses: dict[str, QueueUse]
    max_sizes: dict[str, int]
    attributes: dict[str, str]
    uses_by_name: bool
    uses_obtained: bool

    @classmethod
    def from_uses(cls, uses: Iterable[ObtainQueueUse]) -> PackageQueues:
        """Collect ``uses`` (all the Obtain Queue nodes of the package). Names are
        taken in sorted order, so each gets the same attribute whatever order the
        nodes come in."""
        parsed: list[tuple[ObtainQueueUse, str, int | None, str | None]] = []
        sizes: dict[str, set[int]] = {}
        for use in uses:
            name = ""
            error: str | None = None
            if use.name is not None and use.name.unwritable is not None:
                error = (
                    f"Obtain Queue has a name that holds {use.name.unwritable.reason()}"
                )
            elif use.name is not None:
                value = _constant_value(use.name)
                name = value if isinstance(value, str) else ""
            size: int | None = -1
            unwritable = use.max_size.unwritable if use.max_size is not None else None
            if use.max_size is not None and unwritable is None:
                value = _constant_value(use.max_size)
                size = value if isinstance(value, int) else None
            elif use.max_size_wired or unwritable is not None:
                size = None
            if name and size is None and error is None:
                error = (
                    f"Obtain Queue '{name}' has a maximum size "
                    + (
                        f"that holds {unwritable.reason()}"
                        if unwritable is not None
                        else "computed at run time"
                    )
                    + "; a named queue is defined once, with a constant size"
                )
            if name and size is not None:
                sizes.setdefault(name, set()).add(max(size, 0))
            parsed.append((use, name, size, error))
        conflicts = {name for name, found in sizes.items() if len(found) > 1}
        names = sorted(name for name in sizes if name not in conflicts)
        attributes = dict(
            zip(
                names,
                unique_names([to_var_name(name) for name in names]),
                strict=True,
            )
        )
        by_node: dict[str, QueueUse] = {}
        for use, name, size, error in parsed:
            if name in conflicts:
                error = (
                    f"Queue '{name}' is obtained with maximum sizes "
                    f"{sorted(sizes[name])}; a named queue is defined once, in the "
                    "queues module"
                )
            by_node[use.node_id] = QueueUse(
                name=name,
                computed=use.name_wired and use.name is None,
                attribute=attributes.get(name),
                max_size=None if size is None else max(size, 0),
                created_used=use.created_used,
                error=error,
            )
        return cls(
            uses=by_node,
            max_sizes={name: next(iter(sizes[name])) for name in names},
            attributes=attributes,
            uses_by_name=any(u.computed for u in by_node.values()),
            uses_obtained=any(
                u.created_used and u.attribute is not None for u in by_node.values()
            ),
        )

    def use_of(self, node_id: str, node: object = None) -> QueueUse:
        """The use recorded for the Obtain Queue node ``node_id``."""
        use = self.uses.get(node_id)
        if use is None:
            raise CodeGenError(
                "This Obtain Queue was not among the package's nodes, so its queue "
                "cannot be connected",
                node,  # type: ignore[arg-type]
            )
        return use

    def build_module(self) -> str | None:
        """Module source defining every named queue; None when there is none."""
        if not (self.attributes or self.uses_by_name or self.uses_obtained):
            return None
        body: list[ast.stmt] = [
            ast.Expr(value=ast.Constant(value=_DOCSTRING)),
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            ),
            ast.ImportFrom(module="queue", names=[ast.alias(name="Queue")], level=0),
        ]
        if self.uses_obtained:
            body.append(_annotated("obtained", "set[str]", "set()"))
        if self.uses_by_name:
            body.append(_annotated("by_name", "dict[str, Queue]", "{}"))
        for name, attribute in self.attributes.items():
            body.append(
                _annotated(attribute, "Queue", f"Queue(maxsize={self.max_sizes[name]})")
            )
        module = ast.Module(body=body, type_ignores=[])
        ast.fix_missing_locations(module)
        return ast.unparse(module)


def _annotated(name: str, annotation: str, value: str) -> ast.stmt:
    return ast.AnnAssign(
        target=ast.Name(id=name, ctx=ast.Store()),
        annotation=ast.parse(annotation, mode="eval").body,
        value=ast.parse(value, mode="eval").body,
        simple=1,
    )
