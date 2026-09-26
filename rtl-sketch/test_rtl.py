"""RTL-against-model tests. The ladder RTL must be bit-exact against
model/fixed.py, the modal RTL against model/modal_fixed.py, the whole voice
(voice_dp.v) against model/voice_fx.py and the drum section (drum_kit.v:
drum_dp.v + modal_dp.v) against model/drums_fx.py, and each bench must be
shown to fail on an injected defect -- and, for the voice, on an all-X stub.

Needs iverilog and vvp on PATH, or OSS_CAD_SUITE=/path/to/oss-cad-suite.
Without them the simulation tests SKIP -- a skip is not a pass; CI must treat
it as missing evidence.

    .venv/bin/python -m pytest rtl-sketch/ -q
"""
import os, sys
import pytest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import verify_ladder, verify_modal, verify_drums, verify_top, verify_voice, tanh_rom
import verify_ctl, verify_synth_top

needs_sim = pytest.mark.skipif(
    verify_ladder.tool("iverilog") is None or verify_ladder.tool("vvp") is None,
    reason="iverilog/vvp not found on PATH and OSS_CAD_SUITE not set")


def test_rom_images_are_the_models():
    """tanh16.hex / tanh256.hex must be exactly LadderFx.tbl plus 32767."""
    assert tanh_rom.main(["--check"]) == 0


@needs_sim
@pytest.mark.parametrize("entries", [16, 256])
def test_ladder_rtl_is_bit_exact(entries, tmp_path):
    """28,800 samples over five patches, every one identical to the model."""
    assert verify_ladder.main(["--tanh-n", str(entries), "--outdir", str(tmp_path)]) == 0


@needs_sim
@pytest.mark.parametrize("bug", ["FB", "SAT", "TANH_CLAMP"])
def test_negative_control_is_caught(bug, tmp_path):
    """A bench that cannot fail proves nothing. Each injected defect must
    produce a mismatch: status exactly 1, never 2 (which means it did not run)."""
    assert verify_ladder.main(["--tanh-n", "16", "--inject", bug,
                               "--outdir", str(tmp_path)]) == 1


@needs_sim
def test_modal_rtl_is_bit_exact(tmp_path):
    """The bar's six hits and rail-to-rail stress, the two numerators on
    noise and DC, and the coefficient extremes: every sample identical."""
    assert verify_modal.main(["--outdir", str(tmp_path)]) == 0


@needs_sim
@pytest.mark.parametrize("bug", ["SHIFT", "SAT", "PREEXC", "NUM_HOLD", "EXC_NOCLEAR"])
def test_modal_negative_control_is_caught(bug, tmp_path):
    assert verify_modal.main(["--inject", bug, "--outdir", str(tmp_path)]) == 1


@needs_sim
@pytest.mark.parametrize("nch", [2, 4])
def test_ladder_n_rtl_is_bit_exact_on_every_channel(nch, tmp_path):
    """ladder_dp_n (the voice's two filter contexts, ARCHITECTURE.md section 4):
    the same 28,800 samples driven to each channel in turn, every channel
    identical to the model, 19-bit output. The committed tb_ladder_n.v read
    120-bit words from the 128-bit vector file and could not pass; fixed."""
    assert verify_ladder.main(["--nch", str(nch), "--outdir", str(tmp_path)]) == 0


@needs_sim
def test_top_level_schedule_link_and_i2s(tmp_path):
    """synth_top through its pins: the status word reads back, the datapath is
    idle at every tick, the I2S stream decodes to the sample stream with D = 1,
    and the chip makes sound from the model's own register conversions."""
    assert verify_top.main(["--outdir", str(tmp_path), "--frames", "800"]) == 0


@needs_sim
def test_voice_rtl_is_bit_exact(tmp_path):
    """voice_dp against model/voice_fx.py at the register port: every
    scenario of verify_voice.py (every waveform, including the Model D set of
    contract revision 10; the noise source in both colours; oscillator 3 as a
    modulator on both destinations; every note and the
    increments where 5.5's clamps fire; glide up, down and at its limits;
    gate / trig / retrigger; a release to exactly zero; paraphonic keys; the
    register extremes) in the quick set, every sample, every tap of 16.4 and
    the final state identical. The full set (--set full, ~175k frames, with
    the audition reference sequences) is the documented command."""
    assert verify_voice.main(["--set", "quick", "--outdir", str(tmp_path)]) == 0


# each injected defect and the scenarios that reach the thing it breaks
VOICE_BUGS = [("SQUARE_SIGN", "default"),          # the square takes the saw's sign at the wrap
              ("ENV_FLOOR",   "silence"),          # release without max(1, .): the note never ends
              ("KEFF",        "default"),          # no resonance compensation: k_eff = k
              ("MIX_SAT",     "extremes"),         # the mixer wraps
              ("GLIDE_FLOOR", "notes"),            # slew without max(1, .): a small inc never moves
              ("RECIP_CLAMP", "notes"),            # a power-of-two inc gets r = 0
              ("TRIG_RESET",  "gate"),             # GATE_ON / TRIG reset the level to zero
              ("OUT_SAT",     "extremes"),         # no rail at the master mix
              ("LFSR_TAP",    "noise"),            # one tap of the noise polynomial wrong (6.10)
              ("NOISE_SEL",   "noise"),            # the colour selector stuck on white (2.5)
              ("SHARK_MIX",   "waves3"),           # R030 and R031 read the wrong way round (W3)
              ("MOD_NODELAY", "modulation")]       # the mod pan AND its register both gone (M9)


@needs_sim
def test_voice_bench_fails_on_a_stub(tmp_path):
    """The red run of docs/verification-rules.md: voice_dp's ports with no
    behaviour and every output X must give status exactly 1 -- a mismatch,
    with the frames reported as undefined -- never a pass and never 2."""
    assert verify_voice.main(["--set", "quick", "--only", "default", "--outdir", str(tmp_path),
                              "--rtl", os.path.join(HERE, "stubs", "voice_dp_stub.v")]) == 1


@needs_sim
@pytest.mark.parametrize("bug,only", VOICE_BUGS)
def test_voice_negative_control_is_caught(bug, only, tmp_path):
    """A bench that cannot fail proves nothing. Each injected defect must
    produce a mismatch on the scenario that reaches it: status exactly 1,
    never 2 (which means it did not run)."""
    assert verify_voice.main(["--set", "quick", "--only", only, "--inject", bug, "--outdir", str(tmp_path)]) == 1


@needs_sim
def test_drum_section_rtl_is_bit_exact(tmp_path):
    """The short stimulus (every stop soloed, edge semantics, all eight at
    accent 2.0, a bar with the choke and a BD retune, register extremes on
    the last paths and the spare mode, a RESET, decay to silence): both
    buses identical to the model on every frame. verify_drums.py without
    --short is the full-length run of the same stream."""
    assert verify_drums.main(["--short", "--outdir", str(tmp_path)]) == 0


@needs_sim
@pytest.mark.parametrize("bug", ["DRUM_ENV_FLOOR", "DRUM_LEVEL_TRIG", "DRUM_LFSR_TAP", "DRUM_TAP_NOSAT",
                                 "DRUM_LAST_PATH", "DRUM_SQ_LONE", "MODAL_NUM_HOLD", "MODAL_EXC_NOCLEAR",
                                 "DRUM_FINAL_WEAK", "DRUM_FINAL_SHORT", "DRUM_FINAL_SHIFT",
                                 "DRUM_FCAP_STALE"])
def test_drum_negative_control_is_caught(bug, tmp_path):
    """Each defect -- the envelope without its max(1, .), level- instead of
    edge-triggered stops, a wrong LFSR tap, a wrapping tap, the strawman's
    dropped last drum, a lone-square source that returns the PAIR (rev 5's
    cowbell defect, the one that made a 260 Hz difference tone), a numerator
    with no history, an excitation register that is not consumed -- must
    produce a mismatch: status exactly 1."""
    assert verify_drums.main(["--short", "--inject", bug, "--outdir", str(tmp_path)]) == 1


@needs_sim
def test_drum_timing_contract_is_load_bearing(tmp_path):
    """The control buses must be held from the tick to body_valid (15.6).
    Applying a frame's writes 20 clocks into the frame instead must be seen
    by the comparison: status exactly 1."""
    assert verify_drums.main(["--short", "--jitter", "20", "--outdir", str(tmp_path)]) == 1


# ---- the control link: can it CARRY what the models write? ------------------

@needs_sim
def test_control_link_carries_both_models_register_images(tmp_path):
    """Every bench above drives the register WRITE PORT. This one drives the
    PINS and compares what reaches the port against what the host INTENDED --
    155 writes: the voice's patch image from model/voice_fx.py's own
    conversion, the reference kit from model/drums_fx.py's kit_808(), and
    every drum register class at its full width. Status exactly 0."""
    assert verify_ctl.main(["--outdir", str(tmp_path)]) == 0


@needs_sim
def test_dr7_revision1_frame_could_not_carry_the_drum_image(tmp_path):
    """THE DEFECT THIS BENCH EXISTS FOR, kept runnable. DR 0007 revision 1's
    32-bit frame -- 7-bit address, 24-bit datum, no page bit -- corrupts 118
    of those 155 writes: 118 have no drum page to land in, 67 addresses do not
    fit in 7 bits (A_PATH 0x80, A_MODE 0xC0, A_RESET 0xFF) and 26 data do not
    fit in 24 (ENV_CTL is 27 bits, MODE_A1/A2 are 26). Status exactly 1."""
    assert verify_ctl.main(["--link", "dr7rev1", "--outdir", str(tmp_path)]) == 1


# Each control names the failure it is RECORDED to cause. A status of 1 alone
# is not evidence: an expectation satisfied by the WRONG failure is how a
# strict-xfail entry stayed red in this repository after the defect it tracked
# had already been fixed. `field` must be non-zero and every other field zero.
CTL_CONTROLS = [
    ("SPI_ADDR7",  "bad_addr"),      # revision 1's 7-bit address field
    ("SPI_DATA24", "bad_data"),      # revision 1's 24-bit data field
    ("SPI_NOSEC",  "bad_sec"),       # no page bit: drum writes land on the voice
]


@needs_sim
@pytest.mark.parametrize("bug,field", CTL_CONTROLS)
def test_control_link_negative_control_fails_the_recorded_way(bug, field, tmp_path):
    """Not just "it went red": the field that must be wrong is wrong and the
    other three are clean, so the control cannot be satisfied by an unrelated
    defect somewhere else in the link."""
    assert verify_ctl.main(["--inject", bug, "--outdir", str(tmp_path)]) == 1
    got = verify_ctl.LAST
    assert got[field] > 0, f"{bug} did not corrupt {field}: {got}"
    for other in ("bad_flag", "bad_sec", "bad_addr", "bad_data"):
        if other != field:
            assert got[other] == 0, f"{bug} also corrupted {other}, which it does not model: {got}"


@needs_sim
def test_control_link_discards_a_missized_transaction(tmp_path):
    """A transaction of any length but 48 bits is discarded (DR 0007 section
    1). The control applies them instead, so MORE writes reach the port than
    were sent -- a different failure from a corrupted one, and checked as such."""
    assert verify_ctl.main(["--inject", "SPI_ANYLEN", "--outdir", str(tmp_path)]) == 1
    got = verify_ctl.LAST
    assert got["count_seen"] > got["count_sent"], got


@needs_sim
def test_control_link_drain_must_precede_go(tmp_path):
    """The drain must apply every write before any datapath block reads a
    control register. The control moves it to cycle 8, where `go` is; the
    failure must be the late-write count, not a corrupted payload."""
    assert verify_ctl.main(["--inject", "SPI_DRAIN_LATE", "--outdir", str(tmp_path)]) == 1
    got = verify_ctl.LAST
    assert got["late"] > 0, got


# ---- the whole chip at its pins, against the model -------------------------

@needs_sim
def test_chip_is_bit_exact_at_its_pins(tmp_path):
    """synth_top through SCK/MOSI/CS_N in and BCLK/LRCLK/SDATA out: the voice
    image and the reference drum kit are written over the link, notes are
    played, drums are struck, a body is retuned while it rings, ROUTE.DFILT is
    engaged and released mid-ring, and every I2S word decoded from the WIRE is
    compared against model/synth_top_model.py. Not against dut.sample: that
    comparison is circular and is what tb_synth_top.v does."""
    assert verify_synth_top.main(["--short", "--outdir", str(tmp_path)]) == 0


# Same discipline at the chip level, and here it earns its keep twice over: a
# defect in the CORE and a defect in the SERIALISER both turn the wire red, and
# only the core column tells them apart. "core" means the core's own stream is
# already wrong; "wire" means the core is right and i2s_tx is not.
CHIP_CONTROLS = [
    ("VOICE_MASTER_PRESHIFT", "core"),   # two floors instead of contract 12's one
    ("VOICE_DRUM_CLAMP16",    "core"),   # the drum buses clipped before their gains
    ("VOICE_OUT_SAT",         "core"),   # no rail
    ("MODAL_NUM_HOLD",        "core"),
    ("MODAL_EXC_NOCLEAR",     "core"),
    ("DRUM_LFSR_TAP",         "core"),
    ("I2S_SHIFT",             "wire"),   # every bit one BCLK late
    ("I2S_DELAY",             "wire"),   # the sample a period late (D = 2)
]


@needs_sim
@pytest.mark.parametrize("bug,where", CHIP_CONTROLS)
def test_chip_negative_control_fails_the_recorded_way(bug, where, tmp_path):
    """Status 1 is not enough. A `core` control must show the core's own
    stream already wrong; a `wire` control must show the core CORRECT and the
    decoded wire wrong, which is the only way this bench can tell a serialiser
    defect from a datapath one."""
    assert verify_synth_top.main(["--short", "--inject", bug, "--outdir", str(tmp_path)]) == 1
    got = verify_synth_top.LAST
    assert got["wire_mismatch"] > 0, f"{bug} did not change the wire: {got}"
    if where == "core":
        assert got["core_bad"] > 0, f"{bug} is a datapath defect but the core's stream was clean: {got}"
    else:
        assert got["core_bad"] == 0, f"{bug} is a serialiser defect but the core's stream was wrong too: {got}"


@needs_sim
def test_chip_channel_swap_shows_as_a_channel_swap(tmp_path):
    """I2S_SWAP must fail as L != R and NOT as a wrong sample value: the left
    channel still carries the right word. A bench that only compared one
    channel would call this green, and that is the bug that shipped in trial1."""
    assert verify_synth_top.main(["--short", "--inject", "I2S_SWAP", "--outdir", str(tmp_path)]) == 1
    got = verify_synth_top.LAST
    assert got["swap"] > 0 and got["wire_mismatch"] == 0 and got["core_bad"] == 0, got


@needs_sim
def test_chip_write_landing_frame_matches_the_pin(tmp_path):
    """The model is driven by the frame the CS_N PIN predicts, and the chip
    must agree with it. If the model were driven by the frame the chip
    reported, a link that delayed every write by a frame would move the model
    with it and nothing could see it."""
    assert verify_synth_top.main(["--short", "--outdir", str(tmp_path)]) == 0
    got = verify_synth_top.LAST
    assert got["frame_pred_bad"] == 0 and got["frame_no_pred"] == 0, got


@needs_sim
def test_chip_reaches_the_envelope_dead_zone(tmp_path):
    """DRUM_ENV_FLOOR needs the full-length stimulus, because the short one
    never drives an envelope into the dead zone and the control went UNCAUGHT
    until the stimulus was changed to strike the open hat from just above its
    freeze level. Recorded as a test so the hole cannot come back."""
    assert verify_synth_top.main(["--inject", "DRUM_ENV_FLOOR", "--outdir", str(tmp_path)]) == 1


@needs_sim
@pytest.mark.parametrize("legacy", [False, True])
def test_ladder_channel_bleed_is_caught_by_either_stimulus(legacy, tmp_path):
    """INJECT_BUG_LADDER_CH_BLEED shares the half-sample delay line across
    channels. Both the row-per-channel stimulus and the old every-row-to-every-
    channel one catch it -- measured, and it corrects the claim that the old
    stimulus was blind to cross-channel bleeding. What the old one really could
    not do is run the two contexts on DIFFERENT signals and coefficients, which
    is what the chip does (the drum filter has its own DCUT/DK/DGAIN/DOGAIN)."""
    args = ["--nch", "2", "--inject", "CH_BLEED", "--outdir", str(tmp_path)]
    if legacy:
        args.append("--legacy-stimulus")
    assert verify_ladder.main(args) == 1
