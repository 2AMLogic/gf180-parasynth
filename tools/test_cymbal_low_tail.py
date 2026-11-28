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
import json
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cymbal_low_tail as lt  # noqa: E402
import cymbal_bands as cb  # noqa: E402
import refusal  # noqa: E402

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
    assert set(a) == set(b), (a, b)
    # A depth the instrument refuses (None) must be refused at both gains: a
    # refusal that appears or vanishes with level IS gain dependence. pytest
    # >= 9.2 raises TypeError on approx(None, rel=...), and 9.1.1 let it pass
    # silently -- so None is compared explicitly, never handed to approx.
    for k in a:
        assert (a[k] is None) == (b[k] is None), (k, a[k], b[k])
        if a[k] is not None:
            assert b[k] == pytest.approx(a[k], rel=1e-9), (k, a[k], b[k])
    # Non-vacuous: for this record the -5 and -10 depths are measurable, so an
    # all-None measure cannot pass by agreeing with itself.
    for k in ("-5", "-10"):
        assert a[k] is not None and b[k] is not None, (k, a, b)


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
# the process exit status of a run in which a row refused (#1765, the #1699 shape)
# --------------------------------------------------------------------------
# `main` records a refusal in band ({"refused": ...}) and prints it. It used to
# `return 0` after doing so, which tools/run_all.py renders as PASS: a run whose
# only 808 row held no number read as a pass to anything that looks at the exit
# status. The #1699 ruling, applied here: a row that was SUPPOSED to answer and
# did not makes the run REFUSED (2), while the refusals the module documents as
# by design -- a Fischer record shorter than one window, which is what the three
# frozen windows exist for -- leave it at 0.
#
# The corpora below are synthetic, on disk, and read by the REAL cb._load /
# rc.prepare / measure through the real `fischer`: never a patched `fischer`,
# so each refusal is raised by the loader or by `_trim` exactly as it would be
# on a damaged or incomplete download of the real set. Their lengths are the
# module docstring's DECAY table, so the by-design refusals fall where they do
# on the real corpus (all 25 settings answer at 1.5 s, 20 at 2.0 s, 10 at 3.5 s).
DECAY_LENGTH_S = {"00": 1.5, "25": 2.0, "50": 2.5, "75": 3.5, "10": 4.0}
ALL_SETTINGS = tuple(f"CY{t}{d}" for t in cb.CODES for d in cb.CODES)
BY_DESIGN_REFUSALS_PER_WINDOW = {"1.5s": 0, "2.0s": 5, "3.5s": 15}
# Every corpus the controls below use, by name, so any test can run alone.
CORPORA = {
    "clean25": {},
    "clean5025": {"settings": ("CY5025",)},
    "too_short5025": {"settings": ("CY5025",), "broken": "CY5025"},
    "not_int165025": {"settings": ("CY5025",), "broken": "CY5025", "how": "not_int16"},
    "silent5025": {"settings": ("CY5025",), "broken": "CY5025", "how": "silent"},
}


def _raw_record(dur, seed=7):
    """A strike as a Fischer file is before `rc.prepare` sees it: 20 ms of true
    silence, then the onset, then `dur` seconds of signal."""
    y, sr = lt.synth(tau_l=0.35, a_h=0.5, dur=dur, seed=seed)
    onset = lt.rc.required_lead_samples(sr) + int(round(lt.rc.TRIM_MS * 1e-3 * sr))
    return np.concatenate([np.zeros(int(0.02 * sr)), y[onset:]]), sr


def write_corpus(root, *, settings=ALL_SETTINGS, broken=None, how="too_short", dur_s=1.0):
    """`<root>/cy8/<SETTING>.WAV` in 16-bit PCM, one per setting, each as long
    after t=0 as its DECAY code's record is on the real machine (1.501 s at
    DECAY 00 ... 4.001 s at DECAY 10), so `rc.prepare` and every window treat
    it as the real file. `broken` names the ONE setting that cannot be
    measured, by a refusal raised at each of the three stages that can:
    `how="too_short"` cuts it to `dur_s` after t=0 -- 1.0 s, shorter than the
    1.5 s window every setting supports, so `_trim` refuses it in every window;
    `how="not_int16"` writes it as float32, which `cb._load` refuses (#1117);
    and `how="silent"` writes digital silence at full length, which `measure`
    refuses band by band. Returns `root`."""
    if how not in ("too_short", "not_int16", "silent"):
        raise ValueError(how)
    cy8 = pathlib.Path(root) / "cy8"
    cy8.mkdir(parents=True, exist_ok=True)
    for s in settings:
        dur = dur_s if s == broken and how == "too_short" else DECAY_LENGTH_S[s[4:]]
        x, sr = _raw_record(dur, seed=int(s[2:]))
        if s == broken and how == "silent":
            x = np.zeros_like(x)
        if s == broken and how == "not_int16":
            wavfile.write(cy8 / f"{s}.WAV", sr, x.astype(np.float32))
        else:
            wavfile.write(cy8 / f"{s}.WAV", sr, np.round(x * 20000).astype(np.int16))
    return pathlib.Path(root)


def _refused(res):
    """The (window, setting) pairs whose Fischer row holds a refusal."""
    return {(w, s) for w, wv in res["windows"].items() for s, m in wv["fischer"].items()
            if "refused" in m}


def _refused_lines(printed):
    return [ln for ln in printed.splitlines() if ln.startswith("REFUSED: ")]


@pytest.fixture(scope="module")
def corpora(tmp_path_factory):
    """Each corpus in CORPORA is written once per module run, under its own name."""
    base, made = tmp_path_factory.mktemp("cy1765"), {}

    def get(name):
        if name not in made:
            made[name] = write_corpus(base / name, **CORPORA[name])
        return made[name]
    return get


SWEEPS = ("onset_sensitivity", "detection_floor", "detection_floor_fast", "skirt_baseline")


@pytest.fixture(scope="module")
def stand_ins():
    """Computed once for the module: the real control matrix, the real four
    synthetic sweeps, and a synthetic stand-in for our own kit's CY render
    (3.6 s, like the real one)."""
    return {"check": lt.check(), "ours": _raw_record(3.6, seed=99),
            "sweeps": {name: getattr(lt, name)() for name in SWEEPS}}


@pytest.fixture
def main_fast(monkeypatch, stand_ins):
    """The REAL `main` with four substitutions, none of which a refusal recorded
    in a result can come from: our kit's CY render (10.6 s a call, five calls a
    run) is a synthetic strike; `match_floor`'s bisection returns its input
    unchanged, so the noise-floor control's own refusal logic runs on the real
    corpus record; and the control matrix and the synthetic sweeps are the real
    ones, computed once. `fischer`, `noise_floor_control`, the printer, `--out`
    and the return value are the module's own."""
    def run(refs, *argv, out):
        return lt.main(["--refs", str(refs), *argv, "--out", str(out)])
    monkeypatch.setattr(lt, "check", lambda: stand_ins["check"])
    for name in SWEEPS:
        monkeypatch.setattr(lt, name, lambda n=name: stand_ins["sweeps"][n])
    monkeypatch.setattr(lt.rc, "render_drum_solo", lambda sound, *a, **k: stand_ins["ours"])
    monkeypatch.setattr(lt, "match_floor",
                        lambda y, sr, target, band="M", *, trim_s=lt.TRIM_S, seed=11: (y, 0.0, target))
    return run


@pytest.mark.parametrize("how, why", (("too_short", "common analysis window"),
                                      ("not_int16", "16-bit PCM"),
                                      ("silent", "band is silent")))
def test_main_refuses_when_the_one_requested_record_cannot_be_measured_in_any_window(
        corpora, main_fast, tmp_path, capsys, how, why):
    """The red-first control, and the issue's own scenario: the default run
    measures ONE Fischer setting, and that record cannot supply any window. On
    origin/main this printed three REFUSED rows, wrote `--out`, and returned 0.

    The measured rows -- ours, in every window -- must still be printed and
    written: the refusal is of one row, not of the run's output. And because
    the noise-floor control reads the same record, it refuses too, and both are
    named. Each parameter is a refusal raised at a different stage by the real
    code, from a file on disk: `_trim` (`too_short`), `cb._load` (`not_int16`)
    and `measure` itself (`silent`, which the 3.5 s window then refuses as too
    short -- the classifier must not let one by-design entry excuse the row)."""
    refs = corpora(how + "5025")
    out = tmp_path / "r.json"
    rc = main_fast(refs, out=out)
    assert rc == lt.REFUSED, rc
    res = json.loads(out.read_text())
    assert set(res["windows"]) == {"1.5s", "2.0s", "3.5s"}
    assert _refused(res) == {(w, "CY5025") for w in ("1.5s", "2.0s", "3.5s")}
    assert why in res["windows"]["1.5s"]["fischer"]["CY5025"]["refused"], res["windows"]["1.5s"]
    for w in res["windows"].values():
        assert w["shipped"]["rho"]["M"]["-10"] is not None, w["shipped"]
    assert why in res["noise_floor_control"]["refused"], res["noise_floor_control"]
    assert len(res["unexpected_refusals"]) == 2, res["unexpected_refusals"]
    printed = capsys.readouterr().out
    assert "808 CY5025" in printed and "shipped" in printed, printed
    lines = _refused_lines(printed)
    assert len(lines) == 1, printed
    assert "808 CY5025" in lines[0] and "noise-floor control" in lines[0], lines[0]


def test_a_clean_default_run_still_exits_0(corpora, main_fast, tmp_path, capsys):
    """The control that proves the gate is satisfiable: CY5025 at its real
    2.001 s answers at 1.5 s and 2.0 s, refuses the 3.5 s window as too short
    BY DESIGN, the noise-floor control runs, and the exit status is 0 with no
    REFUSED line of its own."""
    refs = corpora("clean5025")
    out = tmp_path / "r.json"
    rc = main_fast(refs, out=out)
    assert rc == 0, capsys.readouterr().out
    res = json.loads(out.read_text())
    assert _refused(res) == {("3.5s", "CY5025")}
    assert "common analysis window" in res["windows"]["3.5s"]["fischer"]["CY5025"]["refused"]
    for w in ("1.5s", "2.0s"):
        assert res["windows"][w]["fischer"]["CY5025"]["rho"]["M"]["-10"] is not None, w
    assert "refused" not in res["noise_floor_control"]
    assert not any("refused" in r for r in res["noise_floor_control"]["runs"].values())
    assert res["unexpected_refusals"] == []
    assert not _refused_lines(capsys.readouterr().out)


def test_a_clean_all_settings_run_exits_0_with_its_by_design_refusals_present(
        corpora, main_fast, tmp_path, capsys):
    """The edge the curation named: with `--all-settings` the five DECAY 00
    records refuse the 2.0 s and 3.5 s windows by design and answer at 1.5 s.
    Those ten in-band refusals must be present and unchanged -- a run that
    hid them would have changed the measurement, not the exit status -- and
    must not fail the run."""
    refs = corpora("clean25")
    out = tmp_path / "r.json"
    rc = main_fast(refs, "--all-settings", out=out)
    assert rc == 0, capsys.readouterr().out
    res = json.loads(out.read_text())
    got = _refused(res)
    expected = {(w, s) for w in res["windows"] for s in ALL_SETTINGS
                if DECAY_LENGTH_S[s[4:]] < float(w[:-1])}
    assert got == expected, sorted(got ^ expected)
    assert {w: sum(1 for ww, _ in got if ww == w) for w in BY_DESIGN_REFUSALS_PER_WINDOW} \
        == BY_DESIGN_REFUSALS_PER_WINDOW
    for w, s in got:
        assert "common analysis window" in res["windows"][w]["fischer"][s]["refused"], (w, s)
        assert res["windows"][w]["fischer"][s]["record_s"] == pytest.approx(DECAY_LENGTH_S[s[4:]] + 0.001,
                                                                          abs=0.002), (w, s)
    decay00 = {s for s in ALL_SETTINGS if s.endswith("00")}
    for s in decay00:
        assert res["windows"]["1.5s"]["fischer"][s]["rho"]["M"]["-10"] is not None, s
    assert res["unexpected_refusals"] == []
    assert not _refused_lines(capsys.readouterr().out)


@pytest.mark.parametrize("corpus, argv, want", [
    ("clean25", ("--windows", "3.5", "--all-settings"), 0),
    ("clean5025", ("--windows", "3.5"), 0),
    ("too_short5025", ("--windows", "3.5"), lt.REFUSED),
    ("too_short5025", ("--windows", "1.5"), lt.REFUSED),
])
def test_a_restricted_window_run_refuses_only_a_record_no_frozen_window_covers(
        corpora, main_fast, tmp_path, capsys, corpus, argv, want):
    """The input that defeats the obvious rule, both ways. "Refused in every
    window that was run" is NOT enough: `--windows 3.5` alone makes fifteen
    settings refuse in the only window run, by design, and a rule that counted
    them would make a documented run REFUSED for good (verification rule 8 --
    an unsatisfiable gate). Excusing the short-record KIND alone is not enough
    either: a 1.0 s CY5025 refuses with the same words as a 2.001 s one. The
    rule needs both -- and rows 2 and 3 above print the identical REFUSED row
    for CY5025 and get different verdicts on the record's length alone."""
    refs = corpora(corpus)
    out = tmp_path / "r.json"
    rc = main_fast(refs, *argv, out=out)
    assert rc == want, capsys.readouterr().out
    res = json.loads(out.read_text())
    assert set(res["windows"]) == {"3.5s" if "3.5" in argv else "1.5s"}
    assert bool(res["unexpected_refusals"]) == (want == lt.REFUSED), res["unexpected_refusals"]
    assert len(_refused_lines(capsys.readouterr().out)) == (1 if want == lt.REFUSED else 0)


def test_main_refuses_when_a_noise_floor_control_run_refuses(
        corpora, main_fast, tmp_path, monkeypatch, capsys):
    """The second place a refusal is recorded and the run continues: one of the
    control's two conditions cannot be produced. Every 808 row measured, so the
    only thing standing between the rho gap and a floor explanation is missing,
    and the run is REFUSED."""
    def cannot_add_noise(y, sr, target, band="M", *, trim_s=lt.TRIM_S, seed=11):
        raise lt.Refused(f"cannot raise the {band} floor to {target:.2f} dB: noise can only be added")
    monkeypatch.setattr(lt, "match_floor", cannot_add_noise)
    refs = corpora("clean5025")
    out = tmp_path / "r.json"
    rc = main_fast(refs, out=out)
    assert rc == lt.REFUSED, capsys.readouterr().out
    res = json.loads(out.read_text())
    assert _refused(res) == {("3.5s", "CY5025")}
    runs = res["noise_floor_control"]["runs"]
    assert set(runs) == {"matched", "ten_db_noisier"}
    assert all("refused" in r for r in runs.values()), runs
    assert len(res["unexpected_refusals"]) == 2, res["unexpected_refusals"]
    lines = _refused_lines(capsys.readouterr().out)
    assert len(lines) == 1 and lines[0].count("noise-floor control") == 2, lines


def test_main_refuses_when_the_candidate_refuses(corpora, main_fast, tmp_path, monkeypatch, capsys):
    """The third place: the `--candidate` render refuses in every window (its
    calibration is per run), exactly as `ce.calibrate` raising would leave it."""
    def refuses(*, trim_s=lt.TRIM_S):
        raise refusal.Refused("the candidate's calibration refused (stand-in for ce.calibrate)")
    monkeypatch.setattr(lt, "candidate", refuses)
    refs = corpora("clean5025")
    out = tmp_path / "r.json"
    rc = main_fast(refs, "--candidate", out=out)
    assert rc == lt.REFUSED, capsys.readouterr().out
    res = json.loads(out.read_text())
    assert _refused(res) == {("3.5s", "CY5025")}
    assert all("refused" in w["candidate"] for w in res["windows"].values())
    assert len(res["unexpected_refusals"]) == 3, res["unexpected_refusals"]
    lines = _refused_lines(capsys.readouterr().out)
    assert len(lines) == 1 and lines[0].count("candidate in the") == 3, lines


def test_the_real_corpus_recorded_result_has_no_unexpected_refusals():
    """The classifier against the REAL corpus's rows, as the real run recorded
    them: the 20 in-band refusals are exactly the by-design ones (the five DECAY
    00 settings at 2.0 s, fifteen settings at 3.5 s), the noise-floor control
    ran both conditions, and nothing is unexpected -- so the gate is satisfiable
    on the corpus it will actually meet. The file predates the `record_s` key,
    which also checks that a result written before #1765 still reads as clean."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    res = json.loads(RESULT.read_text())
    got = _refused(res)
    assert {w: sum(1 for ww, _ in got if ww == w) for w in BY_DESIGN_REFUSALS_PER_WINDOW} \
        == BY_DESIGN_REFUSALS_PER_WINDOW
    assert all("common analysis window" in res["windows"][w]["fischer"][s]["refused"] for w, s in got)
    assert "refused" not in res["noise_floor_control"]
    assert not any("refused" in r for r in res["noise_floor_control"]["runs"].values())
    assert lt.unexpected_refusals(res) == []


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


def test_the_click_sweep_is_monotone_in_both_columns_so_it_can_be_read_backwards():
    """The onset table is used BACKWARDS -- a record's measured onset excess is
    looked up to bound how much of its rho deficit a click could explain. That
    only works if both columns are monotone in the planted click, so assert it
    rather than assuming the lookup is single-valued."""
    rows = lt.onset_sensitivity()
    rho = [r["rho_M_-10"] for r in rows]
    onset = [r["onset_excess_db"] for r in rows]
    assert rho == sorted(rho, reverse=True), rows
    assert onset == sorted(onset), rows
    # And the sweep must span the onset excess our own renders actually read
    # (+3.24 dB on the shipped CY), or the bound would be an extrapolation.
    assert onset[0] < 3.24 < onset[-1], rows


# --------------------------------------------------------------------------
# the noise-floor control, and the refusal that stops it lying
# --------------------------------------------------------------------------
def test_match_floor_refuses_a_target_below_the_records_existing_floor():
    """Wrong-then-right 6, reinstated as a control per verification rule 5. Noise
    can only be ADDED, so a target below the record's own floor is unreachable --
    and the bisection asked for one converges on zero amplitude and hands back
    the UNMODIFIED record. That is exactly what happened: the control printed
    three identical rows (M floor -33.87 dB, rho 0.8709) with no complaint."""
    y, sr = lt.synth(tau_l=0.20, a_h=0.0, dur=3.0, floor=0.01)
    have = lt.measure(y, sr)["bands"]["M"]["floor_db_re_peak"]
    with pytest.raises(lt.Refused) as e:
        lt.match_floor(y, sr, have - 20.0)
    assert "noise can only be added" in str(e.value)
    assert "other direction" in str(e.value)


def test_match_floor_reaches_a_reachable_target_and_actually_changes_the_record():
    """The paired positive. A target ABOVE the existing floor must be hit to
    within the stated tolerance, and the returned record must differ from the
    input -- the second half is the one the silent no-op would have failed."""
    y, sr = lt.synth(tau_l=0.20, a_h=0.0, dur=3.0)
    have = lt.measure(y, sr)["bands"]["M"]["floor_db_re_peak"]
    target = have + 10.0
    yn, amp, got = lt.match_floor(y, sr, target)
    assert got == pytest.approx(target, abs=0.05), (have, target, got)
    assert amp > 0.0
    assert not np.array_equal(np.asarray(yn)[: len(y)], np.asarray(y)), "record unchanged"
    assert lt.measure(yn, sr)["bands"]["M"]["floor_db_re_peak"] > have + 5.0


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


# --------------------------------------------------------------------------
# the summariser, which is what the scorecard's numbers come from
# --------------------------------------------------------------------------
RESULT = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/cymbal-369/low-tail/low-tail.json"


def test_summarise_refuses_a_result_whose_own_controls_did_not_pass():
    """The console refusal is gone by the time someone picks the JSON up, so the
    precondition is asserted again at the point of USE. A summary of a run whose
    controls failed would look exactly like a summary of one whose controls
    passed."""
    with pytest.raises(lt.Refused):
        lt.summarise({"controls_pass": False, "windows": {}, "skirt_baseline": {}})
    with pytest.raises(lt.Refused):
        lt.summarise({"windows": {}, "skirt_baseline": {}})       # absent, not merely false


def test_the_committed_result_file_is_summarisable_and_says_what_the_scorecard_says():
    """The scorecard's headline numbers, read back out of the committed JSON by
    the same function that produced them. If a later run changes them, this fails
    rather than the prose silently going stale (#383's lesson)."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    s = lt.summarise(json.loads(RESULT.read_text()))
    assert set(s["windows"]) == {"1.5s", "2.0s", "3.5s"}
    assert s["windows"]["1.5s"]["n_refused_short"] == 0
    assert s["windows"]["2.0s"]["n_refused_short"] == 5
    assert s["windows"]["3.5s"]["n_refused_short"] == 15
    for wname in ("1.5s", "2.0s", "3.5s"):
        for band in ("M", "Mn"):
            r = s["windows"][wname]["rows"][f"{band}-10"]
            # The whole finding, in one shape: the 808 above the instrument's
            # zero, both our renders below it, in every band and every window.
            assert r["min"] > r["shipped"], (wname, band, r)
            assert r["shipped"] < r["baseline_min"], (wname, band, r)
            assert r["candidate"] < r["baseline_min"], (wname, band, r)
            assert r["median"] > r["baseline_max"], (wname, band, r)
            assert r["n_808"] == 10, (wname, band, r)
    # M is the wide band and every answering setting clears its baseline there.
    assert s["windows"]["2.0s"]["rows"]["M-10"]["n_808_above_baseline_max"] == 10
    # Mn is the leakage-proof one: 8 of 10, which is the honest weaker figure.
    assert s["windows"]["2.0s"]["rows"]["Mn-10"]["n_808_above_baseline_max"] == 8


def test_the_three_frozen_windows_cover_every_setting_and_no_two_of_them_do():
    """#369 acceptance 3 is "report every setting", and for a decay quantity that
    is a claim about WINDOWS, not about loading 25 files. Asserted both ways: the
    union of the three frozen windows must answer all 25, and dropping the 3.5 s
    one must leave settings uncovered -- otherwise the third window is
    unjustified and should go."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    res = json.loads(RESULT.read_text())
    assert set(res["windows"]) == {f"{w:.1f}s" for w in lt.WINDOWS_S}, sorted(res["windows"])
    c = lt.coverage(res)
    assert c["n_settings"] == 25, c
    assert c["uncovered"] == [], c
    assert c["n_covered"] == 25, c
    two = lt.coverage({"windows": {k: v for k, v in res["windows"].items() if k != "3.5s"}})
    assert two["uncovered"], "the 3.5 s window adds nothing and should be dropped"
    assert two["n_covered"] == 15, two
    # Each window pulls its weight: none of the three is redundant.
    for drop in res["windows"]:
        c2 = lt.coverage({"windows": {k: v for k, v in res["windows"].items() if k != drop}})
        assert c2["n_covered"] < 25, f"the {drop} window is redundant"


def test_the_reference_bands_own_decay_already_matches_the_machine():
    """The one number §5b's reconciliation with `../mid-band/` rests on, and the
    reason a within-record ratio is the right statistic here rather than an
    absolute decay time carrying a +-50 % unit-to-unit tolerance: at CY5025 the
    LOW band's own decay is 591.16 ms for the 808 and 590.56 ms for our shipped
    render. If that ever stopped being true the reconciliation would need
    rewriting, so it fails here rather than going stale in prose."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    w = json.loads(RESULT.read_text())["windows"]["2.0s"]
    ref808 = w["fischer"]["CY5025"]["bands"]["Ln"]["times_ms"]["-10"]
    refours = w["shipped"]["bands"]["Ln"]["times_ms"]["-10"]
    assert abs(ref808 / refours - 1.0) < 0.005, (ref808, refours)
    # And the band beside it is out by far more than that, in opposite directions
    # relative to each record's own low band. That asymmetry IS the finding.
    m808 = w["fischer"]["CY5025"]["bands"]["M"]["times_ms"]["-10"]
    mours = w["shipped"]["bands"]["M"]["times_ms"]["-10"]
    assert m808 > ref808 and mours < refours, (m808, ref808, mours, refours)
    assert abs(m808 / mours - 1.0) > 0.15, (m808, mours)


def test_the_committed_result_files_noise_floor_control_excludes_the_floor_artefact():
    """The control's verdict, pinned. Our render is the NOISIER one in M
    (-33.9 dB against the 808's -60.9), so the noise is added to the 808's
    record; 27 dB of it must leave the 808's rho essentially where it was, or the
    gap is a floor artefact and the finding is withdrawn."""
    if not RESULT.is_file():
        pytest.skip(f"{RESULT} is not present")
    n = json.loads(RESULT.read_text())["noise_floor_control"]
    assert n["cleaner"] == "ref", n
    assert n["ours_floor_db"] > n["ref_floor_db"] + 20.0, n
    assert n["noise_added_to"].startswith("808"), n
    before = n["ref"]["rho_M"]["-10"]
    after = n["runs"]["matched"]["rho_M"]["-10"]
    assert abs(after - before) < 0.01, (before, after)
    # And the gap it was meant to explain is 25x larger than what it moved.
    assert n["gap_before"] > 0.2, n
    assert abs(n["moved_toward_other_by"]) < 0.02 * n["gap_before"], n
