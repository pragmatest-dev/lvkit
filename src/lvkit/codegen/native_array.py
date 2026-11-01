"""Plain-Python expressions for array functions, written for the array's rank.

Each function returns Python source text built from operand source text. The
operands are expressions without side effects, so a template may repeat them.
"""

from __future__ import annotations

import ast


def operand(text: str) -> str:
    """``text`` as an operand of a larger expression: parenthesized unless it is a
    name or a number."""
    return text if text.isidentifier() or text.isdigit() else f"({text})"


def guarded_index(array: str, index: str, default: str) -> str:
    """The element at ``index``, or ``default`` when the index is outside the
    array (Index Array returns the element type's default there; a bare
    subscript would raise)."""
    return f"({array}[{index}] if 0 <= {index} < len({array}) else {default})"


def dimension_sizes(array: str, rank: int) -> list[str]:
    """One expression per dimension of a ``rank``-dimensional array: the length
    along that axis, read down the first element of each outer axis (an empty
    axis counts 0)."""
    sizes = [f"len({array})"]
    path = array
    guards: list[str] = []
    for _ in range(rank - 1):
        guards.append(path)
        path = f"{path}[0]"
        sizes.append(f"(len({path}) if {' and '.join(guards)} else 0)")
    return sizes


def size_vector(array: str, rank: int) -> str:
    """The size of every dimension of a ``rank``-dimensional array: ``len`` for one
    dimension, else a list holding the length along each axis."""
    if rank <= 1:
        return f"len({array})"
    return "[" + ", ".join(dimension_sizes(array, rank)) + "]"


def _place(cell: str, new: str, offsets: list[str], depth: int) -> list[str]:
    """Lines that write ``new`` into ``cell`` starting at ``offsets[0]`` along its
    first dimension, then at the following offsets along the next dimensions; any
    part of ``new`` that does not fit is cropped."""
    first = offsets[0]
    if len(offsets) == 1:
        return [f"{cell}[{first}:{first} + len({new})] = {new}[:len({cell}) - {first}]"]
    index, row = f"_i{depth}", f"_row{depth}"
    lines = [f"for {index}, {row} in enumerate({new}[:len({cell}) - {first}]):"]
    inner = _place(f"{cell}[{first} + {index}]", row, offsets[1:], depth + 1)
    return lines + [f"    {line}" for line in inner]


def replace_subarray_statements(
    array: str, new: str, offsets: list[str], new_rank: int, target: str
) -> list[ast.stmt]:
    """Statements that bind ``target`` to a copy of ``array`` with ``new`` written
    at ``offsets`` (one start index per dimension of the array). ``new`` has
    ``new_rank`` dimensions and lands along the array's last dimensions; the
    leading offsets pick the cell it goes into. The array is unchanged; when an
    offset is negative or past the end of its dimension the copy is returned as it
    is."""
    offsets = [operand(offset) for offset in offsets]
    rank = len(offsets)
    lead = rank - new_rank
    guards = [
        f"0 <= {offset} < len({target}{'[0]' * dimension})"
        for dimension, offset in enumerate(offsets)
    ]
    cell = target + "".join(f"[{offset}]" for offset in offsets[:lead])
    if new_rank == 0:
        write = [f"{cell} = {new}"]
    else:
        write = _place(cell, new, offsets[lead:], 0)
    lines = [f"{target} = copy.deepcopy({array})", f"if {' and '.join(guards)}:"]
    lines += [f"    {line}" for line in write]
    return ast.parse("\n".join(lines)).body


def replaced_field(cluster: str, path: tuple[int, ...], value: str) -> str:
    """The tuple ``cluster`` with the field at ``path`` (an index per cluster
    level) replaced by ``value``; every other field is kept."""
    index, *tail = path
    if tail:
        value = replaced_field(f"{cluster}[{index}]", tuple(tail), value)
    return f"{cluster}[:{index}] + ({value},) + {cluster}[{index + 1}:]"
