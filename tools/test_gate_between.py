"""Known answers for tools/gate_between.py (#379, rule v2). Fast: no audio is read."""
import copy
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_between as gb  # noqa: E402
import gate_calibrate as gc  # noqa: E402
import perceptual_gate as pg  # noqa: E402


def _d(spec, other=1.0):
    d = {f: other for f in pg.FEATURES}
    d.update(pitch=None, pitch_shape=None, modulation=None, spec=spec)
    return d


def _tab(specs_by_group, other=1.0, sound="BD"):
    rows = []
    for g, ds in specs_by_group.items():
        for i, sp in enumerate(ds):
            key = f"k{g}_{i}"
            rows.append({"rel": f"x/{key} Digital.wav", "chain": "Digital", "key": key, "group": g,
                         "d": _d(sp, other)})
    return {"sound": sound, "target": "t", "floor": {f: 0.0 for f in pg.FEATURES}, "rows": rows,
            "missing": [], "keys": sorted({r["key"] for r in rows})}


@pytest.fixture(autouse=True)
def _keep_groups(monkeypatch):
    # synthetic rows carry their own groups; the hash rule is gate_calibrate's
    monkeypatch.setattr(gc, "regroup", lambda tabs: tabs)


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
    cal = gb.calibrate({"BD": _refusing_pool()})
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
    cal = gb.calibrate({"BD": _tab(pool), "RS": _tab({0: [0.5], 1: [0.5]}, sound="RS")})
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
    base = gb.calibrate({"BD": t})
    t2 = copy.deepcopy(t)
    for r in t2["rows"]:
        if r["group"] == 2:
            r["d"] = _d(7777.0, other=7777.0)
    mod = gb.calibrate({"BD": t2})
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


def test_perfect_mars_take_omits_the_feature_from_its_cell():
    exact = pg.verdict({"spec": 0.0}, {"spec": 1.0})
    named = pg.verdict({"spec": 2.0}, {"spec": 1.0})
    context = {"old_bar": "x", "n_takes": 1}
    rows = gb.rank_rows({"LT": {**context, "ours": named, "mars_best": exact, "mars_takes_passing_old_bar": 1},
                         "MT": {**context, "ours": exact, "mars_best": named, "mars_takes_passing_old_bar": 0}})
    table = gb.render_table(rows)
    assert "None" not in table
    assert "| 2.0 (spec) | 0.0 |" in table
    assert "| 0.0 | 2.0 (spec) |" in table


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
            "validation": {"sounds": 1, "best_passes": 1}}


@pytest.mark.parametrize("verdict,status", [("FAIL", "QUALIFIED"), ("PASS", "NOT QUALIFIED"),
                                            ("REFUSED", "REFUSED"), (None, "REFUSED")])
def test_the_rejected_374_candidate_gates_qualification(verdict, status):
    """Every other gate green; only the #374 candidate varies (absent == None)."""
    q = _green_q()
    assert gc.decide(q, False)["status"] == "QUALIFIED"           # v1 never looked at the candidate
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
    q["known_bad"][gb.CANDIDATE] = {"verdict": "FAIL"}
    dec = gb.decide(q, inj)
    assert dec["status"] == "REFUSED" and dec["unrun"] == ["Q5 corrupted corpus"]
    assert gc.injected_outcome(_green_q())[0] is True             # a corrupted corpus that qualifies
    assert gc.injected_outcome(None) == (None, None)
    bad = _green_q()
    bad["seeded_summary"].update(caught=5, rate=0.5)
    assert gc.injected_outcome(bad)[0] is False                   # completed NOT QUALIFIED = caught


# --- #1329: a REFUSED decision must not exit 0 (tools/exit_contract.py) ---------

def _run_between_main(tmp_path):
    import json

    tp = tmp_path / "tables.json"
    tp.write_text(json.dumps({"tables": {}}))
    out = tmp_path / "out.json"
    return gb.main(["--refs", str(tmp_path), "--tables", str(tp), "--out", str(out)]), out


def test_main_exits_refused_when_no_p_reaches_coverage(tmp_path, capsys):
    import json

    import exit_contract

    rc, out = _run_between_main(tmp_path)
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    assert json.loads(out.read_text())["decision"]["status"] == "REFUSED"
    assert rc == exit_contract.REFUSED


def test_main_exits_refused_when_a_gate_is_unrun(tmp_path, monkeypatch, capsys):
    import exit_contract

    q = _green_q()
    q["known_bad"][gb.CANDIDATE] = {"verdict": "REFUSED"}
    monkeypatch.setattr(gb, "calibrate", lambda tabs, force_p=None: {"p": 0.1, "status": "ok", "sounds": {}})
    monkeypatch.setattr(gc, "qualify", lambda cal, refs, ours: q)
    monkeypatch.setattr(gc, "old_bar_context", lambda *a, **k: {})
    rc, _ = _run_between_main(tmp_path)
    assert gb.decide(q, False)["status"] == "REFUSED"
    assert rc == exit_contract.REFUSED


# ---- `table` on a record missing the key main() reads with [] is REFUSED
# (exit 2), not a traceback (#1604, #1623), through the tool's own main().
_CTX = {"A": {"old_bar": "WEAK", "n_takes": 4, "mars_takes_passing_old_bar": 0,
              "mars_best": {"worst_ratio": 2.0, "worst_feature": "spec"},
              "ours": {"worst_ratio": 4.0, "worst_feature": "decay"}},
        "C": {"old_bar": "x", "n_takes": 4, "mars_takes_passing_old_bar": 0,
              "mars_best": {"worst_ratio": 1.0, "worst_feature": "spec"}}}


def _table(tmp_path, capsys, record):
    p = tmp_path / "gate.json"
    p.write_text(json.dumps(record))
    rc = gb.main(["table", str(p)])
    return rc, capsys.readouterr().out


def test_table_renders_a_complete_record(tmp_path, capsys):
    """The guard must be satisfiable against the record main() writes."""
    rc, out = _table(tmp_path, capsys, {"calibration": {"old_bar_context": _CTX},
                                        "qualification": None, "decision": {"status": "REFUSED"}})
    assert rc == 0 and "no render" in out and "REFUSED" not in out


def test_table_refuses_a_record_without_calibration(tmp_path, capsys):
    rc, out = _table(tmp_path, capsys, {"qualification": None, "decision": {"status": "QUALIFIED"}})
    assert rc == 2 and "REFUSED" in out and "'calibration'" in out
    assert "|" not in out                          # no table over a partial record


def test_table_refuses_a_record_with_null_calibration(tmp_path, capsys):
    rc, out = _table(tmp_path, capsys, {"calibration": None})
    assert rc == 2 and "REFUSED" in out and "'calibration'" in out


@pytest.mark.parametrize("record, why", [({}, "'calibration'"), ([], "not a JSON object"),
                                         (None, "not a JSON object")])
def test_table_refuses_a_record_that_is_not_an_object(tmp_path, capsys, record, why):
    rc, out = _table(tmp_path, capsys, record)
    assert rc == 2 and "REFUSED" in out and why in out
