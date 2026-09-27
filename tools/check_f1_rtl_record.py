#!/usr/bin/env python3
"""Gate: an F1 board record that claims `integrated-rtl` must have come from the RTL.

    tools/check_f1_rtl_record.py                        # the board's F1 anchor
    tools/check_f1_rtl_record.py docs/scorecard/results/F1A.json

WHY. `engine` is a free-text field on a result record, and the whole point of
#94's anchors is that the label is load-bearing: a reader of `docs/scorecard/BOARD.md`
uses it to decide whether a row was measured on the instrument or on the model.
A record that says `integrated-rtl` because someone typed it is worse than a
record that says `fixed-model` honestly. So this gate refuses anything that

  * does not name `integrated-rtl` (the check `tools/test_check_f1_rtl_record.py`
    exercises first, and the one the issue asked for);
  * lacks the `model_path.rtl_chain` block naming the bench, the modules, the
    simulator and its version;
  * carries RTL source hashes that do not match the tree it is being checked in
    -- a record produced by a bench that has since changed is history, not
    evidence about this tree;
  * records no per-curve model-versus-RTL stream comparison, or fewer than the
    two curves an F1 case needs (the commanded cutoff and the wide-open plateau);
  * was produced by a truncated stimulus or with an RTL defect compiled in;
  * omits the `engine_comparison` block that #94 requires -- the fixed-model
    twin's reading, so a disagreement is on the record and not overwritten.

Exit: 0 the record is what it says it is, 1 it is not, 2 it could not be read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

ENGINE = "integrated-rtl"
DEFAULT_RECORD = ROOT / "docs/scorecard/results/F1A.json"
#: The sources whose bytes decide what an RTL reading means. Checked against the
#: tree, not merely present.
BOUND_SOURCES = ("rtl-sketch/tb_f1_chain.v", "rtl-sketch/rate_conv_2x.v",
                 "rtl-sketch/ladder_dp_n.v", "rtl-sketch/tanh16.hex")
REQUIRED_CURVES = 2


class Rejected(RuntimeError):
    """The record does not support the claim it makes."""


def _sha(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def check_record(record: dict, *, root: pathlib.Path = ROOT) -> dict:
    """Return a summary, or raise `Rejected` with the reason."""
    if record.get("engine") != ENGINE:
        raise Rejected(f"engine is {record.get('engine')!r}, not {ENGINE!r}: this record "
                       f"is not an RTL anchor")
    if (record.get("provenance") or {}).get("engine") != ENGINE:
        raise Rejected(f"provenance names engine "
                       f"{(record.get('provenance') or {}).get('engine')!r} while the "
                       f"record claims {ENGINE!r}")
    case_id = record.get("case_id")
    if case_id not in ("F1A", "F1B", "F1C"):
        raise Rejected(f"case {case_id!r} is not an F1 cutoff-response case")
    if record.get("INJECTED_CONTROL"):
        raise Rejected(f"produced with {record['INJECTED_CONTROL']} compiled in: a control, "
                       f"not evidence")
    if record.get("SMOKE_RUN"):
        raise Rejected(f"produced by a smoke run ({record['SMOKE_RUN']})")

    chain = (record.get("model_path") or {}).get("rtl_chain")
    if not isinstance(chain, dict):
        raise Rejected("no model_path.rtl_chain block: nothing says which RTL ran")
    if chain.get("rtl_injection"):
        raise Rejected(f"the RTL chain carried injection {chain['rtl_injection']!r}")
    if chain.get("frames_limit"):
        raise Rejected(f"the stimulus was truncated to {chain['frames_limit']} frames")
    if chain.get("simulator") not in ("iverilog", "verilator"):
        raise Rejected(f"simulator {chain.get('simulator')!r} is not a known RTL simulator")
    versions = chain.get("simulator_versions")
    if not isinstance(versions, dict) or not versions.get(chain["simulator"]):
        raise Rejected("the record does not say which simulator build produced it")
    if not chain.get("bench"):
        raise Rejected("the record does not name the bench that composed the chain")

    hashes = chain.get("source_sha256")
    if not isinstance(hashes, dict):
        raise Rejected("the RTL chain block carries no source hashes")
    drifted = []
    for rel in BOUND_SOURCES:
        if rel not in hashes:
            raise Rejected(f"no recorded hash for {rel}")
        actual = _sha(root / rel)
        if hashes[rel] != actual:
            drifted.append(f"{rel} (record {hashes[rel]}, tree {actual})")
    if drifted:
        raise Rejected("the RTL has changed since this record was produced: "
                       + "; ".join(drifted))

    curves = chain.get("curves")
    if not isinstance(curves, list) or len(curves) < REQUIRED_CURVES:
        raise Rejected(f"{0 if not isinstance(curves, list) else len(curves)} curves "
                       f"recorded; an F1 case needs {REQUIRED_CURVES} "
                       f"(commanded cutoff and wide open)")
    frames = mismatches = 0
    for curve in curves:
        agree = (curve or {}).get("rtl_vs_model")
        if not isinstance(agree, dict) or "mismatches" not in agree or not agree.get("frames"):
            raise Rejected(f"curve {curve.get('tag') if isinstance(curve, dict) else curve!r} "
                           f"has no model-versus-RTL stream comparison")
        if not curve.get("words_driven"):
            raise Rejected(f"curve {curve.get('tag')} does not record the coefficient words driven")
        frames += int(agree["frames"])
        mismatches += int(agree["mismatches"])

    comparison = record.get("engine_comparison")
    if not isinstance(comparison, dict):
        raise Rejected("no engine_comparison block: #94 requires the fixed-model twin's "
                       "reading to be on the record, not overwritten by this one")
    metrics = (comparison.get("rtl_vs_model_metrics") or {})
    if metrics.get("twin_present") and not isinstance(metrics.get("metrics"), dict):
        raise Rejected("engine_comparison names a twin but records no per-metric comparison")

    return {"case_id": case_id, "engine": ENGINE,
            "simulator": f"{chain['simulator']} {versions[chain['simulator']]}",
            "curves": len(curves), "frames": frames, "mismatches": mismatches,
            "bit_exact_against_model": mismatches == 0,
            "twin_engine": metrics.get("twin_engine"),
            "twin_agrees": metrics.get("agrees"),
            "metrics_that_moved": metrics.get("metrics_that_moved")}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("record", nargs="?", default=str(DEFAULT_RECORD))
    a = ap.parse_args(argv)
    path = pathlib.Path(a.record)
    if not path.is_absolute():
        path = ROOT / path
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        print(f"check_f1_rtl_record: REFUSED -- cannot read {a.record}: {exc}", file=sys.stderr)
        return 2
    try:
        summary = check_record(record)
    except Rejected as exc:
        print(f"check_f1_rtl_record: REJECTED {a.record} -- {exc}", file=sys.stderr)
        return 1
    print(f"check_f1_rtl_record: {summary['case_id']} is an {ENGINE} record "
          f"({summary['simulator']}, {summary['curves']} curves, {summary['frames']} frames, "
          f"{summary['mismatches']} disagreeing with the model)")
    if summary["twin_engine"]:
        print(f"  fixed-model twin recorded: agrees={summary['twin_agrees']}"
              + (f", moved: {', '.join(summary['metrics_that_moved'])}"
                 if summary["metrics_that_moved"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
