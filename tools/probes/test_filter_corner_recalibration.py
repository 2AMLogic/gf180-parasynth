"""`tools/probes/filter_corner_recalibration.py` refuses when no invariant
was checked (#1469).

`main()` derives its exit from the module-level `fails` list, which only
`check()` appends to. With `docs/reference-compare-results.json` absent,
`real_curves()` returns before its two `check()` calls, `fails` stays empty,
and the probe printed `every stated invariant holds` with exit 0.

    python3 -m pytest tools/probes/test_filter_corner_recalibration.py -q
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
FCR = ROOT / "tools" / "probes" / "filter_corner_recalibration.py"


def _load():
    spec = importlib.util.spec_from_file_location("fcr_under_test", FCR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def mod(monkeypatch):
    module = _load()
    # Only the archived curve section grades these invariants.
    monkeypatch.setattr(module, "ours", lambda: None)
    monkeypatch.setattr(module, "reference_side", lambda: None)
    return module


def test_missing_results_file_refuses_instead_of_declaring_every_invariant_holds(
        mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "RESULTS",
                        mod.RESULTS.parent / "no-such-results.json")
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "every stated invariant holds" not in out
    assert "REFUSED" in out and "no invariant" in out


@pytest.mark.parametrize("payload", ["{}", '{"response-empty": []}'])
def test_results_file_holding_no_usable_curves_refuses(
        mod, tmp_path, monkeypatch, capsys, payload):
    # Rule 8 defeating input for a missing-file-only guard: when RESULTS exists
    # and holds zero usable response curves, `trends` is `{}` and `real_curves`
    # previously ran `check(all([]))` (vacuous OK) and `check(any([]))` (FAIL),
    # exiting 1 instead of 2 REFUSED.
    empty = tmp_path / "empty-results.json"
    empty.write_text(payload)
    monkeypatch.setattr(mod, "RESULTS", empty)
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "every stated invariant holds" not in out
    assert "REFUSED" in out
    assert "get worse" not in out


@pytest.mark.parametrize("subset", ["one-device", "duplicate-cutoff", "wrong-device", "refused-corner"])
def test_trend_needs_each_device_across_the_measured_range(
        mod, tmp_path, monkeypatch, capsys, subset):
    data = json.loads(mod.RESULTS.read_text())
    diva = data["response-diva"]
    if subset == "one-device":
        data = {"response-diva": [r for r in diva if r["cut_hz"] in (400, 800)]}
    elif subset == "duplicate-cutoff":
        diva[-1] = diva[-2]
    elif subset == "wrong-device":
        diva[-1]["device"] = "miniv3"
    else:
        diva[-1]["gain_db"] = [0.0] * len(diva[-1]["freqs"])
    results = tmp_path / "results.json"
    results.write_text(json.dumps(data))
    monkeypatch.setattr(mod, "RESULTS", results)
    assert mod.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "get worse" not in out
    assert not mod.checked


@pytest.mark.parametrize("field", ["device", "freqs", "gain_db", "cut_hz"])
def test_a_null_required_field_refuses_instead_of_raising(mod, tmp_path, monkeypatch, capsys, field):
    """#1469 review: JSON null for gain_db / cut_hz hit len(None) / float(None)
    and raised TypeError instead of the documented exit 2."""
    data = json.loads(mod.RESULTS.read_text())
    data["response-diva"][0][field] = None
    results = tmp_path / "results.json"
    results.write_text(json.dumps(data))
    monkeypatch.setattr(mod, "RESULTS", results)
    assert mod.main() == 2
    out = capsys.readouterr().out
    assert "REFUSED" in out and "get worse" not in out


@pytest.mark.parametrize(("field", "value"), [("cut_hz", 0), ("cut_hz", -400), ("freqs", 0)])
def test_nonpositive_frequency_refuses_before_estimating_a_corner(
        mod, tmp_path, monkeypatch, capsys, field, value):
    data = json.loads(mod.RESULTS.read_text())
    row = next(r for r in data["response-diva"] if r["cut_hz"] == 400)
    if field == "freqs":
        row[field][0] = value
    else:
        row[field] = value
    results = tmp_path / "results.json"
    results.write_text(json.dumps(data))
    monkeypatch.setattr(mod, "RESULTS", results)
    assert mod.main() == 2
    out = capsys.readouterr().out
    assert "missing or misidentified response curve" in out
    assert "REFUSED" in out and not mod.checked and "get worse" not in out


def test_populated_results_still_checks_and_holds(mod, capsys):
    # the good case: the committed fixture drives exactly the two checks
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("      OK  ") == 2
    assert "every stated invariant holds" in out
    assert "4 of 8 configurations get worse" in out


def test_a_failed_invariant_still_fails(mod, monkeypatch, capsys):
    # Keep all curves and make the repaired corner wrong at one grid point.
    real_estimator = mod.rc.filt_corner

    def shifted_corner(cut):
        read = real_estimator(cut)
        if cut != 6400:
            return read

        def shifted(freqs, gains):
            result = read(freqs, gains)
            return mod.am.Estimate(2 * result.value, True, "", result.detail)

        return shifted

    monkeypatch.setattr(mod.rc, "filt_corner", shifted_corner)
    assert mod.main() == 1
    out = capsys.readouterr().out
    assert "FAIL  over 400 Hz" in out and "every stated invariant holds" not in out
