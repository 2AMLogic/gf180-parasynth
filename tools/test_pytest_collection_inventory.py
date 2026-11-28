"""Controls for tools/pytest_collection_inventory.py (issue #564).

The guard is only evidence if it has been seen to go red on the defect it
exists for, and to refuse -- not "catch" -- when it cannot compare. Every
control builds a throwaway git project, so nothing here edits the real tree and
only ``test_the_real_tree_collects_every_defined_test`` collects the whole
repository (once, ~15 s).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest_collection_inventory as inv  # noqa: E402

#: The four modules #564 found uncollected, with their in-file test counts on
#: first collection. Pinned so a discovery change that silently drops one of
#: them (scope, path, parsing) cannot let the real-tree check pass vacuously.
#: A count here goes UP when a module gains a test.
#:
#: model/discrimination_features.py, 13 -> 32, and this line is RE-DERIVED BY
#: COLLECTION rather than picked from either side of a merge: #773 landed with
#: this line conflicting, and NEITHER side was right (merge-base 13, that
#: branch 14, origin/main 18 -- the two branches added disjoint tests, so
#: neither number describes the merged tree). #775 hit the SAME conflict here
#: and the same way: this branch said 23, origin/main said 20, and the merged
#: tree collects 25. Its second conflict with main repeated the pattern --
#: this branch said 25, origin/main said 21, and the merged tree collects 26
#: -- so the count below was re-derived by collection a third time. The #781
#: merge with main is the fourth: both sides said 26, each describing its own
#: tree, and the merged tree collects 31, re-derived by collection again.
#: The #895 merge with main is the fifth, a new shape: BOTH sides said 31 and
#: the line auto-merged cleanly, but it described neither tree -- this branch
#: had added a test at 91dd5b6e without bumping the pin, so it arrived carrying
#: 32-collected/pin-31, and the merged tree collects 32. Re-derive it with
#: `inv.expected_nodes("model/discrimination_features.py", src)`, never by
#: adding the two diffs. The thirty-two, by the side that added each:
#:   13  the merge base (#763's
#:       test_the_dphase_floor_refuses_the_input_that_defeats_it, 12 -> 13)
#:   +5  origin/main, the #767 fold interpolator:
#:       test_dominant_period_picks_a_different_octave_at_the_two_rates,
#:       test_the_fold_interpolator_ships_a_real_width,
#:       test_the_fold_reduces_to_indexing_on_a_whole_period,
#:       test_the_integer_fold_this_replaced_is_still_red_on_this_panel,
#:       test_the_one_residual_the_fold_repair_cannot_reach_is_the_stimulus
#:   +1  #768, origin/main:
#:       test_the_floor_census_counts_a_refusal_from_the_decision_not_the_zero
#:   +1  #773, that merge resolution -- the guard on the signature order the
#:       merge itself could have got wrong:
#:       test_the_merge_order_that_would_feed_a_tap_count_to_the_ppm_floor_is_red
#:   #781, the octave-margin fix, reworked #767's octave pair: -1
#:       test_dominant_period_picks_a_different_octave_at_the_two_rates
#:     (its predicate is the fixed behaviour now, renamed below), +6:
#:       test_dominant_period_returns_the_same_octave_at_both_rates,
#:       test_the_bare_argmax_this_replaced_is_still_wrong_on_that_class,
#:       test_the_octave_check_does_not_touch_a_whole_sample_period,
#:       test_the_octave_margin_does_not_move_the_rate_panel,
#:       test_the_octave_margin_refuses_the_input_that_defeats_it,
#:       test_the_octave_margin_ships_a_real_margin
#:   +1  origin/main, #768's remaining increment -- the `degenerate_refused`
#:       control the census test above stated, correctly, that it did not
#:       have (it landed on main while this branch was open):
#:       test_a_doubly_degenerate_row_is_not_credited_to_the_floor
#:   +5  #775, this branch: the cyclecorr margin finding, the injected control
#:       for the new RATE_TOL_REL, and three on the quarantine
#:   +1  #895, this branch: the 191-column feature-vector guard,
#:       test_the_feature_vector_is_still_191_columns (added at 91dd5b6e
#:       without bumping this pin -- the fifth re-derivation above)
ISSUE_564_MODULES = {
    "tools/probes/excitation_energy.py": 22,
    "tools/probes/dc_blocker.py": 17,
    "tools/probes/trajectory_within_rate_drift.py": 5,  # #1129 Doctor pass: the within-rate drift instrument
    "model/discrimination_features.py": 32,
    #   +5  #1129, this branch: the known-answer unit controls (a cell is a
    #       share of the clip at any rate; the same tone at two rates
    #       differences to zero; DC and Nyquist weighted once; the floor
    #       clamps at a true -75 dB; the one-sample window)
    #   +4  #1565, this branch: the decay-fit controls (windows clamped to the
    #       floor are excluded; two sounding windows near the floor are refused;
    #       the post-peak minimum survives pre-peak windows; an averaged cell
    #       can still carry the floor)
    "model/discrimination_trajectory.py": 15,
}


@pytest.mark.parametrize("ctl", inv.CONTROLS, ids=lambda c: c.name)
def test_injected_control_produces_its_expected_verdict(ctl, tmp_path):
    rep = inv.run_control(ctl, tmp_path)
    assert rep.verdict == ctl.expect, rep.render()
    assert set(rep.missing) == set(ctl.expect_missing), rep.render()


def test_an_omission_report_names_the_missing_path(tmp_path):
    ctl = next(c for c in inv.CONTROLS if c.name == "uncollected_module")
    text = inv.run_control(ctl, tmp_path).render()
    assert text.startswith("FAIL")
    assert "pkg/probe.py::test_probe_fails" in text


def test_an_import_error_is_refused_not_reported_as_an_omission(tmp_path):
    ctl = next(c for c in inv.CONTROLS if c.name == "import_error")
    rep = inv.run_control(ctl, tmp_path)
    assert rep.verdict == inv.REFUSED
    assert rep.missing == []
    assert "collection error is not an omission" in rep.reason


def test_a_collected_failing_test_turns_the_directory_run_red(tmp_path):
    """The other half: once collected, a failing in-file test must fail the
    same directory-style invocation the suites use. The inventory guard
    catches omission; this shows execution propagates a failure."""
    ctl = next(c for c in inv.CONTROLS if c.name == "module_named_in_python_files")
    root = inv.make_project(tmp_path / "p", ctl.files, ctl.ini)
    assert inv.check(root, ("pkg",)).verdict == inv.PASS
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--rootdir", str(root), "pkg/"],
        cwd=root, capture_output=True, text=True)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "pkg/probe.py::test_probe_fails" in proc.stdout


def test_class_methods_and_parametrised_ids_are_matched():
    src = ("import pytest\n"
           "@pytest.mark.parametrize('x', [1, 2])\n"
           "def test_p(x):\n    pass\n"
           "class TestK:\n    def test_m(self):\n        pass\n"
           "def helper():\n    def test_nested():\n        pass\n")
    nodes = inv.expected_nodes("a/b.py", src)
    assert nodes == ["a/b.py::test_p", "a/b.py::TestK::test_m"]
    collected = {"a/b.py::test_p[1]", "a/b.py::test_p[2]", "a/b.py::TestK::test_m"}
    assert all(inv.is_collected(n, collected) for n in nodes)
    # A prefix that is merely a longer name is NOT a match.
    assert not inv.is_collected("a/b.py::test_p", {"a/b.py::test_px"})


def test_the_real_tree_collects_every_defined_test():
    rep = inv.check()
    assert rep.verdict == inv.PASS, rep.render()
    for path, n in ISSUE_564_MODULES.items():
        assert len(rep.expected.get(path, [])) == n, (path, rep.expected.get(path))
