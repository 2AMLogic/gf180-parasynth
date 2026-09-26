"""The release's player-facing domain: accepted boundaries, rejected
transitions, and the CLI path that enforces them (fpga/release/RELEASE.md).

Every rejection test names the RULE it expects, so a validator that rejects
for the wrong reason (or a stub that rejects nothing) fails here."""
import contextlib
import io
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for _p in (HERE, os.path.join(ROOT, "fpga"), os.path.join(ROOT, "model"),
           os.path.join(ROOT, "audition"), os.path.join(ROOT, "tools"),
           os.path.join(ROOT, "rtl-sketch")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import qualified_domain as qd            # noqa: E402
import voice_fx as vf                    # noqa: E402
from dsp import note_hz, phase_inc       # noqa: E402

A_INC, A_GLIDE, A_RESET = 0x00, 0x0C, 0x23
A_WAVE, A_W, A_MROUTE, A_MWHEEL, A_MPD = 0x04, 0x08, 0x1F, 0x25, 0x26
DEFAULT = vf.VoiceFx.patch_regs()


def rule_of(fn, *a, **k):
    with pytest.raises(qd.Rejected) as exc:
        fn(*a, **k)
    return exc.value.rule


def image(waves=("saw", "saw", "square"), weights=(14246, 11397, 7123), glide=2692):
    """A known audible image, as the player path writes it."""
    w = [(0, 0, A_WAVE + k, vf.WAVE_CODE[s]) for k, s in enumerate(waves)]
    w += [(0, 0, A_W + k, g) for k, g in enumerate(weights)]
    return w + [(0, 0, A_GLIDE, glide)]


def incs(values, jump):
    return [(1 if jump else 0, 0, A_INC + k, v) for k, v in enumerate(values)]


# ---- the bounds ----------------------------------------------------------------
def test_bounds_are_the_midi_pitch_span_and_sit_below_nyquist():
    assert qd.INC_LO == phase_inc(note_hz(0)) == 2858
    assert qd.INC_HI == phase_inc(note_hz(127)) == 4384395
    assert qd.INC_HI < qd.NYQUIST_INC == 1 << 23


def test_note_numbers_alone_do_not_bound_the_increment():
    """plan080: MIDI 127 up an octave is above Nyquist, and note_incs lets it
    through (it clamps only at 2^24 - 1)."""
    inc = vf.VoiceFx.note_incs(127, (12.0,))[0]
    assert inc > qd.NYQUIST_INC
    assert rule_of(qd.check_inc, inc) == "INC_RANGE"


def test_keyhost_unclamped_increment_is_refused_not_masked():
    """KeyHost.writes does not clamp: two octaves above MIDI 127 overflows the
    24-bit register, which would keep the low bits -- a different pitch."""
    regs = dict(DEFAULT, detune=(24.0, 24.0, 24.0))
    out = vf.KeyHost().writes([(0, "on", 127)], regs)
    v = [a[1] for f, op, *a in out if op == "INC"][0]
    assert v > (1 << 24) - 1
    assert rule_of(qd.check_inc, v) == "INC_WIDTH"


@pytest.mark.parametrize("v", [qd.INC_LO, qd.INC_HI])
def test_boundary_increments_accepted_unchanged(v):
    assert qd.check_inc(v) == v


@pytest.mark.parametrize("v", [0, qd.INC_LO - 1, qd.INC_HI + 1, qd.NYQUIST_INC, (1 << 24) - 1])
def test_outside_increments_rejected(v):
    assert rule_of(qd.check_inc, v) == "INC_RANGE"


# ---- notes after transposition ---------------------------------------------------
def test_default_patch_playable_range_is_12_to_126():
    """osc 3 at -12 semitones floors it at 12; osc 2 at +0.07 caps it at 126."""
    assert qd.playable_notes(DEFAULT["detune"]) == (12, 126)
    for n in (12, 126):
        qd.check_note(n, DEFAULT)
    assert rule_of(qd.check_note, 11, DEFAULT) == "INC_RANGE"
    assert rule_of(qd.check_note, 127, DEFAULT) == "INC_RANGE"


def test_rejected_note_is_reported_with_the_patch_range_never_repitched():
    with pytest.raises(qd.Rejected) as exc:
        qd.check_note(127, DEFAULT)
    assert "12..126" in str(exc.value) and "+0.07" in str(exc.value)


def test_transposed_up_an_octave_patch_is_capped_at_115():
    regs = dict(DEFAULT, detune=(0.0, 0.0, 12.0))
    assert qd.playable_notes(regs["detune"]) == (0, 115)
    assert rule_of(qd.check_note, 116, regs) == "INC_RANGE"


# ---- glide transitions -------------------------------------------------------------
def test_full_range_glides_accepted_both_ways():
    s = image() + incs([qd.INC_LO] * 3, True) + incs([qd.INC_HI] * 3, False) \
        + incs([qd.INC_LO] * 3, False)
    assert qd.check_stream(s)["glide_transitions"] == 6


def test_retarget_mid_glide_stays_accepted():
    s = image() + incs([qd.INC_HI] * 3, True) + incs([qd.INC_LO] * 3, False) \
        + incs([qd.INC_HI] * 3, False)
    qd.check_stream(s)


def test_glide_to_above_the_range_rejected():
    s = image() + incs([qd.INC_HI] * 3, True) + [(0, 0, A_INC, qd.INC_HI + 1)]
    assert rule_of(qd.check_stream, s) == "INC_RANGE"


def test_issue_247_reproduction_is_rejected():
    """#247's stream: jumps to 0xC00000.. then a glide to 0xFF0000.."""
    s = image() + incs([0xC00000, 0xC80000, 0xD00000], True) \
        + incs([0xFF0000, 0xF80000, 0xF00000], False)
    assert rule_of(qd.check_stream, s) == "INC_RANGE"


def test_247_domain_predicate():
    assert qd.in_247_domain(1 << 23, 100) and qd.in_247_domain(100, 1 << 23)
    assert not qd.in_247_domain(qd.INC_HI, qd.INC_LO)


def test_every_admitted_transition_is_outside_the_247_domain():
    for src in (qd.INC_LO, qd.INC_HI):
        for dst in (qd.INC_LO, qd.INC_HI):
            assert not qd.in_247_domain(src, dst)


def test_glide_from_reset_rejected():
    s = image() + incs([qd.INC_HI] * 3, False)
    assert rule_of(qd.check_stream, s) == "GLIDE_SOURCE"


def test_glide_from_unknown_state_rejected_but_jump_accepted():
    known = image()
    assert rule_of(qd.check_stream, known + incs([qd.INC_HI] * 3, False),
                   initial="unknown", mod_initial="reset") == "GLIDE_SOURCE"
    qd.check_stream(known + incs([qd.INC_HI] * 3, True), initial="unknown", mod_initial="reset")


def test_glide_register_zero_makes_a_plain_write_a_jump():
    s = image(glide=0) + incs([qd.INC_HI] * 3, False)
    assert qd.check_stream(s)["glide_transitions"] == 0


def test_reset_write_forgets_the_source():
    s = image() + incs([qd.INC_HI] * 3, True) + [(0, 0, A_RESET, 0)] + image() \
        + incs([qd.INC_LO] * 3, False)
    assert rule_of(qd.check_stream, s) == "GLIDE_SOURCE"


# ---- modulation, separately ------------------------------------------------------------
def test_modulation_excursion_out_of_range_rejected_as_modulation():
    s = image() + incs([qd.INC_HI // 2] * 3, True) + [
        (0, 0, A_MROUTE, vf.MR_OSC), (0, 0, A_MPD, 3 << vf.OCT_Q), (0, 0, A_MWHEEL, 32768)]
    with pytest.raises(qd.Rejected) as exc:
        qd.check_stream(s)
    assert exc.value.rule == "MOD_EXCURSION"
    assert "not the #247 glide slew" in str(exc.value)


def test_modulation_inside_the_range_accepted():
    mid = phase_inc(note_hz(60))
    s = image() + incs([mid] * 3, True) + [
        (0, 0, A_MROUTE, vf.MR_OSC), (0, 0, A_MPD, 1 << vf.OCT_Q), (0, 0, A_MWHEEL, 32768)]
    qd.check_stream(s)
    assert qd.mod_octaves(vf.MR_OSC, 32768, 1 << vf.OCT_Q) == 1.0


def test_modulation_route_without_wheel_does_not_move_pitch():
    assert qd.mod_octaves(vf.MR_OSC | vf.MR_OSC3, 0, 65535) == 0.0
    s = image() + incs([qd.INC_HI] * 3, True) + [(0, 0, A_MROUTE, 7), (0, 0, A_MPD, 65535)]
    qd.check_stream(s)


def test_unknown_modulation_state_refuses_programming():
    s = image() + incs([qd.INC_HI] * 3, True)
    assert rule_of(qd.check_stream, s, initial="unknown") == "MOD_EXCURSION"


# ---- waveforms, calibration, pulse2x ------------------------------------------------------
def test_unsupported_waveform_set_rejected():
    assert rule_of(qd.check_stream, image(waves=("tri", "saw", "square")) + incs([qd.INC_HI] * 3, True)) \
        == "WAVES"
    assert rule_of(qd.check_patch, vf.VoiceFx.patch_regs(waves=("sine", "saw", "saw"))) == "WAVES"


def test_silent_oscillators_do_not_count_toward_the_set():
    s = image(waves=("pulse29", "saw", "saw"), weights=(32768, 0, 0)) + incs([qd.INC_HI] * 3, True)
    qd.check_stream(s)


def test_unknown_image_refuses_a_note():
    assert rule_of(qd.check_stream, incs([qd.INC_HI] * 3, True), initial="unknown",
                   mod_initial="reset") == "WAVES"


def test_clean_calibration_with_resonance_rejected_at_zero_accepted():
    cal = "surge-type2-clean-v1"
    qd.check_patch(vf.VoiceFx.patch_regs(q=0.0, drive=1.0, filter_calibration=cal))
    assert rule_of(qd.check_patch, vf.VoiceFx.patch_regs(q=0.5, drive=1.0, filter_calibration=cal)) \
        == "CALIBRATION_RESONANCE"


def test_pulse2x_rejected():
    assert rule_of(qd.check_patch, DEFAULT, pulse2x=True) == "PULSE2X"


# ---- the shipped CLI path ------------------------------------------------------------------
def cli(*argv):
    import uart_host as uh
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = uh.main(["--dry-run", *argv])
    return rc, out.getvalue(), err.getvalue()


@pytest.mark.parametrize("argv", [
    ("run", "--note", "12"), ("run", "--note", "126"), ("run", "--note", "45", "--fixture", "none"),
    ("run", "--preset", "m5a-saw", "--note", "0"), ("run", "--preset", "m5a-pulse", "--note", "127"),
    ("play", "--fixture", "m5a"), ("run", "--fixture", "demo"), ("run", "--fixture", "bar808-full"),
    ("load",)])
def test_cli_supported_commands_pass_the_validator(argv):
    rc, out, err = cli(*argv)
    assert rc == 0, err
    assert "release domain OK" in out


@pytest.mark.parametrize("argv,rule", [
    (("run", "--note", "127"), "INC_RANGE"), (("run", "--note", "11"), "INC_RANGE"),
    (("note-on", "--note", "60"), "WAVES")])
def test_cli_refuses_outside_the_domain(argv, rule):
    rc, out, err = cli(*argv)
    assert rc == 2 and f"[{rule}]" in err and "REFUSED" in err


def test_cli_refuses_preset_with_a_fixture():
    rc, _, err = cli("run", "--preset", "m5a-saw", "--fixture", "demo")
    assert rc == 2 and "loads its own patch" in err


def test_cli_engineering_interface_stays_available_and_says_so():
    rc, out, err = cli("--engineering", "run", "--note", "127")
    assert rc == 0 and "OUTSIDE the qualified" in err and "release domain OK" not in out


def test_cli_note_uses_the_presets_own_transposition():
    import uart_host as uh
    regs = uh.preset_regs("m5a-saw")
    assert tuple(regs["detune"]) == (0.0, 0.0, 0.0)
    w = uh.note_writes(72, True, preset_regs=regs)
    got = [d for f, s, a, d in w if a <= 2]
    assert got == [phase_inc(note_hz(72))] * 3


# ---- the held note is audible (the silent first-playback regression) ----------------------------
def _held_peak(mixer: bool):
    import synth_top_model as stm
    import uart_host as uh
    img = uh.voice_image_writes(None) + (uh.voice_mixer_writes(None) if mixer else [])
    ws = [(0, *w) for w in img] + [(10, *w) for w in uh.note_writes(45, True)]
    m = stm.SynthTopModel(oversample_2x=True, filter_2x=True).run(ws, 3000)
    return int(np.abs(m["sample"]).max())


def test_held_note_image_is_audible_and_the_legacy_image_was_silent():
    """Control first: the pre-fix image (no mixer weights) is exact silence,
    so this level check can see the defect; then the fixed image sounds."""
    assert _held_peak(mixer=False) == 0
    assert _held_peak(mixer=True) >= 1024


# ---- #247 as measured: the drum-filter route ------------------------------------------------
def test_voice_through_the_drum_filter_is_refused():
    s = image() + [(0, 0, 0x0F, 1)]
    assert rule_of(qd.check_stream, s) == "ROUTE_DRUMFILTER"
    qd.check_stream(image() + [(0, 0, 0x0F, 0)] + incs([qd.INC_HI] * 3, True))


def test_no_player_path_writes_the_route():
    import uart_host as uh
    for fx in ("demo", "bar808-full", "bar808"):
        st, ev, _ = uh.phrase_static_and_events(fx)
        assert all(a != 0x0F for f, s_, a, d in st) and all(w[3] != 0x0F for w in ev), fx
    assert all(a != 0x0F for f, s_, a, d in uh.voice_image_writes(None) + uh.voice_mixer_writes(None))
