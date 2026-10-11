"""Known answers, refusals and injected controls for tools/probes/tom_distortion.py (#593)."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_distortion as td  # noqa: E402


def test_start_red_stub_estimator_fails_the_harness():
    """Rule 1: the right ports and no behaviour must FAIL the known answers, and
    must fail them for every reason (missed harmonics, no refusals)."""
    r = td.harness_verdict(td.stub_levels, td.ka_grid()[:40])
    assert not r["pass"]
    assert r["missed_resolved"] > 0
    assert len(r["refusal_cases_bad"]) == 8
