"""Boundary controls for the 808 conga reference-lineage verdict.

Run with python -m pytest -q -s tools/probes/conga_descent_rounding.py.
The below-threshold case should fail until the conga wrapper stops deciding
a second time from the rounded correlation.
"""
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import measure_conga_body_spread as conga


@pytest.mark.parametrize("target", [0.94, 0.94998, 0.95002])
def test_conga_keeps_shared_descent_decision(tmp_path, target):
    # The four sign pairs occur equally often. Their two columns have zero
    # mean, unit variance and zero dot product. Shuffle away periodic peaks.
    pairs = np.tile([[1, 1], [1, -1], [-1, 1], [-1, -1]], (1024, 1))
    np.random.default_rng(282).shuffle(pairs)
    reference, noise = pairs.T
    candidate = target * reference + np.sqrt(1 - target**2) * noise
    reference = np.rint(reference * 20000).astype(np.int16)
    candidate = np.rint(candidate * 20000).astype(np.int16)
    expected = float(np.corrcoef(reference, candidate)[0, 1])
    assert abs(expected - target) < 0.000015

    refdir = tmp_path / "refs"
    ref = refdir / "mc8" / "MC50.WAV"
    recordings = tmp_path / "candidates"
    recordings.mkdir()
    # Fill the expected inventory so a missing take cannot explain a verdict.
    for voice in ("LC", "MC", "HC"):
        directory = refdir / f"{voice.lower()}8"
        directory.mkdir(parents=True)
        for tuning in conga.FISCHER_TUNING:
            wavfile.write(directory / f"{voice}{tuning}.WAV", 44100,
                          np.pad(reference, (512, 0)))
    wavfile.write(recordings / "mid conga.wav", 44100, np.pad(candidate, (512, 0)))

    shared, = conga.mh.descent_test(
        recordings, lambda p: {"refs": [ref]}, conga.read_wav, conga.prepare,
        read_candidate=conga.read_wav,
    )
    reported, = conga.descent_test(refdir, recordings)
    assert reported["at_resample_ratio"] == shared["at_resample_ratio"] == 1.0
    assert reported["best_correlation"] == shared["best_correlation"] == round(expected, 4)
    is_match = expected >= conga.mh.MATCH_THRESHOLD
    assert ("re-pressing" in shared["verdict"]) == is_match
    print(f"{expected:.8f} -> {reported['best_correlation']:.4f}: "
          f"{shared['verdict']} | {reported['verdict']}")
    assert ("re-pressing" in reported["verdict"]) == is_match
