#!/usr/bin/env python3
"""#337 envelope response: is the Mini V3's prior-note attack shortening explained
by residual envelope level at the target onset?

Pre-declared in docs/scorecard/mono-attack-context/history-337/README.md before
any number here existed. Reads only frozen, hash-bound reference audio; renders
nothing; changes no engine, patch, scorer or tolerance.

Verdicts: VIABLE / REFUTED / REFUSED. REFUSED is not REFUTED: an instrument that
cannot stand behind a number withholds it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "model"))
import audio_measure as am  # noqa: E402

SRC = ROOT / "docs/scorecard/mono-attack-context"
HISTORY = ("repeat84_gap3p4", "from72_gap3p4", "repeat84_gap5")
NO_HISTORY = ("isolated84", "delayed84_at4p1", "delayed84_at5p7")
WINDOW_S = 0.040        # pre-declared
END_BEFORE_ON_S = 0.005  # pre-declared: window ends 5 ms before note-on
VIABLE_MIN_DB = -30.0
VIABLE_GAP_DB = 20.0
FLOOR_DB = -120.0


class Refused(Exception):
    """The instrument will not answer."""


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rms(x: np.ndarray) -> float:
    return float(math.sqrt(np.mean(np.square(x, dtype=np.float64))))


def held_rms(x, sr, on_s, off_s):
    """Median 5 ms RMS envelope over the last 40 ms of the gate (scorer's rule)."""
    env = am.rms_envelope(x, ms=5.0, sr=sr)
    on, off = int(round(on_s * sr)), int(round(off_s * sr))
    n = int(round(0.040 * sr))
    return float(np.median(env[max(on, off - n):off])) / math.sqrt(2.0)


def residual_db(x, sr, on_s, off_s, *, end_before_on_s=END_BEFORE_ON_S):
    """RMS of the 40 ms ending `end_before_on_s` before note-on, dB re held RMS.

    Floor-limited, not -inf, for a digitally silent window. REFUSES on
    non-finite samples, a window that does not fit, a silent held level, or a
    window that reaches the onset (end_before_on_s <= 0)."""
    x = np.asarray(x, dtype=np.float64)
    if not np.isfinite(x).all():
        raise Refused("non-finite sample in audio")
    if end_before_on_s <= 0:
        raise Refused("residual window must end before note-on")
    end = int(round((on_s - end_before_on_s) * sr))
    start = end - int(round(WINDOW_S * sr))
    if start < 0:
        raise Refused("less than 45 ms of audio before note-on")
    held = held_rms(x, sr, on_s, off_s)
    if not (held > 0 and math.isfinite(held)):
        raise Refused("no measurable held level")
    r = _rms(x[start:end])
    if r <= 0:
        return FLOOR_DB, True
    return max(FLOOR_DB, 20.0 * math.log10(r / held)), False


def zero_run_before_s(x, sr, on_s):
    """Length (s) of the exactly-zero run ending at note-on. Says HOW silent the
    pre-onset audio is, so a floor-limited residual is not read as a measured one."""
    x = np.asarray(x)
    end = int(round(on_s * sr))
    nz = np.flatnonzero(x[:end] != 0)
    return (end - (int(nz[-1]) + 1 if len(nz) else 0)) / sr


def attack_ms(x, sr, on_s, off_s, ms):
    """10-90 % attack on an RMS envelope of `ms` (scorer rule; ms=5 is the scorer's)."""
    env = am.rms_envelope(x, ms=ms, sr=sr)
    on, off = int(round(on_s * sr)), int(round(off_s * sr))
    w = env[on:off]
    peak = float(w.max())
    if not (peak > 0 and math.isfinite(peak)):
        raise Refused("no peak in gate")
    a = np.flatnonzero(w >= 0.1 * peak)
    b = np.flatnonzero(w >= 0.9 * peak)
    if not len(a) or not len(b):
        raise Refused("attack thresholds not reached")
    return (int(b[0]) - int(a[0])) * 1000.0 / sr


def gate(rows):
    """rows: dicts with wave, condition, r_db. Returns per-wave verdict."""
    out = {}
    for wave in sorted({r["wave"] for r in rows}):
        def med(conds):
            v = [r["r_db"] for r in rows if r["wave"] == wave and r["condition"] in conds]
            if len(v) != 9 or not np.isfinite(v).all():
                raise Refused(f"{wave}: need nine finite residuals per group, got {len(v)}")
            return float(np.median(v))
        h, n = med(HISTORY), med(NO_HISTORY)
        out[wave] = {"median_history_db": h, "median_no_history_db": n,
                     "viable": bool(h >= VIABLE_MIN_DB and h - n >= VIABLE_GAP_DB)}
    return out


def verdict(per_wave):
    return "VIABLE" if all(v["viable"] for v in per_wave.values()) else "REFUTED"


def measure(src: Path = SRC):
    rep = json.loads((src / "report.json").read_text())
    rows = []
    for row in rep["renders"]:
        path = src / row["wav"]
        if sha256(path) != row["sha256"]:
            raise Refused(f"hash mismatch: {row['wav']}")
        sr, pcm = wavfile.read(path)
        x = pcm.astype(np.float64) / 32768.0 if pcm.dtype.kind == "i" else pcm.astype(np.float64)
        t = row["events"][-1]
        on, off = t["on_s"], t["on_s"] + t["gate_s"]
        r, floored = residual_db(x, sr, on, off)
        rows.append({"wave": row["wave"], "condition": row["condition"], "repeat": row["repeat"],
                     "r_db": r, "floor_limited": floored,
                     "zero_run_before_on_s": zero_run_before_s(x, sr, on),
                     "attack_5ms": attack_ms(x, sr, on, off, 5.0),
                     "attack_1ms": attack_ms(x, sr, on, off, 1.0),
                     "sha256": row["sha256"]})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    try:
        rows = measure()
        per_wave = gate(rows)
    except Refused as e:
        print("REFUSED:", e)
        return 2
    summary = {}
    for wave in per_wave:
        for c in HISTORY + NO_HISTORY:
            sel = [r for r in rows if r["wave"] == wave and r["condition"] == c]
            summary.setdefault(wave, {})[c] = {
                k: float(np.median([r[k] for r in sel])) for k in ("r_db", "attack_5ms", "attack_1ms")}
    zr = min(r["zero_run_before_on_s"] for r in rows)
    print(f"floor-limited renders: {sum(r['floor_limited'] for r in rows)}/{len(rows)}; "
          f"shortest exactly-zero run before target note-on: {zr:.3f} s")
    res = {"verdict": verdict(per_wave), "per_wave": per_wave, "by_condition": summary, "rows": rows}
    for wave, d in summary.items():
        for c, v in d.items():
            print(f"{wave:5s} {c:16s} resid {v['r_db']:8.1f} dB  attack5ms {v['attack_5ms']:6.2f}  attack1ms {v['attack_1ms']:6.2f}")
    print(per_wave)
    print("VERDICT:", res["verdict"])
    if a.out:
        a.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
