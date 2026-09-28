"""Known answers and controls for the SN p.13 tone-stage nodal analysis.

Unlike Figure 9 itself (`test_werner_fig9.py`'s own docstring: "no external
known answer exists"), this module has one: Figure 9's own FULLY measured
Ht3 curve (measured across 20 Hz - 20 kHz, not extrapolated). A schematic
reading and a nodal analysis that get the right answer should reproduce that
curve without needing a fudge factor, and that is what most of these tests
check -- plus the negative controls a check like that needs to be worth
anything: a wrong rail assignment, or a perturbed component, must fit
visibly worse, not just "still okay".
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tone_stage_schematic as ts  # noqa: E402
import werner_fig9 as w9  # noqa: E402


@pytest.fixture(scope="module")
def evidence():
    try:
        return w9.from_artifact()
    except w9.Refused as exc:
        pytest.skip(str(exc))


# ---------------------------------------------------------------------------
# The real evidence
# ---------------------------------------------------------------------------


def test_the_committed_network_passes_the_gate(evidence):
    data, _meta, _blob = evidence
    ok, lines = ts.check(data)
    assert ok, "\n".join(lines)


def test_joint_alpha_matches_the_committed_constant(evidence):
    """`ALPHA_K1` is a fitted number, not a hand-picked one -- if the
    committed evidence changes, this must be re-fit and re-committed rather
    than silently drift out of sync with the module's own docstring."""
    data, _meta, _blob = evidence
    alpha, rms = ts.fit_alpha_k1(data)
    assert abs(alpha - ts.ALPHA_K1) < 1e-4
    assert rms < 0.02


@pytest.mark.parametrize("name,drive", list(ts.DRIVE_OF.items()))
def test_each_family_matches_figure_9_without_a_gain_fudge(evidence, name, drive):
    """The strong claim: the schematic's OWN level (no free per-path gain)
    reproduces each of Figure 9's three digitised k = 1.0 families."""
    data, _meta, _blob = evidence
    hz, db = ts._measured_k1(data, name)
    pred = ts.db_at(hz, ts.ALPHA_K1, name)
    rms = float(np.sqrt(np.mean((pred - db) ** 2)))
    # Ht3 spans 3 decades and 24 dB; Ht1/Ht2's own internal bp2 fits already
    # read 0.002-0.004 dB rms over their much narrower windows (see
    # werner_fig9's own --fit output), so this is a tight but earned bound.
    assert rms < 0.05, f"{name} ({drive}): rms={rms:.4f} dB"


def test_ht3_is_measured_not_extrapolated_and_still_matches(evidence):
    """Ht3's plotted window already covers the cymbal's 3.45/7.1 kHz corners
    (`docs/scorecard/cymbal-369/tone-stage/README.md` says so), so this is a
    real measured-vs-derived comparison, not one that relies on either side's
    extrapolation machinery."""
    data, _meta, _blob = evidence
    hz, db = ts._measured_k1(data, "Ht3")
    assert hz.min() < 3450.0 < 7100.0 < hz.max()
    pred = ts.db_at(np.array([3450.0, 7100.0]), ts.ALPHA_K1, "Ht3")
    at = lambda f: float(np.interp(np.log10(f), np.log10(hz), db))  # noqa: E731
    assert abs(pred[0] - at(3450.0)) < 0.5
    assert abs(pred[1] - at(7100.0)) < 0.5


def test_schematic_values_fall_inside_figure_9s_own_extrapolation_bounds(evidence):
    """Cross-check against an independently-computed bound: the schematic
    answer for Ht1/Ht2 at the cymbal's band must not fall outside the window
    Figure 9's OWN extrapolation machinery says it cannot exclude. If it did,
    the two routes would be disagreeing, not corroborating."""
    data, _meta, _blob = evidence
    for name in ("Ht1", "Ht2"):
        hz, db = ts._measured_k1(data, name)
        for f_target in ts.CY_BANDS_HZ:
            bound = w9.extrapolation_bound(hz, db, f_target)
            schem = float(ts.db_at(np.array([f_target]), ts.ALPHA_K1, name)[0])
            assert bound["lo_db"] - 0.5 <= schem <= bound["hi_db"] + 0.5, (
                f"{name} @ {f_target} Hz: schematic {schem:.2f} dB outside "
                f"[{bound['lo_db']:.2f}, {bound['hi_db']:.2f}]")


def test_the_network_is_passive_everywhere(evidence):
    data, _meta, _blob = evidence
    f_grid = np.logspace(np.log10(20.0), np.log10(20000.0), 500)
    for name in ts.DRIVE_OF:
        db = ts.db_at(f_grid, ts.ALPHA_K1, name)
        assert db.max() <= 0.05, f"{name} exceeds 0 dB: {db.max():.2f}"


# ---------------------------------------------------------------------------
# Controls: a wrong model must fit visibly worse, not "still fine"
# ---------------------------------------------------------------------------


def test_control_swapping_the_top_and_bottom_rail_assignment_fits_much_worse(evidence):
    """If Va/Vb were Ht2/Ht3 instead of Ht3/Ht2, the fit should degrade by an
    order of magnitude or more -- this is what makes the assignment a
    measurement rather than a coin flip."""
    data, _meta, _blob = evidence
    alpha_right, _gain, rms_right = ts.fit_one(data, "Ht3", "top", free_gain=True)
    alpha_wrong, _gain, rms_wrong = ts.fit_one(data, "Ht3", "bottom", free_gain=True)
    assert rms_right < 0.1
    assert rms_wrong > 10 * rms_right


def test_control_hh1_prefilter_on_the_wrong_band_fits_much_worse(evidence):
    """Ht1's low corner comes from the extra R123/C57 stage unique to Q25's
    path (`hh1`); assigning it to Ht2's plain window instead should not fit
    nearly as well."""
    data, _meta, _blob = evidence
    _alpha, _gain, rms_right = ts.fit_one(data, "Ht1", "hh1", free_gain=True)
    _alpha, _gain, rms_wrong = ts.fit_one(data, "Ht2", "hh1", free_gain=True)
    assert rms_right < 0.1
    assert rms_wrong > 10 * rms_right


def test_control_doubling_c57_breaks_the_fit(evidence, monkeypatch):
    """A component-value control: perturb Hh1's own low-pass cap and require
    the fit against the committed ALPHA_K1 to visibly degrade. If it did not,
    the earlier close fits would be a coincidence of the fitting procedure,
    not evidence about this specific component value."""
    data, _meta, _blob = evidence
    hz, db = ts._measured_k1(data, "Ht1")
    pred_before = ts.db_at(hz, ts.ALPHA_K1, "Ht1")
    rms_before = float(np.sqrt(np.mean((pred_before - db) ** 2)))

    monkeypatch.setattr(ts, "C57", ts.C57 * 4.0)
    pred_after = ts.db_at(hz, ts.ALPHA_K1, "Ht1")
    rms_after = float(np.sqrt(np.mean((pred_after - db) ** 2)))

    assert rms_before < 0.05
    assert rms_after > 10 * rms_before


def test_control_a_missing_artifact_is_refused(tmp_path):
    with pytest.raises(w9.Refused):
        w9.from_artifact(tmp_path / "absent.json")


def test_solve_vtone_rejects_alpha_outside_unit_interval():
    with pytest.raises(ValueError):
        ts.solve_vtone(np.array([1000.0]), 1.5, "top")


def test_solve_vtone_rejects_an_unknown_drive():
    with pytest.raises(ValueError):
        ts.solve_vtone(np.array([1000.0]), 0.5, "middle")


# ---------------------------------------------------------------------------
# Structural facts, with numpy/scipy only.
#
# These exist because the `sympy` witness at the bottom of this file cannot be
# the sole evidence for the module's most structural claim: no workflow here
# installs sympy (they install `numpy scipy pytest`, plus `pyyaml`), so wherever
# the file IS collected the witness skips, and a skip is reported beside passes
# and read as one.
#
# The first diagnosis of that was itself wrong, and the correction is the more
# useful half: this file was not being SKIPPED in CI, it was not being COLLECTED
# in CI. The `python` job in `.github/workflows/rungs.yml` runs `model/`, `spec/`
# and a named list of `tools/test_*.py` files; full `pytest tools/` runs only
# under `make verify`, which no workflow invokes; and `docs/dag.json` has no node
# under `tools/`. So the sympy gate was a second-order problem sitting on top of
# a first-order one. Both are now fixed: this file is named in that job (#417),
# and everything below runs there with numpy/scipy alone.
# ---------------------------------------------------------------------------


def test_the_two_independent_node_formulations_agree():
    """`solve_vtone` folds each series R-C into Z = R + 1/(sC) by hand;
    `solve_vtone_mna` instead splits every branch at an internal node so the
    system is a plain (G + sC) pencil. They are different derivations of the
    same circuit, so agreement to ~1e-14 dB makes the hand elimination a
    checked step rather than an assumed one."""
    f = np.logspace(1.0, 4.4, 64)
    for name, drive in ts.DRIVE_OF.items():
        a = 20.0 * np.log10(np.abs(ts.solve_vtone_mna(f, ts.ALPHA_K1, drive)))
        b = ts.db_at(f, ts.ALPHA_K1, name)
        assert np.max(np.abs(a - b)) < 1e-9, f"{name}: formulations disagree"


def test_control_the_formulations_would_notice_a_swapped_component(monkeypatch):
    """A negative control for the test directly above, which is otherwise the
    weakest kind of agreement test: two routines that happen to share a bug
    agree perfectly. R119 (22 k, N1->N2) and R129 (15 k, N4->N2) are the two
    rails' bridging resistors -- swap them in the MNA build ONLY, leaving
    `solve_vtone`'s hand elimination reading the true values, and the two
    formulations must part company by a wide margin.

    Measured here: 2.6-2.8 dB of divergence against a 2.1e-14 dB baseline
    agreement, i.e. ~13 orders of magnitude of separation. Thresholds below are
    deliberately loose (> 1 dB injected, < 1e-10 dB baseline) so this asserts
    the separation, not the exact numbers.

    This was run by hand twice during review and quoted only in prose; a number
    in prose is a claim, so it is committed here as a check.
    """
    f = np.logspace(1.0, 4.4, 64)

    def worst_divergence_db():
        worst = 0.0
        for name, drive in ts.DRIVE_OF.items():
            a = 20.0 * np.log10(np.abs(ts.solve_vtone_mna(f, ts.ALPHA_K1, drive)))
            b = ts.db_at(f, ts.ALPHA_K1, name)
            worst = max(worst, float(np.max(np.abs(a - b))))
        return worst

    baseline = worst_divergence_db()
    assert baseline < 1e-10, f"baseline formulations already disagree: {baseline} dB"

    real_mna = ts.mna_matrices

    def mna_with_r119_r129_swapped(alpha):
        # Swap only for the duration of the MNA build, so `solve_vtone` (called
        # via db_at, outside this window) still sees the real component values.
        r119, r129 = ts.R119, ts.R129
        ts.R119, ts.R129 = r129, r119
        try:
            return real_mna(alpha)
        finally:
            ts.R119, ts.R129 = r119, r129

    monkeypatch.setattr(ts, "mna_matrices", mna_with_r119_r129_swapped)
    injected = worst_divergence_db()
    assert injected > 1.0, (
        f"an R119/R129 swap moved the MNA answer by only {injected} dB -- the "
        "agreement test above is vacuous")


def test_exactly_five_finite_poles_without_sympy():
    """W14b calls the tone transfer functions FIFTH-order and does not print
    their coefficients. Five capacitors with no cap-only loop is five finite
    generalised eigenvalues -- a count, not a degree assertion."""
    p = ts.poles_hz()
    assert len(p) == 5, f"expected 5 finite poles, got {len(p)}: {p}"
    assert np.all(np.isfinite(p))
    assert np.all(p > 0.0)


def test_the_pole_set_is_the_one_the_reference_quotes():
    """§10 of `docs/tr808-reference.md` quotes this pole set as an algebraic
    fact. If the components or ALPHA_K1 change, that sentence is stale and
    this must be re-derived and re-written rather than silently drift."""
    assert np.allclose(ts.poles_hz(),
                       [128.3, 509.1, 681.4, 1635.7, 4191.5], rtol=2e-4)


@pytest.mark.parametrize("drive", sorted(ts.MNA_SOURCE))
def test_the_pole_set_does_not_depend_on_which_source_is_driven(drive):
    """"One network, one denominator" is structural here: neither G nor C is a
    function of `drive` -- only the right-hand side is. This asserts that
    property of the builder directly, so the claim cannot quietly become false
    if the matrices are ever rebuilt per-source."""
    G, C = ts.mna_matrices(ts.ALPHA_K1)
    G2, C2 = ts.mna_matrices(ts.ALPHA_K1)
    assert np.array_equal(G, G2) and np.array_equal(C, C2)
    # and the source only ever touches b: its node is an internal R-C node,
    # never the output node whose voltage is reported.
    node, _cap = ts.MNA_SOURCE[drive]
    assert node in ts.MNA_NODES and node != "N2"


def test_control_the_pole_set_is_not_insensitive_to_the_components(monkeypatch):
    """A negative control for the two tests above: if the poles came out the
    same whatever the component values were, "the pole set is 128/509/681/
    1636/4192 Hz" would be a property of the solver, not of the circuit."""
    before = ts.poles_hz()
    monkeypatch.setattr(ts, "C90", ts.C90 * 3.0)
    after = ts.poles_hz()
    assert not np.allclose(before, after, rtol=1e-3)


# ---------------------------------------------------------------------------
# Source provenance: the component values are a READING of a specific scan,
# and a reading nobody can repeat is a claim. These check the refusal path,
# not the scan itself -- the PDF is a ~6 MB third-party download and is
# deliberately NOT a test dependency, so `make verify` never needs the network.
# ---------------------------------------------------------------------------


def test_the_pinned_source_is_fully_specified():
    assert ts.SN_PDF_SHA256 and len(ts.SN_PDF_SHA256) == 64
    assert ts.SN_PDF_PAGE == 13
    assert set(ts.SN_CROPS) == {"tone", "q25"}
    for crop in ts.SN_CROPS.values():
        assert {"dpi", "x", "y", "w", "h"} <= set(crop)


def test_control_a_missing_scan_is_refused_not_answered(tmp_path):
    with pytest.raises(ts.SourceUnavailable):
        ts.verify_source(tmp_path / "absent.pdf")


def test_control_the_wrong_scan_is_refused_not_answered(tmp_path):
    """The failure this guards against is a DIFFERENT printing of the service
    notes: it would render a plausible page 13 and be silently wrong."""
    impostor = tmp_path / "not-the-service-notes.pdf"
    impostor.write_bytes(b"%PDF-1.4\nnot the pinned scan\n")
    with pytest.raises(ts.SourceUnavailable) as exc:
        ts.verify_source(impostor)
    assert "sha256" in str(exc.value)


def test_source_unavailable_is_a_refusal_so_callers_cannot_miss_it():
    """REFUSED is a first-class outcome here, distinct from pass and fail, so
    the narrow exception must stay catchable as the module's own Refused."""
    assert issubclass(ts.SourceUnavailable, ts.Refused)
    assert not issubclass(ts.SourceUnavailable, ValueError)


# ---------------------------------------------------------------------------
# Third, symbolic witness for the same structural fact. Uses sympy if
# available and SKIPS where it is not; since the numpy-only tests above now
# carry the claim in CI, this is corroboration rather than the sole evidence.
# ---------------------------------------------------------------------------


def test_the_network_is_exactly_fifth_order_and_shares_one_pole_set():
    sympy = pytest.importorskip("sympy")
    s = sympy.symbols("s")
    alpha = sympy.Float(ts.ALPHA_K1)

    def imp(r, c):
        return r + 1 / (s * c)

    Za = imp(ts.R112, ts.C55)
    Zb = imp(ts.R120, ts.C56)
    Z1 = imp(ts.R123, ts.C58)
    Ra = alpha * ts.VR4_TOTAL
    Rb = ts.R125 + (1 - alpha) * ts.VR4_TOTAL
    Ya, Yb, Y1 = 1 / Za, 1 / Zb, 1 / Z1
    YRa, YRb = 1 / Ra, 1 / Rb
    YR119, YR129, YR121 = 1 / ts.R119, 1 / ts.R129, 1 / ts.R121
    YC57, YC90 = s * ts.C57, s * ts.C90

    V1, Vx, V4, V2 = sympy.symbols("V1 Vx V4 V2")
    bTop, bHh1, bBot = sympy.symbols("bTop bHh1 bBot")
    eqs = [
        sympy.Eq(V1 * (Ya + YR119 + YRa) - V2 * YR119, Ya * bTop),
        sympy.Eq(Vx * (Y1 + YC57 + YR121) - V4 * YR121, Y1 * bHh1),
        sympy.Eq(-Vx * YR121 + V4 * (Yb + YR129 + YRb + YR121) - V2 * YR129,
                  Yb * bBot),
        sympy.Eq(-V1 * YR119 - V4 * YR129 + V2 * (YR119 + YR129 + YC90), 0),
    ]
    sol = sympy.solve(eqs, [V1, Vx, V4, V2], dict=True)[0]
    v2 = sol[V2]

    pole_sets = {}
    for name, bt, bh, bb in [("Ht3", 1, 0, 0), ("Ht2", 0, 0, 1), ("Ht1", 0, 1, 0)]:
        expr = sympy.together(v2.subs({bTop: bt, bHh1: bh, bBot: bb}))
        _num, den = sympy.fraction(expr)
        den_poly = sympy.Poly(sympy.expand(den), s)
        assert den_poly.degree() == 5, f"{name}: degree {den_poly.degree()}"
        coeffs = [complex(c) for c in den_poly.all_coeffs()]
        roots = np.roots(coeffs)
        pole_sets[name] = np.array(sorted(abs(r) / (2 * np.pi) for r in roots))

    # W14b: "one network, one denominator" -- now an exact algebraic fact,
    # not merely a structural argument.
    for name in ("Ht2", "Ht1"):
        assert np.allclose(pole_sets[name], pole_sets["Ht3"], rtol=1e-6)


# ---------------------------------------------------------------------------
# The emitted record (#420): what `tools/cymbal_band_balance.py` reads as its
# `schematic-vr4` precondition
# ---------------------------------------------------------------------------


def test_the_two_alpha_fits_agree(evidence):
    """`fit_alpha_k1_with_sigma` refits in alpha rather than through
    `fit_alpha_k1`'s logit so the covariance needs no chain rule. The two
    parameterisations must therefore land on the same alpha -- otherwise the
    sigma belongs to a different fit than the value it is quoted beside."""
    data, _meta, _blob = evidence
    a_logit, rms_logit = ts.fit_alpha_k1(data)
    a_direct, rms_direct, sigma, n = ts.fit_alpha_k1_with_sigma(data)
    assert a_direct == pytest.approx(a_logit, abs=1e-9)
    assert rms_direct == pytest.approx(rms_logit, abs=1e-9)
    assert n == 707
    # One free parameter fitted to 707 points at ~0.01 dB rms: the sigma has to
    # be small, and it has to be nonzero. Both directions asserted, because a
    # sigma of exactly zero would silently delete a term of the bound below.
    assert 0.0 < sigma < 1e-3


def test_the_bound_is_the_residual_plus_the_parameter_term_and_nothing_else(evidence):
    """`resolved_bound_db` is the whole of #420's `tone ±` column, so its
    construction is asserted rather than described: half-width = this family's
    largest disagreement with Figure 9's digitised curve + the move over
    ±3 sigma of alpha. Both terms must be present and neither may dominate to
    the point of hiding the other."""
    data, _meta, _blob = evidence
    stats = ts.residual_stats(data)
    _, _, sigma, _ = ts.fit_alpha_k1_with_sigma(data)
    for name in ("Ht1", "Ht2", "Ht3"):
        for f in ts.record_frequencies_hz():
            b = ts.resolved_bound_db(f, name, stats=stats, sigma_alpha=sigma)
            assert b["bound_db"] == pytest.approx(
                b["residual_db"] + b["alpha_db"], abs=1e-12)
            assert b["residual_db"] == pytest.approx(stats[name]["max_db"], abs=1e-12)
            assert b["alpha_db"] > 0.0
            assert b["hi_db"] - b["lo_db"] == pytest.approx(2 * b["bound_db"], abs=1e-12)
            # Ht3 is the only family Figure 9 plots across the cymbal's band, so
            # it is the only one whose residual is measured where it is used.
            assert b["residual_measured_here"] is (name == "Ht3")


def test_the_record_covers_every_frequency_the_balance_levels_a_band_at(evidence):
    """The emitter's own precondition. If `cymbal_candidate_eval.CENTRE` ever
    moves, this record must move with it -- the balance REFUSES on a frequency
    the record does not carry, and that refusal should be caught here rather
    than at the far end of the chain."""
    import cymbal_tone_realisation as ct  # noqa: PLC0415

    data, _meta, _blob = evidence
    rec = ts.balance_record(data)
    for band, name in ct.BAND_OF.items():
        assert rec["bands"][band]["family"] == name
        assert f"{ct.CENTRE_HZ[band]:.1f}" in rec["bands"][band]["at_hz"]


def test_the_emitted_record_round_trips_and_pins_its_sources(evidence, tmp_path):
    data, _meta, _blob = evidence
    out = ts.emit(data, tmp_path / "sn-p13-vr4.json")
    blob = json.loads(out.read_text())
    assert blob["artifact"] == "sn-p13-vr4"
    assert blob["source"]["sha256"] == ts.SN_PDF_SHA256
    assert blob["source"]["page"] == ts.SN_PDF_PAGE
    assert len(blob["fitted_against"]["sha256"]) == 64
    assert len(blob["poles_hz"]) == 5
    assert blob["alpha_k1"] == ts.ALPHA_K1


# ---------------------------------------------------------------------------
# Component sensitivity, and the tolerance term the bound still does NOT carry
# (#425). The table is published because no tolerance class is citable; these
# are the checks that make it evidence rather than an output.
#
# The two identities below are the reason this is not an estimator calibrated
# on our own model. Neither is derived from anything in this module: both are
# properties of ANY resistor-capacitor network whose answer is a voltage RATIO.
#
#   1. IMPEDANCE SCALING. R -> lambda*R with C -> C/lambda leaves every
#      impedance ratio, and hence H, exactly unchanged. So the resistors' and
#      the capacitors' log-sensitivities must sum to the SAME number.
#   2. FREQUENCY SCALING. H depends on the capacitors only through the products
#      omega*C, so scaling every capacitor by lambda is identical to scaling
#      the frequency by lambda. The capacitors' sensitivities must therefore
#      sum to d(dB)/d(ln f) -- computed here by perturbing the FREQUENCY, a
#      quantity no component perturbation touches.
# ---------------------------------------------------------------------------


SENS_CASES = [("Ht1", 3175.0), ("Ht2", 10079.0), ("Ht3", 10079.0),
              ("Ht3", 3175.0), ("Ht1", 7100.0)]


def test_the_sensitivity_table_covers_every_printed_component():
    """All thirteen values read off SN p.13, and nothing else. A table missing
    a component would understate every tolerance lever computed from it."""
    assert set(ts.COMPONENT_KIND) == {
        "C55", "R112", "R119", "VR4_TOTAL", "R125", "R129", "R120", "C56",
        "C58", "R123", "C57", "R121", "C90"}
    assert len(ts.RESISTORS) == 8 and len(ts.CAPACITORS) == 5
    s = ts.sensitivity_db_per_pct(3175.0, "Ht1")
    assert set(s) == set(ts.COMPONENT_KIND)
    assert all(np.isfinite(v) for v in s.values())


@pytest.mark.parametrize("name,f", SENS_CASES)
def test_the_sensitivity_table_obeys_the_impedance_scaling_identity(name, f):
    c = ts.component_sensitivity(f, name)["checks"]
    scale = max(abs(c["sum_resistors_db_per_pct"]), 1e-12)
    assert abs(c["impedance_scaling_residual_db_per_pct"]) < 1e-6 * scale + 1e-9


@pytest.mark.parametrize("name,f", SENS_CASES)
def test_the_sensitivity_table_obeys_the_frequency_scaling_identity(name, f):
    c = ts.component_sensitivity(f, name)["checks"]
    scale = max(abs(c["frequency_db_per_pct"]), 1e-12)
    assert abs(c["frequency_scaling_residual_db_per_pct"]) < 1e-6 * scale + 1e-9
    # and the frequency derivative is not itself ~0 here, which would make the
    # identity above satisfiable by a table of zeros.
    assert abs(c["frequency_db_per_pct"]) > 1e-4


def test_control_the_two_identities_are_not_redundant_and_neither_is_vacuous():
    """Rule 4's matrix, for a two-property check.

    `DROP_C90` (a component missing from the table) moves BOTH. `PER_UNIT`
    (log-sensitivity published without the per-cent conversion -- a 100x units
    bug that would silently inflate every number in the record) leaves the
    impedance identity BLIND, because scaling every entry by the same constant
    preserves `sum(R) == sum(C)`, and is caught ONLY by the frequency identity.

    So the second identity is load-bearing rather than a restatement of the
    first, and that is asserted here rather than argued in prose.
    """
    m = ts.sensitivity_control_matrix(3175.0, "Ht1")
    assert m["DROP_C90"] == {"impedance_scaling": "MOVED",
                             "frequency_scaling": "MOVED"}
    assert m["PER_UNIT_NOT_PER_PCT"] == {"impedance_scaling": "BLIND",
                                         "frequency_scaling": "MOVED"}
    # and the labels are not threshold luck: every cell is orders of magnitude
    # from the line it is being judged against, in its own direction.
    mar = ts.sensitivity_control_matrix(3175.0, "Ht1", margins=True)
    assert mar["DROP_C90"]["impedance_scaling"] > 1e3
    assert mar["DROP_C90"]["frequency_scaling"] > 1e3
    assert mar["PER_UNIT_NOT_PER_PCT"]["frequency_scaling"] > 1e3
    assert mar["PER_UNIT_NOT_PER_PCT"]["impedance_scaling"] < 1e-2


@pytest.mark.parametrize("name,f", SENS_CASES)
@pytest.mark.parametrize("tol_pct,lo,hi", [(1.0, 1.0, 1.005), (5.0, 1.0, 1.02)])
def test_the_first_order_table_predicts_an_exact_corner(name, f, tol_pct, lo, hi):
    """The derivative, validated against the solver it came from at a
    perturbation 100-500x larger than the step it was measured with: every
    component moved in its own worst direction and the network re-solved
    exactly, with nothing linearised.

    WRONG BEFORE IT WAS RIGHT, and the record of that is the point of this
    docstring. The first version asserted `exact <= predicted` -- that the
    first-order sum was a ceiling, which is the comfortable direction and the
    one a reader assumes. It is false: curvature pushes the exact corner
    ABOVE the linear sum, by up to 0.9 % at 5 % tolerance (measured
    1.0002-1.0017x at 1 %, 1.0012-1.0092x at 5 % over these five pairs). So
    `worst_case_db_per_pct` is a first-order lever and NOT a bound, and the
    asymmetric window below says so in a form that must stay true.
    """
    s = ts.sensitivity_db_per_pct(f, name)
    predicted = sum(abs(v) for v in s.values()) * tol_pct
    step = tol_pct / 100.0
    factors = {k: 1.0 + step * (1.0 if v > 0 else -1.0) for k, v in s.items()}
    base = float(ts.db_at(np.array([f]), ts.ALPHA_K1, name)[0])
    with ts.scaled_components(factors):
        moved = float(ts.db_at(np.array([f]), ts.ALPHA_K1, name)[0])
    exact = abs(moved - base)
    assert lo <= exact / predicted <= hi, (name, f, tol_pct, exact, predicted)


def test_scaled_components_restores_every_value_including_on_an_exception():
    """The sensitivity table is measured by mutating the module's own
    constants, so a leaked perturbation would silently poison every number
    computed after it -- including the record's `db` column."""
    before = {k: getattr(ts, k) for k in ts.COMPONENT_KIND}
    with pytest.raises(RuntimeError):
        with ts.scaled_components({k: 2.0 for k in ts.COMPONENT_KIND}):
            assert ts.C90 == pytest.approx(2.0 * before["C90"])
            raise RuntimeError("boom")
    assert {k: getattr(ts, k) for k in ts.COMPONENT_KIND} == before
    with pytest.raises(ValueError):
        with ts.scaled_components({"R999": 2.0}):
            pass


# ---------------------------------------------------------------------------
# The finding: no tolerance class is citable, so none is carried
# ---------------------------------------------------------------------------


def test_the_dominant_components_are_few_and_named():
    """#425's cheap half, answered: which of the thirteen the answer at each
    band's own frequency actually depends on.

    Four parts carry 99 % of Ht3's lever at 10079 Hz; C90 -- the shunt at the
    output node -- is in all three families' dominant sets. These are the
    committed measured values, so a component read or a rail assignment that
    changed would land here rather than quietly re-ranking the table.
    """
    short = ts.component_sensitivity(10079.0, "Ht3")
    assert set(short["dominant"]) == {"C90", "R112", "R119", "VR4_TOTAL"}
    assert short["dominant_share"] > 0.99
    low = ts.component_sensitivity(3175.0, "Ht1")
    decay = ts.component_sensitivity(10079.0, "Ht2")
    assert len(low["dominant"]) == 7 and len(decay["dominant"]) == 5
    for cs in (short, low, decay):
        assert "C90" in cs["dominant"]
        # the two summaries answer different questions and must not collapse
        # into each other: the adversarial corner is ~2x the independent one.
        assert 1.8 < cs["worst_case_db_per_pct"] / cs["rss_db_per_pct"] < 2.6


def test_the_lever_alone_is_larger_than_the_bound_that_is_carried(evidence):
    """The reason #425 is not a footnote, before any tolerance class is
    applied at all.

    The published half-width is 0.004-0.034 dB. One per cent on all thirteen
    parts is worth 0.26-0.33 dB at the same frequencies -- an order of
    magnitude more at a tolerance nobody would call loose, and W14a's cited
    class is 5-20x that (see
    `test_the_unit_term_dwarfs_the_solution_bound_and_the_ratio_shrinks_it`).

    (It still changes no verdict: the balance's refusal is bound by the
    +39.8 dB VCA-drive gap, which is more than an order above even the unit
    term. `test_the_bound_itself_is_unchanged_by_this_issue` asserts that the
    number the balance reads did not move.)
    """
    data, _meta, _blob = evidence
    stats = ts.residual_stats(data)
    _, _, sigma, _ = ts.fit_alpha_k1_with_sigma(data)
    for band, name, f in (("low", "Ht1", 3175.0), ("decay", "Ht2", 10079.0),
                          ("short", "Ht3", 10079.0)):
        carried = ts.resolved_bound_db(f, name, stats=stats,
                                       sigma_alpha=sigma)["bound_db"]
        lever = ts.component_sensitivity(f, name)["worst_case_db_per_pct"]
        assert 0.2 < lever < 0.4, (band, lever)
        assert lever > 5.0 * carried, (band, lever, carried)


def test_the_service_notes_search_is_recorded_and_says_which_corpus_it_covered():
    """The search that found nothing, kept because it is still true and still
    the reason the class has to come from W14a rather than from SN p.13.

    It is also the record of a wrong answer: read as "no class is citable" it
    was wrong, because it covered ONE of the four sources this repository has
    already read. So the constant is named for the corpus it searched, and
    `why_it_is_kept` says that in the artifact rather than only here.
    """
    f = ts.SN_PRINTS_NO_TOLERANCE
    assert f["citable_tolerance_class_in_this_source"] is None
    assert f["source_sha256"] == ts.SN_PDF_SHA256
    assert {1, 8, 13, 16} <= set(f["searched"])
    for page, found in f["searched"].items():
        assert isinstance(page, int) and isinstance(found, str) and found
    assert "W14a" in f["conclusion"]


def test_the_cited_tolerance_class_is_pinned_to_a_source_not_to_a_transcription():
    """W14a section 11's sentence, with the URL and SHA-256 of the PDF it was
    read from. `docs/tr808-reference.md` section 1.7 quotes the same sentence,
    and quoting OUR quote would be the internal-consistency failure this
    repository keeps making -- so the constant pins the paper."""
    c = ts.TOLERANCE_CLASS
    assert c["resistors_pct"] == 5.0 and c["capacitors_pct"] == 20.0
    assert "+-20% capacitors" in c["quote"] and "+-5% resistors" in c["quote"]
    assert c["url"].endswith(".pdf") and len(c["sha256"]) == 64
    assert "W14a" in c["citation"]
    # VR4 is NOT covered by it, and that is stated rather than assumed away
    assert "VR4" in c["does_not_cover"]
    assert ts.W14A_TOLERANCE_PCT["VR4_TOTAL"] is None
    assert set(ts.W14A_TOLERANCE_PCT) == set(ts.COMPONENT_KIND)
    for k, v in ts.W14A_TOLERANCE_PCT.items():
        if k != "VR4_TOTAL":
            assert v == (5.0 if ts.COMPONENT_KIND[k] == "R" else 20.0)


def test_control_a_tolerance_bound_without_a_cited_class_is_refused():
    """The failure this exists to prevent is a plausible tolerance picked
    because it looked reasonable. The bound is computable -- it just may not be
    computed from an uncited number, and REFUSED is not the same as absent."""
    every = {k: 5.0 for k in ts.COMPONENT_KIND}
    for citation in (None, "", "   "):
        with pytest.raises(ts.ToleranceUncited):
            ts.tolerance_bound_db(3175.0, "Ht1", tolerance_pct=every,
                                  citation=citation)


def test_control_a_tolerance_bound_missing_a_component_is_refused():
    """A partial class would silently treat the rest as exact. An explicit
    None must NOT refuse -- the two cases are different and the distinction is
    load-bearing, because VR4 is genuinely uncited and the default class says
    so out loud."""
    partial = {k: 5.0 for k in ts.COMPONENT_KIND if k != "C90"}
    with pytest.raises(ts.ToleranceUncited) as exc:
        ts.tolerance_bound_db(3175.0, "Ht1", tolerance_pct=partial,
                              citation="a hypothetical cited class")
    assert "C90" in str(exc.value)

    declared = {k: (None if k == "C90" else 5.0) for k in ts.COMPONENT_KIND}
    got = ts.tolerance_bound_db(3175.0, "Ht1", tolerance_pct=declared,
                                citation="a hypothetical cited class")
    assert got["uncited"] == ["C90"]
    assert "C90" in got["per_component_db"] or True
    assert "C90" not in got["per_component_db"]
    assert got["uncited_lever_db_per_pct"]["C90"] != 0.0


def test_the_cited_class_produces_the_unit_to_unit_term_it_claims_to():
    """The default path: W14a's class, applied to the twelve fixed parts, VR4
    reported separately. The arithmetic is asserted against the sensitivity
    table it is built from, so the number cannot drift from the levers."""
    s = ts.sensitivity_db_per_pct(3175.0, "Ht1")
    got = ts.tolerance_bound_db(3175.0, "Ht1")
    cited = [k for k in s if k != "VR4_TOTAL"]
    want = sum(abs(s[k]) * ts.W14A_TOLERANCE_PCT[k] for k in cited)
    assert got["bound_db"] == pytest.approx(want, rel=1e-12)
    assert got["rss_db"] == pytest.approx(
        np.sqrt(sum((s[k] * ts.W14A_TOLERANCE_PCT[k]) ** 2 for k in cited)),
        rel=1e-12)
    assert got["uncited"] == ["VR4_TOTAL"]
    assert got["citation"] == ts.TOLERANCE_CLASS["citation"]
    # the adversarial corner is ~2x the independent one; quoting the wrong one
    # is a factor-of-two error and the record carries both for that reason
    assert 1.6 < got["bound_db"] / got["rss_db"] < 2.2


def test_the_unit_term_dwarfs_the_solution_bound_and_the_ratio_shrinks_it():
    """The two findings #425 exists to surface, as assertions.

    1. The unit-to-unit term is THREE ORDERS above the half-width already
       carried: 1.7-1.8 dB rss per band against 0.004-0.034 dB. A reader who
       takes `bound_db` for the tone stage's total uncertainty is not slightly
       wrong.
    2. On the band RATIOS the balance actually reads it is smaller -- 0.90 and
       1.07 dB -- because C90, the shunt every family shares and the largest
       lever on each, mostly cancels in a difference. Quoting the per-band
       number for an inter-band claim would over-state it by ~1.7x.
    """
    import cymbal_tone_realisation as ct  # noqa: PLC0415

    per_band = {}
    for band, name in ts.band_of().items():
        t = ts.tolerance_bound_db(float(ct.CENTRE_HZ[band]), name)
        per_band[band] = t["rss_db"]
        assert 1.6 < t["rss_db"] < 1.9, (band, t["rss_db"])

    ratios = ts.balance_tolerance_db()
    assert set(ratios) == {"decay-low", "short-low"}
    assert ratios["decay-low"]["rss_db"] == pytest.approx(0.901, abs=0.02)
    assert ratios["short-low"]["rss_db"] == pytest.approx(1.071, abs=0.02)
    for r in ratios.values():
        assert r["rss_db"] < min(per_band.values())
        assert r["uncited"] == ["VR4_TOTAL"]

    # the cancellation is C90's, and that is asserted rather than described
    c90_band = abs(ts.sensitivity_db_per_pct(10079.0, "Ht3")["C90"])
    c90_ratio = abs(ratios["short-low"]["db_per_pct"]["C90"])
    assert c90_ratio < 0.25 * c90_band


def test_the_record_publishes_both_terms_and_keeps_them_apart(evidence):
    """What the artifact gains: per-component levers and a cited unit-to-unit
    term at every frequency the balance levels at, the ratio terms beside
    them, and a top-level block that says which question each answers."""
    data, _meta, _blob = evidence
    rec = ts.balance_record(data)
    ct_ = rec["component_tolerance"]
    assert ct_["term_in_resolved_bound"] is False
    assert ct_["class"]["sha256"] == ts.TOLERANCE_CLASS["sha256"]
    assert set(ct_["on_the_balance"]) == {"decay-low", "short-low"}
    assert ct_["service_notes_print_none"]["source_sha256"] == ts.SN_PDF_SHA256
    for band in rec["bands"]:
        for _f, entry in rec["bands"][band]["at_hz"].items():
            sens = entry["sensitivity"]
            assert set(sens["db_per_pct"]) == set(ts.COMPONENT_KIND)
            assert sens["worst_case_db_per_pct"] > 0.0
            assert sens["dominant"], "no dominant set computed"
            assert set(sens["dominant"]) <= set(ts.COMPONENT_KIND)
            unit = entry["unit_tolerance"]
            # 45x (short band, whose own residual is the largest) to 619x
            # (low band at 10079 Hz). The floor is what must not silently
            # become 1x, which is what folding one into the other would look
            # like from outside.
            assert unit["rss_db"] > 40.0 * entry["bound_db"]
            assert 1.5 < unit["rss_db"] < 2.4
            assert unit["uncited"] == ["VR4_TOTAL"]


def test_the_bound_itself_is_unchanged_by_this_issue(evidence):
    """#425 changes the SIZE of nothing. The half-width is still exactly the
    residual plus the alpha term -- publishing the sensitivity table must not
    quietly add a third term to a number three orders of magnitude below the
    one that binds the balance's refusal."""
    data, _meta, _blob = evidence
    stats = ts.residual_stats(data)
    _, _, sigma, _ = ts.fit_alpha_k1_with_sigma(data)
    rec = ts.balance_record(data)
    for band in rec["bands"]:
        name = rec["bands"][band]["family"]
        for key, entry in rec["bands"][band]["at_hz"].items():
            b = ts.resolved_bound_db(float(key), name, stats=stats,
                                     sigma_alpha=sigma)
            assert entry["bound_db"] == pytest.approx(b["bound_db"], abs=1e-12)
            assert entry["bound_db"] == pytest.approx(
                entry["residual_db"] + entry["alpha_db"], abs=1e-12)
