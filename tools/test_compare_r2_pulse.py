"""compare_r2_pulse must never accept over missing pairs, and must pair a
repaired row with R1's row for the same scheduled condition."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import compare_r2_pulse as c  # noqa: E402


def _row(engine, drive, unw=-60.0, rel=-50.0, up=-15.0, nominal_drive=None):
    r = {"engine": engine, "note": 84, "verdict": "MEASURED",
         "patch": {"waves": ["pulse29"] * 3, "cutoff": [20000, 20000], "q": 0.0, "drive": drive},
         "stages": {"output": {"unwanted_dbfs": unw, "unwanted_rel_db": rel, "upper_wanted_rel_db": up,
                               "intended_dbfs": -12.0}},
         "clip": {"output_rail_samples": 0}, "dropout_depth_db": -0.1}
    if nominal_drive is not None:
        r["nominal"] = {"waves": ["pulse29"] * 3, "cutoff": [20000, 20000], "q": 0.0, "drive": nominal_drive}
    return r


def test_repaired_row_pairs_by_schedule():
    recs, counts = c.compare([_row("r1", 0.75), _row("pulse2x", 1.0136, unw=-70, nominal_drive=0.75)])
    assert counts == {"measured": 1, "refused": 0, "not_run": 0}


def test_unpaired_rows_are_not_run_and_block_acceptance():
    recs, counts = c.compare([_row("r1", 0.75), _row("pulse2x", 1.0136)])
    assert counts["not_run"] == 1 and counts["measured"] == 0


def test_1_02_db_worse_fails_a_1_00_db_band():
    recs, _ = c.compare([_row("r1", 0.75, rel=-50.0), _row("pulse2x", 0.75, rel=-48.98)])
    assert recs[0]["verdict"] == "FAIL" and recs[0]["unwanted_rel_db"]["state"] == "regressed beyond band"


def test_darker_is_the_adverse_direction_for_brightness():
    recs, _ = c.compare([_row("r1", 0.75, up=-15.0), _row("pulse2x", 0.75, up=-16.5)])
    assert recs[0]["fails_declared_rule"] == ["upper_wanted_rel_db"]
