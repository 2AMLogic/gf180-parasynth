#!/usr/bin/env python3
"""`verify_mono_case` reads the M5A SPI-to-I2S transcript through the one gate.

START RED (#1595). The tool asked one substring -- the whole `M5A path verified
...` line -- and nothing else, so an injected control run, a run that also said
`FAIL --`, a transcript that never said `PASS --` and a transcript from an
unnamed simulator all passed as the end-to-end check, and the decoded-WAV digest
on the record was never read. The gate is `tools/i2s_transcript.validate_clean_pass`
(#1556).

Each test runs `main()` over the committed M5A record
(`docs/scorecard/results/M5A.json`) and its committed transcript, through the
real `scorecard.evaluate`, and replaces only the transcript and the re-run: the
verdict is real board output (the record FAILs, which is a result), so the
transcript is the only thing left that can decide the exit status.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import verify_mono_case as vm                                      # noqa: E402

COMMITTED = HERE.parent / "docs/scorecard/results/M5A.json"
VERIFIED = "M5A path verified from SPI pins through the production voice and I2S pins"


def _committed_transcript() -> str:
    record = json.loads(COMMITTED.read_text())
    return (HERE.parent / record["provenance"]["artefacts"]["spi_i2s"]).read_text()


def _without(prefix: str) -> str:
    return "".join(line for line in _committed_transcript().splitlines(keepends=True)
                   if not line.startswith(prefix))


def _verify(monkeypatch, tmp_path, text: str, *, decoded="keep", returncode=0) -> int:
    """`main()` on a scratch ROOT with `text` as the M5A transcript.

    The re-run of the case is faked to `returncode`. The record that re-run
    would have written is the committed one, re-bound to `text` and, unless
    `decoded` is `"keep"`, to the `decoded:I2S` digest given (or none)."""
    record = json.loads(COMMITTED.read_text())
    (tmp_path / "verification.txt").write_bytes(text.encode())
    record["provenance"]["artefacts"]["spi_i2s"] = "verification.txt"
    record["diagnostics"]["spi_i2s_report_sha256"] = hashlib.sha256(text.encode()).hexdigest()
    if decoded != "keep":
        if decoded is None:
            record["provenance"]["inputs"].pop("decoded:I2S")
        else:
            record["provenance"]["inputs"]["decoded:I2S"] = decoded
    out = tmp_path / "build/scorecard/m5a-verification-results"
    out.mkdir(parents=True)
    (out / "M5A.json").write_text(json.dumps(record))
    rerun = subprocess.CompletedProcess([], returncode, "", "")
    monkeypatch.setattr(vm, "ROOT", tmp_path)
    monkeypatch.setattr(vm.subprocess, "run", lambda *args, **kwargs: rerun)
    return vm.main()


def test_the_committed_m5a_transcript_clears_the_gate(tmp_path, monkeypatch, capsys):
    """The precondition of every refusal below, on real board output: the record
    the repository committed, with the transcript it names, is a valid
    measurement. Were this refused, each red below would be red for a reason of
    its own."""
    assert _verify(monkeypatch, tmp_path, _committed_transcript()) == 0
    assert "M5A sound measurement is valid (" in capsys.readouterr().out


@pytest.mark.parametrize("text,why", [
    pytest.param(_committed_transcript().replace(
        "compile defines: VOICE_OSC_2X, VOICE_FILTER_2X",
        "compile defines: VOICE_OSC_2X, INJECT_BUG_SPI_ADDR7"),
        "mutation runs", id="injected-control"),
    pytest.param(_committed_transcript().replace(
        "verify_synth_top: PASS --",
        "verify_synth_top: FAIL -- 68 writes reached the register port, 67 were sent\n"
        "verify_synth_top: PASS --", 1),
        "it contains FAIL --", id="pass-beside-fail"),
    pytest.param(_committed_transcript() + "verify_synth_top: REFUSED -- partial run\n",
                 "it contains REFUSED", id="pass-beside-refused"),
    pytest.param(_committed_transcript() + "run_all: NO-VERDICT (timeout)\n",
                 "it contains NO-VERDICT", id="pass-beside-no-verdict"),
    pytest.param(_without("verify_synth_top: PASS --"), "no PASS -- line", id="no-pass-line"),
    pytest.param(_without("verify_synth_top: simulator backend"),
                 "omits the simulator backend", id="no-simulator"),
])
def test_a_transcript_that_is_not_one_clean_m5a_pass_is_no_evidence(
        tmp_path, monkeypatch, capsys, text, why):
    """START RED. Each of these carries the whole `M5A path verified ...` line,
    so each left `main()` at 0: "it says PASS" has never meant "it says only
    PASS" (#1376), a control run is a control whatever verdict it reached, and a
    transcript that never passed or names no simulator is not a pass."""
    assert text != _committed_transcript()
    assert _verify(monkeypatch, tmp_path, text) == 2
    assert why in capsys.readouterr().err


def test_the_transcript_must_bind_the_decoded_wav_the_record_names(tmp_path, monkeypatch, capsys):
    """START RED. `decoded:I2S` was on the record and never read, so a transcript
    from a run that decoded some other WAV passed as this record's."""
    record = json.loads(COMMITTED.read_text())
    assert record["provenance"]["inputs"]["decoded:I2S"].removeprefix("sha256:") in _committed_transcript()
    assert _verify(monkeypatch, tmp_path, _committed_transcript(),
                   decoded="sha256:" + "0" * 64) == 2
    assert "does not bind" in capsys.readouterr().err


def test_a_record_without_a_decoded_wav_digest_is_still_accepted(tmp_path, monkeypatch):
    """`run_case` writes no `decoded:I2S` into its own M5A record, and this tool
    re-runs `run_case` (`make verify`): the record it judges on a real run has
    no digest to offer, so the gate binds the WAV only when the record names
    one. Requiring it would refuse every real run -- a gate nothing clears."""
    assert _verify(monkeypatch, tmp_path, _committed_transcript(), decoded=None) == 0


def test_a_verified_line_that_is_not_the_whole_phrase_is_refused(tmp_path, monkeypatch, capsys):
    """The one refusal the substring check did make, kept through the
    consolidation: the bench prints the whole phrase on its exit-0 path, and
    three words of it are also the first three of a line about some other path."""
    text = _committed_transcript().replace(
        VERIFIED, "M5A path verified from the register write port to the I2S pins")
    assert VERIFIED not in text and "M5A path verified" in text
    assert _verify(monkeypatch, tmp_path, text) == 2
    assert "end-to-end pass" in capsys.readouterr().err


def test_a_record_without_provenance_is_refused_by_the_scorer_before_the_reader_reads_it(
        tmp_path, monkeypatch, capsys):
    """Class record for #1622, not a repair: `main()` reads `record["provenance"]`
    with [] twice, and neither read is reachable with the key absent --
    `scorecard.evaluate` answers `no verdict` first and main() leaves at its
    "not scoreable" branch (exit 2). Pinned so a scorer that stops requiring
    provenance turns this red instead of arming a KeyError."""
    record = json.loads(COMMITTED.read_text())
    del record["provenance"]
    out = tmp_path / "build/scorecard/m5a-verification-results"
    out.mkdir(parents=True)
    (out / "M5A.json").write_text(json.dumps(record))
    rerun = subprocess.CompletedProcess([], 0, "", "")
    monkeypatch.setattr(vm, "ROOT", tmp_path)
    monkeypatch.setattr(vm.subprocess, "run", lambda *args, **kwargs: rerun)
    assert vm.main() == 2
    err = capsys.readouterr().err
    assert "not scoreable" in err and "provenance" in err
