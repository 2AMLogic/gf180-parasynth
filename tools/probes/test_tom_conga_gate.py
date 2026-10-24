"""Known answers and controls for tools/probes/tom_conga_gate.py (#558).

The model-independent tests (float resonators, the judge's frozen rules) run
anywhere. The tests that need the Fischer corpus read GF180_TR808_FISCHER (the
checkout of tidalcycles/sounds-tr808-fischer at 85fbecf) and are SKIPPED with
that reason when it is absent -- a skip, never a pass.
"""
from __future__ import annotations

import copy
import json
import os
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import tom_conga_gate as tc  # noqa: E402

pg = tc.pg
REFS = os.environ.get("GF180_TR808_FISCHER")
needs_refs = pytest.mark.skipif(not REFS, reason="GF180_TR808_FISCHER (the Fischer corpus) not set")


# ---- H1: the onset phase of the transfer function moves the gate's pitch track ----
@pytest.mark.parametrize("s", list(tc.KA_VOICES))
def test_all_pole_onset_reads_no_pitch_excess(s):
    f0, tau = tc.KA_VOICES[s]
    assert abs(tc.ka_pitch_onset(f0, tau, (1.0,), s)["cents_first_15ms"][0]) < 8.0


@pytest.mark.parametrize("s", list(tc.KA_VOICES))
def test_dc_zero_onset_reads_a_pitch_excess(s):
    """A pure LTI resonator with NO pitch change at all reads > 30 cents at the
    gate's first frame once its transfer has a DC zero. So an onset excess in
    the gate's track is not, by itself, evidence of a pitch drop."""
    f0, tau = tc.KA_VOICES[s]
    bp = tc.ka_pitch_onset(f0, tau, (1.0, 0.0, -1.0), s)["cents_first_15ms"]
    assert bp[0] > 30.0
    assert bp[0] > bp[2]           # and it relaxes, like a drop would


# ---- H3: the body half of `impulse` is a floor meter ----
def test_body_impulse_rises_with_a_stationary_floor():
    f0, tau = tc.KA_VOICES["LC"]
    levels = [tc.ka_floor(f0, tau, lv)[1] for lv in (-100.0, -85.0, -70.0)]
    assert levels[0] < levels[1] < levels[2]
    assert levels[2] - levels[0] > 20.0        # ~1 dB per dB of floor
    none = tc.ka_floor(f0, tau, None)[1]
    assert none < levels[0]


def test_strike_impulse_is_blind_to_the_floor():
    f0, tau = tc.KA_VOICES["MC"]
    a, b = tc.ka_floor(f0, tau, None)[0], tc.ka_floor(f0, tau, -70.0)[0]
    assert abs(a - b) < 1.0


# ---- the frozen judge ----
def _row(split, ps, decay=1.0, peak=0.5, cand_ps=None, cand_decay=None, cand_peak=None):
    def v(p, d):
        r = {f: 1.0 for f in pg.FEATURES}
        r["pitch_shape"], r["decay"] = p, d
        return {"ratio": r, "worst": max(r.values()), "worst_feature": "x", "verdict": "FAIL", "d": {}}
    variants = {"shipped": v(ps, decay)}
    pf = {"shipped": peak}
    for n in ("BP", "BP+X4"):
        variants[n] = v(cand_ps if cand_ps is not None else ps,
                        cand_decay if cand_decay is not None else decay)
        pf[n] = cand_peak if cand_peak is not None else peak
    for g in (0, -20):
        variants[f"shipped+take_floor{g:+.0f}dB"] = v(ps, decay)
    return {"split": split, "variants": variants, "peak_fs": pf}


PRE = {"candidates": {"BP": "BP", "BP+X4": "BP+X4"},
       "rules": {"targets": ["pitch_shape"], "preserve_rel": 1.10, "preserve_abs": 0.10,
                 "peak_db": 1.0, "confirm_improved_frac": 0.75, "confirm_median_reduction": 0.25},
       "attribution": {"floor_gain_db": [0.0, -20.0]}}


def _rec(**kw):
    return {"rows": {f"D{i}": _row("development", 10.0, **kw) for i in range(3)}
            | {f"U{i}": _row("untouched", 10.0, **kw) for i in range(4)}}


def test_judge_confirms_a_clean_improvement():
    r = tc.judge(_rec(cand_ps=5.0), PRE)
    assert r["chosen"] in PRE["candidates"]
    assert r["confirmation"][r["chosen"]]["confirmed"]


def test_judge_rejects_a_win_bought_with_decay():
    """#351's own outcome: pitch better, decay worse -> inadmissible."""
    r = tc.judge(_rec(cand_ps=5.0, cand_decay=3.0), PRE)
    assert r["chosen"] is None
    assert not any(c["confirmed"] for c in r["confirmation"].values())


def test_judge_rejects_a_win_bought_with_silence():
    r = tc.judge(_rec(cand_ps=5.0, cand_peak=0.05), PRE)
    assert r["chosen"] is None


def test_judge_does_not_confirm_no_change():
    r = tc.judge(_rec(), PRE)
    assert not any(c["confirmed"] for c in r["confirmation"].values())


def test_judge_confirmation_reads_untouched_only():
    # six development rows that improve, two untouched that do not: a judge
    # that pooled the splits would see 6/8 improved and confirm. (With 3 + 4
    # rows it pooled to 3/7 and this test was BLIND to that defect --
    # wrong-then-right, caught by the injection below.)
    rec = {"rows": {f"D{i}": _row("development", 10.0, cand_ps=5.0) for i in range(6)}
           | {f"U{i}": _row("untouched", 10.0, cand_ps=5.0) for i in range(2)}}
    for k, row in rec["rows"].items():
        if row["split"] == "untouched":
            for n in PRE["candidates"]:
                row["variants"][n]["ratio"]["pitch_shape"] = 10.0
    r = tc.judge(rec, PRE)
    assert r["chosen"] is not None
    assert not r["confirmation"][r["chosen"]]["confirmed"]


# ---- the engine and the corpus ----
def test_retune_unity_is_shipped():
    import run_case as rc
    for s in ("MT", "LC"):
        assert np.array_equal(tc.render(s)[0], rc.render_drum_solo(s)[0])


def test_retune_moves_pitch():
    a, sr = tc.render("HC", 1.0)
    b, _ = tc.render("HC", 1.05)
    fa = pg._strongest_line(pg.condition(a, sr), 200, 800)
    fb = pg._strongest_line(pg.condition(b, sr), 200, 800)
    assert 1.03 < fb / fa < 1.07


def test_prereg_is_frozen_and_complete():
    p = tc.load_prereg()
    assert len(p["corpus"]["sha256_16"]) == 30
    splits = list(p["split_of"].values())
    assert splits.count("development") == 6 and splits.count("untouched") == 18
    for s in ("MT", "LC", "HC"):
        assert all(p["split_of"][f"{s}{c}"] != "development" for c in pg.CODES)


@needs_refs
def test_bar_for_take_is_the_gates_bar():
    refs = pathlib.Path(REFS)
    for s in ("LT", "MC"):
        rel = pg.target_rel(s)
        T = pg.Target(*pg.load_wav(refs / rel), s, rel)
        ours, theirs = tc.bar_for_take(s, rel, refs, T), pg.bar_for(s, refs, T)
        assert ours["from"] == theirs["from"]
        for f in pg.FEATURES:
            assert ours["bar"][f] == theirs["bar"][f]


@needs_refs
def test_refuses_a_different_corpus(tmp_path):
    refs = pathlib.Path(REFS)
    hashes = copy.deepcopy(tc.load_prereg()["corpus"]["sha256_16"])
    hashes["lt8/LT50.WAV"] = "0" * 16
    with pytest.raises(tc.Refused):
        tc.load_checked(refs, "lt8/LT50.WAV", hashes)
    (tmp_path / "lt8").mkdir()
    (tmp_path / "lt8" / "LT50.WAV").write_bytes(b"")
    with pytest.raises(Exception):
        tc.load_checked(tmp_path, "lt8/LT50.WAV", None)


#: The float twin's RAW form must give the gate's pitch_shape the shipped engine
#: gives, or the twin-BP vs twin-RAW attribution says nothing about the engine.
#: twin.json's worst case over all 30 conditions is 0.229 (HC25). LT00 carries
#: the diode-drop staircase (the conga modes have no per-frame host writes), so
#: it is the row the `twin-no-host-writes` injection turns red (3.34 -> 6.10).
TWIN_TOL = 0.25
TWIN_ROWS = (("LT", "00"), ("HC", "25"), ("MC", "00"))


@needs_refs
def test_twin_raw_tracks_the_engine():
    refs = pathlib.Path(REFS)
    hashes = tc.load_prereg()["corpus"]["sha256_16"]
    for s, c in TWIN_ROWS:
        rel = tc.take_rel(s, c)
        x50, sr50 = tc.load_checked(refs, tc.take_rel(s, "50"), hashes)
        f50 = tc.take_f0(pg.Target(x50, sr50, s, tc.take_rel(s, "50")))
        x, sr = tc.load_checked(refs, rel, hashes)
        T = pg.Target(x, sr, s, rel)
        bar = tc.bar_for_take(s, rel, refs, T, hashes)["bar"]
        ratio = tc.take_f0(T) / f50
        ship = tc.score(T, bar, *tc.render(s, ratio), "shipped")["ratio"]["pitch_shape"]
        twin = tc.score(T, bar, *tc.render_twin(s, ratio), "twin-RAW")["ratio"]["pitch_shape"]
        assert abs(twin - ship) <= TWIN_TOL, f"{s}{c}: twin-RAW {twin:.3f} vs shipped {ship:.3f}"


# ---- provenance is read at the start, and a dirty tree is REFUSED ----
def _fake_git(state):
    def g(*a):
        if a[:2] == ("rev-parse", "HEAD"):
            return state["head"]
        if a[:1] == ("rev-parse",):
            return "origin-main-sha"
        if a[:1] == ("status",):
            state["status_scope"] = a[3:]
            return state["dirty"]
        raise AssertionError(a)
    return g


def test_dirty_tree_is_refused_before_any_measurement(monkeypatch, tmp_path):
    """The defeating input for a provenance read: a record that ran from
    uncommitted code. It must REFUSE (exit 2), write nothing, and never start
    the measurement."""
    state = {"head": "A", "dirty": " M tools/probes/tom_conga_gate.py"}
    monkeypatch.setattr(tc, "git", _fake_git(state))
    ran = []
    monkeypatch.setattr(tc, "knownanswer", lambda: ran.append(1) or {})
    out = tmp_path / "ka.json"
    assert tc.main(["knownanswer", "--out", str(out)]) == 2
    assert not ran and not out.exists()
    # the scope includes the pre-registration the probe reads, not only code
    assert "model" in state["status_scope"] and "tools" in state["status_scope"]
    assert any(p.endswith("prereg.json") for p in state["status_scope"])


def test_provenance_names_the_commit_the_run_started_from(monkeypatch, tmp_path):
    """The defeating input for an end-of-run read (what run.json and twin.json
    have): HEAD moves while the run is in progress."""
    state = {"head": "A", "dirty": ""}
    monkeypatch.setattr(tc, "git", _fake_git(state))

    def work():
        state["head"] = "B"          # a commit lands mid-run
        state["dirty"] = " M model/drums_fx.py"
        return {}
    monkeypatch.setattr(tc, "knownanswer", work)
    out = tmp_path / "ka.json"
    assert tc.main(["knownanswer", "--out", str(out)]) == 0
    p = json.loads(out.read_text())["provenance"]
    assert p["commit"] == "A" and p["read_at"] == "start" and p["model_tools_dirty"] is False
    assert p["commit_at_end"] == "B" and p["head_moved_during_run"] and p["dirty_at_end"]


# ---- rule 5: every injection turns its known answer red ----
INJECTED = {"no-preserve": test_judge_rejects_a_win_bought_with_decay,
            "no-peak": test_judge_rejects_a_win_bought_with_silence,
            "confirm-reads-all": test_judge_confirmation_reads_untouched_only,
            "allpole-stub": lambda: test_dc_zero_onset_reads_a_pitch_excess("MC"),
            "twin-no-host-writes": test_twin_raw_tracks_the_engine}


@pytest.mark.parametrize("name", tc.INJECTIONS)
def test_injection_turns_its_known_answer_red(name):
    assert name in INJECTED, f"injection {name} has no known answer to redden"
    if name == "twin-no-host-writes" and not REFS:
        pytest.skip("GF180_TR808_FISCHER (the Fischer corpus) not set")
    tc.INJECT.add(name)
    try:
        with pytest.raises(AssertionError):
            INJECTED[name]()
    finally:
        tc.INJECT.discard(name)
    INJECTED[name]()               # and green again once removed
