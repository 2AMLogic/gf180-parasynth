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
#: model/discrimination_features.py, 13 -> 20, and this line is RE-DERIVED BY
#: COLLECTION rather than picked from either side of a merge: #773 landed with
#: this line conflicting, and NEITHER side was right (merge-base 13, this
#: branch 14, origin/main 18 -- the two branches added disjoint tests, so
#: neither number describes the merged tree). Re-derive it with
#: `inv.expected_nodes("model/discrimination_features.py", src)`, never by
#: adding the two diffs. The twenty, by the side that added each:
#:   13  the merge base (#763's
#:       test_the_dphase_floor_refuses_the_input_that_defeats_it, 12 -> 13)
#:   +5  origin/main, the #767 fold interpolator:
#:       test_dominant_period_picks_a_different_octave_at_the_two_rates,
#:       test_the_fold_interpolator_ships_a_real_width,
#:       test_the_fold_reduces_to_indexing_on_a_whole_period,
#:       test_the_integer_fold_this_replaced_is_still_red_on_this_panel,
#:       test_the_one_residual_the_fold_repair_cannot_reach_is_the_stimulus
#:   +1  #768, this branch:
#:       test_the_floor_census_counts_a_refusal_from_the_decision_not_the_zero
#:   +1  #773, this merge resolution -- the guard on the signature order the
#:       merge itself could have got wrong:
#:       test_the_merge_order_that_would_feed_a_tap_count_to_the_ppm_floor_is_red
ISSUE_564_MODULES = {
    "tools/probes/excitation_energy.py": 22,
    "tools/probes/dc_blocker.py": 17,
    "model/discrimination_features.py": 20,
    "model/discrimination_trajectory.py": 6,
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
