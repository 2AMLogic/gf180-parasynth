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
import run_case

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


@pytest.mark.parametrize("extra,offending", [
    ("verify_synth_top: REFUSED: the drum solo did not reach: partial run", "REFUSED"),
    ("run_all: NO-VERDICT (timeout)", "NO-VERDICT"),
])
def test_refuses_a_bare_refusal_or_a_no_verdict_next_to_a_pass(extra, offending):
    """START RED: both used to pass. The gate refused `REFUSED --` and `FAIL --`
    only, so a refusal spelled without the dashes, or a timeout, could score."""
    with pytest.raises(score.Refused, match="not a clean PASS") as caught:
        score.validate_transcript(_transcript(extra=extra), "deadbeef", "SD", FRAMES)
    assert offending in str(caught.value)


@pytest.mark.parametrize("extra", [
    "verify_synth_top: REFUSED: the drum solo did not reach: partial run",
    "run_all: NO-VERDICT (timeout)",
])
def test_main_refuses_a_bare_refusal_before_reading_the_window(
        tmp_path, monkeypatch, capsys, extra):
    """START RED, on the path that ships: a transcript that used to pass the
    gate went on to the Fischer corpus and to a board row."""
    def read(*_):
        raise AssertionError("the scorer read the window described by a transcript "
                             "it had to refuse")
    monkeypatch.setattr(score, "load_window", read)
    wav = tmp_path / "d02a-i2s.wav"
    wav.write_bytes(b"never read: the transcript is refused first")
    report = tmp_path / "report.txt"
    report.write_text(_transcript(sha=hashlib.sha256(wav.read_bytes()).hexdigest(),
                                  extra=extra))
    out = tmp_path / "D02A.json"
    assert score.main(["--case", "D02A", "--wav", str(wav), "--verification", str(report),
                       "--out", str(out)]) == 2
    assert "REFUSED -- the report is not a clean PASS" in capsys.readouterr().out
    assert not out.exists()


def test_the_committed_d02a_transcript_still_passes_with_its_records_digest():
    """Real board output: the transcript behind the committed D02A row, which
    has to clear the gate exactly as it cleared the hand-copied checks the
    gate replaces."""
    import json
    root = pathlib.Path(score.ROOT)
    record = json.loads((root / "docs/scorecard/results/D02A.json").read_text())
    text = (root / "docs/scorecard/drum-d02a-i2s/verify-drum-solo-sd.log").read_text()
    run = score.validate_transcript(text, record["diagnostics"]["decoded_i2s_sha256"],
                                    "SD", FRAMES)
    assert run["simulator"] == "verilator"


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


def test_measurement_sources_hash_the_transcript_gate():
    """Mirror of #1208: the module this scorer calls to validate transcripts
    must be in its own hashed sources, derived from the import rather than a
    literal so a future extraction cannot silently fall outside
    `measurement_source_sha256`."""
    helper = "tools/" + pathlib.Path(score.i2s_transcript.__file__).name
    assert helper in score.MEASUREMENT_SOURCES


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


# ---- one capture in the file, not two (#1562) -------------------------------

TWO_RUNS = ("ambiguous transcript: it holds 2 simulator backend declarations, "
            "2 PASS verdicts, 2 decoded I2S WAV digests, and one run prints one of each")
WITHOUT_A_BACKEND = ("ambiguous transcript: it holds 2 PASS verdicts, "
                     "2 decoded I2S WAV digests, and one run prints one of each")
_SHA_A, _SHA_B = "a" * 64, "b" * 64


def _second_run(**kwargs):
    """A clean run that strikes in frame 783, not 782."""
    transcript = _transcript(**kwargs).replace("782", "783")
    assert "struck in frame 783" in transcript
    return transcript


def _without_backend(transcript):
    stripped = transcript.replace("verify_synth_top: simulator backend verilator\n", "")
    assert stripped != transcript
    return stripped


def test_two_clean_runs_in_one_transcript_are_refused_rather_than_paired():
    """START RED (#1562). Offered the second run's WAV, the transcript used to
    yield the FIRST run's strike frame."""
    a = _transcript(sha=_SHA_A)
    b = _second_run(sha=_SHA_B)
    assert score.validate_transcript(a, _SHA_A, "SD", FRAMES)["strike_frame"] == 782
    assert score.validate_transcript(b, _SHA_B, "SD", FRAMES)["strike_frame"] == 783
    other = b.replace("simulator backend verilator", "simulator backend iverilog")
    for second, reason in ((b, TWO_RUNS), (other, TWO_RUNS),
                           (_without_backend(b), WITHOUT_A_BACKEND)):
        with pytest.raises(score.Refused) as caught:
            score.validate_transcript(a + second, _SHA_B, "SD", FRAMES)
        assert str(caught.value) == reason


def test_main_refuses_a_two_run_transcript_before_reading_the_window(
        tmp_path, monkeypatch, capsys):
    """START RED (#1562), on the path that ships: this transcript used to pass
    the gate, and the window it then read was scored as the first run's."""
    def read(*_):
        raise AssertionError("the scorer read the window described by a transcript "
                             "it had to refuse")
    monkeypatch.setattr(score, "load_window", read)
    wav = tmp_path / "d02a-i2s.wav"
    wav.write_bytes(b"never read: the transcript is refused first")
    report = tmp_path / "report.txt"
    report.write_text(_transcript(sha=_SHA_A) + _second_run(
        sha=hashlib.sha256(wav.read_bytes()).hexdigest()))
    out = tmp_path / "D02A.json"
    assert score.main(["--case", "D02A", "--wav", str(wav), "--verification", str(report),
                       "--out", str(out)]) == 2
    assert "REFUSED -- ambiguous transcript" in capsys.readouterr().out
    assert not out.exists()


# ---- the reference corpus boundary (#1920) --------------------------------
#
# The #1782 census listed this module as Refused-only against the estimator's
# `NonFiniteAudio`, and about this module it is right -- but nothing this
# module accepts makes the estimator refuse, because `run_case.load_reference`
# converts the refusal before it returns. Observed on `8f3d8ed2`, through the
# real `__main__` with a float32 NaN where the corpus keeps SD: exit 2,
#
#   score_drum_i2s: REFUSED -- reference recording sd8/SD5050.WAV holds 1
#   non-finite samples of 2000 (1 NaN, 0 Inf; first at index 500): this is not
#   audio. Every threshold guard downstream of here would be DEFEATED by it
#   rather than tripped, because NaN compares False against any bound (#133,
#   #134)
#
# and nothing on stderr, no report. Unreachable, so no handler was widened.

REF_SAMPLES = 2000


def _ref_corpus(tmp_path: pathlib.Path, x) -> tuple:
    """A one-recording Fischer corpus holding `x` where `load_reference` reads
    SD from. Returns the corpus root, the recording's relative name, and what
    scipy reads BACK, so a test asserts what the loader was handed rather than
    what the test wrote."""
    rel = run_case.REF_MAIN["SD"][0]
    refdir = tmp_path / "refs"
    (refdir / rel).parent.mkdir(parents=True)
    wavfile.write(refdir / rel, 48000, x)
    return refdir, rel, wavfile.read(refdir / rel)[1]


def _bound_inputs(tmp_path: pathlib.Path) -> list:
    """The argv for a window and a transcript bound to its digest: enough for
    `main` to get past its own gates and reach the reference read, which is
    the site under test."""
    x = np.zeros(FRAMES, dtype=np.int16)
    x[10] = 1000
    wav = _wav(tmp_path, x)
    report = tmp_path / "report.txt"
    report.write_text(_transcript(sha=hashlib.sha256(wav.read_bytes()).hexdigest()))
    return ["--case", "D02A", "--wav", str(wav), "--verification", str(report)]


@pytest.mark.parametrize("bad,nan,inf", [(np.nan, 1, 0), (np.inf, 0, 1)],
                         ids=["nan", "inf"])
def test_a_non_finite_reference_is_refused_before_it_can_leave_as_nonfiniteaudio(
        tmp_path, capsys, bad, nan, inf):
    """Pins the gate that makes the #1920 candidate unreachable, by its REASON
    and not only by its exit code: a float32 file is also what the width guard
    below the finiteness assert refuses, and an exit code alone cannot tell
    "holds a NaN" from "is float32". Goes red the moment `run_case.load_reference`
    stops converting `NonFiniteAudio` (the class then leaves `main`) or moves
    that conversion behind the width guard."""
    ref = np.zeros(REF_SAMPLES, dtype=np.float32)
    ref[10] = 1000.0
    ref[500] = bad
    refdir, rel, back = _ref_corpus(tmp_path, ref)
    assert back.dtype == np.float32 and not np.isfinite(back[500])
    assert int(np.isfinite(back).sum()) == REF_SAMPLES - 1
    out = tmp_path / "D02A.json"
    assert score.main(_bound_inputs(tmp_path)
                      + ["--refs", str(refdir), "--out", str(out)]) == 2
    assert (f"score_drum_i2s: REFUSED -- reference recording {rel} holds 1 "
            f"non-finite samples of {REF_SAMPLES} ({nan} NaN, {inf} Inf; first at "
            f"index 500): this is not audio") in capsys.readouterr().out
    assert not out.exists()


def test_an_int16_reference_through_the_same_seam_is_not_refused_as_non_finite(tmp_path):
    """The control for the rows above: the same corpus root, the same file
    name, the same writer, and 16-bit PCM -- the one Fischer format
    `load_reference` accepts, which cannot hold a NaN. Nothing is refused as
    non-finite. Without this a refusal up there could be the apparatus (a
    path scipy cannot read, a directory in the wrong place) rather than the
    NaN."""
    x = np.zeros(REF_SAMPLES, dtype=np.int16)
    x[10] = 1000
    refdir, rel, back = _ref_corpus(tmp_path, x)
    assert back.dtype == np.int16
    y, _sr, rel_back, _setting = run_case.load_reference("SD", refdir)
    assert rel_back == rel
    assert bool(np.isfinite(y).all()) and y[10] == pytest.approx(1000 / 32768.0)


@pytest.mark.parametrize("fault", [AssertionError, run_case.am.NonFiniteAudio],
                         ids=["plain-AssertionError", "NonFiniteAudio"])
def test_this_module_alone_does_not_stop_an_assertionerror_at_the_reference_seam(
        tmp_path, monkeypatch, fault):
    """The census row, run rather than argued: a plain `AssertionError` -- and
    the `NonFiniteAudio` that subclasses it -- raised where `main` calls
    `run_case.load_reference` passes both of this module's handlers and leaves
    `main`, an exit-1 traceback from `__main__`. That is the contract
    (`tools/refusal.py`: an unexpected exception is an execution error, never
    a refusal), and it is why the NaN rows are load-bearing: nothing in
    `tools/score_drum_i2s.py` stops the estimator's refusal; only
    `run_case.load_reference` does. Goes red if this handler is ever widened
    to `AssertionError` or `Exception`, or `InsufficientEvidence` is named
    here as well."""
    def seam(*_, **__):
        raise fault("injected at the load_reference seam")
    monkeypatch.setattr(run_case, "load_reference", seam)
    out = tmp_path / "D02A.json"
    with pytest.raises(fault, match="injected at the load_reference seam"):
        score.main(_bound_inputs(tmp_path) + ["--refs", str(tmp_path), "--out", str(out)])
    assert not out.exists()
