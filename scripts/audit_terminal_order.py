#!/usr/bin/env python
"""Read-only audit of terminal / operand ordering correctness.

Three independent audits, all read-only (no file in src/ or data/ is
modified):

  AUDIT 1 - Primitive operand order
      For every primitive whose python_code references >= 2 distinct
      ``in_N`` tokens and whose operator(s) are order-sensitive
      (non-commutative), find real usages in the sample heaps by
      ``<primResID>``.  For each usage the physical operand order is
      derived from the input terminals' geometry (termBounds, sorted by
      left then top -> leftmost/topmost is operand 1).  LabVIEW writes the
      top input as the first operand, so a correct left-stacked binary
      formula is ``in_<top> OP in_<bottom>``.  Any primitive whose declared
      ``in_N`` appearance order disagrees with the geometry-derived order is
      FLAGGED as a suspected inversion.

  AUDIT 2 - vi.lib terminal mapping
      For each ``data/vilib/*.json`` entry that has resolved (indexed)
      input terminals, report the input type vector and risk class.  If the
      VI is locally loadable its connector pane is compared against the
      declared index -> name mapping.

  AUDIT 3 - connector-pane completeness
      Scan the sample ``*_FPHb.xml`` heaps; report VIs whose connector pane
      is empty (no connected slots) or exposes fewer connections than the
      VI has wired front-panel terminals - the cases where the index=0
      fallback collapses argument ordering.

Usage::

    uv run python scripts/audit_terminal_order.py
    uv run python scripts/audit_terminal_order.py -o .tmp/terminal_order_audit.md
    uv run python scripts/audit_terminal_order.py --conpane-limit 500
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIMS_JSON = ROOT / "src" / "lvkit" / "data" / "primitives.json"
VILIB_DIR = ROOT / "src" / "lvkit" / "data" / "vilib"
# Two DIFFERENT roots, deliberately: extracted XML now lives in the project-local
# cache, while the .vi source files it describes still live in the corpus tree.
# Conflating them silently yields zero matches (a clean-looking audit that
# checked nothing).
EXTRACTED_DIR = ROOT / ".lvkit" / "cache" / "extracted"
SAMPLES_DIR = ROOT / ".lvkit" / "cache" / "samples"

IN_TOKEN_RE = re.compile(r"\bin_(\d+)\b")

# Operators whose result depends on operand order.
ORDER_SENSITIVE_OP_RE = re.compile(r"(//|<<|>>|<=|>=|[-/%<>])")
# Purely commutative operators / function names (order cannot matter).
COMMUTATIVE_OPS = {"+", "*", "&", "|", "^"}
COMMUTATIVE_FUNCS = {"min", "max", "and", "or"}


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _formula_text(python_code: object) -> str:
    """Flatten python_code (dict of expr or plain str) into one string."""
    if isinstance(python_code, dict):
        return " ; ".join(str(v) for v in python_code.values())
    return str(python_code)


def _distinct_in_tokens(text: str) -> list[int]:
    """Distinct ``in_N`` indices in order of first appearance."""
    seen: list[int] = []
    for m in IN_TOKEN_RE.finditer(text):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def _parse_bounds(text: str | None) -> tuple[int, int] | None:
    """Parse ``(top, left, bottom, right)`` -> (top, left)."""
    if not text:
        return None
    nums = re.findall(r"-?\d+", text)
    if len(nums) < 2:
        return None
    top, left = int(nums[0]), int(nums[1])
    return top, left


# --------------------------------------------------------------------------
# AUDIT 1 - primitive operand order
# --------------------------------------------------------------------------
@dataclass
class PrimSpec:
    res_id: str
    name: str
    formula: str
    in_tokens: list[int]
    input_types: list[str]
    verified: object
    classification: str  # commutative | order_sensitive | order_sensitive_complex


@dataclass
class PrimUsage:
    vi: str
    # parmIndex -> (top, left)
    inputs: dict[int, tuple[int, int]]
    geometry_order: list[int]  # parmIndexes sorted by (left, top)


def _classify_formula(formula: str, input_types: list[str]) -> str:
    """commutative | order_sensitive | order_sensitive_complex."""
    # Remove the two commutative comparison operators so their '=' / '<' / '>'
    # characters do not trip the order-sensitive matcher.
    masked = formula.replace("==", " ").replace("!=", " ")

    if ORDER_SENSITIVE_OP_RE.search(masked):
        return "order_sensitive"

    # String / array indexing or slicing with multiple operands is ordered.
    if "[" in formula and len(_distinct_in_tokens(formula)) >= 2:
        return "order_sensitive"

    # '+' is commutative for numbers but is concatenation (ordered) for
    # strings / arrays.
    if "+" in formula and any(
        (t or "").lower() in ("string", "array") for t in input_types
    ):
        return "order_sensitive"

    # Function-call forms: commutative only for known symmetric functions.
    func_names = set(re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", formula))
    if func_names and not func_names <= COMMUTATIVE_FUNCS:
        return "order_sensitive_complex"

    return "commutative"


def load_prim_specs() -> list[PrimSpec]:
    data = json.loads(PRIMS_JSON.read_text())
    specs: list[PrimSpec] = []
    for res_id, entry in data["primitives"].items():
        formula = _formula_text(entry.get("python_code", ""))
        in_tokens = _distinct_in_tokens(formula)
        if len(in_tokens) < 2:
            continue
        input_types = [
            t.get("type", "")
            for t in entry.get("terminals", [])
            if t.get("direction") in ("in", "input")
        ]
        classification = _classify_formula(formula, input_types)
        specs.append(
            PrimSpec(
                res_id=res_id,
                name=entry.get("name", "?"),
                formula=formula,
                in_tokens=in_tokens,
                input_types=input_types,
                verified=entry.get("verified"),
                classification=classification,
            )
        )
    return specs


def _extract_prim_usages_from_heap(
    path: Path, target_ids: set[str]
) -> dict[str, list[PrimUsage]]:
    """Return {res_id: [PrimUsage, ...]} found in one BD heap file."""
    out: dict[str, list[PrimUsage]] = {}
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return out
    for elem in root.iter("SL__arrayElement"):
        if elem.get("class") != "prim":
            continue
        res_el = elem.find("primResID")
        if res_el is None or res_el.text not in target_ids:
            continue
        res_id = res_el.text
        tl = elem.find("termList")
        if tl is None:
            continue
        inputs: dict[int, tuple[int, int]] = {}
        for term in tl.findall("SL__arrayElement"):
            dco = term.find("dco")
            if dco is None:
                continue
            pidx = dco.find("parmIndex")
            if pidx is None or pidx.text is None:
                continue  # output terminals carry no parmIndex
            tb = _parse_bounds(
                dco.find("termBounds").text
                if dco.find("termBounds") is not None
                else None
            )
            if tb is None:
                continue
            inputs[int(pidx.text)] = tb  # (top, left)
        if len(inputs) < 2:
            continue
        # sort by (left, top): leftmost then topmost is operand 1
        geometry_order = sorted(inputs, key=lambda p: (inputs[p][1], inputs[p][0]))
        out.setdefault(res_id, []).append(
            PrimUsage(
                vi=path.name,
                inputs=inputs,
                geometry_order=geometry_order,
            )
        )
    return out


def scan_heaps_for_prims(
    target_ids: set[str],
) -> dict[str, list[PrimUsage]]:
    """Single pass over all BD heaps collecting usages of target primIDs."""
    tag_markers = {f"<primResID>{rid}</primResID>" for rid in target_ids}
    usages: dict[str, list[PrimUsage]] = {rid: [] for rid in target_ids}
    for path in EXTRACTED_DIR.rglob("*_BDHb.xml"):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        if not any(marker in text for marker in tag_markers):
            continue
        for rid, found in _extract_prim_usages_from_heap(path, target_ids).items():
            usages[rid].extend(found)
    return usages


@dataclass
class PrimFinding:
    spec: PrimSpec
    # CONSISTENT | SUSPECTED_INVERSION | GEOMETRY_VARIES | UNVALIDATED
    #   | INDEX_MAP_MISMATCH
    status: str
    n_usages: int
    geometry_orders: dict[tuple[int, ...], int]  # order -> count
    sample_vi: str | None
    sample_inputs: dict[int, tuple[int, int]] | None
    expected_formula: str | None
    confidence: str = "-"  # HIGH | MEDIUM | LOW | -
    detail: str = ""  # declared vs observed parmIndex sets, etc.


def _swap_binary_formula(formula: str, a: int, b: int) -> str:
    """Swap in_a and in_b tokens in a formula (for the binary inversion case)."""

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if n == a:
            return f"in_{b}"
        if n == b:
            return f"in_{a}"
        return m.group(0)

    return IN_TOKEN_RE.sub(repl, formula)


def audit1(
    specs: list[PrimSpec], usages: dict[str, list[PrimUsage]]
) -> list[PrimFinding]:
    findings: list[PrimFinding] = []
    for spec in specs:
        if spec.classification == "commutative":
            continue
        u = usages.get(spec.res_id, [])
        declared = spec.in_tokens
        if not u:
            findings.append(
                PrimFinding(
                    spec=spec,
                    status="UNVALIDATED",
                    n_usages=0,
                    geometry_orders={},
                    sample_vi=None,
                    sample_inputs=None,
                    expected_formula=None,
                    confidence="-",
                    detail="no local sample usage",
                )
            )
            continue

        decl_set = set(declared)
        sample = u[0]
        # Most common observed input parmIndex set across usages.
        set_tally: dict[frozenset[int], int] = {}
        for usage in u:
            key = frozenset(usage.inputs)
            set_tally[key] = set_tally.get(key, 0) + 1
        observed_set = set(max(set_tally, key=lambda k: set_tally[k]))

        # The formula's in_N numbering must map onto real input parmIndexes.
        # If any declared in_N is absent from observed parmIndexes (e.g. the
        # formula references in_0 but the heap has parmIndex 1..N), the
        # comparison is not trustworthy -> report as inconclusive.
        if not decl_set <= observed_set:
            findings.append(
                PrimFinding(
                    spec=spec,
                    status="INDEX_MAP_MISMATCH",
                    n_usages=len(u),
                    geometry_orders={},
                    sample_vi=sample.vi,
                    sample_inputs=sample.inputs,
                    expected_formula=None,
                    confidence="LOW",
                    detail=(
                        f"declared in_N {sorted(decl_set)} not a subset of "
                        f"observed parmIndexes {sorted(observed_set)} - "
                        "manual mapping required"
                    ),
                )
            )
            continue

        # Tally geometry order restricted to the declared in_N.
        tally: dict[tuple[int, ...], int] = {}
        for usage in u:
            restricted = [p for p in usage.geometry_order if p in decl_set]
            tally[tuple(restricted)] = tally.get(tuple(restricted), 0) + 1
        majority = max(tally, key=lambda k: tally[k])
        declared_tuple = tuple(declared)

        # Mapping quality: formula uses *all* inputs (cleanest) vs a subset.
        uses_all_inputs = observed_set == decl_set
        clean_binary = uses_all_inputs and len(decl_set) == 2

        if len(tally) > 1:
            status = "GEOMETRY_VARIES"
        elif majority == declared_tuple:
            status = "CONSISTENT"
        else:
            status = "SUSPECTED_INVERSION"

        # Confidence: HIGH for clean binaries with many agreeing usages.
        if status in ("CONSISTENT", "SUSPECTED_INVERSION"):
            if clean_binary and len(u) >= 5 and len(tally) == 1:
                confidence = "HIGH"
            elif uses_all_inputs and len(tally) == 1:
                confidence = "MEDIUM"
            else:
                confidence = "LOW"
        else:
            confidence = "LOW"

        expected = None
        if status == "SUSPECTED_INVERSION" and clean_binary:
            expected = _swap_binary_formula(spec.formula, declared[0], declared[1])

        detail = (
            f"declared {sorted(decl_set)}; observed inputs {sorted(observed_set)}; "
            f"{'uses all inputs' if uses_all_inputs else 'formula uses a subset'}"
        )
        findings.append(
            PrimFinding(
                spec=spec,
                status=status,
                n_usages=len(u),
                geometry_orders=tally,
                sample_vi=sample.vi,
                sample_inputs=sample.inputs,
                expected_formula=expected,
                confidence=confidence,
                detail=detail,
            )
        )
    return findings


# --------------------------------------------------------------------------
# AUDIT 2 - vi.lib terminal mapping
# --------------------------------------------------------------------------
@dataclass
class VilibFinding:
    file: str
    name: str
    n_inputs: int
    input_types: list[str]
    risk: str  # at_risk_same_type | type_disambiguated | loadable_checked
    note: str


def _find_local_vi(name: str, vi_path: str | None) -> Path | None:
    """Best-effort: locate a VI file for a vilib entry in the samples tree."""
    candidates: list[Path] = []
    if vi_path:
        p = Path(vi_path)
        if p.is_absolute() and p.exists():
            return p
        candidates.append(SAMPLES_DIR / p.name)
    # search by .vi filename
    target = f"{name}.vi"
    for cand in candidates:
        if cand.exists():
            return cand
    hits = list(SAMPLES_DIR.rglob(target))
    return hits[0] if hits else None


def audit2() -> tuple[list[VilibFinding], dict[str, int]]:
    findings: list[VilibFinding] = []
    counts = {
        "entries_total": 0,
        "entries_with_indexed_terminals": 0,
        "multi_input_resolved": 0,
        "at_risk_same_type": 0,
        "type_disambiguated": 0,
        "loadable_checked": 0,
    }
    for f in sorted(VILIB_DIR.glob("*.json")):
        if f.name.startswith("_"):
            continue
        data = json.loads(f.read_text())
        for entry in data.get("entries", []):
            counts["entries_total"] += 1
            terms = entry.get("terminals", [])
            indexed = [t for t in terms if "index" in t]
            if not indexed:
                continue
            counts["entries_with_indexed_terminals"] += 1
            inputs = [t for t in indexed if t.get("direction") in ("in", "input")]
            if len(inputs) < 2:
                continue
            counts["multi_input_resolved"] += 1
            types = [t.get("type") or "?" for t in inputs]
            # type-disambiguated only if every input type is known and distinct
            known = [t for t in types if t != "?"]
            distinct = len(set(known)) == len(known) and len(known) == len(types)
            if distinct:
                risk = "type_disambiguated"
                note = "all input types known and distinct (lower risk)"
                counts["type_disambiguated"] += 1
            else:
                risk = "at_risk_same_type"
                note = (
                    "order is a hand-assigned guess (>=2 inputs share a type "
                    "or have unknown types -> unverifiable without the VI)"
                )
                counts["at_risk_same_type"] += 1
            # Best-effort loadability check.
            vi = _find_local_vi(entry.get("name", ""), entry.get("vi_path"))
            if vi is not None:
                note += f"; local VI found: {vi.name}"
                counts["loadable_checked"] += 1
            findings.append(
                VilibFinding(
                    file=f.name,
                    name=entry.get("name", "?"),
                    n_inputs=len(inputs),
                    input_types=types,
                    risk=risk,
                    note=note,
                )
            )
    return findings, counts


# --------------------------------------------------------------------------
# AUDIT 3 - connector-pane completeness
# --------------------------------------------------------------------------
@dataclass
class ConpaneFinding:
    vi: str
    pattern_id: int
    connected_slots: int
    total_slots: int
    wired_fp_terminals: int
    issue: str  # EMPTY | PARTIAL


def audit3(limit: int | None) -> tuple[list[ConpaneFinding], dict[str, int]]:
    from lvkit.parser.front_panel import (  # local import: optional dependency
        extract_fp_terminals,
        parse_connector_pane,
    )

    findings: list[ConpaneFinding] = []
    counts = {
        "fp_heaps_scanned": 0,
        "empty_conpane": 0,
        "partial_conpane": 0,
    }
    fp_files = sorted(EXTRACTED_DIR.rglob("*_FPHb.xml"))
    if limit:
        fp_files = fp_files[:limit]
    for fp in fp_files:
        bd = fp.with_name(fp.name.replace("_FPHb.xml", "_BDHb.xml"))
        if not bd.exists():
            continue
        counts["fp_heaps_scanned"] += 1
        try:
            conpane = parse_connector_pane(fp)
        except (ET.ParseError, OSError):
            continue
        if conpane is None:
            continue
        connected = sum(1 for s in conpane.slots if s.fp_dco_uid)
        total = len(conpane.slots)
        try:
            bd_root = ET.parse(bd).getroot()
            wired = len(extract_fp_terminals(bd_root, fp))
        except (ET.ParseError, OSError):
            wired = 0
        issue: str | None = None
        if connected == 0 and wired >= 2:
            issue = "EMPTY"
            counts["empty_conpane"] += 1
        elif 0 < connected < wired and wired >= 2:
            issue = "PARTIAL"
            counts["partial_conpane"] += 1
        if issue:
            findings.append(
                ConpaneFinding(
                    vi=fp.name.replace("_FPHb.xml", ""),
                    pattern_id=conpane.pattern_id,
                    connected_slots=connected,
                    total_slots=total,
                    wired_fp_terminals=wired,
                    issue=issue,
                )
            )
    return findings, counts


# --------------------------------------------------------------------------
# Report rendering
# --------------------------------------------------------------------------
def _fmt_geom(orders: dict[tuple[int, ...], int]) -> str:
    return ", ".join(
        f"{list(k)}x{v}" for k, v in sorted(orders.items(), key=lambda kv: -kv[1])
    )


def render_report(
    a1: list[PrimFinding],
    a2: list[VilibFinding],
    a2_counts: dict[str, int],
    a3: list[ConpaneFinding],
    a3_counts: dict[str, int],
    n_order_sensitive: int,
) -> str:
    L: list[str] = []
    L.append("# Terminal / Operand Order Audit\n")
    L.append(
        "Read-only audit. No primitive, vilib, or source definition was "
        "modified. Geometry rule: input terminals sorted by (left, then top); "
        "leftmost/topmost is operand 1. LabVIEW writes the top input as the "
        "first operand, so a correct left-stacked binary formula is "
        "`in_<top> OP in_<bottom>`.\n"
    )

    # ---- AUDIT 1 ----
    inversions = [f for f in a1 if f.status == "SUSPECTED_INVERSION"]
    inv_high = [f for f in inversions if f.confidence == "HIGH"]
    inv_other = [f for f in inversions if f.confidence != "HIGH"]
    consistent = [f for f in a1 if f.status == "CONSISTENT"]
    unvalidated = [f for f in a1 if f.status == "UNVALIDATED"]
    varies = [f for f in a1 if f.status == "GEOMETRY_VARIES"]
    mismatch = [f for f in a1 if f.status == "INDEX_MAP_MISMATCH"]
    L.append("## AUDIT 1 - Primitive operand order\n")
    L.append(
        f"- Order-sensitive primitives (>=2 distinct `in_N`): "
        f"**{n_order_sensitive}**\n"
        f"- Suspected inversions: **{len(inversions)}** "
        f"(HIGH confidence: **{len(inv_high)}**, lower: **{len(inv_other)}**)\n"
        f"- Consistent with geometry: **{len(consistent)}**\n"
        f"- Geometry varies across usages: **{len(varies)}**\n"
        f"- Index-map mismatch (inconclusive, manual mapping needed): "
        f"**{len(mismatch)}**\n"
        f"- Unvalidated (no local sample usage): **{len(unvalidated)}**\n"
    )
    L.append(
        "\nConfidence: HIGH = clean two-input op, >=5 sample usages, single "
        "agreed geometry. INDEX_MAP_MISMATCH = the formula's `in_N` numbering "
        "does not line up with the heap's parmIndexes (e.g. references `in_0` "
        "where the heap starts at parmIndex 1), so it needs manual mapping "
        "before any conclusion.\n"
    )
    _status_rank = {
        "SUSPECTED_INVERSION": 0,
        "GEOMETRY_VARIES": 1,
        "INDEX_MAP_MISMATCH": 2,
        "CONSISTENT": 3,
        "UNVALIDATED": 4,
    }
    L.append(
        "\n| primResID | name | classification | current formula | status "
        "| confidence | usages | geometry order(s) [parmIndex] | sample VI "
        "| sample parmIndex->(top,left) | mapping detail | expected formula "
        "| verified |"
    )
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for f in sorted(a1, key=lambda x: (_status_rank.get(x.status, 9), x.spec.name)):
        si = (
            "; ".join(f"{p}->{f.sample_inputs[p]}" for p in sorted(f.sample_inputs))
            if f.sample_inputs
            else "-"
        )
        L.append(
            f"| {f.spec.res_id} | {f.spec.name} | {f.spec.classification} "
            f"| `{f.spec.formula}` | **{f.status}** | {f.confidence} "
            f"| {f.n_usages} | {_fmt_geom(f.geometry_orders) or '-'} "
            f"| {f.sample_vi or '-'} | {si} | {f.detail} | "
            f"{('`' + f.expected_formula + '`') if f.expected_formula else '-'} "
            f"| {f.spec.verified} |"
        )

    # ---- AUDIT 2 ----
    L.append("\n## AUDIT 2 - vi.lib terminal mapping\n")
    L.append(
        f"- vilib entries total: **{a2_counts['entries_total']}**\n"
        f"- entries with resolved (indexed) terminals: "
        f"**{a2_counts['entries_with_indexed_terminals']}**\n"
        f"- resolved entries with >=2 inputs: "
        f"**{a2_counts['multi_input_resolved']}**\n"
        f"- at-risk (same/unknown input types): "
        f"**{a2_counts['at_risk_same_type']}**\n"
        f"- type-disambiguated (lower risk): "
        f"**{a2_counts['type_disambiguated']}**\n"
        f"- local VI found for cross-check: "
        f"**{a2_counts['loadable_checked']}**\n"
    )
    L.append("\n| file | name | #inputs | input type vector | risk | note |")
    L.append("|---|---|---|---|---|---|")
    for v in sorted(a2, key=lambda x: (x.risk != "at_risk_same_type", x.name)):
        L.append(
            f"| {v.file} | {v.name} | {v.n_inputs} | {v.input_types} "
            f"| {v.risk} | {v.note} |"
        )
    L.append(
        "\n*Note: only 9 vilib entries carry resolved per-terminal index/"
        "direction metadata; the remaining ~2936 entries are documentation-"
        "only (name/description, no direction or type), so an ordering "
        "decision has not yet been encoded for them and cannot be audited "
        "here.*\n"
    )

    # ---- AUDIT 3 ----
    L.append("\n## AUDIT 3 - connector-pane completeness\n")
    L.append(
        f"- FP heaps scanned (with matching BD heap): "
        f"**{a3_counts['fp_heaps_scanned']}**\n"
        f"- empty conpane (0 connected, >=2 wired FP terminals): "
        f"**{a3_counts['empty_conpane']}**\n"
        f"- partial conpane (connected < wired, >=2 wired): "
        f"**{a3_counts['partial_conpane']}**\n"
    )
    L.append(
        "\n| VI | pattern_id | connected slots | total slots "
        "| wired FP terminals | issue |"
    )
    L.append("|---|---|---|---|---|---|")
    for c in sorted(a3, key=lambda x: (x.issue, x.vi))[:200]:
        L.append(
            f"| {c.vi} | {c.pattern_id} | {c.connected_slots} | {c.total_slots} "
            f"| {c.wired_fp_terminals} | {c.issue} |"
        )
    if len(a3) > 200:
        L.append(f"\n*(showing first 200 of {len(a3)} flagged VIs)*")
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "-o",
        "--output",
        default=str(ROOT / ".tmp" / "terminal_order_audit.md"),
        help="markdown report path",
    )
    ap.add_argument(
        "--conpane-limit",
        type=int,
        default=None,
        help="cap number of FP heaps scanned in AUDIT 3 (default: all)",
    )
    ap.add_argument(
        "--skip-conpane",
        action="store_true",
        help="skip AUDIT 3 (the slow full-heap scan)",
    )
    args = ap.parse_args()

    sys.path.insert(0, str(ROOT / "src"))

    specs = load_prim_specs()
    order_sensitive = [s for s in specs if s.classification != "commutative"]
    target_ids = {s.res_id for s in order_sensitive}
    print(
        f"[audit1] {len(specs)} multi-input primitives, "
        f"{len(order_sensitive)} order-sensitive; scanning heaps...",
        file=sys.stderr,
    )
    usages = scan_heaps_for_prims(target_ids)
    a1 = audit1(specs, usages)

    print("[audit2] scanning vilib...", file=sys.stderr)
    a2, a2_counts = audit2()

    if args.skip_conpane:
        a3, a3_counts = (
            [],
            {
                "fp_heaps_scanned": 0,
                "empty_conpane": 0,
                "partial_conpane": 0,
            },
        )
    else:
        print("[audit3] scanning FP heaps...", file=sys.stderr)
        a3, a3_counts = audit3(args.conpane_limit)

    report = render_report(a1, a2, a2_counts, a3, a3_counts, len(order_sensitive))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)

    inversions = [f for f in a1 if f.status == "SUSPECTED_INVERSION"]
    inv_high = [f for f in inversions if f.confidence == "HIGH"]
    inv_other = [f for f in inversions if f.confidence != "HIGH"]
    mismatch = [f for f in a1 if f.status == "INDEX_MAP_MISMATCH"]
    unvalidated = [f for f in a1 if f.status == "UNVALIDATED"]
    print(f"\nReport written to {out}")
    print(f"order-sensitive primitives: {len(order_sensitive)}")
    print(
        "HIGH-confidence inversions: "
        + (", ".join(f"{f.spec.name}({f.spec.res_id})" for f in inv_high) or "none")
    )
    print(
        "lower-confidence inversions: "
        + (", ".join(f"{f.spec.name}({f.spec.res_id})" for f in inv_other) or "none")
    )
    print(
        "index-map mismatch (inconclusive): "
        + (", ".join(f"{f.spec.name}({f.spec.res_id})" for f in mismatch) or "none")
    )
    print(f"unvalidated (no local usage): {len(unvalidated)}")
    print(f"vilib at-risk: {a2_counts['at_risk_same_type']}")
    print(f"empty conpane: {a3_counts['empty_conpane']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
