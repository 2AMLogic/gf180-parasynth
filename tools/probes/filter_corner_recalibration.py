#!/usr/bin/env python3
"""F1A / F1B / F1C re-expressed on the calibrated corner estimator (#150),
and the part of that re-expression this host CANNOT do, said out loud.

    .venv/bin/python tools/probes/filter_corner_recalibration.py

#146 concluded that our filter's two Filters failures are "one trend... the
discrepancy is in the cutoff mapping, not the passband". #150 showed the
instrument contributes part of that trend. This re-reads everything that can
be re-read with the repaired estimator and reports the delta.

THE ANSWER, SO IT IS NOT BURIED. Our own side of F1A/F1B/F1C moves by
-17.0 / -3.9 / -1.1 % at 250 / 1000 / 4000 Hz once the instrument is
corrected -- a 15.9-point trend on ONE SIDE of a comparison whose reported
trend is 8.2 points. #146's attribution cannot stand on that, and it cannot be
settled here either, because the reference side needs audio this host does not
have. Over 400 Hz-6.4 kHz, where five real devices CAN be re-read, the
instrument contributes at most 1.9 points in either direction and four of
eight devices get WORSE -- so the droop those devices show up there is theirs.
The instrument's contribution is a low-cutoff effect, and F1A is the low
cutoff.

WHAT THIS CAN AND CANNOT ANSWER, BEFORE ANY NUMBER
--------------------------------------------------
F1A/F1B/F1C compare OUR ladder against a FROZEN Surge Type 2 clip. Our side is
rendered here from the integer model and is exact. **The reference side is
audio that is not in this repository** -- `refprofile/cache/` is gitignored and
is re-rendered only on a host with the plugin -- so on a host without it the
reference corner cannot be re-measured and the F1 errors cannot be restated.
`run_case.py` says so itself, as a no-verdict with that reason, which is the
correct outcome and not a gap this probe should paper over.

What IS in the repository is `docs/reference-compare-results.json`: 56 real
measured response curves, five devices including Surge Type 2's own
Huovilainen mode, seven commanded cutoffs each, with their raw `freqs` and
`gain_db` arrays. Those can be re-read, and they answer the attribution
question #146 got wrong -- how much of an apparent trend across cutoffs is the
instrument -- on real plugin audio rather than on closed forms.

It exits 1 if a stated invariant fails and REFUSES (exit 2) when any of the
eight configurations lacks a usable curve on the five-cutoff trend grid.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import audio_measure as am                                          # noqa: E402
import run_case as rc                                               # noqa: E402
import refprofile as rp                                             # noqa: E402
import reference_compare as rcmp                                    # noqa: E402

RESULTS = ROOT / "docs" / "reference-compare-results.json"
# Configurations compared in the archived #146 experiment, including three
# model variants. Each trend needs two accepted corners at every grid point.
TREND_DEVICES = ("diva", "miniv3", "ours", "ours-2pole", "ours-huovtune",
                "ours-skew30", "surge-huov", "surge-rk")
TREND_CUTOFFS = (400, 800, 1600, 3200, 6400)
fails: list[str] = []
checked: list[str] = []       # every invariant check() was asked, held or not


def check(ok: bool, what: str):
    checked.append(what)
    print(f"      {'OK  ' if ok else 'FAIL'}  {what}")
    if not ok:
        fails.append(what)


def corner_pre150(freqs, g, cut_hz):
    """`filt_corner` exactly as it stood before #150: -3 dB below the MEDIAN of
    a band whose top edge moves with the commanded cutoff."""
    return am.corner_from_curve(freqs, g, ref_band=rc._ref_band(freqs, cut_hz))


def _ours_row(cid, cut, o, n):
    """One section-1 row. Where either estimator refuses it prints REFUSED and
    the reason's first clause, the way `real_curves` already does -- a refusal
    is a first-class outcome, not a TypeError on a None `.value` (#792)."""
    if not o.ok:
        return (f"   {cid:>5}{cut:11.0f}{'REFUSED':>19}"
                f"{'':>8}   {o.reason.split(' -- ')[0][:44]}")
    if not n.ok:
        return (f"   {cid:>5}{cut:11.0f}{o.value:19.2f}{o.value/cut:8.4f}"
                f"{'REFUSED':>16}   {n.reason.split(' -- ')[0][:44]}")
    return (f"   {cid:>5}{cut:11.0f}{o.value:19.2f}{o.value/cut:8.4f}"
            f"{n.value:16.2f}{n.value/cut:8.4f}{100*(n.value/o.value-1):+8.2f}%")


# ===========================================================================
# 1. Our own side of F1A/F1B/F1C, which this host CAN render exactly
# ===========================================================================
def ours():
    print("=" * 78)
    print("1. OUR side of F1A/F1B/F1C, rendered here from the integer model")
    print("=" * 78)
    F = np.asarray(rcmp.FREQS, dtype=np.float64)
    print(f"   reference_rigs.OurLadder, res 0.0, probe amp {rp.PROBE_AMP} "
          f"({rp.PROBE_LEVEL_DBFS:+.2f} dBFS),")
    print(f"   {len(F)} tones {F[0]:.0f}-{F[-1]:.0f} Hz -- the F1 stimulus exactly.\n")
    print(f"   {'case':>5}{'commanded':>11}{'SHIPPED (pre-#150)':>19}{'ratio':>8}"
          f"{'REPAIRED (#150)':>16}{'ratio':>8}{'moved':>9}")
    out = {}
    for cid, cut in (("F1A", 250.0), ("F1B", 1000.0), ("F1C", 4000.0)):
        g = rc.our_filter_curve(F, cut, 0.0, rp.PROBE_AMP)
        o = corner_pre150(F, g, cut)
        n = rc.filt_corner(cut)(F, g)
        out[cid] = (cut, o, n, g)
        print(_ours_row(cid, cut, o, n))
    print("\n   Our ladder's own corner/cutoff ratio, which an ideal filter's is")
    print("   CONSTANT by construction, still moves across the range after the")
    print("   repair -- that part is the filter and not the instrument.")
    return out


# ===========================================================================
# 2. The reference side, which it cannot
# ===========================================================================
def reference_side():
    print("\n" + "=" * 78)
    print("2. The REFERENCE side of F1A/F1B/F1C -- refused on this host")
    print("=" * 78)
    profile = rp.load_profile()
    for cid, clip in (("F1A", "surge-type2/lp-cut250-res0.00"),
                      ("F1B", "surge-type2/lp-cut1000-res0.00"),
                      ("F1C", "surge-type2/lp-cut4000-res0.00")):
        try:
            rp.load_clip(clip, profile)
            print(f"   {cid}  {clip}  CACHED -- re-run tools/run_case.py {cid}")
        except Exception as e:
            print(f"   {cid}  {clip}\n        REFUSED: {str(e).splitlines()[0]}")
    print("\n   So F1A/F1B/F1C's errors are NOT restated here. `run_case.py` writes")
    print("   them as no-verdicts with this reason, which is the right outcome:")
    print("   a corrected error needs both sides measured by the corrected")
    print("   instrument, and correcting only ours would be worse than not")
    print("   correcting at all.")


# ===========================================================================
# 3. What the repair does to REAL measured curves, and to a trend across them
# ===========================================================================
def _usable_curve(r: dict) -> bool:
    """True only when `freqs` and `gain_db` are equal-length non-empty numeric
    lists with positive frequencies and a positive finite cutoff. A JSON null
    or a wrong type in any of them is an unusable grid point."""
    def nums(v):
        return (isinstance(v, list) and len(v) > 0
                and all(type(x) in (int, float) and math.isfinite(x) for x in v))
    cut = r.get("cut_hz")
    return (nums(r.get("freqs")) and nums(r.get("gain_db"))
            and len(r["freqs"]) == len(r["gain_db"])
            and all(f > 0 for f in r["freqs"])
            and type(cut) in (int, float) and math.isfinite(cut) and cut > 0)


def real_curves():
    print("\n" + "=" * 78)
    print("3. The repair on measured response curves (#146's question)")
    print("=" * 78)
    if not RESULTS.exists():
        print(f"   {RESULTS} is missing")
        return
    data = json.loads(RESULTS.read_text())
    if not isinstance(data, dict):
        print(f"   {RESULTS} lacks device response sections")
        return
    print(f"   {'device':14}{'cut':>6}{'SHIPPED (pre-#150)':>19}"
          f"{'REPAIRED (#150)':>16}{'new/old':>9}"
          f"{'shipped (pre-#150) ratio':>25}{'repaired (#150) ratio':>22}")
    factors: dict[float, list[float]] = {}
    trends: dict[str, tuple] = {}
    for device in TREND_DEVICES:
        rows = data.get(f"response-{device}", [])
        if not isinstance(rows, list):
            print(f"   {device}: missing response list")
            return
        ratios = {}
        for r in rows:
            if (not isinstance(r, dict) or r.get("device") != device
                    or not _usable_curve(r)):
                print(f"   {device}: missing or misidentified response curve")
                return
            f = np.asarray(r["freqs"], float)
            g = np.asarray(r["gain_db"], float)
            cut = float(r["cut_hz"])
            o = corner_pre150(f, g, cut)
            n = rc.filt_corner(cut)(f, g)
            if not o.ok:
                continue
            if not n.ok:
                print(f"   {r['device']:14}{cut:6.0f}{o.value:19.3f}"
                      f"{'REFUSED':>16}   {n.reason.split(' -- ')[0][:44]}")
                continue
            if cut in TREND_CUTOFFS:
                if cut in ratios:
                    print(f"   {device}: duplicate response at {cut:g} Hz")
                    return
                ratios[cut] = (o.value / cut, n.value / cut)
                factors.setdefault(cut, []).append(n.value / o.value)
            print(f"   {r['device']:14}{cut:6.0f}{o.value:19.3f}{n.value:16.3f}"
                  f"{n.value/o.value:9.4f}{o.value/cut:25.4f}{n.value/cut:22.4f}")
        missing = sorted(set(TREND_CUTOFFS) - ratios.keys())
        if missing:
            print(f"   {device}: missing usable response at {missing} Hz")
            return
        old, new = zip(*ratios.values())
        trends[device] = (max(old) / min(old) - 1, max(new) / min(new) - 1)
    print(f"\n   The repair's factor at each cutoff across {len(trends)} configurations:")
    print(f"   {'commanded':>10}{'mean':>9}{'spread':>9}")
    for cut in sorted(factors):
        v = factors[cut]
        print(f"   {cut:9.0f} {np.mean(v):9.4f}{100*(max(v)/min(v)-1):8.2f}%")
    print("\n   The trend each device's own corner/cutoff ratio shows across the")
    print("   cutoffs this dataset holds inside the validated domain -- 400 Hz to")
    print("   6.4 kHz -- which is the quantity #146 read as our cutoff mapping:")
    print(f"   {'device':14}{'SHIPPED (pre-#150) spread':>26}"
          f"{'REPAIRED (#150) spread':>23}{'moved':>10}")
    for dev, (o, n) in trends.items():
        print(f"   {dev:14}{100*o:25.2f}%{100*n:22.2f}%{100*(o-n):+9.2f} pts")
    worst = max(abs(o - n) for o, n in trends.values())
    worse = sum(n > o for o, n in trends.values())
    print(f"\n   Over 400 Hz-6.4 kHz the largest change in droop is "
          f"{100 * worst:.2f} points. {worse} of {len(trends)} configurations get worse.")
    check(worst < 0.02,
          "over 400 Hz-6.4 kHz the repair moves no device's droop by more than "
          "2 points: up there the instrument is not the explanation, either way")
    check(worse > 0,
          "and it moves some of them the WRONG way, which is what 'the instrument "
          "contributes nothing here' looks like when it is measured instead of "
          "assumed")


def main() -> int:
    fails.clear()
    checked.clear()
    ours()
    reference_side()
    real_curves()
    print("\n" + "=" * 78)
    if not checked:
        # The verdict is derived from `fails`, which only check() fills. With
        # no results file real_curves() returns before its two check() calls,
        # so an empty `fails` here means nothing was compared (#1469).
        print("REFUSED  no invariant was checked -- the results file above is "
              "missing or lacks the required usable curves.")
        return 2
    if fails:
        for f in fails:
            print(f"   {f}")
        return 1
    print(f"every stated invariant holds ({len(checked)} checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
