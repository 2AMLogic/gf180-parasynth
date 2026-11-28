#!/usr/bin/env python3
"""Loudness-matched A/B for listening. Reads the raw -a-808 / -b-ours WAVs written by
ab_808.py and writes NN-SND-c-AB.wav as 808, ours, 808, ours (0.25 s gaps), both matched
to the same K-weighted loudness (ITU-R BS.1770 K filter), measured only over the part of
the hit within 20 dB of its loudest 50 ms block (so tails/silence don't skew it).
Both sides are then scaled together so the loudest peak is -1 dBFS: no clipping, and the
two stay matched to each other. For listening only; raw levels are in -a/-b files.

    python3 tools/ab_808_loud.py <pack-dir>

Exits 0 with the pack written, or 2 (REFUSED, `tools/exit_contract.py`) with nothing
written when a side cannot be read or the pack holds no *-a-808.wav pair (#1861)."""
import glob, os, pathlib, sys
import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
import perceptual_gate as pg  # noqa: E402
import refusal  # noqa: E402

SR = 48000


class Refused(refusal.Refused, ValueError):
    """A listening pack this script cannot build: a side it cannot read, or
    no pair at all (#1861). Also a `ValueError`, which the width refusal it
    carries was before (#1117)."""


def load(p):
    try:
        sr, x = wavfile.read(p)
    except (OSError, ValueError) as exc:
        raise Refused(f"{p}: unreadable WAV ({exc})") from exc
    if x.dtype != np.int16:     # #1117: /32768 is only the 16-bit divisor
        raise Refused(f"{p}: {x.dtype} samples, expected 16-bit PCM (int16)")
    x = x.astype(np.float64) / 32768.0
    return resample_poly(x, SR, sr) if sr != SR else x


def main(argv=None) -> int:
    D = sys.argv[1] if argv is None else argv[0]
    gap = np.zeros(int(0.25 * SR))
    pairs, report = [], []
    try:
        sides = sorted(glob.glob(os.path.join(D, "*-a-808.wav")))
        if not sides:
            raise Refused(f"no *-a-808.wav files in {D}")
        for a in sides:
            tag = os.path.basename(a)[:-len("-a-808.wav")]
            r, o = load(a), load(os.path.join(D, f"{tag}-b-ours.wav"))
            # trim ours' 10 ms lead-in and both tails of near-silence after the hit (> 60 dB down)
            def trim(x):
                env = np.abs(x); thr = env.max() * 1e-3; idx = np.nonzero(env > thr)[0]
                return x[idx[0]: idx[-1] + int(0.05 * SR)]
            r, o = trim(r), trim(o)
            lr, lo = pg.loudness_db(r), pg.loudness_db(o)
            o = o * 10 ** ((lr - lo) / 20)                       # ours matched to the 808's loudness
            k = 10 ** (-1 / 20) / max(np.abs(r).max(), np.abs(o).max())
            r, o = r * k, o * k                                   # scale the pair together to -1 dBFS peak
            pairs.append((tag, np.concatenate([r, gap, o, gap, r, gap, o, gap * 2])))
            report.append(f"{tag}: ours was {lo - lr:+.1f} dB vs the 808 (K-weighted); matched")
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return exit_contract.REFUSED
    allab = []
    for tag, ab in pairs:
        wavfile.write(os.path.join(D, f"{tag}-c-AB.wav"), SR, np.round(ab * 32767).astype("<i2"))
        allab.append(ab)
    wavfile.write(os.path.join(D, "00-all-AB.wav"), SR, np.round(np.concatenate(allab) * 32767).astype("<i2"))
    print("\n".join(report))
    return exit_contract.PASS


if __name__ == "__main__":
    raise SystemExit(main())
