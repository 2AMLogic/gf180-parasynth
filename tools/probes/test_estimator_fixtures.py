#!/usr/bin/env python3
"""The fixture catalogue's own apparatus checks (#517).

`estimator_fixtures.axis_coverage` is a guard, and its docstring says out loud
what defeats it: it checks the SPREAD of the axis values a family DECLARES, not
that the family's signal responds to them. A family that recorded
`level=0.001` in `axes` and synthesised at 1.0 anyway would pass. This file is
the committed input that defeats it -- `docs/verification-rules.md` rule 8 --
plus the closed forms the catalogue's `truth` entries are built from, checked
against numerical integration rather than against themselves.

It is collected by `pytest tools/`, which `make verify` already runs, so these
checks ride the same turn as the rest of the fast set.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import estimator_fixtures as ef                                      # noqa: E402

SR = ef.SR


# ---------------------------------------------------------------------------
# A model-free signature of a record. numpy only, ON PURPOSE: the point of
# these tests is that the catalogue can be checked without consulting the
# module it exists to measure, so importing `audio_measure` here would be the
# same circularity the fixtures module is written to avoid.
# ---------------------------------------------------------------------------
def signature(x) -> tuple:
    """Enough of a record to tell it from a different one, with no estimator.

    Sensitive to each of the five swept axes by construction: `n` to duration,
    `rms` to level and SNR, `peak` to level, `first` to phase (the first
    sounding sample of a struck record is amp*sin(phase)) and `argmax` to
    frequency."""
    x = np.asarray(x, float)
    nz = np.flatnonzero(np.abs(x) > 0)
    first = float(x[nz[0]]) if len(nz) else 0.0
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return (len(x),
            round(float(np.sqrt(np.mean(x ** 2))), 12),
            round(float(np.max(np.abs(x))), 12),
            round(first, 12),
            int(np.argmax(mag)))


def _ofat_groups(fixtures, axis):
    """Fixtures grouped so that inside one group ONLY `axis` differs.

    The catalogue is one-factor-at-a-time, so a group is the set of records
    whose other four declared axes are identical. A group of one says nothing
    and is dropped."""
    groups = {}
    others = [a for a in ef.AXES if a != axis]
    for fx in fixtures:
        if fx.axes.get(axis) is None:
            continue
        key = tuple(repr(fx.axes.get(a)) for a in others)
        groups.setdefault(key, []).append(fx)
    return [g for g in groups.values() if len(g) > 1]


@pytest.fixture(scope="module")
def catalogue():
    return ef.catalogue()


# ---------------------------------------------------------------------------
# THE INPUT THAT DEFEATS `axis_coverage`
# ---------------------------------------------------------------------------
def test_declared_axes_move_the_signal(catalogue):
    """Every axis a family DECLARES must change the record it declares it on.

    This is the check `axis_coverage` cannot make. It compares records that
    differ in one declared axis and nothing else, and requires their
    signatures to differ: a family that wrote a value into `axes` without
    synthesising at it would produce two identical records under two different
    declared values, and `axis_coverage` would still call the axis swept."""
    inert = []
    compared = 0
    for kind in ef.FAMILIES:
        fx = [f for f in catalogue if f.kind == kind]
        for axis in ef.AXES:
            if (kind, axis) in ef.AXIS_NOT_APPLICABLE:
                continue
            for group in _ofat_groups(fx, axis):
                seen = {}
                for f in group:
                    s = signature(f.x)
                    compared += 1
                    if s in seen and seen[s] != f.axes[axis]:
                        inert.append(
                            f"{kind}/{axis}: declared {seen[s]!r} and "
                            f"{f.axes[axis]!r} produce the SAME record "
                            f"({f.label})")
                    seen[s] = f.axes[axis]
    assert compared > 100, (
        f"only {compared} records compared: this check lost its own "
        f"population, which would make it pass vacuously")
    assert not inert, "declared axes that do not move the signal:\n  " + \
        "\n  ".join(inert)


def test_the_defeating_input_is_actually_caught():
    """Start red: the same comparison, run against a family that LIES.

    A guard nobody has watched fail is not a guard (rule 1). This builds the
    exact pathology the test above is for -- a record that declares four
    different levels and synthesises one -- and requires the comparison to
    report it, so a later refactor that quietly made the comparison vacuous
    would be caught here rather than by inspection."""
    lying = []
    for level in (1.0, 0.1, 0.01, 0.001):
        core = ef.damped(220.0, 0.030, 1.0, int(0.4 * SR), SR, 0.3)   # not `level`
        x, i0, i1 = ef._struck(core, SR)
        lying.append(ef.Fixture("damped_sine", f"liar level={level:g}", x, SR,
                                dict(i0=i0, i1=i1),
                                dict(frequency=220.0, duration=0.4, phase=0.3,
                                     snr_db=float("inf"), level=level)))
    # `axis_coverage` sees four declared levels spanning 1000x and calls the
    # axis swept. That is the hole, recorded as a measurement rather than as a
    # worry -- and it is why the signature comparison above exists.
    rows = {(k, a): ok for k, a, _n, _s, ok, _note in ef.axis_coverage(lying)}
    assert rows[("damped_sine", "level")] is True

    seen, caught = {}, []
    for f in lying:
        s = signature(f.x)
        if s in seen and seen[s] != f.axes["level"]:
            caught.append((seen[s], f.axes["level"]))
        seen[s] = f.axes["level"]
    assert caught, "the signature comparison did not catch a family that " \
                   "declares a level it does not synthesise at"


def test_axis_coverage_refuses_a_degenerate_sweep():
    """Two values a per cent apart is not a sweep, and the guard says so."""
    fx = [ef.Fixture("damped_sine", f"f={f:g}", np.zeros(8), SR, {},
                     dict(frequency=f, duration=0.4, phase=0.3,
                          snr_db=float("inf"), level=1.0))
          for f in (220.0, 222.0)]
    rows = {(k, a): ok for k, a, _n, _s, ok, _note in ef.axis_coverage(fx)}
    assert rows[("damped_sine", "frequency")] is False
    assert rows[("damped_sine", "duration")] is False       # one value only


def test_a_not_applicable_entry_names_a_real_family_and_axis():
    for kind, axis in ef.AXIS_NOT_APPLICABLE:
        assert kind in ef.FAMILIES, kind
        assert axis in ef.AXES, axis


def test_every_issue_158_signal_type_has_exactly_one_family():
    assert set(ef.ISSUE_158_TYPES.values()) == set(ef.FAMILIES)
    assert len(ef.ISSUE_158_TYPES) == len(ef.FAMILIES) == 7


def test_the_catalogue_never_consults_the_module_it_measures():
    """The fixtures' independence, as a check rather than as a docstring.

    `CLAUDE.md`: 'An estimator calibrated on our own model is not validated.'
    Every `truth` entry must be a synthesis parameter or a closed form derived
    from one, which is only true as long as nothing here can read
    `audio_measure`."""
    src = (HERE / "estimator_fixtures.py").read_text()
    # the source NAMES audio_measure in its prose, which is the point of the
    # prose; what it must not do is import it. The first draft of this check
    # grepped for the name and failed on the docstring that explains why the
    # name is absent from the imports.
    imports = [ln for ln in src.splitlines()
               if ln.lstrip().startswith(("import ", "from "))]
    assert not [ln for ln in imports if "audio_measure" in ln], imports
    assert "audio_measure" not in {getattr(v, "__name__", "")
                                   for v in vars(ef).values()}
    assert not any(getattr(v, "__module__", "") == "audio_measure"
                   for v in vars(ef).values())


# ---------------------------------------------------------------------------
# The closed forms, against numerical integration
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("f,tau,phase", [(220.0, 0.030, 0.3), (56.0, 0.300, 1.9),
                                         (3450.0, 0.003, 0.0), (130.0, 0.010, 5.1)])
def test_damped_mean_square_matches_numerical_integration(f, tau, phase):
    """Against a FINE-GRID integral, not against the record's sample mean.

    Wrong-then-right, recorded because the difference is a real property of
    the catalogue and not a tolerance to be widened: `damped_mean_square` is
    the exact integral over [0, D], and the mean of `n` SAMPLES differs from it
    by the Euler-Maclaurin term (f(0) - f(D)) / 2n -- 0.39 % at f=130 Hz,
    tau=10 ms, D=0.4 s, where almost all the energy is in the first 40 ms and
    the endpoint term is a visible fraction of it. The first draft of this test
    compared against `np.mean(x**2)` at a 0.2 % tolerance and went red on the
    closed form being right."""
    dur = 0.4
    over = 32                                      # 32x oversampled trapezoid
    t = np.arange(int(dur * SR * over) + 1) / (SR * over)
    g = (np.exp(-t / tau) * np.sin(2 * math.pi * f * t + phase)) ** 2
    want = float(np.trapz(g, t) / dur)
    got = ef.damped_mean_square(f, tau, 1.0, phase, dur)
    assert abs(got / want - 1.0) < 1e-5, (got, want)
    # and the record's own sample mean is close to it, but NOT equal: the gap
    # is the discretisation, dominated by the endpoint term above, and it is
    # 0.39 % at (130 Hz, 10 ms, 0.4 s) and 7e-5 % at (3450 Hz, 3 ms, 0.4 s).
    # Bounded loosely on purpose -- the exact integral is what the suite uses
    # and the fine-grid assert above is what gates it.
    x = ef.damped(f, tau, 1.0, int(dur * SR), SR, phase)
    assert abs(float(np.mean(x ** 2)) / got - 1.0) < 0.01


def test_the_lazy_mean_square_is_not_good_enough():
    """Why `damped_mean_square` carries the oscillation term at all.

    The form that drops it -- mean(sin^2) = 1/2 -- is 4 % wrong inside this
    catalogue's own frequency sweep, which is larger than any tolerance the
    suite gates on. Recorded here so the exact integral cannot be 'simplified'
    back without something turning red."""
    f, tau, phase, dur = 56.0, 0.300, 1.9, 0.05
    n = int(dur * SR)
    exact = float(np.mean(ef.damped(f, tau, 1.0, n, SR, phase) ** 2))
    lazy = 0.5 * tau / 2.0 * (1.0 - math.exp(-2 * dur / tau)) / dur
    assert abs(lazy / exact - 1.0) > 0.02
    assert abs(ef.damped_mean_square(f, tau, 1.0, phase, dur) / exact - 1.0) < 2e-3


@pytest.mark.parametrize("f,tau,phase", [(220.0, 0.030, 1.9), (220.0, 0.030, 0.3),
                                         (90.0, 0.100, 0.0), (7100.0, 0.030, 5.1)])
def test_damped_peak_is_the_largest_sample_of_the_record(f, tau, phase):
    n = int(0.4 * SR)
    x = ef.damped(f, tau, 1.0, n, SR, phase)
    want = float(np.max(np.abs(x)))
    got, where = ef.damped_peak(f, tau, 1.0, phase)
    assert abs(got / want - 1.0) < 1e-3, (got, want, where)


def test_damped_peak_takes_the_t0_boundary_when_it_wins():
    """The candidate the first draft of `damped_peak` did not have.

    At phase 1.9 the record's largest excursion is its FIRST sample, not an
    interior extremum, and a truth that returned only the interior one
    declared 0.005 % of a clean record 'clipped' at 1.01x its own peak."""
    got, where = ef.damped_peak(220.0, 0.030, 1.0, 1.9)
    assert where == "t=0 boundary"
    assert abs(got - abs(math.sin(1.9))) < 1e-12
    assert ef.damped_peak(220.0, 0.030, 1.0, 0.3)[1] == "interior extremum"


def test_add_noise_puts_the_floor_at_the_requested_snr():
    n = int(0.3 * SR)
    core = ef.damped(220.0, 0.100, 1.0, n, SR, 0.3)
    x, i0, i1 = ef._struck(core, SR)
    for snr in (60.0, 40.0, 20.0):
        y, nrms = ef.add_noise(x, snr, 7, over=slice(i0, i1))
        ref = math.sqrt(float(np.mean(x[i0:i1] ** 2)))
        assert abs(20.0 * math.log10(ref / nrms) - snr) < 1e-9
        got = math.sqrt(float(np.mean((y - x) ** 2)))
        assert abs(got / nrms - 1.0) < 0.05


def test_add_noise_of_an_infinite_snr_is_the_identity():
    x = ef.damped(220.0, 0.1, 1.0, 1000, SR, 0.3)
    y, nrms = ef.add_noise(x, float("inf"), 1)
    assert nrms == 0.0
    assert np.array_equal(x, y)


def test_two_tone_leads_with_silence_by_default():
    """`band_energy`'s precondition, as a check on the synthesis rather than a
    paragraph about it: a struck record that begins at full amplitude
    manufactures a filter edge worth up to 10 dB."""
    x = ef.two_tone(220.0, 330.0, 0.1, 0.1, 1.0, 0.7, 0.2)
    assert np.all(x[:int(0.009 * SR)] == 0.0)
    assert not np.all(ef.two_tone(220.0, 330.0, 0.1, 0.1, 1.0, 0.7, 0.2,
                                  lead_ms=0.0)[:10] == 0.0)


def test_every_fixture_is_finite_and_sounding(catalogue):
    """The apparatus precondition for every check downstream: a record that is
    silent or holds a NaN would make an estimator's refusal look like a
    measurement."""
    for fx in catalogue:
        x = np.asarray(fx.x, float)
        assert np.all(np.isfinite(x)), fx.label
        assert float(np.max(np.abs(x))) > 0.0, fx.label
        assert fx.n == len(x)


def test_every_fixture_declares_the_five_axes_or_a_reason(catalogue):
    for fx in catalogue:
        for axis in ef.AXES:
            if fx.axes.get(axis) is None:
                assert (fx.kind, axis) in ef.AXIS_NOT_APPLICABLE, (fx.kind, axis)


def test_filtered_noise_reference_curve_is_the_filters_own_response():
    """The ground truth for the response-curve estimators is `freqz` of the
    SAME coefficients the noise was filtered with -- checked here against an
    independent route, the DFT of the filter's own impulse response."""
    for kind, fc, q, order in (("lowpass", 1200.0, 0.707, 2),
                               ("highpass", 600.0, 0.707, 4),
                               ("resonant", 900.0, 8.0, 2)):
        fx = ef._filtered_noise(kind, fc, q, order, 0.2, 0.5, float("inf"), 11)
        ir = fx.truth["ir"]
        spec = np.abs(np.fft.rfft(ir))
        f = np.fft.rfftfreq(len(ir), 1.0 / SR)
        db = 20.0 * np.log10(np.maximum(spec, 1e-30))
        worst = 0.0
        for p in (50.0, 200.0, 900.0, 4000.0):
            a = float(np.interp(p, f, db))
            b = float(np.interp(p, fx.truth["ref_freqs"], fx.truth["ref_mag_db"]))
            worst = max(worst, abs(a - b))
        assert worst < 0.5, (kind, worst)
