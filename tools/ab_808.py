#!/usr/bin/env python3
"""Listening aid: each of our sixteen drum sounds next to the classic TR-808
recording the scorecard compares it with (Fischer corpus, via run_case.REF_MAIN).

Writes, per sound:
  NN-SND-a-808.wav      the 808 reference, as recorded (mono, its own rate)
  NN-SND-b-ours.wav     ours, the model's raw output at the chip's drum gain (0.45)
  NN-SND-c-AB.wav       808, 0.4 s silence, ours -- BOTH peak-normalised to -1 dBFS
                        at 48 kHz, for listening only. Loudness in this file is
                        NOT a gain result; the raw files above carry the real levels.
and 00-all-AB.wav: every A/B pair in order.
"""
import csv, json, os, pathlib, sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
ROOT = pathlib.Path(sys.argv[1]).resolve(); OUT = pathlib.Path(sys.argv[2]); REFS = pathlib.Path(sys.argv[3])
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT / "model"))
import run_case as rc
import drums_fx as dx
OUT.mkdir(parents=True, exist_ok=True)
SR = 48000
def w16(p, x, sr):
    wavfile.write(str(p), sr, np.clip(np.round(np.asarray(x) * 32767), -32768, 32767).astype("<i2"))
def norm(x):
    pk = np.max(np.abs(x)); return x * (10 ** (-1 / 20) / pk) if pk > 0 else x
rows, allab = [], []
gap = np.zeros(int(0.4 * SR))
for i, snd in enumerate(dx.SOUND_NAMES):
    ref_x, ref_sr, rel, setting = rc.load_reference(snd, REFS)
    ours, osr = rc.render_drum_solo(snd)
    tag = f"{i:02d}-{snd}"
    w16(OUT / f"{tag}-a-808.wav", ref_x, ref_sr)
    w16(OUT / f"{tag}-b-ours.wav", ours, osr)
    r48 = resample_poly(ref_x, SR, ref_sr) if ref_sr != SR else ref_x
    ab = np.concatenate([norm(r48), gap, norm(ours), gap])
    w16(OUT / f"{tag}-c-AB.wav", ab, SR)
    allab += [ab, gap]
    rows.append(dict(n=i, sound=snd, reference=rel, reference_setting=str(setting),
                     ref_peak_dbfs=round(20 * np.log10(np.max(np.abs(ref_x))), 1),
                     ours_peak_dbfs=round(20 * np.log10(np.max(np.abs(ours))), 1)))
    print(tag, rel, setting, flush=True)
w16(OUT / "00-all-AB.wav", np.concatenate(allab), SR)
with open(OUT / "index.csv", "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
print("wrote", len(rows), "pairs")
