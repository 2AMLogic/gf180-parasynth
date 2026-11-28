#!/usr/bin/env python3
"""tools/test_dsp_dpreg_sim_exit.py -- the simulator's exit contract, driven
through main() on the committed dsim evidence with the simulator itself
replaced.

THE DEFECT, AS MEASURED ON main @ 03148399 (2026-11-28, red-first run of
every control in this file): tools/dsp_dpreg_sim.py used the exit
contract's two statuses SWAPPED.  Its three no-verdict paths (the run
failed, a hash mismatched, a trace came back short) exited 1, which
tools/exit_contract.py reserves for a measurement that DISAGREED; and its
verdict path exited 2 -- REFUSED -- for BOTH halves of `red1 and red2 and
clean0`, so a clean run that FAILED under controls that had fired, a real
refutation of the disposition, was rendered NO-VERDICT by tools/run_all.py.
The module's own docstring already stated the contract ("each must turn the
harness red ... or the run REFUSES"; mode 0 "must report 0 X and 0
wrong-value cycles"); the statuses had never been made to match it.

    before                          after
    run failed                      1 -> REFUSED
    hash mismatch                   1 -> REFUSED
    trace incomplete                1 -> REFUSED   (printed NO VERDICT all along)
    a control did not fire          2    REFUSED   (unchanged: no evidence)
    clean run failed, controls red  2 -> FAIL      (a result, the dangerous half)

WHAT IS DRIVEN.  `main()` runs for real: the stimulus is rebuilt (a fixed
LCG seed), the feedback identity map is asserted against the committed dump,
the expected vectors are regenerated, the manifest is re-checked and the
classification runs.  Only `run_local` -- the iverilog/vvp shell loop -- is
replaced, by a function that edits the copied dsim directory the way a
broken run would.  Nothing here runs a simulator.

THE KNOWN ANSWER the harness is calibrated on: the committed evidence IS a
passing run (both controls red, the clean sequence clean), so an untouched
copy must reach exit 0.  If that control ever fails, every other control in
this file proves nothing and must not be read.

Exit statuses are read from tools/exit_contract.py, never retyped.
"""
import hashlib
import importlib.util
import pathlib
import shutil
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import exit_contract                                             # noqa: E402

_spec = importlib.util.spec_from_file_location("dsp_dpreg_sim", HERE / "dsp_dpreg_sim.py")
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)

REAL_DSIM = sim.DSIM
REAL_RUN = subprocess.run


def _manifest(d):
    """Fresh self-consistent manifest over every file in d (a run that
    finished writes one; a tamper that wants to get PAST the hash check
    must re-manifest, which is what a wrong-but-complete run looks like)."""
    lines = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.name != "MANIFEST.sha256":
            lines.append(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name)
    (d / "MANIFEST.sha256").write_text("\n".join(lines) + "\n")


@pytest.fixture
def dsim(tmp_path, monkeypatch):
    """A copy of the committed dsim directory that main() will run against,
    with `run_local` replaced.  Returns (module, setter for the fake run)."""
    d = tmp_path / "dsim"
    shutil.copytree(REAL_DSIM, d, ignore=shutil.ignore_patterns("*.vvp"))
    monkeypatch.setattr(sim, "DSIM", d)
    # main() asks the simulator for its version after the verdict; answered
    # from a stub so this file needs no iverilog on PATH (nothing here runs one)
    monkeypatch.setattr(subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(
                            cmd, 0, "Icarus Verilog version 12.0\n", "")
                        if list(cmd)[:1] == ["iverilog"] else REAL_RUN(cmd, **kw))

    def install(fake):
        monkeypatch.setattr(sim, "run_local", lambda: fake(d))
    return sim, install


def test_the_committed_evidence_is_a_passing_run(dsim, capsys):
    """The calibration: an untouched copy, with the simulator merely
    'succeeding', reaches exit 0.  Every other control depends on this."""
    sim, install = dsim
    install(lambda d: 0)
    assert sim.main() == exit_contract.PASS
    assert "clean sequence       : PASS" in capsys.readouterr().out


def test_a_failed_sim_run_is_refused(dsim, capsys):
    sim, install = dsim
    install(lambda d: 3)
    assert sim.main() == exit_contract.REFUSED
    assert "REFUSED: local sim run failed rc=3" in capsys.readouterr().out


def test_a_hash_mismatch_is_refused(dsim, capsys):
    """A file edited after the run wrote its manifest: the evidence is not
    what the simulator produced, so nothing below it is read."""
    sim, install = dsim

    def tamper(d):
        (d / "actual_mode0.txt").write_text((d / "actual_mode0.txt").read_text() + "x\n")
        return 0
    install(tamper)
    assert sim.main() == exit_contract.REFUSED
    assert "REFUSED: hash mismatch actual_mode0.txt" in capsys.readouterr().out


def test_an_incomplete_trace_is_refused(dsim, capsys):
    """The case tools/dsp_dpreg_refusal_cases.py also drives through a real
    child process: a clean trace cut to 100 of 148 cycles and re-manifested."""
    sim, install = dsim

    def truncate(d):
        lines = (d / "actual_mode0.txt").read_text().splitlines()[:100]
        (d / "actual_mode0.txt").write_text("\n".join(lines) + "\n")
        _manifest(d)
        return 0
    install(truncate)
    assert sim.main() == exit_contract.REFUSED
    out = capsys.readouterr().out
    assert "NO VERDICT: mode 0 trace incomplete: 100 actual" in out
    assert "REFUSED" not in out        # the label it prints is NO VERDICT; the status is the contract's


def test_a_clean_run_that_fails_under_fired_controls_is_a_result(dsim, capsys):
    """THE DANGEROUS HALF.  Both controls fire (the committed run's own mode 1
    and mode 2 traces) and the clean sequence disagrees with the golden: the
    extracted configuration is refuted.  That is evidence, not the absence
    of it, and it must reach a caller as FAIL."""
    sim, install = dsim

    def wrong_clean(d):
        # a complete, wrong mode 0 trace: mode 1's values where mode 0's belong
        other = (d / "actual_mode1.txt").read_text()
        (d / "actual_mode0.txt").write_text(other)
        _manifest(d)
        return 0
    install(wrong_clean)
    assert sim.main() == exit_contract.FAIL
    out = capsys.readouterr().out
    assert "pregflip control red : yes" in out and "opmode-p control red : yes" in out
    assert "clean sequence       : NO" in out


def test_a_control_that_did_not_fire_is_refused(dsim, capsys):
    """A harness that cannot see the injected defect has shown nothing about
    the clean configuration: no evidence, by the module's own docstring."""
    sim, install = dsim

    def blind_control(d):
        # mode 1 reads exactly as its golden: the injected PREG flip went undetected
        rows = [(d / "expected_mode1.txt").read_text().splitlines()]
        (d / "actual_mode1.txt").write_text("\n".join(r + " 0" for r in rows[0]) + "\n")
        _manifest(d)
        return 0
    install(blind_control)
    assert sim.main() == exit_contract.REFUSED
    out = capsys.readouterr().out
    assert "pregflip control red : NO" in out


def test_the_module_declares_its_contract_from_the_contract():
    """No status is retyped in the module: the four outcomes are the
    contract's names."""
    src = (HERE / "dsp_dpreg_sim.py").read_text()
    assert "import exit_contract" in src
    assert "return exit_contract.REFUSED" in src and "exit_contract.FAIL" in src
