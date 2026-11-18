#!/usr/bin/env python3
"""Re-measurement of two numeric claims in issue #109, against the tree as it
stands on this branch, rather than carrying them forward unverified.

    .venv/bin/python tools/probes/verify_109_claims.py

#109's own curation pass flagged that its two supporting numbers do not
appear verbatim anywhere in the repo and may predate #108's fix to
`tone_ratio_db`/`find_line` (which resolves partial frequencies by search
rather than assuming the nominal chart value):

  1. "`tone_ratio_db` ... loses 7.3 dB to 1% detuning, 16.0 dB to 5%
     detuning, and 8.7 dB when equal-amplitude partials decay at different
     rates."
  2. "The reference rimshot carries signal for ~20 ms" and "the
     808-from-mars rimshots are 26 and 39 ms."

This script re-measures (1) directly against the CURRENT `tone_ratio_db`, and
(2) against the actual Fischer `RS.WAV`/`CB.WAV` files this repository scores
D10A/D13A against (`808 From Mars` audio is operator-side storage, per
`refaudio/README.md` -- not fetched to this host, so its two rimshot lengths
are NOT re-measured here; that gap is stated rather than silently skipped).

It exits non-zero if either finding is not what the printed numbers say, so
this is a check and not only a transcript. Exit statuses follow the runner's
convention (0 match / 1 mismatch / 2 no evidence): the verdict line always
COUNTS -- `N of T checks passed`, with any check a precondition prevented
reported as `NOT RUN` where its result would have been, never folded into a
global pass. With the corpus optional, absence keeps exit 0 (the deliberate
decision recorded at Makefile:130-134, restated per check); with
`GF180_REQUIRE_TR808_REFS=1` it is exit 2 / NO VERDICT naming the missing
file, because a required gate does not pass by skipping (the `fischer_refs`
policy, tools/test_run_case.py). A corpus root that exists while its
recordings are missing, empty or unreadable is the same did-not-run state --
never an uncaught traceback on exit 1 (#814).
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "probes"))

import run_case as rc                                                # noqa: E402
#: #517 acceptance criterion 6: this file stays standalone -- it re-measures
#: two withdrawn numeric claims from #109 and is not a per-estimator gate --
#: but its `_damped`/`_two_tone` were copies of `estimator_fixtures`'
#: primitives and are now imported from there. NOTE `lead_ms=0`: this file's
#: numbers were measured WITHOUT the pre-onset lead `two_tone` adds by
#: default, so the lead is switched off rather than silently acquired.
import estimator_fixtures as ef                                      # noqa: E402

SR = 48000
fails: list[str] = []
not_run: list[str] = []
n_checks = 0
n_ok = 0


def check(ok: bool, what: str):
    global n_checks, n_ok
    n_checks += 1
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if ok:
        n_ok += 1
    else:
        fails.append(what)


def note_not_run(what: str, why: str):
    """A precondition prevented this check: record it WHERE THE RESULT WOULD
    HAVE BEEN, so the verdict line can count it instead of papering over it
    (#814 row 2). Never a bare return past a check."""
    global n_checks
    n_checks += 1
    print(f"      NOT RUN  {what}")
    print(f"               ({why})")
    not_run.append(f"{what}: {why}")


_damped = ef.damped


def _two_tone(f1, f2, tau1, tau2, a1, a2, seconds, sr):
    return ef.two_tone(f1, f2, tau1, tau2, a1, a2, seconds, sr, lead_ms=0.0)


# ===========================================================================
# 1. tone_ratio_db's detuning / differential-decay sensitivity, TODAY
# ===========================================================================
def measure_tone_ratio_sensitivity():
    print("\n" + "=" * 78)
    print("1. tone_ratio_db detuning / differential-decay sensitivity (re-measured)")
    print("=" * 78)
    f1, f2 = 540.0, 800.0                        # the cowbell's own nominal pair
    a1, a2 = 1.0, 0.5
    truth_db = 20.0 * math.log10(a2 / a1)

    # (a) 1% and 5% detuning, near-stationary tone (isolates the frequency
    # term from decay): the withdrawn claim is 7.3 dB / 16.0 dB of loss.
    for pct, claimed_loss in ((0.01, 7.3), (0.05, 16.0)):
        x = _two_tone(f1, f2 * (1 + pct), 5.0, 5.0, a1, a2, 0.5, SR)
        e = rc.tone_ratio_db(x, SR, f2, f1)
        check(e.ok, f"{pct*100:.0f}% detune: tone_ratio_db resolves a line at all")
        if e.ok:
            loss = abs(e.value - truth_db)
            print(f"      {pct*100:.0f}% detune: measured loss {loss:.4f} dB "
                  f"(issue claimed {claimed_loss} dB)")
            check(loss < 0.5, f"{pct*100:.0f}% detune: loss is negligible, "
                              f"NOT the claimed {claimed_loss} dB")

    # (b) equal-amplitude partials decaying at different rates, at rimshot
    # speed (tau ~ 6 ms / 1.5 ms, matching D10A's own two modes' relative
    # rates): the withdrawn claim is 8.7 dB of loss.
    for tau1, tau2, seconds in ((0.006, 0.006, 2.2), (0.006, 0.0015, 2.2)):
        x = _two_tone(f1, f2, tau1, tau2, a1, a2, seconds, SR)
        e = rc.tone_ratio_db(x, SR, f2, f1)
        tag = f"tau1={tau1*1e3:.1f}ms tau2={tau2*1e3:.1f}ms"
        if e.ok:
            print(f"      {tag}: tone_ratio_db returned {e.value:.2f} dB "
                  f"(loss {abs(e.value - truth_db):.2f} dB)")
        else:
            print(f"      {tag}: tone_ratio_db REFUSED ({e.reason}) rather than "
                  f"reporting a biased number")
        # Either outcome refutes "loses 8.7 dB": a silent 8.7 dB bias never
        # happens -- it is either near-exact or an explicit refusal.
        check(not e.ok or abs(e.value - truth_db) < 1.0,
              f"{tag}: no silent multi-dB bias (either refused or accurate)")

    print("\n      CONCLUSION: current tone_ratio_db (post-#108, find_line-based)")
    print("      is essentially exact under 1%/5% detuning and REFUSES outright on")
    print("      rimshot-speed differential decay rather than reporting a biased")
    print("      ratio. The issue's 7.3/16.0/8.7 dB figures do not reproduce against")
    print("      this tree and must not be carried forward as a baseline -- they")
    print("      describe the pre-#108 nominal-probe estimator (still visible, by")
    print("      design, as the withdrawn comparison in this same probes/ package).")


# ===========================================================================
# 2. The Fischer RS.WAV / CB.WAV references' own signal length
# ===========================================================================
REFERENCE_VOICES = (("RS", rc.RS_BALANCE_OP), ("CB", rc.CB_BALANCE_OP))


def _refuse_voice(voice: str, why: str, refusals: list[str]):
    """One voice's did-not-run state (#814): the check is reported NOT RUN
    where its result would have been, and the reason is recorded so the half
    can refuse outright at the end under GF180_REQUIRE_TR808_REFS=1. The
    refusal is PER VOICE, not per half (review round 1, measured): the
    half-wide handler this replaces was called from inside the per-voice
    loop, so a mixed corpus -- one recording usable, one damaged -- counted
    the measured voice twice (once OK, once NOT RUN blaming the other
    voice's file) and inflated the verdict total from 8 to 9."""
    note_not_run(f"{voice} reference: balance_trajectory_db resolves on "
                 f"the real reference", why)
    refusals.append(why)


def conclusion_extent_lines(extents: dict[str, dict]) -> list[str]:
    """The half's CONCLUSION block, with every extent figure derived from
    this run's own measurements (#988): the prose and the detail lines above
    it are built from the same values, so the prose cannot drift from the
    measurement it describes -- the failure class that left a hard-coded
    `~41 ms` standing next to a measured `latest 53.9 ms` on the same run,
    in prose no gate reads. `t2_ms` is conditional in balance_trajectory_db's
    detail (present only when a second floor-clearing instant exists past
    min_gap_ms), so a single-instant record degrades the phrase rather than
    KeyError-ing (#814's start-red test surfaced that condition)."""
    def span(v: dict) -> str:
        if v["t2_ms"] is None:
            return f"a single floor-clearing instant at {v['t1_ms']:.1f} ms"
        return f"{v['t1_ms']:.1f} ms to {v['t2_ms']:.1f} ms"

    def end(v: dict) -> str:
        return span(v) if v["t2_ms"] is None else f"{v['t2_ms']:.1f} ms"

    rs, cb = extents["RS"], extents["CB"]
    return [
        "\n      CONCLUSION: for RS.WAV the joint-floor-clearing region runs from",
        f"      {span(rs)} of a {rs['t_end_ms']:.0f} ms search window -- longer than the",
        "      ~20 ms this issue quotes, but the same order of magnitude, and a",
        "      small minority of the case's nominal window either way. For CB.WAV",
        f"      it runs to {end(cb)} of a {cb['t_end_ms']:.0f} ms window. Neither",
        "      number is a single constant 'signal length' -- floor-clearing is",
        "      intermittent, not a contiguous prefix -- which is the concrete",
        "      reason this fix gates each instant against its own measured floor",
        "      (#92) instead of trying to pick one better window length (#101).",
    ]


def measure_reference_signal_length():
    print("\n" + "=" * 78)
    print("2. RS.WAV / CB.WAV floor-clearing extent (re-measured from the actual")
    print("   reference audio D10A/D13A score against)")
    print("=" * 78)
    refs = rc.configured_refs()
    required = os.environ.get(rc.REFS_REQUIRED_ENV) == "1"
    refusals: list[str] = []
    measured: set[str] = set()
    extents: dict[str, dict] = {}

    for voice, op in REFERENCE_VOICES:
        # File-level precondition (#814 row 3), PER VOICE: the WAV this half
        # loads is probed at the path run_case's own REF_MAIN names, so the
        # precondition cannot drift from the loader it guards; missing and
        # zero-length are named in one message. Only the unusable voice is
        # refused -- the other voice's measurement is kept.
        rel = rc.REF_MAIN[voice][0]
        path = refs / rel
        if not path.exists() or path.stat().st_size == 0:
            state = "missing" if not path.exists() else "zero-length"
            _refuse_voice(
                voice,
                f"reference recording {rel} ({state}) at {refs}; clone "
                f"tidalcycles/sounds-tr808-fischer there or set {rc.REFS_ENV}",
                refusals)
            continue
        try:
            x, sr, rel, _setting = rc.load_reference(voice, refs)
        except Exception as why:  # noqa: BLE001 -- see the comment below
            # Deliberately broad, and safe: load_reference's only work is
            # reading one corpus WAV (scipy raises ValueError on a
            # zero-length/header-corrupt file and can raise its own internal
            # errors on other damage -- measured, #814 -- none of which is
            # `Refused`), plus finite/silence checks that already raise
            # Refused. Nothing here can mask estimator logic; the measurement
            # calls below keep their narrow Refused catch. Any crash in the
            # read is the corpus being unusable: did-not-run, not a mismatch.
            _refuse_voice(
                voice,
                f"reference recording {rc.REF_MAIN[voice][0]} unreadable "
                f"({type(why).__name__}: {why})", refusals)
            continue
        try:
            y = rc.prepare(x, sr, side="reference")
            e = rc.balance_trajectory_db(y, sr, op["f_lo_range"], op["f_hi_range"],
                                         win_ms=op["win_ms"], hop_ms=op["hop_ms"],
                                         t_end=op["t_end"], guards=op["guards"],
                                         min_gap_ms=op["min_gap_ms"])
        except rc.Refused as why:
            # Measured (#814): a reference truncated inside its data chunk
            # reads fine, then `prepare` refuses -- no pre-onset lead. A
            # refusal is did-not-run, never a FAIL.
            _refuse_voice(
                voice,
                f"reference recording {rel} refused by preparation: {why}",
                refusals)
            continue
        check(e.ok, f"{voice} {rel}: balance_trajectory_db resolves on the real reference")
        if not e.ok:
            continue
        measured.add(voice)
        d = e.detail
        extents[voice] = {"t1_ms": d["t1_ms"], "t2_ms": d.get("t2_ms"),
                          "t_end_ms": op["t_end"] * 1e3}
        # t2_ms is conditional in balance_trajectory_db's detail: present only
        # when a second floor-clearing instant exists past min_gap_ms. A
        # single-instant record is a valid verdict, not a KeyError (#814's
        # start-red test surfaced this on a synthetic reference).
        tail = (f", latest {d['t2_ms']:.1f} ms" if "t2_ms" in d else
                "; no second floor-clearing instant past the "
                f"{op['min_gap_ms']} ms gap")
        print(f"      {voice} {rel}: prepared record is {len(y)/sr*1e3:.1f} ms long; "
              f"{d['n_points_above_floor']} of {d['n_points_total']} probed instants "
              f"in [0, {op['t_end']*1e3:.0f}] ms clear the joint floor by 6 dB; "
              f"earliest {d['t1_ms']:.1f} ms{tail}")

    if len(measured) == len(REFERENCE_VOICES):
        # The CONCLUSION compares both voices' measured extents, so it is
        # printed only when both produced measurements this run: a half-run
        # keeps its per-voice detail lines and NOT RUN records without
        # asserting the numbers of the voice that did not run. The figures
        # come from conclusion_extent_lines(), derived from this run (#988),
        # so the prose and the detail lines above cannot disagree.
        for line in conclusion_extent_lines(extents):
            print(line)
    print("\n      The '808-from-mars' 26 ms / 39 ms rimshot figures are NOT")
    print("      re-measured here: that pack's audio is operator-side storage")
    print("      (refaudio/README.md) and was not available on this host.")

    if refusals and required:
        # A required gate does not pass by skipping: one refused voice is
        # enough to refuse the half (the `fischer_refs` policy).
        for r in refusals:
            print(f"      NO VERDICT: {r}")
        print(f"      {rc.REFS_REQUIRED_ENV}=1 makes the reference half a required")
        print("      gate, and a required gate does not pass by skipping.")
        return "; ".join(refusals)
    return None


def main() -> int:
    measure_tone_ratio_sensitivity()
    no_verdict = measure_reference_signal_length()
    total = n_checks
    print("\n" + "=" * 78)
    if fails:
        print(f"{n_ok} of {total} checks passed; {len(fails)} FAILED:")
        for f in fails:
            print(f"  - {f}")
        if not_run:
            print(f"  (and {len(not_run)} check(s) NOT RUN -- see NOT RUN above)")
        return 1
    if no_verdict:
        print(f"{n_ok} of {total} checks passed; {len(not_run)} NOT RUN "
              f"-- NO VERDICT ({rc.REFS_REQUIRED_ENV}=1)")
        return 2
    if not_run:
        print(f"{n_ok} of {total} checks passed; {len(not_run)} NOT RUN "
              f"(reference corpus not usable on this host -- see NOT RUN above)")
        return 0
    print(f"{n_ok} of {total} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
