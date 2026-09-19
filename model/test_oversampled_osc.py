import numpy as np

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
