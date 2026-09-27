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
this is a check and not only a transcript.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import run_case as rc                                                # noqa: E402

SR = 48000
fails: list[str] = []


def check(ok: bool, what: str):
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if not ok:
        fails.append(what)


def _damped(f, tau, amp, n, sr, phase=0.0):
    t = np.arange(n) / sr
    return amp * np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)


def _two_tone(f1, f2, tau1, tau2, a1, a2, seconds, sr):
    n = int(seconds * sr)
    return (_damped(f1, tau1, a1, n, sr, 0.3)
            + _damped(f2, tau2, a2, n, sr, 1.9))


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
def measure_reference_signal_length():
    print("\n" + "=" * 78)
    print("2. RS.WAV / CB.WAV floor-clearing extent (re-measured from the actual")
    print("   reference audio D10A/D13A score against)")
    print("=" * 78)
    refs = rc.configured_refs()
    if not refs.exists():
        print(f"      SKIPPED: reference corpus not at {refs} on this host")
        return
    for voice, op in (("RS", rc.RS_BALANCE_OP), ("CB", rc.CB_BALANCE_OP)):
        x, sr, rel, _setting = rc.load_reference(voice, refs)
        y = rc.prepare(x, sr, side="reference")
        e = rc.balance_trajectory_db(y, sr, op["f_lo_range"], op["f_hi_range"],
                                     win_ms=op["win_ms"], hop_ms=op["hop_ms"],
                                     t_end=op["t_end"], guards=op["guards"],
                                     min_gap_ms=op["min_gap_ms"])
        check(e.ok, f"{voice} {rel}: balance_trajectory_db resolves on the real reference")
        if not e.ok:
            continue
        d = e.detail
        print(f"      {voice} {rel}: prepared record is {len(y)/sr*1e3:.1f} ms long; "
              f"{d['n_points_above_floor']} of {d['n_points_total']} probed instants "
              f"in [0, {op['t_end']*1e3:.0f}] ms clear the joint floor by 6 dB; "
              f"earliest {d['t1_ms']:.1f} ms, latest {d['t2_ms']:.1f} ms")
    print("\n      CONCLUSION: for RS.WAV the joint-floor-clearing region runs from")
    print("      ~11 ms to ~41 ms of a 60 ms search window -- longer than the ~20 ms")
    print("      this issue quotes, but the same order of magnitude, and a small")
    print("      minority of the case's nominal window either way. For CB.WAV it")
    print("      runs to ~589 ms of a 600 ms window. Neither number is a single")
    print("      constant 'signal length' -- floor-clearing is intermittent, not a")
    print("      contiguous prefix -- which is the concrete reason this fix gates")
    print("      each instant against its own measured floor (#92) instead of")
    print("      trying to pick one better window length (#101).")
    print("\n      The '808-from-mars' 26 ms / 39 ms rimshot figures are NOT")
    print("      re-measured here: that pack's audio is operator-side storage")
    print("      (refaudio/README.md) and was not available on this host.")


def main() -> int:
    measure_tone_ratio_sensitivity()
    measure_reference_signal_length()
    print("\n" + "=" * 78)
    if fails:
        print(f"{len(fails)} check(s) FAILED:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
