#!/usr/bin/env python3
"""Loudness-matched cymbal listening pack (#369): 808 / shipped / candidate at one setting.

For listening only; the measurements decide promotion. Each file is
808, shipped, 808, candidate (0.25 s gaps), every hit matched to the 808's
K-weighted loudness (tools/ab_808_loud.py's BS.1770 K filter and gating), then
scaled together to -1 dBFS peak. Raw renders are written alongside.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "model"), str(ROOT / "tools")]
import cymbal_bands as cb  # noqa: E402
import drums_fx as dx  # noqa: E402
import run_case as rc  # noqa: E402
import cymbal_candidate as cc  # noqa: E402
import cymbal_candidate_eval as ev  # noqa: E402

SR = 48000


def _kweight(x):
    from scipy.signal import lfilter
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(b2, a2, lfilter(b1, a1, x))


def _loud(x):
    y = _kweight(x)
    blk = int(0.05 * SR)
    e = np.array([np.mean(y[i:i + blk] ** 2) for i in range(0, len(y) - blk, blk // 2)])
    g = e >= e.max() * 10 ** (-2)
    return 10 * np.log10(np.mean(e[g]) + 1e-20)


def _trim(x):
    env = np.abs(x)
    idx = np.nonzero(env > env.max() * 1e-3)[0]
    return x[idx[0]: idx[-1] + int(0.05 * SR)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--refs", default=str(rc.configured_refs()))
    ap.add_argument("--out", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    cal = ev.calibrate()
    ref, rsr = cb._load(pathlib.Path(a.refs) / "cy8" / "CY5025.WAV")
    if rsr != SR:
        from scipy.signal import resample_poly
        ref = resample_poly(ref, SR, rsr)
    ship, _ = rc.render_drum_solo("CY")
    cand, _ = cc.render(ev.kit_with_levels(cal["amps"]), "CY")
    a.out.mkdir(parents=True, exist_ok=True)
    clips = {"808": _trim(ref), "shipped": _trim(ship), "candidate": _trim(cand)}
    lr = _loud(clips["808"])
    for k in ("shipped", "candidate"):
        clips[k] = clips[k] * 10 ** ((lr - _loud(clips[k])) / 20)
    pk = max(np.abs(v).max() for v in clips.values())
    for k in clips:
        clips[k] = clips[k] * (10 ** (-1 / 20) / pk)
        wavfile.write(a.out / f"CY5025-{k}.wav", SR, np.round(clips[k] * 32767).astype("<i2"))
    gap = np.zeros(int(0.25 * SR))
    ab = np.concatenate([clips["808"], gap, clips["shipped"], gap, clips["808"], gap, clips["candidate"], gap])
    wavfile.write(a.out / "CY5025-AB-808-shipped-808-candidate.wav", SR, np.round(ab * 32767).astype("<i2"))
    (a.out / "README.txt").write_text(
        "Cymbal at the shipped setting against Fischer CY5025 (TONE 5.0, DECAY 2.5).\n"
        "CY5025-AB-808-shipped-808-candidate.wav: 808, shipped, 808, candidate; each matched to the\n"
        "808's K-weighted loudness, then scaled together to -1 dBFS peak. Listening only; the\n"
        "measurements decide promotion (docs/scorecard/cymbal-369/candidate/). Other TONE/DECAY\n"
        "settings wait for the knob-law repair (the knob render is not the instrument, #371).\n")
    print("wrote", sorted(p.name for p in a.out.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
