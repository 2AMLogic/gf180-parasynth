"""Known-answer qualification of the M1A attack measurement (analysis v3).

Every signal's 10-90 % attack is known by construction (attack_known_answer),
independently of both synthesizers. Each control below is a defect that must
turn a check red."""
import json
import math

import pytest

import attack_fit_v3
import attack_known_answer as kaa
import measure_mono_m1a_reference as v2
import mono_m1a_score as bass
import qualify_m1a_attack as qual

REPORT = json.loads((qual.OUT / "qualification.json").read_text())
SUITE_V3 = json.loads((qual.OUT / "suite-v3.json").read_text())
SUITE_V2 = json.loads((qual.OUT / "suite-v2.json").read_text())

# A live slice: realistic cases (ADS decay, -60 dB noise, 16-bit) across
# shapes inside and outside the fit's family, both notes, steady and moving
# spectra, whose v3 fits the committed suite put inside the domain.
LIVE = [dict(shape=s, t1090_ms=d, spectrum=sp, note=n, phase=ph, decay_s=.08,
             sustain=.75, noise_db=-60., seed=7)
        for s, d, sp, n, ph in (("p1", 3.0, "steady-dull", 36, 0.0),
                                ("p3", 1.0, "steady-bright", 43, 0.37),
                                ("rc3", 5.0, "steady-dull", 43, 0.0),
                                ("p0.5", 2.0, "steady-bright", 36, 0.37))]


def test_known_answers_are_analytic():
    assert kaa.power_kfrac(1.0) == pytest.approx(0.8)
    assert kaa.power_kfrac(4.0) == pytest.approx(0.9 ** .25 - 0.1 ** .25)
    t = [i / 1e6 for i in range(200001)]                  # 0..0.2 s at 1 us
    import numpy as np
    t = np.asarray(t)
    for shape in kaa.SHAPES.values():
        env = kaa.envelope(t, 0.01, 4.0, shape)
        t10 = t[np.argmax(env >= 0.1)]
        t90 = t[np.argmax(env >= 0.9)]
        assert (t90 - t10) * 1000 == pytest.approx(4.0, abs=2e-3), shape


def test_v3_live_slice_inside_domain_within_bound():
    domain = REPORT["v3"]["domain"]
    for case in LIVE:
        row = kaa.measure_case(case, "v3")
        fit = {"attack_10_90_ms": row["measured_1090_ms"],
               "explained_ratio": row["explained_ratio"],
               "search_boundary": row["search_boundary"]}
        cover = qual.covering_row(domain, fit)
        assert cover is not None, row
        assert abs(row["error_ms"]) <= cover["max_abs_error_ms"] + 1e-9, row


def test_committed_domain_reproduces_from_committed_suite():
    assert qual.derive_domain(SUITE_V3["rows"]) == REPORT["v3"]["domain"]
    assert qual.derive_domain(SUITE_V2["rows"]) == REPORT["v2"]["domain"]
    assert all(r["max_abs_error_ms"] <= qual.BOUND_MS for r in REPORT["v3"]["domain"])


def test_start_red_v2_fails_the_realistic_suite():
    """v2's own claimed domain (known 10-90 0.5-20 ms, any explained ratio
    above its 0.6 refusal) does NOT hold on the realistic suite."""
    rows = [r for r in qual.measurable(SUITE_V2["rows"]) if 0.5 <= r["measured_1090_ms"] <= 20]
    assert max(abs(r["error_ms"]) for r in rows) > 4.0
    # and a single live case: RC attack with ADS decay, steady spectrum
    case = dict(shape="rc5", t1090_ms=10.0, spectrum="steady-dull", note=36, phase=0.0,
                decay_s=.08, sustain=.75, noise_db=-60., seed=1)
    assert abs(kaa.measure_case(case, "v2")["error_ms"]) > 2.0


def test_control_biased_estimator_fails_qualification():
    """A 15 % slow estimator must not qualify: the domain rule finds no
    row within the bound over the same known answers."""
    def biased(rows, gain=1.15):
        out = []
        for r in rows:
            r = dict(r)
            if "refused" not in r:
                r["measured_1090_ms"] *= gain
                r["error_ms"] = r["measured_1090_ms"] - r["t1090_ms"]
            out.append(r)
        return out
    honest = qual.derive_domain(SUITE_V3["rows"])
    broken = qual.derive_domain(biased(SUITE_V3["rows"]))
    width = lambda d: max((r["hi_ms"] - r["lo_ms"] for r in d), default=0)   # noqa: E731
    assert width(broken) < width(honest)
    # live: the biased estimator exceeds the bound on the live slice
    def slow(*a, **k):
        r = dict(attack_fit_v3.attack_fit_v3(*a, **k))
        r["attack_10_90_ms"] *= 1.3
        return r
    errors = [abs(kaa.measure_case(c, slow)["error_ms"]) for c in LIVE]
    assert max(errors) > qual.BOUND_MS


def test_control_ramp_is_not_the_10_90_quantity():
    """v2's gate tested ramp_ms. At p = 4 on the search minimum the ramp is
    0.667 ms -- inside v2's '0.5-20 ms' -- while the reported 10-90 value is
    0.274 ms. The v3 gate reads the 10-90 value and the boundary flag."""
    fit = {"shape_p": 4.0, "ramp_ms": 32 / 48, "attack_10_90_ms": 32 / 48 * kaa.power_kfrac(4.0),
           "explained_ratio": 0.99, "search_boundary": "minimum"}
    assert 0.5 <= fit["ramp_ms"] <= 20.0                          # v2 range check passed it
    assert bass.attack_fit_qualified(fit)                          # v3: unqualified
    # a p = 4 fit whose RAMP is in 0.5-6 ms but whose 10-90 value is outside
    long = {**fit, "ramp_ms": 5.5, "attack_10_90_ms": 5.5 * kaa.power_kfrac(4.0),
            "search_boundary": None}
    assert bass.attack_fit_qualified(long) is None                 # 2.27 ms 10-90: inside
    wide = {**long, "ramp_ms": 16.0, "attack_10_90_ms": 16.0 * kaa.power_kfrac(4.0)}
    assert wide["attack_10_90_ms"] > 6.0 and bass.attack_fit_qualified(wide)


@pytest.mark.parametrize("boundary", ["minimum", "maximum"])
def test_search_boundary_is_never_qualified(boundary):
    fit = {"attack_10_90_ms": 2.0, "explained_ratio": 1.0, "search_boundary": boundary}
    assert "search" in bass.attack_fit_qualified(fit)


def test_low_explained_ratio_is_unqualified_outside_its_row():
    ok = {"attack_10_90_ms": 2.0, "explained_ratio": 0.99, "search_boundary": None}
    assert bass.attack_fit_qualified(ok) is None
    assert bass.attack_fit_qualified({**ok, "explained_ratio": 0.72})


def test_stale_suite_refuses(monkeypatch):
    real = bass.sha
    monkeypatch.setattr(bass, "sha", lambda p: "0" * 64 if str(p).endswith("attack_fit_v3.py") else real(p))
    with pytest.raises(bass.Refused, match="stale"):
        bass.qualified_attack_domain()


def test_v3_reports_its_search_boundary():
    case = dict(shape="p1", t1090_ms=0.1, spectrum="steady-dull", note=43, phase=0.0)
    x, on, off = kaa.render(case)
    r = attack_fit_v3.attack_fit_v3(x, kaa.SR, kaa.note_hz(43), on, off)
    assert r["search_boundary"] == "minimum"
    assert math.isclose(r["ramp_ms"], 32 / 48)
