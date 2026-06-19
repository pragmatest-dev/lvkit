"""Tokenizer for the LabVIEW Formula Node language.

Produces a flat token stream for the parser. Comments (``//`` and ``/* */``)
are skipped. The Formula Node decimal separator is always ``.`` (per NI
docs), so number scanning is locale-independent.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import FormulaTranspileError

# Multi-character operators, longest first so the scanner is greedy.
_OPERATORS = (
    "**", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||", "++", "--",
    "+", "-", "*", "/", "%", "<", ">", "=", "&", "|", "^", "~", "!",
    "(", ")", "[", "]", "{", "}", ",", ";",
)


@dataclass(frozen=True)
class Token:
    kind: str   # "num" | "ident" | "op"
    value: str
    line: int
    col: int


def tokenize(src: str) -> list[Token]:
    """Tokenize a Formula Node script. Raises FormulaTranspileError on a
    character that cannot begin any token."""
    tokens: list[Token] = []
    i = 0
    line = 1
    col = 1
    n = len(src)

    def advance(count: int) -> None:
        nonlocal i, line, col
        for _ in range(count):
            if src[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        c = src[i]

        # Whitespace
        if c in " \t\r\n":
            advance(1)
            continue

        # Comments
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                advance(1)
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            advance(2)
            while i < n and not (src[i] == "*" and i + 1 < n and src[i + 1] == "/"):
                advance(1)
            if i < n:
                advance(2)  # consume */
            continue

        start_line, start_col = line, col

        # Number: digits with optional fraction and exponent. '.' only.
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            while j < n and (src[j].isdigit() or src[j] == "."):
                j += 1
            # optional exponent e/E[+/-]digits
            if j < n and src[j] in "eE":
                k = j + 1
                if k < n and src[k] in "+-":
                    k += 1
                if k < n and src[k].isdigit():
                    j = k
                    while j < n and src[j].isdigit():
                        j += 1
            text = src[i:j]
            advance(j - i)
            tokens.append(Token("num", text, start_line, start_col))
            continue

        # Identifier / keyword
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            text = src[i:j]
            advance(j - i)
            tokens.append(Token("ident", text, start_line, start_col))
            continue

        # Operators / punctuation
        for op in _OPERATORS:
            if src.startswith(op, i):
                advance(len(op))
                tokens.append(Token("op", op, start_line, start_col))
                break
        else:
            raise FormulaTranspileError(
                f"unexpected character {c!r}", start_line, start_col
            )

    return tokens
