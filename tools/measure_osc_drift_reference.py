#!/usr/bin/env python3
"""What the REFERENCES say about oscillator drift, measured from frozen audio.

Issue #56 asks for the amount of drift to be established from Surge XT, Mini V3
and Diva before any is implemented. This tool answers that question with the
material that is actually available on a clean checkout, and REFUSES for each
reference it cannot reach rather than leaving a gap that reads as zero.

WHAT IT CAN AND CANNOT REACH, and why -- asserted, not assumed
-------------------------------------------------------------
`model/reference_rigs.py` hosts all three references through `dawdreamer` from
`/Library/Audio/Plug-Ins/VST3`, i.e. from a macOS operator machine. Most hosts
in this fleet have neither (`refprofile/README.md` says so in as many words).
This tool therefore does NOT render: it measures the frozen, hash-bound Mini V3
audio committed under `docs/scorecard/`, and reports the other two as REFUSED
with the specific missing precondition.

  Mini V3    frozen renders under docs/scorecard/mono-m1a-miniv3 and
             mono-m5a-miniv3, hash-bound in their manifests. MEASURABLE.
  Surge XT   refprofile/ holds sixteen frozen Surge clips, but every one is the
             FILTER driven by our own stepped tone through Audio In -- Surge's
             oscillators are not in any of them. No frozen sustained Surge
             oscillator tone exists in this repository. REFUSED.
  Diva       no frozen Diva audio exists in this repository at all, and Diva
             has no audio input, so its own oscillator is the only source it
             could ever have been recorded with. REFUSED.

Every window's hash is checked against the manifest before it is measured. A
clip that is absent or does not hash is REFUSED for that reason, never skipped.

WHAT THE FROZEN MATERIAL CAN SUPPORT, AND WHAT IT CANNOT
--------------------------------------------------------
These renders were frozen for attack, release and spectrum cases, not for
drift: the longest held note in any of them is 0.6 s of gate plus its release.
`model/osc_drift_probe.py` needs seconds of held tone to call a wander
aperiodic, so several of these windows come back REFUSED with their own code.
That is the measurement, not a failure of it -- and the across-note reading
below is what the short notes still support.

  across-note: two instances of the SAME commanded note, seconds apart in one
  continuous render, differ in f0 by exactly the drift accumulated between
  them. M1A plays MIDI 36 at 0.1 s and again at 4.1 s; M5A plays MIDI 84 in
  both of its segments, 13.6 s apart. Those baselines need no long window, only
  a precise f0 in each, and the probe's floor bounds it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "audition"))

import numpy as np                                                   # noqa: E402

import osc_drift_probe as odp                                        # noqa: E402
from dsp import note_hz                                              # noqa: E402

SCOREBOARD = os.path.join(ROOT, "docs", "scorecard")
TAIL_S = 1.2        # how far past GATE OFF to look for usable held tone. A
                    # decay does not move a sinusoid's zeros, so a tail IS
                    # usable pitch -- but only the part still well above the
                    # floor, which `held_window` below, not this constant, picks.
TRIM_DB = 1.5       # the held part is where the envelope is within this of its
                    # 90th percentile. Attack, decay and silence are all
                    # excluded by the same rule, measured per window.


def held_window(x, sr: int, f0: float, trim_db: float = TRIM_DB):
    """The contiguous run of `x` that is actually a HELD tone at f0.

    The envelope is the magnitude of the signal heterodyned to baseband and
    box-averaged over three nominal periods -- the same front end the probe
    uses -- and the window is the longest run within `trim_db` of its 90th
    percentile. Without this, a window specified from MIDI events alone carries
    the attack, the release and the silence after it, the fundamental's level
    moves by hundreds of dB, and the probe refuses (correctly) on a window that
    does hold a measurable tone somewhere inside it."""
    import math
    n = len(x)
    L = max(8, int(round(sr / f0)) * 3)
    if n < 4 * L:
        return 0, n
    t = np.arange(n) / sr
    z = np.asarray(x, dtype=np.float64) * np.exp(-2j * math.pi * f0 * t)
    k = np.ones(L) / L
    e = np.abs(np.convolve(z, k, mode="same"))
    ref = float(np.percentile(e, 90))
    if ref <= 0:
        return 0, n
    ok = e >= ref * 10.0 ** (-trim_db / 20.0)
    # longest contiguous True run
    best = (0, 0)
    i = 0
    while i < n:
        if ok[i]:
            j = i
            while j < n and ok[j]:
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j
        else:
            i += 1
    return best


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _clip(case: str, name: str, want_sha: str | None) -> tuple:
    """(sr, x) for a frozen clip, or a refusal dict. The hash is a
    PRECONDITION: audio that is not the audio the manifest describes is not the
    reference, and measuring it would produce a number nobody can reproduce."""
    path = os.path.join(SCOREBOARD, case, name)
    if not os.path.exists(path):
        return {"verdict": odp.REFUSED, "refusal": "CLIP_ABSENT",
                "refusal_detail": path}
    if want_sha:
        got = sha256(path)
        if got != want_sha:
            return {"verdict": odp.REFUSED, "refusal": "CLIP_HASH",
                    "refusal_detail": f"{name}: {got[:12]} != {want_sha[:12]}"}
    sr, x = odp.read_wav(path)
    return sr, x


def _windows_m1a(man: dict) -> list:
    """(file, sha, label, semitone_offset, note, t0, t1) for the M1A phrase and
    its single-oscillator isolation controls, from the manifest's own timeline.

    `osc2_open` isolates the OCTAVE saw, so its declared f0 is the note + 12.
    Declaring the bare note instead was this tool's own first error, and the
    probe caught it: `zc` read 1196 cents above `het`, one octave to within four
    cents, and refused with ESTIMATOR_DISAGREE rather than reporting the
    nominal. It is left in the record because a cross-check that has never
    caught anything is not known to work."""
    out = []
    files = [(r["file"], r["sha256"], f"phrase{i}")
             for i, r in enumerate(man["renders"])]
    for key, c in man.get("controls", {}).items():
        files.append((c["file"], c["sha256"], key))
    for fn, sha, kind in files:
        semis = 12 if kind == "osc2_open" else 0
        for i, ev in enumerate(man["events"]):
            t0 = ev["on_s"]
            t1 = t0 + ev["gate_s"] + TAIL_S
            out.append((fn, sha, f"m1a/{kind}/n{i}-midi{ev['note'] + semis}",
                        ev["note"] + semis, t0, t1))
    return out


def _windows_m5a(man: dict) -> list:
    fn = man["audio"]["file"]
    sha = man["audio"]["sha256"]
    out = []
    for s, seg in enumerate(man["timeline"]["segments"]):
        off = seg["offset_samples"] / man["host"]["sample_rate_hz"]
        for i, ev in enumerate(seg["midi_events"]):
            t0 = off + ev["on_s"]
            t1 = t0 + ev["gate_s"] + TAIL_S
            out.append((fn, sha, f"m5a/seg{s}-{seg['wave']}/n{i}-midi{ev['note']}",
                        ev["note"], t0, t1))
    return out


def measure_miniv3() -> dict:
    """Every held note in the frozen Mini V3 renders, measured."""
    reports, cache = [], {}
    for case, picker in (("mono-m1a-miniv3", _windows_m1a),
                         ("mono-m5a-miniv3", _windows_m5a)):
        mpath = os.path.join(SCOREBOARD, case, "manifest.json")
        if not os.path.exists(mpath):
            reports.append({"label": case, "verdict": odp.REFUSED,
                            "refusal": "MANIFEST_ABSENT",
                            "refusal_detail": mpath})
            continue
        man = json.load(open(mpath))
        for fn, sha, label, note, t0, t1 in picker(man):
            key = (case, fn)
            if key not in cache:
                cache[key] = _clip(case, fn, sha)
            got = cache[key]
            if isinstance(got, dict):
                reports.append(dict(got, label=label))
                continue
            sr, x = got
            seg = x[max(0, int(t0 * sr)):min(len(x), int(t1 * sr))]
            f0 = note_hz(note)
            i0, i1 = held_window(seg, sr, f0)
            r = odp.drift_report(seg[i0:i1], sr, f0, sr_expected=48000,
                                 label=label)
            r["note"] = note
            r["window_s"] = [t0, t1]
            r["held_s"] = [t0 + i0 / sr, t0 + i1 / sr]
            reports.append(r)
    # the across-note baselines: same commanded note, seconds apart
    across = {}
    for name, sel in (("m1a-midi36-4.0s-apart",
                       lambda r: r["label"].startswith("m1a/phrase0/")
                       and r.get("note") == 36),
                      ("m1a-osc1-midi36-4.0s-apart",
                       lambda r: r["label"].startswith("m1a/osc1_open/")
                       and r.get("note") == 36),
                      ("m1a-osc2-midi48-4.0s-apart",
                       lambda r: r["label"].startswith("m1a/osc2_open/")
                       and r.get("note") == 48),
                      ("m5a-midi84-13.6s-apart",
                       lambda r: r.get("note") == 84)):
        across[name] = odp.across_note_drift([r for r in reports if sel(r)])
    return {"reference": "Arturia Mini V3 3.12.0.3422 (frozen renders)",
            "reports": reports, "across_note": across}


def refused_references() -> list:
    """The two references this host cannot reach, each with the precondition
    that is missing. Written out so the report cannot be read as "Surge and
    Diva showed no drift"."""
    try:
        import dawdreamer                                            # noqa: F401
        have_dd = True
    except Exception:
        have_dd = False
    vst3 = "/Library/Audio/Plug-Ins/VST3"
    return [
        {"reference": "Surge XT", "verdict": odp.REFUSED,
         "refusal": "NO_SUSTAINED_OSCILLATOR_CLIP",
         "refusal_detail":
             f"dawdreamer importable: {have_dd}; {vst3} present: "
             f"{os.path.isdir(vst3)}. refprofile/ holds 16 frozen Surge clips "
             f"and all 16 drive the FILTER from our own stepped tone through "
             f"Audio In -- Surge's oscillators appear in none of them."},
        {"reference": "u-he Diva", "verdict": odp.REFUSED,
         "refusal": "NO_FROZEN_AUDIO",
         "refusal_detail":
             f"dawdreamer importable: {have_dd}; {vst3} present: "
             f"{os.path.isdir(vst3)}. No frozen Diva audio exists in this "
             f"repository."},
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    mv = measure_miniv3()
    out = {"miniv3": mv, "refused": refused_references(),
           "probe_constants": {k: getattr(odp, k) for k in
                               ("MIN_PERIODS", "SNR_SIGMA", "SLOW_S", "MIN_TAUS",
                                "MULTI_PARTIAL_DB", "AM_DEPTH_DB", "PERIODIC_R",
                                "REPEAT_R", "EDGE_PERIODS", "HET_CASCADE")}}
    print(f"== {mv['reference']}")
    for r in mv["reports"]:
        line = f"   {r['label']:<40} {r['verdict']:<9}"
        if r["verdict"] == odp.REFUSED:
            line += f" {r['refusal']}: {r.get('refusal_detail', '')[:70]}"
        else:
            line += (f" rms {r['drift_rms_cents']:7.4f} c  "
                     f"p2p {r['drift_p2p_cents']:7.4f} c  "
                     f"floor {r['floor_cents']:7.4f} c  "
                     f"f0 {r['f0_offset_cents']:+7.4f} c  "
                     f"ripple {r['env_ripple_db']:5.2f} dB")
        print(line)
    print("   -- across-note baselines (same commanded note, seconds apart)")
    for name, ac in mv["across_note"].items():
        if ac["verdict"] == odp.REFUSED:
            print(f"      {name:<34} REFUSED {ac['refusal']} "
                  f"{ac.get('refusal_detail', '')}")
        else:
            print(f"      {name:<34} {ac['verdict']:<9} span "
                  f"{ac['span_cents']:.4f} c over {ac['n_windows']} windows, "
                  f"floor {ac['floor_cents']:.4f} c")
    for r in out["refused"]:
        print(f"== {r['reference']}: REFUSED {r['refusal']}\n   {r['refusal_detail']}")
    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        with open(a.json, "w") as fh:
            json.dump(out, fh, indent=1, sort_keys=True, default=float)
        print(f"-- wrote {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
