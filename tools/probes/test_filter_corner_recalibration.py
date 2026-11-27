"""`tools/probes/filter_corner_recalibration.py` refuses when no invariant
was checked (#1469).

`main()` derives its exit from the module-level `fails` list, which only
`check()` appends to. With `docs/reference-compare-results.json` absent,
`real_curves()` returns before its two `check()` calls, `fails` stays empty,
and the probe printed `every stated invariant holds` with exit 0.

    python3 -m pytest tools/probes/test_filter_corner_recalibration.py -q
"""
import importlib.util
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
def mod():
    return _load()


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
    monkeypatch.setattr(mod, "ours", lambda: None)
    monkeypatch.setattr(mod, "reference_side", lambda: None)
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 2
    assert "every stated invariant holds" not in out
    assert "REFUSED" in out


def test_populated_results_still_checks_and_holds(mod, capsys):
    # the good case: the committed fixture drives exactly the two checks
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0
    assert out.count("      OK  ") == 2
    assert "every stated invariant holds" in out


def test_a_failed_invariant_still_fails(mod, monkeypatch, capsys):
    # and a populated bad case is still red: force one check false
    real_check = mod.check
    mod.check = lambda ok, what: real_check(ok and False, what)
    try:
        rc = mod.main()
    finally:
        mod.check = real_check
    assert rc == 1
    assert "every stated invariant holds" not in capsys.readouterr().out
