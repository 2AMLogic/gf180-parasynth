"""Corpus-completeness controls for the 808 gate (#1263). All WAVs are synthetic."""
from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
from test_perceptual_gate import g, strike  # noqa: E402


def write_take(refs: pathlib.Path, rel: str, hz: float) -> None:
    samples, sr = strike(f=hz, noise=0.05, sr=44100)
    path = refs / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, sr, np.round(16000 * samples).astype(np.int16))


def write_bd_corpus(refs: pathlib.Path) -> tuple[str, list[str]]:
    target = g.target_rel("BD")
    neighbours = g.neighbours("BD", target)
    write_take(refs, target, 56)
    for i, rel in enumerate(neighbours):
        write_take(refs, rel, 56 if i == 0 else 70)
    return target, neighbours


@pytest.mark.parametrize("missing", ["nearest", "farthest", "all"])
def test_incomplete_bd_corpus_cannot_grade_a_candidate(tmp_path, monkeypatch, missing):
    target, neighbours = write_bd_corpus(tmp_path)
    T = g.Target(*g.load_wav(tmp_path / target), "BD", target)
    candidate = T.distance(*g.candidate_path(*g.load_wav(tmp_path / neighbours[1])))
    complete = g.bar_for("BD", tmp_path, T)
    assert complete["kind"] == "808-neighbour"
    assert complete["from"] == neighbours[0]
    assert g.verdict(candidate, complete["bar"])["verdict"] == "FAIL"

    removed = (
        neighbours[:1] if missing == "nearest"
        else neighbours[-1:] if missing == "farthest"
        else neighbours
    )
    for rel in removed:
        (tmp_path / rel).unlink()

    calls = []
    orig_distance = T.distance
    monkeypatch.setattr(T, "distance", lambda *a, **kw: calls.append(1) or orig_distance(*a, **kw))

    try:
        result = g.bar_for("BD", tmp_path, T)
    except g.Refused as exc:
        msg = str(exc)
        for rel in removed:
            assert str(tmp_path / rel) in msg
            assert rel in msg
        assert calls == [], "completeness must be checked before computing any distance"
        return
    grade = g.verdict(candidate, result["bar"])["verdict"]
    pytest.fail(f'{len(result["neighbours"])}/{len(neighbours)} neighbours, '
                f'{result["kind"]}, candidate {grade}; expected REFUSED')


def test_actual_single_take_keeps_weak_calibration(tmp_path):
    write_take(tmp_path, g.target_rel("CH"), 3300)
    res = g.bar_for("CH", tmp_path)
    assert res["kind"] == "WEAK-resampled-take"
    assert res["neighbours"] == {}


def test_directory_at_neighbour_path_still_refuses(tmp_path, monkeypatch):
    """Rule 8 defeating input: a directory satisfies Path.exists() while
    holding no WAV; completeness must refuse it before computing distances."""
    target, neighbours = write_bd_corpus(tmp_path)
    T = g.Target(*g.load_wav(tmp_path / target), "BD", target)
    bad = tmp_path / neighbours[-1]
    bad.unlink()
    bad.mkdir()

    calls = []
    monkeypatch.setattr(T, "distance", lambda *a, **kw: calls.append(1))
    with pytest.raises(g.Refused) as exc_info:
        g.bar_for("BD", tmp_path, T)
    assert str(bad) in str(exc_info.value)
    assert calls == []


def test_survey_requires_all_expected_neighbours_before_distances(tmp_path, monkeypatch, capsys):
    _, bd_neighbours = write_bd_corpus(tmp_path)
    write_take(tmp_path, g.target_rel("CH"), 3300)

    full = g.survey(tmp_path, ["CH", "BD"])
    assert set(full["BD"]["neighbours"]) == set(bd_neighbours)
    assert full["CH"]["neighbours"] == {}

    missing_rel = bd_neighbours[-1]
    (tmp_path / missing_rel).unlink()
    capsys.readouterr()

    calls = []
    orig_distance = g.Target.distance
    monkeypatch.setattr(g.Target, "distance",
                        lambda self, *a, **kw: calls.append(1) or orig_distance(self, *a, **kw))
    with pytest.raises(g.Refused) as exc_info:
        g.survey(tmp_path, ["CH", "BD"])
    assert str(tmp_path / missing_rel) in str(exc_info.value)
    assert calls == []

    out = tmp_path / "survey.json"
    rc = g.main(["survey", "--refs", str(tmp_path), "--sounds", "BD", "--out", str(out)])
    captured = capsys.readouterr()
    assert rc == exit_contract.REFUSED
    assert "REFUSED" in (captured.out + captured.err)
    assert str(tmp_path / missing_rel) in (captured.out + captured.err)
    assert not out.exists()
