"""`validate_clean_pass`: the one transcript gate every SPI-to-I2S scorer uses.

Rule 1 (docs/verification-rules.md): these were first run against a stub with
the right signature and no behaviour, which answered `"verilator"` for every
input. Every refusal below was RED against it and every acceptance GREEN --
the shape a gate's tests must have before the gate exists.

The last three tests are not fixture tests. They run the gate over files the
repository has actually committed -- real board output -- because a gate that
refuses the runs it was written for is worse than no gate (rule 8).
"""
import pathlib
import re
import subprocess

import pytest

import i2s_transcript as gate

SHA = "a" * 64
OTHER_SHA = "b" * 64
SIMULATOR_LINE = "verify_synth_top: simulator backend"
SUCCESS_LINE = "verified from SPI pins"
#: `tools/run_all.py` ends every log with `<ok>/<n> passed`.
RUN_ALL_SUMMARY = re.compile(r"^\s*\d+/\d+ passed\b", re.M)


def _transcript(*extra, pass_line=True, simulator="verilator", sha=SHA):
    lines = ["verify_synth_top: M5A stimulus: 27.2 s phrase and complete release"]
    if simulator is not None:
        lines.append(f"verify_synth_top: simulator backend {simulator}")
    if sha is not None:
        lines.append(f"verify_synth_top: decoded I2S WAV sha256 {sha}")
    if pass_line:
        lines.append("verify_synth_top: PASS -- 1305600 I2S periods decoded from the wire")
    lines += list(extra)
    return "\n".join(lines) + "\n"


class _Refused(Exception):
    """A caller's own refusal class, standing in for a scorer's `Refused`."""


@pytest.mark.parametrize("simulator", ["verilator", "iverilog"])
def test_a_clean_pass_from_a_known_simulator_is_accepted_and_names_it(simulator):
    assert gate.validate_clean_pass(_transcript(simulator=simulator), wav_sha256=SHA) == simulator


@pytest.mark.parametrize("text", [
    _transcript("verify_synth_top: selected 2x saw + causal 2x filter candidate; compile "
                "defines: VOICE_OSC_2X, INJECT_BUG_SPI_ADDR7"),
    "INJECT_BUG_X\nverify_synth_top: FAIL -- of 3 decoded I2S periods\n",
])
def test_a_mutation_run_is_refused_whatever_verdict_it_reached(text):
    with pytest.raises(ValueError, match="mutation runs cannot provide sound evidence"):
        gate.validate_clean_pass(text)


def test_the_mutation_precondition_stands_alone_so_a_scorer_can_ask_it_first():
    """`validate_part` and `validate_integration_report` recognise their own
    run before the rest of the gate, and a control report is never that run.
    Asked alone, first, the mutation keeps a control from being refused as
    merely some other, incomplete run."""
    for text in ("INJECT_BUG_X\n", _transcript("compile defines: INJECT_BUG_DRUM_ENV_FLOOR")):
        with pytest.raises(ValueError, match="^mutation runs cannot provide sound evidence$"):
            gate.refuse_mutation(text)
    with pytest.raises(_Refused, match="^mix: mutation runs cannot provide sound evidence$"):
        gate.refuse_mutation(_transcript("INJECT_BUG_X"), refuse=_Refused, prefix="mix: ")
    assert gate.refuse_mutation(_transcript(), refuse=_Refused, prefix="mix: ") is None


@pytest.mark.parametrize("extra,offending", [
    ("verify_synth_top: FAIL -- 68 writes reached the register port, 67 were sent", "FAIL --"),
    ("verify_synth_top: REFUSED -- partial run", "REFUSED"),
    ("verify_synth_top: REFUSED: partial run", "REFUSED"),
    ("run_all: NO-VERDICT (timeout)", "NO-VERDICT"),
])
def test_a_pass_beside_a_forbidden_outcome_is_refused_naming_the_outcome(extra, offending):
    """#1376's shape: the bench states a defect with `FAIL --` and, the wire
    matching, still ends the run in `PASS --`."""
    with pytest.raises(ValueError, match="not a clean PASS") as caught:
        gate.validate_clean_pass(_transcript(extra))
    assert offending in str(caught.value)


def test_a_failing_run_is_told_what_it_contains_rather_than_what_it_lacks():
    with pytest.raises(ValueError, match="it contains FAIL --"):
        gate.validate_clean_pass("verify_synth_top: FAIL -- of 3 decoded I2S periods\n")


@pytest.mark.parametrize("text", [_transcript(pass_line=False), ""])
def test_a_transcript_that_never_said_pass_is_refused(text):
    with pytest.raises(ValueError, match="no PASS -- line"):
        gate.validate_clean_pass(text)


def test_the_wav_digest_binds_the_offered_wav_and_only_when_one_is_offered():
    with pytest.raises(ValueError, match="does not bind this decoded I2S WAV"):
        gate.validate_clean_pass(_transcript(), wav_sha256=OTHER_SHA)
    assert gate.validate_clean_pass(_transcript(), wav_sha256=SHA) == "verilator"
    assert gate.validate_clean_pass(_transcript(sha=None)) == "verilator"


@pytest.mark.parametrize("simulator", ["magic", None])
def test_an_unnamed_or_missing_simulator_is_refused(simulator):
    with pytest.raises(ValueError, match="omits the simulator backend"):
        gate.validate_clean_pass(_transcript(simulator=simulator))


def test_the_callers_refusal_class_and_prefix_are_used():
    with pytest.raises(_Refused, match="^mix: the report omits the simulator backend$"):
        gate.validate_clean_pass(_transcript(simulator=None), refuse=_Refused, prefix="mix: ")


# ---- the gate against what the repository has actually committed -----------

def _committed_single_run_transcripts() -> dict[str, str]:
    """Every tracked file under docs/ and fpga/reports that is ONE run of the
    bench, captured on its own.

    Three things disqualify a file, each read off its content. A JSON job
    report is not a transcript (the bench prints text). A `run_all` log is
    several jobs' heads and tails, so it can hold ONE simulator line beside
    three jobs' verdicts -- `f1-calibrated/run_all.log` does. And the bench
    prints its simulator line once per run, so a text file holding any count
    other than one is more than one run (`drum-d02a-i2s/controls-run_all.log`
    holds three)."""
    root = pathlib.Path(__file__).resolve().parents[1]
    listed = subprocess.run(["git", "grep", "-l", "-e", SIMULATOR_LINE, "--",
                             "docs", "fpga/reports"],
                            cwd=root, capture_output=True, text=True, check=True).stdout.split()
    texts = {rel: (root / rel).read_text(errors="replace") for rel in listed}
    return {rel: text for rel, text in texts.items()
            if text.count(SIMULATOR_LINE) == 1
            and not text.lstrip().startswith(("{", "["))
            and not RUN_ALL_SUMMARY.search(text)}


def test_the_gate_accepts_every_passing_run_the_repository_has_committed():
    """Real board output, not a fixture this file built. A passing run is
    recognised by the bench's success-only line -- `verified from SPI pins`,
    printed on its exit-0 path and nowhere else -- so this goes red if the gate
    is ever tightened past what a passing run actually prints."""
    single = _committed_single_run_transcripts()
    passing = {rel: text for rel, text in single.items()
               if SUCCESS_LINE in text and "INJECT_BUG_" not in text}
    assert len(passing) >= 30, sorted(passing)
    for rel, text in sorted(passing.items()):
        assert gate.validate_clean_pass(text) in gate.SIMULATORS, rel


def test_the_gate_refuses_every_committed_mutation_control_and_non_passing_run():
    single = _committed_single_run_transcripts()
    controls = {rel: text for rel, text in single.items()
                if "INJECT_BUG_" in text or "verify_synth_top: PASS --" not in text}
    assert len(controls) >= 6, sorted(controls)
    for rel, text in sorted(controls.items()):
        with pytest.raises(ValueError, match="mutation runs|not a clean PASS"):
            gate.validate_clean_pass(text)


def test_a_mutation_run_that_reached_pass_is_still_refused():
    """Real board output, and the reason `INJECT_BUG_` is asked first: the
    committed D02A controls log holds three injected runs, and
    `INJECT_BUG_DRUM_ENV_FLOOR` ended in `PASS --` with the SD drum path
    'verified from SPI pins'. Nothing but its compile define separates that
    transcript from evidence, so the precondition is asserted before the
    verdict is trusted."""
    path = (pathlib.Path(__file__).resolve().parents[1]
            / "docs/scorecard/drum-d02a-i2s/controls-run_all.log")
    text = path.read_text()
    assert "compile defines: INJECT_BUG_DRUM_ENV_FLOOR" in text
    assert "verify_synth_top: PASS --" in text
    with pytest.raises(ValueError, match="mutation runs cannot provide sound evidence"):
        gate.validate_clean_pass(text)
