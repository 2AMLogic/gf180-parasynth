#!/usr/bin/env python3
"""Check the committed route-DRC extract against the committed classification record (#1006).

``route_drc_extract.json`` exists so two claims in ``docs/tool-findings.md``
survive the expiry of their source artefacts (a gitignored ``.klt/`` tree and a
batch bucket with ~30-day object life). The claim numbers travel in the
extract; their inputs stay bound by sha256; this checker enforces every
cross-binding that is checkable inside the repository:

1. the extract's ``drc_report`` and ``metrics`` sha256s are the same ones the
   classification record binds (``route_drc_classification.json`` ``inputs``);
2. the byte-identical pair claim is internally true: both named files carry the
   *same* sha256, and that sha256 is the classification record's
   ``drc_report`` binding;
3. the series' final value, both headline counts, and the classification
   record's ``drc_report.total_records`` are one number (24), so no reading of
   the extract can quietly disagree with the committed classification;
4. the DEF analysis consumed a report byte-identical to the committed
   binding: ``def_site_analysis.json``'s ``drc_report`` sha256 equals the
   extract's ``drc_report`` binding. That equality *is* the docs' "the
   re-run reproduced the original route" sentence -- the re-run's report and
   the original route's report are the same bytes -- so it is machine-checked
   here rather than left as prose.

What defeats this check, stated rather than left to be discovered
(``docs/verification-rules.md`` rule 8): both sides live in the repo, so a
mutation applied to *both* the extract and the classification record
consistently passes. That is inherent -- the artefacts the sha256s name are
not in the tree, so "matches the artefacts" is not checkable here; it *is*
checkable from the operator's copy or the bucket while it lasts, by
re-running ``classify_route_drc.py`` on them. The check this tool makes is
the in-repo half: extract and classification record cannot drift apart
silently, and an injected wrong hash turns it red (``test_route_drc_extract.py``
carries that control).

Exit: ``0`` every check passed; ``1`` at least one failed, with each printed.
A missing file is a failure, not a REFUSED -- the committed pair is supposed
to be here unconditionally.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

EXTRACT = HERE / "route_drc_extract.json"
CLASSIFICATION = HERE / "route_drc_classification.json"
SITE_ANALYSIS = HERE / "def_site_analysis.json"


def verify(
    extract: dict, classification: dict, site_analysis: dict
) -> list[str]:
    """Return one string per failed check; empty list means all passed."""
    problems: list[str] = []

    inputs = classification.get("inputs") or {}
    extract_inputs = extract.get("inputs") or {}

    # 1. sha256 bindings agree with the classification record.
    for name in ("drc_report", "metrics"):
        want = ((inputs.get(name) or {}).get("sha256") or "").strip()
        got = ((extract_inputs.get(name) or {}).get("sha256") or "").strip()
        if not want:
            problems.append(f"classification record binds no sha256 for {name}")
        elif got != want:
            problems.append(
                f"{name} sha256 disagrees: extract {got!r} vs classification {want!r}"
            )

    # 2. the byte-identical pair claim: same sha256 for both named files, and
    #    that sha256 is the classification record's drc_report binding.
    pair = (extract.get("claims") or {}).get("route_pass_pair_byte_identical") or {}
    pair_files = pair.get("files") or []
    pair_sha = (pair.get("sha256") or "").strip()
    if len(pair_files) != 2:
        problems.append(f"pair claim names {len(pair_files)} files, expected 2")
    rpt_binding = ((inputs.get("drc_report") or {}).get("sha256") or "").strip()
    if not pair_sha:
        problems.append("pair claim carries no sha256")
    elif pair_sha != rpt_binding:
        problems.append(
            f"pair sha256 {pair_sha!r} is not the classification record's "
            f"drc_report binding {rpt_binding!r}"
        )
    pass0 = ((extract_inputs.get("drc_report_pass_0") or {}).get("sha256") or "").strip()
    if pass0 != pair_sha:
        problems.append(
            f"pair sha256 {pair_sha!r} != extract's drc_report_pass_0 {pass0!r}"
        )

    # 3. one number everywhere the final violation count is claimed.
    series = (extract.get("claims") or {}).get("per_iteration_violation_series") or {}
    final_value = series.get("final_value")
    headline = (extract.get("claims") or {}).get("headline_counts") or {}
    total = (classification.get("drc_report") or {}).get("total_records")
    values = {
        "extract series final_value": final_value,
        "extract route__drc_errors": headline.get("route__drc_errors"),
        "extract route_pass:0__route__drc_errors": headline.get(
            "route_pass:0__route__drc_errors"
        ),
        "classification total_records": total,
    }
    distinct = {k: v for k, v in values.items() if v is not None}
    if len(distinct) < len(values):
        missing = [k for k, v in values.items() if v is None]
        problems.append(f"final-count claims missing: {missing}")
    if len(set(distinct.values())) > 1:
        problems.append(f"final-count claims disagree: {values}")

    # series final iteration must carry the final value it claims
    series_points = series.get("series") or {}
    final_iter = series.get("final_iteration")
    if final_iter is not None and final_value is not None:
        if str(final_iter) not in series_points:
            problems.append(f"series has no point at final_iteration {final_iter}")
        elif series_points[str(final_iter)] != final_value:
            problems.append(
                f"series[{final_iter}]={series_points[str(final_iter)]} != "
                f"claimed final_value {final_value}"
            )

    # 4. the reproduction claim: the DEF analysis's report is the committed
    #    binding's bytes. If the two records ever disagree, "the re-run
    #    reproduced the original route" is no longer true in-repo.
    rerun_sha = ((site_analysis.get("inputs") or {}).get("drc_report") or {}).get(
        "sha256"
    ) or ""
    rerun_sha = rerun_sha.strip()
    if not rerun_sha:
        problems.append("def_site_analysis record binds no sha256 for drc_report")
    elif rerun_sha != rpt_binding:
        problems.append(
            f"reproduction claim fails: def_site_analysis drc_report "
            f"{rerun_sha!r} != extract's committed binding {rpt_binding!r} "
            f"(the re-run's report is no longer byte-identical to the "
            f"original route's)"
        )

    return problems


def main(argv: list[str] | None = None) -> int:
    extract_path = EXTRACT
    classification_path = CLASSIFICATION
    if argv:
        extract_path = Path(argv[0])
        if len(argv) > 1:
            classification_path = Path(argv[1])

    problems: list[str] = []
    for label, path in (
        ("extract", extract_path),
        ("classification", classification_path),
        ("site analysis", SITE_ANALYSIS),
    ):
        try:
            json.loads(Path(path).read_text())
        except OSError as exc:
            problems.append(f"{label} unreadable: {exc}")
        except json.JSONDecodeError as exc:
            problems.append(f"{label} not JSON: {exc}")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1

    problems = verify(
        json.loads(Path(extract_path).read_text()),
        json.loads(Path(classification_path).read_text()),
        json.loads(SITE_ANALYSIS.read_text()),
    )
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        print(f"route_drc_extract check: {len(problems)} problem(s)")
        return 1
    print("route_drc_extract check: OK (extract and classification record agree)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
