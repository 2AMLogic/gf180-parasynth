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
    _write_takes(tmp_path, ["lc8/LC50.WAV", "hc8/HC50.WAV"])
    # Hold reproduction at its passing value to isolate the two-tone control.
    rows = probe.validate_reproduction(tmp_path)
    for row in rows:
        row.update(published_db=row["recomputed_db"], delta_db=0.0)
    monkeypatch.setattr(probe, "validate_reproduction", lambda refdir: rows)
    argv = ["--check", "--refs", str(tmp_path)]
    assert probe.main(argv) == 0
    real = probe.am.band_energy
    monkeypatch.setattr(probe.am, "band_energy", lambda *a, **kw: real(*a, **kw)[::-1])
    assert probe.main(argv) == 1


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
    _make_corpus(tmp_path)  # the inventory check is real; only rendering is stubbed
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


# --- #1309: an incomplete expected set must REFUSE, never shrink the spread ---
def _make_corpus(root, voices=("LC", "MC", "HC")):
    _write_takes(root, probe.expected_takes(voices))


def _write_takes(root, rels):
    from scipy.io import wavfile
    t = np.arange(8820) / 44100
    x = (np.exp(-t * 20) * np.sin(2 * np.pi * 200 * t) * 20000).astype(np.int16)
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(str(p), 44100, x)


def test_complete_corpus_measures_all_fifteen_takes(tmp_path):
    _make_corpus(tmp_path)  # positive control
    assert len(probe.measure_corpus(tmp_path)) == 15


def test_missing_voice_dir_and_a_non_anchor_take_are_named(tmp_path):
    import shutil
    _make_corpus(tmp_path)
    shutil.rmtree(tmp_path / "mc8")
    (tmp_path / "lc8" / "LC75.WAV").unlink()
    with pytest.raises(probe.InventoryRefused) as e:
        probe.measure_corpus(tmp_path)
    assert set(e.value.missing) == {"lc8/LC75.WAV", *probe.expected_takes(("MC",))}


def test_a_directory_at_a_wav_path_is_not_a_take(tmp_path):
    _make_corpus(tmp_path)
    (tmp_path / "hc8" / "HC10.WAV").unlink()
    (tmp_path / "hc8" / "HC10.WAV").mkdir()
    with pytest.raises(probe.InventoryRefused) as e:
        probe.measure_corpus(tmp_path)
    assert e.value.missing == ["hc8/HC10.WAV"]


def test_an_equal_count_substitute_with_another_name_does_not_stand_in(tmp_path):
    _make_corpus(tmp_path)
    (tmp_path / "hc8" / "HC10.WAV").rename(tmp_path / "hc8" / "HC99.WAV")
    with pytest.raises(probe.InventoryRefused) as e:
        probe.measure_corpus(tmp_path)
    assert e.value.missing == ["hc8/HC10.WAV"]


def test_an_lc_only_request_needs_only_lc_takes(tmp_path):
    _make_corpus(tmp_path, ("LC",))
    assert len(probe.measure_corpus(tmp_path, voices=("LC",))) == 5


def test_windowed_alike_refuses_a_missing_anchor(tmp_path):
    with pytest.raises(probe.InventoryRefused) as e:
        probe.windowed_alike(tmp_path, [])
    assert set(e.value.missing) == {"lc8/LC50.WAV", "hc8/HC50.WAV"}


def test_floor_refuses_a_missing_take(tmp_path):
    _make_corpus(tmp_path, ("LC", "HC"))
    (tmp_path / "lc8" / "LC00.WAV").unlink()
    with pytest.raises(probe.InventoryRefused):
        probe.floor_for_these_signals(tmp_path)


def _stub_controls(monkeypatch):
    monkeypatch.setattr(probe, "validate_reproduction", lambda refdir: [], raising=True)
    monkeypatch.setattr(probe, "validation_status", lambda report: 0)


@pytest.mark.parametrize("extra", [[], ["--descent"]])
def test_main_full_run_refuses_with_every_missing_path_and_no_statistics(
        tmp_path, monkeypatch, capsys, extra):
    _stub_controls(monkeypatch)
    _make_corpus(tmp_path)
    (tmp_path / "mc8" / "MC00.WAV").unlink()
    (tmp_path / "hc8" / "HC10.WAV").unlink()
    out = tmp_path / "r.json"
    argv = ["probe", "--refs", str(tmp_path), "--json", str(out)]
    if extra:
        argv += ["--descent", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", argv)
    assert probe.main() == 2
    err = capsys.readouterr().err
    assert "mc8/MC00.WAV" in err and "hc8/HC10.WAV" in err
    import json
    rep = json.loads(out.read_text())
    assert sorted(rep["refusal"]["missing"]) == ["hc8/HC10.WAV", "mc8/MC00.WAV"]
    assert not {"corpus", "spread", "sensitivity", "within_10pct"} & set(rep)


def test_check_with_a_directory_at_an_anchor_refuses_instead_of_crashing(
        tmp_path, monkeypatch):
    _make_corpus(tmp_path)
    (tmp_path / "lc8" / "LC50.WAV").unlink()
    (tmp_path / "lc8" / "LC50.WAV").mkdir()
    monkeypatch.setattr(sys, "argv", ["probe", "--check", "--refs", str(tmp_path)])
    assert probe.main() == 2


# --- review round 2 (#1322): preflight ordering, outcome code, order, iterator ---
import json as _json


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["probe", *argv])
    return probe.main()


@pytest.mark.parametrize("extra", [[], ["descent"]])
def test_full_mode_names_every_missing_take_even_with_an_absent_anchor(
        tmp_path, monkeypatch, capsys, extra):
    """No stubs: the real controls must not pre-empt the inventory."""
    _make_corpus(tmp_path)
    (tmp_path / "lc8" / "LC50.WAV").unlink()       # anchor
    (tmp_path / "mc8" / "MC00.WAV").unlink()       # non-anchor
    out = tmp_path / "r.json"
    argv = ["--refs", str(tmp_path), "--json", str(out)]
    if extra:
        argv += ["--descent", str(tmp_path)]
    rc = _run_main(monkeypatch, argv)
    err = capsys.readouterr().err
    rep = _json.loads(out.read_text())
    assert rc == 2 and rep["outcome_code"] == rc
    assert sorted(rep["refusal"]["missing"]) == ["lc8/LC50.WAV", "mc8/MC00.WAV"]
    assert "lc8/LC50.WAV" in err and "mc8/MC00.WAV" in err


def test_check_keeps_its_two_anchor_scope(tmp_path):
    _write_takes(tmp_path, ["lc8/LC50.WAV", "hc8/HC50.WAV"])
    out = tmp_path / "r.json"
    code = probe.main(["--check", "--refs", str(tmp_path), "--json", str(out)])
    report = _json.loads(out.read_text())
    assert code == report["outcome_code"] == 1  # synthetic anchors differ from Fischer's
    assert [r["file"] for r in report["reproduction"]] == ["lc8/LC50.WAV", "hc8/HC50.WAV"]
    assert all(r["delta_db"] is not None for r in report["reproduction"])


def test_check_names_the_missing_anchor_before_decoding_either(tmp_path, capsys):
    (tmp_path / "lc8").mkdir()
    (tmp_path / "lc8/LC50.WAV").write_bytes(b"broken header")
    out = tmp_path / "r.json"
    code = probe.main(["--check", "--refs", str(tmp_path), "--json", str(out)])
    assert "hc8/HC50.WAV" in capsys.readouterr().err
    report = _json.loads(out.read_text())
    assert code == report["outcome_code"] == 2
    assert report["refusal"]["missing"] == ["hc8/HC50.WAV"]
    with pytest.raises(probe.InventoryRefused) as exc:
        probe.validate_reproduction(tmp_path)
    assert exc.value.missing == ["hc8/HC50.WAV"]


@pytest.mark.parametrize("descent", [False, True])
@pytest.mark.parametrize("write_json", [False, True])
def test_nonexistent_root_names_all_missing_takes(tmp_path, capsys, descent, write_json):
    # The inventory in section 3 of the historical study, independent of the probe.
    expected = {f"{voice.lower()}8/{voice}{suffix}.WAV"
                for voice in ("LC", "MC", "HC") for suffix in ("00", "25", "50", "75", "10")}
    argv = ["--refs", str(tmp_path / "nope")]
    if descent:
        argv += ["--descent", str(tmp_path)]
    out = tmp_path / "r.json"
    if write_json:
        argv += ["--json", str(out)]
    code = probe.main(argv)
    err = capsys.readouterr().err
    assert code == 2
    assert {name for name in expected if name in err} == expected
    if write_json:
        report = _json.loads(out.read_text())
        assert report["outcome_code"] == code
        assert set(report["refusal"]["missing"]) == expected
        assert "spread" not in report


def test_complete_reports_keep_sorted_filename_order(tmp_path):
    _make_corpus(tmp_path)
    want = [f"lc8/LC{k}.WAV" for k in ("00", "10", "25", "50", "75")]
    got = [r["file"] for r in probe.measure_corpus(tmp_path, voices=("LC",))]
    assert got == want
    assert probe.expected_takes(("LC",)) == want


def test_measure_corpus_consumes_a_one_shot_voice_iterator_once(tmp_path):
    _make_corpus(tmp_path, ("LC",))
    assert len(probe.measure_corpus(tmp_path, iter(("LC",)))) == 5
