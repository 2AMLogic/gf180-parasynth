"""The scored I2S listening WAV must preserve the decoded phrase, gap included."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from scipy.io import wavfile

import mono_m5a_score as score


class CapturedGapTest(unittest.TestCase):
    def test_scored_wav_preserves_samples_between_segments(self):
        manifest = json.loads(score.MANIFEST.read_text())
        board = json.loads((score.ROOT / "docs/scorecard/results/M5A.json").read_text())
        raw = score.MANIFEST.parent / "saw-cutoff-20khz-i2s.wav"
        self.assertEqual(hashlib.sha256(raw.read_bytes()).hexdigest(),
                         board["diagnostics"]["decoded_i2s_sha256"])
        sr, clean = wavfile.read(raw)
        out_sr, published = wavfile.read(score.ROOT / board["audio"])
        phrase_samples = manifest["audio"]["samples"]
        self.assertEqual(sr, out_sr)
        np.testing.assert_array_equal(published, clean[:phrase_samples])

        first, second = manifest["timeline"]["segments"]
        start = first["offset_samples"] + first["samples"]
        stop = second["offset_samples"]
        self.assertEqual(stop - start, round(sr * manifest["timeline"]["segment_silence_s"]))
        self.assertGreaterEqual(stop - start, 48)
        self.assertFalse(np.any(clean[start:stop]))
        marked = clean.copy()
        marked[start:start + 48] = 16384

        with tempfile.TemporaryDirectory() as directory:
            candidate = Path(directory) / "candidate.wav"
            output = Path(directory) / "scored.wav"
            wavfile.write(candidate, sr, marked)
            result = score.measure(case_id="M5A", candidate_wav=candidate, output_path=output)
            self.assertEqual(result["candidate_i2s_sha256"],
                             hashlib.sha256(candidate.read_bytes()).hexdigest())
            scored_sr, scored = wavfile.read(output)
            self.assertEqual(scored_sr, sr)
            np.testing.assert_array_equal(scored, marked[:phrase_samples])


if __name__ == "__main__":
    unittest.main()
