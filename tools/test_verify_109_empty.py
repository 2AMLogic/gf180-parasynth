"""#1454's control, as a regression test on verify_109_claims.py's verdict.

`main()` reads its verdict off the state the two measurement steps leave
behind: `fails`, `not_run`, and the `no_verdict` string half 2 returns. With
both steps recording nothing, that state is empty, and the pre-fix `main()`
had no branch for it: it fell through to `0 of 0 checks passed.` and
returned 0. That is #1451's shape -- an empty record reads as a pass because
nothing in it contradicts one -- and `tools/run_all.py` reports exit 0 as
PASS without scraping output, so the verifier's own status is all that
stands between an empty run and a green one.

Measured on `origin/main` at 267955a3 (the start-red run): with
`measure_tone_ratio_sensitivity` and `measure_reference_signal_length`
replaced by no-ops, `main()` printed `0 of 0 checks passed.` and returned 0.

The fixed `main()` must answer exit 2 (NO VERDICT) naming that no check was
executed, and must answer so ONLY for that state. The other three rows pin
what the empty-run branch must not take over. Each is produced through the
probe's own `check` / `note_not_run`, so the state is recorded the way the
shipped steps record it:

- one check that FAILS and nothing else keeps exit 1. `n_ok` is 0 on this
  row too, so a guard written on `n_ok == 0` instead of `n_checks == 0`
  would call this measured mismatch an empty run;
- one check NOT RUN and nothing else keeps the optional-corpus exit 0, with
  the skip counted;
- a required-corpus refusal -- half 2 records its NOT RUN and returns the
  refusals, as `_refuse_voice` does -- keeps exit 2 with its own count. The
  exit code alone cannot tell that branch from the empty-run one; the
  counted line must.

Every row loads the probe fresh from its file, with the probe setting up
its own import path as it loads, so the tally starts at zero by
construction: nothing is inherited from another test's in-process import of
the same module, and no hand-kept reset list can go stale when a tally
global is added.

Inputs that would satisfy a row while violating its intent
(docs/verification-rules.md rule 8), stated rather than left implicit:

- A step patch that stops taking effect (a refactor that binds the steps
  before `main()` looks them up) runs the REAL steps. Those record checks,
  so the empty-run row goes red on its exit code; it cannot go green on a
  run that measured something.
- A tally carried over from an earlier `main()` call in the same process
  would let an empty run report the earlier one's verdict. It has no
  shipped caller -- the entry point calls `main()` once per process -- and
  it cannot reach these rows, which never reuse a loaded module.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
PROBE = HERE / "probes" / "verify_109_claims.py"

EMPTY_RUN = "NO CHECK EXECUTED"


@pytest.fixture
def probe():
    """The probe loaded fresh from its file: its tally (`n_checks`, `n_ok`,
    `fails`, `not_run`) is the module's own definition, before anything has
    recorded into it."""
    name = "verify_109_claims_under_test"
    spec = importlib.util.spec_from_file_location(name, PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _verdict(probe, monkeypatch, capsys, half1=lambda: None,
             half2=lambda: None):
    """`main()`'s exit code and output with the two measurement steps
    replaced: by no-ops, or by a step that records through the probe's own
    `check` / `note_not_run` and returns what the real half returns."""
    monkeypatch.setattr(probe, "measure_tone_ratio_sensitivity", half1)
    monkeypatch.setattr(probe, "measure_reference_signal_length", half2)
    code = probe.main()
    return code, capsys.readouterr().out


def _shows(out: str, text: str) -> str:
    """An assertion message that carries the run's output."""
    return f"{text}:\n{out}"


def _verdict_line(out: str) -> str:
    """The one counted line (`N of T checks passed`): what a reader, or a
    grep for the count, sees of the run."""
    lines = [line for line in out.splitlines() if "checks passed" in line]
    assert len(lines) == 1, f"expected exactly one verdict line, got {lines}"
    return lines[0]


def test_no_check_executed_exits_2_naming_it(probe, monkeypatch, capsys):
    """The #1454 control: both measurement steps are no-ops, so nothing was
    recorded. Exit 2 (NO VERDICT), and the counted line itself says no check
    was executed. Red on the pre-fix `main()`, which printed
    `0 of 0 checks passed.` and returned 0."""
    code, out = _verdict(probe, monkeypatch, capsys)
    assert code == 2, _shows(
        out, f"an empty run returned {code}, expected 2 (NO VERDICT)")
    line = _verdict_line(out)
    assert "0 of 0 checks passed" in line, _shows(
        out, "the verdict line must still count")
    assert EMPTY_RUN in line, _shows(
        out, "the counted line does not say that no check ran")
    assert "NO VERDICT" in line, _shows(
        out, "exit 2 is NO VERDICT; the counted line must say so")


def test_one_failing_check_alone_keeps_exit_1(probe, monkeypatch, capsys):
    """A FAIL and nothing else: `n_ok` is 0 here too, so a guard written on
    `n_ok == 0` instead of `n_checks == 0` would report this measured
    mismatch as an empty run. It stays exit 1."""
    def fail_one():
        probe.check(False, "injected: the only check fails")

    code, out = _verdict(probe, monkeypatch, capsys, half1=fail_one)
    assert code == 1, _shows(out, f"exit {code}")
    line = _verdict_line(out)
    assert "0 of 1 checks passed; 1 FAILED:" in line, out
    assert EMPTY_RUN not in out, _shows(
        out, "a measured failure was reported as an empty run")


def test_one_not_run_alone_keeps_optional_exit_0(probe, monkeypatch, capsys):
    """One NOT RUN and nothing else (`n_checks == 1`): the optional-corpus
    outcome the probe's docstring records as deliberate is kept -- exit 0
    with the skip counted, never relabelled an empty run."""
    def not_run_one():
        probe.note_not_run("injected: the only check did not run",
                           "precondition")

    code, out = _verdict(probe, monkeypatch, capsys, half1=not_run_one)
    assert code == 0, _shows(out, f"exit {code}")
    line = _verdict_line(out)
    assert "0 of 1 checks passed; 1 NOT RUN" in line, out
    assert EMPTY_RUN not in out, _shows(
        out, "a counted skip was reported as an empty run")


def test_required_refusal_is_not_the_empty_run(probe, monkeypatch, capsys):
    """Half 2 under the required-corpus gate, by its real contract: the
    refused voice is recorded NOT RUN and the half returns its refusals.
    Exit 2 either way, so the exit code alone cannot tell the empty-run
    branch from this one; the counted line must."""
    def refuse_half():
        probe.note_not_run("injected: reference refused", "recording missing")
        return "injected: reference refused"

    code, out = _verdict(probe, monkeypatch, capsys, half2=refuse_half)
    assert code == 2, _shows(out, f"exit {code}")
    line = _verdict_line(out)
    assert "0 of 1 checks passed; 1 NOT RUN -- NO VERDICT" in line, out
    assert EMPTY_RUN not in out, _shows(
        out, "a required refusal was reported as an empty run")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
