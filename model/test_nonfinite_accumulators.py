#!/usr/bin/env python3
"""Issue #1289: a NaN must not vanish into a max() accumulator.

REACHABILITY, measured by injection (CPython 3.12.3, numpy/scipy venv):
  * tom: `_fit_exponential` on an excess containing NaN returns e0=None
    (curve_fit raises ValueError), so ratio_at_onset is absent -- NaN is
    unreachable through the fit. Pinned below as a control.
  * bd: `band_energy_interval` on an all-zero window returns [nan]*4. All-or-
    nothing, so the NaN is column 0 and survived `max()` only by position.
    Reachable; the partial-NaN case is not producible today but defeats the old
    guard (`[0.1,0.1,0.1,nan]` -> worst 0.1 -> pass).
"""
import os, subprocess, sys
import numpy as np
import pytest
sys.path.insert(0, os.path.dirname(__file__))
import tom_drop_measure as tm   # first: must import without bd's sys.path side effect
import bd_excitation_probe as bd
import tom_pitch_probe as P
import drum_fit as df

NAN = float("nan")


def _row(vals):
    r = {"voice": "V", "accent": "A", "file": "f.wav"}
    for lead, v in zip((0.0, 0.5, 1.0, 10.0), vals):
        r[f"lead_{lead}"] = (v, 100.0, "OK")
    return r


def test_tom_finite_rows_unchanged():
    assert tm.leading_silence_worst_delta([_row([1.5, 1.5, 1.52, 1.4])]) == pytest.approx(0.1)
    assert tm.leading_silence_worst_delta([_row([1.5, None, 1.5, 1.5])]) == 0.0


@pytest.mark.parametrize("vals", [[1.5, NAN, 1.5, 1.5], [NAN, 1.5, 1.5, 1.5],
                                  [NAN] * 4, [1.5, 1.5, 1.5, float("inf")]])
def test_tom_nonfinite_refuses_not_zero(vals):
    # injected-bug control: the old accumulator printed 0.0 for the all-NaN row
    with pytest.raises(tm.Refused, match="nonfinite"):
        tm.leading_silence_worst_delta([_row(vals)])


@pytest.mark.parametrize("i", range(4))
def test_bd_nan_in_any_column_refuses(i):
    a = [0.1, 0.2, 0.3, 0.4]; b = list(a); b[i] = NAN
    with pytest.raises(bd.Refused):
        bd.worst_column_move(a, b)


def test_bd_finite():
    assert bd.worst_column_move([1, 2, 3, 4], [1, 2, 3, 4.25]) == 0.25


@pytest.mark.parametrize("a, b", [
    ([0.1, 0.2], [0.1, 0.2, NAN]),
    ([0.1, 0.2, NAN], [0.1, 0.2]),
    ([1], [1, 2]),
    ([], []),
    ([], [1]),
    ([1], []),
])
def test_bd_invalid_row_widths_refuse(a, b):
    with pytest.raises(bd.Refused, match=rf"got {len(a)} and {len(b)} columns"):
        bd.worst_column_move(a, b)


@pytest.mark.parametrize("a, b, expected", [
    ([1], [3], 2),
    ([1, 2, 3, 4, 5], [1, 5, 3, 4, 6], 3),
])
def test_bd_equal_width_iterables(a, b, expected):
    assert bd.worst_column_move(iter(a), iter(b)) == expected


def test_bd_zero_window_is_nan_row():
    r = df.band_energy_interval(np.zeros(48000), 48000, bd.EDGES, bd.T0, bd.T1)
    assert all(np.isnan(r))


def test_tom_nan_excess_gives_no_fit_not_nan():
    t = np.linspace(0.001, 0.1, 40); exc = 0.5 * np.exp(-t / 0.025); exc[3] = NAN
    assert P._fit_exponential(t, exc)["e0"] is None


@pytest.mark.parametrize("script", ["tom_drop_measure.py", "bd_excitation_probe.py"])
def test_entry_point_runs_as_shipped(script):
    # The shipped thing is the script run as a process: an in-process import is
    # repaired by whichever module imported first (review of #1291, finding 1/2).
    r = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), script),
                        "--help"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]


def test_bd_self_test_refusal_is_exit_2_not_traceback(monkeypatch, capsys):
    # A Refused is no-evidence (exit 2), never an exit-1 FAIL traceback.
    def boom():
        raise bd.Refused("nonfinite column 0")
    monkeypatch.setattr(bd, "self_test", boom)
    assert bd.main(["--self-test"]) == 2
    assert "REFUSED: nonfinite column 0" in capsys.readouterr().out
