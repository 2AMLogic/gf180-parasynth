"""#610: stage_plugins must keep exception types and tell a missing plugin
from a defect in building the rig."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reference_movement as rm


def _run(monkeypatch, exc, capsys):
    def boom(name, block=rm.PLUGIN_BLOCK):
        raise exc
    monkeypatch.setattr(rm, "_build", boom)
    rows = rm.stage_plugins(["miniv3"], cache=None)
    return rows, capsys.readouterr().out


def test_a_missing_plugin_is_unavailable_with_its_type(monkeypatch, capsys):
    rows, out = _run(monkeypatch, FileNotFoundError("Mini V3.vst3"), capsys)
    assert rows[0]["status"] == "unavailable" and rows[0]["error_type"] == "FileNotFoundError"
    assert "NOT AVAILABLE" in out and "FileNotFoundError" in out


def test_a_build_bug_is_not_reported_as_unavailable(monkeypatch, capsys):
    """start-red: a KeyError/TypeError used to print 'NOT AVAILABLE'."""
    rows, out = _run(monkeypatch, KeyError("param 265"), capsys)
    assert rows[0]["status"] == "error" and rows[0]["error_type"] == "KeyError"
    assert "NOT AVAILABLE" not in out and "KeyError" in out


def test_an_import_error_from_our_own_code_is_still_unavailable_known_gap(monkeypatch, capsys):
    """Rule 8: the input that defeats `_UNAVAILABLE`, pinned as an accepted limit.

    An ImportError from a refactor in OUR code is still reported as
    unavailable / "NOT AVAILABLE", the same status as a plugin that is not
    installed. The mitigation is that `error_type` and `why` carry the type
    and message, in the row and in the printed line. If this starts failing
    because the classification was narrowed, update it and the comment on
    `_UNAVAILABLE`."""
    rows, out = _run(monkeypatch,
                     ImportError("cannot import name 'renamed_name' from 'am'"), capsys)
    assert rows[0]["status"] == "unavailable"                 # the known gap
    assert rows[0]["error_type"] == "ImportError"             # the mitigation
    assert "ImportError: cannot import name 'renamed_name'" in rows[0]["why"]
    assert "ImportError: cannot import name 'renamed_name'" in out


# ---------------------------------------------------------------------------
# the EXIT STATUS when the plugins stage measured nothing (issue #1823)
#
# `main` returned 0 unconditionally. On a host with no plugin host every
# device came back `status: unavailable`, `plugins.json` said so, and the
# process still exited 0 -- PASS, a RESULT -- having measured nothing. The
# per-device row is right and stays (the three tests above); the status is
# wrong. Exit 0 is a result (`tools/exit_contract.py`); the truth is REFUSED,
# 2. A stage where a build DEFECT (#610) is among the rows is FAIL, 1: a bug
# must not read as "this host lacks a plugin". A sweep in which some device
# answered keeps 0.
#
# Red-first: every refusal and failure test here fails on 474bd2ff with
# rc == 0. THE CONTROL is test_a_partial_sweep_still_passes -- widen the rule
# to "any unavailable" and it goes red. Only the host boundary is stood in
# for (`_build`), plus the render for `ours` (5.8 s of real DSP, and the exit
# status is what is under test): `stage_plugins`, the rows it builds, the
# estimator and `main` are the real ones.
# ---------------------------------------------------------------------------
import json                                                            # noqa: E402

import numpy as np                                                     # noqa: E402
import pytest                                                          # noqa: E402

# tools/exit_contract.py, retyped so the oracle is not read off its subject
PASS, FAIL, REFUSED = 0, 1, 2
NO_HOST = ModuleNotFoundError("No module named 'dawdreamer'")
OURS_BROKEN = ImportError("cannot import name 'renamed_name' from 'am'")
OTHER_STAGES = ("control", "sweep", "injected", "resonance", "audio_rate_mod")
SWEEP_KEYS = {"device", "seconds", "oct_per_s", "ripple_db", "host_block", "ripple_rate_hz", "why"}


class _Rig:
    """Stands in for an installed plugin rig. `answer` renders each sweep, or
    is the exception `swept_cutoff` raises."""

    block = rm.PLUGIN_BLOCK

    def __init__(self, answer):
        self._answer = answer

    def swept_cutoff(self, carrier, lo, hi, seconds, cache):
        if isinstance(self._answer, BaseException):
            raise self._answer
        return self._answer(int(seconds * rm.SR))


def _tone(n):
    t = np.arange(n) / rm.SR
    return (0.25 * np.sin(2 * np.pi * rm.CARRIER * t)).astype(np.float32)


def _silence(n):
    return np.zeros(n, dtype=np.float32)


def _main(monkeypatch, tmp_path, capsys, devices, raise_for=(), answer=None, stage="plugins"):
    """(rc, plugins.json, the stdout lines) of one `rm.main` into tmp_path.

    raise_for: (device, exception) pairs; `_build` raises it for that device.
    answer:    {device: render}; each is installed and answers every sweep.
    Any other device reaches the real `_build`. Under `--stage all` the five
    `ours`-only stages are stubs that return their own name: 30 s of real DSP
    that is not what is under test there, which is `main`'s ORDER -- every
    report written, and only then the status."""
    raise_for, answer = dict(raise_for), dict(answer or {})
    real_build, real_sweep = rm._build, rm.dev_sweep

    def build(name, block=rm.PLUGIN_BLOCK):
        if name in raise_for:
            raise raise_for[name]
        if name in answer:
            return _Rig(answer[name])
        return real_build(name, block=block)

    def sweep(dev, name, carrier, lo, hi, seconds, cache):
        if name == "ours":
            return _tone(int(seconds * rm.SR))
        return real_sweep(dev, name, carrier, lo, hi, seconds, cache)

    monkeypatch.setattr(rm, "_build", build)
    monkeypatch.setattr(rm, "dev_sweep", sweep)
    if stage == "all":
        for st in OTHER_STAGES:
            monkeypatch.setattr(rm, f"stage_{st}", lambda st=st: {"stage": st})
    rc = rm.main(["--stage", stage, "--devices", devices, "--out", str(tmp_path)])
    return rc, json.load(open(tmp_path / "plugins.json")), capsys.readouterr().out.splitlines()


def _verdicts(out, kind):
    """The lines that BEGIN with `kind:`. The estimators' own `REFUSED:`
    strings sit mid-row in a table under `--stage all` and never begin a
    line, so they are not confused with the stage's verdict."""
    return [ln for ln in out if ln.startswith(f"{kind}:")]


def _unavailable(names):
    return [(d, OURS_BROKEN if d == "ours" else NO_HOST) for d in names]


@pytest.mark.parametrize("devices, names", [
    ("surge-huov", ["surge-huov"]),
    ("ours,surge-huov,surge-rk,miniv3,diva", ["ours", "surge-huov", "surge-rk", "miniv3", "diva"]),
], ids=["the-issues-reproduction", "the-default-sweep"])
def test_nothing_measured_is_refused_not_a_pass(monkeypatch, tmp_path, capsys, devices, names):
    """Host-independent: `_build` raises what a missing plugin host raises,
    whether or not this host has it. The per-device record is unchanged and
    still written; the status and one line say nothing was measured."""
    pairs = _unavailable(names)
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, devices, raise_for=pairs)
    assert rc == REFUSED, (rc, out)
    assert rows == [dict(device=d, status="unavailable", error_type=type(e).__name__,
                         why=f"{type(e).__name__}: {e}") for d, e in pairs]
    refused = _verdicts(out, "REFUSED")
    assert len(refused) == 1 and refused[0].startswith("REFUSED: --stage plugins "), out
    assert f"0 of {len(names)}" in refused[0], refused
    assert all(d in refused[0] for d in names), refused
    assert _verdicts(out, "FAIL") == []


def test_nothing_answerable_is_refused(monkeypatch, tmp_path, capsys):
    """The other way a row carries no measurement: the rig built, and the
    sweep raised NotImplementedError (`not-answerable`)."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, "surge-huov,miniv3", answer={
        "surge-huov": NotImplementedError("no swept cutoff"),
        "miniv3": NotImplementedError("no swept cutoff")})
    assert rc == REFUSED, (rc, out)
    assert [r["status"] for r in rows] == ["not-answerable"] * 2
    refused = _verdicts(out, "REFUSED")
    assert len(refused) == 1 and "0 of 2" in refused[0], out
    assert "surge-huov" in refused[0] and "miniv3" in refused[0], refused


def test_no_device_requested_is_refused(monkeypatch, tmp_path, capsys):
    """`--devices ""` measures nothing by construction. Exit 0 there would be
    the same defect with no host to blame."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, "")
    assert rc == REFUSED and rows == [], (rc, rows, out)
    refused = _verdicts(out, "REFUSED")
    assert len(refused) == 1 and "0 of 0" in refused[0], out


@pytest.mark.parametrize("devices, raise_for, answer, built", [
    (["miniv3"], [("miniv3", KeyError("param 265"))], {}, ["miniv3 (KeyError)"]),
    (["surge-huov", "miniv3"], _unavailable(["surge-huov"]) + [("miniv3", KeyError("param 265"))],
     {}, ["miniv3 (KeyError)"]),
    (["surge-huov", "miniv3"], [("miniv3", KeyError("param 265"))],
     {"surge-huov": NotImplementedError("no swept cutoff")}, ["miniv3 (KeyError)"]),
], ids=["error-only", "error-and-unavailable", "error-and-not-answerable"])
def test_nothing_measured_with_a_build_defect_fails_not_refuses(
        monkeypatch, tmp_path, capsys, devices, raise_for, answer, built):
    """FAIL wins over REFUSED whatever else is in the rows: a defect (#610)
    must not read as "this host lacks a plugin"."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, ",".join(devices),
                          raise_for=raise_for, answer=answer)
    assert rc == FAIL, (rc, out)
    assert sorted(r["device"] for r in rows if r["status"] == "error") == ["miniv3"]
    assert _verdicts(out, "REFUSED") == [], out
    failed = _verdicts(out, "FAIL")
    assert len(failed) == 1 and failed[0].startswith("FAIL: --stage plugins "), out
    assert all(b in failed[0] for b in built), failed


def test_an_unknown_device_is_a_build_defect_not_a_pass(monkeypatch, tmp_path, capsys):
    """Through the REAL `_build`: a device nobody can build is a KeyError
    there, which is the `error` class -- not a missing plugin."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, "bogus")
    assert rc == FAIL, (rc, rows)
    assert rows[0]["status"] == "error" and rows[0]["error_type"] == "KeyError", rows
    assert len(_verdicts(out, "FAIL")) == 1 and "bogus" in _verdicts(out, "FAIL")[0], out


@pytest.mark.parametrize("stage", ["plugins", "all"])
@pytest.mark.parametrize("devices, absent, who", [
    ("ours,surge-huov,miniv3", ["surge-huov", "miniv3"], "ours"),
    ("surge-huov,miniv3,diva", ["surge-huov", "diva"], "miniv3"),
], ids=["ours-plus-absent-plugins", "plugin-plus-absent-plugins"])
def test_a_partial_sweep_still_passes(monkeypatch, tmp_path, capsys, stage, devices, absent, who):
    """THE CONTROL. A sweep where some device answered is 0, with `unavailable`
    rows beside the measured ones -- `ours` plus absent plugins is this host,
    every day. Widening the rule to "any unavailable" turns this red."""
    answer = {} if who == "ours" else {who: _tone}
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, devices, raise_for=_unavailable(absent),
                         answer=answer, stage=stage)
    assert rc == PASS, (rc, out)
    measured = [r for r in rows if r["device"] == who]
    assert len(measured) == len(rm.RATES_S) and all(set(r) == SWEEP_KEYS for r in measured), rows
    assert all(r["ripple_db"] is not None for r in measured)
    assert sorted(r["device"] for r in rows if r.get("status") == "unavailable") == sorted(absent)
    assert _verdicts(out, "REFUSED") == [] and _verdicts(out, "FAIL") == [], out
    if stage == "all":
        assert all((tmp_path / f"{st}.json").exists() for st in OTHER_STAGES)


def test_a_device_that_rendered_counts_as_measured_even_when_the_estimator_refuses(
        monkeypatch, tmp_path, capsys):
    """The boundary of "measured", and out of scope on purpose (#1823): the
    rig answered, the REAL estimator found the render silent and refused.
    That row carries `seconds` and `ripple_db: None`; it is a measurement
    that could not answer, not a device that was not here."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, "surge-huov",
                          answer={"surge-huov": _silence})
    assert rc == PASS, (rc, out)
    assert len(rows) == len(rm.RATES_S)
    assert all(r["ripple_db"] is None and r["why"] == "silent" for r in rows), rows
    assert _verdicts(out, "REFUSED") == [], out


def test_stage_all_writes_every_report_before_refusing(monkeypatch, tmp_path, capsys):
    """The same rule on `--stage all`. The five `ours`-only stages do not
    depend on the devices, so their reports -- and `plugins.json` -- exist
    when the refusal comes back, and the refusal is the last thing printed."""
    rc, rows, out = _main(monkeypatch, tmp_path, capsys, "surge-huov",
                         raise_for=_unavailable(["surge-huov"]), stage="all")
    assert rc == REFUSED, (rc, out)
    for st in OTHER_STAGES:
        assert json.load(open(tmp_path / f"{st}.json")) == {"stage": st}, st
    assert rows == [dict(device="surge-huov", status="unavailable",
                         error_type="ModuleNotFoundError",
                         why="ModuleNotFoundError: No module named 'dawdreamer'")]
    refused = _verdicts(out, "REFUSED")
    assert len(refused) == 1 and out[-1] == refused[0], out
