"""Known answers and controls for tools/probes/tom_conga_gate.py (#558).

The model-independent tests (float resonators, the judge's frozen rules) run
anywhere. The two that need the Fischer corpus read GF180_TR808_FISCHER (the
checkout of tidalcycles/sounds-tr808-fischer at 85fbecf) and are SKIPPED with
that reason when it is absent -- a skip, never a pass.
"""
from __future__ import annotations

import copy
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


# ---- rule 5: every injection turns its known answer red ----
INJECTED = {"no-preserve": test_judge_rejects_a_win_bought_with_decay,
            "no-peak": test_judge_rejects_a_win_bought_with_silence,
            "confirm-reads-all": test_judge_confirmation_reads_untouched_only,
            "allpole-stub": lambda: test_dc_zero_onset_reads_a_pitch_excess("MC")}


@pytest.mark.parametrize("name", tc.INJECTIONS)
def test_injection_turns_its_known_answer_red(name):
    assert name in INJECTED, f"injection {name} has no known answer to redden"
    tc.INJECT.add(name)
    try:
        with pytest.raises(AssertionError):
            INJECTED[name]()
    finally:
        tc.INJECT.discard(name)
    INJECTED[name]()               # and green again once removed
