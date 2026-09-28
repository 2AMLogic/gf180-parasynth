"""Known answers and must-fail controls for tools/cymbal_low_tail.py (#400).

The module under test adds a QUALIFIED 1-2.5 kHz decay to the cymbal work
(#369 step 6). What has to be true before its numbers may be quoted:

  * the within-record ratio rho(d) = T_M(d)/T_Ln(d) tracks a PLANTED difference
    in decay between the two bands, in BOTH directions, and reads its stated
    zero point when there is none;
  * that zero point is measured, not assumed to be 1.0 -- it is 0.89-0.99 in Mn,
    and a downward reading smaller than the bias is not a reading;
  * the crossing times themselves equal the planted times off an independently
    derived formula, not off the module's own arithmetic;
  * it REFUSES rather than answers on a record that cannot support the depth,
    and the refusal is per depth rather than per record;
  * the analysis filter's rejection at the three frequencies that could leak in
    is what the docstring claims (#101 was window leakage);
  * the real corpus's record lengths, which are what force two windows;
  * every injected defect turns at least one named property red, and the one
    transformation asserted blind stays blind.
"""
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_low_tail as lt  # noqa: E402
import cymbal_bands as cb  # noqa: E402

SR = lt.SR


# --------------------------------------------------------------------------
# the band definitions are frozen, and tied to the finding they came from
# --------------------------------------------------------------------------
def test_M_is_exactly_the_five_thirds_the_finding_reports():
    """Section 6 of docs/scorecard/cymbal-369/candidate3/README.md reports the
    1.0, 1.26, 1.59, 2.0 and 2.5 kHz 1/3 octaves. M must be their union --
    otherwise the qualified band and the unqualified finding are not about the
    same frequencies, and the qualification proves nothing about the claim."""
    lo = cb.THIRDS[0] / 2 ** (1 / 6)
    hi = cb.THIRDS[4] * 2 ** (1 / 6)
    assert lt.LOW_BANDS["M"] == (round(lo), round(hi)) or (
        abs(lt.LOW_BANDS["M"][0] - lo) < 1.0 and abs(lt.LOW_BANDS["M"][1] - hi) < 1.0), (
        lt.LOW_BANDS["M"], (lo, hi))
    # Mn is the lowest THREE of the same five.
    assert abs(lt.LOW_BANDS["Mn"][1] - cb.THIRDS[2] * 2 ** (1 / 6)) < 1.0, lt.LOW_BANDS["Mn"]


def test_reference_band_is_the_frozen_Ln_not_a_second_copy_of_it():
    """rho is divided by Ln, and Ln must be the one in cymbal_bands.BANDS so a
    change there cannot leave two disagreeing definitions in the tree."""
    assert lt.BANDS[lt.REF_BAND] == cb.BANDS["Ln"] == (2900.0, 4100.0)


def test_cymbal_bands_frozen_instrument_is_not_modified():
    """The whole point of a separate module: nothing already committed under
    docs/scorecard/cymbal-369/ may change. `measure()` there reads L, Ln, H and
    nothing else."""
    assert set(cb.BANDS) == {"L", "Ln", "H"}


# --------------------------------------------------------------------------
# the analysis filter's rejection -- the #101 guard, stated as numbers
# --------------------------------------------------------------------------
def test_analysis_rejection_matches_the_documented_figures():
    """`cymbal_low_tail`'s docstring quotes these, and M's -24.6 dB at the low
    band's own 3.45 kHz peak is the reason Mn is reported beside it everywhere.
    Pin them so the prose cannot drift from the filter (#383's lesson)."""
    r = lt.rejection_db()
    assert r["M"]["3450"] == pytest.approx(-24.6, abs=0.15), r["M"]
    assert r["M"]["7100"] == pytest.approx(-91.3, abs=0.5), r["M"]
    assert r["Mn"]["3450"] == pytest.approx(-85.1, abs=0.5), r["Mn"]
    assert r["Mn"]["7100"] == pytest.approx(-147.0, abs=1.0), r["Mn"]
    # Mn really does put ~60 dB more between itself and the low band's peak.
    assert r["Mn"]["3450"] - r["M"]["3450"] < -55.0, r


def test_rejection_is_the_two_pass_response_not_the_one_pass_one():
    """`cymbal_bands._bp` uses sosfiltfilt, so what the measurement sees is
    |H|^2. Reporting the one-way |H| would overstate the rejection by 2x in dB
    -- assert the square is the thing reported."""
    from scipy.signal import butter, sosfreqz
    lo, hi = lt.BANDS["M"]
    w, h = sosfreqz(butter(4, [lo, hi], btype="bandpass", fs=SR, output="sos"), worN=1 << 17, fs=SR)
    one = 20 * np.log10(np.abs(h) + 1e-300)
    i = int(np.argmin(np.abs(w - 3450.0)))
    assert lt.rejection_db()["M"]["3450"] == pytest.approx(2 * (one[i] - one.max()), abs=0.2)


# --------------------------------------------------------------------------
# known answers: the planted times, from a formula derived here independently
# --------------------------------------------------------------------------
def _planted(tau, depth):
    """An independent derivation of the same quantity `lt.t_edt` returns, so a
    sign or factor-of-two error in the module cannot be confirmed by itself --
    which is exactly the error this test caught on the first run.

    Amplitude exp(-t/tau) -> power exp(-2t/tau) -> Schroeder integral
    (tau/2)exp(-2t/tau) -> curve in dB = 10*log10(exp(-2t/tau)). Solve for t.
    """
    return 1e3 * (-depth / 10.0) * math.log(10.0) * tau / 2.0


def test_the_modules_planted_time_formula_agrees_with_an_independent_one():
    for tau in (0.05, 0.10, 0.35, 0.60):
        for d in (-5.0, -10.0, -20.0, -30.0):
            assert lt.t_edt(tau, d) == pytest.approx(_planted(tau, d), rel=1e-9)


@pytest.mark.parametrize("tau", (0.10, 0.20, 0.35))
def test_crossing_times_read_their_planted_exponential(tau):
    y, sr = lt.synth(tau_l=tau, a_h=0.0, a_m=0.0, dur=3.0)
    r = lt.measure(y, sr, trim_s=1.2 if tau <= 0.20 else 2.0)["bands"]["Ln"]
    for d in (-5.0, -10.0):
        got = r["times_ms"][f"{d:.0f}"]
        assert got is not None, r
        assert got == pytest.approx(_planted(tau, d), rel=0.10), (tau, d, got)


def test_depths_are_equally_spaced_in_time_for_one_exponential():
    """A single exponential's Schroeder curve is a straight line, so T(-20) must
    be twice T(-10) and T(-30) three times it. This is what makes a DEPARTURE
    from that spacing evidence of a second component rather than noise."""
    y, sr = lt.synth(tau_l=0.30, a_h=0.0, dur=3.0)
    t = lt.measure(y, sr, trim_s=2.0)["bands"]["Ln"]["times_ms"]
    assert t["-20"] / t["-10"] == pytest.approx(2.0, abs=0.10), t
    assert t["-30"] / t["-10"] == pytest.approx(3.0, abs=0.15), t


# --------------------------------------------------------------------------
# the load-bearing pair: rho sees a planted difference, and does not invent one
# --------------------------------------------------------------------------
def test_rho_reads_a_planted_slower_low_component():
    """POSITIVE half. A separate 891-2828 Hz component decaying 2.5x slower than
    the low band must push rho above 1, in BOTH bands, at EVERY depth.

    The time constants are 0.15/0.375 s rather than the 0.35/0.875 s the
    `rho-tracks` property uses, and that is a precondition rather than a
    convenience: a 2.0 s window can only resolve a depth d for tau <
    8.686*2.0/(|d| + 15) s, so at tau_m = 0.875 s the window supports Mn only to
    -5 dB. The next test covers that case at the depths it does support. Here the
    same RATIO is placed inside the window's measurable range so all six depths
    answer -- which makes this the stronger known answer, not the weaker one."""
    y, sr = lt.synth(tau_l=0.15, tau_m=0.375, a_m=0.35)
    r = lt.measure(y, sr)["rho"]
    for band in ("M", "Mn"):
        for d in lt.DEPTHS:
            got = r[band][f"{d:.0f}"]
            assert got is not None, (band, d, r[band])
            assert got > 1.15, (band, d, r[band])
    # And it is not a constant offset: a slower component separates further the
    # deeper you look, so rho must RISE with depth over the shallow half.
    assert r["M"]["-20"] > r["M"]["-5"], r["M"]


def test_a_plant_too_slow_for_the_window_refuses_the_deep_depths_and_answers_the_shallow():
    """The 0.35/0.875 s case the `rho-tracks` property uses, stated as the
    per-depth refusal it is. This is the pair to the test above: the instrument
    must not silently answer a depth the window cannot support, and it must still
    answer -- correctly, above 1.15 -- the depths it can.

    Wrong-then-right 5's sibling: an earlier version of the test above asserted
    Mn at -10 dB on THIS case and failed with a TypeError comparing None, which
    read like a bug in the instrument and was in fact the end-margin guard being
    right."""
    y, sr = lt.synth(tau_l=0.35, tau_m=0.875, a_m=0.35)
    m = lt.measure(y, sr)
    assert m["rho"]["M"]["-10"] > 1.15, m["rho"]["M"]
    assert m["rho"]["Mn"]["-5"] > 1.15, m["rho"]["Mn"]
    for band, first_refused in (("M", "-15"), ("Mn", "-10")):
        assert m["rho"][band][first_refused] is None, (band, m["rho"][band])
        assert "too short" in m["bands"][band]["refused"][first_refused]


def test_rho_reads_about_one_when_M_is_only_the_low_bands_own_skirt():
    """NEGATIVE half, paired, and the #101 guard. With NO separate low component
    the only 891-2828 Hz content is the 3.45 kHz Q 6 band-pass's real skirt,
    which carries the low band's envelope -- so rho MUST read ~1. If it did not,
    every rho above 1 measured on a recording would be the instrument's own
    leakage rather than the machine's behaviour."""
    y, sr = lt.synth(tau_l=0.35, a_m=0.0)
    r = lt.measure(y, sr)["rho"]
    for band in ("M", "Mn"):
        assert 0.92 < r[band]["-10"] < 1.08, (band, r[band])


def test_the_skirt_baseline_is_not_1_and_its_bounds_are_the_stated_ones():
    """The test above holds at ONE tau and ONE depth, and passes in Mn by 0.006.
    Swept over tau_l 0.10-0.60 s the skirt-only zero point is 0.94-1.02 in M and
    0.89-0.99 in Mn at -10 dB, and as low as 0.85 in Mn at -5 dB -- so 1.0 is NOT
    this instrument's zero and a downward reading must be judged against this
    baseline (module docstring, wrong-then-right 5).

    Pinned as bounds rather than point values so the numbers in the docstring and
    the scorecard cannot drift from the filter, and so that a change which made
    the bias WORSE would fail here."""
    sb = lt.skirt_baseline()
    assert sb["M_-10"]["refused"] == 0 and sb["Mn_-10"]["refused"] == 0, sb
    assert 0.93 <= sb["M_-10"]["min"] and sb["M_-10"]["max"] <= 1.03, sb["M_-10"]
    assert 0.88 <= sb["Mn_-10"]["min"] and sb["Mn_-10"]["max"] <= 1.00, sb["Mn_-10"]
    assert 0.84 <= sb["Mn_-5"]["min"] <= 0.90, sb["Mn_-5"]
    # The whole point: Mn's bias is the larger one, and it is downward.
    assert sb["Mn_-10"]["median"] < sb["M_-10"]["median"] < 1.0, sb
    # It is a BIAS, not noise: no tau reads high enough to be mistaken for a
    # planted slow component (the 1.15 threshold the positive tests use).
    assert sb["M_-5"]["max"] < 1.15 and sb["Mn_-5"]["max"] < 1.15, sb


def test_rho_inverts_when_the_planted_low_component_is_faster():
    """Third case of the same pair: rho is signed, not a magnitude. A FASTER low
    component must read BELOW ITS OWN SKIRT BASELINE at the same tau_l -- not
    below 1.0, which is the assertion this test made before and which failed at
    0.944 against a threshold of 0.92 (wrong-then-right 5).

    The floor is stated in the same breath: at a_m = 1.0 the planted component
    adds only ~1.2 dB to M's own energy, and that is what a departure of ~0.12 in
    M is worth."""
    base = lt.measure(*lt.synth(tau_l=0.35, a_m=0.0))["rho"]
    y, sr = lt.synth(tau_l=0.35, tau_m=0.10, a_m=1.0)
    r = lt.measure(y, sr)["rho"]
    for band in ("M", "Mn"):
        assert r[band]["-10"] < base[band]["-10"] - 0.05, (band, r[band], base[band])
    # Mn is the more sensitive band downward, which is why it is quoted beside M.
    assert (base["Mn"]["-10"] - r["Mn"]["-10"]) > (base["M"]["-10"] - r["M"]["-10"])


def test_the_downward_detection_floor_is_monotone_and_has_a_blind_row():
    """`detection_floor_fast` is what converts a measured rho below baseline into
    a size. It is only usable if it is monotone in the planted level and if it
    contains both a row the instrument cannot see and a row it plainly can --
    otherwise the floor is unstated in one direction or the other."""
    rows = lt.detection_floor_fast()
    for col in ("d_rho_M", "d_rho_Mn"):
        d = [r[col] for r in rows]
        assert all(v is not None for v in d), rows
        assert d == sorted(d, reverse=True), (col, rows)   # monotonically downward
        assert d[0] == 0.0, (col, rows)                    # the a_m = 0 row IS the baseline
    assert abs(rows[1]["d_rho_M"]) < 0.02, rows            # blind at a_m = 0.25
    assert rows[-1]["d_rho_M"] < -0.4, rows                # unmistakable at a_m = 3.0
    # The energy cost is monotone too, so the x axis of the floor is ordered.
    e = [r["M_energy_vs_skirt_db"] for r in rows]
    assert e == sorted(e), rows


def test_rho_is_invariant_to_record_gain():
    """rho is a ratio inside one record, which is the whole reason it can compare
    the 808 with our render without a level rule linking them. Assert it."""
    y, sr = lt.synth(tau_l=0.35, tau_m=0.875, a_m=0.35)
    a = lt.measure(y, sr)["rho"]["M"]
    b = lt.measure(0.01 * y, sr)["rho"]["M"]
    for k in a:
        assert b[k] == pytest.approx(a[k], rel=1e-9), (k, a[k], b[k])


def test_detection_floor_is_monotone_and_states_where_rho_goes_blind():
    """The instrument's own floor, reported rather than assumed: below some level
    a planted slow component does not move rho at all. The table must be
    monotone in the planted level and must contain both a blind row and a
    clearly-detected one -- if every row detected, the floor would be unstated."""
    rows = lt.detection_floor()
    rho = [r["rho_M_-10"] for r in rows]
    assert rho == sorted(rho), rows
    assert rho[0] < 1.05, rows                       # the skirt-only row is blind
    assert rho[-1] > 1.3, rows                       # the loudest planted row is not


# --------------------------------------------------------------------------
# refusals
# --------------------------------------------------------------------------
def test_a_record_shorter_than_the_window_refuses_rather_than_answers():
    y, sr = lt.synth(tau_l=0.20, a_h=0.0, dur=3.0)
    cut = y[: lt.rc.required_lead_samples(sr) + int(1.0 * sr)]
    with pytest.raises(lt.Refused):
        lt.measure(cut, sr, trim_s=2.0)


def test_a_depth_without_end_margin_refuses_while_shallower_depths_answer():
    """The refusal is per depth, not per record: at tau 0.5 in a 2.0 s window the
    margin at depth d is 8.686/tau*2.0 - |d| = 34.7 - |d| dB, so -10 answers and
    -30 must refuse."""
    y, sr = lt.synth(tau_l=0.50, a_h=0.0, dur=3.0)
    r = lt.measure(y, sr, trim_s=2.0)["bands"]["Ln"]
    assert r["times_ms"]["-10"] is not None
    assert r["times_ms"]["-30"] is None and "-30" in r["refused"], r
    assert "too short" in r["refused"]["-30"]


def test_a_refused_depth_makes_rho_refuse_and_not_silently_drop_a_band():
    y, sr = lt.synth(tau_l=0.50, a_h=0.0, dur=3.0)
    assert lt.measure(y, sr, trim_s=2.0)["rho"]["M"]["-30"] is None


def test_the_real_corpus_record_lengths_force_two_windows():
    """Not a synthetic property: the Fischer CY files are 1.501 s at DECAY 00 and
    4.001 s at DECAY 10, so a single window cannot cover all 25 settings. This
    pins the fact the two-window protocol rests on; if the corpus ever changes,
    this fails rather than the conclusions silently mixing windows."""
    refs = lt.rc.configured_refs() / "cy8"
    if not refs.is_dir():
        pytest.skip(f"the Fischer corpus is not present at {refs}")
    lens = {}
    for code in cb.CODES:
        p = refs / f"CY50{code}.WAV"
        x, sr = cb._load(p)
        y = lt.rc.prepare(x, sr, side=p.name)
        lens[code] = round((len(y) - lt.rc.required_lead_samples(sr)) / sr, 3)
    assert lens["00"] == pytest.approx(1.501, abs=0.01), lens
    assert lens["10"] == pytest.approx(4.001, abs=0.01), lens
    assert min(lens.values()) < lt.TRIM_S, (
        "if every record now supports TRIM_S the two-window protocol is "
        "unnecessary and the scorecard's reason for it is stale", lens)


def test_the_full_decay_to_length_map_is_the_one_the_docstring_states():
    """All 25 files, not one TONE column: the module docstring's table claims the
    length is a function of the DECAY code ALONE. If it were also a function of
    TONE, every TONE comparison in the scorecard would be between records of
    different length -- which is the exact artefact the trim exists to prevent.
    Asserted rather than assumed (wrong-then-right 4 was this docstring's
    previous, false, single-length claim)."""
    refs = lt.rc.configured_refs() / "cy8"
    if not refs.is_dir():
        pytest.skip(f"the Fischer corpus is not present at {refs}")
    want = {"00": 1.501, "25": 2.001, "50": 2.501, "75": 3.501, "10": 4.001}
    lens = {}
    for tone in cb.CODES:
        for decay in cb.CODES:
            p = refs / f"CY{tone}{decay}.WAV"
            x, sr = cb._load(p)
            y = lt.rc.prepare(x, sr, side=p.name)
            lens[(tone, decay)] = (len(y) - lt.rc.required_lead_samples(sr)) / sr
            assert lens[(tone, decay)] == pytest.approx(want[decay], abs=0.002), (
                tone, decay, lens[(tone, decay)], want[decay])
    # The two frozen windows, as counts rather than as prose.
    assert sum(1 for v in lens.values() if v >= 1.5) == 25, lens
    assert sum(1 for v in lens.values() if v >= lt.TRIM_S) == 20, lens


def test_the_two_frozen_windows_answer_and_refuse_the_settings_they_claim_to():
    """The counts above are record lengths; these are the tool's own verdicts.
    Every DECAY 00 setting must come back REFUSED at 2.0 s and answered at 1.5 s
    -- a refusal the caller can see, not a silently missing row."""
    refs = lt.rc.configured_refs()
    if not (refs / "cy8").is_dir():
        pytest.skip(f"the Fischer corpus is not present at {refs}")
    settings = [f"CY{t}{d}" for t in cb.CODES for d in ("00", "25")]
    at20 = lt.fischer(refs, settings, trim_s=2.0)
    at15 = lt.fischer(refs, settings, trim_s=1.5)
    for s in settings:
        if s.endswith("00"):
            assert "refused" in at20[s], (s, sorted(at20[s]))
            assert "common analysis window" in at20[s]["refused"], at20[s]
        else:
            assert "rho" in at20[s], (s, sorted(at20[s]))
        assert "rho" in at15[s], (s, sorted(at15[s]))


# --------------------------------------------------------------------------
# the onset confound, bounded rather than assumed away
# --------------------------------------------------------------------------
def test_a_broadband_click_drags_rho_down_and_shows_up_in_the_onset_share():
    """The one confound rho's shallow depths have. A click is broadband, so it
    lands harder in M (1937 Hz wide) than in Ln (1200 Hz wide) and pulls rho
    DOWN -- which means it can only ever EXPLAIN a rho below 1, never one above.
    It must also be visible in the onset share the same measurement reports, or
    it would be an unfalsifiable explanation."""
    rows = lt.onset_sensitivity()
    base, loud = rows[0], rows[-1]
    assert loud["rho_M_-10"] < base["rho_M_-10"], rows
    assert loud["onset_excess_db"] > base["onset_excess_db"] + 3.0, rows


# --------------------------------------------------------------------------
# the matrix itself
# --------------------------------------------------------------------------
def test_every_named_property_passes_on_the_clean_measurement():
    got = lt.properties()
    assert set(got) == set(lt.PROPERTIES)
    bad = {k: v for k, v in got.items() if not v[0]}
    assert not bad, bad


def test_both_directions_of_rho_are_named_properties():
    """Verification rule 4 in the direction it is easiest to miss: the sign our
    own renders read is the DOWNWARD one, and a suite that only had `rho-tracks`
    would have carried no control at all on it. Pinned so a later edit cannot
    quietly drop the half that is actually used."""
    assert "rho-tracks" in lt.PROPERTIES and "rho-inverts" in lt.PROPERTIES


def test_the_downward_property_is_seen_by_a_defect_and_is_not_decoration():
    """A property that no injected defect moves is coverage in costume. Assert
    that at least one defect turns `rho-inverts` red specifically -- not merely
    that every defect is caught by something."""
    saw = []
    for d in lt.DEFECTS:
        try:
            got = lt.properties(defect=d)
        except lt.Refused:
            continue
        if got["rho-inverts"][0] is False:
            saw.append(d)
    assert saw, "no injected defect moves rho-inverts"


@pytest.mark.parametrize("defect", lt.DEFECTS)
def test_each_injected_defect_turns_at_least_one_property_red(defect):
    """A control that cannot fail is worse than no control (#376). Each defect
    must move something, and the matrix in `--check` records WHICH."""
    try:
        got = lt.properties(defect=defect)
    except lt.Refused:
        return                      # a defect that makes the tool refuse is caught
    red = [p for p in lt.PROPERTIES if got[p][0] is False]
    assert red, f"{defect} turned nothing red"


@pytest.mark.parametrize("blind", lt.BLIND_BY_CONSTRUCTION)
def test_the_transformation_asserted_blind_really_is_blind(blind):
    """rho and the crossing times are both invariant to record gain, so scaling
    must move NOTHING. Asserted rather than assumed, because a blind control
    that secretly moves is a silent failure of the invariance claim rho rests
    on."""
    got = lt.properties(defect=blind)
    moved = [p for p in lt.PROPERTIES if got[p][0] is False]
    assert not moved, (blind, moved)


def test_check_returns_ok_on_the_current_tree():
    """Run the gate against the current state before relying on it -- an
    unsatisfiable gate trains everyone to ignore gates."""
    ok, lines = lt.check()
    assert ok, "\n".join(lines)
