"""What `tools/score_drum_i2s.py` refuses to turn into a board row.

The scorer's whole job is to attach the label `integrated-rtl` to three
numbers. Everything that makes that label mean something is a precondition on
the transcript and the WAV it is offered, and every one of them is checked
here -- because a scorer that answers when its preconditions failed produces
output indistinguishable from evidence.
"""
import hashlib
import pathlib

import numpy as np
import pytest
from scipy.io import wavfile

import score_drum_i2s as score

FRAMES = int(2.2 * 48000)          # run_case's SD render length


def _transcript(*, sha="deadbeef", sound="SD", frames=FRAMES, accent="1.00",
                exact="True", differing=0, start=303, pass_line=True,
                backend="verilator", extra=""):
    end = start + frames
    lines = [
        "verify_synth_top: 154 writes over the pins (3 voice, 151 drum), 106144 frames after the last",
        f"verify_synth_top: drum solo {sound} (circuit 1 of 11) at accent {accent}, "
        f"{frames / 48000:.2f} s / {frames} frames, both drum buses 0.45, the voice silent; ROUTE = 0",
        "verify_synth_top: selected legacy single-rate waveform; compile defines: (none)",
        f"verify_synth_top: simulator backend {backend}",
        "verify_synth_top: built from 017e87c-dirty, outdir /tmp/x; synth_top.v 2fc3939d1140, "
        "drum_kit.v 23e97ff6e0e4, drum_regs.v 1282ebc944ee",
        f"verify_synth_top: drum solo {sound} struck in frame 782 at accent register 32768; "
        f"wire lag 1 period(s) behind the core; scored window [{start}, {end}) of 107409 "
        f"decoded periods",
        f"verify_synth_top: drum solo {sound} decoded window against the fixed model struck in "
        f"the SAME frame (782): {differing} of {frames} samples differ, max |difference| "
        f"{0 if exact == 'True' else 2414} LSB; bit-exact {exact}",
        f"verify_synth_top: drum solo {sound} decoded window against run_case.render_drum_solo "
        f"(strike in frame 480, the free-running noise LFSR at a different phase): 11411 of "
        f"{frames} samples differ, max |difference| 4522 LSB",
        "verify_synth_top: drum solo window sha256 3d366f83e4d8",
        "verify_synth_top: decoded I2S WAV written to /tmp/d02a-i2s.wav",
        f"verify_synth_top: decoded I2S WAV sha256 {sha}",
    ]
    if pass_line:
        lines += [
            "verify_synth_top: PASS -- 107409 I2S periods decoded from the wire, every one "
            "identical to the model; both channels agree; every slot 32 BCLK; the core's own "
            "stream matches too",
            f"verify_synth_top: {sound} drum path verified from SPI pins through the production "
            f"drum engine and I2S pins",
        ]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def test_a_clean_transcript_yields_the_integration_evidence():
    run = score.validate_transcript(_transcript(), "deadbeef", "SD", FRAMES)
    assert run["simulator"] == "verilator"
    assert run["strike_frame"] == 782
    assert run["wire_lag_periods"] == 1
    assert run["fixed_model_at_realised_strike"] == {
        "differing_samples": 0, "max_abs_difference": 0, "identical": True}
    assert run["fixed_model_scorecard_render"]["differing_samples"] == 11411
    assert run["fixed_model_scorecard_render"]["identical"] is False
    assert run["rtl_build"] == "017e87c-dirty"


@pytest.mark.parametrize("kwargs,match", [
    # a mutation run is a control, never evidence
    (dict(extra="verify_synth_top: INJECT_BUG_DRUM_LFSR_TAP"), "mutation runs"),
    # a wire that is not the model is a broken chip, not a measurement
    (dict(pass_line=False), "not a clean PASS"),
    (dict(extra="verify_synth_top: FAIL -- of 10 decoded I2S periods"), "not a clean PASS"),
    (dict(extra="verify_synth_top: REFUSED -- the drum solo did not reach"), "not a clean PASS"),
    # the transcript has to be about THIS audio
    (dict(sha="0000"), "does not bind this decoded I2S WAV"),
    # ... and about THIS sound
    (dict(sound="CH"), "does not verify the SD drum path"),
    # a smoke run is not the case
    (dict(frames=7200), "render length"),
    # the board's render is accent 1.0
    (dict(accent="2.00"), "accent 1.0"),
    # the controlled comparison failing means the difference is the CHIP
    (dict(exact="False", differing=6659), "not bit-exact against the fixed model"),
    # an unnamed backend is an unrecorded apparatus
    (dict(backend="nosuchsim"), "omits the simulator backend"),
])
def test_refuses_a_transcript_whose_preconditions_failed(kwargs, match):
    sha = kwargs.pop("sha", "deadbeef")
    frames = kwargs.get("frames", FRAMES)
    text = _transcript(sha=sha, **kwargs)
    with pytest.raises(score.Refused, match=match):
        score.validate_transcript(text, "deadbeef", "SD", FRAMES)
    assert frames is not None      # the smoke-run case really did shorten it


def test_refuses_a_transcript_with_no_stimulus_report():
    with pytest.raises(score.Refused, match="omits the drum-solo stimulus"):
        score.validate_transcript(
            "verify_synth_top: PASS -- nothing here\n"
            "verify_synth_top: SD drum path verified from SPI pins through the production "
            "drum engine and I2S pins\n"
            "verify_synth_top: decoded I2S WAV sha256 deadbeef\n"
            "verify_synth_top: simulator backend verilator\n",
            "deadbeef", "SD", FRAMES)


# ---- the window itself ----------------------------------------------------

def _wav(tmp_path: pathlib.Path, x, sr=48000) -> pathlib.Path:
    p = tmp_path / "w.wav"
    wavfile.write(p, sr, x)
    return p


def test_accepts_the_case_length_window(tmp_path):
    x = np.zeros(FRAMES, dtype=np.int16)
    x[10] = 1000
    y = score.load_window(_wav(tmp_path, x), FRAMES)
    assert len(y) == FRAMES and y[10] == pytest.approx(1000 / 32768.0)


@pytest.mark.parametrize("make,match", [
    (lambda: (np.zeros(FRAMES, dtype=np.int16), 44100), "48000 Hz"),
    (lambda: (np.zeros((FRAMES, 2), dtype=np.int16), 48000), "mono"),
    (lambda: (np.zeros(FRAMES, dtype=np.float32), 48000), "int16"),
    (lambda: (np.zeros(7200, dtype=np.int16), 48000), "this case renders"),
])
def test_refuses_a_window_that_is_not_the_case(tmp_path, make, match):
    x, sr = make()
    with pytest.raises(score.Refused, match=match):
        score.load_window(_wav(tmp_path, x, sr), FRAMES)


# ---- the engine label -----------------------------------------------------

def test_the_scorer_names_the_integrated_rtl_engine_and_nothing_else():
    """run_case's default is `fixed-model`; this must not have moved it, and
    this scorer must not be producing anything else."""
    import run_case
    assert run_case.ENGINE == "fixed-model"
    assert score.ENGINE == "integrated-rtl"


def test_the_scored_record_binds_its_own_measurement_sources():
    """Every source that decides what the number is must exist, or the record's
    `measurement_source_sha256` is a list of promises rather than hashes."""
    root = pathlib.Path(score.ROOT)
    missing = [rel for rel in score.MEASUREMENT_SOURCES if not (root / rel).is_file()]
    assert missing == []


def test_the_committed_record_is_an_integrated_rtl_record_bound_to_its_evidence():
    """The anchor this issue exists to produce, checked as it stands on disk:
    a Drums row whose engine is the chip, whose audio artefact is committed,
    and whose decoded-I2S hash is the hash of that artefact."""
    import json
    root = pathlib.Path(score.ROOT)
    record = json.loads((root / "docs/scorecard/results/D02A.json").read_text())
    assert record["engine"] == "integrated-rtl"
    assert record["provenance"]["engine"] == "integrated-rtl"
    audio = root / record["audio"].split("ours ")[1]
    assert audio.is_file()
    assert (hashlib.sha256(audio.read_bytes()).hexdigest()
            == record["diagnostics"]["decoded_i2s_sha256"])
    integration = record["diagnostics"]["integration"]
    assert integration["fixed_model_at_realised_strike"]["identical"] is True
