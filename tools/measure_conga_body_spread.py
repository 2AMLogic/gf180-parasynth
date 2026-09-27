#!/usr/bin/env python3
"""How far does the real TR-808's conga BODY SPECTRUM move, and is the
scorecard's 1.04 inside that?

This preserves the historical measurement definition and published anchors.
The device side renders the checked-out model, so a new run need not reproduce
the old device values and is not a report of the current scorecard policy.

THE DEFINITION, copied from the case runner that produced D04A/D08A
(`tools/run_case.py` @ d0c1d59ee990, `model/audio_measure.py` @ 7386f4a9297f)
and re-implemented here so this tool does not depend on another agent's
working tree.  It is validated by EXACT REPRODUCTION of the two reference
numbers those results published (`--check`), which is the only way to know the
definition matches:

    x        = wavfile.read(...) -> mono mean -> / 32768.0
    prepare  = pk = max|x| ; i = first index with |x| > 0.02*pk ;
               lead = max(0, i - 1 ms) ; if lead >= 5 ms: subtract mean(x[:lead])
               y = x[lead:] ; y /= max|y|
    window   = y[0 : 0.150 s]                      <- 150 ms from the trim point
    value    = 10*log10( E[split..hi] / E[lo..split] )
               where E is audio_measure.band_energy: a 4th-order Butterworth
               band-pass applied with sosfiltfilt (zero phase), sum of squares,
               each divided by the segment's total energy.
               LC: lo=20  split=700   hi=2000
               HC: lo=20  split=1300  hi=2000
               (BAND / SPLIT_HZ from model/drum_verify.py)
    refuse   if either side holds no energy, or |value| > 80 dB.

    tolerance 3.0 dB, flat -- "the half-power convention", TOLERANCE_POLICY
    in run_case.py.  Scorecard distance = |ours - reference| / 3.0.

Nothing here reads or writes model/drums_fx.py except by importing it to
render our own side, exactly as the case runner does.

Usage:
    python tools/measure_conga_body_spread.py --refs /tmp/tr808-ref
    python tools/measure_conga_body_spread.py --refs /tmp/tr808-ref --json out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
import sys
import datetime

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))

import audio_measure as am           # noqa: E402
import drum_verify as dv             # noqa: E402
import measure_harness as mh         # noqa: E402

BAND, SPLIT_HZ = dv.BAND, dv.SPLIT_HZ
TOL_DB = 3.0

#: What D04A/D08A were measured against, and what the result files recorded.
#: Reproducing these two numbers is what says the definition matches.
PUBLISHED = {
    "LC": dict(ref_file="lc8/LC50.WAV", ref_value=-33.5108, ours_value=-36.6182,
               worst=1.04),
    "HC": dict(ref_file="hc8/HC50.WAV", ref_value=-31.2884, ours_value=-34.4438,
               worst=1.05),
}

#: Fischer's TUNING knob position for each file.  His README: the knob has
#: "11 uniformly spaced position marks … I consider these 11 marks to be 0
#: through 10", and he "sampled the 808 at five uniformly spaced positions".
#: Those five are 0, 2.5, 5, 7.5 and 10 -- so "10" is the knob at MAXIMUM, not
#: at 1.0.  Confirmed by the measured f0, which is monotone in exactly that
#: order and only in that order (LC 184.7 < 189.5 < 200.4 < 211.7 < 224.8 Hz).
FISCHER_TUNING = {"00": 0.0, "25": 2.5, "50": 5.0, "75": 7.5, "10": 10.0}


# --------------------------------------------------------------------------
# The definition
# --------------------------------------------------------------------------
def prepare(x, sr: int) -> np.ndarray:
    """Verbatim from run_case.prepare."""
    x = np.asarray(x, dtype=np.float64)
    if am.is_silent(x):
        return x
    pk = float(np.abs(x).max())
    i = int(np.argmax(np.abs(x) > 0.02 * pk))
    lead = max(0, i - int(1e-3 * sr))
    if lead >= int(5e-3 * sr):
        x = x - float(x[:lead].mean())
    y = x[lead:]
    p = float(np.abs(y).max())
    return y / p if p > 0 else y


def window(y, sr: int, t0: float, t1):
    a = int(t0 * sr)
    b = len(y) if t1 is None else min(len(y), int(t1 * sr))
    return y[a:b]


def band_ratio_db(x, sr: int, split_hz: float, lo: float, hi: float,
                  *, floor_db: float = -80.0):
    """Verbatim from run_case.band_ratio_db."""
    hi = min(sr / 2.0 - 1.0, hi)
    lo = max(lo, 1.0)
    if hi <= split_hz or split_hz <= lo:
        return None, "the split is outside the band"
    e_lo, e_hi = am.band_energy(x, ((lo, split_hz), (split_hz, hi)), sr)
    if e_lo <= 0.0 or e_hi <= 0.0:
        return None, "one side of the split holds no energy"
    r = 10.0 * math.log10(e_hi / e_lo)
    if r < floor_db or r > -floor_db:
        return None, "band ratio past the stated floor"
    return r, ""


def body_spectrum(x, sr: int, voice: str):
    """The scorecard's `body spectrum` for one signal, end to end."""
    lo, hi = BAND[voice]
    seg = window(prepare(x, sr), sr, 0.0, 0.150)
    return band_ratio_db(seg, sr, SPLIT_HZ[voice], lo, hi)


def read_wav(path: pathlib.Path):
    sr, x = wavfile.read(str(path))
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x / 32768.0, int(sr)


def f0_of(x, sr: int, voice: str):
    """The case's `Pitch` estimator, for the sensitivity study only."""
    lo, hi = BAND[voice]
    e = am.dominant_frequency(window(prepare(x, sr), sr, 0.010, 0.200), lo, hi, sr)
    return e.value if e.ok else None


# --------------------------------------------------------------------------
# Validation: an estimator that has not met a signal with a known answer is
# not a measurement.  None of these is calibrated on our model.
# --------------------------------------------------------------------------
def _sine(hz, secs, sr, amp=1.0):
    t = np.arange(int(secs * sr)) / sr
    return amp * np.sin(2 * np.pi * hz * t)


def validate_known_answer(sr: int = 48000) -> list[dict]:
    """Two steady sines either side of the split: the answer is
    20*log10(a_hi/a_lo) by construction, known without reference to anything
    in this repository.

    The methodology (compare a measurement against an analytically-known
    value) is `measure_harness.validate_known_answer` (issue #104); only the
    case construction below -- which two frequencies, which band split -- is
    conga-specific."""
    cases = []
    for voice, f_lo, f_hi in (("LC", 100.0, 1600.0), ("HC", 150.0, 1800.0)):
        for a_hi in (1.0, 0.5, 0.1, 0.0316):
            x = _sine(f_lo, 0.5, sr, 1.0) + _sine(f_hi, 0.5, sr, a_hi)
            lo, hi = BAND[voice]
            cases.append(dict(
                case=f"{voice} {f_lo:.0f}Hz vs {f_hi:.0f}Hz @ {a_hi:g}",
                signal=(x, sr),
                expected=20 * math.log10(a_hi),
                kwargs=dict(split_hz=SPLIT_HZ[voice], lo=lo, hi=hi)))

    def measure(x, sr, split_hz, lo, hi):
        return band_ratio_db(x, sr, split_hz, lo, hi)

    return mh.validate_known_answer(cases, measure)


def validate_reproduction(refdir: pathlib.Path) -> list[dict]:
    """Does this implementation reproduce the numbers D04A/D08A published for
    the SAME files?  If not, it is measuring something else and every number
    below is about a different quantity."""
    out = []
    for voice, p in PUBLISHED.items():
        path = refdir / p["ref_file"]
        if not path.exists():
            out.append(dict(voice=voice, file=p["ref_file"], status="REFUSED",
                            why="reference file not on this host"))
            continue
        x, sr = read_wav(path)
        got, why = body_spectrum(x, sr, voice)
        out.append(dict(voice=voice, file=p["ref_file"],
                        published_db=p["ref_value"],
                        recomputed_db=None if got is None else round(got, 4),
                        delta_db=None if got is None else round(got - p["ref_value"], 4),
                        why=why))
    return out


def floor_for_these_signals(refdir: pathlib.Path) -> list[dict]:
    """The estimator's floor is NOT one constant.  Issue #92: a published floor
    that was not constant withdrew a whole column of #61.  So it is reported per
    PERTURBATION and per SIGNAL, on the actual conga recordings.  Each one
    leaves the machine and the strike untouched and changes only the apparatus.

    lead_1ms_db is the one that matters and it is not small.  `prepare` trims
    to 1 ms before the onset *when there is 1 ms of signal there to keep*.  The
    Fischer files begin at the strike -- their first sample above 2 % of peak is
    sample 5-7 -- so their window opens 0.16 ms before the strike and `lead`
    comes out 0.  Our render leads with 10 ms of exact digital silence, so its
    window opens at exactly 1.00 ms.  `band_energy` filters with `sosfiltfilt`,
    whose odd extension at a segment that STARTS at full amplitude manufactures
    an edge; the high band holds ~0.05 % of the energy, so that edge is a large
    relative error there and a negligible one below the split.  Prepending
    digital silence -- which cannot change what the machine did -- therefore
    moves the number, and the effect saturates by 0.5 ms and is then flat out to
    10 ms, which is the signature of a fixed-length filter edge and not of any
    property of the sound.

    The row/perturbation/delta bookkeeping is `measure_harness.
    floor_for_these_signals` (issue #104); only which five perturbations to run,
    and what each one does to a conga recording, is conga-specific.
    """
    def signals():
        files = [("LC", f) for f in sorted((refdir / "lc8").glob("LC*.WAV"))]
        files += [("HC", f) for f in sorted((refdir / "hc8").glob("HC*.WAV"))]
        for voice, path in files:
            x, sr = read_wav(path)

            def measure(x, sr, voice=voice):
                return body_spectrum(x, sr, voice)

            def lead_1ms(x, sr, voice=voice):
                z = np.concatenate([np.zeros(int(1e-3 * sr)), x])
                v, _ = body_spectrum(z, sr, voice)
                return v

            def lead_10ms(x, sr, voice=voice):
                z = np.concatenate([np.zeros(int(1e-2 * sr)), x])
                v, _ = body_spectrum(z, sr, voice)
                return v

            def sample_rate_44k_to_48k(x, sr, voice=voice):
                y = resample_poly(x, 48000, sr)
                v, _ = body_spectrum(y, 48000, voice)
                return v

            def quantisation_16bit(x, sr, voice=voice):
                q = np.round(x * 32768.0) / 32768.0
                v, _ = body_spectrum(q, sr, voice)
                return v

            def window_10ms(x, sr, voice=voice):
                base, _ = body_spectrum(x, sr, voice)
                if base is None:
                    return None
                d = []
                for t1 in (0.140, 0.160):
                    seg = window(prepare(x, sr), sr, 0.0, t1)
                    lo, hi = BAND[voice]
                    v, _ = band_ratio_db(seg, sr, SPLIT_HZ[voice], lo, hi)
                    if v is not None:
                        d.append(abs(v - base))
                return base + max(d) if d else None

            perturbations = {
                "lead_1ms_db": lead_1ms,
                "lead_10ms_db": lead_10ms,
                "sample_rate_44k_to_48k_db": sample_rate_44k_to_48k,
                "quantisation_16bit_db": quantisation_16bit,
                "window_10ms_db": window_10ms,
            }
            yield dict(voice=voice, file=path.name), x, sr, measure, perturbations

    return mh.floor_for_these_signals(signals())


def windowed_alike(refdir: pathlib.Path, ours: list[dict]) -> list[dict]:
    """The scorecard distance when BOTH sides get the same window geometry.

    Two geometries, because neither is obviously right and the answer should
    not depend on which is picked: `lead_1ms` gives the reference our render's
    1 ms of leading silence; `lead_0ms` takes our render's leading silence away
    so its window opens at the strike, as the reference's does.

    The row/worst-score formatting is `measure_harness.windowed_alike` (issue
    #104); only the render and the two alignment geometries are conga-specific.
    """
    import drums_fx as dx
    pairs = []
    for voice, p in PUBLISHED.items():
        path = refdir / p["ref_file"]
        if not path.exists():
            continue
        x, sr = read_wav(path)
        n = int(2.2 * dx.SR)
        d = dx.DrumsFx()
        dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], 1.0)],
                                      dx.kit_with_sounds(voice)), n)
        g = dx.accent_reg(0.45)
        y = np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g),
                       dtype=np.float64) / 32768.0

        # as shipped: reference with no lead, ours with 1 ms.
        r0, _ = body_spectrum(x, sr, voice)
        o1, _ = body_spectrum(y, dx.SR, voice)

        # both at 1 ms of lead.
        r1, _ = body_spectrum(np.concatenate([np.zeros(int(1e-3 * sr)), x]), sr, voice)

        # both opening at the strike: cut our render's lead to the reference's.
        pk = float(np.abs(y).max())
        i = int(np.argmax(np.abs(y) > 0.02 * pk))
        keep = int(round((7 / 44100) * dx.SR))       # the Fischer files' own lead
        o0, _ = body_spectrum(y[max(0, i - keep):], dx.SR, voice)

        pairs.append(dict(
            label=dict(voice=voice, published_worst=p["worst"]),
            tol=TOL_DB,
            as_shipped=(r0, o1),
            variants=dict(both_lead_1ms=(r1, o1), both_lead_0ms=(r0, o0))))
    return mh.windowed_alike(pairs)


# --------------------------------------------------------------------------
# The recordings
# --------------------------------------------------------------------------
def measure_corpus(refdir: pathlib.Path, voices=("LC", "MC", "HC")) -> list[dict]:
    out = []
    for voice in voices:
        d = refdir / f"{voice.lower()}8"
        if not d.exists():
            continue
        for path in sorted(d.glob("*.WAV")):
            x, sr = read_wav(path)
            v, why = body_spectrum(x, sr, voice)
            key = path.stem[2:]
            out.append(dict(
                voice=voice, file=f"{d.name}/{path.name}",
                tuning=FISCHER_TUNING.get(key),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                sr=sr, seconds=round(len(x) / sr, 4),
                f0_hz=None if (f := f0_of(x, sr, voice)) is None else round(f, 3),
                body_spectrum_db=None if v is None else round(v, 4),
                why=why))
    return out


def measure_ours(voices=("LC", "MC", "HC")) -> list[dict]:
    """Our side, rendered here and now the way run_case.render_drum_solo does.
    drums_fx.py is imported, never written."""
    import drums_fx as dx
    out = []
    for voice in voices:
        if voice not in dx.SOUND_NAMES:
            out.append(dict(voice=voice, status="REFUSED",
                            why=f"{voice} is not one of the kit's sounds"))
            continue
        n = int(2.2 * dx.SR)
        d = dx.DrumsFx()
        dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], 1.0)],
                                      dx.kit_with_sounds(voice)), n)
        g = dx.accent_reg(0.45)
        y = np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g),
                       dtype=np.float64) / 32768.0
        v, why = body_spectrum(y, dx.SR, voice)
        out.append(dict(voice=voice, sr=dx.SR,
                        f0_hz=None if (f := f0_of(y, dx.SR, voice)) is None
                        else round(f, 3),
                        body_spectrum_db=None if v is None else round(v, 4),
                        why=why))
    return out


def sensitivity(corpus: list[dict]) -> list[dict]:
    """d(body spectrum)/d(f0), as a straight-line fit across Fischer's TUNING
    series for one voice.  With it, docs/tr808-reference.md section 1.7's
    +-10 % component tolerance on f0 becomes a dB figure -- a PROPAGATED bound
    on unit-to-unit spread, not a measurement of one."""
    out = []
    for voice in sorted({r["voice"] for r in corpus}):
        rows = [r for r in corpus
                if r["voice"] == voice and r["f0_hz"] and r["body_spectrum_db"] is not None]
        if len(rows) < 3:
            continue
        f = np.array([r["f0_hz"] for r in rows])
        b = np.array([r["body_spectrum_db"] for r in rows])
        slope, icept = np.polyfit(f, b, 1)
        resid = float(np.abs(b - (slope * f + icept)).max())
        mid = [r for r in rows if r["tuning"] == 5.0]
        f_mid = mid[0]["f0_hz"] if mid else float(np.median(f))
        out.append(dict(voice=voice, n=len(rows),
                        f0_range_hz=[round(float(f.min()), 2), round(float(f.max()), 2)],
                        body_range_db=[round(float(b.min()), 3), round(float(b.max()), 3)],
                        slope_db_per_hz=round(float(slope), 5),
                        max_residual_db=round(resid, 3),
                        f0_mid_hz=round(float(f_mid), 2),
                        propagated_pm10pct_f0_db=round(float(abs(slope) * 0.10 * f_mid), 3)))
    return out


def descent_test(refdir: pathlib.Path, candidates: pathlib.Path) -> list[dict]:
    """Is a candidate "808 conga" recording a RE-PRESSING of the Fischer set?

    "Different pressings are not different machines."  A pack that says 808 on
    the tin is only a second machine if it is a second recording, and that is
    checkable: peak normalised cross-correlation of the first 150 ms against
    every Fischer file of the same voice, allowing a small resampling ratio
    because a re-pressing is usually pitch-shifted.  A best correlation near
    1.0 is the same recording and carries no independent information.

    The cross-correlation/re-pressing check itself is `measure_harness.
    descent_test` (issue #104) -- it is the check every future reference-corpus
    claim should run, not something conga-specific; only voice classification
    by filename and the sample-rate normalisation to 44.1 kHz stay here.

    Needs `soundfile` for non-WAV candidates; skips what it cannot read."""

    def classify(path):
        voice = next((v for v in ("LC", "MC", "HC")
                      if any(t in path.name.lower() for t in
                             {"LC": ("low",), "MC": ("mid",), "HC": ("hi", "high")}[v])), None)
        if voice is None or "conga" not in path.name.lower():
            return None
        return dict(voice=voice,
                    refs=sorted((refdir / f"{voice.lower()}8").glob("*.WAV")))

    def measure(x, sr, info):
        return body_spectrum(x, sr, info["voice"])

    def read_candidate(path: pathlib.Path):
        """soundfile, mixed to mono, resampled to 44.1 kHz -- the rate this
        repo's `prepare`/`body_spectrum` definition is exercised at -- exactly
        as the original conga-only implementation did."""
        import soundfile as sf
        x, sr = sf.read(str(path))
        if np.ndim(x) > 1:
            x = x.mean(axis=1)
        x = np.asarray(x, dtype=float)
        if sr != 44100:
            from fractions import Fraction
            f = Fraction(44100, int(sr)).limit_denominator(1000)
            x, sr = resample_poly(x, f.numerator, f.denominator), 44100
        return x, sr

    try:
        import soundfile  # noqa: F401
    except ImportError:
        return [dict(status="REFUSED", why="soundfile is not installed")]

    rows = mh.descent_test(candidates, classify, read_wav, prepare,
                           read_candidate=read_candidate,
                           measure=measure, measure_field="body_spectrum_db",
                           match_threshold=mh.MATCH_THRESHOLD)
    for r in rows:
        if "best_match" in r:
            r["verdict"] = ("a re-pressing of the Fischer set -- not a second machine"
                            if r["best_correlation"] >= mh.MATCH_THRESHOLD else
                            "no Fischer file matches it")
    return rows


def within_10pct_of_f0(corpus: list[dict]) -> list[dict]:
    """The recordings whose f0 lies inside +-10 % of the anchor's -- section
    1.7's own component tolerance on f0 -- and the body-spectrum range across
    them.  A direct read-off, with no straight-line fit to go wrong.

    What it is: how far the number moves when THIS machine's conga resonator is
    moved across the same f0 span that separates two units built to the same
    schematic.  What it is NOT: a measurement of two units.  Section 1.7 also
    puts +-50 % on Q, and the TUNING pot does not move Q at all, so this is a
    lower bound on unit-to-unit spread and not an estimate of it."""
    out = []
    for voice in ("LC", "MC", "HC"):
        rows = [r for r in corpus if r["voice"] == voice and r["f0_hz"]
                and r["body_spectrum_db"] is not None]
        anchor = next((r for r in rows if r["tuning"] == 5.0), None)
        if not anchor:
            continue
        f0 = anchor["f0_hz"]
        inb = [r for r in rows if 0.9 * f0 <= r["f0_hz"] <= 1.1 * f0]
        b = [r["body_spectrum_db"] for r in inb]
        out.append(dict(voice=voice, anchor_f0_hz=f0,
                        f0_window_hz=[round(0.9 * f0, 2), round(1.1 * f0, 2)],
                        files=[r["file"] for r in inb],
                        excluded=[r["file"] for r in rows if r not in inb],
                        body_min_db=round(min(b), 4), body_max_db=round(max(b), 4),
                        range_db=round(max(b) - min(b), 4)))
    return out


def spread(values: list[float]) -> dict:
    a = np.array(values, dtype=float)
    return dict(n=len(a), min=round(float(a.min()), 4), max=round(float(a.max()), 4),
                range_db=round(float(a.max() - a.min()), 4),
                median=round(float(np.median(a)), 4),
                mean=round(float(a.mean()), 4),
                sd_db=round(float(a.std(ddof=1)), 4) if len(a) > 1 else None,
                iqr_db=round(float(np.percentile(a, 75) - np.percentile(a, 25)), 4))


# --------------------------------------------------------------------------
def provenance(refdir: pathlib.Path) -> dict:
    def git(*a):
        try:
            return subprocess.run(["git", "-C", str(ROOT), *a], capture_output=True,
                                  text=True, check=True).stdout.strip()
        except Exception:
            return ""

    def sha(p):
        q = ROOT / p
        return ("sha256:" + hashlib.sha256(q.read_bytes()).hexdigest()[:16]
                if q.exists() else "absent")

    return dict(
        commit=git("rev-parse", "--short=12", "HEAD"),
        branch=git("rev-parse", "--abbrev-ref", "HEAD"),
        dirty=bool(git("status", "--porcelain")),
        run_at=datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        python=sys.version.split()[0],
        refs=str(refdir),
        inputs={p: sha(p) for p in ("model/drums_fx.py", "model/audio_measure.py",
                                    "model/drum_verify.py",
                                    "tools/measure_conga_body_spread.py")},
    )


def validation_status(report: dict) -> int:
    """0 validated, 1 wrong known answer, 2 missing measurement.

    The recovered --check printed its controls and returned zero regardless
    of their results. A report is usable only after both controls pass.
    """
    known = report["known_answer"]
    reproduction = report["reproduction"]
    if any(r.get("error_db") is not None and
           (not math.isfinite(r["error_db"]) or abs(r["error_db"]) > 0.1)
           for r in known):
        return 1
    if (len(known) != 8 or len(reproduction) != len(PUBLISHED) or
            any(r.get("error_db") is None for r in known) or
            any(r.get("delta_db") is None for r in reproduction)):
        return 2
    if any(not math.isfinite(r["delta_db"]) or abs(r["delta_db"]) > 0.001
           for r in reproduction):
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refs", default="/tmp/tr808-ref", type=pathlib.Path)
    ap.add_argument("--json", type=pathlib.Path)
    ap.add_argument("--descent", type=pathlib.Path,
                    help="directory of candidate '808 conga' files to test for "
                         "descent from the Fischer set")
    ap.add_argument("--check", action="store_true",
                    help="validation only: known-answer signals and reproduction")
    args = ap.parse_args(argv)

    refdir = args.refs
    if not refdir.exists():
        print(f"REFUSED  no reference corpus at {refdir}", file=sys.stderr)
        return 2

    report = dict(provenance=provenance(refdir))
    report["definition"] = (
        "10*log10(E[split..2000] / E[20..split]) over the first 150 ms after "
        "prepare()'s onset trim, band_energy 4th-order Butterworth sosfiltfilt; "
        "LC split 700 Hz, HC split 1300 Hz; tolerance 3.0 dB flat")

    report["known_answer"] = validate_known_answer()
    report["reproduction"] = validate_reproduction(refdir)
    report["outcome_code"] = validation_status(report)
    if report["outcome_code"]:
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        print("REFUSED: known-answer or historical-reproduction control did not pass",
              file=sys.stderr)
        return report["outcome_code"]

    print("== estimator against signals with a known answer "
          "(two sines, no model of ours involved) ==")
    for r in report["known_answer"]:
        print(f"  {r['case']:38s} expected {r['expected_db']:8.3f}  "
              f"got {r['measured_db']:8.3f}  err {r['error_db']:+.3f} dB")
    worst_known = max(abs(r["error_db"]) for r in report["known_answer"]
                      if r["error_db"] is not None)
    print(f"  worst |error| on a known answer: {worst_known:.3f} dB")

    print("\n== does this reproduce what D04A/D08A published for the same file? ==")
    for r in report["reproduction"]:
        if r.get("recomputed_db") is None:
            print(f"  {r['voice']}  REFUSED: {r.get('why')}")
        else:
            print(f"  {r['voice']} {r['file']:14s} published {r['published_db']:9.4f}  "
                  f"recomputed {r['recomputed_db']:9.4f}  delta {r['delta_db']:+.4f} dB")

    if args.check:
        if args.json:
            args.json.write_text(json.dumps(report, indent=2))
        return 0

    report["floor"] = floor_for_these_signals(refdir)
    print("\n== the estimator's floor ON THESE SIGNALS, per perturbation "
          "(dB change in body spectrum; the machine did not move) ==")
    print(f"  {'file':14s} {'+1ms lead':>10s} {'+10ms lead':>11s} "
          f"{'44.1k->48k':>11s} {'16-bit':>9s} {'win+-10ms':>10s}")
    for r in report["floor"]:
        print(f"  {r['file']:14s} {r['lead_1ms_db']:+10.3f} {r['lead_10ms_db']:+11.3f} "
              f"{r['sample_rate_44k_to_48k_db']:+11.3f} "
              f"{r['quantisation_16bit_db']:+9.3f} {r['window_10ms_db']:10.3f}")
    worst_lead = max(abs(r["lead_1ms_db"]) for r in report["floor"]
                     if r["lead_1ms_db"] is not None)
    print(f"  worst window-start term: {worst_lead:.3f} dB, against a "
          f"{TOL_DB:.1f} dB tolerance")

    report["corpus"] = measure_corpus(refdir)
    report["ours"] = measure_ours()
    report["sensitivity"] = sensitivity(report["corpus"])

    print("\n== every real-hardware conga recording on this host ==")
    print(f"  {'file':16s} {'TUNING':>7s} {'f0 Hz':>9s} {'body dB':>9s}  sha256")
    for r in report["corpus"]:
        print(f"  {r['file']:16s} {r['tuning'] if r['tuning'] is not None else '?':>7} "
              f"{r['f0_hz']:9.2f} {r['body_spectrum_db']:9.3f}  {r['sha256']}")

    print("\n== ours, rendered here ==")
    for r in report["ours"]:
        if r.get("body_spectrum_db") is None:
            print(f"  {r['voice']}  REFUSED: {r.get('why')}")
        else:
            print(f"  {r['voice']:3s} f0 {r['f0_hz']:8.2f} Hz   "
                  f"body {r['body_spectrum_db']:9.3f} dB")

    report["spread"] = {}
    for voice in ("LC", "MC", "HC"):
        vals = [r["body_spectrum_db"] for r in report["corpus"]
                if r["voice"] == voice and r["body_spectrum_db"] is not None]
        if vals:
            report["spread"][voice] = spread(vals)

    print("\n== spread of the reference recordings, per voice "
          "(one machine, five TUNING positions -- see the report) ==")
    for voice, s in report["spread"].items():
        print(f"  {voice}  n={s['n']}  range {s['min']:.3f} .. {s['max']:.3f} dB "
              f"= {s['range_db']:.3f} dB   sd {s['sd_db']:.3f}  iqr {s['iqr_db']:.3f}")

    report["within_10pct"] = within_10pct_of_f0(report["corpus"])
    print("\n== the recordings inside +-10 % of the anchor's f0 "
          "(section 1.7's component tolerance on f0), direct read-off ==")
    for r in report["within_10pct"]:
        print(f"  {r['voice']}  f0 {r['f0_window_hz'][0]:.0f}-{r['f0_window_hz'][1]:.0f} Hz, "
              f"{len(r['files'])} recordings: body {r['body_min_db']:.3f} .. "
              f"{r['body_max_db']:.3f} dB = {r['range_db']:.3f} dB range "
              f"(tolerance {TOL_DB:.1f})")

    print("\n== d(body spectrum)/d(f0), and section 1.7's +-10 % propagated ==")
    for r in report["sensitivity"]:
        print(f"  {r['voice']}  slope {r['slope_db_per_hz']:+.4f} dB/Hz "
              f"(max residual {r['max_residual_db']:.2f} dB over "
              f"{r['f0_range_hz'][0]:.0f}-{r['f0_range_hz'][1]:.0f} Hz)  "
              f"-> +-10 % of f0 = +-{r['propagated_pm10pct_f0_db']:.2f} dB")

    report["windowed_alike"] = windowed_alike(refdir, report["ours"])
    print("\n== the scorecard distance when both sides get the SAME window ==")
    for r in report["windowed_alike"]:
        for k in ("as_shipped", "both_lead_1ms", "both_lead_0ms"):
            d = r[k]
            print(f"  {r['voice']}  {k:14s} ref {d['ref_db']:8.3f}  "
                  f"ours {d['ours_db']:8.3f}  worst {d['worst']:.3f}"
                  + ("   <- historical window definition" if k == "as_shipped" else ""))

    print("\n== the scorecard distance, recomputed ==")
    for voice, p in PUBLISHED.items():
        ours = next((r["body_spectrum_db"] for r in report["ours"]
                     if r["voice"] == voice), None)
        ref = next((r["body_spectrum_db"] for r in report["corpus"]
                    if r["file"] == p["ref_file"]), None)
        if ours is None or ref is None:
            continue
        print(f"  {voice}  ours {ours:8.3f}  ref {ref:8.3f}  "
              f"|err| {abs(ours - ref):5.3f} dB / {TOL_DB:.1f} = "
              f"{abs(ours - ref) / TOL_DB:.3f}   (published {p['worst']})")

    if args.descent:
        report["descent"] = descent_test(refdir, args.descent)
        print("\n== is any other '808 conga' on this host a second MACHINE, "
              "or a re-pressing? ==")
        for r in report["descent"]:
            if "best_match" not in r:
                print(f"  {r.get('file', '?')}: {r.get('status')} {r.get('why', '')}")
                continue
            print(f"  {r['file']:24s} body {r['body_spectrum_db']:8.3f} dB   best "
                  f"{r['best_match']} r={r['best_correlation']:.3f} "
                  f"@x{r['at_resample_ratio']:.4f}  -> {r['verdict']}")

    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
