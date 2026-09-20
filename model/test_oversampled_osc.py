import numpy as np
from pathlib import Path
import re

import audio_measure as am
import dsp
import oversampled_osc as os2
import voice_fx as vf


def test_true_2x_decimator_preserves_high_note_harmonics_and_reduces_foldback():
    note = 84
    f0 = vf.note_hz(note)
    n = int(0.7 * dsp.SR)
    inc = vf.phase_inc(f0)
    raw = vf.OscFx("saw", smooth=False).render(n, inc) / 32768.0
    fixed = os2.render_saw(n, inc) / 32768.0
    before = am.inharmonic_fraction_db(raw, f0, dsp.SR).require("raw")
    after = am.inharmonic_fraction_db(fixed, f0, dsp.SR).require("2x")
    assert before - after > 10.0, (before, after)
    f_before = am.harmonic_signature(raw, dsp.SR, f0=f0, kmax=12)
    f_after = am.harmonic_signature(fixed, dsp.SR, f0=f0, kmax=12)
    assert max(abs(f_after[f"h{k}"] - f_before[f"h{k}"]) for k in range(2, 13)) < 2.0


def test_voice_2x_filter_history_survives_play_chunk_boundaries():
    inc = vf.phase_inc(vf.note_hz(45))
    whole = vf.OscFx("saw", smooth=True)
    split = vf.OscFx("saw", smooth=True)
    h0 = np.zeros(30, dtype=np.int64)
    h1 = np.zeros(30, dtype=np.int64)
    expected, _, _ = vf._render_2x(whole, 500, inc, h0, 0)
    a, h1, phase = vf._render_2x(split, 250, inc, h1, 0)
    b, h1, _ = vf._render_2x(split, 250, inc, h1, phase)
    assert np.array_equal(expected, np.concatenate((a, b)))


def test_2x_decimator_headroom_is_safe_across_the_midi_range():
    """The FIR must not turn its own ringing into Q1.15 clipping distortion."""
    n = int(0.12 * dsp.SR)
    worst_peak = (0, None)
    full_scale_clips = 0
    for note in range(128):
        inc = vf.phase_inc(vf.note_hz(note))
        hi = vf.OscFx("saw", smooth=False).render(2 * n, inc // 2).astype(np.int64)

        def decimator_peak(gain_q15):
            scaled = (hi * gain_q15) >> 15
            joined = np.concatenate((np.zeros(30, dtype=np.int64), scaled))
            accum = np.convolve(joined, vf._DECIM2_TAPS, mode="full")
            raw = (accum[30:30 + 2 * n] >> 15)[1::2]
            return int(np.max(np.abs(raw))), int(np.count_nonzero(np.abs(raw) > 32767))

        peak, clips = decimator_peak(32767)
        full_scale_clips += clips
        peak_h, _ = decimator_peak(vf._OS2_SUBSTEP_GAIN_Q15)
        if peak_h > worst_peak[0]:
            worst_peak = (peak_h, note)

    # The unattenuated control is deliberately wrong; the chosen input gain
    # must leave every measured MIDI note below the saturating output rail.
    assert full_scale_clips > 0
    assert worst_peak[0] <= 32767, (worst_peak, full_scale_clips)


def test_rtl_coefficients_and_headroom_match_python_reference():
    rtl_dir = Path(__file__).parents[1] / "rtl-sketch"
    rtl = (rtl_dir / "decimate_2x_tm_sym.v").read_text()
    entries = {int(i): int(v) for i, v in re.findall(r"h\[(\d+)\]\s*=\s*(-?\d+)", rtl)}
    taps = [entries[i] for i in range(16)] + [entries[i] for i in range(14, -1, -1)]
    assert taps == vf._DECIM2_TAPS.tolist()
    osc_rtl = (rtl_dir / "polyblep_saw_pair.v").read_text()
    gains = [int(v) for v in re.findall(r"SUBSTEP_GAIN_Q15\s*=\s*16'sd(\d+)", osc_rtl)]
    assert gains == [32767, vf._OS2_SUBSTEP_GAIN_Q15]


def test_zero_increment_is_rejected_by_standalone_api():
    import pytest
    with pytest.raises(ValueError, match="inc must be positive"):
        os2.render_saw(1, 0)
