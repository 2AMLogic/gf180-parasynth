"""Controls for the recovered conga instrument, including its false-green CLI."""
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import measure_conga_body_spread as probe
import preparation_contract as pc
import run_case as rc
from test_preparation_contract import _fields, _ours, _prepare_pre_132, _reference


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


def test_the_probe_uses_run_cases_prepare_and_window_not_copies():
    """#636: a "verbatim" private copy went stale when #132 changed the original."""
    assert probe.prepare is rc.prepare
    assert probe.window is rc.window


def test_a_valid_unequal_pair_passes_the_contract():
    probe.check_pair(_reference(), 44100, _ours(), 48000, "LC", "LC50.WAV")


def test_the_pre_132_clamp_is_refused_by_the_contract(monkeypatch):
    """Control (#101): the clamp gave the reference 0.16 ms of lead and our
    render 1.00 ms. Injected into the probe's own prepare it must be REFUSED."""
    monkeypatch.setattr(probe, "prepare", _prepare_pre_132)
    with pytest.raises(pc.Refused) as why:
        probe.check_pair(_reference(), 44100, _ours(), 48000, "LC", "LC50.WAV")
    assert "lead" in _fields(why.value)


def test_a_record_cut_into_the_strike_is_refused_not_clamped():
    x = _reference()
    with pytest.raises(pc.Refused):
        probe.body_spectrum(x[rc._onset_index(x):], 44100, "LC")


def test_a_short_lead_is_padded_so_the_body_spectrum_is_unmoved_by_more_silence():
    x = _reference()
    a, _ = probe.body_spectrum(x, 44100, "LC")
    z = np.concatenate([np.zeros(441), x])
    b, _ = probe.body_spectrum(z, 44100, "LC")
    assert a is not None and b is not None
    assert abs(a - b) < 1e-6
