"""Generate the Formula Node semantics probe + decoder, and self-check it.

The probe is a Formula Node script a LabVIEW user runs once to pin the numeric
model the NI docs leave underspecified. This generator ALSO runs every probe
through lvkit's own transpiler, so:
  * the emitted script is known to parse in our grammar (no dead lines), and
  * the decoder shows our current output beside the competing rules — every
    LabVIEW value that differs is a rule to fix/confirm.

It is kept (not throwaway): once LabVIEW's values come back, the same PROBES
table drives tests/test_formula_semantics.py.

Run:    uv run python scripts/gen_semantics_probe.py
Writes: docs/formula_semantics_probe.md
"""

from __future__ import annotations

from pathlib import Path

from lvkit.formula import FormulaTranspileError
from lvkit.formula.emit import VarSpec, transpile

# A probe = (slot, decls, stmt, note). `slot` picks the output array and its
# type: RI=int32, RF=double, RX=double-but-quarantined (may trap). `stmt`
# assigns the result to TARGET. `decls` are any typed locals it needs. Probes
# may reference the standard inputs A = [10,20,30,40,50] (double[]) and n = 5.
# `note` lists the competing values each output distinguishes.
P = []  # appended to in sections below


def add(slot, stmt, note, decls=""):
    P.append((slot, decls, stmt, note))


# === A. Rounding mode on float -> int store ================================
for v, lbl in [(0.5, ""), (1.5, ""), (2.5, ""), (3.5, ""),
               (-0.5, ""), (-1.5, ""), (-2.5, "")]:
    add("RI", f"TARGET = {v};", "ties-even vs half-up vs half-away vs trunc")

# === B. Fixed-width integer wrap vs saturate ===============================
add("RI", "u8 = 300; TARGET = u8;",   "44=wrap 255=saturate", "uInt8 u8;")
add("RI", "u8 = -1; TARGET = u8;",    "255=wrap 0=saturate", "uInt8 u8;")
add("RI", "i8 = 200; TARGET = i8;",   "-56=wrap 127=saturate", "int8 i8;")
add("RI", "i8 = -200; TARGET = i8;",  "56=wrap -128=saturate", "int8 i8;")
add("RI", "u16 = 70000; TARGET = u16;", "4464=wrap 65535=saturate", "uInt16 u16;")
add("RI", "i16 = 40000; TARGET = i16;", "-25536=wrap 32767=saturate", "int16 i16;")
add("RI", "u32 = 5000000000; TARGET = u32;",
    "705032704=wrap 4294967295=saturate", "uInt32 u32;")

# === C. Integer promotion / intermediate width =============================
add("RI", "a = 100; b = 100; TARGET = a * b;",
    "10000=promote 16=narrow-intermediate", "int8 a; int8 b;")
add("RI", "a = 30000; b = 30000; TARGET = a + b;",
    "60000=promote -5536=narrow-intermediate", "int16 a; int16 b;")

# === D. Integer division typing & rounding =================================
add("RI", "TARGET = 7 / 2;",   "4=round 3=trunc")
add("RI", "TARGET = -7 / 2;",  "-4=round-even -3=trunc")
add("RI", "TARGET = 5 / 2;",   "2=round-even 3=half-up")
add("RF", "TARGET = 7 / 2;",   "3.5=real-div 3.0=trunc")
add("RF", "TARGET = 1 / 3;",   "0.333..=real-div 0.0=trunc")

# === E. Modulo: operator (int & float) and mod()/rem() functions ===========
add("RI", "TARGET = -7 % 3;",  "-1=C-sign-of-dividend 2=floored")
add("RI", "TARGET = 7 % -3;",  "1=C-sign-of-dividend -2=floored")
add("RF", "TARGET = 7.5 % 2;", "1.5=fmod (does %% work on doubles?)")
add("RF", "TARGET = mod(-7, 3);",  "-1=C 2=floored")
add("RF", "TARGET = mod(7.5, 2);", "1.5")
add("RF", "TARGET = rem(7, 3);",   "1=round-remainder")
add("RF", "TARGET = rem(-7, 3);",  "-1=round-remainder")

# === F. Bitwise & | ^ ~ (incl. on negatives) ===============================
add("RI", "TARGET = 12 & 10;", "8")
add("RI", "TARGET = 12 | 10;", "14")
add("RI", "TARGET = 12 ^ 10;", "6")
add("RI", "TARGET = ~0;",      "-1=twos-complement")
add("RI", "TARGET = ~5;",      "-6")
add("RI", "TARGET = -1 & 255;", "255=signed-and")

# === G. Shifts (arithmetic vs logical, overflow) ===========================
add("RI", "TARGET = 1 << 4;",   "16")
add("RI", "TARGET = 1 << 31;",  "-2147483648=wrap 2147483648=widen")
add("RI", "TARGET = -8 >> 1;",  "-4=arithmetic 2147483644=logical")
add("RI", "TARGET = 256 >> 2;", "64")
add("RI", "u8 = 200; TARGET = u8 >> 1;", "100=unsigned-logical-shift", "uInt8 u8;")

# === H. Comparisons: result value & type ===================================
add("RI", "TARGET = (3 > 2);",  "1=int-1/0")
add("RI", "TARGET = (2 > 3);",  "0")
add("RI", "TARGET = (5 == 5);", "1")
add("RI", "TARGET = (5 != 5);", "0")
add("RI", "TARGET = (3 <= 3);", "1")
add("RI", "TARGET = (3 >= 4);", "0")
add("RF", "TARGET = (3 > 2);",  "1.0=comparison stored to double")
add("RF", "TARGET = (0.1 + 0.2 == 0.3);", "0=float-eq-is-exact 1=tolerant")

# === I. Logical && || ! ====================================================
add("RI", "TARGET = (5 && 3);", "1=logical 3=value-of-operand")
add("RI", "TARGET = (5 && 0);", "0")
add("RI", "TARGET = (0 || 5);", "1=logical 5=value-of-operand")
add("RI", "TARGET = (0 || 0);", "0")
add("RI", "TARGET = !5;",       "0")
add("RI", "TARGET = !0;",       "1")
add("RI", "TARGET = !!5;",      "1")

# === J. Power ==============================================================
add("RI", "TARGET = 2 ** 3;",   "8")
add("RI", "TARGET = 2 ** 10;",  "1024")
add("RI", "TARGET = (-2) ** 2;", "4")
add("RI", "TARGET = (-2) ** 3;", "-8")
add("RF", "TARGET = 2 ** -1;",  "0.5")
add("RF", "TARGET = 2 ** 0.5;", "1.41421..")
add("RF", "TARGET = 2 ** 0;",   "1")

# === K. Precedence & associativity (silent-miscompute risk) ================
add("RI", "TARGET = 1 + 2 * 3;",   "7=mul-first 9=left-to-right")
add("RI", "TARGET = 2 ** 3 ** 2;", "512=right-assoc 64=left-assoc")
add("RI", "TARGET = -2 ** 2;",     "-4=power>unary 4=unary>power")
add("RI", "TARGET = 1 << 2 + 1;",  "8=add-first 5=shift-first")
add("RI", "TARGET = 20 - 5 - 3;",  "12=left-assoc 18=right-assoc")
add("RF", "TARGET = 2 ** -2 ** 2;", "0.0625=right 16=left")

# === L. Functions: identity, units, convention =============================
# trigonometry — RADIANS vs degrees is the key question
add("RF", "TARGET = sin(pi / 2);", "1=radians ~0.027=degrees")
add("RF", "TARGET = acos(0.5);",   "1.047=radians 60=degrees")
add("RF", "TARGET = atan(1.0);",   "0.785=radians 45=degrees")
add("RF", "TARGET = cos(0.0);",    "1")
add("RF", "TARGET = tan(0.0);",    "0")
add("RF", "TARGET = asin(0.5);",   "0.5236=radians")
add("RF", "TARGET = atan2(1.0, 1.0);", "0.785=radians")
add("RF", "TARGET = sinh(0.0);",   "0")
add("RF", "TARGET = cosh(0.0);",   "1")
add("RF", "TARGET = tanh(0.0);",   "0")
add("RF", "TARGET = asinh(1.0);",  "0.8814")
add("RF", "TARGET = acosh(2.0);",  "1.3170")
add("RF", "TARGET = atanh(0.5);",  "0.5493")
add("RF", "TARGET = cot(1.0);",    "0.6421")
add("RF", "TARGET = csc(1.0);",    "1.1884")
add("RF", "TARGET = sec(1.0);",    "1.8508")
add("RF", "TARGET = sinc(1.0);",   "0.8415=unnormalized 0=normalized(sin(pi x))")
# logs/exp — log10 vs natural is the key question
add("RF", "TARGET = log(100.0);",  "2=log10 4.605=natural")
add("RF", "TARGET = ln(2.718281828);", "1=natural")
add("RF", "TARGET = log2(8.0);",   "3")
add("RF", "TARGET = exp(1.0);",    "2.71828")
add("RF", "TARGET = expm1(0.0);",  "0")
add("RF", "TARGET = lnp1(0.0);",   "0")
add("RF", "TARGET = sqrt(2.0);",   "1.41421")
# rounding / sign / misc functions
add("RF", "TARGET = int(2.5);",    "2=ties-even 3=half-up")
add("RF", "TARGET = int(-2.5);",   "-2=ties-even")
add("RF", "TARGET = intrz(2.7);",  "2=toward-zero")
add("RF", "TARGET = intrz(-2.7);", "-2=toward-zero")
add("RF", "TARGET = ceil(2.1);",   "3")
add("RF", "TARGET = floor(-2.1);", "-3")
add("RF", "TARGET = abs(-2.5);",   "2.5")
add("RI", "TARGET = abs(-3);",     "3=polymorphic-int")
add("RF", "TARGET = max(2.0, 5.0);", "5")
add("RF", "TARGET = min(2.0, 5.0);", "2")
add("RI", "TARGET = sign(-3);",    "-1")
add("RI", "TARGET = sign(0);",     "0")
add("RF", "TARGET = pow(2.0, 10.0);", "1024=pow-fn-matches-**")
# getexp/getman convention (IEEE mantissa in [1,2) vs frexp in [0.5,1))
add("RF", "TARGET = getexp(12.0);", "3=IEEE 4=frexp")
add("RF", "TARGET = getman(12.0);", "1.5=IEEE 0.75=frexp")
add("RF", "TARGET = getexp(0.75);", "-1=IEEE 0=frexp")
add("RF", "TARGET = getman(0.75);", "1.5=IEEE 0.75=frexp")
add("RF", "TARGET = rand();",       "uniform [0,1) — report a sample")

# === M. Arrays: sizeOfDim + indexing (1D and 2D) ===========================
# B is a 2-by-3 double array input = [[1,2,3],[4,5,6]].
add("RI", "TARGET = sizeOfDim(A, 0);", "5=length-of-A")
add("RF", "TARGET = A[0];",            "10")
add("RF", "TARGET = A[n - 1];",        "50")
add("RI", "TARGET = sizeOfDim(B, 0);", "2=rows")
add("RI", "TARGET = sizeOfDim(B, 1);", "3=cols")
add("RF", "TARGET = B[1][2];",         "6=2D-indexing")

# === N. Constants / literals ===============================================
add("RF", "TARGET = pi;",          "3.14159265")
add("RF", "TARGET = 1E3;",         "1000=accepts-exponent")

# === M(float32). Single precision ==========================================
add("RF", "s = 1.0 / 3.0; TARGET = s;",
    "0.3333333432=float32-truncates 0.3333333333=stays-double", "float32 s;")

# === O. Special / domain values — QUARANTINED in a separate node ===========
add("RX", "TARGET = 1 / 0;",       "inf=int/int-is-real traps=int-div-by-zero")
add("RX", "TARGET = 1.0 / 0.0;",   "inf=IEEE traps=node-errors")
add("RX", "TARGET = -1.0 / 0.0;",  "-inf=IEEE traps")
add("RX", "TARGET = 0.0 / 0.0;",   "nan=IEEE traps")
add("RX", "TARGET = sqrt(-1.0);",  "nan traps")
add("RX", "TARGET = ln(0.0);",     "-inf traps")
add("RX", "TARGET = ln(-1.0);",    "nan traps")
add("RX", "TARGET = log(0.0);",    "-inf traps")
add("RX", "TARGET = acos(2.0);",   "nan=out-of-domain traps")
add("RX", "TARGET = (-8) ** (1.0 / 3.0);", "nan=C-pow -2=real-cube-root")
add("RX", "TARGET = 0 ** 0;",      "1 nan")
add("RX", "TARGET = tan(pi / 2);", "huge-finite inf")

PROBES = P

# Standard inputs every probe may reference.
_INPUTS = [
    VarSpec("A", "NumFloat64", "in", True),
    VarSpec("n", "NumInt32", "in", False),
]
_A = [10.0, 20.0, 30.0, 40.0, 50.0]


def our_value(slot: str, decls: str, stmt: str) -> str:
    """Run the probe through *our* transpiler; report the value or why not."""
    out_type = "NumInt32" if slot == "RI" else "NumFloat64"
    script = (decls + "\n" + stmt.replace("TARGET", "r")).strip()
    variables = [VarSpec("r", out_type, "out", False), *_INPUTS]
    try:
        res = transpile(script, variables, func_name="probe")
        ns: dict = {}
        exec("import math\nfrom lvkit.runtime import lv as _lv\n" + res.source, ns)
        return repr(ns["probe"](A=list(_A), n=5)["r"])
    except FormulaTranspileError as e:
        return f"UNSUPPORTED ({e})"
    except Exception as e:
        return f"RUNTIME {type(e).__name__}: {e}"


def slot_sizes() -> dict[str, int]:
    sizes: dict[str, int] = {}
    for slot, *_ in PROBES:
        sizes[slot] = sizes.get(slot, 0) + 1
    return sizes


def build_node_script(slots: tuple[str, ...], header: str) -> str:
    decls: list[str] = []
    seen: set[str] = set()
    lines: list[str] = []
    idx = {s: 0 for s in slots}
    for slot, d, stmt, _ in PROBES:
        if slot not in slots:
            continue
        for tok in d.split(";"):
            tok = tok.strip()
            if tok and tok not in seen:
                seen.add(tok)
                decls.append(tok + ";")
        k = idx[slot]
        idx[slot] += 1
        lines.append(stmt.replace("TARGET", f"{slot}[{k}]"))
    body = ("\n".join(decls) + "\n\n" if decls else "") + "\n".join(lines) + "\n"
    return header + body


def main() -> None:
    sizes = slot_sizes()
    rows = []
    idx = {"RI": 0, "RF": 0, "RX": 0}
    for slot, decls, stmt, note in PROBES:
        k = idx[slot]
        idx[slot] += 1
        expr = stmt.replace("TARGET = ", "").rstrip(";")
        if decls:
            expr = f"{decls} {expr}"
        rows.append((f"{slot}[{k}]", expr, our_value(slot, decls, stmt), note))

    main_head = (
        "/* lvkit Formula Node semantics probe — MAIN.\n"
        " * Create a Formula Node with these terminals:\n"
        "   *  A  : double array   (INPUT)  = [10,20,30,40,50]\n"
        "   *  B  : double 2D array (INPUT)  = [[1,2,3],[4,5,6]]\n"
        "   *  n  : int32          (INPUT)  = 5\n"
        f"   *  RI : int32 array  (in+out)  >= {sizes['RI']} elements, init 0\n"
        f"   *  RF : double array (in+out)  >= {sizes['RF']} elements, init 0\n"
        " * Paste this, run once, report RI and RF. */\n"
    )
    edge_head = (
        "/* lvkit Formula Node semantics probe — SPECIAL VALUES.\n"
        " * Put this in a SEPARATE Formula Node so a divide-by-zero / sqrt(-1)\n"
        " * trap can't void the MAIN run. A trap here is itself a valid result.\n"
        f"   *  RX : double array (in+out) >= {sizes['RX']} elements, init 0 */\n"
    )
    main_lv = build_node_script(("RI", "RF"), main_head)
    edge_lv = build_node_script(("RX",), edge_head)

    out = [
        "# Formula Node semantics probe\n",
        f"A comprehensive probe ({sizes['RI']} integer + {sizes['RF']} real + "
        f"{sizes['RX']} special-value cases) that pins the numeric model the NI "
        "docs leave underspecified. Run it in a real **Formula Node** (typed "
        "terminals) — the Eval Formula Node VI is real-numbers-only and can't "
        "show integer behaviour.\n",
        "## Script 1 — main probe (required)\n", "```c", main_lv.rstrip(), "```\n",
        "## Script 2 — special values (run as a SEPARATE Formula Node)\n",
        "These can trap at the node level (divide-by-zero, sqrt of a negative, "
        "domain errors). A separate node means a trap can't take Script 1 down "
        "with it. If this node errors instead of producing `inf`/`nan`, **that "
        "is the answer** — report \"traps\" (delete lines one at a time to find "
        "which).\n",
        "```c", edge_lv.rstrip(), "```\n",
        "## Reading the results back out of LabVIEW\n",
        "Display format must keep the distinguishing bits visible. Wire each "
        "array through **Array To Spreadsheet String** and copy the text:\n"
        "- `RF`/`RX` (double): format **`%.17g`** — full precision, and renders "
        "`inf` / `-inf` / `nan` literally.\n"
        "- `RI` (int32): format **`%d`** — exact integers; wrap/overflow show "
        "directly. For the bitwise/shift rows, add a second `RI` string with "
        "**`0x%08X`** (or set the indicator radix to Hex) so the bit pattern is "
        "explicit, not just signed decimal.\n"
        "- Change only the indicator's *display*, never a value. If one line "
        "stops the node compiling, delete it, re-run, and say which line.\n",
        "## Decoder — `our value` is lvkit's current output; any LabVIEW value "
        "that differs is a rule to fix/confirm\n",
        "| slot | expression | our value | LabVIEW says → rule |",
        "|------|------------|-----------|---------------------|",
    ]
    for slot, expr, ours, note in rows:
        e = expr.replace("|", "\\|")
        out.append(f"| `{slot}` | `{e}` | `{ours}` | {note} |")
    out.append("")

    doc = Path("docs/formula_semantics_probe.md")
    doc.write_text("\n".join(out))
    bad = [(s, e, o) for s, e, o, _ in rows
           if o.startswith(("RUNTIME", "UNSUPPORTED"))]
    print(f"wrote {doc}  "
          f"({sizes['RI']} int, {sizes['RF']} real, {sizes['RX']} special)")
    print(f"\n{len(bad)} probes our engine can't handle yet "
          "(unsupported fn or runtime error):")
    for s, e, o in bad:
        print(f"  {s:7s} {e:34.34s} -> {o}")


if __name__ == "__main__":
    main()
