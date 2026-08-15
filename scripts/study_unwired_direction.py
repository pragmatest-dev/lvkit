"""Corpus study — which objFlags source predicts an UNWIRED terminal's
TRUE direction?

Read-only analysis. Does NOT modify any parser/core code and makes no
commits; it only harvests corpus evidence and writes a report.

Investigates the suspected bug at ``src/lvkit/parser/vi.py:667``:

    combined_flags = term_flags | dco_flags
    is_output = is_output_terminal(combined_flags)   # bit 0: 0x1

for UNWIRED primitive terminals. Four hypotheses are compared against
ground truth harvested from WIRED instances of the same (primResID, index)
slot elsewhere in the corpus (a primitive terminal's direction is fixed per
connector-pane index):

    term_only    = bool(term_flags & 1)
    dco_only     = bool(dco_flags & 1)
    term_or_dco  = bool((term_flags | dco_flags) & 1)   # current behavior
    term_and_dco = bool((term_flags & dco_flags) & 1)

Method (see task): grep the pre-extracted ``*_BDHb.xml`` cache for
``<primResID>N</primResID>`` per project rule (never parse the whole
corpus), harvest up to ``CAP`` instances per primResID, replicate vi.py's
terminal-direction/flags extraction exactly (same wire-source/sink
membership test, same raw ``objFlags`` reads), then compare.

Usage::

    uv run python scripts/study_unwired_direction.py
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lvkit.cache_paths import global_cache_root
from lvkit.parser.constants import TERMINAL_CLASS
from lvkit.parser.utils import safe_int
from lvkit.tools.connector_geometry_profile import (
    build_primresid_index,
    load_primitive_entries,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRIMITIVES_JSON = REPO_ROOT / "src" / "lvkit" / "data" / "primitives.json"
REPORT_PATH = REPO_ROOT / "outputs" / "unwired_direction_flag_study.md"

# Per-primResID corpus-instance cap. Raised from the module default (5) used
# by connector_geometry_profile to widen ground-truth coverage, per task
# instructions — still capped, never a full-corpus sweep.
CAP = 15

PRIM_ELEM_CLASS = "prim"


# ---------------------------------------------------------------------------
# Typed observation record (project rule: dataclasses, not raw dicts)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TerminalObservation:
    """One primitive terminal instance: replicates vi.py's per-terminal
    wire-membership + raw-flags extraction (src/lvkit/parser/vi.py, the loop
    around lines 587-668) for ONE (primResID, index) slot in ONE gathered
    corpus file."""

    prim_res_id: int
    source_file: str
    node_uid: str
    term_uid: str
    index: int
    wired: bool
    truth_is_output: bool | None  # None when unwired
    term_flags: int
    dco_flags: int

    @property
    def slot(self) -> tuple[int, int]:
        return (self.prim_res_id, self.index)


# ---------------------------------------------------------------------------
# Harvesting — replicates vi.py's extraction exactly, read-only
# ---------------------------------------------------------------------------
def _extract_wire_membership(root: ET.Element) -> tuple[set[str], set[str]]:
    """Same wire-source/sink derivation as vi.py's ``_extract_wires`` +
    ``_extract_terminal_info`` (signalList -> first term = source, rest =
    sinks; see vi.py lines 506-527 and 799-801)."""
    sources: set[str] = set()
    sinks: set[str] = set()
    for sig in root.findall(".//signalList/SL__arrayElement[@class='signal']"):
        terms = [
            t_uid
            for t in sig.findall("termList/SL__arrayElement")
            if (t_uid := t.get("uid"))
        ]
        if len(terms) >= 2:
            sources.add(terms[0])
            sinks.update(terms[1:])
    return sources, sinks


def _terminal_index(dco: ET.Element | None) -> int:
    """Replicates vi.py's ``parm_index`` resolution (lines 600-655) for
    ``elem_class == "prim"`` terminals ONLY. primitives.json's "primitives"
    entries are always ``class="prim"`` nodes with a direct ``<primResID>``
    child -- never one of the specialized node_types classes keyed in
    vi.py's ``_NODE_DCO_MAP`` (aDelete, aIndx, ...), so that branch (lines
    624-648) never applies here and is intentionally not replicated. "prim"
    IS in vi.py's default-0 list (line 610-614), so a missing parmIndex/
    paramIdx resolves to 0, matching vi.py exactly for this node class.
    """
    if dco is not None:
        for idx_field in ("parmIndex", "paramIdx"):
            idx_elem = dco.find(idx_field)
            if idx_elem is not None and idx_elem.text:
                return int(idx_elem.text)
        return 0
    return 0


def harvest_instances(
    prim_res_id: int, files: list[Path], cap: int
) -> list[TerminalObservation]:
    """Parse up to ``cap`` grep-matched files and extract every terminal of
    every ``class="prim"`` node whose ``<primResID>`` == ``prim_res_id``.
    Each file's tree is dropped before the next (never hold more than one
    parsed file at a time -- project rule against corpus-wide sweeps)."""
    observations: list[TerminalObservation] = []
    for f in files[:cap]:
        try:
            tree = ET.parse(f)
        except ET.ParseError:
            continue
        root = tree.getroot()
        wire_sources, wire_sinks = _extract_wire_membership(root)

        for elem in root.iter():
            if elem.get("class") != PRIM_ELEM_CLASS:
                continue
            pr_elem = elem.find("primResID")
            if pr_elem is None or not pr_elem.text:
                continue
            try:
                if int(pr_elem.text) != prim_res_id:
                    continue
            except ValueError:
                continue
            node_uid = elem.get("uid") or ""

            term_list = elem.findall(
                f"./termList/SL__arrayElement[@class='{TERMINAL_CLASS}']"
            )
            for term in term_list:
                term_uid = term.get("uid")
                if not term_uid:
                    continue
                dco = term.find("dco")
                index = _terminal_index(dco)

                if term_uid in wire_sources:
                    wired, truth = True, True
                elif term_uid in wire_sinks:
                    wired, truth = True, False
                else:
                    wired, truth = False, None

                term_flags = safe_int(term.find("objFlags"))
                dco_flags_elem = dco.find("objFlags") if dco is not None else None
                dco_flags = safe_int(dco_flags_elem)

                observations.append(
                    TerminalObservation(
                        prim_res_id=prim_res_id,
                        source_file=f.name,
                        node_uid=node_uid,
                        term_uid=term_uid,
                        index=index,
                        wired=wired,
                        truth_is_output=truth,
                        term_flags=term_flags,
                        dco_flags=dco_flags,
                    )
                )
        del tree, root
    return observations


# ---------------------------------------------------------------------------
# Hypotheses
# ---------------------------------------------------------------------------
def hyp_term_only(o: TerminalObservation) -> bool:
    return bool(o.term_flags & 1)


def hyp_dco_only(o: TerminalObservation) -> bool:
    return bool(o.dco_flags & 1)


def hyp_term_or_dco(o: TerminalObservation) -> bool:
    """Current vi.py:667-668 behavior."""
    return bool((o.term_flags | o.dco_flags) & 1)


def hyp_term_and_dco(o: TerminalObservation) -> bool:
    return bool((o.term_flags & o.dco_flags) & 1)


HYPOTHESES: dict[str, Callable[[TerminalObservation], bool]] = {
    "term_only": hyp_term_only,
    "dco_only": hyp_dco_only,
    "term_or_dco (current)": hyp_term_or_dco,
    "term_and_dco": hyp_term_and_dco,
}


@dataclass(frozen=True)
class HypothesisScore:
    name: str
    correct: int
    total: int

    @property
    def pct(self) -> float:
        return 100.0 * self.correct / self.total if self.total else 0.0


def score_hypotheses(
    observations: list[TerminalObservation],
    truth_of: Callable[[TerminalObservation], bool],
) -> list[HypothesisScore]:
    scores = []
    for name, fn in HYPOTHESES.items():
        correct = sum(1 for o in observations if fn(o) == truth_of(o))
        scores.append(HypothesisScore(name, correct, len(observations)))
    return sorted(scores, key=lambda s: -s.pct)


@dataclass(frozen=True)
class DisagreementCase:
    prim_res_id: int
    index: int
    term_flags: int
    dco_flags: int
    truth: bool
    predictions: dict[str, bool]


def format_score_table(scores: list[HypothesisScore]) -> str:
    lines = ["| Hypothesis | Correct | Total | Accuracy |", "|---|---|---|---|"]
    for s in scores:
        lines.append(f"| {s.name} | {s.correct} | {s.total} | {s.pct:.1f}% |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> None:
    entries = load_primitive_entries(PRIMITIVES_JSON)
    target_ids = set(entries.keys())
    cache_root = global_cache_root()
    print(f"primResIDs in primitives.json: {len(target_ids)}")
    print(f"Cache root: {cache_root}")

    file_index = build_primresid_index(cache_root, target_ids)

    all_observations: list[TerminalObservation] = []
    prims_covered = 0
    prims_absent_from_corpus = 0
    for pid in sorted(target_ids):
        files = file_index.get(pid, [])
        if not files:
            prims_absent_from_corpus += 1
            continue
        prims_covered += 1
        obs = harvest_instances(pid, files, CAP)
        all_observations.extend(obs)
        del obs
    print(
        f"primResIDs with >=1 corpus match: {prims_covered} "
        f"(absent from corpus: {prims_absent_from_corpus})"
    )

    node_instances = {
        (o.prim_res_id, o.source_file, o.node_uid) for o in all_observations
    }
    print(f"primitive node instances harvested: {len(node_instances)}")
    print(f"terminal observations harvested: {len(all_observations)}")

    # --- Ground truth per (primResID, index) from WIRED terminals --------
    wired_truths: dict[tuple[int, int], set[bool]] = defaultdict(set)
    for o in all_observations:
        if o.wired:
            assert o.truth_is_output is not None
            wired_truths[o.slot].add(o.truth_is_output)

    clean_truth: dict[tuple[int, int], bool] = {}
    inconsistent_slots: dict[tuple[int, int], set[bool]] = {}
    for slot, truths in wired_truths.items():
        if len(truths) == 1:
            clean_truth[slot] = next(iter(truths))
        else:
            inconsistent_slots[slot] = truths

    # --- Test set: UNWIRED terminals whose slot has clean ground truth ---
    test_set = [o for o in all_observations if not o.wired and o.slot in clean_truth]
    wired_obs = [o for o in all_observations if o.wired]

    print(f"clean ground-truth slots: {len(clean_truth)}")
    print(f"inconsistent slots excluded: {len(inconsistent_slots)}")
    print(f"unwired test terminals (with ground truth): {len(test_set)}")

    def test_truth(o: TerminalObservation) -> bool:
        return clean_truth[o.slot]

    def wired_truth(o: TerminalObservation) -> bool:
        assert o.truth_is_output is not None
        return o.truth_is_output

    headline_scores = score_hypotheses(test_set, test_truth)
    wired_scores = score_hypotheses(wired_obs, wired_truth)

    print("\n=== HEADLINE: accuracy on UNWIRED test terminals ===")
    print(format_score_table(headline_scores))
    print("\n=== SANITY: accuracy on WIRED terminals (self-check) ===")
    print(format_score_table(wired_scores))

    # --- Disagreement cases: where current (term_or_dco) is WRONG ---------
    seen: set[tuple[int, int, int, int]] = set()
    disagreements: list[DisagreementCase] = []
    for o in test_set:
        truth = clean_truth[o.slot]
        if hyp_term_or_dco(o) == truth:
            continue
        key = (o.prim_res_id, o.index, o.term_flags, o.dco_flags)
        if key in seen:
            continue
        seen.add(key)
        disagreements.append(
            DisagreementCase(
                prim_res_id=o.prim_res_id,
                index=o.index,
                term_flags=o.term_flags,
                dco_flags=o.dco_flags,
                truth=truth,
                predictions={name: fn(o) for name, fn in HYPOTHESES.items()},
            )
        )

    write_report(
        entries=entries,
        prims_covered=prims_covered,
        prims_absent_from_corpus=prims_absent_from_corpus,
        node_instances=len(node_instances),
        clean_truth=clean_truth,
        inconsistent_slots=inconsistent_slots,
        test_set=test_set,
        wired_obs=wired_obs,
        headline_scores=headline_scores,
        wired_scores=wired_scores,
        disagreements=disagreements,
    )
    print(f"\nReport written to {REPORT_PATH}")


def _direction(b: bool) -> str:
    return "out" if b else "in"


def write_report(
    *,
    entries: dict,
    prims_covered: int,
    prims_absent_from_corpus: int,
    node_instances: int,
    clean_truth: dict[tuple[int, int], bool],
    inconsistent_slots: dict[tuple[int, int], set[bool]],
    test_set: list[TerminalObservation],
    wired_obs: list[TerminalObservation],
    headline_scores: list[HypothesisScore],
    wired_scores: list[HypothesisScore],
    disagreements: list[DisagreementCase],
) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append("# Unwired-terminal direction-flag corpus study")
    lines.append("")
    lines.append(
        "Investigates `src/lvkit/parser/vi.py:667` "
        "(`combined_flags = term_flags | dco_flags`) for UNWIRED primitive "
        "terminals: which objFlags source best predicts the terminal's "
        "TRUE direction?"
    )
    lines.append("")
    lines.append("## Sample sizes")
    lines.append("")
    lines.append(f"- primResIDs in primitives.json: {len(entries)}")
    lines.append(f"- primResIDs with >=1 corpus instance: {prims_covered}")
    lines.append(f"- primResIDs absent from local corpus: {prims_absent_from_corpus}")
    lines.append(f"- per-primResID instance cap: {CAP}")
    lines.append(f"- primitive node instances harvested: {node_instances}")
    lines.append(
        f"- (primResID, index) slots with clean wired ground truth: {len(clean_truth)}"
    )
    lines.append(
        f"- (primResID, index) slots EXCLUDED as inconsistent "
        f"(wired instances disagree): {len(inconsistent_slots)}"
    )
    lines.append(f"- unwired test terminals (ground truth available): {len(test_set)}")
    lines.append(f"- wired terminals (sanity cross-check set): {len(wired_obs)}")
    lines.append("")

    lines.append("## Headline: accuracy on UNWIRED test terminals")
    lines.append("")
    lines.append(
        "This is the set that decides the fix: unwired terminals whose "
        "(primResID, index) slot has unambiguous ground truth from >=1 "
        "wired instance elsewhere in the corpus."
    )
    lines.append("")
    lines.append(format_score_table(headline_scores))
    lines.append("")

    lines.append("## Sanity cross-check: accuracy on WIRED terminals")
    lines.append("")
    lines.append(
        "Same four hypotheses evaluated against each WIRED terminal's own "
        "(directly known) direction. Tells us whether ANY flag source is "
        "reliable at all, independent of the unwired-fallback question."
    )
    lines.append("")
    lines.append(format_score_table(wired_scores))
    lines.append("")

    lines.append("## Disagreement cases (current `term_or_dco` is WRONG)")
    lines.append("")
    if not disagreements:
        lines.append(
            "None found — `term_or_dco` matched ground truth on every "
            "unwired test terminal in this corpus sample."
        )
    else:
        lines.append(
            "One row per distinct (primResID, index, term_flags, dco_flags) "
            "combination where the CURRENT formula (`term_or_dco`) predicts "
            "the wrong direction. `T`=truth, `Y`=hypothesis says output, "
            "`N`=hypothesis says input."
        )
        lines.append("")
        header = (
            "| primResID | name | index | term_flags | dco_flags | truth | "
            + " | ".join(HYPOTHESES.keys())
            + " |"
        )
        sep = "|---" * (6 + len(HYPOTHESES)) + "|"
        lines.append(header)
        lines.append(sep)
        for d in sorted(disagreements, key=lambda d: (d.prim_res_id, d.index)):
            name = entries[d.prim_res_id].name if d.prim_res_id in entries else "?"
            preds = " | ".join(_direction(d.predictions[h]) for h in HYPOTHESES)
            lines.append(
                f"| {d.prim_res_id} | {name} | {d.index} | "
                f"0x{d.term_flags:x} | 0x{d.dco_flags:x} | "
                f"{_direction(d.truth)} | {preds} |"
            )
        lines.append("")
        if any(d.prim_res_id == 1538 for d in disagreements):
            lines.append(
                "Includes the Search/Split String (primResID 1538) case "
                "that motivated this study."
            )
            lines.append("")

    lines.append("## Recommendation")
    lines.append("")
    best = headline_scores[0]
    all_100 = [s for s in headline_scores if s.total > 0 and s.correct == s.total]
    if best.total == 0:
        lines.append(
            "Inconclusive — no unwired test terminals with ground truth "
            "were harvested from the local corpus. Re-run with a higher "
            "CAP or after pulling more sample VIs."
        )
    elif len(all_100) == 1:
        lines.append(
            f"**Adopt `{all_100[0].name}`** at vi.py:668 — it is the only "
            f"hypothesis with 100% accuracy ({all_100[0].correct}/"
            f"{all_100[0].total}) on the unwired ground-truth test set."
        )
    elif len(all_100) > 1:
        lines.append(
            "Multiple hypotheses tie at 100% accuracy on this corpus "
            f"sample: {', '.join(s.name for s in all_100)}. See the "
            "disagreement table (empty in this run) — cannot "
            "distinguish them from this evidence alone; prefer the "
            "narrowest (most conservative) one, `term_and_dco`, unless a "
            "future counterexample favors another."
        )
    else:
        lines.append(
            f"Best observed: `{best.name}` at {best.pct:.1f}% "
            f"({best.correct}/{best.total}) — no hypothesis reaches 100%, "
            "so treat this as the strongest available signal rather than "
            "a fully reliable fix. See disagreement table above."
        )

    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
