"""Controls for tools/measure_pulse2x_headroom.py: the rail detector must fire
on the documented full-scale saw failure and stay quiet where the saw sweep
that chose 0.85 found no clipping (docs/oversampled-rtl-plan.md)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_pulse2x_headroom as h  # noqa: E402


def test_full_scale_saw_clips_at_midi_84():          # the documented 1,080-sample failure
    assert h.decimator_run("saw", 84, 32767)["clipped"] > 0


def test_saw_at_085_never_reaches_the_rail():        # the documented MIDI 0-127 sweep
    for note in (0, 2, 24, 60, 84, 96, 108, 127):
        r = h.decimator_run("saw", note, h.BASE_Q15)
        assert r["clipped"] == 0 and r["peak"] <= 32767, (note, r)


def test_gain_scales_level_exactly():
    a = h.decimator_run("pulse479", 60, h.q15(0.85))
    b = h.decimator_run("pulse479", 60, h.q15(0.80))
    assert abs((b["rms_dbfs"] - a["rms_dbfs"]) - 20 * __import__("math").log10(0.80 / 0.85)) < 0.02


def test_rect_scope_leaves_saw_untouched():
    import numpy as np
    import voice_fx as vf
    def render():
        v = vf.VoiceFx(oversample_2x=True, oversample_pulse_2x=True)
        return v.note(84, 0.2, waves=("saw", "saw", "saw"), mix=(1.0, 0.0, 0.0))
    base = render()
    with h.candidate(h.q15(0.80), "rect"):
        cand = render()
    assert np.array_equal(base, cand)
    with h.candidate(h.q15(0.80), "all"):
        moved = render()
    assert not np.array_equal(base, moved)
