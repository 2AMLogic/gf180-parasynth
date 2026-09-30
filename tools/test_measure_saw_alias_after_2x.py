"""Controls for `tools/measure_saw_alias_after_2x.py` (issue #61).

Every test here is an INJECTED DEFECT that must turn a precondition red, plus
the one reproduction check that says the instrument is the one the frozen
reference table was made with. A measurement tool's only failure mode that
matters is a false green: a refusal that cannot fire is not a refusal, it is a
comment (`docs/verification-rules.md`, rules 1 and 2).
"""
import numpy as np
import pytest

# The tool puts `model/` and `audition/` on sys.path, so it is imported first.
import measure_saw_alias_after_2x as msa
import audio_measure as am                                          # noqa: E402
import voice_fx as vf                                               # noqa: E402


# ---- the frozen table ------------------------------------------------------
def test_the_frozen_reference_table_parses_the_rows_it_is_quoted_for():
    """The Surge and Mini V3 saw rows, at the six pitches, with the report's own
    '<' floor marks carried through rather than dropped."""
    t = msa.parse_report_table()
    for key in (("ours", "saw"), ("ideal", "saw"), ("surge", "saw"), ("miniv3", "saw")):
        assert key in t, key
        assert set(t[key]) == set(msa.REFERENCE_NOTES), (key, sorted(t[key]))
    # 55 Hz is where every reference cell sat on the estimator's floor -- the
    # column this issue withdrew. The parser must preserve that, not launder it.
    assert t[("surge", "saw")][33]["at_floor"] is True
    assert t[("miniv3", "saw")][33]["at_floor"] is True
    assert t[("surge", "saw")][45]["at_floor"] is False
    # ...and an excluded cell stays absent rather than becoming a number.
    assert t[("miniv3", "saw")][93]["value"] is None


def test_a_missing_frozen_table_refuses_rather_than_returning_nothing(tmp_path):
    empty = tmp_path / "no-such-report.txt"
    with pytest.raises(msa.Refused, match="missing"):
        msa.parse_report_table(empty)
    wrong = tmp_path / "wrong.txt"
    wrong.write_text("a report with no aliasing section at all\n")
    with pytest.raises(msa.Refused, match="no section"):
        msa.parse_report_table(wrong)


# ---- precondition 2: the instrument the table was made with -----------------
def test_the_reconstructed_estimator_reproduces_the_frozen_tables_own_rows():
    """`ours/saw` and `ideal/saw` are the two rows this host can re-render from
    source. If they come back, the Surge and Mini V3 rows are on the same
    instrument as anything measured today and a margin means something."""
    mod = msa.load_report_estimator()
    check = msa.assert_report_instrument(mod, msa.parse_report_table())
    assert check["ok"] and check["worst_delta_db"] <= msa.REPRODUCTION_TOL_DB
    assert len(check["rows"]) == 2 * len(msa.REFERENCE_NOTES)


def test_control_todays_estimator_is_refused_against_the_frozen_table():
    """INJECTED: hand the reproduction check TODAY's `audio_measure` instead of
    the report's. It is a better instrument -- Blackman-Harris, floor measured
    per call -- and it is the wrong one, because the frozen rows were read with
    Hann. The check must refuse rather than silently mix two estimators.

    This is the defect the tool exists to avoid: `docs/reference-voice-report.txt`
    was written by 74ce6a0 and the window was replaced by ba14af2 four hours
    later, with no regeneration."""
    with pytest.raises(msa.Refused, match="does not reproduce"):
        msa.assert_report_instrument(am, msa.parse_report_table())


def test_control_an_estimator_from_the_wrong_commit_is_refused():
    """INJECTED: reconstruct the estimator from a commit that is not the one
    that wrote the table."""
    mod = msa.load_report_estimator("ba14af2")          # #132, the rewindowing
    with pytest.raises(msa.Refused, match="does not reproduce"):
        msa.assert_report_instrument(mod, msa.parse_report_table())


def test_control_a_hollowed_out_frozen_table_is_refused():
    """INJECTED: a table with no `ours/saw` row to check against. Nothing can
    be validated, so nothing may be reported."""
    t = msa.parse_report_table()
    del t[("ours", "saw")]
    with pytest.raises(msa.Refused, match="no ours/saw row"):
        msa.assert_report_instrument(msa.load_report_estimator(), t)


# ---- precondition 1: the measured path is the shipped path ------------------
def test_the_2x_path_measured_here_is_the_one_the_voice_renders():
    c = msa.assert_shipped_path()
    assert c["ok"] and c["worst_lsb"] == 0
    assert c["off_vs_on_max_lsb"] > 0, "the oversample_2x flag must change the output"


def test_control_a_voice_that_ignores_oversample_2x_is_caught(monkeypatch):
    """INJECTED: `VoiceFx` silently drops the flag -- the exact shape of this
    repository's own "every bench drove the register write port rather than the
    link". The measured curve would then be the base rate wearing the fixed
    path's name."""
    real = vf.VoiceFx.__init__

    def deaf(self, *a, **kw):
        kw["oversample_2x"] = False
        real(self, *a, **kw)
    monkeypatch.setattr(vf.VoiceFx, "__init__", deaf)
    with pytest.raises(msa.Refused, match="NOT the one VoiceFx"):
        msa.assert_shipped_path()


def test_control_a_voice_that_always_oversamples_is_caught(monkeypatch):
    """INJECTED: the other direction -- the flag is stuck on, so `False` and
    `True` render the same thing and the "before" half of every comparison is
    secretly the "after"."""
    real = vf.VoiceFx.__init__

    def stuck(self, *a, **kw):
        kw["oversample_2x"] = True
        real(self, *a, **kw)
    monkeypatch.setattr(vf.VoiceFx, "__init__", stuck)
    with pytest.raises(msa.Refused, match="not selecting anything"):
        msa.assert_shipped_path()


def test_control_the_stale_results_json_is_named_and_refused():
    c = msa.assert_stale_json_refused()
    assert c["ok"] and c["path"] == "docs/reference-voice-results.json"
    assert c["last_written_by"] != c["report_written_by"], \
        "the refusal only makes sense while the JSON predates the report"


# ---- the readings themselves -----------------------------------------------
def test_a_reading_at_the_estimators_floor_is_labelled_a_limit():
    """The whole 55 Hz column of issue #61 was withdrawn because a reading was
    quoted where only a floor existed. An alias-free closed form reads its own
    floor by construction, so this must come back `floor_limited`."""
    y, f0 = msa._ideal("saw", 33)
    r = msa.reading(y, f0, "band-limited ideal")
    assert r["floor_limited"] is True, r
    assert r["headroom_db"] < msa.MIN_HEADROOM_DB


def test_a_reading_well_clear_of_the_floor_is_not_labelled_a_limit():
    """The control on the control: the label has to be able to be False, or it
    is not reporting anything."""
    y, f0 = msa.render_base(64)
    r = msa.reading(y, f0, "base rate")
    assert r["floor_limited"] is False and r["headroom_db"] > 20.0, r
    assert r["window"] == "blackman-harris-4"


def test_the_decimator_is_what_helps_and_the_drop_control_is_what_hurts():
    """The improvement must be attributable to the FIR and not to running the
    oscillator faster. Issue #80 measured that keeping the last sub-step is
    ~9.6 dB WORSE than the base rate; that control is carried here so a green
    "after" number has something it is green against."""
    for note in (40, 88):
        yb, f0 = msa.render_base(note)
        ya, _ = msa.render_2x(note)
        yd, _ = msa.render_2x_drop(note)
        base = am.inharmonic_fraction_db(yb, f0).require("base")
        filt = am.inharmonic_fraction_db(ya, f0).require("2x + FIR")
        drop = am.inharmonic_fraction_db(yd, f0).require("2x + drop")
        assert filt < base - 15.0, (note, base, filt)
        assert drop > base + 5.0, (note, base, drop)


def test_the_warmup_is_a_continuation_and_costs_the_reading_nothing():
    """WRONG THEN RIGHT, recorded rather than deleted.

    The warm-up was added on the assumption that a cold decimator -- 30 zero
    samples of history -- fades the record in and inflates the reading. The
    first form of this test asserted `|cold[:30]| < |warm[:30]|` and FAILED:
    0.877 against 0.123. Phase 0 is the sawtooth's own discontinuity, so a cold
    render starts at the edge and a warm one starts part-way down the ramp; the
    comparison was of two different parts of the waveform, not of a transient.

    What is actually true, and what this now asserts:

      * the warm record is EXACTLY the continuation of one long render, so
        warming up skips a transient rather than changing the signal;
      * at the 0.5 s measurement length the transient is 30 samples in 24 000
        and moves the reading by ~0.001 dB. **The published curve does not
        depend on this choice**, which is worth knowing before anyone spends
        time defending it."""
    n = 4096
    long_, _ = msa.render_2x(40, n=n + msa.WARMUP, warmup=0)
    warm, _ = msa.render_2x(40, n=n, warmup=msa.WARMUP)
    assert np.array_equal(long_[msa.WARMUP:], warm), \
        "the decimator's history must make a warm render the tail of a long one"
    for note in (40, 100):
        cold, f0 = msa.render_2x(note, warmup=0)
        full, _ = msa.render_2x(note, warmup=msa.WARMUP)
        a = am.inharmonic_fraction_db(cold, f0).require("cold")
        b = am.inharmonic_fraction_db(full, f0).require("warm")
        assert abs(a - b) < 0.05, (note, a, b)


def test_the_verdict_is_computed_from_the_tables_and_not_written_by_hand():
    """A hand-copied verdict is a number with no instrument behind it."""
    alias = [dict(note=40, before=dict(value=-40.0), after=dict(value=-60.0),
                  improvement_db=20.0),
             dict(note=100, before=dict(value=-28.0), after=dict(value=-52.0),
                  improvement_db=24.0)]
    ref = [dict(ours_2x_floor_limited=True, note=33,
                margins=dict(surge=dict(margin_db=99.0), miniv3=dict(margin_db=99.0))),
           dict(ours_2x_floor_limited=False, note=45,
                margins=dict(surge=dict(margin_db=10.0), miniv3=dict(margin_db=1.0)))]
    v = msa.verdict(alias, ref)
    assert v["before_slope_db_per_octave"] == pytest.approx(2.4, abs=0.01)
    assert v["after_slope_db_per_octave"] == pytest.approx(1.6, abs=0.01)
    assert v["floor_limited_rows"] == [33]
    # the floor-limited row must not reach the quoted range
    assert v["gap_to_surge_db"] == dict(min=10.0, max=10.0)
    assert v["gap_to_miniv3_db"] == dict(min=1.0, max=1.0)
