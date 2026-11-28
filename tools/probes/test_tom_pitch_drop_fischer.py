"""Known answers, refusals, defeating inputs and injected defects for
tools/probes/tom_pitch_drop_fischer.py (#592).

Everything here except the last section is independent of the Fischer corpus
and of `DrumsFx`'s correctness: the known answers are closed-form tones and
scipy resonators. The corpus tests read GF180_TR808_FISCHER and SKIP (never
pass) without it.

START RED: `TPD592_INJECT=stub pytest tools/probes/test_tom_pitch_drop_fischer.py`
runs the whole file against the estimator stub (every estimate NO-DROP). The
known-answer tests for non-zero drops must fail; the record of that run is in
docs/scorecard/tom-pitch-drop-592/README.md.

The CLI's `knownanswer` verdict is read off the record's rows against the
declared grid. The inputs that defeated the counter-only verdict it replaced
-- an empty generator, a 3 / 3 summary with no rows, a missing case, a
duplicate standing in for one -- are the committed controls (#1451).
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys
from collections import Counter

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_pitch_drop_fischer as tp  # noqa: E402

REFS = os.environ.get("GF180_TR808_FISCHER")
needs_refs = pytest.mark.skipif(not REFS, reason="GF180_TR808_FISCHER (the Fischer corpus) not set")
_ENV_INJECT = {s for s in os.environ.get("TPD592_INJECT", "").split(",") if s}


@pytest.fixture(autouse=True)
def _inject():
    """Every test runs clean unless TPD592_INJECT names a mutant (start red)."""
    tp.INJECT = set(_ENV_INJECT)
    yield
    tp.INJECT = set()


# ---- known answers: phase-aware, independent of the model -----------------
KA = tp.ka_cases(full=False)


@pytest.mark.parametrize("case", KA, ids=[
    f"{c['family']}-{c['f_inf']:.0f}Hz-ph{(c['phi0'] or 0):.2f}-E{c['E']}-tau{c['tau']}"
    + (f"-{c.get('numer_name')}" if c["family"] == "lti" else "") for c in KA])
def test_known_answer(case):
    est = tp.estimate(tp.ka_signal(case), case["sr"])
    chk = tp.ka_check(case, est)
    assert chk["ok"], chk["why"]


@pytest.mark.parametrize("f", tp.KA_F)
@pytest.mark.parametrize("ph", tp.KA_PHASE)
def test_phase_only_is_never_a_drop(f, ph):
    """A constant-pitch tone, any onset phase: NO-DROP. This is the case the
    #379 gate reads as +37..+39 cents."""
    c = dict(family="base", f_inf=f, E=0.0, tau=0.02, tau_a=tp.ka_tau_a(f), phi0=ph,
             noise_db=-70.0, h2_db=-35.0, strike_db=-6.0, sr=48000)
    est = tp.estimate(tp.ka_signal(c), 48000)
    assert est["class"] == "NO-DROP", est


@pytest.mark.parametrize("numer", list(tp.KA_NUMER))
def test_the_gates_onset_phase_fixture_reads_no_drop_here(numer):
    """tom_conga_gate's own known answer: an LTI band-pass resonator with no
    pitch change reads > 30 cents in the gate's first frame. Here: NO-DROP."""
    f0, tau = tp.KA_LTI["LT"]
    est = tp.estimate(tp.lti_resonator(f0, tau, tp.KA_NUMER[numer]), 48000)
    assert est["class"] == "NO-DROP", est


def test_the_gate_does_read_that_fixture_as_an_onset_excess():
    """The other half of the comparison, so the test above is not vacuous:
    the gate's track DOES read the band-pass resonator as an excess."""
    import tom_conga_gate as tc
    f0, tau = tc.KA_VOICES["LT"]
    assert tc.ka_pitch_onset(f0, tau, (1.0, 0.0, -1.0), "LT")["cents_first_15ms"][0] > 30.0


@pytest.mark.parametrize("E,tau", [(0.06, 0.020), (0.15, 0.012)])
def test_drop_recovered_at_44k1_and_48k(E, tau):
    for sr in (44100, 48000):
        c = dict(family="base", f_inf=101.0, E=E, tau=tau, tau_a=tp.ka_tau_a(101.0), phi0=1.0,
                 noise_db=-70.0, h2_db=-35.0, strike_db=-6.0, sr=sr)
        chk = tp.ka_check(c, tp.estimate(tp.ka_signal(c), sr))
        assert chk["ok"], (sr, chk["why"])


# ---- reason-specific refusals, each with its defeating input ---------------
def test_refuses_nonfinite_before_silence():
    """Defeating input for a silence guard: NaN (IEEE comparisons are False)."""
    assert tp.estimate(np.full(48000, np.nan), 48000)["reason"] == "non-finite"
    x = tp.analytic(90.0, 0.06, 0.02, 0.09, 0.0, sr=48000)
    x[100] = np.inf
    assert tp.estimate(x, 48000)["reason"] == "non-finite"


def test_refuses_silence():
    x = tp.analytic(90.0, 0.06, 0.02, 0.09, 0.0, sr=48000) * 1e-5
    assert tp.estimate(x, 48000)["reason"] == "silent"
    assert tp.estimate(np.zeros(48000), 48000)["reason"] == "silent"


def test_refuses_wrong_sample_rate():
    x = tp.analytic(90.0, 0.06, 0.02, 0.09, 0.0, sr=44100)
    assert tp.estimate(x, 22050)["reason"] == "sample-rate"


def test_refuses_too_short_and_padding_cannot_rescue_it():
    """Rule 8's #139 adversary: appending digital silence to a short record."""
    x = tp.analytic(90.0, 0.06, 0.02, tp.ka_tau_a(90.0), 0.0, sr=44100, seconds=0.07)
    assert tp.estimate(x, 44100)["reason"] == "too-short"
    assert tp.estimate(np.concatenate([x, np.zeros(44100)]), 44100)["reason"] == "too-short"


def test_refuses_unobservable_relaxation():
    """tau 3 ms at 82 Hz: the curvature spans < MIN_OBS_HALFCYCLES half-cycles."""
    x = tp.analytic(82.0, 0.15, 0.003, tp.ka_tau_a(82.0), 0.0, sr=44100)
    assert tp.estimate(x, 44100)["reason"] == "relaxation-unobservable"


def test_refuses_route_disagreement_on_the_beating_tone():
    est = tp.estimate(tp.beating_tone(), 48000)
    assert est["class"] == "REFUSED" and est["reason"] == "route-disagreement", est


def test_known_blind_spot_a_close_partial_can_mask_a_drop():
    """STATED, not fixed (rule 8 form 2): a second partial 3 % BELOW at -10 dB
    makes both routes agree on NO-DROP for a real E 0.06 drop. The estimator
    reports the absence of a detectable drop, not a refusal. If this ever
    starts reading DROP or REFUSED, update the record of the blind spot."""
    f, ta = 135.0, tp.ka_tau_a(135.0)
    y = tp.analytic(f, 0.06, 0.02, ta, 0.3) + tp.analytic(f * 0.97, 0.0, 0.02, ta * 1.5, 1.0) * 10 ** (-10 / 20)
    assert tp.estimate(y, 48000)["class"] == "NO-DROP"


def _wav(path: pathlib.Path, y, sr=44100):
    import wave
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())


def test_corpus_reader_refuses_wrong_hash_missing_and_unregistered(tmp_path):
    y = tp.analytic(90.0, 0.05, 0.02, tp.ka_tau_a(90.0), 0.0, sr=44100)
    _wav(tmp_path / "lt8" / "LT50.WAV", y)
    good = tp._sha(tmp_path / "lt8" / "LT50.WAV")
    x, sr = tp.load_take(tmp_path, "LT50", {"lt8/LT50.WAV": good})
    assert sr == 44100 and len(x) > 0
    for table, cond, reason in (({"lt8/LT50.WAV": "0" * 64}, "LT50", "wrong-hash"),
                                ({}, "LT50", "unregistered-take"),
                                ({"lt8/LT25.WAV": good}, "LT25", "missing-take")):
        with pytest.raises(tp.Refused) as e:
            tp.load_take(tmp_path, cond, table)
        assert e.value.reason == reason


def test_corpus_reader_refuses_a_silent_take_even_with_the_right_hash(tmp_path):
    _wav(tmp_path / "lt8" / "LT50.WAV", np.zeros(44100))
    good = tp._sha(tmp_path / "lt8" / "LT50.WAV")
    with pytest.raises(tp.Refused) as e:
        tp.load_take(tmp_path, "LT50", {"lt8/LT50.WAV": good})
    assert e.value.reason == "silent"


# ---- the shipped side: the production host's staircase --------------------
@pytest.mark.parametrize("sound", tp.TOMS)
def test_production_host_staircase_is_the_reference_staircase(sound):
    for r in (0.9, 1.0, 1.1):
        st = tp.staircase(sound, r)            # REFUSES on any mismatch
        assert st["n_writes"] == 14
        f = [hz for _, hz in st["steps"]]
        assert f[0] > f[-1] > st["f0_image"]   # endpoint f0 (1 + E e^-3), not f0 (#598)
        assert all(a >= b for a, b in zip(f, f[1:]))


def test_unity_render_is_the_production_solo_render():
    tp.assert_unity_is_shipped(("LT",))


def test_horizon_is_not_the_time_constant():
    """TOM_DROP_MS (60 ms) is a scheduling horizon; the continuous law's tau is
    20 ms, and the REALISED staircase reads shorter still (interval means, then
    a held endpoint above f0). A reader that took 60 ms for tau would be 3-4x
    out; this pins the shipped render's measured tau well below 60 ms."""
    import drums_fx as dx
    m = tp.measure_ours("LT", 1.0)
    assert m["render"]["class"] == "DROP"
    assert m["carry"]["ok"], m["carry"]
    tau_ms = m["render"]["tau"] * 1e3
    assert 5.0 < tau_ms < dx.TOM_DROP_MS / 3.0


def test_omitted_host_writes_break_the_carry_check():
    tp.INJECT = {"omit-host-writes"}
    m = tp.measure_ours("LT", 1.0)
    assert m["render"]["class"] == "NO-DROP", m["render"]
    assert not m["carry"]["ok"]


# ---- development / confirmation discipline --------------------------------
PRE = {"rules": {"min_dev_rows": 2, "min_confirm_rows": 2, "confirm_frac": 0.75, "jnd_cents": 5.0,
                 "tau_ratio_threshold": 1.25},
       "adequacy": {"independent_confirmation": "REFUSED: test", "scope": "test"},
       "split_of": {"LT25": "development", "LT50": "development", "LT75": "development",
                    "LT00": "confirmation", "LT10": "confirmation", "MT00": "confirmation",
                    "MT25": "confirmation"}}
CONF = ("LT00", "LT10", "MT00", "MT25")


def _row(c, split, d, lt=0.0, u=1.0):
    return {"split": split, "take": tp.take_rel(c), "class": "MEASURED",
            "discrepancy": {"d_cents": d, "u_cents": u, "ln_tau_ratio": lt, "u_ln_tau": 0.05}}


def _dev(d1=30.0, d2=34.0, lt1=0.0, lt2=0.0):
    return {"LT25": _row("LT25", "development", d1, lt1), "LT50": _row("LT50", "development", d2, lt2)}


def test_fit_refuses_a_non_development_row():
    with pytest.raises(tp.Refused) as e:
        tp.fit({"LT25": _row("LT25", "development", 10.0), "LT00": _row("LT00", "confirmation", 10.0)}, PRE)
    assert e.value.reason == "fit-reads-non-development"


@pytest.mark.parametrize("rows", [
    # reserved identity relabelled development
    {"LT25": _row("LT25", "development", 1.0), "LT00": _row("LT00", "development", 1.0)},
    # unknown identity
    {"LT25": _row("LT25", "development", 1.0), "XX99": _row("XX99", "development", 1.0)},
    # row filed under another condition's id (take path disagrees)
    {"LT25": _row("LT25", "development", 1.0), "LT50": _row("LT00", "development", 1.0)},
])
def test_fit_binds_rows_to_the_frozen_split_not_the_label(rows):
    with pytest.raises(tp.Refused) as e:
        tp.fit(rows, PRE)
    assert e.value.reason == "fit-reads-non-development"


def test_fit_refuses_a_prereg_without_split_of():
    pre = {k: v for k, v in PRE.items() if k != "split_of"}
    with pytest.raises(tp.Refused) as e:
        tp.fit(_dev(), pre)
    assert e.value.reason == "prereg-no-split_of"


def test_fit_with_too_few_rows_is_not_identifiable():
    r = tp.fit({"LT25": _row("LT25", "development", 10.0)}, PRE)
    assert r["status"] == "REFUSED" and "not-identifiable" in r["reason"]


def test_confirmation_confirms_a_systematic_discrepancy():
    fit = tp.fit(_dev(30.0, 34.0, 0.5, 0.6), PRE)
    conf = {c: _row(c, "confirmation", 28.0 + i, 0.5) for i, c in enumerate(CONF)}
    j = tp.judge_confirmation(conf, fit, PRE)
    assert j["magnitude"]["verdict"] == "CONFIRMED"
    assert j["relaxation"]["verdict"] == "CONFIRMED"
    # a confirmed MAGNITUDE discrepancy is never a justified repair here: the
    # Fischer accent is undocumented (prereg adequacy.accent_confound)
    assert j["decision"]["magnitude"].startswith("not identifiable (accent confound)")
    assert j["decision"]["relaxation"].startswith("repair supported")
    assert j["decision"]["independent_confirmation"].startswith("REFUSED")


def test_confirmation_rejects_a_discrepancy_that_does_not_hold():
    fit = tp.fit(_dev(), PRE)
    conf = {c: _row(c, "confirmation", (-1) ** i * 2.0) for i, c in enumerate(CONF)}
    j = tp.judge_confirmation(conf, fit, PRE)
    assert j["magnitude"]["verdict"] == "NOT-CONFIRMED"
    assert j["decision"]["magnitude"] == "unsupported"


def test_confirmation_with_too_few_rows_is_refused():
    fit = tp.fit(_dev(), PRE)
    j = tp.judge_confirmation({"LT00": _row("LT00", "confirmation", 30.0)}, fit, PRE)
    assert j["magnitude"]["verdict"] == "REFUSED"
    assert j["decision"]["magnitude"] == "not identifiable"


def test_confirmation_pooling_development_rows_is_caught():
    assert tp._prop_confirmation_disjoint()
    tp.INJECT = {"confirm-pools-dev"}
    assert not tp._prop_confirmation_disjoint()


def test_confirmation_refuses_a_supplied_development_row_without_injection():
    """Defeating input for the normal branch: development rows MIXED INTO the
    supplied set (no --inject) must be REFUSED, not silently filtered out."""
    fit = tp.fit(_dev(), PRE)
    conf = {c: _row(c, "confirmation", 30.0) for c in CONF}
    conf["LT75"] = _row("LT75", "development", 30.0)
    assert not tp.INJECT
    j = tp.judge_confirmation(conf, fit, PRE)
    assert j["development_rows_in_confirmation"] == ["LT75"]
    assert j["magnitude"] == j["relaxation"] == {"verdict": "REFUSED", "reason": "confirmation-pooled-development"}


@pytest.mark.parametrize("key,row", [
    ("LT25", _row("LT25", "confirmation", 30.0)),    # development identity relabelled confirmation
    ("XX99", _row("XX99", "confirmation", 30.0)),    # unknown identity
    ("MT25", _row("LT00", "confirmation", 30.0)),    # take path of another condition
])
def test_confirmation_binds_rows_to_the_frozen_split_not_the_label(key, row):
    fit = tp.fit(_dev(), PRE)
    conf = {c: _row(c, "confirmation", 30.0) for c in CONF if c != key}
    conf[key] = row
    j = tp.judge_confirmation(conf, fit, PRE)
    assert key in j["development_rows_in_confirmation"]
    assert j["magnitude"] == {"verdict": "REFUSED", "reason": "confirmation-pooled-development"}


# ---- optimizer convergence is asserted, not assumed -----------------------
class _Res:
    def __init__(self, x, cost, success):
        self.x, self.cost, self.success = np.asarray(x, float), cost, success
        self.jac, self.fun = np.eye(len(self.x)), np.zeros(len(self.x))


def _signal():
    sr = 48000
    t = np.arange(int(0.6 * sr)) / sr
    return 150.0 * 0 + np.exp(-t / 0.2) * np.sin(2 * np.pi * 120 * t), sr


def test_route_a_refuses_when_no_start_converged(monkeypatch):
    x, sr = _signal()
    monkeypatch.setattr(tp, "least_squares", lambda f, q0, **k: _Res(q0, 1e-12, False))
    with pytest.raises(tp.Refused) as e:
        tp.route_a(x, sr)
    assert e.value.reason == "fit-failed"


def test_route_a_unconverged_lower_cost_never_displaces_a_converged_fit(monkeypatch):
    x, sr = _signal()
    real, kept = tp.least_squares, []

    def mixed(f, q0, **k):
        if not kept:    # start 1: the honest, converged fit
            kept.append(real(f, q0, **k))
            return kept[0]
        return _Res(kept[0].x + 1.0, 0.0, False)   # cost 0 beats it, but did not converge
    monkeypatch.setattr(tp, "least_squares", mixed)
    got = tp.route_a(x, sr)
    assert got["f_inf"] == pytest.approx(float(kept[0].x[0]))


def test_route_b_refuses_an_unconverged_refinement(monkeypatch):
    class R:
        x, fun, success = 0.0, 1.0, False
    monkeypatch.setattr(tp, "minimize_scalar", lambda *a, **k: R())
    t = np.linspace(0, 0.3, 40)
    with pytest.raises(tp.Refused) as e:
        tp.fit_halfcycles(t, 100 * t + 1.0 - np.exp(-t / 0.05))
    assert e.value.reason == "fit-failed"


# ---- the CLI's verdict: the rows are the evidence, the counters are not ----
#: the archived passing run: 195 executed rows, every one inside its bounds
KA_ARCHIVE = tp.PREREG.parent / "rerun-8fedbf4" / "knownanswer.json"


def _archived_ka():
    return json.loads(KA_ARCHIVE.read_text())


def _good_controls():
    clean = {p: True for p in tp.PROPERTIES}
    return {"clean": clean, "clean_again": dict(clean),
            "mutants": {m: {"verdict": "CAUGHT"} for m in tp.INJECTIONS}}


def _out_of_bounds(rec):
    """The first executed drop reads outside its frozen bounds; returns its index."""
    i = next(k for k, r in enumerate(rec["rows"]) if r["class"] == "DROP")
    rec["rows"][i].update(ok=False, why="E 0.0900 vs 0.0200 (|e| 0.0700)")
    return i


def _cli_knownanswer(tmp_path, monkeypatch, record=None, cases=None):
    """The normal entry point, `knownanswer --out`. `record` replaces the record
    generator wholesale; `cases` replaces the case generator and lets the REAL
    `knownanswer` build the record. Provenance is stubbed (a dirty tree is its
    own precondition, not the verdict under test). Returns the exit status and
    the record the CLI wrote."""
    monkeypatch.setattr(tp, "provenance", lambda refs: {"commit": "x"})
    monkeypatch.setattr(tp, "close_provenance", lambda prov: prov)
    if record is not None:
        monkeypatch.setattr(tp, "knownanswer", lambda *a, _r=record, **k: json.loads(json.dumps(_r)))
    if cases is not None:
        monkeypatch.setattr(tp, "ka_cases", lambda full: list(cases))
    out = tmp_path / "knownanswer.json"
    return tp.main(["knownanswer", "--out", str(out)]), json.loads(out.read_text())


@pytest.mark.parametrize("cmd", ["knownanswer", "controls"])
def test_cli_exit_status_is_red_for_a_failed_verification_then_green(cmd, tmp_path, monkeypatch):
    """The command's own acceptance path: bad record -> nonzero (and not the
    refusal status 2), the record is still written; good record -> 0. The
    known-answer pair is the ARCHIVED run (195 executed rows), not a summary
    that claims success (#1451)."""
    good = {"knownanswer": _archived_ka, "controls": _good_controls}[cmd]()
    bad = json.loads(json.dumps(good))
    if cmd == "knownanswer":
        _out_of_bounds(bad)
    else:
        bad["mutants"][tp.INJECTIONS[0]]["verdict"] = "MISSED"
    fn = {"knownanswer": "knownanswer", "controls": "controls"}[cmd]
    out = tmp_path / "rec.json"
    # provenance REFUSES a dirty tree (its own precondition); stub it so the
    # exit-status path is what is observed
    monkeypatch.setattr(tp, "provenance", lambda refs: {"commit": "x"})
    monkeypatch.setattr(tp, "close_provenance", lambda prov: prov)
    for rec, want in ((bad, 1), (good, 0)):
        monkeypatch.setattr(tp, fn, lambda *a, _r=rec, **k: json.loads(json.dumps(_r)))
        out.unlink(missing_ok=True)
        assert tp.main([cmd, "--out", str(out)]) == want
        assert out.exists()  # the diagnostic record is retained either way


def test_the_archived_passing_record_is_the_whole_declared_grid():
    """What every test below leans on: the archive is 195 EXECUTED rows, each
    inside its bounds, covering the declared grid exactly. A grid change fails
    this, and the archive must be re-run before it is the passing case again."""
    rec = _archived_ka()
    assert rec["n"] == rec["n_ok"] == len(rec["rows"]) == 195
    assert all(r["ok"] is True for r in rec["rows"])
    archived = Counter(tp.ka_identity(r["case"]) for r in rec["rows"])
    assert len(archived) == 195
    assert archived == Counter(tp.ka_identity(c) for c in tp.ka_cases(full=True))
    assert tp.verification_failures("knownanswer", rec) == []


def test_cli_knownanswer_refuses_an_empty_generator(tmp_path, monkeypatch, capsys):
    """#1451's reproduction: the REAL `knownanswer` with a case generator that
    yields nothing. Its two counters still agree (0 / 0) -- all the old verdict
    read -- so this exited 0."""
    status, rec = _cli_knownanswer(tmp_path, monkeypatch, cases=[])
    assert status == 1
    assert rec["rows"] == [] and rec["n"] == rec["n_ok"] == 0     # the record that used to pass
    assert "FAILED: known answers: 0 / 195 declared cases present" in capsys.readouterr().out


@pytest.mark.parametrize("copies", [1, 2], ids=["truncated", "repeated"])
def test_cli_rejects_a_generator_broken_before_module_initialization(tmp_path, capsys, copies):
    # Install the defect at definition time, so even import-time calls see it.
    path = pathlib.Path(tp.__file__)
    definition = "def ka_cases("
    source = path.read_text()
    assert source.count(definition) == 1
    source = source.replace(definition, "@shorten\n" + definition)
    probe = {"__file__": str(path), "__name__": "shortened_probe",
             "shorten": lambda generate: lambda full: generate(full)[:1] * copies}
    exec(compile(source, str(path), "exec"), probe)
    probe["provenance"] = lambda refs: {}
    probe["close_provenance"] = lambda prov: prov

    out = tmp_path / "shortened.json"
    status = probe["main"](["knownanswer", "--out", str(out)])
    rows = json.loads(out.read_text())["rows"]
    assert len(rows) == copies and all(row["ok"] is True for row in rows)
    assert len({tp.ka_identity(row["case"]) for row in rows}) == 1
    assert status == 1
    diagnostic = capsys.readouterr().out
    assert "FAILED: known answers: 1 / 195 declared cases present" in diagnostic
    if copies == 2:
        assert "repeated in place of a declared case" in diagnostic


def test_cli_knownanswer_refuses_success_counters_with_no_rows(tmp_path, monkeypatch, capsys):
    """The fixture this file used to PASS the CLI on: a summary claiming
    3 / 3 with not one row behind it."""
    status, rec = _cli_knownanswer(tmp_path, monkeypatch, record={"n": 3, "n_ok": 3, "rows": []})
    assert status == 1
    assert rec["rows"] == []     # the diagnostic record is still written
    assert "FAILED: known answers: 0 / 195 declared cases present" in capsys.readouterr().out


def test_cli_knownanswer_refuses_a_missing_case(tmp_path, monkeypatch, capsys):
    """A run that lost one case keeps its counters equal (194 / 194)."""
    rec = _archived_ka()
    gone = rec["rows"].pop(100)["case"]
    rec["n"] = rec["n_ok"] = 194
    status, _ = _cli_knownanswer(tmp_path, monkeypatch, record=rec)
    assert status == 1
    out = capsys.readouterr().out
    assert "FAILED: known answers: 194 / 195 declared cases present" in out
    assert tp.ka_identity(gone) in out     # it says WHICH case the evidence lacks


def test_cli_knownanswer_refuses_a_duplicate_in_place_of_a_case(tmp_path, monkeypatch, capsys):
    """Same count, same 195 / 195: one case's row is standing in for another's."""
    rec = _archived_ka()
    gone = rec["rows"][100]["case"]
    rec["rows"][100] = json.loads(json.dumps(rec["rows"][101]))
    status, _ = _cli_knownanswer(tmp_path, monkeypatch, record=rec)
    assert status == 1
    out = capsys.readouterr().out
    assert "FAILED: known answers: 194 / 195 declared cases present" in out
    assert tp.ka_identity(gone) in out
    assert "repeated in place of a declared case" in out


def test_cli_knownanswer_keeps_an_executed_case_outside_its_bounds_red(tmp_path, monkeypatch, capsys):
    """Completeness is not correctness: a full grid with one bad measurement is
    still a FAILED measurement (exit 1), not a precondition refusal (2)."""
    rec = _archived_ka()
    i = _out_of_bounds(rec)
    status, _ = _cli_knownanswer(tmp_path, monkeypatch, record=rec)
    assert status == 1
    assert (f"{tp.ka_identity(rec['rows'][i]['case'])} ran outside its frozen bounds: "
            "E 0.0900 vs 0.0200 (|e| 0.0700)") in capsys.readouterr().out


def test_knownanswer_verdict_reads_each_row_as_the_record_serialises_it():
    """The verdict judges each row as the record serialises it: a numpy
    `True_` (which `is True` would reject) and an archived JSON `true` read
    the same. A malformed row or record is a failure to REPORT, not an
    exception out of the verdict."""
    whole = [{"case": c, "ok": np.True_} for c in tp.ka_cases(full=True)]
    assert tp.verification_failures("knownanswer", {"n": 195, "n_ok": 195, "rows": whole}) == []
    no_id = _archived_ka()
    no_id["rows"][7] = {"ok": True}
    assert any("row 7 carries no case identity" in w for w in tp.verification_failures("knownanswer", no_id))
    for bare in ({"n": 195, "n_ok": 195}, {"rows": None}):
        assert tp.verification_failures("knownanswer", bare) == ["known answers: the record carries no row list"]


@pytest.mark.parametrize("available", [False, True], ids=["missing", "truncated"])
def test_cli_refuses_an_unavailable_or_changed_reference(tmp_path, monkeypatch, capsys, available):
    archive = tmp_path / "archive.json"
    if available:
        archive.write_text(json.dumps({"rows": _archived_ka()["rows"][:1]}))
    monkeypatch.setattr(tp, "KA_ARCHIVE", archive)
    monkeypatch.setattr(tp, "knownanswer", lambda full: {"rows": []})
    monkeypatch.setattr(tp, "provenance", lambda refs: {})
    monkeypatch.setattr(tp, "close_provenance", lambda prov: prov)
    out = tmp_path / "result.json"
    assert tp.main(["knownanswer", "--out", str(out)]) == 2
    assert "REFUSED: knownanswer-archive" in capsys.readouterr().out
    assert not out.exists()


def test_cli_controls_red_on_non_caught_variants_and_dirty_clean():
    base = _good_controls()
    assert tp.verification_failures("controls", base) == []
    nv = json.loads(json.dumps(base))
    nv["mutants"][tp.INJECTIONS[0]]["verdict"] = "NO-VERDICT"
    assert tp.verification_failures("controls", nv)
    cl = json.loads(json.dumps(base))
    cl["clean_again"][tp.PROPERTIES[0]] = "NO-VERDICT: boom"
    assert tp.verification_failures("controls", cl)


def test_confirm_runs_once(tmp_path):
    out = tmp_path / "confirm.json"
    out.write_text("{}")
    assert tp.main(["confirm", "--refs", str(tmp_path), "--fit", str(out), "--out", str(out)]) == 2


def test_confirm_cannot_be_repeated_by_changing_the_output_path(tmp_path, monkeypatch):
    """DEFEATING INPUT for the once-only guard: a second `confirm` of the same
    preregistration and fit under a DIFFERENT report path. The reservation is
    bound to the preregistration identity, not to the report destination, and
    is claimed atomically BEFORE any reserved take is opened -- so the second
    run is refused having read nothing, and exactly one reserved read happens."""
    monkeypatch.setattr(tp, "PREREG", tmp_path / "prereg.json")
    (tmp_path / "prereg.json").write_text("{}\n")
    monkeypatch.setattr(tp, "provenance", lambda refs: {"refs": str(refs)})
    monkeypatch.setattr(tp, "close_provenance", lambda prov: prov)
    reads = []
    monkeypatch.setattr(tp, "measure_rows", lambda refs, pr, split: reads.append(split) or {})
    fit = tmp_path / "fit.json"
    fit.write_text(json.dumps({"status": "FITTED"}))
    out1, out2 = tmp_path / "confirm-1.json", tmp_path / "confirm-2.json"
    assert tp.main(["confirm", "--refs", str(tmp_path), "--fit", str(fit), "--out", str(out1)]) == 0
    assert tp.main(["confirm", "--refs", str(tmp_path), "--fit", str(fit), "--out", str(out2)]) == 2, \
        "a different report path must not buy a second reserved evaluation"
    assert reads == ["confirmation"], f"the reserved rows were read {len(reads)} times"
    assert out1.exists() and not out2.exists()
    assert tp.confirm_claim_path().exists(), "the claim must outlive the run it guards"


def test_a_refused_fit_never_claims_the_reserved_evaluation(tmp_path, monkeypatch):
    """The no-fit branch reads no reserved data, so it must not consume the
    once-only reservation either: a successor can still confirm, and repeated
    refused-fit confirmations stay possible."""
    monkeypatch.setattr(tp, "PREREG", tmp_path / "prereg.json")
    (tmp_path / "prereg.json").write_text(json.dumps(PRE) + "\n")
    fit = tmp_path / "fit.json"
    fit.write_text(json.dumps({"status": "REFUSED", "reason": "not-identifiable: test"}))
    tp.confirm_record(tmp_path, json.loads((tmp_path / "prereg.json").read_text()), fit)
    assert not tp.confirm_claim_path().exists(), \
        "a no-fit confirm reads nothing reserved, so it must not burn the claim"


def test_a_refused_fit_leaves_the_reserved_rows_unread(tmp_path):
    """With no development fit there is nothing to confirm: the record is the
    rule's REFUSED and NO reserved take is opened (an empty refs directory
    would otherwise REFUSE with missing-take). Asymmetric values: each
    quantity must be refused for the *exact* no-fit reason with an EMPTY
    contamination list -- a NOT-READ row that failed its own identity binding
    would be misread as pooled development data and mask the no-fit reason."""
    fit = tmp_path / "fit.json"
    fit.write_text(json.dumps({"status": "REFUSED", "reason": "not-identifiable: test"}))
    rec = tp.confirm_record(tmp_path, tp.load_prereg(), fit)
    assert rec["reserved_rows_read"] is False
    assert all(r["class"] == "NOT-READ" for r in rec["rows"].values())
    assert all(r["take"] == tp.take_rel(k) for k, r in rec["rows"].items()), \
        "unread rows must carry their reserved take identity"
    assert rec["judgement"]["development_rows_in_confirmation"] == []
    for quantity in ("magnitude", "relaxation"):
        assert rec["judgement"][quantity]["verdict"] == "REFUSED"
        assert rec["judgement"][quantity]["reason"] == "no development fit", \
            f"{quantity}: refused for the wrong reason (false contamination?)"
    assert rec["judgement"]["decision"]["magnitude"] == "not identifiable"
    assert rec["judgement"]["decision"]["independent_confirmation"].startswith("REFUSED")


# ---- injected defects: each must move its declared property ----------------
@pytest.mark.parametrize("mutant", tp.INJECTIONS)
def test_injection_moves_its_declared_property(mutant, tmp_path):
    """Rule 5 conditions: clean holds the property; the mutant RUNS to a bool
    (not an exception: that would be NO VERDICT); the property flips."""
    fns = {"zero_drop_any_phase": tp._prop_zero_drop_phase,
           "nonzero_drop_recovered": tp._prop_nonzero_drop,
           "route_disagreement_refused": tp._prop_route_disagreement_refused,
           "staircase_carried": tp._prop_staircase_carry,
           "wrong_hash_refused": lambda: tp._prop_hash_refusal(tmp_path),
           "confirmation_disjoint": tp._prop_confirmation_disjoint}
    fn = fns[tp.DECLARED[mutant]]
    tp.INJECT = set()
    assert fn() is True
    tp.INJECT = {mutant}
    got = fn()
    tp.INJECT = set()
    assert isinstance(got, bool), f"NO VERDICT: {got!r}"
    assert got is False, f"{mutant} did not move {tp.DECLARED[mutant]}"


# ---- the corpus (skipped without it) --------------------------------------
@needs_refs
def test_frozen_corpus_hashes_match():
    pre = tp.load_prereg()
    refs = pathlib.Path(REFS)
    for rel, want in pre["corpus"]["sha256"].items():
        assert tp._sha(refs / rel) == want, rel


def test_prereg_freezes_the_estimator_constants():
    """The constants the preregistration names are the constants that run."""
    pre = tp.load_prereg()
    for k, v in pre["estimator"]["constants"].items():
        got = getattr(tp, k)
        got = list(got) if isinstance(got, tuple) else got
        assert got == v, (k, got, v)


def test_prereg_splits_are_disjoint_and_cover_the_toms():
    pre = tp.load_prereg()
    sp = pre["split_of"]
    assert set(sp.values()) == {"development", "confirmation"}
    assert sorted(sp) == sorted(f"{s}{c}" for s in tp.TOMS for c in ("00", "25", "50", "75", "10"))
    assert set(pre["corpus"]["sha256"]) == {tp.take_rel(c) for c in sp}
    json.dumps(pre)


# ---- a fit record that cannot be read is REFUSED, not a traceback (#1604) --
def _fit(monkeypatch, tmp_path, capsys, record):
    """`main(["fit", ...])` over a record under tmp_path. `provenance()` refuses
    on a dirty tree, and that precondition is not what these controls are
    about, so it is answered rather than exercised; the record and the reader
    are real."""
    monkeypatch.setattr(tp, "provenance", lambda refs: {"commit": "stub"})
    rec, out = tmp_path / "measure.json", tmp_path / "fit.json"
    rec.write_text(json.dumps(record))
    code = tp.main(["fit", str(rec), "--out", str(out)])
    return code, capsys.readouterr().out, out


def test_a_fit_record_without_rows_is_refused(monkeypatch, tmp_path, capsys):
    # #1604's own input: `KeyError: 'rows'`, exit 1, no --out, before the repair
    code, out, written = _fit(monkeypatch, tmp_path, capsys, {})
    assert code == 2 and "REFUSED" in out and "'rows'" in out
    assert not written.exists()


def test_a_fit_record_with_null_rows_is_refused(monkeypatch, tmp_path, capsys):
    code, out, written = _fit(monkeypatch, tmp_path, capsys, {"rows": None})
    assert code == 2 and "'rows'" in out and not written.exists()
