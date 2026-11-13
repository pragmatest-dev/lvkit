"""The Python class name of a user-defined LabVIEW type.

One spelling for a type's class wherever generated code names it: in an
annotation, as the enum an enum constant is a member of, and in the module that
defines it.
"""

from __future__ import annotations

import keyword

from lvkit.python_names import name_characters, normalised, starts_a_name

_TYPE_EXTENSIONS = (".ctl", ".lvclass")

# LabVIEW's own root class: every class descends from it and it has no file.
ROOT_CLASS = "LabVIEW Object"


def derive_python_name(type_name: str) -> str:
    """Python class name of a typedef or class from its (possibly
    library-qualified) file name.

    Args:
        type_name: Name like "sysdir.llb:System Directory Type.ctl" or
            "TestCase.lvclass".

    Returns:
        PascalCase class name like "SystemDirectoryType": the file's own name
        without its extension, each word's first letter upper-cased and the
        rest of the word kept as written.
    """
    name = normalised(type_name).rsplit(":", 1)[-1]
    for extension in _TYPE_EXTENSIONS:
        name = name.removesuffix(extension)
    # Normalised first, so a full-width underscore is a word break too: a class
    # name never has an underscore inside it (``variants_module`` relies on it).
    words = name.replace("-", " ").replace("_", " ").split()
    result = "".join(word[:1].upper() + word[1:] for word in words)
    result = name_characters(result)
    if result and not starts_a_name(result):
        result = f"_{result}"
    return result or "UnknownType"


def is_enum_member_name(name: str) -> bool:
    """True when ``name`` can be an enum member written as ``Class.name``."""
    return name.isidentifier() and not keyword.iskeyword(name)
