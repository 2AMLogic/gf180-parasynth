"""Baseline gate for the two unwired mutation records (#1130).

Each script is copied with its mutation target into an isolated tree whose
suite is a one-test fixture, so the scripts never mutate the live tree.
Rule-8 input: `def test_x(): assert 0` must give exit 2, not KILLED/RED.
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# (script, mutation target, suite path, passing-suite exit code, verdict marker)
CASES = {
    "fpga": ("fpga/testdata/mutate_pnr_report.py", "fpga/scripts/pnr_report.py",
             "fpga/test_pnr_report.py", "KILLED"),
    "sound": ("model/probe_injection_discrimination.py", "model/sound_report.py",
              "model/test_sound_report.py", "RED (good)"),
}


def stage(tmp, key, suite_src):
    script, target, suite, _ = CASES[key]
    for rel in (script, target):
        d = tmp / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / rel, d)
    (tmp / suite).write_text(suite_src)
    return tmp / script, tmp / target


def run(script, tmp):
    return subprocess.run([sys.executable, str(script)], cwd=tmp,
                          capture_output=True, text=True)


@pytest.mark.parametrize("key", CASES)
def test_red_baseline_refuses(tmp_path, key):
    script, target = stage(tmp_path, key, "def test_x():\n    assert 0\n")
    before = target.read_bytes()
    r = run(script, tmp_path)
    out = r.stdout + r.stderr
    assert r.returncode == 2, out
    assert "REFUSED" in out and "baseline" in out
    assert CASES[key][3] not in out and "survived" not in out.lower()
    assert target.read_bytes() == before


@pytest.mark.parametrize("key", CASES)
def test_green_baseline_reaches_mutants(tmp_path, key):
    script, target = stage(tmp_path, key, "def test_x():\n    assert 1\n")
    before = target.read_bytes()
    r = run(script, tmp_path)
    out = r.stdout + r.stderr
    # Insensitive suite: every mutant survives -> exit 1, verdicts printed.
    assert r.returncode == 1, out
    assert "baseline" not in out and "REFUSED:" not in out and "probe REFUSED" not in out
    assert "SURVIVED" in out
    assert target.read_bytes() == before
