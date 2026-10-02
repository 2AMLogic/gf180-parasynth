"""The decay gate's qualification, pinned.

Four assertions and one pin. The fourth is the one that distinguishes a repair
from a goalpost: the repaired gate must still go red on a decay that really
did get faster.
"""
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import dc_blocker as db                                    # noqa: E402
import dc_t20_gate_qualification as q                      # noqa: E402


def test_a_global_t20_is_inflated_by_a_harmless_sub20_pedestal():
    r = q.case()
    # 4 % of clip energy at DC more than doubles the measured decay.
    assert r["global_error_pct"] >= 100.0
    assert r["global_t20_ms"] > 2.0 * r["true_t20_ms"]


def test_the_qualified_band_recovers_the_constructed_decay():
    r = q.case()
    assert abs(r["banded_error_pct"]) <= 1.5, r


def test_removing_the_pedestal_fails_the_global_gate_and_passes_the_banded_one():
    """The candidate's intended effect, read by both gates. The global one
    condemns a 3 % allowance on a change that is not a decay change."""
    r = q.case()
    assert abs(r["removal_global_pct"]) > 3.0
    assert abs(r["removal_banded_pct"]) <= 3.0


def test_the_repaired_gate_still_sees_a_real_decay_regression():
    """NOT a loosened gate. A genuinely 10 % faster decay must still read
    outside the 3 % allowance after the repair."""
    r = q.case()
    assert abs(r["faster_banded_pct"]) >= 3.0
    assert r["faster_banded_pct"] < 0.0        # faster, so shorter


def test_the_band_limit_is_a_no_op_on_a_pedestal_free_signal():
    r = q.case()
    assert abs(r["clean_delta_ms"]) <= q.T20_FRAME_MS


def test_this_file_and_dc_blocker_measure_t20_identically():
    """The duplication in this file is deliberate -- it must be able to condemn
    the estimator it qualifies -- so the two are pinned equal here instead."""
    rng = np.random.default_rng(20260165)
    for _ in range(4):
        x = rng.standard_normal(int(0.3 * q.SR)) * np.exp(
            -np.arange(int(0.3 * q.SR)) / 2000.0)
        a, b = q.t20_ms(x), db.t20_ms(x)
        assert (np.isnan(a) and np.isnan(b)) or a == b, (a, b)


def test_a_decay_that_fits_the_clip_is_clip_invariant():
    r = q.clip_invariance(q.TRUE_T20_MS)
    assert r["invariant"], r


def test_a_decay_that_does_not_fit_the_clip_is_rejected_by_the_doubling_test():
    """The cymbal's own defect, in closed form: a Schroeder integral normalised
    by the energy inside the clip reads SHORT when the voice is still ringing
    at the end. 464 ms at 0.60 s against 500 at 1.20."""
    r = q.clip_invariance(q.TRUNCATED_T20_MS)
    assert not r["invariant"], r
    assert r["short_t20_ms"] < r["long_t20_ms"], r


def test_the_doubling_test_is_the_criterion_the_probe_enforces():
    """Both legs, through main(): the probe must refuse if the criterion either
    rejects a case it must accept or accepts the truncation it must reject."""
    assert q.INVARIANCE_PCT > 0.0
    assert q.main([]) == 0


def test_the_qualification_refuses_rather_than_reports_when_its_premise_fails():
    """A pedestal of zero removes the premise. The probe must REFUSE (exit 1),
    not print a number that looks like a qualification."""
    assert q.main(["--json", "/dev/null"]) == 0        # the real case qualifies
    assert q.main(["--pedestal-frac", "1e-12"]) == 1   # premise removed
