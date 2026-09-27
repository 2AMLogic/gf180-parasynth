#!/usr/bin/env python3
"""Score one F1 cutoff-response case on the RTL filter chain (`integrated-rtl`).

    tools/score_f1_rtl.py --case F1A                     # the board record
    tools/score_f1_rtl.py --inject F1_CHAIN_DROP_DECIM \
        --results build/scorecard/f1-rtl-control         # the same case, defect compiled in

Both run the whole stepped-tone train twice (the commanded cutoff and the
wide-open reference), about twenty minutes of iverilog. The CHEAP control is
`tools/f1_rtl_filter_path.py --inject ... --expect-mismatch --frames 30000`,
which compares the streams without scoring a case; `make controls` runs that
one.

WHY THIS EXISTS. #94 asked for four `integrated-rtl` anchors -- one drum, one
mono, one filter, one ensemble -- because every scorecard case said
`fixed-model`, so nothing on the board had been measured on the instrument that
ships. `M5A` landed (`tools/score_m5a_i2s.py`); the Filters family had no anchor
at all. This scorer is that anchor for F1: the stepped tone goes through
`rate_conv_2x.v` + `ladder_dp_n.v` under iverilog
(`tools/f1_rtl_filter_path.py`), and every dB it scores is projected from words
that came out of vvp.

HOW IT AVOIDS SCORING ITSELF A DIFFERENT WAY. The metrics, the frozen Surge
reference, the estimators, the tolerance policy and the probe grid all come from
`run_case.run_filter_case`, the same function that produced the `fixed-model`
twin -- it takes the filter path as an argument. A second scoring path would
have been free to drift from the twin, and then a difference between the two
engines could not be told from a difference between two scorers.

WHAT IT REPORTS THAT #94 ASKED FOR EXPLICITLY. The twin's metric values are read
off the board BEFORE this record replaces them and land on the new record under
`engine_comparison`, together with the board state each engine produces. If the
RTL disagrees -- either in the raw sample stream or in a scored metric -- the
difference is printed and recorded rather than silently overwritten, because
#94's own instruction is that a disagreement means every model row on the board
needs re-reading.

Exit: 0 the case was measured and passes, 1 it was measured and fails,
2 REFUSED -- no measurement (a refusal never writes a record).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (ROOT / "model", ROOT / "tools", ROOT / "tools" / "probes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import f1_rtl_filter_path as rtlpath   # noqa: E402
import run_case as rc                  # noqa: E402
import scorecard                       # noqa: E402

CASES = ("F1A", "F1B", "F1C")
#: How far a scored metric may move between the two engines before this tool
#: calls it a disagreement in its own right. The raw stream comparison is exact;
#: this is for the projected numbers, whose last digits are float arithmetic.
METRIC_SAME = 1e-4


def compare_with_twin(case: dict, twin: dict | None, new: dict) -> dict:
    """What the two engines say about the same case, metric by metric."""
    if twin is None:
        return {"twin_present": False,
                "why": "no earlier record for this case: nothing to compare with"}
    before = scorecard.evaluate(case, twin)
    after = scorecard.evaluate(case, new)
    metrics = {}
    for name, m_new in (new.get("metrics") or {}).items():
        m_old = (twin.get("metrics") or {}).get(name) or {}
        v_old, v_new = m_old.get("value"), m_new.get("value")
        delta = (None if v_old is None or v_new is None else
                 round(float(v_new) - float(v_old), 6))
        metrics[name] = {
            "twin_value": v_old, "rtl_value": v_new, "units": m_new.get("units"),
            "reference": m_new.get("reference"), "delta": delta,
            "twin_error": m_old.get("error"), "rtl_error": m_new.get("error"),
            "same": bool(delta is not None and abs(delta) <= METRIC_SAME),
        }
    moved = sorted(n for n, m in metrics.items() if not m["same"])
    return {"twin_present": True,
            "twin_engine": twin.get("engine"),
            "twin_source_commit": twin.get("source_commit"),
            "twin_analysis_run": twin.get("analysis_run"),
            "twin_state": before["state"], "rtl_state": after["state"],
            "twin_worst": before["worst"], "rtl_worst": after["worst"],
            "state_changed": before["state"] != after["state"],
            "metrics": metrics,
            "metrics_that_moved": moved,
            "agrees": bool(not moved and before["state"] == after["state"])}


def stream_agreement(record: dict) -> dict:
    """The raw model-versus-RTL comparison, gathered from every curve."""
    curves = ((record.get("model_path") or {}).get("rtl_chain") or {}).get("curves") or []
    per = [{"tag": c["tag"], "cut_hz": c["cut_hz"]} | c["rtl_vs_model"] for c in curves]
    return {"curves": per,
            "bit_exact": bool(per) and all(c["bit_exact"] for c in per),
            "total_frames": sum(c["frames"] for c in per),
            "total_mismatches": sum(c["mismatches"] for c in per)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", choices=CASES, default="F1A")
    ap.add_argument("--inject", choices=rtlpath.INJECTS, default=None,
                    help="compile a defect into the RTL. A control, never evidence: it "
                         "refuses to write into the board's results.")
    ap.add_argument("--results", default=None,
                    help=f"where the record goes (default {rc.RESULTS.relative_to(ROOT)})")
    ap.add_argument("--no-audio", action="store_true",
                    help="do not write the response-curve artefact")
    ap.add_argument("--allow-stale", action="store_true",
                    help="run even though this tree is behind origin/main, and say so "
                         "on the record it writes")
    ap.add_argument("--timeout-s", type=float, default=14400.0)
    a = ap.parse_args(argv)

    outdir = pathlib.Path(a.results) if a.results else rc.RESULTS
    if not outdir.is_absolute():
        outdir = ROOT / outdir
    on_board = outdir.resolve() == rc.RESULTS.resolve()
    if on_board and a.inject:
        print("score_f1_rtl: REFUSED -- an injected control must not write into "
              "docs/scorecard/results; pass --results", file=sys.stderr)
        return 2

    # The premise of the run, asserted before any of it runs (run_case.base_check).
    try:
        rc.BASE_STATE = rc.base_check(a.allow_stale)
    except rc.StaleBase as exc:
        print(f"score_f1_rtl: REFUSED -- {exc}", file=sys.stderr)
        return 2
    if rc.BASE_STATE.get("problems"):
        print(f"WARNING (--allow-stale): {'; '.join(rc.BASE_STATE['problems'])}")
    elif rc.BASE_STATE.get("note"):
        print(f"base: {rc.BASE_STATE['note']}")

    case = next((c for c in rc.load_cases() if c["case_id"] == a.case), None)
    if case is None:
        print(f"score_f1_rtl: REFUSED -- no such case {a.case}", file=sys.stderr)
        return 2

    dest = outdir / f"{a.case}.json"
    twin = None
    board_record = rc.RESULTS / f"{a.case}.json"
    if board_record.is_file():
        try:
            twin = json.loads(board_record.read_text())
        except ValueError as exc:
            print(f"score_f1_rtl: REFUSED -- the board record for {a.case} is not "
                  f"readable JSON, so a disagreement could not be reported: {exc}",
                  file=sys.stderr)
            return 2

    try:
        path = rtlpath.RtlFilterChainPath(inject=a.inject, timeout_s=a.timeout_s)
        print(f"score_f1_rtl: {a.case} on {rtlpath.ENGINE} "
              f"({path.tools['iverilog']}); this runs two RTL curves")
        record = rc.run_filter_case(case, "", keep_audio=not a.no_audio,
                                    engine_path=path, engine=rtlpath.ENGINE,
                                    artifact_tag="-rtl")
    except (rtlpath.Refused, rc.Refused) as exc:
        print(f"score_f1_rtl: REFUSED -- {exc}", file=sys.stderr)
        return 2

    agreement = stream_agreement(record)
    comparison = compare_with_twin(case, twin, record)
    record["engine_comparison"] = {
        "why": ("#94: an integrated-rtl reading of a case whose twin is fixed-model. A "
                "disagreement means every model row on the board needs re-reading, so "
                "both readings are on this record rather than one overwriting the other."),
        "rtl_vs_model_samples": agreement,
        "rtl_vs_model_metrics": comparison,
        "controls": {
            "exercised_by_this_stimulus": list(rtlpath.INJECTS_EXERCISED),
            "not_exercised_by_this_stimulus": rtlpath.INJECTS_NOT_EXERCISED,
            "why": ("which RTL injections this stimulus reaches was measured, not assumed: "
                    "at resonance 0 the ladder's feedback term is multiplied by zero and at "
                    "-12 dBFS nothing saturates, so the arithmetic-corner controls cannot "
                    "fire here and the composition controls are the ones that do."),
        },
    }
    record["provenance"]["config"]["rtl_injection"] = a.inject
    record["provenance"]["inputs"]["rtl:tb_f1_chain"] = (
        "sha256:" + hashlib.sha256((ROOT / "rtl-sketch/tb_f1_chain.v").read_bytes()).hexdigest()[:16])
    record["provenance"]["inputs"]["rtl:ladder_dp_n"] = (
        "sha256:" + hashlib.sha256((ROOT / "rtl-sketch/ladder_dp_n.v").read_bytes()).hexdigest()[:16])
    record["provenance"]["inputs"]["rtl:rate_conv_2x"] = (
        "sha256:" + hashlib.sha256((ROOT / "rtl-sketch/rate_conv_2x.v").read_bytes()).hexdigest()[:16])
    record["source_dirty"] = bool(subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True,
        text=True).stdout.strip())
    if a.inject:
        record["INJECTED_CONTROL"] = f"RTL:{a.inject}"

    if record["engine"] != rtlpath.ENGINE:
        print(f"score_f1_rtl: REFUSED -- the record names engine {record['engine']!r}, "
              f"not {rtlpath.ENGINE!r}", file=sys.stderr)
        return 2
    verdict = scorecard.evaluate(case, record)
    if verdict["state"] not in (scorecard.PASS, scorecard.FAIL):
        print(f"score_f1_rtl: REFUSED -- measurement is not scoreable: {verdict['why']}",
              file=sys.stderr)
        return 2
    record["provenance"]["outcome_code"] = rc.OUTCOME_CODE[verdict["state"]]

    print(f"  RTL vs model samples: "
          f"{'BIT-EXACT' if agreement['bit_exact'] else 'DIFFERS'} "
          f"({agreement['total_mismatches']} of {agreement['total_frames']} frames)")
    for name, m in (comparison.get("metrics") or {}).items():
        mark = "same" if m["same"] else "MOVED"
        print(f"  {name:<18} rtl {m['rtl_value']} vs fixed-model {m['twin_value']} "
              f"({mark}), reference {m['reference']}")
    if comparison.get("twin_present") and not comparison["agrees"]:
        print("  DISAGREEMENT: the integrated-rtl reading differs from the fixed-model "
              "twin. #94: every model row on the board needs re-reading.")
    print(f"  board verdict: {verdict['state']}"
          + (f" (worst {verdict['worst']:.2f})" if verdict["worst"] is not None else ""))

    outdir.mkdir(parents=True, exist_ok=True)
    if on_board:
        rc.carry_rubric_history(case, dest, record)
    dest.write_text(json.dumps(record, indent=2, sort_keys=False) + "\n")
    print(f"score_f1_rtl: wrote {dest.relative_to(ROOT) if dest.is_relative_to(ROOT) else dest}")
    if a.inject:
        print("score_f1_rtl: this record is a CONTROL, not evidence")
    return 0 if verdict["state"] == scorecard.PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
