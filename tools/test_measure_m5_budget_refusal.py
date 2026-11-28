#!/usr/bin/env python3
"""Exit-status controls for the `--drives` budget fence of the two M5
operating-level tools (#1694): a drive outside the frozen budget must leave the
process as REFUSED (2), never as a FAIL (1).

THE DEFECT, AS MEASURED ON origin/main @ be426416 (2026-11-28): the fence
raised a bare `SystemExit` whose string began `REFUSED: ...` in both tools, so
`--drives 0.9` exited 1 with the refusal text on stderr and nothing on stdout
-- a missing precondition rendered as a FAIL, the inversion
tools/exit_contract.py exists to prevent -- while a handler that renders it
correctly (`except refusal.Refused` in `main()`) sat one frame above the
fence, bypassed. Red-first on that tree, 2026-11-28: 6 of the 12 controls
failed -- the four CLI controls with the process at exit 1 and the refusal on
stderr, the two class controls with no `Refused` in either module -- and the
six in-budget controls were green, as they must be on any tree.

THE KNOWN ANSWER, independent of anything this repository models: 0.9 is not
the baseline 0.75 and not one of the three frozen candidates, so the request
cannot be served and nothing may be measured. The frozen budget is restated
here as literals -- base 0.75; candidates 0.5 / 0.35 / 0.25 (operating level)
and 0.5 / 0.35 / 0.3 (saw drive) -- so a control never reads the budget from
the module it is checking.

WHAT THE TOOLS OWE, and what these controls assert:

  out of budget   exit `exit_contract.REFUSED` (2), exactly one line on
                  stdout, `REFUSED: <reason>`, the reason pinned per tool; no
                  REFUSED on stderr, no traceback, no --out file
  whole request   a list holding the baseline, a candidate AND one bad value
                  is refused before any drive is rendered: no per-drive row
                  is printed, so a caller splitting a sweep across jobs never
                  gets a partial report from a refused request
  in budget       the baseline alone, the candidates alone and the default
                  list all pass the fence, in the order given, and reach the
                  per-drive loop: the calibration is called for exactly the
                  candidates. The render is replaced by recorders, so the
                  control runs the real `main()` without measuring anything.
                  It is green on the pre-fix tree and must stay green.

The refusal controls run the real file in a real interpreter, which is the
process a batch runner sees; the in-budget controls run the real `main()` in
this process.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract                                     # noqa: E402
import refusal                                           # noqa: E402

BAD_DRIVE = "0.9"
#: per tool: the frozen budget as literals, the fence's exact line, the prefix
#: of a per-drive row on stdout, and the render functions a stub replaces
BUDGET = {
    "measure_m5_operating_level": {
        "base": 0.75, "candidates": (0.5, 0.35, 0.25),
        "reason": "REFUSED: [0.9] outside the frozen three-candidate budget",
        "row_prefix": "drive ", "renders": ("phrase", "probe")},
    "measure_m5_saw_drive": {
        "base": 0.75, "candidates": (0.5, 0.35, 0.3),
        "reason": "REFUSED: outside the frozen three-candidate budget",
        "row_prefix": "saw drive ", "renders": ("phrase", "probe", "r1_binding")},
}
MODULES = sorted(BUDGET)
RENDER_STUBS = {
    "phrase": lambda *a: {"errors": {}, "events": []},
    "probe": lambda *a: {},
    "r1_binding": lambda *a: {"candidate_changes_r1_bytes": False},
}


def _cli(module: str, *argv: str) -> subprocess.CompletedProcess:
    """The real file in a real interpreter: the process a batch runner sees."""
    return subprocess.run([sys.executable, str(ROOT / "tools" / f"{module}.py"), *argv],
                          capture_output=True, text=True, timeout=600, cwd=str(ROOT))


def _refused_lines(stdout: str) -> list[str]:
    return [line for line in stdout.splitlines() if line.startswith("REFUSED")]


def _assert_refused(p: subprocess.CompletedProcess, reason: str, out: pathlib.Path) -> None:
    assert p.returncode == exit_contract.REFUSED, (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert _refused_lines(p.stdout) == [reason], p.stdout[-400:]
    assert "REFUSED" not in p.stderr and "Traceback" not in p.stderr, p.stderr[-400:]
    assert not out.exists(), "a refused run must leave no --out report behind"


def _drives(monkeypatch, tmp_path, module: str, argv: list[str]) -> tuple[list[str], list[float]]:
    """Run the real `main()` with every render replaced by a stub; return the
    rows the report holds (the drives let through to the per-drive loop) and
    the drives the calibration was called for."""
    tool = importlib.import_module(module)
    calibrated = []

    def calibrate(engine, drive):
        calibrated.append(drive)
        return 1.0

    monkeypatch.setattr(importlib.import_module("measure_m5_operating_level"), "calibrate", calibrate)
    for name in BUDGET[module]["renders"]:
        monkeypatch.setattr(tool, name, RENDER_STUBS[name])
    out = tmp_path / "out.json"
    assert tool.main([*argv, "--out", str(out)]) == exit_contract.PASS, module
    return list(json.loads(out.read_text())["rows"]), calibrated


# ---------------------------------------------------------------------------
# the class relation the whole fix rests on
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("module", MODULES)
def test_fence_raises_the_module_refusal_the_boundary_catches(module, tmp_path):
    tool = importlib.import_module(module)

    assert tool.Refused is not refusal.Refused
    assert issubclass(tool.Refused, refusal.Refused)
    with pytest.raises(tool.Refused, match="outside the frozen three-candidate budget"):
        tool._run(["--drives", BAD_DRIVE, "--out", str(tmp_path / "out.json")])


# ---------------------------------------------------------------------------
# out of budget: REFUSED by name on stdout, exit 2, no report
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["bad-alone", "baseline-candidate-and-bad"])
@pytest.mark.parametrize("module", MODULES)
def test_out_of_budget_drive_exits_refused_2(tmp_path, module, shape):
    budget = BUDGET[module]
    drives = [BAD_DRIVE] if shape == "bad-alone" else [
        str(budget["base"]), str(budget["candidates"][0]), BAD_DRIVE]
    out = tmp_path / "out.json"
    p = _cli(module, "--drives", *drives, "--out", str(out))
    _assert_refused(p, budget["reason"], out)
    assert not any(line.startswith(budget["row_prefix"]) for line in p.stdout.splitlines()), (
        "a request holding a bad drive is refused whole, before any drive is rendered",
        p.stdout[-400:])


# ---------------------------------------------------------------------------
# in budget: the fence admits exactly what it admitted before
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("shape", ["baseline", "candidates", "default"])
@pytest.mark.parametrize("module", MODULES)
def test_in_budget_request_passes_the_fence_and_reaches_the_loop(monkeypatch, tmp_path, module,
                                                                   shape):
    budget = BUDGET[module]
    drives = {"baseline": [budget["base"]],
              "candidates": list(budget["candidates"]),
              "default": [budget["base"], *budget["candidates"]]}[shape]
    argv = [] if shape == "default" else ["--drives", *(str(d) for d in drives)]
    rows, calibrated = _drives(monkeypatch, tmp_path, module, argv)
    assert rows == [str(d) for d in drives]
    assert calibrated == [d for d in drives if d != budget["base"]]
