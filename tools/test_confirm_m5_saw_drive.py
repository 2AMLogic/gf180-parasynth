"""The confirmation rule must fail each defect it names, and pass a clean
point; plus one real render at a fresh note (the tool's own path)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import confirm_m5_saw_drive as c  # noqa: E402

BASE = {"unwanted_rel_db": -48.0, "upper_wanted_rel_db": -15.0, "ladder_deficit_db": -5.0,
        "intended_dbfs": -17.0, "output_rail": 0}


def _with(**kw):
    return {**BASE, **kw}


def test_clean_candidate_passes():
    assert c.judge(BASE, _with(ladder_deficit_db=-2.0, upper_wanted_rel_db=-14.0)) == []


def test_each_defect_is_named():
    assert c.judge(BASE, _with(unwanted_rel_db=-46.9)) == ["unwanted"]
    assert c.judge(BASE, _with(upper_wanted_rel_db=-15.2)) == ["darker"]
    assert c.judge(BASE, _with(ladder_deficit_db=-5.5)) == ["deficit worse"]
    assert c.judge(BASE, _with(intended_dbfs=-18.6)) == ["level drift"]
    assert c.judge(BASE, _with(output_rail=3)) == ["rail"]


def test_real_fresh_point_renders_and_the_candidate_is_brighter():
    b = c.point(c.BASE, c.SAW_VOL, 78, 20000, 0.0)
    cand = c.point(c.CAND, c.SAW_VOL * 1.9231, 78, 20000, 0.0)
    assert cand["ladder_deficit_db"] > b["ladder_deficit_db"]
