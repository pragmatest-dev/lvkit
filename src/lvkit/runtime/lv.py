"""Run-time support the generated code still imports: default references for
unwired Property and Invoke Nodes, and the stand-in for a primitive with no
implementation."""

from __future__ import annotations

from .default_properties import DEFAULT_PROPERTIES as _DEFAULT_PROPERTIES


class UnavailableReferenceMember(AttributeError):
    """A property or method was used on a reference object that has no value
    for it."""


class DefaultReference:
    """The object a Property or Invoke Node uses when its reference input is
    unwired.

    NI documents the default for the Application class (the current
    application instance) and the VI class (the VI containing the node). The
    object names its class and the VI that owns the node. A property that was
    written can be read back, and a property that NI documents a value for
    (``default_properties``) reads that value; any other property or method
    raises ``UnavailableReferenceMember`` naming the class and the member.
    """

    def __init__(self, class_name: str, owner: str):
        self.__dict__["class_name"] = class_name
        self.__dict__["owner"] = owner

    def __getattr__(self, name: str):
        documented = _DEFAULT_PROPERTIES.get(self.class_name, {}).get(name)
        if documented is not None:
            return documented.read()
        raise UnavailableReferenceMember(
            f"default {self.class_name} reference of {self.owner!r} has no"
            f" value for {name!r}"
        )

    def __repr__(self) -> str:
        return f"DefaultReference({self.class_name!r}, owner={self.owner!r})"


_DEFAULT_REFERENCES: dict[tuple[str, str], DefaultReference] = {}


def default_reference(class_name: str, owner: str) -> DefaultReference:
    """The one default reference of ``class_name`` for the VI ``owner``, so
    every unwired node of that class in the VI reads and writes the same
    object."""
    key = (class_name, owner)
    if key not in _DEFAULT_REFERENCES:
        _DEFAULT_REFERENCES[key] = DefaultReference(class_name, owner)
    return _DEFAULT_REFERENCES[key]


class UnresolvedOutput(RuntimeError):
    """A value was used that a primitive without an implementation should have
    produced."""


class Placeholder:
    """The value of an output of a primitive that has no implementation yet.

    It stands in so the generated function has a defined name for the output;
    using it (reading a property, calling a method, indexing, iterating)
    raises ``UnresolvedOutput`` naming the primitive and the output.
    """

    def __init__(self, primitive: str, output: str):
        self.__dict__["primitive"] = primitive
        self.__dict__["output"] = output

    def _unresolved(self, *_args, **_kwargs):
        raise UnresolvedOutput(
            f"output {self.output!r} of unimplemented primitive"
            f" {self.primitive!r} was used"
        )

    __getattr__ = _unresolved
    __setattr__ = _unresolved
    __call__ = _unresolved
    __getitem__ = _unresolved
    __iter__ = _unresolved

    def __repr__(self) -> str:
        return f"Placeholder({self.primitive!r}, output={self.output!r})"


def placeholder(primitive: str, output: str) -> Placeholder:
    return Placeholder(primitive, output)
