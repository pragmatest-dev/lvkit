"""The .NET names a VI records: the assemblies it calls and the type of each
terminal of a .NET node.

Both are assembly-qualified names in .NET's own text form, ``MccDaq.ErrorInfo,
MccDaq, Version=3.0.0.0, Culture=neutral, PublicKeyToken=a37eefcf5c6ca10a``: the
type name, then the assembly's identity. The layouts they are read from are in
``docs/_internal/design/labview-binary-format.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

from .text_encoding import decode_labview_text


class DotNetTypeError(ValueError):
    """A recorded .NET type name that is not hex-encoded text."""


@dataclass(frozen=True, order=True)
class DotNetAssembly:
    """The identity a VI records for a .NET assembly it uses."""

    name: str
    version: str
    culture: str
    public_key_token: str

    @property
    def qualified(self) -> str:
        """The assembly in .NET's text form, ``MccDaq, Version=3.0.0.0,
        Culture=neutral, PublicKeyToken=a37eefcf5c6ca10a``."""
        return (
            f"{self.name}, Version={self.version}, Culture={self.culture}, "
            f"PublicKeyToken={self.public_key_token}"
        )


@dataclass(frozen=True)
class DotNetType:
    """The .NET type of a terminal of a .NET node.

    ``qualified`` is the whole recorded name; ``name`` is its type part
    (``MccDaq.DaqDeviceDescriptor[]``, array suffix included) and ``assembly`` the
    rest, the assembly's identity (empty when the name carries none).
    """

    qualified: str
    name: str
    assembly: str

    @classmethod
    def from_qualified(cls, text: str) -> DotNetType:
        """Split an assembly-qualified name at its first comma outside brackets
        (generic arguments are bracketed and carry commas of their own)."""
        depth = 0
        for i, ch in enumerate(text):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            elif ch == "," and depth == 0:
                return cls(text, text[:i].strip(), text[i + 1 :].strip())
        return cls(text, text.strip(), "")

    @classmethod
    def from_item_type(cls, hex_text: str) -> DotNetType:
        """From the ``DotNetItemType`` heap field: the assembly-qualified name as
        hex-encoded bytes ending in a NUL."""
        try:
            raw = bytes.fromhex(hex_text.strip())
        except ValueError:
            raise DotNetTypeError(
                f"DotNetItemType {hex_text!r} is not hex-encoded"
            ) from None
        text = decode_labview_text(raw.rstrip(b"\x00"))
        if not text:
            raise DotNetTypeError("DotNetItemType holds no type name")
        return cls.from_qualified(text)
