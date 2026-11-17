"""The module a generated package holds for a Configuration File VI's .ini file.

It is written once into the package root as ``ini.py``, only when a generated module
imports it (a catalog entry whose ``imports`` list ``from .ini import IniFile``), and
imports nothing from the package or from lvkit. Its source is the header below and the
text of ``IniFile``.

``IniFile``: Python has no call that changes an .ini file in place. ``configparser``
writes a file back from its in-memory data and drops every comment and blank line,
and NI's Configuration File VIs keep them: "During a write operation, Configuration
File VIs preserve lines that lack section names or value-key pairs." (NI, Windows
Configuration Settings File Format). The class is a ``ConfigParser`` that keeps the
text it read and, when it saves, edits that text: the lines of a changed key take the
new value, new keys go to "the end of the specified section" and new sections "to the
end of the configuration data" (NI, Write Key), a removed key's lines go, and every
other line stays byte for byte.
"""

from __future__ import annotations

import configparser
import inspect
import re

INI_MODULE_NAME = "ini"
INI_CLASS_NAME = "IniFile"

# A relative import of the package's ``ini`` module.
_INI_IMPORT = re.compile(rf"^from \.+{INI_MODULE_NAME} import ")


def imports_ini(statement: str) -> bool:
    """Whether ``statement`` imports from the package's ``ini`` module."""
    return _INI_IMPORT.match(statement) is not None


def ini_import(prefix: str) -> str:
    """``from <prefix>ini import IniFile``."""
    return f"from {prefix}{INI_MODULE_NAME} import {INI_CLASS_NAME}"


class IniFile(configparser.ConfigParser):
    """A ``ConfigParser`` that saves by editing the text it loaded."""

    _ini_text = ""
    _ini_original = {}

    def _ini_snapshot(self):
        return {name: dict(self[name]) for name in self.sections()}

    def load(self, path):
        """Read the file (an absent file is empty) and keep its text."""
        try:
            with open(path, encoding="utf-8", newline="") as file:
                self._ini_text = file.read()
        except FileNotFoundError:
            self._ini_text = ""
        # NI: "During a read operation, Configuration File VIs ignore lines that lack
        # section names or value-key pairs", and a key-value pair is one "on the lines
        # after the section line": whatever comes before the first section is ignored.
        lines = self._ini_text.splitlines(keepends=True)
        first = next(
            (
                n
                for n, line in enumerate(lines)
                if not line.strip().startswith(";") and self.SECTCRE.match(line.strip())
            ),
            len(lines),
        )
        self.read_string("".join(lines[first:]), str(path))
        self._ini_original = self._ini_snapshot()

    def merged_text(self):
        """The loaded text with the changes made since, as the file should read."""
        current = self._ini_snapshot()
        original = self._ini_original
        eol = "\r\n" if "\r\n" in self._ini_text else "\n"

        def value_text(value):
            return "" if value is None else "=" + value.replace("\n", eol + "\t")

        out = []
        end_of = {}
        section = None
        key = None
        key_indent = 0
        key_kept = True
        for line in self._ini_text.splitlines(keepends=True):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if stripped == "" or stripped.startswith(";"):
                out.append(line)
                continue
            if section is not None and key is not None and indent > key_indent:
                if key_kept:
                    out.append(line)
                    end_of[section] = len(out)
                continue
            header = self.SECTCRE.match(stripped)
            if header is not None:
                section = header.group("header")
                key = None
                if section == self.default_section:
                    section = None
                    out.append(line)
                elif section in current:
                    out.append(line)
                    end_of[section] = len(out)
                continue
            if section is None:
                out.append(line)
                continue
            parts = re.match(r"(?P<head>[^=]*?\s*=\s*)(?P<value>.*)$", stripped)
            name = (parts.group("head").split("=")[0] if parts else stripped).strip()
            if name not in original.get(section, {}):
                key = None
                out.append(line)
                continue
            key = name
            key_indent = indent
            now = current.get(section, {})
            key_kept = key in now
            if not key_kept:
                continue
            if now[key] == original[section][key]:
                out.append(line)
            else:
                head = parts.group("head") if parts else key + "="
                out.append(line[:indent] + head + value_text(now[key])[1:] + eol)
            end_of[section] = len(out)
        added = []
        for name, keys in current.items():
            fresh = [k for k in keys if k not in original.get(name, {})]
            if name in original and fresh:
                at = end_of.get(name, len(out))
                if at and not out[at - 1].endswith(("\n", "\r")):
                    out[at - 1] += eol
                added.append((at, [k + value_text(keys[k]) + eol for k in fresh]))
        for at, lines in sorted(added, key=lambda item: -item[0]):
            out[at:at] = lines
        tail = []
        for name, keys in current.items():
            if name in original:
                continue
            if tail:
                tail.append(eol)
            tail.append("[" + name + "]" + eol)
            tail.extend(k + value_text(v) + eol for k, v in keys.items())
        if tail:
            if out and not out[-1].endswith(("\n", "\r")):
                out[-1] += eol
            if out and out[-1].strip():
                out.append(eol)
            out.extend(tail)
        return "".join(out)

    def save(self, path):
        """Write the file, keeping its other lines, and treat it as loaded."""
        text = self.merged_text()
        with open(path, "w", encoding="utf-8", newline="") as file:
            file.write(text)
        self._ini_text = text
        self._ini_original = self._ini_snapshot()


_HEADER = '''"""Reads and writes an .ini file as NI's Configuration File VIs do: a write
keeps the file's comments, blank lines and order."""

from __future__ import annotations

import configparser
import re


'''

INI_MODULE_SOURCE = _HEADER + inspect.getsource(IniFile)
