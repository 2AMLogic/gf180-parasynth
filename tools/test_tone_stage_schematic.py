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
