"""Issue #1552 reproduction. Only the component's sample sources are replaced."""
import wave

import numpy as np
import pytest

import manifest
import measure_mono_case as component
import run_case


def read_words(path):
    with wave.open(str(path), "rb") as wav:
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")


def test_case_writer_domain(tmp_path):
    grid = np.arange(-32768, 32768)
    fractions = np.array([1.5, 2.5, -1.5, -2.5])
    for write, fractional_result in [
        (run_case.write_wav16, [1, 2, -1, -2]),
        (manifest._write_wav16, [2, 2, -2, -2]),
    ]:
        path = tmp_path / "samples.wav"
        write(path, grid / 32768.0, 48000)
        np.testing.assert_array_equal(read_words(path), grid)
        write(path, fractions / 32768.0, 48000)
        np.testing.assert_array_equal(read_words(path), fractional_result)


@pytest.mark.parametrize("peak,expected", [(0.5, "VALID"), (1.01, "REFUSED")])
def test_component_retention(tmp_path, monkeypatch, peak, expected):
    n = int(component.SECONDS * component.SR)
    t = np.arange(n) / component.SR
    f = component.vf.note_hz(component.NOTE)
    tone = sum(np.sin(2 * np.pi * k * f * t) / k for k in range(1, 13))
    tone /= np.max(np.abs(tone))
    reference = peak * tone
    dut = np.rint(16000 * tone).astype(np.int16)
    monkeypatch.setattr(component, "_apparatus", lambda: {"reference": reference})
    monkeypatch.setattr(component.vf.OscFx, "render", lambda *args: dut)
    monkeypatch.setattr(component.os2, "render_saw", lambda *args: dut)
    code, report = component.run(tmp_path)
    over = np.count_nonzero(np.abs(reference) > 1.0)
    assert (over > 0) == (peak > 1.0)
    assert report["status"] == expected, (over, code, report["status"])
    if expected == "REFUSED":
        assert code == component.REFUSED
        assert not list(tmp_path.glob("*.wav"))
    else:
        assert code == 0
        assert (tmp_path / "reference-surge.wav").is_file()
