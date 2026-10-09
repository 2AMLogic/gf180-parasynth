"""Tests for the #608 guard. Each injected defect must REFUSE, not answer."""
import pathlib
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_iverilog_generate_case_string as g  # noqa: E402

needs_iv = pytest.mark.skipif(not shutil.which("iverilog"), reason="iverilog absent")


def test_no_iverilog_refuses():
    assert g.classify("iverilog-does-not-exist", g.MICRO)[0] == "REFUSED"


@needs_iv
def test_pinned_version_known_defect_or_fixed():
    state, detail = g.classify("iverilog", g.MICRO)
    assert state in ("KNOWN_DEFECT", "FIXED"), detail


@needs_iv
@pytest.mark.parametrize("inj", ["BROKEN_REPRO", "GARBAGE"])
def test_injected_defects_refuse(inj):
    assert g.main(["--inject", inj, "--expect", "refused"]) == 0


def test_inject_no_iverilog_cli():
    assert g.main(["--inject", "NO_IVERILOG", "--expect", "refused"]) == 0
    assert g.main(["--inject", "NO_IVERILOG"]) == 2
