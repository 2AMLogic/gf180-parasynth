#!/usr/bin/env python3
"""Loudness-matched A/B for listening. Reads the raw -a-808 / -b-ours WAVs written by
ab_808.py and writes NN-SND-c-AB.wav as 808, ours, 808, ours (0.25 s gaps), both matched
to the same K-weighted loudness (ITU-R BS.1770 K filter), measured only over the part of
the hit within 20 dB of its loudest 50 ms block (so tails/silence don't skew it).
Both sides are then scaled together so the loudest peak is -1 dBFS: no clipping, and the
two stay matched to each other. For listening only; raw levels are in -a/-b files."""
import glob, os, sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import bilinear, lfilter, resample_poly
D = sys.argv[1]; SR = 48000
def load(p):
    sr, x = wavfile.read(p); x = x.astype(np.float64) / 32768.0
    return resample_poly(x, SR, sr) if sr != SR else x
def kweight(x):
    # BS.1770 K-weighting at 48 kHz (standard published coefficients)
    b1, a1 = [1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]
    b2, a2 = [1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621]
    return lfilter(b2, a2, lfilter(b1, a1, x))
def loud(x):
    y = kweight(x); blk = int(0.05 * SR)
    e = np.array([np.mean(y[i:i+blk]**2) for i in range(0, len(y) - blk, blk // 2)])
    gate = e >= e.max() * 10 ** (-20 / 10)
    return 10 * np.log10(np.mean(e[gate]) + 1e-20)
gap = np.zeros(int(0.25 * SR)); allab = []
report = []
for a in sorted(glob.glob(os.path.join(D, "*-a-808.wav"))):
    tag = os.path.basename(a)[:-len("-a-808.wav")]
    r, o = load(a), load(os.path.join(D, f"{tag}-b-ours.wav"))
    # trim ours' 10 ms lead-in and both tails of near-silence after the hit (> 60 dB down)
    def trim(x):
        env = np.abs(x); thr = env.max() * 1e-3; idx = np.nonzero(env > thr)[0]
        return x[idx[0]: idx[-1] + int(0.05 * SR)]
    r, o = trim(r), trim(o)
    lr, lo = loud(r), loud(o)
    o = o * 10 ** ((lr - lo) / 20)                       # ours matched to the 808's loudness
    k = 10 ** (-1 / 20) / max(np.abs(r).max(), np.abs(o).max())
    r, o = r * k, o * k                                   # scale the pair together to -1 dBFS peak
    ab = np.concatenate([r, gap, o, gap, r, gap, o, gap * 2])
    wavfile.write(os.path.join(D, f"{tag}-c-AB.wav"), SR, np.round(ab * 32767).astype("<i2"))
    allab.append(ab)
    report.append(f"{tag}: ours was {lo - lr:+.1f} dB vs the 808 (K-weighted); matched")
wavfile.write(os.path.join(D, "00-all-AB.wav"), SR, np.round(np.concatenate(allab) * 32767).astype("<i2"))
print("\n".join(report))
