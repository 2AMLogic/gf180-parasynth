import hashlib
import pathlib
import re
import subprocess

import pytest

import score_m5a_i2s as scorer
from score_m5a_i2s import validate_integration_report

_HASH = "a" * 64


def _report(detail="27.2 s phrase and complete release", config="selected 2x saw + causal 2x filter candidate"):
    return "\n".join(("verify_synth_top: " + detail,
                      "verify_synth_top: " + config,
                      "verify_synth_top: M5A controls: pulse=pulse29; effective pulse=pulse479 (47.90% duty); saw cutoff=20000 Hz; saw volume correction=-0.45428 dB",
                      "verify_synth_top: selected 2x path; compile defines: VOICE_OSC_2X, VOICE_FILTER_2X",
                      "verify_synth_top: PASS -- decoded I2S periods match",
                      "verify_synth_top: M5A path verified from SPI pins through the production voice and I2S pins",
                      "verify_synth_top: simulator backend verilator",
                      f"verify_synth_top: decoded I2S WAV sha256 {_HASH}"))


def test_accepts_only_complete_filter_candidate_integration_report():
    controls = validate_integration_report(_report(), _HASH, pulse_shape="pulse29",
                                           saw_cutoff_hz=20_000,
                                           saw_volume_correction_db=-0.45428)
    assert controls["pulse_control_label"] == "pulse29"
    assert controls["pulse_effective_waveform"] == "pulse479"
    assert controls["pulse_effective_duty_percent"] == pytest.approx(47.9, abs=0.01)


@pytest.mark.parametrize("text", [
    _report(detail="smoke: envelope completion not claimed"),
    _report(config="selected 2x saw candidate"),
    "PASS -- unrelated simulator output",
])
def test_rejects_smoke_or_unrelated_report(text):
    with pytest.raises(ValueError, match="full filter-candidate"):
        validate_integration_report(text, _HASH)


def test_refuses_report_bound_to_a_different_wav():
    with pytest.raises(ValueError, match="does not bind"):
        validate_integration_report(_report(), "b" * 64)


def test_refuses_report_with_different_sound_controls():
    with pytest.raises(ValueError, match="cutoff"):
        validate_integration_report(_report(), _HASH, saw_cutoff_hz=14_073)


def test_refuses_report_without_filter2x_duty_mapping():
    text = _report().replace("VOICE_FILTER_2X", "VOICE_FILTER_1X")
    with pytest.raises(ValueError, match="VOICE_FILTER_2X"):
        validate_integration_report(text, _HASH)


def test_refuses_inconsistent_control_and_effective_pulse_metadata():
    text = _report().replace("pulse479 (47.90% duty)", "pulse29 (29.00% duty)")
    with pytest.raises(ValueError, match="effective pulse"):
        validate_integration_report(text, _HASH)


def test_filter2x_rtl_duty_encoding_matches_reported_effective_waveform():
    source = (scorer.ROOT / "rtl-sketch/voice_dp.v").read_text()
    match = re.search(
        r"`ifdef VOICE_FILTER_2X\s+localparam \[23:0\] DUTY_WIDE = 24'd(\d+)",
        source)
    assert match, "selected RTL must declare its VOICE_FILTER_2X pulse-width encoding"
    assert int(match.group(1)) == scorer.vf.DUTY["pulse479"]
    assert 100 * int(match.group(1)) / scorer.vf.CYCLE == pytest.approx(47.9, abs=0.001)


def test_case_and_pulse_configuration_must_match_requested_measurement():
    text = _report().replace("M5A", "M5B").replace(
        "VOICE_FILTER_2X", "VOICE_FILTER_2X, VOICE_PULSE_2X")
    controls = validate_integration_report(text, _HASH, case_id="M5B", pulse_2x=True)
    assert controls["oscillator_pulse_oversample_2x"] is True
    with pytest.raises(ValueError, match="full filter-candidate"):
        validate_integration_report(text, _HASH, case_id="M5A", pulse_2x=True)
    with pytest.raises(ValueError, match="pulse 2x"):
        validate_integration_report(_report(), _HASH, pulse_2x=True)
    with pytest.raises(ValueError, match="mutation"):
        validate_integration_report(text + "\nINJECT_BUG_VOICE_PULSE2X_OFF", _HASH,
                                    case_id="M5B", pulse_2x=True)


# ---- one transcript gate for every SPI-to-I2S scorer (#1556) ----------------

def _before_the_verdict(report, line):
    """`line` where the bench prints it: before the run's final verdict."""
    return report.replace("verify_synth_top: PASS --",
                          line + "\nverify_synth_top: PASS --", 1)


@pytest.mark.parametrize("text,offending", [
    # #1376: the bench states the surplus register writes with FAIL -- and, the
    # wire matching, still ends the run with PASS --
    (_before_the_verdict(_report(), "verify_synth_top: FAIL -- 68 writes reached the "
                                    "register port, 67 were sent"), "FAIL --"),
    (_report() + "\nverify_synth_top: REFUSED -- partial run", "REFUSED"),
    (_report() + "\nverify_synth_top: REFUSED: partial run", "REFUSED"),
    (_report() + "\nverify_m5a_filter2x_i2s: NO-VERDICT -- full RTL phrase timed out",
     "NO-VERDICT"),
])
def test_refuses_a_pass_beside_a_forbidden_outcome(text, offending):
    with pytest.raises(ValueError, match="not a clean PASS") as caught:
        validate_integration_report(text, _HASH)
    assert offending in str(caught.value)


def test_refuses_a_transcript_that_never_said_pass():
    text = _report().replace("verify_synth_top: PASS -- decoded I2S periods match\n", "")
    with pytest.raises(ValueError, match="not a clean PASS"):
        validate_integration_report(text, _HASH)


def test_a_mutation_run_is_named_before_the_run_is_even_recognised():
    with pytest.raises(ValueError, match="^mutation runs cannot provide sound evidence$"):
        validate_integration_report("verify_synth_top: INJECT_BUG_SPI_ADDR7; no M5A tokens\n",
                                    _HASH)


@pytest.mark.parametrize("backend", ["simulator backend magic", "simulator backend"])
def test_the_validator_itself_refuses_an_unnamed_simulator_backend(backend):
    """The backend used to be looked for in `main()` only, so the validator
    accepted a transcript that named no simulator this repository knows."""
    text = _report().replace("verify_synth_top: simulator backend verilator",
                             f"verify_synth_top: {backend}")
    with pytest.raises(ValueError, match="simulator backend"):
        validate_integration_report(text, _HASH)


@pytest.mark.parametrize("simulator", ["verilator", "iverilog"])
def test_the_validator_returns_the_simulator_the_transcript_names(simulator):
    controls = validate_integration_report(
        _report().replace("simulator backend verilator", f"simulator backend {simulator}"),
        _HASH)
    assert controls["simulator"] == simulator


def test_main_refuses_a_surplus_write_transcript_before_measuring_anything(
        tmp_path, monkeypatch, capsys):
    """START RED (#1376), on the path that ships. `FAIL --` for writes that
    reached the register port but were never sent, then `PASS --`: this
    transcript used to pass the validator and reach `m5a.measure`, and a board
    row came out the other side."""
    def measured(**_):
        raise AssertionError("the scorer measured audio described by a transcript "
                             "it had to refuse")
    monkeypatch.setattr(scorer.m5a, "measure", measured)
    wav = tmp_path / "phrase.wav"
    wav.write_bytes(b"never read: the transcript is refused first")
    report = tmp_path / "report.txt"
    report.write_text(_before_the_verdict(
        _report().replace(_HASH, hashlib.sha256(wav.read_bytes()).hexdigest()),
        "verify_synth_top: FAIL -- 68 writes reached the register port, 67 were sent"))
    out = tmp_path / "M5A.json"
    assert scorer.main(["--wav", str(wav), "--verification", str(report),
                        "--out", str(out)]) == 2
    assert "REFUSED -- the report is not a clean PASS" in capsys.readouterr().out
    assert not out.exists()


def test_every_committed_m5a_run_that_binds_a_wav_still_passes_with_its_own_digest():
    """Real board output: every clean M5A/M5B run the repository holds a
    transcript of, validated with the digest it printed. A gate tightened past
    what the chip's own passing runs print would go red here, on the files
    behind the committed rows, before it did on the board.

    A run is found by structure, not by name: the bench's exit-0 line for the
    mono path, printed on no other path; one simulator line, so one run and
    not a `run_all` report; and its own controls line and WAV digest, which is
    what this scorer reads. Mutation controls are not clean runs and are
    excluded rather than expected to pass."""
    root = pathlib.Path(scorer.ROOT)
    verified = "path verified from SPI pins through the production voice and I2S pins"
    listed = subprocess.run(["git", "grep", "-lF", verified, "--", "docs", "fpga/reports"],
                            cwd=root, capture_output=True, text=True, check=True).stdout.split()
    bound = []
    for rel in listed:
        text = (root / rel).read_text()
        digest = re.search(r"decoded I2S WAV sha256 ([0-9a-f]{64})", text)
        controls_line = re.search(r"verify_synth_top: (M5[AB]) controls:", text)
        single_run = (text.count("verify_synth_top: simulator backend") == 1
                      and not text.lstrip().startswith(("{", "[")))
        if not (digest and controls_line and single_run) or "INJECT_BUG_" in text:
            continue
        bound.append(rel)
        controls = validate_integration_report(text, digest.group(1),
                                               case_id=controls_line.group(1))
        assert controls["simulator"] in ("verilator", "iverilog"), rel
    assert len(bound) >= 16, bound


def test_measurement_sources_hash_the_transcript_gate():
    """Mirror of #1208: the module this scorer calls to validate transcripts
    must be in its own hashed sources, derived from the import rather than a
    literal so a future extraction cannot silently fall outside
    `measurement_source_sha256`."""
    helper = "tools/" + pathlib.Path(scorer.i2s_transcript.__file__).name
    assert helper in scorer.MEASUREMENT_SOURCES
    root = pathlib.Path(scorer.ROOT)
    missing = [rel for rel in scorer.MEASUREMENT_SOURCES if not (root / rel).is_file()]
    assert missing == []


# ---- one capture in the file, not two (#1562) -------------------------------

TWO_RUNS = ("ambiguous transcript: it holds 2 simulator backend declarations, "
            "2 PASS verdicts, 2 decoded I2S WAV digests, and one run prints one of each")
WITHOUT_A_BACKEND = ("ambiguous transcript: it holds 2 PASS verdicts, "
                     "2 decoded I2S WAV digests, and one run prints one of each")
#: the two controls of #1562's own reproduction
_A = dict(sha="a" * 64, cutoff_hz=14_073, correction_db=-0.45428)
_B = dict(sha="b" * 64, cutoff_hz=20_000, correction_db=5.22576)


def _capture(*, sha, cutoff_hz, correction_db, simulator="verilator"):
    """One clean M5A capture, with its own controls, WAV and simulator."""
    report = (_report()
              .replace("saw cutoff=20000 Hz", f"saw cutoff={cutoff_hz} Hz")
              .replace("saw volume correction=-0.45428 dB",
                       f"saw volume correction={correction_db:+.5f} dB")
              .replace(_HASH, sha)
              .replace("simulator backend verilator", f"simulator backend {simulator}"))
    assert f"verify_synth_top: decoded I2S WAV sha256 {sha}" in report
    return report


def _without_backend(report):
    stripped = "".join(line for line in report.splitlines(keepends=True)
                       if not line.startswith("verify_synth_top: simulator backend"))
    assert stripped != report
    return stripped


def test_two_clean_runs_in_one_file_are_refused_rather_than_paired():
    """START RED (#1562). Offered the second run's WAV, the validator used to
    return the FIRST run's controls."""
    a, b = _capture(**_A), _capture(**_B)
    assert validate_integration_report(a, _A["sha"])["saw_cutoff_hz"] == 14_073
    assert validate_integration_report(b, _B["sha"])["saw_cutoff_hz"] == 20_000
    other = _capture(**{**_B, "simulator": "iverilog"})
    for second, reason in ((b, TWO_RUNS), (other, TWO_RUNS),
                           (_without_backend(b), WITHOUT_A_BACKEND)):
        with pytest.raises(ValueError) as caught:
            validate_integration_report(a + second, _B["sha"])
        assert str(caught.value) == reason


def test_main_refuses_a_two_run_transcript_before_measuring_anything(
        tmp_path, monkeypatch, capsys):
    """START RED (#1562), on the path that ships: this transcript used to pass
    the validator and reach `m5a.measure`, which scored the offered WAV under
    the first run's controls."""
    def measured(**_):
        raise AssertionError("the scorer measured audio described by a transcript "
                             "it had to refuse")
    monkeypatch.setattr(scorer.m5a, "measure", measured)
    wav = tmp_path / "phrase.wav"
    wav.write_bytes(b"never read: the transcript is refused first")
    report = tmp_path / "report.txt"
    report.write_text(_capture(**_A) + _capture(
        **{**_B, "sha": hashlib.sha256(wav.read_bytes()).hexdigest()}))
    out = tmp_path / "M5A.json"
    assert scorer.main(["--wav", str(wav), "--verification", str(report),
                        "--out", str(out)]) == 2
    assert "REFUSED -- ambiguous transcript" in capsys.readouterr().out
    assert not out.exists()


def test_two_committed_m5a_captures_score_alone_and_refuse_concatenated():
    """Real board output: the two captures #1562 was filed on, replayed as the
    issue's table has them. Each alone returns the controls it printed; offered
    the second run's WAV, the concatenation used to return the first run's."""
    base = scorer.ROOT / "docs/scorecard/m5-artifacts-333"
    a = (base / "repro/rtl-M5A.txt").read_text()
    b = (base / "saw-drive/confirm/M5A.txt").read_text()
    sha_a = re.search(r"decoded I2S WAV sha256 ([0-9a-f]{64})", a).group(1)
    sha_b = re.search(r"decoded I2S WAV sha256 ([0-9a-f]{64})", b).group(1)
    assert sha_a != sha_b
    alone_a = validate_integration_report(a, sha_a)
    alone_b = validate_integration_report(b, sha_b)
    assert alone_a["saw_volume_correction_db"] == pytest.approx(-0.45428, abs=1e-5)
    assert alone_b["saw_volume_correction_db"] == pytest.approx(5.22576, abs=1e-5)
    for text, reason in ((a + b, TWO_RUNS), (a + _without_backend(b), WITHOUT_A_BACKEND)):
        with pytest.raises(ValueError) as caught:
            validate_integration_report(text, sha_b)
        assert str(caught.value) == reason
