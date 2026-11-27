"""Round-trip M5A component DUT evidence through the production WAV writer."""
import hashlib
import json
from pathlib import Path
import sys
import wave

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import measure_mono_case as m
import manifest


def read_pcm(path):
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, m.SR)
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")


@pytest.mark.parametrize("case", ["all_codes", "dut", "dut_2x"])
def test_component_wav_preserves_scored_pcm(case, tmp_path):
    n = int(m.SECONDS * m.SR)
    inc = m.vf.phase_inc(m.vf.note_hz(m.NOTE))
    if case == "all_codes":
        codes = np.arange(-32768, 32768, dtype=np.int32)
    elif case == "dut":
        codes = m.vf.OscFx(m.WAVE, smooth=True).render(n, inc)
    else:
        codes = m.os2.render_saw(n, inc)
    codes = np.asarray(codes)
    assert np.issubdtype(codes.dtype, np.integer)
    assert codes.min() >= -32768 and codes.max() <= 32767
    scored = codes.astype(np.float64) / 32768.0

    control = tmp_path / "manifest.wav"
    manifest._write_wav16(control, scored, m.SR)
    np.testing.assert_array_equal(read_pcm(control), codes)

    wav = tmp_path / "component.wav"
    m._write_wav(wav, scored)
    captured = read_pcm(wav)
    assert captured.shape == codes.shape
    delta = captured.astype(np.int32) - codes.astype(np.int32)
    if case != "all_codes":
        report = ROOT / "docs/scorecard/mono-m5a-component-2x/report.json"
        digest = hashlib.sha256(wav.read_bytes()).hexdigest()
        print(f"{case} matches archived hash: {digest == json.loads(report.read_text())['audio'][case + '_sha256']}")
    assert np.array_equal(captured, codes), (
        f"{case}: {np.count_nonzero(delta)} of {len(codes)} words differ, "
        f"maximum {np.abs(delta).max()} LSB")
