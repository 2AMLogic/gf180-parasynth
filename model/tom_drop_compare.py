#!/usr/bin/env python3
"""The model's tom pitch trajectory against the real machine's, period by period.

    python model/tom_drop_compare.py --json out.json
    python model/tom_drop_compare.py --stage before --json docs/tom-pitch-drop-before.json

WHAT THE COMPARATOR IS, stated here because a figure without one is decoration:
every number on the reference side of every row below is `tom_pitch_probe`'s
reading of a NAMED WAV member of the 808 From Mars clean-digital set, recorded
from a real TR-808. Nothing is compared against x1.7, against the contract, or
against a previous revision of the model. Closeness to x1.7 measures nothing --
x1.7 is the defect.

Both sides go through the SAME estimator. The model renders at 48 kHz and the
recordings are 44.1 kHz, so the model's render is rate-converted (polyphase,
147/160) before measurement. A rate conversion does not move a frequency in Hz,
but asserting that is cheaper than believing it, so `validate_resample()` puts
three known drops through the same path and refuses if any of them comes back
wrong. It runs before any comparison, every time.

WHAT IS COMPARED

  the accent axis  three model accents against the pack's A / B / C, at the
                   TUNING pot's centre
  the tuning axis  the pot's two ends and its centre at one accent, the model
                   retuned to the frequency the machine was actually at

The model is retuned to the RECORDING's measured settled f0 for every row, so
the trajectories are compared at the same pitch and a residual is a residual in
the sweep, not in the tuning.
"""
from __future__ import annotations
import argparse, csv, json, math, pathlib, subprocess, sys
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "audition"))
import tom_pitch_probe as P

REPO = HERE.parent
CACHE = REPO / "refaudio" / "cache" / "808-from-mars"
INDEX = REPO / "refaudio" / "index" / "808-from-mars.tsv"
STEM = "808 From Mars/WAV/01. Individual Hits"
FOLDER = {"LT": "03. Low Tom", "MT": "04. Mid Tom", "HT": "05. Hi Tom",
          "LC": "06. Low Conga", "MC": "07. Mid Conga", "HC": "08. Hi Conga"}
FILE_STEM = {"LT": "Tom Low", "MT": "Tom Mid", "HT": "Tom Hi",
             "LC": "Conga Low", "MC": "Conga Mid", "HC": "Conga Hi"}
# The pack's own notes: A = No Accent, B = Accent, C = More Accent.
# model/tom_drop_fit.py's accent map, repeated here so a reader of one file does
# not have to open the other to know what is being lined up with what.
ACCENT_MAP = {"A": 1.0, "B": 1.4, "C": 2.0}
CENTRE_TUNING = 6          # 01..11 across the pot, so 06 is its centre
POT_ENDS = (1, 6, 11)


class Refused(Exception):
    """A precondition is not met; nothing was attempted."""


# --------------------------------------------------------- preconditions ----

def indexed_sizes() -> dict:
    if not INDEX.exists():
        raise Refused(f"{INDEX} is missing: nothing vouches for the audio")
    with INDEX.open(encoding="utf-8", newline="") as fh:
        return {r["path"]: int(r["bytes"]) for r in csv.DictReader(fh, delimiter="\t")}


def member(voice: str, accent: str, tuning: int) -> str:
    return f"{STEM}/{FOLDER[voice]}/Clean/Digital/{accent}/{FILE_STEM[voice]} {accent} 808 {tuning:02d}.wav"


def reference_wav(voice: str, accent: str, tuning: int, sizes: dict) -> pathlib.Path:
    """One recording, REFUSED unless it is byte-for-byte the size the committed
    index says. `tools/refaudio_local.py` hashes the archive; this is the
    per-member half of that check, asserted again at the point of use because a
    cache is not an archive and this module is not the one that filled it."""
    m = member(voice, accent, tuning)
    p = CACHE / m
    if m not in sizes:
        raise Refused(f"{m!r} is not in {INDEX.name}: unindexed audio is not evidence")
    if not p.exists():
        raise Refused(f"{p} is not in the cache; fetch it with tools/refaudio_local.py "
                      f"(or tools/refaudio_fetch.py) first")
    if p.stat().st_size != sizes[m]:
        raise Refused(f"{p.name} is {p.stat().st_size} bytes, the index says {sizes[m]}")
    return p


def validate_resample(tol: float = 0.01) -> dict:
    """Put a KNOWN drop through the model's rate conversion and check it survives.

    The model renders at 48 kHz and the machine was recorded at 44.1 kHz. A rate
    conversion is frequency-preserving in Hz, which is exactly the kind of claim
    this repository has been wrong about before, so it is measured: three
    synthetic toms carrying x1.06, x1.24 and x1.70 at 48 kHz, converted, read by
    the same probe. REFUSES if any excess moves by more than `tol` of itself."""
    out = []
    for ratio in (1.06, 1.24, 1.70):
        y = P.synth_tom(90.0, 25.0, P.tau_from_q(90.0, 25.0), ratio=ratio,
                        drop_ms=60.0, sr=48000, seed=5, trim=False)
        r = P.measure(to_44100(y), P.SR_EXPECTED, label=f"resample x{ratio}")
        if r["verdict"] == "REFUSED":
            raise Refused(f"the resample control refused at x{ratio}: {r['why']}")
        got = r["ratio_at_onset"]
        rel = abs((got - 1.0) - (ratio - 1.0)) / (ratio - 1.0)
        out.append({"ratio": ratio, "recovered": got, "rel_error_of_excess": rel})
        if rel > tol:
            raise Refused(f"48 kHz -> 44.1 kHz moved a known x{ratio} drop to x{got:.4f} "
                          f"({100*rel:.2f} % of the excess, over {100*tol:.0f} %)")
    return {"tolerance_rel": tol, "cases": out}


def trim_dead_tail(x: np.ndarray) -> np.ndarray:
    """Cut the model render's EXACTLY ZERO tail before measuring it.

    The model is integer and its ring reaches 0 and stays there; the recordings
    have a dither floor and never do. `tom_pitch_probe` measures each file's own
    floor from its last 30 ms, so a digitally silent tail reports a floor of
    -400 dBFS, every period in the file then clears floor + 40 dB including the
    ones that are pure quantisation residue, and the coarse f0 taken from them
    lands far enough out that the sanity band throws the REAL periods away --
    six of twenty rows refused as `only 0 clean periods after the pulse` before
    this was found. Trimming the dead tail leaves the probe the same kind of
    floor the recordings give it. No sample inside the ring is touched."""
    nz = np.nonzero(np.asarray(x) != 0.0)[0]
    return x if len(nz) == 0 else x[:nz[-1] + 1]


def to_44100(x: np.ndarray) -> np.ndarray:
    from scipy.signal import resample_poly
    return resample_poly(np.asarray(x, dtype=np.float64), 147, 160)


# ------------------------------------------------------------- the model ----

def render(voice: str, accent: float, f0_hz: float | None = None,
           seconds: float = 2.2) -> np.ndarray:
    """One hit of one tom/conga through the register interface, as the scorecard
    renders it (`tools/run_case.render_drum_solo`): the kit that ships, the
    circuit switched to this sound, both drum buses at 0.45.

    `f0_hz` retunes the circuit first, which is what the TUNING pot does and
    what `hit_writes` reads back out of the image."""
    import drums_fx as dx
    img = dict(dx.kit_with_sounds(voice))
    mode = {"LT": dx.M_LT, "LC": dx.M_LT, "MT": dx.M_MT, "MC": dx.M_MT,
            "HT": dx.M_HT, "HC": dx.M_HT}[voice]
    if f0_hz is not None:
        q = dx.TOM_PRESET[voice][1]
        amp = dx._kit_amp(sorted(img.items()), mode)
        for a, v in dx.mode_writes(mode, f0_hz, q, amp):
            img[a] = v
    kit = sorted(img.items())
    n = int(seconds * dx.SR)
    d = dx.DrumsFx()
    dm, bd = d.play(dx.hit_writes([(int(0.01 * dx.SR), dx.SOUND_STOP[voice], accent)], kit), n)
    g = dx.accent_reg(0.45)
    return np.asarray(dx.output_fx(np.zeros(n), 0, dm, g, bd, g), dtype=np.float64) / 32768.0


# ----------------------------------------------------------- one comparison --

def _thin(traj, t_max: float = 0.20) -> list:
    """(t since onset in ms, f in Hz) for the published figure. Both crossing
    directions are kept -- they are two interleaved estimates of the same
    trajectory and hiding one would hide the estimator's own scatter."""
    return [[round(1e3 * t, 4), round(f, 4)] for t, f, _a, _d in traj if 0 < t <= t_max]


def compare_one(voice: str, accent_level: str, tuning: int, sizes: dict) -> dict:
    ref_path = reference_wav(voice, accent_level, tuning, sizes)
    sr, x = P.read_wav(str(ref_path))
    ref = P.measure(x, sr, label=ref_path.name)
    row = {"voice": voice, "accent_level": accent_level, "tuning": tuning,
           "model_accent": ACCENT_MAP[accent_level],
           "comparator": f"808-from-mars member {member(voice, accent_level, tuning)!r}, "
                         f"measured by tom_pitch_probe -- NOT the contract's x1.7",
           "reference": {"member": member(voice, accent_level, tuning),
                         "bytes": ref_path.stat().st_size, "verdict": ref["verdict"],
                         "why": ref.get("why")}}
    if ref["verdict"] == "REFUSED":
        row["skipped"] = "the reference row refuses; nothing to compare against"
        return row
    f_ref = ref["f_settled_hz"]
    row["reference"].update({
        "f_settled_hz": f_ref, "floor_hz": ref["floor_hz"],
        "ratio_at_onset": ref.get("ratio_at_onset"),
        "ratio_first_period": ref.get("ratio_first_period"),
        "tau_ms": ref.get("fit_tau_ms"), "traj": _thin(ref["traj"])})

    y = to_44100(trim_dead_tail(render(voice, ACCENT_MAP[accent_level], f0_hz=f_ref)))
    ours = P.measure(y, P.SR_EXPECTED, label=f"model {voice} accent {ACCENT_MAP[accent_level]}")
    row["ours"] = {"verdict": ours["verdict"], "why": ours.get("why")}
    if ours["verdict"] == "REFUSED":
        return row
    row["ours"].update({
        "f_settled_hz": ours["f_settled_hz"], "floor_hz": ours["floor_hz"],
        "ratio_at_onset": ours.get("ratio_at_onset"),
        "ratio_first_period": ours.get("ratio_first_period"),
        "tau_ms": ours.get("fit_tau_ms"), "traj": _thin(ours["traj"])})
    e_ref = (ref.get("ratio_at_onset") or float("nan")) - 1.0
    e_our = (ours.get("ratio_at_onset") or float("nan")) - 1.0
    row["excess_error"] = float(e_our - e_ref)
    row["excess_ratio_ours_over_ref"] = float(e_our / e_ref) if e_ref else None
    row["f_settled_error_hz"] = float(ours["f_settled_hz"] - f_ref)
    # the trajectory residual itself, which is the thing a scalar cannot say:
    # the model resampled onto the recording's own period midpoints.
    tr, fr = np.array([p[0] for p in row["reference"]["traj"]]), np.array([p[1] for p in row["reference"]["traj"]])
    to, fo = np.array([p[0] for p in row["ours"]["traj"]]), np.array([p[1] for p in row["ours"]["traj"]])
    if len(tr) > 2 and len(to) > 2:
        o = np.argsort(to)
        fi = np.interp(tr, to[o], fo[o])
        d = (fi - fr) / f_ref
        row["traj_residual_frac"] = {"rms": float(np.sqrt(np.mean(d ** 2))),
                                     "max_abs": float(np.max(np.abs(d))),
                                     "first_20ms_rms": float(np.sqrt(np.mean(d[tr <= 20.0] ** 2)))
                                     if (tr <= 20.0).any() else None}
    return row


def provenance() -> dict:
    def sh(*a):
        try:
            return subprocess.run(a, cwd=REPO, capture_output=True, text=True, timeout=20).stdout.strip()
        except Exception:
            return None
    import hashlib
    h = hashlib.sha256((REPO / "model" / "drums_fx.py").read_bytes()).hexdigest()[:16]
    return {"commit": sh("git", "rev-parse", "HEAD"),
            "branch": sh("git", "rev-parse", "--abbrev-ref", "HEAD"),
            # git unanswered (empty stdout, nonzero rc) cannot be called clean (#615)
            "dirty": bool(sh("git", "status", "--porcelain")) or not sh("git", "rev-parse", "HEAD"),
            "drums_fx_sha256": h,
            "index_sha256": hashlib.sha256(INDEX.read_bytes()).hexdigest(),
            "python": sys.version.split()[0]}


def run(stage: str) -> dict:
    sizes = indexed_sizes()
    out = {"stage": stage, "provenance": provenance(),
           "comparator": "808 From Mars clean-digital members named per row, measured by "
                         "model/tom_pitch_probe.py. Never the contract's x1.7.",
           "resample_control": validate_resample(),
           "accent_map": ACCENT_MAP, "rows": []}
    import drums_fx as dx
    out["shipped_constants"] = {k: getattr(dx, k) for k in dir(dx)
                                if k.startswith("TOM_DROP") and not callable(getattr(dx, k))}
    for voice in ("LT", "MT", "HT"):
        for lvl in ("A", "B", "C"):
            out["rows"].append(compare_one(voice, lvl, CENTRE_TUNING, sizes))
        for t in POT_ENDS:
            if t == CENTRE_TUNING:
                continue
            out["rows"].append(compare_one(voice, "C", t, sizes))
    for voice in ("LC", "MC", "HC"):
        for lvl in ("A", "B"):
            out["rows"].append(compare_one(voice, lvl, CENTRE_TUNING, sizes))
    ok = [r for r in out["rows"] if "excess_error" in r]
    if ok:
        e = np.array([r["excess_error"] for r in ok])
        out["summary"] = {"n": len(ok), "bias": float(e.mean()),
                          "rms": float(np.sqrt(np.mean(e ** 2))),
                          "max_abs": float(np.max(np.abs(e))),
                          "worst_row": max(ok, key=lambda r: abs(r["excess_error"]))["reference"]["member"]}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--stage", default="current", help="a label for this run, e.g. before / after")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    try:
        r = run(a.stage)
    except Refused as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"stage: {r['stage']}   drums_fx {r['provenance']['drums_fx_sha256']}   "
          f"constants {r['shipped_constants']}")
    print("resample control: " + ", ".join(
        f"x{c['ratio']} -> x{c['recovered']:.4f} ({100*c['rel_error_of_excess']:.2f} %)"
        for c in r["resample_control"]["cases"]))
    print(f"\n{'voice':5s} {'lvl':3s} {'tun':>3s} {'acc':>4s} {'ref f0':>8s} "
          f"{'REF x':>7s} {'OURS x':>7s} {'err':>8s} {'ours/ref':>9s} {'traj rms':>9s}")
    for row in r["rows"]:
        if "excess_error" not in row:
            print(f"{row['voice']:5s} {row['accent_level']:3s} {row['tuning']:3d} "
                  f"{'':>4s} -- {row.get('skipped') or row.get('ours', {}).get('why', 'no reading')}")
            continue
        tr = row.get("traj_residual_frac", {})
        print(f"{row['voice']:5s} {row['accent_level']:3s} {row['tuning']:3d} "
              f"{row['model_accent']:4.1f} {row['reference']['f_settled_hz']:8.2f} "
              f"{row['reference']['ratio_at_onset']:7.4f} {row['ours']['ratio_at_onset']:7.4f} "
              f"{row['excess_error']:+8.4f} {row['excess_ratio_ours_over_ref']:9.2f} "
              f"{tr.get('rms', float('nan')):9.4f}")
    s = r.get("summary")
    if s:
        print(f"\nagainst the machine, over {s['n']} rows: bias {s['bias']:+.4f}, "
              f"rms {s['rms']:.4f}, worst {s['max_abs']:.4f} ({s['worst_row'].split('/')[-1]})")
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
