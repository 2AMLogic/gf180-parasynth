"""Mutation record for fpga/scripts/pnr_report.py (issue #565, Judge on PR #594).

Applies each mutant in turn, runs fpga/test_pnr_report.py, restores the file,
and prints KILLED / SURVIVED / REFUSED per mutant. Usage:

    python fpga/testdata/mutate_pnr_report.py

The suite runs under the interpreter that runs this script. "J-" names are the
Judge's mutants from PR #594. "J-equivalent: disable rc is None" is a known
EQUIVALENT mutant -- `classify` still REFUSES a missing status through the
earlier `status is None` path -- and is the one EXPECTED SURVIVOR: a green
suite under it is not a hole, and a red one is REFUSED because nothing is
recorded that could attribute it.

A red is a kill only if it is the recorded tests' red (issue #1401,
docs/verification-rules.md 5). Until then this record read ANY nonzero pytest
status as KILLED, so a suite that raised at collection under every mutant --
pytest exits 2 and runs no test -- printed fourteen kills and exited 0. The
decision is therefore not made here: `verdict()` in
tools/probes/holdout_injected_controls.py (#1400) is applied to each mutant
with this suite and `EXPECTED`, the test and assertion that caught it:

  * KILLED    pytest exited 1 and a recorded assertion failed, in a test that
              passed on the baseline. The matching test ID is printed.
  * SURVIVED  pytest stayed green with the mutation in place: a hole in the
              suite. For the equivalent mutant only, an EXPECTED SURVIVOR.
  * REFUSED   anything else, with no kill credit: a collection error, an
              ERROR-only run, a red outside the recorded tests or at another
              assertion, a recorded check that did not pass in both runs, a
              search text that no longer matches exactly once.

Exit 0 when every other mutant was killed and the equivalent one survived; 1
when any other mutant survived (a mix with refusals included, both named); 2
when nothing survived but something refused, and for a red baseline
(issue #1130), before any mutant runs.

Recorded result, 2026-11-27 (`267955a3` plus this change, Python 3.12.3,
pytest 9.2.0, 44 tests, `make` present so the fake-executable rig ran):

    13 mutant(s) killed, 1 expected survivor (J-equivalent: disable rc is None)

Each kill named the tests `EXPECTED` records for it (supersets are printed as
information only). Both unlink mutants and the disabled bitstream check are
caught by `test_make_silent_packer_beside_stale_bitstream_fails`, the
stale-artefact control. Measured with no `make` on PATH (the rig fixture then
skips): the same run exits 2, REFUSING the five mutants whose recorded checks
were skipped -- a skipped control establishes neither a kill nor a survivor --
and still names the eight kills that never needed the rig.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGET = ROOT / "fpga" / "scripts" / "pnr_report.py"
SUITE = "fpga/test_pnr_report.py"

sys.path.insert(0, str(ROOT / "tools" / "probes"))
import holdout_injected_controls as hic  # noqa: E402

UNLINK = '''    for name in (*TARGETS[a.target]["stale"], log_p.name, st_p.name, console.name,
                 f"{a.target}_refused.txt", f"{a.target}_nofit.txt"):
        (build / name).unlink(missing_ok=True)
    log_p.write_text("")
'''
M = {
 "J-survivor: remove unlink loop + log truncation": [(UNLINK, "")],
 "remove unlink loop only (keep truncation)": [(UNLINK, '    log_p.write_text("")\n')],
 "J-survivor: disable evaluate bitstream check": [("if not (bit_p.is_file() and bit_p.stat().st_size > 0):", "if False:")],
 "J-survivor: disable util[0] > r order check": [("if util and util[0] > r:", "if False:")],
 "J-equivalent: disable rc is None": [('if rc is None:\n', 'if False:\n')],
 "always write --out": [("if v.state == ROUTED or (v.state == NOFIT and a.allow_no_fit):\n        Path(a.out)", "if True:\n        Path(a.out)")],
 "ignore error lines at rc 0": [("    if errors:\n        return Verdict(REFUSED", "    if False:\n        return Verdict(REFUSED")],
 "missing status treated as OK": [("    if status is None:\n        return", "    if status is None:\n        status = {'nextpnr': {'returncode': 0}, 'after': []}\n    if False:\n        return")],
 "ignore --after failures": [("if st.get(\"error\") or st.get(\"signal\") or st.get(\"returncode\") != 0:", "if False:")],
 "skip running --after": [("        for s in a.after:", "        for s in []:")],
 "any error counts as no-fit": [("fit = [l for l in errors if FIT_ERRORS.search(l)]", "fit = errors")],
 "allow-no-fit accepts REFUSED": [("if v.state == ROUTED or (v.state == NOFIT and allow_no_fit):", "if v.state == ROUTED or allow_no_fit:")],
 "non-zero rc treated as no-fit": [("return Verdict(REFUSED, why + [f\"nextpnr exited {rc} without", "return Verdict(NOFIT, why + [f\"nextpnr exited {rc} without")],
 "new: render Fmax on unrouted reports": [("if mf and verdict.state != ROUTED:", "if False:")],
}
EQUIVALENT = "J-equivalent: disable rc is None"

# The test and the failed assertion that caught each mutant on the real suite,
# read from pytest's own report on 2026-11-27 (`267955a3`, Python 3.12.3,
# pytest 9.2.0, 44 tests). Parameter IDs are kept where the case is the point.
# Overlap is enough: a superset still fires and its extras are printed as
# information, while a renamed-away record refuses rather than passing.
_STALE = "test_make_silent_packer_beside_stale_bitstream_fails"
_SILENT = {
    f"{_STALE}[ecp5-ecppack-ecp5.bit-ecp5_25f.txt-stale0]": hic.Site("assert r.returncode != 0"),
    f"{_STALE}[ice40-icepack-ice40.bin-ice40_up5k.txt-stale1]": hic.Site("assert r.returncode != 0"),
}
_CLOBBERED = 'assert rep.read_bytes() == b"OLD COMMITTED REPORT\\n"'
_KEPT = 'assert r.returncode != 0 and rep.read_text() == "OLD\\n"'
_HEADROOM = hic.Site("assert r.returncode != 0 and not rep.exists()")
_REFUSED = hic.Site("assert v.state == pr.REFUSED")
_UNCLASSIFIED = hic.Site('assert v.state == pr.REFUSED and "unclassified" in " ".join(v.reasons)')
_BAD_EXIT = "test_bad_exit_never_routed_even_beside_complete_log"
EXPECTED = {
    "J-survivor: remove unlink loop + log truncation": dict(_SILENT),
    "remove unlink loop only (keep truncation)": dict(_SILENT),
    "J-survivor: disable evaluate bitstream check": {
        "test_evaluate_empty_bitstream_is_refused":
            hic.Site('assert v.state == pr.REFUSED and "ecp5.bit missing or empty" in v.reasons[0]'),
        **_SILENT,
    },
    "J-survivor: disable util[0] > r order check": {
        "test_utilisation_only_after_route_marker_is_refused_for_order": _REFUSED,
    },
    EQUIVALENT: {},
    "always write --out": {
        "test_make_ecp5_failed_packer_fails_and_keeps_report": hic.Site(_KEPT),
        "test_make_ecp5_missing_tool_fails_and_keeps_report": hic.Site(_KEPT),
        f"{_STALE}[ecp5-ecppack-ecp5.bit-ecp5_25f.txt-stale0]": hic.Site(_CLOBBERED),
        f"{_STALE}[ice40-icepack-ice40.bin-ice40_up5k.txt-stale1]": hic.Site(_CLOBBERED),
        "test_make_headroom_accepts_only_declared_no_fit": _HEADROOM,
    },
    "ignore error lines at rc 0": {
        "test_exit_zero_with_error_line_is_refused":
            hic.Site('assert pr.classify(synth_log() + "ERROR: late failure\\n", OK).state == pr.REFUSED'),
    },
    "missing status treated as OK": {
        "test_missing_log_and_missing_status_are_refused":
            hic.Site('assert v.state == pr.REFUSED and "exit status" in v.reasons[0]'),
    },
    "ignore --after failures": {
        "test_failed_pack_stage_is_refused":
            hic.Site('assert v.state == pr.REFUSED and "icetime" in v.reasons[0]'),
        "test_make_ecp5_failed_packer_fails_and_keeps_report": hic.Site('assert "ecppack" in r.stderr'),
    },
    "skip running --after": {
        "test_make_ecp5_success_writes_report": hic.Site("assert r.returncode == 0"),
        "test_make_ice40_success_and_no_fit": hic.Site('assert run("ice40").returncode == 0'),
    },
    "any error counts as no-fit": {
        "test_generic_tool_error_is_refused_not_no_fit": _UNCLASSIFIED,
    },
    "allow-no-fit accepts REFUSED": {
        "test_make_headroom_accepts_only_declared_no_fit": _HEADROOM,
    },
    "non-zero rc treated as no-fit": {
        "test_generic_tool_error_is_refused_not_no_fit": _UNCLASSIFIED,
        f"{_BAD_EXIT}[nx0]": _REFUSED,
        f"{_BAD_EXIT}[nx1]": _REFUSED,
    },
    "new: render Fmax on unrouted reports": {
        "test_refused_report_does_not_label_an_fmax_as_post_route_result":
            hic.Site('assert "post-route RESULT" not in text and "ESTIMATE:" not in text'),
    },
}


def _mutated(original: str, subs) -> str | None:
    """The source with each substitution applied, or None if a search text
    is not present exactly once: the file has moved under the record."""
    s = original
    for a, b in subs:
        if s.count(a) != 1:
            return None
        s = s.replace(a, b)
    return s


def main() -> int:
    original = TARGET.read_bytes()
    text = original.decode()

    moved = [name for name, subs in M.items() if _mutated(text, subs) is None]
    if moved:
        print("REFUSED -- these mutants' search text no longer matches exactly once "
              f"in {TARGET.relative_to(ROOT)}:")
        for name in moved:
            print(f"    {name}")
        print("The file has changed under the record. Update the mutants; "
              "do not read this as a pass. No mutant was run.")
        return 2

    # A suite red for its own reason would "kill" every mutant
    # (verification rule 5). REFUSE (exit 2) unless it is green unmutated.
    base = hic.run_baseline(ROOT, SUITE)
    if base.report is None:
        print(f"REFUSED -- baseline `pytest {SUITE}`: {base.reason}. No mutant was run.")
        print(base.tail.rstrip())
        return 2

    survivors, refused, expected = [], 0, []
    for name, subs in M.items():
        TARGET.write_bytes(_mutated(text, subs).encode())
        try:
            rc, report, _ = hic.run_suite(ROOT, SUITE)
        finally:
            TARGET.write_bytes(original)
        res = hic.verdict(rc, report, EXPECTED[name], suite=SUITE, baseline=base.report)
        if res.outcome == hic.REFUSED:
            refused += 1
            print(f"{'REFUSED':17s}  {name} -- {res.reason}")
            if res.failed:
                print(f"    (failed, no credit: {', '.join(res.failed)})")
        elif res.outcome == hic.NO_FIRE:
            if name == EQUIVALENT:
                expected.append(name)
                print(f"{'EXPECTED SURVIVOR':17s}  {name}")
            else:
                survivors.append(name)
                print(f"{'SURVIVED':17s}  {name} -- {res.reason}")
        else:
            print(f"{'KILLED':17s}  {name} -- {len(res.failed)} test(s) caught it")
            for test in res.failed:
                print(f"    {test}")
            if res.other:
                print(f"    (other failures, information only: {', '.join(res.other)})")

    # A survivor -- a hole in the suite -- outranks a refusal, as in the holdout
    # and sound probes (#1113, #1400): 1 when any unexpected mutant survived (a
    # mix included), else 2 when any refused, else 0.
    if survivors:
        print(f"\nUNEXPECTED SURVIVORS ({len(survivors)}): " + "; ".join(survivors)
              + (f"  -- and {refused} refused, see above" if refused else ""))
        return 1
    if refused:
        print(f"\n{refused} mutant(s) REFUSED -- the reasons are above; nothing was "
              "measured for them, and no kill is claimed.")
        return 2
    print(f"\n{len(M) - len(expected)} mutant(s) killed"
          + (f", {len(expected)} expected survivor ({expected[0]})" if expected else "")
          + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
