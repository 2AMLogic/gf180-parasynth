"""Tests for tools/pekonen_coloration_probe.py (issue #243)."""
import os
import sys

import pytest

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


NON_FINITE = [float("nan"), float("inf"), -float("inf")]


@pytest.fixture(scope="module")
def committed():
    """The committed row tables, and the residuals the real model gives: the
    positive control for every refusal below."""
    mini, ours = pk.load_reference()
    rows, _ = pk.build_residuals(mini, ours)
    return mini, ours, rows


def _with_level(mini, ours, device, note, k, value):
    """Both row tables with ONE recorded harmonic level replaced."""
    tables = {"ours": {n: dict(r) for n, r in ours.items()},
              "miniv3": {n: dict(r) for n, r in mini.items()}}
    tables[device][note][f"h{k}"] = value
    return tables["miniv3"], tables["ours"]


@pytest.mark.parametrize("device", ["ours", "miniv3"])
@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf"])
def test_a_recorded_level_that_is_not_a_finite_number_is_refused_naming_it(committed, device, bad):
    """#1994. Every ordering comparison against NaN is False (#134), so
    `abs(recorded - now) > STALE_OURS_DB` did not trip on a recorded NaN: a
    non-finite 'ours' level read as current, and a non-finite Mini V3 level
    went into the residual the pole is fitted to without any comparison at
    all."""
    mini, ours, rows = committed
    assert rows
    note, k = rows[0][0], rows[0][1]
    with pytest.raises(pk.Refused) as info:
        pk.build_residuals(*_with_level(mini, ours, device, note, k, bad))
    text = str(info.value)
    assert f"{device}[{note}].h{k}" in text and repr(bad) in text, text


def test_a_miniv3_level_goes_straight_into_the_residual_the_pole_is_fitted_to(committed):
    """Why the Mini V3 level is asserted although no comparison guards it: the
    reader subtracts the model from it and fits a pole to the result."""
    mini, ours, rows = committed
    note, k = rows[0][0], rows[0][1]
    moved, _ = pk.build_residuals(*_with_level(mini, ours, "miniv3", note, k, mini[note][f"h{k}"] + 1.0))
    by_key = lambda residual: {(n, harmonic): x for n, harmonic, _, x in residual}
    before, after = by_key(rows), by_key(moved)
    assert after[(note, k)] == pytest.approx(before[(note, k)] + 1.0)
    assert all(after[key] == before[key] for key in before if key != (note, k))
