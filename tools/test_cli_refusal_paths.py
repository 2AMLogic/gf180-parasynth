#!/usr/bin/env python3
"""Controls for the CLI refusal paths #1685 repairs: a callee's refusal must
come out of the shipped `main()` as `REFUSED` with exit 2, never as an uncaught
exception.

Each test runs the REAL `main()` with the one callee that owns the site stubbed
to raise its own module's `Refused`, and asserts that the printed reason is the
stub's own. An earlier precondition that refuses reads the same exit code, so
only the reason separates the two. The control beside each is the same stub
raising an unrelated exception, which must still propagate: a bare `except
Exception` relabels it, and that control is what goes red when one is swapped
in. Every test started red on `origin/main` (@ `69ce038d`).
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for p in (ROOT / "tools", ROOT / "tools" / "probes"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import exit_contract                     # noqa: E402
import check_m1a_selection as sel        # noqa: E402
import clap_d12a_probe as cp             # noqa: E402
import run_case as rc                    # noqa: E402

REASON = "<control> the callee refused on purpose"


def _assert_refused(code: int, out: str) -> None:
    assert code == exit_contract.REFUSED, (code, out[-400:])
    assert "REFUSED" in out and REASON in out, out[-400:]


# ---------------------------------------------------------------------------
# tools/check_m1a_selection.py: `check()` is called after main's only try, and
# `mono_m1a_score.patch_for_reference` inside it has no handler on its path.
# ---------------------------------------------------------------------------
def test_check_m1a_selection_a_patch_for_reference_refusal_exits_refused_2(monkeypatch, capsys):
    def refusing(*a, **k):
        raise sel.bass.Refused(REASON)
    monkeypatch.setattr(sel.bass, "patch_for_reference", refusing)
    code = sel.main([])
    _assert_refused(code, capsys.readouterr().out)


def test_check_m1a_selection_control_an_unrelated_error_still_propagates(monkeypatch, capsys):
    # RuntimeError, not ValueError: main's handler catches ValueError (as a
    # refused record), so it would no longer propagate.
    def broken(*a, **k):
        raise RuntimeError(REASON)
    monkeypatch.setattr(sel.bass, "patch_for_reference", broken)
    with pytest.raises(RuntimeError, match=REASON):
        sel.main([])
    assert "REFUSED" not in capsys.readouterr().out


# ---------------------------------------------------------------------------
# tools/clap_d12a_probe.py: `known_answer()` runs at the top of main, before
# its try, and calls `run_case.prepare` five times with no handler on the path.
# ---------------------------------------------------------------------------
def test_clap_d12a_probe_a_known_answer_prepare_refusal_exits_refused_2(tmp_path, monkeypatch, capsys):
    def refusing(*a, **k):
        raise rc.Refused(REASON)
    monkeypatch.setattr(rc, "prepare", refusing)
    out = tmp_path / "probe.json"
    code = cp.main(["--known-answer-only", "--out", str(out)])
    _assert_refused(code, capsys.readouterr().out)
    assert not out.exists(), "a refused run must not write its report"


def test_clap_d12a_probe_control_an_unrelated_error_still_propagates(tmp_path, monkeypatch, capsys):
    def broken(*a, **k):
        raise ValueError(REASON)
    monkeypatch.setattr(rc, "prepare", broken)
    with pytest.raises(ValueError, match=REASON):
        cp.main(["--known-answer-only", "--out", str(tmp_path / "probe.json")])
    assert "REFUSED" not in capsys.readouterr().out


@pytest.mark.parametrize("ok, code", [(True, 0), (False, 1)])
def test_clap_d12a_probe_known_answer_only_keeps_its_exit_codes(tmp_path, monkeypatch, capsys, ok, code):
    """The repair wraps the call and must not move the flag's own verdicts."""
    monkeypatch.setattr(cp, "known_answer", lambda: {"ok": ok, "cases": []})
    assert cp.main(["--known-answer-only", "--out", str(tmp_path / "probe.json")]) == code
