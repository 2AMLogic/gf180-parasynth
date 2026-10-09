"""Tests for the #608 guard.

Each injected defect must REFUSE with its intended reason code, and a refusal
for any other reason (missing tool, unrelated compile failure) must be a
NO_VERDICT / nonzero, never a caught control (verification rule 5).
"""
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_iverilog_generate_case_string as g  # noqa: E402

needs_iv = pytest.mark.skipif(not (shutil.which("iverilog") and shutil.which("vvp")),
                              reason="iverilog or vvp absent")
MUTANT_CONTROLS = ["BROKEN_REPRO", "GARBAGE"]


def test_no_iverilog_refuses_with_no_tool():
    state, reason, _ = g.classify("iverilog-does-not-exist", g.MICRO)
    assert (state, reason) == ("REFUSED", g.NO_TOOL)


def test_no_vvp_refuses_with_no_tool():
    state, reason, _ = g.classify("iverilog", g.MICRO, vvp="vvp-does-not-exist")
    assert (state, reason) == ("REFUSED", g.NO_TOOL)


@needs_iv
def test_pinned_version_known_defect_or_fixed():
    state, reason, detail = g.classify("iverilog", g.MICRO)
    assert state in ("KNOWN_DEFECT", "FIXED"), detail
    assert reason == ""


@needs_iv
@pytest.mark.parametrize("inj", MUTANT_CONTROLS)
def test_mutant_refuses_with_intended_reason(inj, tmp_path):
    src = tmp_path / "m.v"
    src.write_text(g.MUTANTS[inj](g.MICRO.read_text()))
    state, reason, detail = g.classify("iverilog", src)
    assert (state, reason) == ("REFUSED", g.CONTROL_MAP[inj]), detail


@needs_iv
@pytest.mark.parametrize("inj", MUTANT_CONTROLS)
def test_injected_controls_caught(inj):
    assert g.main(["--inject", inj, "--expect", "refused"]) == 0


def test_inject_no_iverilog_cli():
    assert g.main(["--inject", "NO_IVERILOG", "--expect", "refused"]) == 0
    assert g.main(["--inject", "NO_IVERILOG"]) == 2


# --- defeating inputs: a refusal for the wrong reason must not count ---------

@pytest.mark.parametrize("inj", MUTANT_CONTROLS)
@pytest.mark.parametrize("tool", ["--iverilog", "--vvp"])
def test_missing_tool_is_no_verdict_not_caught(inj, tool, capsys):
    rc = g.main(["--inject", inj, "--expect", "refused", tool, "absent-tool-xyz"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "NO_VERDICT" in out and "CAUGHT" not in out


@needs_iv
@pytest.mark.parametrize("inj", MUTANT_CONTROLS)
def test_unrelated_compile_failure_is_not_caught(inj, monkeypatch, capsys):
    # The mutant breaks the syntax instead of exercising the intended defect.
    monkeypatch.setitem(g.MUTANTS, inj, lambda text: text + "\nthis is not verilog;\n")
    rc = g.main(["--inject", inj, "--expect", "refused"])
    assert rc != 0
    out = capsys.readouterr().out
    assert f"REFUSED/{g.COMPILE_FAILED}" in out and "CAUGHT" not in out


@needs_iv
def test_mutant_that_changes_nothing_is_not_caught(monkeypatch):
    monkeypatch.setitem(g.MUTANTS, "BROKEN_REPRO", lambda text: text)
    assert g.main(["--inject", "BROKEN_REPRO", "--expect", "refused"]) == 1


def test_expect_refused_requires_inject():
    with pytest.raises(SystemExit):
        g.main(["--expect", "refused"])
