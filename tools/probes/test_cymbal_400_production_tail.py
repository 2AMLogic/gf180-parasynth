"""Controls for tools/probes/cymbal_400_production_tail.py (#400).

    python3 -m pytest tools/probes/test_cymbal_400_production_tail.py -q

Written and committed RED against a stub (git history carries the ordering).
Groups: known answers (planted constants, independent of the instrument),
defeating inputs (preparation mismatch, truncation/floor, filter-skirt leak,
non-finite), refusals (corpus / frozen record / unsupported knob settings),
invariances, and executed mutants (a defect injected into the probe must turn
the matching control red -- a control never seen to fail is not a control).
"""
import hashlib
import inspect
import json
import math
import pathlib
import sys

import numpy as np
import pytest
from scipy.io import wavfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1] / "model"))
import cymbal_400_production_tail as pt  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402
import run_case as rc  # noqa: E402

SR = 48_000


def strike(tau_m, a_m, **kw):
    """Planted strike with a SEPARATE 0.9-2.8 kHz component of known tau and amplitude."""
    return lt.synth(tau_l=0.35, tau_m=tau_m, a_m=a_m, tau_h=0.2, a_h=0.6, **kw)[0]


@pytest.fixture(scope="module")
def ref_long():
    return strike(0.25, 2.0)


# ---------------------------------------------------------------- known answers
@pytest.mark.parametrize("kind,p,r,want", [
    ("energy_db", 5.0, 0.0, "DISCREPANT"), ("energy_db", -5.0, 0.0, "DISCREPANT"),
    ("energy_db", 2.9, 0.0, "MATCH"), ("energy_db", 0.0, 0.0, "MATCH"),
    ("time_ratio", 1.6, 1.0, "DISCREPANT"), ("time_ratio", 0.4, 1.0, "DISCREPANT"),
    ("time_ratio", 1.4, 1.0, "MATCH"), ("time_ratio", 1.0, 1.0, "MATCH"),
])
def test_classify_known_answers(kind, p, r, want):
    assert pt.classify(p, r, kind)["verdict"] == want


@pytest.mark.parametrize("p,r", [(None, 1.0), (1.0, None), (float("nan"), 1.0), (1.0, float("inf"))])
def test_classify_refuses_missing_or_nonfinite_never_matches(p, r):
    assert pt.classify(p, r, "time_ratio")["verdict"] == "REFUSED"


def test_classify_unknown_kind_refuses():
    with pytest.raises(pt.Refused):
        pt.classify(1.0, 1.0, "vibes")


def test_properties_are_reported_separately_energy_match_decay_discrepant(ref_long):
    # tau_m 0.25 -> 0.60 (2.4x longer). Measured rho 0.79 -> 1.47 (+86 %): the 2.4x planted ratio
    # compresses to ~1.9x because Ln shares the band's skirt. SHORTER prods (<=0.15) are REFUSED by
    # the instrument itself, so the discrepant direction that can be planted is "prod outlasts ref".
    prod = strike(0.60, 1.5)
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "MATCH", r["energy"]
    assert r["decay"]["verdict"] == "DISCREPANT", r["decay"]
    assert r["joint"] is False


def test_properties_are_reported_separately_energy_discrepant_decay_match(ref_long):
    prod = strike(0.25, 8.0)                      # a_m 2 -> 8 reads +4.8 dB of M25 share (measured: -6.32 -> -1.47; the share saturates, 4x amplitude is NOT 12 dB), same decay
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "DISCREPANT", r["energy"]
    assert r["decay"]["verdict"] == "MATCH", r["decay"]


def test_null_control_a_record_against_itself_matches_everywhere(ref_long):
    r = pt.compare_records(ref_long, SR, ref_long, SR, prod_has_hh1=True)
    assert r["energy"]["verdict"] == "MATCH" and r["decay"]["verdict"] == "MATCH"


def test_joint_requires_both(ref_long):
    prod = strike(0.60, 8.0)
    r = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert r["joint"] is True


# ---------------------------------------------------------------- invariances
def test_scale_and_prepended_silence_invariance(ref_long):
    base = strike(0.25, 2.0)
    raw = lt.synth(tau_l=0.35, tau_m=0.25, a_m=2.0, tau_h=0.2, a_h=0.6)[0]
    scaled = rc.prepare(0.1 * raw, SR, side="scaled")
    padded = rc.prepare(np.concatenate([np.zeros(SR // 4), raw]), SR, side="padded")
    a = pt.compare_records(base, SR, ref_long, SR, prod_has_hh1=True)
    for v in (scaled, padded):
        b = pt.compare_records(v, SR, ref_long, SR, prod_has_hh1=True)
        assert b["energy"]["verdict"] == a["energy"]["verdict"]
        assert b["decay"]["verdict"] == a["decay"]["verdict"]
        assert b["energy"]["prod"] == pytest.approx(a["energy"]["prod"], abs=0.05)
        assert b["decay"]["prod"] == pytest.approx(a["decay"]["prod"], rel=0.02)


def test_trailing_silence_is_outside_the_domain_so_it_never_silently_changes_a_verdict(ref_long):
    """Trailing digital silence zeroes the floor estimate the Schroeder curve
    subtracts. The declared domain forbids it: the probe must either give the
    same verdict or REFUSE -- never a different verdict."""
    base = strike(0.25, 2.0)
    a = pt.compare_records(base, SR, ref_long, SR, prod_has_hh1=True)
    padded = np.concatenate([base, np.zeros(SR)])
    try:
        b = pt.compare_records(padded, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    for k in ("energy", "decay"):
        assert b[k]["verdict"] in (a[k]["verdict"], "REFUSED"), (k, a[k], b[k])


# ---------------------------------------------------------------- defeating inputs
def test_preparation_mismatch_unprepared_record_is_refused(ref_long):
    raw = np.concatenate([np.zeros(10), np.hanning(64) * 0.5, np.random.default_rng(1).standard_normal(3 * SR) * 0.01])
    with pytest.raises(pt.Refused, match="prepar"):
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True)
    with pytest.raises(pt.Refused, match="prepar"):
        pt.compare_records(ref_long, SR, raw, SR, prod_has_hh1=True)


def test_assert_prepared_accepts_prepare_output(ref_long):
    pt.assert_prepared(ref_long, SR, "x")


@pytest.mark.parametrize("bad", [np.nan, np.inf])
def test_nonfinite_is_refused(ref_long, bad):
    y = ref_long.copy()
    y[-1000] = bad
    with pytest.raises(pt.Refused, match="finite"):
        pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True)


def test_truncation_refuses_decay_instead_of_reporting_a_short_tail(ref_long):
    prod = strike(0.25, 2.0)
    short = prod[: rc.required_lead_samples(SR) + int(0.5 * SR)]   # cut inside the tail
    try:
        r = pt.compare_records(short, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    assert r["decay"]["verdict"] == "REFUSED", r["decay"]
    assert r["joint"] is False


def test_floor_contamination_is_not_a_discrepancy(ref_long):
    noisy = strike(0.25, 2.0, floor=0.05)
    try:
        r = pt.compare_records(noisy, SR, ref_long, SR, prod_has_hh1=True)
    except pt.Refused:
        return
    assert r["decay"]["verdict"] in ("REFUSED", "MATCH"), r["decay"]


def test_filter_skirt_leak_only_band_refuses_not_discrepant(ref_long):
    """Production with NO separate 0.9-2.8 kHz content: its band is the 3.45 kHz
    band-pass's skirt. Reading that against the reference's real content and
    calling it a defect is the #101 failure mode; it must be REFUSED."""
    leak_only = lt.synth(tau_l=0.35, a_m=0.0, tau_h=0.2, a_h=0.6)[0]
    r = pt.compare_records(leak_only, SR, ref_long, SR, prod_has_hh1=False)
    assert r["energy"]["verdict"] == "REFUSED", r["energy"]
    assert r["decay"]["verdict"] == "REFUSED", r["decay"]
    assert "skirt" in (r["energy"]["reason"] + r["decay"]["reason"]).lower() or "leak" in (r["energy"]["reason"] + r["decay"]["reason"]).lower()


# ---------------------------------------------------------------- refusal attribution
# The #400 report once attributed the decay refusal to production when result.json showed the
# REFERENCE had refused it: the reason was built as `p_reason or r_reason` with no side label, so
# neither a reader nor the author could tell whose gate tripped. These plant a refusal on ONE side.
@pytest.mark.parametrize("k", ["energy", "decay"])
def test_reference_only_refusal_names_the_reference_not_production(ref_long, k):
    leak_only = lt.synth(tau_l=0.35, a_m=0.0, tau_h=0.2, a_h=0.6)[0]
    r = pt.compare_records(ref_long, SR, leak_only, SR, prod_has_hh1=True)   # prod qualifies, ref is leak-only
    assert r[k]["verdict"] == "REFUSED", r[k]
    assert r[k]["prod"] is not None, "planted production side should qualify: the plant is wrong"
    assert r[k]["reason"].startswith("reference: "), r[k]["reason"]
    assert "production:" not in r[k]["reason"], r[k]["reason"]
    assert r[k]["refused_by"] == {"production": None, "reference": r[k]["reason"][len("reference: "):]}


@pytest.mark.parametrize("k", ["energy", "decay"])
def test_production_only_refusal_names_production_not_the_reference(ref_long, k):
    leak_only = lt.synth(tau_l=0.35, a_m=0.0, tau_h=0.2, a_h=0.6)[0]
    r = pt.compare_records(leak_only, SR, ref_long, SR, prod_has_hh1=True)   # ref qualifies, prod is leak-only
    assert r[k]["verdict"] == "REFUSED", r[k]
    assert r[k]["ref"] is not None, "planted reference side should qualify: the plant is wrong"
    assert r[k]["reason"].startswith("production: "), r[k]["reason"]
    assert "reference:" not in r[k]["reason"], r[k]["reason"]
    assert r[k]["refused_by"]["reference"] is None and r[k]["refused_by"]["production"]


def test_both_sides_refusing_records_both_reasons():
    leak_only = lt.synth(tau_l=0.35, a_m=0.0, tau_h=0.2, a_h=0.6)[0]
    r = pt.compare_records(leak_only, SR, leak_only, SR, prod_has_hh1=True)
    for k in ("energy", "decay"):
        assert r[k]["verdict"] == "REFUSED", r[k]
        assert "production: " in r[k]["reason"] and "reference: " in r[k]["reason"], r[k]["reason"]
        assert all(r[k]["refused_by"].values()), r[k]["refused_by"]


# ---------------------------------------------------------------- refusals
def test_production_path_has_no_knob_parameters_so_only_cy5025_is_supported():
    params = set(inspect.signature(rc.render_drum_solo).parameters)
    assert not {"tone", "decay", "knobs", "setting"} & params, (
        "render_drum_solo now takes knobs: wire real support into production_supported "
        "and re-freeze, do not leave the refusal stale")
    pt.production_supported("CY5025")
    for s in ("CY0000", "CY5000", "CY2500", "CY7575", "kit_at", "OH00"):
        with pytest.raises(pt.Refused, match="knob|support|setting"):
            pt.production_supported(s)


def test_corpus_missing_is_refused_not_failed(tmp_path):
    with pytest.raises(pt.Refused, match="GF180_TR808_REFS|corpus"):
        pt.require_corpus(tmp_path / "nope", frozen=None)


def test_corpus_hash_mismatch_with_frozen_is_refused(tmp_path):
    d = tmp_path / "cy8"
    d.mkdir()
    (d / "CY5025.WAV").write_bytes(b"not the fischer recording")
    got = pt.require_corpus(tmp_path, frozen=None)
    assert got["CY5025.WAV"]
    with pytest.raises(pt.Refused, match="hash"):
        pt.require_corpus(tmp_path, frozen={"reference_sha256": {"CY5025.WAV": "0" * 64}})


# ---------------------------------------------------------------- executed mutants
def test_mutant_no_prep_assert_lets_the_unprepared_record_through(ref_long):
    raw = np.concatenate([np.zeros(10), np.hanning(64) * 0.5, np.random.default_rng(1).standard_normal(3 * SR) * 0.01])
    with pytest.raises(pt.Refused):
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True)
    try:
        pt.compare_records(raw, SR, ref_long, SR, prod_has_hh1=True, defect="NO_PREP_ASSERT")
    except pt.Refused as e:
        assert "prepar" not in str(e), "mutant still hit the preparation guard: it is not the mutant"


def test_mutant_no_finite_check_bypasses_the_guard_and_downstream_still_refuses(ref_long):
    """The finite guard is DEFENCE IN DEPTH, not the only thing stopping NaN input: with it removed,
    `classify` refuses energy ("a side is missing or non-finite") and cymbal_mid's floor gate refuses
    decay. What the guard adds is an early, side-named refusal of the whole record. This asserts what
    the mutant actually produces, so a downstream change that let NaN through to a verdict goes red."""
    y = ref_long.copy()
    y[-1000] = np.nan
    with pytest.raises(pt.Refused, match="production: record is not finite"):
        pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True)
    r = pt.compare_records(y, SR, ref_long, SR, prod_has_hh1=True, defect="NO_FINITE_CHECK")
    for k in ("energy", "decay"):
        assert r[k]["verdict"] == "REFUSED", (k, r[k])
        assert "record is not finite" not in r[k]["reason"], "mutant still hit the finite guard: not the mutant"
    assert r["joint"] is False


def test_mutant_wrong_hh1_declaration_changes_the_leak_prediction(ref_long):
    prod = strike(0.25, 2.0)
    a = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=False)
    b = pt.compare_records(prod, SR, ref_long, SR, prod_has_hh1=True)
    assert a["energy"]["detail"]["predicted_leak_db_re_L"] != b["energy"]["detail"]["predicted_leak_db_re_L"]


# ---------------------------------------------------------------- properties x defects matrix (rule 4)
# Two properties are decided (energy, decay), so every injected defect is reported as MOVED for the
# properties that saw it and BLIND for the ones that did not -- a property that never sees anything
# is decoration wearing the costume of coverage (docs/verification-rules.md rule 4). Executed, not
# declared: each row below was run, and the report is printed so a reader sees the reasons, not just
# the word MOVED.

DEFECT_RUNS = 0


def _outcome(record, ref, **kw) -> dict:
    """(verdict, prod, ref, reason[, predicted leak]) per property; a record-level refusal
    (compare_records raised) is recorded as such for BOTH properties, with the exception text."""
    try:
        r = pt.compare_records(record, SR, ref, SR, **kw)
    except pt.REFUSED_TYPES as e:
        return {k: ("REFUSED(record-level)", None, None, str(e), None) for k in ("energy", "decay")}
    out = {}
    for k in ("energy", "decay"):
        d = r[k]
        out[k] = (d["verdict"],
                  None if d.get("prod") is None else round(float(d["prod"]), 6),
                  None if d.get("ref") is None else round(float(d["ref"]), 6),
                  d.get("reason", ""),
                  (d.get("detail") or {}).get("predicted_leak_db_re_L") if k == "energy" else None)
    return out


def test_control_matrix_energy_decay_x_defects_is_printed_and_no_defect_goes_unseen(ref_long):
    unprepared = np.concatenate([np.zeros(10), np.hanning(64) * 0.5,
                                 np.random.default_rng(1).standard_normal(3 * SR) * 0.01])
    nan_rec = ref_long.copy()
    nan_rec[-1000] = np.nan
    plants = {
        # defect -> (the defeating input, how compare_records is run with the defect in place)
        "NO_PREP_ASSERT": (unprepared, {}),
        "NO_FINITE_CHECK": (nan_rec, {}),
        "WRONG_HH1": (ref_long, {"prod_has_hh1": False}),
    }
    rows = {}
    for name, (rec, kw) in plants.items():
        defect = name if name != "WRONG_HH1" else None
        hh1 = kw.get("prod_has_hh1", True)
        clean = _outcome(rec, ref_long, prod_has_hh1=True)
        mutant = _outcome(rec, ref_long, defect=defect, prod_has_hh1=hh1)
        rows[name] = {k: (clean[k], mutant[k], "MOVED" if clean[k] != mutant[k] else "BLIND")
                      for k in ("energy", "decay")}
        assert any(cell[2] == "MOVED" for cell in rows[name].values()), (
            f"{name} moved NO property: it is not a control, remove it or fix the plant", rows[name])
    print("\nproperties x defects (executed; clean outcome -> mutant outcome):")
    for name, cells in rows.items():
        for prop, (clean, mutant, seen) in cells.items():
            print(f"  {name:16s} {prop:6s} {seen:5s}  clean={clean} -> mutant={mutant}")
    # The finite guard is defence in depth: its removal must be distinguishable from the
    # downstream refusals that survive it -- the reason changes, the verdicts do not.
    fin = rows["NO_FINITE_CHECK"]
    for prop in ("energy", "decay"):
        assert "not finite" in fin[prop][0][2], (prop, fin[prop][0])
        assert fin[prop][1][0] == "REFUSED", (prop, fin[prop][1])
        assert "not finite" not in fin[prop][1][2], (prop, fin[prop][1])
    assert fin["energy"][0][0] == "REFUSED(record-level)" and fin["energy"][1][0] == "REFUSED"


# ---------------------------------------------------------------- source-tree identity (freeze closure)
# The first freeze pinned six files enumerated by hand from the probe's own imports; the render path
# runs through model/modal_fixed.py, audition/dsp.py, model/fixed.py, model/voice_fx.py,
# and preparation runs through
# model/audio_measure.py -- none was pinned, so an edit to the modal bank after freeze changed the
# render under an unchanged guard while result.json attributed it to the frozen commit.

def test_source_closure_covers_the_render_and_preparation_path():
    need = {"model/modal_fixed.py", "audition/dsp.py", "model/fixed.py", "model/voice_fx.py",
            "model/audio_measure.py", "tools/preparation_contract.py"}
    assert need <= set(pt.SOURCE_FILES), f"closure missing {sorted(need - set(pt.SOURCE_FILES))}"


def _no_render(*a, **k):
    raise AssertionError("render ran: a refusal must happen BEFORE rendering")


def _fake_corpus(tmp_path, samples) -> dict:
    d = tmp_path / "cy8"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "CY5025.WAV"
    wavfile.write(str(p), SR, (np.asarray(samples, dtype=np.float64) * 32768.0).astype(np.int16))
    return {"CY5025.WAV": hashlib.sha256(p.read_bytes()).hexdigest()}


def _frozen_for(tmp_path, ref_sha, *, hashes=None) -> pathlib.Path:
    f = dict(pt.FROZEN, source_commit=pt._git("rev-parse", "HEAD"), source_hashes=hashes or pt.source_hashes(),
             reference_sha256=ref_sha, corpus_git_commit=None, windows_s=[1.5])
    p = tmp_path / "frozen.json"
    p.write_text(json.dumps(f))
    return p


def _cli(tmp_path, frozen) -> list:
    return ["measure", "--refs", str(tmp_path), "--frozen", str(frozen), "--out", str(tmp_path / "result.json")]


def _require_clean_tree():
    """The CLI controls drive the committed tree end to end; from a dirty tree the guard
    refuses before anything else (by design), which would read as a confusing assertion."""
    dirty = pt._git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        pytest.fail("the CLI controls run against the committed tree; commit first:\n" + dirty)


def test_changed_closure_dependency_refuses_before_rendering(tmp_path, capsys, monkeypatch, ref_long):
    _require_clean_tree()
    sha = _fake_corpus(tmp_path, ref_long)
    hashes = pt.source_hashes()
    hashes["model/modal_fixed.py"] = "0" * 64          # the defeat: an omitted closure file edited post-freeze
    monkeypatch.setattr(pt.rc, "render_drum_solo", _no_render)
    code = pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha, hashes=hashes)))
    assert code == 2, "the changed closure file must refuse, not measure"
    out = capsys.readouterr().out
    assert "modal_fixed" in out and "re-freeze" in out, out


def test_dirty_tracked_tree_refuses_before_rendering(tmp_path, capsys, monkeypatch, ref_long):
    _require_clean_tree()
    sha = _fake_corpus(tmp_path, ref_long)
    real_git = pt._git

    def fake_git(*a):
        return " M model/modal_fixed.py" if a[:2] == ("status", "--porcelain") else real_git(*a)

    monkeypatch.setattr(pt, "_git", fake_git)
    monkeypatch.setattr(pt.rc, "render_drum_solo", _no_render)
    code = pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha)))
    assert code == 2, "an uncommitted tree is not attributable to the frozen commit"
    assert "uncommitted" in capsys.readouterr().out


def test_require_tree_identity_records_the_measured_commit(monkeypatch):
    monkeypatch.setattr(pt, "_git", lambda *a: "abc123" if a[:2] == ("rev-parse", "HEAD") else "")
    assert pt.require_tree_identity() == "abc123"


# ---------------------------------------------------------------- refusal boundary (CLI)
# run_case.prepare refuses non-finite / cut-into-strike records with run_case.Refused and cymbal_mid
# refuses a silent record with cymbal_bands.Refused -- classes distinct from this probe's Refused,
# which the CLI caught alone, so those preconditions escaped as a traceback (exit 1) instead of the
# advertised REFUSED / NO VERDICT (exit 2). Each control drives pt.main end to end; the production
# render is a planted record where the render itself is not what is under test (declared per test).

def test_measure_mid_silence_is_attributed_to_the_side_it_refused(ref_long):
    silent = np.zeros(2 * SR)
    with pytest.raises(pt.Refused, match="reference: silent"):
        pt.compare_records(ref_long, SR, silent, SR, prod_has_hh1=True)


def test_cli_silent_reference_refuses_with_the_side_not_a_traceback(tmp_path, capsys, monkeypatch, ref_long):
    _require_clean_tree()
    sha = _fake_corpus(tmp_path, np.zeros(2 * SR))
    monkeypatch.setattr(pt.lt, "check", lambda: (True, []))
    monkeypatch.setattr(pt.rc, "render_drum_solo", lambda *a, **k: (ref_long, SR))
    code = pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha)))
    assert code == 2, "a silent reference must refuse, not crash"
    assert "REFUSED / NO VERDICT" in capsys.readouterr().out


def test_cli_nonfinite_render_refuses_not_tracebacks(tmp_path, capsys, monkeypatch, ref_long):
    _require_clean_tree()
    sha = _fake_corpus(tmp_path, ref_long)
    monkeypatch.setattr(pt.lt, "check", lambda: (True, []))
    called = []

    def nan_render(*a, **k):
        called.append(True)
        return np.full(SR, np.nan), SR

    monkeypatch.setattr(pt.rc, "render_drum_solo", nan_render)
    code = pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha)))
    assert called, "the control must reach the render it is defeating"
    assert code == 2
    assert "production" in capsys.readouterr().out


def test_cli_reference_cut_into_its_strike_refuses_not_tracebacks(tmp_path, capsys, monkeypatch):
    _require_clean_tree()
    t = np.arange(2 * SR) / SR
    loud = 0.5 * np.sign(np.sin(2 * np.pi * 1000.0 * t))    # at 50 % of full scale from sample 0
    sha = _fake_corpus(tmp_path, loud)
    monkeypatch.setattr(pt.lt, "check", lambda: (True, []))
    monkeypatch.setattr(pt.rc, "render_drum_solo", _no_render)   # the reference is prepared first
    code = pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha)))
    assert code == 2
    out = capsys.readouterr().out
    assert "cut into the strike" in out and "CY5025" in out, out


def test_cli_implementation_error_is_not_swallowed_as_a_refusal(tmp_path, monkeypatch, ref_long):
    _require_clean_tree()
    sha = _fake_corpus(tmp_path, ref_long)
    monkeypatch.setattr(pt.lt, "check", lambda: (True, []))

    def boom(*a, **k):
        raise ValueError("implementation error, not a refusal")

    monkeypatch.setattr(pt.rc, "render_drum_solo", boom)
    with pytest.raises(ValueError, match="implementation error"):
        pt.main(_cli(tmp_path, _frozen_for(tmp_path, sha)))


# ---------------------------------------------------------------- frozen record
def test_frozen_record_declares_the_pre_measurement_commitments():
    f = pt.FROZEN
    for k in ("supported_settings", "windows_s", "tolerances", "candidate_budget",
              "preservation_set", "stage_isolation", "development_settings", "confirmation_settings",
              "source_tree"):
        assert k in f
    assert f["candidate_budget"] == 0
    assert set(f["preservation_set"]) >= {"D15A", "D16A", "OH00", "OH75"}
    assert f["supported_settings"] == ["CY5025"]
    assert not set(f["development_settings"]) & set(f["confirmation_settings"])
    assert all(p["status"] in ("measured", "inferred", "unverified") for p in f["stage_isolation"]["predictions"])
    json.dumps(f)
