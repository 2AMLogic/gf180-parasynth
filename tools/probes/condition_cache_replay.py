"""Reproduce a stale 808 conditioning report with a known change in onset.

Run from any directory with Python, numpy and scipy. Uses a temporary synthetic
CH recording. The cache is produced with the study's own feature and NPZ code.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile

import numpy as np
from scipy.io import wavfile

MODEL = Path(__file__).resolve().parents[2] / "model"
sys.path.insert(0, str(MODEL))
import condition_boundary as study


def report(refs, cache, output):
    subprocess.run([sys.executable, str(MODEL / "condition_boundary.py"),
                    "--refs", str(refs), "--cache", str(cache), "--json", str(output)],
                   check=True, stdout=subprocess.DEVNULL)
    return json.loads(output.read_text())


def main():
    rate = 48000
    t = np.arange(rate // 3) / rate
    # Cosine starts at full amplitude, so its known onset is sample zero.
    hit = np.rint(16000 * np.cos(2 * np.pi * 1500 * t) * np.exp(-8 * t)).astype(np.int16)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        refs = root / "refs"
        wav = refs / "ch8" / "CH.WAV"
        wav.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(refs)], check=True)
        wavfile.write(wav, rate, hit)
        subprocess.run(["git", "-C", str(refs), "add", "-f", str(wav)], check=True)
        subprocess.run(["git", "-C", str(refs), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.invalid", "commit", "-qm", "original hit"],
                       check=True)
        vectors = study._both_ways(*study.td.read_wav(wav))
        cache = root / "features.npz"
        study.save_cache({("CH", (), side): vectors for side in ("real", "ours")}, cache)
        before = report(refs, cache, root / "before.json")
        assert before["artefact"]["CH"]["lead_ms_real"] == 0
        wavfile.write(wav, rate, np.pad(hit, (rate // 50, 0)))
        after = report(refs, cache, root / "after.json")
        assert before["provenance"]["corpus_sha256"] != after["provenance"]["corpus_sha256"]
        fresh = study._both_ways(*study.td.read_wav(wav))["_prep"]["lead_ms"]
        assert fresh == 20
        reported = after["artefact"]["CH"]["lead_ms_real"]
        print(f"CH reference lead after inserting 960 samples at 48 kHz: "
              f"fresh {fresh:g} ms, cached report {reported:g} ms")
        if reported != fresh:
            raise SystemExit("MISMATCH: report carries the new corpus hash and the old lead")


if __name__ == "__main__":
    main()
