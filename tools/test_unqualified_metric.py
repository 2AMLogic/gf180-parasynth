"""An UNQUALIFIED metric (plan084 section 5) is refused, keeps its reading
labelled beside it, and cannot let a case pass -- checked through the real
scorer, not assumed."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import run_case as rc  # noqa: E402
import scorecard  # noqa: E402

GOOD = {"value": 20.0, "units": "ms", "reference": 21.0, "error": -1.0, "tolerance": 10.5,
        "valid": True, "tolerance_basis": "time", "purpose": "match"}


def _case():
    return next(c for c in rc.load_cases() if c["case_id"] == "D12A")


def _res(timing):
    """The committed D12A record (for its provenance), with every metric made a
    pass except the one under test."""
    import json
    rec = json.loads((pathlib.Path(__file__).resolve().parent.parent
                      / "docs/scorecard/results/D12A.json").read_text())
    ok = dict(GOOD, value=0.0, reference=0.0, error=0.0, tolerance=3.0)
    rec["metrics"] = {"Burst timing": timing, "burst/tail ratio": ok, "decay": ok}
    return rec


def test_unqualified_metric_is_refused_and_keeps_its_reading():
    m = rc.unqualified_metric("ms", rc.UNQUALIFIED[("CP", "Burst timing")], GOOD)
    assert m["valid"] is False and "error" not in m and m["qualification"] == "UNQUALIFIED"
    assert m["unqualified_value"] == 20.0 and m["unqualified_error"] == -1.0
    assert "UNQUALIFIED" in m["why"]


def test_a_case_cannot_pass_on_an_unqualified_property():
    case = _case()
    passing = scorecard.evaluate(case, _res(GOOD))
    assert passing["state"] == scorecard.PASS                      # control: the same record, qualified
    m = rc.unqualified_metric("ms", rc.UNQUALIFIED[("CP", "Burst timing")], GOOD)
    got = scorecard.evaluate(case, _res(m))
    assert got["state"] != scorecard.PASS and "Burst timing" in got["why"]
