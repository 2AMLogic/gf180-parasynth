"""Whole-chip register-image tests for the top-level reference model."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "audition"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pytest
import synth_top_model as stm
import voice_fx as vf
import drums_fx as dx
import patches
from dsp import SR

FS = 32768.0                     # Q1.15 full scale
BPM = 124.0                      # DR 0008's combined render, same tempo
RIFF = [(0, 26), (3, 26), (6, 33), (8, 26), (11, 24), (12, 24), (14, 31)]


@pytest.mark.parametrize("address,image_key", [
    (stm.A_AMP + 3, "amp"),
    (stm.A_FILT + 3, "fenv"),
])
def test_voice_release_rate_keeps_the_exponent_field(address, image_key):
    model = stm.SynthTopModel()
    rate = 0x0EA1D6

    assert model._write_voice(0, address, rate) == "image"

    assert model.img[image_key][3] == rate


# =============================================================================
# the master mix: bass and drums at once (contract 12, DR 0008)
# =============================================================================
def _growl_bass_regs() -> dict:
    """The audition's growl-bass patch -- the loudest of the eight and the one
    DR 0008's combined render uses -- as a SEC = 0 register image."""
    _, seq, _ = next(p for p in patches.MONO if p[0].endswith("growl-bass"))
    return vf.VoiceFx.patch_regs(**dict(seq[0][3]))


def _bass_and_drums_writes(regs: dict, *, dvol: float, bvol: float, bars: int = 1,
                           start_s: float = 0.02, riff=None, gate_steps: float = 1.8,
                           seconds: float = None):
    """One bar of the 808 pattern under a bass riff, as the write stream the
    chip's register port would see."""
    step = 60.0 / BPM / 4.0
    w = []
    for k, s in enumerate(regs["waves"]):
        w.append((0, 0, stm.SEC_VOICE, stm.A_WAVE + k, vf.WAVE_CODE[s]))
    for k, g in enumerate(regs["weights"]):
        w.append((0, 0, stm.SEC_VOICE, stm.A_W + k, g))
    for base, key in ((stm.A_AMP, "amp"), (stm.A_FILT, "fenv")):
        for j, v in enumerate(regs[key]):
            w.append((0, 0, stm.SEC_VOICE, base + j, v))
    for addr, key in ((stm.A_CUT_LO, "cut_lo"), (stm.A_CUT_HI, "cut_hi"),
                      (stm.A_K, "k"), (stm.A_GAIN, "gain"), (stm.A_OGAIN, "ogain"),
                      (stm.A_GLIDE, "glide"), (stm.A_VOL, "vol")):
        w.append((0, 0, stm.SEC_VOICE, addr, regs[key]))
    w.append((0, 0, stm.SEC_VOICE, stm.A_DVOL, dx.accent_reg(dvol)))
    w.append((0, 0, stm.SEC_VOICE, stm.A_BVOL, dx.accent_reg(bvol)))

    riff = RIFF if riff is None else riff
    first = True
    for bar in range(bars):
        for st, note in riff:
            f = int(round((start_s + (bar * 16 + st) * step) * SR))
            for k, v in enumerate(vf.VoiceFx.note_incs(note, regs["detune"])):
                w.append((f, 1 if first else 0, stm.SEC_VOICE, stm.A_INC + k, v))
            w.append((f, 0, stm.SEC_VOICE, stm.A_TRACK,
                      vf.VoiceFx.note_track(note, regs["track"])))
            w.append((f, 0, stm.SEC_VOICE, stm.A_GATE_ON, 0))
            w.append((f + int(round(step * gate_steps * SR)), 0, stm.SEC_VOICE,
                      stm.A_GATE_OFF, 0))
            first = False

    hits = dx.pattern_hits(dx.PATTERN_808, bpm=BPM, bars=bars, start_s=start_s)
    for f, a, d in dx.hit_writes(hits, dx.kit_808()):
        w.append((int(f), 0, stm.SEC_DRUM, a, d))

    total = (start_s + 60.0 / BPM * 4 * bars + 0.5) if seconds is None else seconds
    n = int(round(total * SR))
    return [x for x in w if x[0] < n], n


def _longest_rail_run(sample: np.ndarray) -> int:
    """The longest run of consecutive samples sitting on the rail. One sample
    at 32767 is a rounding event; eighty-five in a row is a flat top, and the
    two are not the same defect even at the same clamped fraction."""
    at = np.abs(np.asarray(sample, dtype=np.int64)) >= 32767
    if not at.any():
        return 0
    idx = np.flatnonzero(np.diff(np.concatenate(([0], at.view(np.int8), [0]))))
    return int((idx[1::2] - idx[::2]).max())


def _mix_report(gain: float, riff=None, gate_steps: float = 1.8, seconds: float = None) -> dict:
    """One bass-and-drums render through the whole chip, reported node by
    node. `gain` is both drum buses.

    The apparatus is asserted before the result is: a render where the bass
    never sounded, or the kit never fired, passes any headroom bound
    trivially, and that is the way this test would silently stop measuring
    anything (docs/verification-rules.md; CLAUDE.md on preconditions)."""
    regs = _growl_bass_regs()
    writes, n = _bass_and_drums_writes(regs, dvol=gain, bvol=gain, riff=riff,
                                       gate_steps=gate_steps, seconds=seconds)
    r = stm.SynthTopModel().run(writes, n)
    assert not r["route"].any(), "REFUSED: the drum filter is routed; this is the direct-sum case"
    voice = (r["v"] * r["vol"]) >> 15                 # the voice's term of contract 12's sum
    drums = r["dacc"] >> 15                           # the two drum buses', already summed
    acc = r["v"] * r["vol"] + r["dacc"]               # ONE exact sum, before the one shift
    rep = dict(sample=r["sample"], v=r["v"], voice=voice, drums=drums, pre=acc >> 15,
               rail=float(np.mean(np.abs(r["sample"]) >= 32767)),
               run=_longest_rail_run(r["sample"]))
    assert np.abs(voice).max() / FS > 0.5, \
        f"REFUSED: the bass did not play (voice term peaked at {np.abs(voice).max()/FS:.3f} x FS)"
    assert np.abs(drums).max() / FS > 0.1, \
        f"REFUSED: the kit did not play (drum term peaked at {np.abs(drums).max()/FS:.3f} x FS)"
    # the master mix is the formula and nothing else: one sum, one shift, one clamp
    assert np.array_equal(rep["sample"], np.clip(rep["pre"], -32768, 32767)), \
        "the output is not sat16 of the shifted exact sum (contract 12)"
    return rep


# The DR 0008 render's own riff (`model/drums_fx_render.py:combined`), an
# octave above the patch's audition riff, kept so the case that IS documented
# can be reproduced beside the one that is not.
RIFF_05_COMBINED = [(0, 33), (3, 33), (6, 40), (8, 33), (11, 31), (12, 33), (14, 35)]


def test_bass_and_drums_together_meet_the_master_clamp_at_the_reference_gains():
    """[measured-here: combined bass + 808 groove through synth_top_model's master mix]
    **The case the master clamp exists for, and the one least tested.** Every
    other headroom test in the repository drives ONE bus: the voice's eight
    audition patches with the drums silent
    (`test_moog_acceptance.test_nothing_clips_at_the_reference_gain_structure`),
    or the kit's loudest legal hit with the voice silent
    (`test_drums_fx.test_bank_headroom_zero_and_nineteen_bits_hold_the_kits_loudest_hit`),
    or synthetic full-scale buses into `output_fx`
    (`test_drums_fx.test_output_stage_is_the_voice_formula_when_the_drums_are_silent`).
    None of them plays a real bass patch and a real drum groove at once, which
    is the only case where `sat16` at contract 12 can fire on musical material.

    Measured here, one bar of `PATTERN_808` at 124 BPM under the growl-bass
    patch playing its own audition riff (`patches.MONO` 07, MIDI 24..33), both
    drum buses at the reference 0.45 of DR 0005:

        voice term  v*vol >> 15        0.863 x FS      has headroom alone
        drum term   dacc  >> 15        0.554 x FS      has headroom alone
        their sum   acc   >> 15        1.140 x FS      does NOT
        output      sat16(acc >> 15)   185 of 117 863 samples at the rail
                                       = 0.157 %, longest flat top 85 samples

    So the answer to "is there headroom when drums and bass play together" is
    **no, not at the reference gains** -- the clamp is load-bearing, not a
    formality. This test does not call that a defect (DR 0005 put exactly one
    hard rail at the output on purpose, and `drums_fx_render.py` already says
    the reference gains clip a few samples): it bounds it, so that a change to
    the gain structure has to move a stated number rather than an impression.

    Tolerances: the clamped fraction in 0.05 %..0.50 % -- a LOWER bound too,
    because a render that stopped clipping without anyone deciding to change
    the gains means the bass or the kit stopped playing, and that is the
    failure this test is most likely to die of -- and the longest flat top
    under 200 samples (4.2 ms at 48 kHz).
    """
    r = _mix_report(0.45)
    voice, drums, pre = (np.abs(r[k]).max() / FS for k in ("voice", "drums", "pre"))
    assert voice < 1.0, f"the voice term alone reaches the rail: {voice:.3f} x FS"
    assert drums < 1.0, f"the drum term alone reaches the rail: {drums:.3f} x FS"
    assert pre > max(voice, drums), (voice, drums, pre)      # the sum is the interaction
    assert 1.05 < pre < 1.35, f"the pre-clamp sum peaks at {pre:.3f} x FS"
    assert 0.0005 < r["rail"] < 0.005, f"{100*r['rail']:.4f} % of samples at the rail"
    assert r["run"] < 200, f"longest flat top {r['run']} samples"
    # the VCA's word, carried into the mix UNSATURATED (synth_top_model's
    # point 3): the 19-bit Q4.15 word must hold it, or the clamp above is not
    # the only nonlinearity on the path
    assert np.abs(r["v"]).max() < (1 << 18), np.abs(r["v"]).max()


def test_backing_the_drum_buses_off_buys_the_combined_mix_its_headroom():
    """[measured-here: the same render at lower drum-bus gains]
    **The remedy, and its price, as two numbers rather than an opinion.** The
    clamping above is a GAIN choice, not a limit of the block, and the way to
    show that is to move the gain and watch it go away. At 0.20 on both drum
    buses the same bar does not touch the rail at all -- the drum term falls
    to 0.246 x FS, the sum to 0.973, and 0 of 117 863 samples clamp.

    The second case is the one `model/drums_fx_render.py:combined` documents
    ("the reference 0.45 clips a few samples ... 0.30 does not"), reproduced
    here as an assertion: with the DR 0008 render's own riff -- the same patch
    an octave higher -- 0.30 clips nothing. **Both statements are true and
    they are about different music**, which is the finding: the same patch,
    the same kit and the same gains rail or do not rail depending on the notes
    played, so "0.30 is safe" is a property of that render and not of the
    instrument. The audition riff still rails 44 samples (0.037 %) at 0.30.
    """
    quiet = _mix_report(0.20)
    assert quiet["rail"] == 0.0, f"{100*quiet['rail']:.4f} % at the rail with the buses at 0.20"
    assert np.abs(quiet["pre"]).max() / FS < 1.0, np.abs(quiet["pre"]).max() / FS

    documented = _mix_report(0.30, riff=RIFF_05_COMBINED, gate_steps=2.4)
    assert documented["rail"] == 0.0, \
        (f"drums_fx_render.combined says 0.30 does not clip; this render clamps "
         f"{100*documented['rail']:.4f} % of samples")


def test_control_the_combined_headroom_measurement_can_fail(monkeypatch):
    """[control: docs/verification-rules.md rules 1 and 2] **A headroom test
    that cannot go red is a decoration.** Two injections, on a 0.4 s window so
    the control costs a fraction of the measurement it guards:

    1. **Silent kit** (both drum buses at gain 0). Nothing rails, every bound
       above is satisfied, and the answer is worthless -- so the apparatus
       must REFUSE rather than report. This is the way the measurement dies in
       practice: a patch or kit change makes one side inaudible and the
       headroom test goes on passing.
    2. **No master clamp** (`sat16` replaced by identity, with the buses hot
       enough to overflow). The sum leaves the 16-bit word and the test must
       see it. Without this, the test would be measuring `np.clip` rather than
       contract 12's output stage.
    """
    with pytest.raises(AssertionError, match="the kit did not play"):
        _mix_report(0.0, seconds=0.4)

    monkeypatch.setattr(stm, "sat16", lambda v: v)
    with pytest.raises(AssertionError, match="contract 12"):
        _mix_report(2.0, seconds=0.4)
