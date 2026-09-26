"""The D12A probe's own apparatus: the burst/tail estimator on signals whose
answer is closed form, and the mutants that must be caught. No reference
corpus and no drum render needed, so this runs in the fast suite."""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import clap_d12a_probe as cp  # noqa: E402
import run_case as rc  # noqa: E402


@pytest.mark.parametrize("sr", [44100, 48000])
@pytest.mark.parametrize("kw", [dict(), dict(b=0.05), dict(b=0.5, t_tau=0.090),
                                dict(n_bursts=4, period_s=0.012, tail_start_s=0.050)])
def test_scorer_ratio_equals_closed_form(sr, kw):
    x, q = cp.synthetic_clap(sr, **kw)
    got = cp.ratio_db(rc.prepare(x, sr), sr)
    assert abs(got - cp.synthetic_expected_db(q)) <= cp.KNOWN_ANSWER_TOL_DB


def test_every_mutant_is_killed_and_the_control_is_green():
    ka = cp.known_answer()
    assert ka["ok"], ka
    assert all(ka["mutants_killed"].values()), ka["mutants_killed"]


def test_split_mutant_is_caught_on_the_default_signal():
    x, q = cp.synthetic_clap(48000)
    y = rc.prepare(x, 48000)
    assert abs(cp._mutant_split40(y, 48000) - cp.synthetic_expected_db(q)) > 1.0


def test_tail_doubling_is_seen_at_its_closed_form_size():
    x, q = cp.synthetic_clap(44100)
    x2, q2 = cp.synthetic_clap(44100, b=0.4)
    d = cp.ratio_db(rc.prepare(x2, 44100), 44100) - cp.ratio_db(rc.prepare(x, 44100), 44100)
    want = cp.synthetic_expected_db(q2) - cp.synthetic_expected_db(q)
    assert abs(d - want) <= cp.KNOWN_ANSWER_TOL_DB
    assert want < -5.0


def test_overlapping_synthetic_is_refused():
    with pytest.raises(ValueError):
        cp.synthetic_clap(48000, n_bursts=4, period_s=0.012, tail_start_s=0.031)


@pytest.mark.parametrize("tau", [0.047, 0.090])
def test_tail_fit_recovers_a_known_time_constant(tau):
    x, _ = cp.synthetic_clap(48000, t_tau=tau, b=0.3)
    f = cp.tail_fit(rc.prepare(x, 48000), 48000)
    assert math.isclose(f["amp_tau_ms"], tau * 1e3, rel_tol=0.01)


def test_manifest_mismatch_refuses(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"abc")
    m = tmp_path / "m.sha256"
    m.write_text("0" * 64 + "  ./a.wav\n")
    with pytest.raises(cp.Refused):
        cp.verify_manifest(tmp_path, m)
