"""Controls for the recovered conga instrument, including its false-green CLI."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_conga_body_spread as probe


def test_known_answer_control_checks_two_tones_without_our_model():
    rows = probe.validate_known_answer()
    assert len(rows) == 8
    assert max(abs(row["error_db"]) for row in rows) < 0.1


def test_check_refuses_an_empty_existing_reference_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["probe", "--check", "--refs", str(tmp_path)])
    assert probe.main() == 2


def test_check_fails_an_injected_band_direction_error(tmp_path, monkeypatch):
    real = probe.am.band_energy
    monkeypatch.setattr(probe.am, "band_energy", lambda *a, **kw: real(*a, **kw)[::-1])
    monkeypatch.setattr(sys, "argv", ["probe", "--check", "--refs", str(tmp_path)])
    assert probe.main() == 1


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 1.0])
def test_check_fails_invalid_or_drifted_historical_reproduction(value):
    report = {"known_answer": probe.validate_known_answer(),
              "reproduction": [{"delta_db": 0.0}, {"delta_db": value}]}
    assert probe.validation_status(report) == 1


def test_check_passes_qualified_controls():
    report = {"known_answer": probe.validate_known_answer(),
              "reproduction": [{"delta_db": 0.0}, {"delta_db": 0.0}]}
    assert probe.validation_status(report) == 0


@pytest.mark.parametrize("rows,code", [
    ([], 0),
    ([{"status": "unreadable"}, {"status": "REFUSED"}, {"best_match": "x"}], 0),
    ([{"status": "unreadable"}, {"status": "error", "error_type": "KeyError"}], 3),
])
def test_a_descent_defect_row_turns_the_run_red_but_a_stated_decision_does_not(rows, code):
    """#615: `error` rows were printed and then `main` returned 0."""
    assert probe.descent_status(rows) == code


def test_main_exits_nonzero_when_descent_records_a_defect(tmp_path, monkeypatch):
    """Through `main`, so the exit code is the shipped one and not the helper's."""
    floor = {k: 0.0 for k in ("lead_1ms_db", "lead_10ms_db", "sample_rate_44k_to_48k_db",
                              "quantisation_16bit_db", "window_10ms_db")}
    monkeypatch.setattr(probe, "validate_reproduction", lambda refdir: [], raising=True)
    monkeypatch.setattr(probe, "validation_status", lambda report: 0)
    monkeypatch.setattr(probe, "floor_for_these_signals",
                        lambda refdir: [dict(floor, file="f")])
    for name in ("measure_corpus", "measure_ours", "sensitivity", "windowed_alike"):
        monkeypatch.setattr(probe, name, lambda *a, **k: [])
    monkeypatch.setattr(probe, "descent_test", lambda refdir, cands: [
        {"file": "a.wav", "status": "error", "error_type": "TypeError", "why": "x"}])
    monkeypatch.setattr(sys, "argv", ["probe", "--refs", str(tmp_path),
                                      "--descent", str(tmp_path)])
    assert probe.main() == 3
