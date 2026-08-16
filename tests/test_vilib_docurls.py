"""Regression lock for the vilib doc_url migration (task #87).

The vilib catalog carried a per-entry `page` field = a page number into the
licensed LabVIEW reference PDF (devs don't have it). That was replaced by a
public `doc_url` into NI's online docs (bundle labview-api-ref), parity with
the primitives.json pdf_page->doc_url swap. These tests pin:
  - the PDF ref is gone (no `page` field survives anywhere),
  - assigned URLs use the canonical public docs form,
  - a few known functions map to their known page,
  - coverage stays high (guards an accidental mass-drop),
  - no vilib doc_url contradicts primitives.json for the same function.
"""

from __future__ import annotations

import json
import re

from lvkit._data import data_dir as _data_dir

VILIB_DIR = _data_dir() / "vilib"
DOC_PREFIX = "https://www.ni.com/docs/en-US/bundle/labview-api-ref/page/"
DOC_RE = re.compile(re.escape(DOC_PREFIX) + r"[\w./-]+\.html$")


def _entry_files():
    return [p for p in sorted(VILIB_DIR.glob("*.json")) if not p.name.startswith("_")]


def _all_entries():
    for jf in _entry_files():
        data = json.loads(jf.read_text(encoding="utf-8"))
        for e in data.get("entries", []):
            yield jf.name, e


def test_no_pdf_page_field_survives():
    """Every `page` PDF ref must be gone from the shipped vilib catalog."""
    offenders = [(f, e.get("name")) for f, e in _all_entries() if "page" in e]
    assert offenders == [], f"stale PDF page refs remain: {offenders[:10]}"


def test_index_source_is_not_pdf():
    idx = json.loads((VILIB_DIR / "_index.json").read_text(encoding="utf-8"))
    assert not idx["source"].endswith(".pdf"), idx["source"]


def test_doc_urls_are_canonical_public_form():
    bad = [
        (f, e.get("name"), e["doc_url"])
        for f, e in _all_entries()
        if e.get("doc_url") and not DOC_RE.match(e["doc_url"])
    ]
    assert bad == [], f"non-canonical doc_urls: {bad[:10]}"


def test_known_boolean_docurls():
    want = {
        "And Function": DOC_PREFIX + "functions/and.html",
        "Or Function": DOC_PREFIX + "functions/or.html",
        "Not Function": DOC_PREFIX + "functions/not.html",
        "Exclusive Or Function": DOC_PREFIX + "functions/exclusive-or.html",
    }
    got = {
        e["name"]: e.get("doc_url") for _f, e in _all_entries() if e.get("name") in want
    }
    for name, url in want.items():
        assert got.get(name) == url, f"{name}: {got.get(name)!r} != {url!r}"


def test_coverage_stays_high():
    """>=85% of entries should carry a doc_url; guards an accidental mass-drop.
    (Uncovered = palette category headers, edge math funcs, ambiguous channel
    ops with no single canonical page.)"""
    entries = list(_all_entries())
    covered = sum(1 for _f, e in entries if e.get("doc_url"))
    assert covered / len(entries) >= 0.85, f"only {covered}/{len(entries)} covered"


def test_docurl_field_loads_on_model():
    from lvkit.vilib_resolver import VIEntry

    url = DOC_PREFIX + "functions/and.html"
    e = VIEntry.model_validate({"name": "X", "doc_url": url})
    assert e.doc_url == url
    assert not hasattr(e, "page")


def test_enriched_terminals_are_well_formed():
    """docs_enriched entries (task #88) carry doc-derived connector panes:
    every terminal has a verbatim name, an in/out direction, a python_param,
    and NO fabricated index (indices are learned at resolve-time by name-match,
    never invented from doc order)."""
    bad = []
    for _f, e in _all_entries():
        if e.get("status") != "docs_enriched":
            continue
        for t in e.get("terminals", []):
            if (
                not t.get("name")
                or t.get("direction") not in ("in", "out")
                or not t.get("python_param")
                or t.get("index") is not None
            ):
                bad.append((e.get("name"), t))
    assert bad == [], f"malformed enriched terminals: {bad[:10]}"


def test_enrichment_did_not_clobber_resolved_entries():
    """Entries that already had learned indices keep them (never overwritten
    by a doc pull)."""
    indexed = [
        e.get("name")
        for _f, e in _all_entries()
        if any(t.get("index") is not None for t in e.get("terminals", []))
    ]
    # the resolved/observed entries survive; none became docs_enriched
    for _f, e in _all_entries():
        if any(t.get("index") is not None for t in e.get("terminals", [])):
            assert e.get("status") != "docs_enriched", e.get("name")
    assert indexed, "expected some entries to retain learned indices"


def test_and_function_enriched_to_correct_pane():
    """And Function was mislabeled (only its output terminal); the doc pull
    fixes it to x, y inputs + the AND output."""
    entry = next(
        (e for _f, e in _all_entries() if e.get("name") == "And Function"), None
    )
    assert entry is not None
    ins = [t["name"] for t in entry["terminals"] if t["direction"] == "in"]
    outs = [t["name"] for t in entry["terminals"] if t["direction"] == "out"]
    assert ins == ["x", "y"], ins
    assert outs == ["x .and. y?"], outs


def test_vilib_docurls_agree_with_primitives():
    """A vilib entry and a primitive that resolve to the same NI function name
    must not point at contradictory doc pages."""
    prims = json.loads((_data_dir() / "primitives.json").read_text(encoding="utf-8"))
    prim_by_name = {
        p["name"]: p["doc_url"]
        for p in prims["primitives"].values()
        if p.get("name") and p.get("doc_url")
    }

    def base(n: str) -> str:
        return re.sub(r"\s+(Function|VI)$", "", n).strip().lower()

    prim_by_base = {base(n): u for n, u in prim_by_name.items()}
    conflicts = []
    for _f, e in _all_entries():
        du = e.get("doc_url")
        if not du:
            continue
        pu = prim_by_base.get(base(e.get("name", "")))
        if pu and pu != du:
            conflicts.append((e["name"], du, pu))
    assert conflicts == [], f"vilib/primitive doc_url conflicts: {conflicts[:10]}"
