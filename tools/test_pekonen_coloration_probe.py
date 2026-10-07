"""Tests for tools/pekonen_coloration_probe.py (issue #243)."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pekonen_coloration_probe as pk  # noqa: E402


def test_every_injected_control_behaves():
    c = pk.controls()
    assert all(c.values()), c


def test_known_pole_is_adopted_and_recovered_but_reference_is_not():
    f0s = {n: 440.0 * 2 ** ((n - 69) / 12) for n in (33, 45, 57, 69, 81)}
    rows = [(n, k, f0s[n], pk.stage_db(0.55, k, f0s[n])) for n in f0s for k in range(2, 13)]
    assert pk.verdict(rows)["adopt"]
    mini, ours = pk.load_reference()
    rows, excl = pk.build_residuals(mini, ours)
    assert excl == [93]
    assert not pk.verdict(rows)["adopt"]


def test_stale_reference_is_refused():
    mini, ours = pk.load_reference()
    bad = {n: dict(r, h4=r["h4"] - 1.0) for n, r in ours.items()}
    try:
        pk.build_residuals(mini, bad)
    except pk.Refused:
        return
    raise AssertionError("stale ours was scored")
