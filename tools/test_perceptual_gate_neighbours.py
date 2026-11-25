"""Corpus-completeness controls for the 808 gate. All WAVs are synthetic."""
import numpy as np
import pytest
from scipy.io import wavfile

from test_perceptual_gate import g, strike


def write_take(refs, rel, hz):
    samples, sr = strike(f=hz, noise=0.05, sr=44100)
    path = refs / rel
    path.parent.mkdir(exist_ok=True)
    wavfile.write(path, sr, np.round(16000 * samples).astype(np.int16))


@pytest.mark.parametrize("missing", ["nearest", "all"])
def test_incomplete_bd_corpus_cannot_grade_a_candidate(tmp_path, missing):
    target = g.target_rel("BD")
    neighbours = g.neighbours("BD", target)
    write_take(tmp_path, target, 56)
    for i, rel in enumerate(neighbours):
        write_take(tmp_path, rel, 56 if i == 0 else 70)
    T = g.Target(*g.load_wav(tmp_path / target), "BD", target)
    candidate = T.distance(*g.candidate_path(*g.load_wav(tmp_path / neighbours[1])))
    complete = g.bar_for("BD", tmp_path, T)
    assert complete["from"] == neighbours[0]
    assert g.verdict(candidate, complete["bar"])["verdict"] == "FAIL"

    for rel in (neighbours[:1] if missing == "nearest" else neighbours):
        (tmp_path / rel).unlink()
    try:
        result = g.bar_for("BD", tmp_path, T)
    except g.Refused as exc:
        assert neighbours[0] in str(exc)
        return
    grade = g.verdict(candidate, result["bar"])["verdict"]
    pytest.fail(f'{len(result["neighbours"])}/{len(neighbours)} neighbours, '
                f'{result["kind"]}, candidate {grade}; expected REFUSED')


def test_actual_single_take_keeps_weak_calibration(tmp_path):
    write_take(tmp_path, g.target_rel("CH"), 3300)
    assert g.bar_for("CH", tmp_path)["kind"] == "WEAK-resampled-take"
