"""A vi.lib inline template's placeholders are read from the catalog template,
never from a value written into it: a string with braces of its own fed to
Trim Whitespace is that string, not a missing input. A tiny diagram is generated
and run."""

from __future__ import annotations

from tests.error_diagrams import (
    STR,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
)

NAME = "Trim.vi"


def _string_hex(text: str) -> str:
    data = text.encode()
    return f"{len(data):08X}{data.hex().upper()}"


def test_a_value_with_braces_is_written_as_it_is(project: Project) -> None:  # noqa: F811
    vi = Vi(NAME)
    trimmed = vi.indicator("trimmed", STR)
    node = vi.call(
        "Trim Whitespace.vi",
        [("string", 0, STR, False), ("trimmed string", 2, STR, True)],
        vilib="Utility/error.llb",
    )
    vi.wire(vi.constant(STR, _string_hex("  {error_message} {x}  ")), node["string"])
    vi.wire(node["trimmed string"], trimmed)
    project.add(vi)
    package = project.generate(NAME)
    assert "{error_message}" in package.source
    assert package.function().trimmed == "{error_message} {x}"
