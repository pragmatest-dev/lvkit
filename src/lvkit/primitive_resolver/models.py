"""Pydantic models for primitive JSON entries and resolved-primitive results."""

from __future__ import annotations

from pydantic import BaseModel, Field

from lvkit.models import ScalarValue, TerminalDirection
from lvkit.terminal_default import TerminalDefault


class PrimitiveTerminal(TerminalDefault):
    """A terminal on a primitive."""

    index: int
    direction: TerminalDirection
    # NI's label for the terminal, as LabVIEW shows it. Generated code names the
    # terminal from it (``codegen/catalog_names.py``).
    name: str | None = ""
    # The Python name generated code gives the terminal, when it is not the one
    # derived from the label (``label_names.catalog_python_label``).
    python_name: str | None = None
    # LabVIEW type: "numeric", "string", "boolean", "array",
    # "cluster", "path", "refnum", "variant", "polymorphic"
    type: str | None = None
    expandable: bool = False  # True if this terminal can be resized (N instances)
    # Index of the array terminal whose dimension count sets how many
    # consecutive terminals this slot occupies (one per dimension).
    per_dimension_of: int | None = None
    # Terminals sharing a group name are one set that repeats on a resized
    # node (see graph/terminal_layout.py).
    group: str | None = None
    # Named DCO reference tag in XML (e.g., "srcDCO4", "delDCO")
    dco_ref: str | None = None
    # The value NI documents for this output when the node's operation fails
    # (``codegen/native_failure.py``); none: the type's default.
    on_failure: ScalarValue = None
    # NI's page says that when an error cluster is wired to this input, only
    # the error cluster's status passes to it.
    error_cluster_status: bool = False


class NodeIcon(BaseModel):
    """An optional, declarative node glyph for the renderer.

    Backward-compatible: entries without an ``icon`` field simply resolve
    to ``None`` and the renderer falls through to the next resolver in its
    chain (see ``render/nodes.py``). Exactly one of ``svg``/``file`` is
    expected to be set — ``svg`` is an inline fragment (drawn in its own
    local coordinate space sized by ``size``, then scaled to the node's
    heap bounds); ``file`` is a path under ``data/glyphs/`` (resolved
    relative to ``src/lvkit/data/``).
    """

    svg: str | None = None
    file: str | None = None
    size: tuple[int, int] | None = None


class TerminalAlias(BaseModel):
    """One terminal of an alternative numbering: LabVIEW versions can number
    the same terminal differently. ``index`` and ``direction`` are the number
    and direction a node of that numbering carries; ``terminal`` is the index
    of the entry's own (primary) terminal it stands for."""

    index: int
    direction: TerminalDirection
    terminal: int


class NativeError(BaseModel):
    """One way a catalog node's Python operation can fail, and the LabVIEW error
    code that failure is: the Python exception class ``exception``, imported
    from ``module`` (``None`` for a built-in one), and the code from NI's public
    error-code list (``None`` for the generic code). ``since_python`` is the
    Python version the module has the class from, when it is not every version
    lvkit's generated code runs on. ``base`` makes ``exception`` a class of the
    operation's own, which only its generated code raises: the generated module
    defines it as a subclass of the built-in ``base``."""

    exception: str
    module: str | None = None
    code: int | None = None
    since_python: str | None = None
    base: str | None = None


class PrimitiveEntry(BaseModel):
    """A primitive entry from JSON."""

    name: str = ""
    # Neutral semantic-operation tag (e.g. "STRING_SUBSET"). Language-agnostic:
    # each code generator maps this op to its own emission (see
    # codegen/nodes/ops/). Lets the target-language translation live in the
    # generator instead of as a Python string baked into this data file.
    op: str | None = None
    python_code: str | dict[str, str] | None = None
    inline: bool = True
    terminals: list[PrimitiveTerminal] = Field(default_factory=list)
    # Other numberings of the same terminals (see TerminalAlias); names, types
    # and code come from ``terminals`` only.
    alternative_terminal_sets: list[list[TerminalAlias]] = Field(
        default_factory=list
    )
    guess_reason: str | None = None
    imports: list[str] = Field(default_factory=list)
    # Standard third-party packages (by their PyPI name) the node's generated
    # code imports because Python has no construct of its own for the function;
    # the generated package lists them in ``requirements.txt``.
    requires: list[str] = Field(default_factory=list)
    icon: NodeIcon | None = None
    # NI's page says the node runs even when an error comes in on its error in.
    runs_on_error: bool = False
    # The failures of the node's Python operation that are its error out, in
    # the order they are caught (``codegen/native_failure.py``).
    errors: list[NativeError] = Field(default_factory=list)


class ResolvedPrimitive(BaseModel):
    """Resolved primitive with full info."""

    prim_id: str | None = None
    name: str = ""
    # Neutral semantic-operation tag from the data file (see PrimitiveEntry.op).
    # When set and no ``python_code`` is present, codegen asks the per-language
    # op registry (codegen/nodes/ops/) to produce the template.
    op: str | None = None
    python_code: str | dict[str, str] | None = None
    # Alternate template used when the wired operands are INTEGERS — LabVIEW's
    # boolean-logic prims (And/Or) are bitwise on ints. Codegen selects this over
    # ``python_code`` when an input carries an integer type. (Not/negations need
    # width-masking, so they're handled in codegen, not here.)
    python_code_int: str | dict[str, str] | None = None
    inline: bool = True
    terminals: list[PrimitiveTerminal] = Field(default_factory=list)
    confidence: str = "unknown"
    # Set when the entry has several numberings and the node's terminals fit
    # none of them, or more than one; ``terminals`` is then empty.
    terminal_set_gap: str | None = None
    description: str = ""
    imports: list[str] = Field(default_factory=list)
    # LabVIEW numeric primitives operate element-wise on arrays. When True,
    # codegen broadcasts the operation over list operands.
    elementwise: bool = False
    # Optional declarative render glyph (render/nodes.py::JsonGlyphResolver).
    icon: NodeIcon | None = None
    # Public NI docs URL (labview-api-ref bundle) — surfaced in the rendered
    # SVG's hover tooltip (task #67). None when the primitive has no page.
    doc_url: str | None = None
    # NI's page says the node runs even when an error comes in on its error in
    # (``CodeFragment.runs_on_error``).
    runs_on_error: bool = False
    # NI's page says every input of the node takes only the status of an error
    # cluster wired to it: a node whose inputs grow with it and have no catalog
    # terminals (Compound Arithmetic). A fixed input says it on its own terminal
    # (``PrimitiveTerminal.error_cluster_status``).
    inputs_error_cluster_status: bool = False


def _collect_icon(prim: dict) -> NodeIcon | None:
    """Parse the optional ``icon`` field of a primitive JSON entry.

    Absent on (nearly) every entry today — tolerated, not required, so
    existing data files never need updating.
    """
    icon = prim.get("icon")
    if not icon:
        return None
    return NodeIcon.model_validate(icon)


def _collect_imports(prim: dict) -> list[str]:
    """Collect imports from both 'imports' (list) and '_import' (string) fields."""
    imports = list(prim.get("imports", []))
    imp = prim.get("_import")
    if isinstance(imp, str):
        imports.append(imp)
    elif isinstance(imp, list):
        imports.extend(imp)
    return imports
