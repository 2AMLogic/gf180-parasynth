"""hh_probe3's four `.require()` sites must REFUSE at the CLI (exit 2, named),
never leave as an exit-1 InsufficientEvidence traceback (#1914; the #1751
class, one row of the #1782 census).

THE DEFECT, AS MEASURED ON origin/main @ 560a1580 (2026-11-28; Python 3.12.3,
numpy 2.5.5, scipy 1.18.1; red first). `tools/probes/hihat/hh_probe3.py` has
no `Refused` and no handler. Its four sites -- `schroeder_t20(x0).require("CY
T20 shipped")`, the same on the retuned render, `dominant_frequency(x1, 2-5
kHz).require("CY low line")` and `line_stability(x1).require("CY line
stability")` -- raise `audio_measure.InsufficientEvidence`, an
AssertionError subclass and NOT a `refusal.Refused`. Uncaught, that is exit 1
with a traceback: a refusal rendered as FAIL, the inversion
tools/exit_contract.py exists to prevent.

IT IS LIVE, NOT LATENT. With no injection at all -- the shipped command, the
real `hh_probe.render`, the tree's own constants -- the probe refused at its
FIRST site and exited 1 after 20.5 s:

    CY T20 shipped: insufficient evidence -- the record ends before the decay
    does: only 1087.4 ms follow the -25 dB point and a T20 of 898.9 ms needs
    1797.8 ms after it    (the retuned kit, x1: 880.8 ms after, T20 1031.0 ms
                            needs 2061.9 ms)

A 2.0 s render cannot hold MIN_TAIL_T20 (2.0) x T20 after the -25 dB point of
a ~0.9-1.0 s decay. The guard is right; this is the decay-length guard of
docs/verification-rules.md rule 8's own worked example (#139). The low line
(3658.9 Hz on both) and stability (0.955 / 0.952) answer on both real
signals, and the probe never reaches them. That is #1965's to repair; this
file pins only how the refusal EXITS. One 2.0 s render costs about 9 s on one
core (69 us/frame: `DrumsFx.play` is a per-frame Python loop), so the census
row's "nothing has been run" and the curation's "heavy, build box only" were
both inferences. Reproduce the natural refusal (about 10 s):

    .venv/bin/python -c "import sys; sys.path[:0]=['tools/probes/hihat','model',\
'audition']; import hh_probe3 as P, audio_measure as am; from dsp import SR; \
import drums_fx as dx; print(am.schroeder_t20(P.render(dx.kit_with_sounds('CY'), 2.0)[1], SR))"

THE KNOWN ANSWERS, independent of anything this repository models. The
fixtures are sums of exponentially decaying sinusoids, A e^(-t/tau): the level
falls 20 dB in tau ln(10) -- 461 ms at tau = 0.2 s, closed form, and
`schroeder_t20` reads 461 ms on it. A single 6.5 kHz line holds no 2-5 kHz
line. A single line is fewer than `line_stability`'s min_lines=5. A 0.30 s
cut of a 461 ms decay ends before its tail does. Digital silence is silent.
Each fixture therefore REFUSES at exactly one site and answers at every site
before it, so all four are shown reachable and the one handler is shown to
cover each.

WHAT THE PROBE OWES, and what these controls assert:

  refusal               exit `exit_contract.REFUSED` (2); the last line of
                        stdout is `REFUSED  <site>: insufficient evidence --
                        <reason>`; no traceback on either stream; no result
                        file written
  plain AssertionError  still exit 1 with a traceback. InsufficientEvidence IS
                        an AssertionError, so `except AssertionError` is the
                        tempting wrong handler: this is the input that defeats
                        it (rule 8)
  ValueError            still exit 1: an execution error is never a refusal.
                        Defeats `except Exception`
  good render           runs to its end, exit 0, hh_part3.json written, and the
                        T20 it recorded is the closed form

HOW THE ENTRY POINT IS DRIVEN. The handler lives in the `__main__` block, so
calling `hh_probe3.main()` would never execute it. Every control runs the
shipped file unmodified, in a fresh interpreter, through
`runpy.run_path(..., run_name="__main__")` and reads what the OS read. The one
substitution is the render's OUTPUT: hh_probe3 binds `render` with `from
hh_probe import render`, so patching the already-imported `hh_probe` module
before the file executes is the fixture -- what a silent corpus WAV is to
tools/test_rs_guard_band_refusal.py.

THE SCRATCH PATH. The probe's last statement writes hh_part3.json into a
hardcoded macOS scratchpad directory (`hh_probe3.py:10-11`) that exists on no
other host, so a run whose every estimator answers still dies there with
FileNotFoundError. The child redirects that one path to tmp_path and touches
nothing else about `os.path`. The constant itself is #1965's, out of #1914's
scope.

WRONG-THEN-RIGHT (1, caught by running the estimator, not by reading): the
first "good" fixture used tau = 0.3 s, from a -25 dB point mis-derived as
tau ln(10). The estimator refused it for truncation -- 1136.6 ms after the
-25 dB point, where a closed-form 691 ms T20 needs 1382 ms -- and it was
right: the -25 dB point sits at tau ln(17.8). Fixed at tau = 0.2 s.
"""

from __future__ import annotations

import json
import math
import pathlib
import subprocess
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import exit_contract  # noqa: E402

TAU = 0.20
GOOD = {
    "kind": "tone",
    "tau": TAU,
    "hz": (3150.0, 2600.0, 5200.0, 6100.0, 7300.0, 8200.0, 8800.0),
    "amp": (1.0, 0.2, 0.3, 0.3, 0.3, 0.3, 0.3),
}
SILENT = {"kind": "zeros"}
ONE_LINE_OFF_BAND = {"kind": "tone", "tau": TAU, "hz": (6500.0,), "amp": (1.0,)}
ONE_LINE_IN_BAND = {"kind": "tone", "tau": TAU, "hz": (3150.0,), "amp": (1.0,)}
CUT_SHORT = dict(GOOD, cut=0.30)

_CHILD = r"""
import json, os, runpy, sys
spec = json.loads(sys.argv[1])
for rel in ("tools/probes/hihat", "model", "audition"):
    sys.path.insert(0, os.path.join(spec["root"], rel))
import numpy as np
import hh_probe
import hh_probe3 as _probe
from dsp import SR
from hh_probe import PRE

real_join = os.path.join
def join(first, *rest):
    if first == _probe.SCRATCH:
        return real_join(spec["out"], *rest)
    return real_join(first, *rest)
os.path.join = join

def signal(recipe, seconds):
    pre = int(PRE * SR)
    n = pre + int(seconds * SR)
    if recipe["kind"] == "zeros":
        return np.zeros(n)
    t = np.arange(n - pre) / SR
    x = sum(a * np.sin(2 * np.pi * f * t) for f, a in zip(recipe["hz"], recipe["amp"]))
    return np.concatenate([np.zeros(pre), 16000.0 * x * np.exp(-t / recipe["tau"])])

calls, seen = spec["calls"], [0]
def fake(kit, seconds, *a, **k):
    recipe = calls[min(seen[0], len(calls) - 1)]
    seen[0] += 1
    if recipe["kind"] == "raise":
        raise {"AssertionError": AssertionError, "ValueError": ValueError}[recipe["exc"]](recipe["msg"])
    x = signal(recipe, seconds)
    if "cut" in recipe:
        x = x[:int(PRE * SR) + int(recipe["cut"] * SR)]
    return None, x
hh_probe.render = fake
runpy.run_path(os.path.join(spec["root"], "tools/probes/hihat/hh_probe3.py"), run_name="__main__")
"""


def _run(tmp_path: pathlib.Path, *calls: dict) -> subprocess.CompletedProcess:
    spec = {"root": str(ROOT), "out": str(tmp_path), "calls": list(calls)}
    return subprocess.run(
        [sys.executable, "-c", _CHILD, json.dumps(spec)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(tmp_path),
        check=False,
    )


def _last_line(text: str) -> str:
    return text.strip().splitlines()[-1] if text.strip() else ""


@pytest.mark.parametrize(
    "calls, site, reason",
    [
        ([SILENT], "CY T20 shipped", "silent"),
        ([GOOD, CUT_SHORT], "CY T20 retuned", "the record ends before the decay does"),
        ([GOOD, ONE_LINE_OFF_BAND], "CY low line", "no prominent line in the band"),
        (
            [GOOD, ONE_LINE_IN_BAND],
            "CY line stability",
            "too few lines in the first window to judge stability",
        ),
    ],
    ids=["line58-silent-x0", "line59-cut-x1", "line62-no-low-line", "line65-one-line"],
)
def test_require_site_refuses_with_exit_2_and_names_itself(
    tmp_path, calls, site, reason
):
    p = _run(tmp_path, *calls)
    tail = (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert p.returncode == exit_contract.REFUSED, tail
    last = _last_line(p.stdout)
    assert last.startswith("REFUSED  "), tail
    assert f"{site}: insufficient evidence -- {reason}" in last, tail
    assert "Traceback" not in p.stderr and "Traceback" not in p.stdout, tail
    assert not (tmp_path / "hh_part3.json").exists(), (
        "a refused run must not write a result"
    )


def test_control_plain_assertion_error_still_exits_1(tmp_path):
    p = _run(
        tmp_path,
        {
            "kind": "raise",
            "exc": "AssertionError",
            "msg": "a plain assertion, not a refusal",
        },
    )
    tail = (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert p.returncode == exit_contract.FAIL, tail
    assert "Traceback (most recent call last)" in p.stderr, tail
    assert "AssertionError: a plain assertion, not a refusal" in p.stderr, tail
    assert "REFUSED" not in p.stdout, tail


def test_control_unrelated_error_still_exits_1(tmp_path):
    p = _run(
        tmp_path,
        {
            "kind": "raise",
            "exc": "ValueError",
            "msg": "an execution error is never a refusal",
        },
    )
    tail = (p.returncode, p.stdout[-300:], p.stderr[-400:])
    assert p.returncode == exit_contract.FAIL, tail
    assert "Traceback (most recent call last)" in p.stderr, tail
    assert "ValueError: an execution error is never a refusal" in p.stderr, tail
    assert "REFUSED" not in p.stdout, tail


def test_good_render_still_completes_and_writes_its_result(tmp_path):
    p = _run(tmp_path, GOOD)
    tail = (p.returncode, p.stdout[-400:], p.stderr[-400:])
    assert p.returncode == exit_contract.PASS, tail
    assert "REFUSED" not in p.stdout and "Traceback" not in p.stderr, tail
    assert "(written hh_part3.json)" in p.stdout, tail
    out = json.loads((tmp_path / "hh_part3.json").read_text())
    assert sorted(out) == ["CH", "OH", "control_numerator", "cy"], sorted(out)
    assert out["cy"]["t20_shipped"] == pytest.approx(TAU * math.log(10), rel=0.05), (
        out["cy"]["t20_shipped"],
        "the fixture's closed-form T20, 461 ms",
    )
