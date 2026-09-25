"""The sensitivity gate must be satisfiable as committed, and must be able to fail.

Both halves matter and the second is the one this repository keeps getting
wrong. Three unsatisfiable gates were written here in one day, and separately a
negative-control job once reported four catches in 1.6 s because it read any
non-zero exit as "bug caught". So: the real records pass (satisfiable), every
injected control turns it red (capable of failing), and a control with the
injection REMOVED does not pass (not vacuous).
"""
import copy
import json
import shutil
import subprocess
import sys

import pytest

import sensitivity as sens

FILES = ["docs/sensitivity/registry.json",
         "docs/sensitivity/drum-kit-modes.json",
         "docs/sensitivity/modal-bank-nums.json",
         "fpga/reports/mode_sweep.txt",
         "rtl-sketch/drum_kit.v"]


@pytest.fixture
def root(tmp_path):
    """A throwaway copy of exactly the files the gate reads."""
    for rel in FILES:
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(sens.ROOT / rel, dst)
    return tmp_path


def record(root, name="drum-kit-modes.json"):
    path = root / "docs/sensitivity" / name
    return path, json.loads(path.read_text())


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def run(root, **kw):
    import io
    out = io.StringIO()
    try:
        return sens.run_check(root=root, out=out, **kw), out.getvalue()
    except sens.Refused as exc:
        return 2, f"REFUSED: {exc}"


# --------------------------------------------------------------------------
# satisfiable as committed


def test_the_gate_passes_against_the_tree_it_ships_with():
    """Run a gate against the current state before committing it.
    An unsatisfiable gate is worse than no gate."""
    code, text = run(sens.ROOT)
    assert code == 0, text
    assert "MODES" in text and "NUMS" in text


def test_both_registered_parameters_ship_a_value_on_their_own_grid():
    records = sens.load_records(sens.load_registry())
    for rec in records:
        row = sens.evaluate(rec)
        assert row["ships_on_grid"], row
        assert not row["problems"], row["problems"]


# --------------------------------------------------------------------------
# capable of failing


EXPECT = {"VERDICT_ASSERTED": 1, "POINT_TRANSCRIBED": 1, "GRID_CHERRY_PICKED": 1,
          "SHIPPED_OFF_GRID": 1, "PREDICTION_WRONG": 1, "RULE_UNSTATED": 2}


def test_every_declared_injection_is_covered_here():
    """A control list that drifts from the tool is how a control stops firing."""
    assert set(EXPECT) == set(sens.INJECTIONS)


@pytest.mark.parametrize("name,code", sorted(EXPECT.items()))
def test_injected_control_turns_the_gate_red(root, name, code):
    got, text = run(root, injection=name)
    assert got == code, f"{name}: expected {code}, got {got}\n{text}"


def test_the_clean_tree_does_not_satisfy_expect_fail():
    """The only failure mode a control has that matters is a false green: if
    the run comes back red with the injection removed, the control proves
    nothing. `--expect fail` on a clean tree must itself fail."""
    assert sens.main(["check", "--expect", "fail"]) == 1
    assert sens.main(["check", "--expect", "refused"]) == 1
    assert sens.main(["check"]) == 0


# --------------------------------------------------------------------------
# the verdict arithmetic


def test_flat_sensitive_and_staircase_are_distinguished():
    flat = [(1, 100.0), (2, 101.0), (3, 100.5)]
    slope = [(1, 100.0), (2, 110.0), (3, 121.0)]
    stair = [(1, 100.0), (2, 200.0), (3, 201.0), (4, 202.0)]
    assert sens.state_of(sens.runs(flat, 0.02), 3) == "FLAT"
    assert sens.state_of(sens.runs(slope, 0.02), 3) == "SENSITIVE"
    assert sens.runs(stair, 0.02) == [[1], [2, 3, 4]]
    assert sens.state_of(sens.runs(stair, 0.02), 4) == "MIXED"


def test_a_group_is_bounded_by_its_own_spread_not_by_adjacent_pairs():
    """Every adjacent step inside the rule, the span outside it. Comparing
    only neighbours would call a monotone drift flat."""
    drift = [(1, 100.0), (2, 101.5), (3, 103.0), (4, 104.5)]
    assert all(b / a - 1 <= 0.02 for (_, a), (_, b) in zip(drift, drift[1:]))
    assert sens.runs(drift, 0.02) == [[1, 2], [3, 4]]


def test_tolerance_boundary_is_inclusive():
    assert sens.runs([(1, 100.0), (2, 102.0)], 0.02) == [[1, 2]]
    assert sens.runs([(1, 100.0), (2, 102.01)], 0.02) == [[1], [2]]


# --------------------------------------------------------------------------
# the artefact parser -- prose must not read as evidence


def test_the_committed_artefact_yields_the_three_tables():
    tables = sens.parse_fixed_width_tables((sens.ROOT / "fpga/reports/mode_sweep.txt").read_text())
    assert set(tables) == {"modal_dp", "drum_kit", "mode_cfg_regs"}
    assert len(tables["drum_kit"]) == 7 and len(tables["modal_dp"]) == 11
    assert tables["drum_kit"][0] == {"MODES": 8, "NUMS": 6, "cells": 21314,
                                     "um2": 519777.3, "flops": 2400}


def test_commentary_between_tables_is_not_read_as_rows():
    """The report interleaves tables with sentences that contain numbers --
    'drum_dp alone: 10769 cells 272894.1 um2 1361 flops'. A parser that took
    that as a row would turn a remark into a measurement."""
    text = ("thing -- a table\n"
            "  A    B\n"
            "  1  2.0\n"
            "  2  3.0\n"
            "\n"
            "  A remark mentioning 10769 cells and 272894.1 um2\n"
            "\n"
            "other -- another table\n"
            "  A    B\n"
            "  9  9.0\n")
    tables = sens.parse_fixed_width_tables(text)
    assert tables == {"thing": [{"A": 1, "B": 2.0}, {"A": 2, "B": 3.0}],
                      "other": [{"A": 9, "B": 9.0}]}


def test_held_fixed_selects_the_controlled_rows_only(root):
    _, rec = record(root)
    assert sens.extract_points(rec, root) == [
        (8, 519777.3), (11, 607141.8), (12, 603118.0), (14, 606843.3), (16, 607051.8)]
    rec["evidence"]["held_fixed"] = {"NUMS": 11}
    assert [v for v, _ in sens.extract_points(rec, root)] == [16, 18]


def test_an_unknown_table_or_column_refuses_rather_than_returning_nothing(root):
    path, rec = record(root)
    rec["evidence"]["table"] = "no_such_block"
    with pytest.raises(sens.Refused, match="no table"):
        sens.extract_points(rec, root)
    rec["evidence"]["table"] = "drum_kit"
    rec["evidence"]["objective_column"] = "mm2"
    with pytest.raises(sens.Refused, match="no column"):
        sens.extract_points(rec, root)


# --------------------------------------------------------------------------
# the parameter as it actually ships


def test_the_shipped_value_is_read_from_the_rtl_not_from_the_record(root):
    text = (root / "rtl-sketch/drum_kit.v").read_text()
    assert sens.parse_parameter(text, "MODES", "x") == 16
    assert sens.parse_parameter(text, "NUMS", "x") == 11


def test_a_parameter_that_is_absent_refuses():
    with pytest.raises(sens.Refused, match="found 0 declarations"):
        sens.parse_parameter("nothing here\n", "MODES", "x")


def test_an_ambiguous_parameter_refuses():
    """Two declarations means the gate cannot tell which value ships. It must
    say so rather than pick one."""
    with pytest.raises(sens.Refused, match="found 2 declarations"):
        sens.parse_parameter("  parameter MODES = 16,\n  parameter MODES = 12,\n",
                             "MODES", "x")


def test_a_shipped_value_off_the_grid_is_red_not_a_pass(root):
    path, rec = record(root)
    src = root / "rtl-sketch/drum_kit.v"
    src.write_text(src.read_text().replace("parameter MODES = 16,", "parameter MODES = 13,"))
    code, text = run(root)
    assert code == 1 and "no sweep evidence" in text


# --------------------------------------------------------------------------
# record hygiene


def test_a_grid_that_does_not_say_when_it_was_stated_refuses(root):
    path, rec = record(root)
    del rec["grid"]["stated_before_results"]
    write(path, rec)
    code, text = run(root)
    assert code == 2 and "stated before results" in text


def test_a_retrospective_grid_must_admit_it(root):
    path, rec = record(root)
    del rec["retrofit"]
    write(path, rec)
    code, text = run(root)
    assert code == 2 and "retrofit" in text


def test_a_record_missing_a_required_key_refuses(root):
    path, rec = record(root)
    del rec["evidence"]
    write(path, rec)
    code, text = run(root)
    assert code == 2 and "missing evidence" in text


def test_a_registry_naming_an_absent_record_refuses(root):
    (root / "docs/sensitivity/drum-kit-modes.json").unlink()
    code, text = run(root)
    assert code == 2 and "does not exist" in text


def test_show_refuses_an_unregistered_parameter():
    assert sens.main(["show", "not-a-parameter"]) == 2
    assert sens.main(["show", "drum-kit-modes"]) == 0


# --------------------------------------------------------------------------
# the retrofit: the finding of failure-modes mechanism 5, recomputed
#
# This is the acceptance test for the retrofit half of issue #224. It does not
# re-state the conclusion in prose; it derives it from the committed
# measurements under the committed rule, so it goes red if either moves.


def test_the_mode_count_debate_was_about_a_plateau():
    row = sens.evaluate(sens.load_records(sens.load_registry())[0])
    assert row["parameter"] == "MODES"
    plateau = [g for g in row["runs"] if len(g) > 1]
    assert plateau == [[11, 12, 14, 16]], row["runs"]
    objective = dict(row["points"])
    spread = max(objective[v] for v in plateau[0]) / min(objective[v] for v in plateau[0]) - 1
    assert spread < row["tolerance"], f"plateau spread {spread:.2%}"
    assert spread == pytest.approx(0.0067, abs=5e-4)
    # ... and 8, the other half of the same argument, is a real step
    step = objective[11] / objective[8] - 1
    assert step > 5 * row["tolerance"] and step == pytest.approx(0.168, abs=5e-3)


def test_the_variable_nobody_swept_is_the_one_the_objective_responds_to():
    row = sens.evaluate(sens.load_records(sens.load_registry())[1])
    assert row["parameter"] == "NUMS" and row["state"] == "SENSITIVE"
    objective = dict(row["points"])
    values = [v for v, _ in row["points"]]
    steps = [objective[b] / objective[a] - 1 for a, b in zip(values, values[1:])]
    assert all(s > row["tolerance"] for s in steps), steps
    assert steps == pytest.approx([0.0635, 0.0752], abs=5e-4)


def test_the_two_dials_are_scored_by_the_same_rule():
    """Comparing two parameters under different thresholds would decide the
    comparison by choosing the thresholds."""
    rows = [sens.evaluate(r) for r in sens.load_records(sens.load_registry())]
    assert len({r["tolerance"] for r in rows}) == 1


# --------------------------------------------------------------------------
# --changed-since: pricing a move the branch actually makes


@pytest.fixture
def repo(root):
    def git(*a):
        subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "base"], cwd=root, check=True, capture_output=True)
    return root


def move(root, old, new):
    src = root / "rtl-sketch/drum_kit.v"
    src.write_text(src.read_text().replace(old, new))


def test_a_move_inside_a_plateau_is_reported_as_unjustified_by_this_objective(repo):
    move(repo, "parameter MODES = 16,", "parameter MODES = 12,")
    code, text = run(repo, changed_since="HEAD")
    assert code == 0, text          # flat is not an error: another objective may justify it
    assert "FLAT-MOVE" in text and "-0.65%" in text
    assert "not a drum_kit cell area optimisation" in text


def test_a_move_the_objective_responds_to_is_priced(repo):
    move(repo, "parameter NUMS  = 11,", "parameter NUMS  = 6,")
    code, text = run(repo, changed_since="HEAD")
    assert code == 0 and "PRICED" in text and "-5.97%" in text
    assert "FLAT-MOVE" not in text


def test_a_move_to_an_unswept_value_is_red(repo):
    move(repo, "parameter MODES = 16,", "parameter MODES = 17,")
    code, text = run(repo, changed_since="HEAD")
    assert code == 1 and "sweep it before changing it" in text


def test_an_unchanged_tree_prices_nothing(repo):
    code, text = run(repo, changed_since="HEAD")
    assert code == 0 and "FLAT-MOVE" not in text and "PRICED" not in text


def test_an_unresolvable_ref_refuses_rather_than_passing_quietly(repo):
    code, text = run(repo, changed_since="no-such-ref")
    assert code == 2, text


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", *sys.argv[1:]]))
