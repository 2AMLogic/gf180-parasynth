"""The acceptance guard, against records the system ACTUALLY produces.

`test_acceptance_policy.py` has twelve passing tests and every one uses a
hand-built fixture. The guard it verifies was INERT on real records: it looked
for `provenance.analysis_run`, `rubric_version` and `inputs.refs`, while
`run_case.py` writes `analysis_run` at top level, has no `rubric_version`, and
keys `inputs` by path. Every basis field was None on both sides, compared equal,
and nothing ever fired.

Twelve green tests, and the thing they tested did not work.

So these load COMMITTED RESULT FILES. A fixture written by the author of the
code specifies nothing -- both can share one misunderstanding and agree.
"""
from __future__ import annotations
import copy, glob, json, pathlib, sys
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import scorecard as sc                                              # noqa: E402


def _real_results():
    out = []
    for p in sorted(glob.glob(str(ROOT / "docs/scorecard/results/*.json"))):
        d = json.loads(pathlib.Path(p).read_text())
        if (d.get("metrics") or {}) and d.get("engine"):
            # Only records that yield a VERDICT. compare() refuses a
            # NO_VERDICT for its own reason, which would mask the basis
            # behaviour these tests are about.
            case = {"required_measurements": ";".join(d.get("metrics", {}))}
            if sc.evaluate(case, d).get("state") in (sc.PASS, sc.FAIL):
                out.append((pathlib.Path(p).stem, d))
    return out


REAL = _real_results()
assert REAL, "the real-record control cannot run without committed results"


def _evaluated(doc):
    case = {"required_measurements": ";".join(doc.get("metrics", {}))}
    return sc.evaluate(case, doc)


def test_a_real_record_carries_a_basis_at_all():
    """The bug in one line: it did not."""
    _, doc = REAL[0]
    b = sc.measurement_basis(doc)
    assert b["apparatus"], f"no apparatus hashes found in a real record: {b}"
    assert b["engine"], "no engine in a real record"


def test_m5a_record_pins_its_scorer_and_scorecard_implementation():
    basis = sc.measurement_basis(dict(REAL)["M5A"])
    assert basis["analysis_version"] == "m5a-score-v3"
    assert basis["apparatus"].get("tools/mono_m5a_score.py")
    assert basis["apparatus"].get("tools/scorecard.py")


def test_evaluate_passes_the_basis_through_to_compare():
    """compare() consumes evaluate() output. It used to arrive stripped."""
    _, doc = REAL[0]
    out = _evaluated(doc)
    assert sc.measurement_basis(out)["apparatus"], \
        "evaluate() dropped provenance, so compare() could never see a basis"


def test_a_repaired_estimator_is_INCOMPARABLE_on_real_records():
    """THE CASE THE GUARD EXISTS FOR, and the one it failed in production:
    a filter rescore read an estimator artefact as the device regressing."""
    _, doc = REAL[0]
    base = _evaluated(doc)
    moved = copy.deepcopy(doc)
    moved["provenance"]["inputs"]["model/audio_measure.py"] = "sha256:REPAIRED"
    out = sc.compare(base, _evaluated(moved))
    assert out["verdict"] == sc.INCOMPARABLE, out
    assert any("basis differs" in r for r in out["reasons"]), out


def test_a_changed_mono_analysis_version_is_INCOMPARABLE():
    doc = copy.deepcopy(dict(REAL)["M5A"])
    base = _evaluated(doc)
    changed = copy.deepcopy(doc)
    changed["analysis_version"] = "m5a-score-next"
    out = sc.compare(base, _evaluated(changed))
    assert out["verdict"] == sc.INCOMPARABLE, out
    assert any("analysis_version" in reason for reason in out["reasons"]), out


def test_changing_the_DEVICE_is_still_comparable():
    """The converse, and it is what makes the guard usable: putting
    model/drums_fx.py in the basis would make every model change INCOMPARABLE
    and block the comparisons this exists to enable."""
    _, doc = REAL[0]
    base = _evaluated(doc)
    moved = copy.deepcopy(doc)
    moved["provenance"]["inputs"]["model/drums_fx.py"] = "sha256:NEWCOEFFS"
    assert sc.compare(base, _evaluated(moved))["verdict"] != sc.INCOMPARABLE


def test_the_same_record_against_itself_is_never_INCOMPARABLE():
    """A basis that differs from itself would refuse everything."""
    for name, doc in REAL[:6]:
        e = _evaluated(doc)
        assert sc.compare(e, e)["verdict"] != sc.INCOMPARABLE, name


@pytest.mark.parametrize("name,doc", REAL)
def test_every_committed_record_yields_a_usable_basis(name, doc):
    b = sc.measurement_basis(doc)
    assert b["apparatus"], f"{name}: apparatus empty -- guard would be inert"


@pytest.mark.parametrize("prefix,case_id", [("reference:", "D09A"), ("frozen:", "F1A")])
def test_changed_reference_bytes_at_the_same_path_are_incomparable(prefix, case_id):
    doc = dict(REAL)[case_id]
    moved = copy.deepcopy(doc)
    key = next(k for k in moved["provenance"]["inputs"] if k.startswith(prefix))
    moved["provenance"]["inputs"][key] = "sha256:CHANGED_AUDIO"
    assert sc.compare(_evaluated(doc), _evaluated(moved))["verdict"] == sc.INCOMPARABLE


@pytest.mark.parametrize("field,value", [("tolerance", 999.0), ("units", "different units")])
def test_changing_a_metric_definition_is_not_an_improvement(field, value):
    doc = dict(REAL)["D09A"]
    moved = copy.deepcopy(doc)
    next(iter(moved["metrics"].values()))[field] = value
    assert sc.compare(_evaluated(doc), _evaluated(moved))["verdict"] == sc.INCOMPARABLE


def test_missing_apparatus_on_both_sides_does_not_compare_equal():
    doc = copy.deepcopy(dict(REAL)["D09A"])
    for key in ("model/audio_measure.py", "tools/run_case.py"):
        doc["provenance"]["inputs"].pop(key)
    out = _evaluated(doc)
    assert sc.compare(out, out)["verdict"] == sc.INCOMPARABLE


def test_timestamp_and_cache_location_do_not_change_the_measurement_basis():
    doc = dict(REAL)["D09A"]
    moved = copy.deepcopy(doc)
    moved["analysis_run"] = "the same apparatus, run again tomorrow"
    moved["provenance"]["config"]["refs"] = "/another/host/cache"
    moved["provenance"]["worktree"]["commit"] = "a-new-device-commit"
    assert sc.compare(_evaluated(doc), _evaluated(moved))["verdict"] != sc.INCOMPARABLE


def test_changed_measurement_settings_are_incomparable():
    doc = dict(REAL)["D09A"]
    moved = copy.deepcopy(doc)
    moved["provenance"]["config"]["level_matched"] = False
    assert sc.compare(_evaluated(doc), _evaluated(moved))["verdict"] == sc.INCOMPARABLE


def test_changing_the_required_property_set_needs_a_new_baseline():
    doc = dict(REAL)["D09A"]
    required = list(doc["metrics"])
    assert len(required) > 1
    smaller = sc.evaluate({"required_measurements": required[0]}, doc)
    assert sc.compare(_evaluated(doc), smaller)["verdict"] == sc.INCOMPARABLE
