"""Graph -> netlist text projection -- FACADE.

LabVIEW is the *schematic* view of code; this module (together with the
modules it re-exports from) adds the *netlist* -- the text form a schematic
always has in EDA. Prior art deliberately copied: structural HDL netlists
(named signals + instances + nested scopes) and compiler SSA/IR (named
values). See ``.tmp/netlist-spec.md`` for the full design contract (syntax
is LOCKED).

ONE canonical projection, three consumers::

    graph (truth)  ->  NetlistModule (IR)  ->  { text render, diff render, viewer }

This module itself contains no logic -- it is a pure re-export point kept
for backward compatibility, so every existing ``from ...graph.netlist
import X`` call site keeps working unchanged while the actual code lives in
focused modules:

- ``netlist_build.py`` -- builds the IR (``build_netlist_from_graph``,
  ``index_module``).
- ``netlist_models.py`` -- the IR dataclasses/enums.
- ``netlist_diff_helpers.py`` -- shared netlist-diff text helpers
  (``ambiguous_bares``/``instance_line``/``scope_header``).
- ``netlist_json.py`` -- the JSON serializer (``netlist_to_dict``).
- ``render_lvnet.py`` -- the current verbose ``lvnet`` text renderer
  (``render_lvnet``).

The IR also carries ``NetlistModule.components`` -- the Verilog-module /
VHDL-entity half of the netlist: every distinct subVI/primitive's typed
port interface, declared once, alongside ``body``'s per-call
instantiations. ``describe.py`` renders it as ``## Components``.
"""

from __future__ import annotations

# The IR-building code (``build_netlist_from_graph``, ``index_module``, and
# every private helper) lives in ``netlist_build.py`` (kept there so the
# graph-walk machinery is importable/editable without pulling in this facade).
# Re-exported here so every existing ``from ...graph.netlist import
# build_netlist_from_graph`` (etc.) call site keeps working unchanged.
# ``netlist_build.py`` must never import from this module (that would be a
# fragile, import-order-dependent circular import).
from .netlist_build import (
    build_netlist_from_graph as build_netlist_from_graph,  # noqa: F401 -- re-exported for external callers
)
from .netlist_build import (
    index_module as index_module,  # noqa: F401 -- re-exported for external callers
)

# ``ambiguous_bares``/``instance_line``/``scope_header`` -- the shared netlist
# helpers ``diff.py`` imports and calls -- live in ``netlist_diff_helpers.py``
# (which must never import from this module, to avoid a fragile
# import-order-dependent cycle). Re-exported here so every existing ``from
# ...graph.netlist import ambiguous_bares`` (etc.) call site keeps working
# unchanged.
from .netlist_diff_helpers import (
    ambiguous_bares as ambiguous_bares,  # noqa: F401 -- re-exported for external callers
)
from .netlist_diff_helpers import (
    instance_line as instance_line,  # noqa: F401 -- re-exported for external callers
)
from .netlist_diff_helpers import (
    scope_header as scope_header,  # noqa: F401 -- re-exported for external callers
)

# The JSON serializer (``netlist_to_dict``) lives in ``netlist_json.py`` (kept
# there so the self-contained to_dict cluster is importable without pulling
# in the build/render machinery here). Re-exported so existing ``from
# ...graph.netlist import netlist_to_dict`` call sites keep working unchanged.
from .netlist_json import (
    netlist_to_dict as netlist_to_dict,  # noqa: F401 -- re-exported for external callers
)

# ============================================================
# The IR
# ============================================================
#
# The IR dataclasses/enums live in ``netlist_models.py`` (kept there so the
# data model can be imported without pulling in the build/render machinery
# below). Re-exported here so existing ``from ...graph.netlist import
# NetlistModule`` (etc.) call sites keep working unchanged.
from .netlist_models import (
    BoundaryOutput as BoundaryOutput,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    ConnectorPane as ConnectorPane,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    ConnectorPaneTerminal as ConnectorPaneTerminal,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    DefaultValue as DefaultValue,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    DependencyKind as DependencyKind,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    EtaMerge as EtaMerge,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    GammaCase as GammaCase,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    GammaMerge as GammaMerge,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    MuMerge as MuMerge,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistBoundaryInput as NetlistBoundaryInput,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistComponent as NetlistComponent,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistConstant as NetlistConstant,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistDependency as NetlistDependency,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistFeedback as NetlistFeedback,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistFrame as NetlistFrame,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistInstance as NetlistInstance,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistInstanceKind as NetlistInstanceKind,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistItem as NetlistItem,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistModule as NetlistModule,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistOutput as NetlistOutput,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistPropertyAccess as NetlistPropertyAccess,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistScope as NetlistScope,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistTerminalBinding as NetlistTerminalBinding,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetlistTunnelInfo as NetlistTunnelInfo,  # noqa: F401 -- re-exported for external callers
)
from .netlist_models import (
    NetRef as NetRef,  # noqa: F401 -- re-exported for external callers
)
from .render_lvnet import (
    render_lvnet as render_lvnet,  # noqa: F401 -- re-exported for external callers
)
