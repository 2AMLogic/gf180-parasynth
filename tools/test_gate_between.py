"""Known answers for tools/gate_between.py (#379, rule v2). Fast: no audio is read."""
import copy
import json
import pathlib
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract  # noqa: E402
import gate_between as gb  # noqa: E402
import gate_calibrate as gc  # noqa: E402
import perceptual_gate as pg  # noqa: E402

SCORECARD = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/gate-379"
TABLES = SCORECARD / "mars-calibration/tables.json"
BETWEEN_P075 = SCORECARD / "mars-calibration-v2/between-p0.75.json"
VAL9 = ("BD", "CY", "HC", "HT", "LC", "LT", "MC", "MT", "SD")


def _d(spec, other=1.0):
    d = {f: other for f in pg.FEATURES}
    d.update(pitch=None, pitch_shape=None, modulation=None, spec=spec)
    return d


def _rel(g, i, sound="BD"):
    """A synthetic take path whose real `group_of(key_of(rel))` is `g`, while
    preserving `(g, i)` lexicographic key order so `regroup` needs no patch."""
    n = 0
    while True:
        rel = f"{gc.FOLDER[sound]}/Clean/Digital/k{g}_{i:02d}_{n}.wav"
        if gc.group_of(gc.key_of(rel)) == g:
            return rel
        n += 1


def _tab(specs_by_group, other=1.0, sound="BD"):
    rows = []
    for g, ds in specs_by_group.items():
        for i, sp in enumerate(ds):
            rel = _rel(g, i, sound)
            key = gc.key_of(rel)
            rows.append({"rel": rel, "chain": "Digital", "key": key, "group": g,
                         "d": _d(sp, other)})
    return {"sound": sound, "target": "t", "floor": {f: 0.0 for f in pg.FEATURES}, "rows": rows,
            "missing": [], "keys": sorted({r["key"] for r in rows})}


def _idx(tabs):
    """Independent take inventory matching a synthetic fixture's declared takes."""
    return {s: {r["rel"] for r in t["rows"]} | {m["rel"] for m in t["missing"]} for s, t in tabs.items()}


def test_matched_count_is_a_fraction_of_the_keys_not_an_absolute_k():
    assert gb.n_matched(72, 0.10) == 8 and gb.n_matched(22, 0.10) == 3
    assert gb.n_matched(2, 0.05) == 1                      # never zero


def test_representative_is_the_nearest_take_of_a_key():
    rows = [{"key": "a", "d": _d(0.5), "rel": "a1"}, {"key": "a", "d": _d(0.2), "rel": "a2"},
            {"key": "b", "d": _d(0.3), "rel": "b1"}]
    assert [r["rel"] for r in gb.reps(rows)] == ["a2", "b1"]


def test_split_coverage_is_seeded_and_sees_a_homogeneous_pool():
    t = _tab({0: [0.30 + 0.001 * i for i in range(10)], 1: [0.30 + 0.001 * i for i in range(10)]})
    a = gb.split_coverage(gb.pool_rows(t), t["floor"], 0.5, splits=20)
    b = gb.split_coverage(gb.pool_rows(t), t["floor"], 0.5, splits=20)
    assert a == b and a["rate"] == pytest.approx(1.0)


def _refusing_pool(n_near=14, n_far=6):
    """A pool whose split coverage is below COVERAGE at every p in P_GRID but
    above it at p = 0.75 and 1.0, so WIDENING past the grid would pass.

    Keys are ranked by `spec`. Each of the n_near nearest keys carries a
    private excess on one non-spec feature (value 5 + rank, cycling through the
    six), which no nearer key covers; the n_far farthest keys are large on every
    non-spec feature. So the best take of one half passes the other half's bar
    only when that bar's matched set reaches a far key, and that needs a large
    fraction of the half. Measured (SPLITS=200): mean rate 0.005, 0.005, 0.045,
    0.045, 0.155, 0.458 over P_GRID; 0.917 at 0.75; 1.0 at 1.0."""
    free = [f for f in pg.FEATURES if f not in ("spec", "pitch", "pitch_shape", "modulation")]
    specs = [0.1 + 0.01 * r for r in range(n_near + n_far)]
    tab = _tab({0: specs[0::2], 1: specs[1::2]})
    for r in tab["rows"]:
        rank = round((r["d"]["spec"] - 0.1) / 0.01)
        if rank < n_near:
            r["d"][free[rank % len(free)]] = 5.0 + rank
        else:
            for f in free:
                r["d"][f] = 1000.0
    return tab


def test_selection_refuses_rather_than_widen_when_halves_disagree():
    """The answer is REFUSED with no bars, not a looser p.

    Defeating input (verification rule 8): `_refusing_pool`, where every p in
    P_GRID misses COVERAGE but p = 0.75 and 1.0 reach it. Mutation check, done
    when this test was written: selecting from P_SWEEP instead of P_GRID in
    `choose_p` turns this test red (it calibrates at 0.75); the previous version
    of this test passed under that mutation."""
    tabs = {"BD": _refusing_pool()}
    cal = gb.calibrate(tabs, expected=_idx(tabs))
    by = cal["selection"]["by_p"]
    assert "BD" in cal["selection"]["eligible"]
    # the input really is one that widening would rescue: otherwise this proves nothing
    assert all(by[q]["mean_rate"] < gb.COVERAGE for q in gb.P_GRID)
    assert any(by[q]["mean_rate"] >= gb.COVERAGE for q in gb.P_SWEEP if q not in gb.P_GRID)
    assert cal["status"].startswith("REFUSED")
    assert cal["selection"]["chosen"] is None and cal["p"] is None
    assert cal["sounds"] == {}                                  # no bars emitted


def test_calibrates_homogeneous_pool_validates_and_labels_weak_sounds():
    pool = {0: [0.30 + 0.002 * i for i in range(8)], 1: [0.30 + 0.002 * i for i in range(8)], 2: [0.301]}
    tabs = {"BD": _tab(pool), "RS": _tab({0: [0.5], 1: [0.5]}, sound="RS")}
    cal = gb.calibrate(tabs, expected=_idx(tabs))
    by = cal["selection"]["by_p"]
    assert cal["status"] == "CALIBRATED"
    # the chosen p is the SMALLEST selectable one that reaches coverage (a p that is
    # too small fails half the time by symmetry: the half holding the best take)
    assert by[cal["p"]]["mean_rate"] >= gb.COVERAGE
    assert all(by[q]["mean_rate"] < gb.COVERAGE for q in gb.P_GRID if q < cal["p"])
    assert cal["sounds"]["BD"]["status"] == "VALIDATED"
    assert cal["sounds"]["RS"]["status"].startswith("WEAK-UNVALIDATED")
    assert cal["sounds"]["RS"]["kind"].startswith("WEAK")     # said wherever it is used


def test_selection_never_reads_the_untouched_group():
    """Leak control: rewriting every VAL (group 2) row to nonsense must not move
    the selection or the bar. If it does, VAL is not untouched."""
    pool = {0: [0.30 + 0.002 * i for i in range(8)], 1: [0.30 + 0.002 * i for i in range(8)], 2: [0.305, 0.31]}
    t = _tab(pool)
    tabs = {"BD": t}
    base = gb.calibrate(tabs, expected=_idx(tabs))
    t2 = copy.deepcopy(t)
    for r in t2["rows"]:
        if r["group"] == 2:
            r["d"] = _d(7777.0, other=7777.0)
    tabs2 = {"BD": t2}
    mod = gb.calibrate(tabs2, expected=_idx(tabs2))
    assert mod["selection"]["by_p"] == base["selection"]["by_p"] and mod["p"] == base["p"]
    assert mod["sounds"]["BD"]["bar"] == base["sounds"]["BD"]["bar"]
    assert mod["sounds"]["BD"]["status"].startswith("VALIDATION-FAILED")   # but VAL is still READ for the verdict


def test_bar_has_the_perceptual_floors_and_is_never_a_fit():
    t = _tab({0: [0.3] * 4, 1: [0.3] * 4})
    bar = gb.bar_p(t["rows"], t["floor"], 0.5)
    assert bar["attack"] >= pg.ATTACK_JND_MS and bar["impulse"] >= pg.IMPULSE_FLOOR_DB


def test_rank_orders_by_distance_to_the_real_recordings_and_marks_missing_renders():
    ctx = {"A": {"old_bar": "WEAK", "n_takes": 4, "mars_takes_passing_old_bar": 0,
                 "mars_best": {"worst_ratio": 2.0, "worst_feature": "spec"}, "ours": {"worst_ratio": 4.0, "worst_feature": "decay"}},
           "B": {"old_bar": "x", "n_takes": 4, "mars_takes_passing_old_bar": 0,
                 "mars_best": {"worst_ratio": 1.0, "worst_feature": "spec"}, "ours": {"worst_ratio": 9.0, "worst_feature": "pitch"}},
           "C": {"old_bar": "x", "n_takes": 4, "mars_takes_passing_old_bar": 0,
                 "mars_best": {"worst_ratio": 1.0, "worst_feature": "spec"}}}
    rows = gb.rank_rows(ctx)
    assert [r["sound"] for r in rows] == ["B", "A", "C"]          # 9x, 2x, then the one with no render
    assert "no render" in gb.render_table(rows)


def test_a_perfect_render_with_worst_ratio_zero_is_not_labelled_no_render():
    # the input that defeated the old `if not r["ours"]` / `or -1` guards: 0.0 is falsy.
    # The missing row is listed BEFORE the zero row so a stable sort on a tied key keeps it ahead.
    mars = {"old_bar": "x", "n_takes": 1, "mars_takes_passing_old_bar": 0,
            "mars_best": {"worst_ratio": 2.0, "worst_feature": "spec"}}
    ctx = {"SD": {**mars, "ours": {"worst_ratio": 4.0, "worst_feature": "decay"}},
           "RS": dict(mars),
           "BD": {**mars, "ours": {"worst_ratio": 0.0, "worst_feature": None}}}
    rows = gb.rank_rows(ctx)
    assert [r["sound"] for r in rows] == ["SD", "BD", "RS"]       # 2.0x, 0.0x, then no render
    lines = {l.split("|")[2].strip(): l for l in gb.render_table(rows).splitlines()[2:]}
    assert "no render" not in lines["BD"] and "0.0x" in lines["BD"]
    assert "None" not in lines["BD"]
    assert "no render" in lines["RS"]


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
@pytest.mark.parametrize("side", ["ours", "mars_best"])
def test_rank_refuses_a_non_finite_ratio_rather_than_sort_it(bad, side):
    """A NaN ratio makes the sort order undefined (#134's class); refuse it."""
    ctx = {"A": {"old_bar": "x", "n_takes": 4, "mars_takes_passing_old_bar": 0,
                 "mars_best": {"worst_ratio": 2.0, "worst_feature": "spec"},
                 "ours": {"worst_ratio": 4.0, "worst_feature": "decay"}}}
    ctx["A"][side]["worst_ratio"] = bad
    with pytest.raises(ValueError, match="non-finite"):
        gb.rank_rows(ctx)


def _green_q():
    return {"sounds_with_bar": ["BD"], "start_red": {"BD": "FAIL", "BD-silence": "REFUSED"},
            "seeded_summary": {"n": 10, "caught": 10, "missed": [], "rate": 1.0},
            "known_bad": {"shipped-cymbal": {"verdict": "FAIL"}}, "clean": {"BD": "PASS"},
            "validation": {"rule": "v1", "by_sound": {s: True for s in VAL9}, "sounds": 9, "best_passes": 9}}


@pytest.mark.parametrize("verdict,status", [("FAIL", "QUALIFIED"), ("PASS", "NOT QUALIFIED"),
                                            ("REFUSED", "REFUSED"), (None, "REFUSED")])
def test_the_rejected_374_candidate_gates_qualification(verdict, status):
    """Every other gate green; only the #374 candidate varies (absent == None)."""
    q = _green_q()
    assert gc.decide(q, False)["status"] == "QUALIFIED"           # v1 never looked at the candidate
    q["validation"]["rule"] = "v2"
    if verdict:
        q["known_bad"][gb.CANDIDATE] = {"verdict": verdict}
    assert gb.decide(q, False)["status"] == status


def test_a_refused_injected_run_is_not_a_caught_control():
    """Clean qualification passes but the injected control cannot complete (its
    shipped-cymbal control is unavailable): Q5 must stay unrun, not become a catch."""
    qi = _green_q()
    qi["known_bad"]["shipped-cymbal"] = {"verdict": "REFUSED"}
    inj, inj_dec = gc.injected_outcome(qi)
    assert inj is None and inj_dec["status"] == "REFUSED" and inj_dec["unrun"]
    q = _green_q()
    q["validation"]["rule"] = "v2"
    q["known_bad"][gb.CANDIDATE] = {"verdict": "FAIL"}
    dec = gb.decide(q, inj)
    assert dec["status"] == "REFUSED" and dec["unrun"] == ["Q5 corrupted corpus"]
    assert gc.injected_outcome(_green_q())[0] is True             # a corrupted corpus that qualifies
    assert gc.injected_outcome(None) == (None, None)
    bad = _green_q()
    bad["seeded_summary"].update(caught=5, rate=0.5)
    assert gc.injected_outcome(bad)[0] is False                   # completed NOT QUALIFIED = caught


# ---------------------------------------------------------------------------
# #1304: index-backed pool eligibility and Q4 validation sound-identity checks
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def real():
    return json.loads(TABLES.read_text())["tables"]


def _zero(tabs):
    z = copy.deepcopy(tabs)
    for t in z.values():
        for r in t["rows"]:
            r["d"] = {f: (None if v is None else 0.0) for f, v in r["d"].items()}
    return z


def _damage(tabs, how, keep="BD"):
    t = copy.deepcopy(tabs)
    for s in t:
        if s == keep:
            continue
        if how == "empty":
            t[s]["rows"], t[s]["keys"] = [], []
        elif how == "one":
            t[s]["rows"] = t[s]["rows"][:1]
        elif how == "missing":
            t[s]["missing"] = t[s]["missing"] + [{"rel": r["rel"], "why": "x"} for r in t[s]["rows"]]
            t[s]["rows"] = []
    return t


def _hc_lost_pool(tabs):
    """HC Case (a): retain 1 key in group 0 and 1 key in group 1 plus all VAL
    takes; move removed pool takes into `missing`. Leaves 2 pool keys (< 8)."""
    t = copy.deepcopy(tabs)
    reg = gc.regroup({"HC": t["HC"]})["HC"]["rows"]
    k0 = min(r["key"] for r in reg if r["group"] == 0)
    k1 = min(r["key"] for r in reg if r["group"] == 1)
    keep = {r["rel"] for r in reg if r["key"] in (k0, k1) or r["group"] == 2}
    t["HC"]["missing"] = t["HC"]["missing"] + [{"rel": r["rel"], "why": "x"} for r in t["HC"]["rows"] if r["rel"] not in keep]
    t["HC"]["rows"] = [r for r in t["HC"]["rows"] if r["rel"] in keep]
    return t


def _hc_lost_val(tabs):
    """HC Case (b): retain all 18 pool keys and move every VAL take into `missing`."""
    t = copy.deepcopy(tabs)
    reg = gc.regroup({"HC": t["HC"]})["HC"]["rows"]
    keep = {r["rel"] for r in reg if r["group"] in (0, 1)}
    t["HC"]["missing"] = t["HC"]["missing"] + [{"rel": r["rel"], "why": "x"} for r in t["HC"]["rows"] if r["rel"] not in keep]
    t["HC"]["rows"] = [r for r in t["HC"]["rows"] if r["rel"] in keep]
    return t


def test_full_record_retains_v2_split_coverage_refusal(real):
    """Regression: real regrouping + real 16-sound record still refuses on split
    coverage, with the nine index-eligible voices in `eligible`."""
    cal = gb.calibrate(real)
    assert cal["p"] is None and cal["selection"]["chosen"] is None
    assert cal["status"] == "REFUSED: no p in P_GRID reaches the split-coverage requirement"
    assert cal["selection"]["eligible"] == list(VAL9)
    assert cal["sounds"] == {}


def test_positive_control_zero_distance_real_layout_calibrates_and_validates(real):
    cal = gb.calibrate(_zero(real))
    assert cal["status"] == "CALIBRATED" and cal["p"] is not None
    assert cal["selection"]["eligible"] == list(VAL9)
    validated = {s: r["validation"]["best_passes"] for s, r in cal["sounds"].items() if "validation" in r}
    assert validated == {s: True for s in VAL9}
    for s in ("RS", "CL", "CP", "MA", "CB", "OH", "CH"):
        assert cal["sounds"][s]["status"].startswith("WEAK-UNVALIDATED")


@pytest.mark.parametrize("how", ["empty", "one", "missing"])
@pytest.mark.parametrize("force_p", [None, 0.5, 0.75])
def test_damaged_non_bd_tables_refuse_in_v2_including_forced_p(real, how, force_p):
    """Start-red: unfixed `gb.calibrate` returned p=0.5 CALIBRATED on BD alone
    (or DIAGNOSTIC-CALIBRATION with force_p) and emitted 16 sound entries."""
    cal = gb.calibrate(_damage(real, how), force_p=force_p)
    assert cal["status"].startswith("REFUSED: incomplete evidence")
    assert cal["p"] is None and cal["selection"] is None and cal["diagnostic"] is None
    assert cal["sounds"] == {} and gc.load_bars(cal) == {}


@pytest.mark.parametrize("how", ["empty", "missing"])
@pytest.mark.parametrize("extra", [[], ["--force-p", "0.75"]])
def test_cli_refusal_on_empty_rows_is_not_masked_by_old_bar_context(real, tmp_path, monkeypatch, capsys, how, extra):
    """Unfixed CLI crashed with `ValueError: min() iterable argument is empty`
    inside `old_bar_context` before writing the refusal record."""
    sig = np.concatenate([np.zeros(2400), np.sin(2 * np.pi * 200 * np.arange(9600) / 48000) * np.exp(-np.arange(9600) / 1500)])
    monkeypatch.setattr(pg, "load_wav", lambda p: (sig.copy(), 48000))
    monkeypatch.setattr(pg, "bar_for", lambda s, refs, T=None: {"kind": "WEAK", "bar": {f: 1.0 for f in pg.FEATURES}})
    monkeypatch.setattr(pg, "provenance", lambda a: {})
    tpath = tmp_path / "tables.json"
    tpath.write_text(json.dumps({"tables": _damage(real, how)}))
    out = tmp_path / "between.json"
    rc = gb.main(["--refs", str(tmp_path), "--tables", str(tpath), "--out", str(out), *extra])
    assert rc == exit_contract.REFUSED and out.exists()
    res = json.loads(out.read_text())
    printed = json.loads(capsys.readouterr().out)
    assert res["decision"]["status"] == "REFUSED" and printed["status"] == "REFUSED"
    assert res["decision"]["why"].startswith("REFUSED: incomplete evidence")
    assert res["calibration"]["p"] is None and res["calibration"]["sounds"] == {}
    assert res["qualification"] is None
    assert res["calibration"]["old_bar_context"]["SD"]["n_takes"] == 0
    assert res["calibration"]["old_bar_context"]["SD"]["mars_best"] is None
    assert gb.rank_rows(res["calibration"]["old_bar_context"])   # descriptive table without `ours` does not crash


@pytest.mark.parametrize(("fault", "reason"), [
    (None, None),
    ("missing", "no readable MARS takes"),
    ("nonfinite", "non-finite worst ratio"),
    ("serialized_nonfinite", "non-finite worst ratio"),
])
def test_table_command_reports_rank_refusal(tmp_path, fault, reason):
    rec = json.loads(BETWEEN_P075.read_text())
    hc = rec["calibration"]["old_bar_context"]["HC"]
    if fault == "missing":
        hc.update(mars_best=None, n_takes=0, mars_takes_passing_old_bar=0)
    elif fault == "nonfinite":
        hc["ours"]["worst_ratio"] = float("nan")
    elif fault == "serialized_nonfinite":
        hc["mars_best"]["worst_ratio"] = pg._r(float("inf"))
    path = tmp_path / "between.json"
    path.write_text(json.dumps(rec))
    cmd = subprocess.run([sys.executable, gb.__file__, "table", str(path)], capture_output=True, text=True)
    if reason:
        assert cmd.returncode == exit_contract.REFUSED, cmd.stderr
        assert cmd.stderr.startswith(f"REFUSED: HC: {reason}") and "Traceback" not in cmd.stderr
        assert not cmd.stdout
    else:
        assert cmd.returncode == exit_contract.PASS and not cmd.stderr
        assert len(cmd.stdout.splitlines()) == 18 and "| HC |" in cmd.stdout


def test_rank_refuses_when_ours_present_but_no_mars_takes():
    ctx = {"SD": {"old_bar": "WEAK", "n_takes": 0, "mars_takes_passing_old_bar": 0,
                  "mars_best": None, "ours": {"worst_ratio": 4.0, "worst_feature": "decay"}}}
    with pytest.raises(ValueError, match="no readable MARS takes"):
        gb.rank_rows(ctx)


@pytest.mark.parametrize("force_p", [None, 0.75])
def test_hc_lost_pool_eligibility_is_refused_and_names_hc(real, force_p):
    """HC Case (a): 2 pool keys readable (< 8) while all VAL takes remain."""
    cal = gb.calibrate(_hc_lost_pool(real), force_p=force_p)
    assert cal["status"].startswith("REFUSED: incomplete evidence")
    assert "HC" in cal["status"] and "pool" in cal["status"]
    assert "BD" not in cal["status"] and "SD" not in cal["status"]
    assert cal["p"] is None and cal["sounds"] == {}


@pytest.mark.parametrize("force_p", [None, 0.75])
def test_hc_lost_validation_is_refused_and_names_hc(real, force_p):
    """HC Case (b): all 18 pool keys remain while every VAL take is in `missing`."""
    cal = gb.calibrate(_hc_lost_val(real), force_p=force_p)
    assert cal["status"].startswith("REFUSED: incomplete evidence")
    assert "HC" in cal["status"] and "validation" in cal["status"]
    assert "BD" not in cal["status"] and "SD" not in cal["status"]
    assert cal["p"] is None and cal["sounds"] == {}


@pytest.mark.parametrize("drop_group", [(0, 1), (2,)])
def test_single_unreadable_take_preserving_participation_is_accepted(real, drop_group):
    """One unreadable pool take (17/18 pool keys >= 8) or one unreadable VAL take
    (still >= 1 VAL key) in HC is accepted and calibrates on zeroed distances."""
    t = _zero(real)
    reg = gc.regroup({"HC": t["HC"]})["HC"]["rows"]
    victim = next(r["rel"] for r in reg if r["group"] in drop_group)
    t["HC"]["rows"] = [r for r in t["HC"]["rows"] if r["rel"] != victim]
    t["HC"]["missing"].append({"rel": victim, "why": "no-verdict"})
    cal = gb.calibrate(t)
    assert cal["status"] == "CALIBRATED" and cal["p"] is not None
    assert {s for s, r in cal["sounds"].items() if "validation" in r} == set(VAL9)


def test_q4_count_only_7_of_8_after_omitting_hc_cannot_qualify():
    """Start-red on `decide` itself: on unfixed code, omitting HC's failed
    validation entry turned Q4 from 7/9 (NOT QUALIFIED) into 7/8 (QUALIFIED)."""
    rec = json.loads(BETWEEN_P075.read_text())
    q = copy.deepcopy(rec["qualification"])
    q["known_bad"][gb.CANDIDATE] = {"verdict": "FAIL"}
    per = {s: r["validation"]["best_passes"] for s, r in rec["calibration"]["sounds"].items()
           if "validation" in r and s != "HC"}
    q["validation"] = {"sounds": len(per), "best_passes": sum(per.values())}
    assert gc.decide(q, False)["status"] == "REFUSED"
    assert gb.decide(q, False)["status"] == "REFUSED"


@pytest.mark.parametrize("gate", [gc, gb], ids=["v1", "v2"])
@pytest.mark.parametrize(("modulation", "verdict", "passes"), [
    (0.0, "PASS", True),
    (1.0, "FAIL", False),
    (None, "REFUSED", None),
])
def test_q4_preserves_held_out_verdict(real, gate, modulation, verdict, passes):
    tabs = _zero(real)
    held = {r["rel"]: r["d"] for r in tabs["BD"]["rows"] if gc.group_of(gc.key_of(r["rel"])) == 2}
    assert len(held) == 44
    for d in held.values():
        d["modulation"] = modulation

    cal = gate.calibrate(tabs)
    assert cal["status"] == "CALIBRATED"
    bd = cal["sounds"]["BD"]
    validation = bd["validation"]
    matched = validation["matched"][0] if gate is gc else validation["matched"]
    assert pg.verdict(held[matched], bd["bar"])["verdict"] == verdict

    q = json.loads(BETWEEN_P075.read_text())["qualification"]
    q["validation"] = gc.summarize_validation(cal)
    dec = gate.decide(q, False)
    q4 = dec["gates"]["Q4 stays green"]
    if verdict == "REFUSED":
        assert dec["status"] == "REFUSED", q4
        assert dec["unrun"] == ["Q4 stays green"] and q4["ok"] is None
        assert "BD" in q4["observed"] and "REFUSED" in q4["observed"]
        assert "REFUSED" in bd["status"]
        assert q["validation"]["by_sound"]["BD"] is None and q["validation"]["best_passes"] is None
    else:
        assert dec["status"] == "QUALIFIED" and q4["ok"] is True
        assert ("9/9" if passes else "8/9") in q4["observed"]
    assert validation["best_passes"] is passes


@pytest.mark.parametrize("record_path", [
    SCORECARD / "mars-calibration/calibrationforcek5.json", BETWEEN_P075,
])
def test_stored_validation_booleans_agree_with_measured_verdicts(real, record_path):
    # Legacy False could also mean REFUSED. Check each archived take against
    # its stored bar before relying on the historical 2/9 and 7/9 replays.
    cal = json.loads(record_path.read_text())["calibration"]
    for sound in VAL9:
        row = cal["sounds"][sound]
        val = row["validation"]
        matched = val["matched"]
        rel = matched[0] if isinstance(matched, list) else matched
        d = next(r["d"] for r in real[sound]["rows"] if r["rel"] == rel)
        assert pg.verdict(d, row["bar"])["verdict"] == ("PASS" if val["best_passes"] else "FAIL")


def test_q4_replay_on_stored_v2_diagnostic_covers_both_callers():
    """Replay `mars-calibration-v2/between-p0.75.json` through `gc.decide` and
    `gb.decide`. Complete record fails Q4 on 7/9 (genuine failed held-out takes
    in HC and CY). Omitting HC's validation entry, passing count-only 7/8, or
    swapping HC for RS must REFUSE naming HC, never turn Q4 green."""
    rec = json.loads(BETWEEN_P075.read_text())
    cal_ok = copy.deepcopy(rec["calibration"])
    q_ok = copy.deepcopy(rec["qualification"])
    q_ok["known_bad"][gb.CANDIDATE] = {"verdict": "FAIL"}
    q_ok["validation"] = gc.summarize_validation(cal_ok)
    for dec_fn in (gc.decide, gb.decide):
        d = dec_fn(q_ok, False)
        assert d["status"] == "NOT QUALIFIED"
        assert d["gates"]["Q4 stays green"]["ok"] is False
        assert "7/9" in d["gates"]["Q4 stays green"]["observed"]

    # Omit HC's failed validation entry (7/8 = 87.5% >= 80% on unfixed decide)
    cal_no_hc = copy.deepcopy(cal_ok)
    del cal_no_hc["sounds"]["HC"]["validation"]
    q_no_hc = copy.deepcopy(q_ok)
    q_no_hc["validation"] = gc.summarize_validation(cal_no_hc)
    for dec_fn in (gc.decide, gb.decide):
        d = dec_fn(q_no_hc, False)
        assert d["status"] == "REFUSED" and "Q4 stays green" in d["unrun"]
        assert d["gates"]["Q4 stays green"]["ok"] is None
        assert "HC" in d["gates"]["Q4 stays green"]["observed"]

    # Legacy count-only validation dict without per-sound identities also refuses
    q_counts = copy.deepcopy(q_ok)
    q_counts["validation"] = {"sounds": 8, "best_passes": 7}
    for dec_fn in (gc.decide, gb.decide):
        d = dec_fn(q_counts, False)
        assert d["status"] == "REFUSED" and d["gates"]["Q4 stays green"]["ok"] is None

    # Rule-8 defeating input for a count-only check: replace HC: False with RS: True
    q_swap = copy.deepcopy(q_no_hc)
    q_swap["validation"]["by_sound"]["RS"] = True
    q_swap["validation"]["sounds"] = 9
    q_swap["validation"]["best_passes"] = 8
    for dec_fn in (gc.decide, gb.decide):
        d = dec_fn(q_swap, False)
        assert d["status"] == "REFUSED" and d["gates"]["Q4 stays green"]["ok"] is None
        obs = d["gates"]["Q4 stays green"]["observed"]
        assert "HC" in obs and "RS" in obs


def test_bypassing_pool_and_validation_checks_separately_restores_each_failure(real, monkeypatch):
    """Mutation / bypass proof that the pool-eligibility check and the validation
    check are independently live on the two HC inputs."""
    # 1. Bypass ONLY the v2 pool check on HC Case (a): validation check stays active
    # and passes (all 4 VAL keys remain), while HC silently drops out of `eligible`.
    z_pool = _hc_lost_pool(_zero(real))
    assert gb.calibrate(z_pool)["status"].startswith("REFUSED: incomplete evidence")
    with monkeypatch.context() as mp:
        mp.setattr(gc, "v2_pool_issues", lambda *a, **k: [])
        cal_a = gb.calibrate(z_pool)
        assert cal_a["status"] == "CALIBRATED"
        assert "HC" not in cal_a["selection"]["eligible"] and len(cal_a["selection"]["eligible"]) == 8
        assert cal_a["sounds"]["HC"]["status"].startswith("WEAK-UNVALIDATED")

    # 2. Bypass ONLY the validation checks on HC Case (b): pool check stays active
    # and passes (all 18 pool keys remain); bypassing upstream lets `calibrate` emit
    # 7/8 while Q4 still refuses, and bypassing Q4 too flips 7/9 FAIL -> 7/8 QUALIFIED.
    hc_b = _hc_lost_val(real)
    assert gb.calibrate(hc_b, force_p=0.75)["status"].startswith("REFUSED: incomplete evidence")
    rec = json.loads(BETWEEN_P075.read_text())
    q_base = copy.deepcopy(rec["qualification"])
    q_base["known_bad"][gb.CANDIDATE] = {"verdict": "FAIL"}
    with monkeypatch.context() as mp:
        mp.setattr(gc, "validation_evidence_issues", lambda *a, **k: [])
        cal_b = gb.calibrate(hc_b, force_p=0.75)
        assert cal_b["status"] == "DIAGNOSTIC-CALIBRATION"
        assert "HC" in cal_b["selection"]["eligible"]
        assert "validation" not in cal_b["sounds"]["HC"]
        q_b = copy.deepcopy(q_base)
        q_b["validation"] = gc.summarize_validation(cal_b)
        assert q_b["validation"]["sounds"] == 8 and q_b["validation"]["best_passes"] == 7
        # Q4 identity check still catches HC before reducing to 7/8
        assert gb.decide(q_b, False)["status"] == "REFUSED"
        assert gc.decide(q_b, False)["status"] == "REFUSED"
        # Bypassing Q4 identity check as well restores the 7/8 QUALIFIED bug
        mp.setattr(gc, "validation_refusal_q4", lambda *a, **k: None)
        assert gb.decide(q_b, False)["status"] == "QUALIFIED"
        assert gc.decide(q_b, False)["status"] == "QUALIFIED"


# --- #1329: a REFUSED decision must not exit 0 (tools/exit_contract.py) ---------

def _run_between_main(tmp_path, tables):
    tp = tmp_path / "tables.json"
    tp.write_text(json.dumps({"tables": tables}))
    out = tmp_path / "out.json"
    return gb.main(["--refs", str(tmp_path), "--tables", str(tp), "--out", str(out)]), out


def test_main_exits_refused_when_no_p_reaches_coverage(real, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gc, "old_bar_context", lambda *a, **k: {})
    rc, out = _run_between_main(tmp_path, real)
    decision = json.loads(capsys.readouterr().out)
    assert decision["status"] == "REFUSED" and decision["why"].startswith("REFUSED: no p in P_GRID")
    assert json.loads(out.read_text())["decision"]["status"] == "REFUSED"
    assert rc == exit_contract.REFUSED


def test_main_exits_refused_when_a_gate_is_unrun(tmp_path, monkeypatch, capsys):
    q = _green_q()
    q["validation"]["rule"] = "v2"
    q["known_bad"][gb.CANDIDATE] = {"verdict": "REFUSED"}
    monkeypatch.setattr(gb, "calibrate", lambda tabs, force_p=None: {"p": 0.1, "status": "ok", "sounds": {}})
    monkeypatch.setattr(gc, "qualify", lambda cal, refs, ours: q)
    monkeypatch.setattr(gc, "old_bar_context", lambda *a, **k: {})
    rc, _ = _run_between_main(tmp_path, {})
    decision = json.loads(capsys.readouterr().out)
    assert decision["status"] == "REFUSED"
    assert decision["unrun"] == ["Q5 corrupted corpus", "Q6 rejected #374 candidate"]
    assert rc == exit_contract.REFUSED
