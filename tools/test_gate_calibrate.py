"""Known answers for tools/gate_calibrate.py (#379). Uses synthetic audio for the old-bar CLI controls."""
import copy
import csv
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_calibrate as gc  # noqa: E402
import perceptual_gate as pg  # noqa: E402

INDEX = pathlib.Path(__file__).resolve().parents[1] / "refaudio" / "index" / "808-from-mars.tsv"


@pytest.fixture
def measured_sounds(monkeypatch):
    calls = []

    def record(sound, refs, corrupt=None):
        calls.append(sound)
        return {"sound": sound, "rows": [], "missing": []}

    monkeypatch.setattr(gc, "measure", record)
    return calls


@pytest.mark.parametrize("existing", [True, False])
@pytest.mark.parametrize("sounds", [[], ["BDD"], ["BD", "BDD"]])
def test_measure_rejects_invalid_sounds_before_writing(
    tmp_path, capsys, measured_sounds, sounds, existing
):
    out = tmp_path / "tables.json"
    previous = b'{"tables": {"BD": {}}}\n'
    if existing:
        out.write_bytes(previous)

    with pytest.raises(SystemExit) as exc:
        gc.main(
            ["measure", "--refs", str(tmp_path), "--sounds", *sounds, "--out", str(out)]
        )

    assert exc.value.code == 2
    reason = "invalid choice: 'BDD'" if sounds else "expected at least one argument"
    assert f"error: argument --sounds: {reason}" in capsys.readouterr().err
    assert measured_sounds == []
    if existing:
        assert out.read_bytes() == previous
    else:
        assert not out.exists()


@pytest.mark.parametrize(
    "args, expected", [([], pg.SOUNDS16), (["--sounds", "CH", "BD"], ("CH", "BD"))]
)
def test_measure_writes_requested_tables(tmp_path, measured_sounds, args, expected):
    out = tmp_path / "tables.json"
    assert gc.main(["measure", "--refs", str(tmp_path), *args, "--out", str(out)]) == 0
    assert measured_sounds == list(expected)
    assert json.loads(out.read_text())["tables"] == {
        sound: {"sound": sound, "rows": [], "missing": []} for sound in expected
    }


def test_twin_pairing_on_the_real_index():
    """The first key rule paired NO twin for BD/SD/toms/congas/CY/OH (the Tape
    take names ' Tape' mid-name). Pairing is checked against the committed
    index, not a hand-picked example."""
    pre = "808 From Mars/WAV/01. Individual Hits/"
    ch = {}
    for row in csv.DictReader(INDEX.open(), delimiter="\t"):
        p = row["path"]
        if not p.startswith(pre) or "/Clean/" not in p or "Combo" in p:
            continue
        rel = p[len(pre):]
        chain = "Digital" if "/Digital/" in rel else "Tape"
        ch.setdefault(gc.key_of(rel), set()).add(chain)
    assert len(ch) == 386   # 772 takes: the index, not a guess
    unpaired = [k for k, c in ch.items() if len(c) != 2]
    assert not unpaired, unpaired[:5]


def test_group_is_deterministic_and_twins_share_it():
    a = gc.key_of("01. Bass Drum/Clean/Digital/A/BD A 808 Decay A 01.wav")
    b = gc.key_of("01. Bass Drum/Clean/Tape/A/BD A 808 Tape Decay A 01.wav")
    assert a == b
    assert gc.group_of(a) == gc.group_of(b) == gc.group_of(a)


def _d(spec, **kw):
    d = {f: 1.0 for f in pg.FEATURES}
    d.update(pitch=None, pitch_shape=None, modulation=None)
    d["spec"] = spec
    d.update(kw)
    return d


def _rel(g, i, sound="BD"):
    """A synthetic take path whose REAL hash group (regroup is not patched
    out any more) is g: search the name until group_of(key_of(.)) == g."""
    n = 0
    while True:
        rel = f"{gc.FOLDER[sound]}/Clean/Digital/{sound}{i}x{n}.wav"
        if gc.group_of(gc.key_of(rel)) == g:
            return rel
        n += 1


def _tab(dists_by_group, n_keys=9, sound="BD"):
    rows = []
    for g, ds in dists_by_group.items():
        for i, sp in enumerate(ds):
            rel = _rel(g, i, sound)
            rows.append({"rel": rel, "chain": "Digital", "key": gc.key_of(rel), "group": g, "d": _d(sp)})
    return {"sound": sound, "target": "t", "floor": {f: 0.0 for f in pg.FEATURES}, "rows": rows,
            "missing": [], "keys": [f"k{i}" for i in range(n_keys)]}


def _idx(tabs):
    """The synthetic index: exactly the takes the tables hold."""
    return {s: {r["rel"] for r in t["rows"]} | {m["rel"] for m in t["missing"]} for s, t in tabs.items()}


def test_make_bar_takes_max_over_matched_set_and_applies_floors():
    t = _tab({1: [0.2, 0.3, 0.4]})
    bar = gc.make_bar(gc.matched(t["rows"], 1, 2), t["floor"])
    assert bar["spec"] == pytest.approx(0.3)             # nearest two by spec, max of them
    assert bar["attack"] == pytest.approx(1.0)           # never below the perceptual floor
    assert bar["pitch"] is None and bar["modulation"] is None
    assert gc.make_bar([], t["floor"]) is None


def test_selection_refuses_rather_than_widen():
    """Injected defect for the selection rule: the development group sits far
    from the calibration group, so no k reaches coverage. The answer is a
    REFUSED calibration with no bars, not a looser k."""
    far = {0: [0.1, 0.1, 0.1, 0.1, 0.1], 1: [5.0, 5.0, 5.0, 5.0, 5.0], 2: [5.0]}
    tabs = {"BD": _tab(far)}
    cal = gc.calibrate(tabs, expected=_idx(tabs))
    assert cal["k"] is None and cal["status"].startswith("REFUSED: no k reaches")
    assert "sounds" in cal and cal["sounds"] == {}


def test_calibrates_when_groups_agree_and_marks_unvalidated():
    near = {0: [0.5, 0.6, 0.7], 1: [0.5, 0.55, 0.6], 2: [0.45]}
    tabs = {"BD": _tab(near), "RS": _tab({0: [0.5], 1: [0.5]}, n_keys=2, sound="RS")}
    cal = gc.calibrate(tabs, expected=_idx(tabs))
    assert cal["k"] == 1 and cal["status"] == "CALIBRATED"
    assert cal["sounds"]["BD"]["status"] == "VALIDATED"
    assert cal["sounds"]["RS"]["status"].startswith("UNVALIDATED")   # < 3 keys: cannot hold anything out


# ---------------------------------------------------------------------------
# #1301: a record whose sound SET is complete but whose per-sound evidence is
# not must not calibrate where the full corpus is refused.
# ---------------------------------------------------------------------------
TABLES = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/gate-379/mars-calibration/tables.json"


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


def test_committed_index_matches_the_committed_record_for_all_16(real):
    """The independent source is satisfiable: index take set == the record's."""
    exp = gc.expected_takes()
    assert set(exp) == set(pg.SOUNDS16) and all(exp.values())
    for s, t in real.items():
        assert {r["rel"] for r in t["rows"]} | {m["rel"] for m in t["missing"]} == exp[s], s


def test_full_record_still_refuses_for_the_original_reason(real):
    """Regression, separate from the new checks: the real record is refused by
    the coverage rule, not by the evidence check."""
    cal = gc.calibrate(real)
    assert cal["k"] is None and cal["status"].startswith("REFUSED: no k reaches")
    assert cal["selection"]["by_k"][3] == {"sounds": 9, "covered": 3, "coverage": pytest.approx(1 / 3)}


def test_positive_control_zero_distance_real_layout_calibrates(real):
    """The guard is not an always-refusing gate: agreeing distances on the real
    corpus layout (9 eligible sounds) calibrate."""
    cal = gc.calibrate(_zero(real))
    assert cal["status"] == "CALIBRATED" and cal["k"] is not None
    assert cal["selection"]["by_k"][cal["k"]]["sounds"] == 9


@pytest.mark.parametrize("how", ["empty", "one", "missing"])
def test_damaged_records_are_refused(real, how):
    """Start-red controls: unfixed calibrate() returned k=3 CALIBRATED on 1/1
    for each of these (recorded in the PR)."""
    cal = gc.calibrate(_damage(real, how))
    assert cal["k"] is None and cal["status"].startswith("REFUSED: incomplete evidence")


def test_missing_route_names_the_empty_group(real):
    cal = gc.calibrate(_damage(real, "missing"))
    assert "SD: no readable take in the development and calibration group" in cal["status"]


def test_one_take_dropped_from_a_four_take_sound_names_that_sound(real):
    t = copy.deepcopy(real)
    t["RS"]["rows"] = t["RS"]["rows"][1:]
    st = gc.calibrate(t)["status"]
    assert st.startswith("REFUSED: incomplete evidence") and "RS: expected 4 takes, found 3" in st
    assert "BD" not in st


def test_take_absent_from_the_index_is_refused(real):
    t = copy.deepcopy(real)
    extra = dict(t["RS"]["rows"][0], rel=t["RS"]["rows"][0]["rel"].replace(".wav", " extra.wav"))
    t["RS"]["rows"].append(extra)
    assert "RS: expected 4 takes, found 5 (0 absent, 1 not in the index)" in gc.calibrate(t)["status"]


def test_rows_missing_overlap_and_duplicates_are_refused(real):
    t = copy.deepcopy(real)
    t["RS"]["missing"].append({"rel": t["RS"]["rows"][0]["rel"], "why": "x"})
    assert "RS: 1 take(s) duplicated or in both" in gc.calibrate(t)["status"]
    t = copy.deepcopy(real)
    t["RS"]["rows"].append(dict(t["RS"]["rows"][0]))
    assert "RS: 1 take(s) duplicated or in both" in gc.calibrate(t)["status"]


@pytest.mark.parametrize("how", ["empty", "one", "missing"])
def test_force_k_does_not_bypass_the_check(real, how):
    cal = gc.calibrate(_damage(real, how), force_k=3)
    assert cal["status"].startswith("REFUSED: incomplete evidence") and cal["k"] is None


def test_one_unreadable_take_in_missing_is_not_a_refusal(real):
    """Legitimate: a take the gate cannot read sits in `missing`; the union
    still matches and the sound keeps readable development and calibration."""
    t = _zero(real)
    r = t["SD"]["rows"].pop()
    t["SD"]["missing"].append({"rel": r["rel"], "why": "no-verdict"})
    cal = gc.calibrate(t)
    assert cal["status"] == "CALIBRATED"


def test_sounds_that_cannot_populate_both_groups_stay_ineligible_not_refused(real):
    """RS/CL/CP/MA/CB/OH/CH have no development or calibration group in the
    index; they must not trip check 2 (the real record carries all of them)."""
    exp = gc.expected_takes()
    for s in ("RS", "CL", "CP", "MA", "CB", "OH", "CH"):
        assert not {0, 1} <= {gc.group_of(gc.key_of(r)) for r in exp[s]}, s
    assert gc.evidence_refusal(gc.regroup(real)) is None


def test_all_sixteen_empty_is_refused(real):
    assert gc.calibrate(_damage(real, "empty", keep=None))["status"].startswith("REFUSED: incomplete evidence")


def test_missing_index_refuses_rather_than_passes(real, monkeypatch, tmp_path):
    monkeypatch.setattr(gc, "INDEX", tmp_path / "absent.tsv")
    monkeypatch.setattr(gc.expected_takes, "__defaults__", (tmp_path / "absent.tsv",))
    cal = gc.calibrate(_zero(real))
    assert cal["k"] is None and cal["status"].startswith("REFUSED: incomplete evidence: cannot read the file index")


def test_decide_does_not_count_an_unrun_control_as_a_pass():
    q = {"sounds_with_bar": ["BD"], "start_red": {"BD": "FAIL", "BD-silence": "REFUSED"},
         "seeded_summary": {"n": 10, "caught": 10, "missed": [], "rate": 1.0},
         "known_bad": {"shipped-cymbal": {"verdict": "REFUSED"}}, "clean": {"BD": "PASS"},
         "validation": {"sounds": 1, "best_passes": 1}}
    assert gc.decide(q, False)["status"] == "REFUSED"             # Q3 could not run
    q["known_bad"]["shipped-cymbal"] = {"verdict": "FAIL"}
    assert gc.decide(q, None)["status"] == "REFUSED"              # Q5 vacuous (corpus refused too)
    assert gc.decide(q, False)["status"] == "QUALIFIED"
    assert gc.decide(q, True)["status"] == "NOT QUALIFIED"        # a corrupted corpus qualified: reject
    q["seeded_summary"].update(caught=5, rate=0.5)
    assert gc.decide(q, False)["status"] == "NOT QUALIFIED"       # a bar that misses seeds is not authority


# ---------------------------------------------------------------------------
# #1307: a missing neighbour WAV must REFUSE (exit 2, a row), never traceback.
# ---------------------------------------------------------------------------
def _old_bar_fixture(tmp_path, drop_neighbour):
    """One BD take, all its expected reference WAVs, and a longer-decay render."""
    import numpy as np
    from scipy.io import wavfile

    rel = pg.target_rel("BD")
    nbs = pg.neighbours("BD", rel)
    assert nbs, "BD must be a multi-take sound for this control to mean anything"
    noise = np.random.default_rng(0).standard_normal(9600)
    samples = np.arange(len(noise))

    def write_hit(path, decay):
        sig = np.concatenate([np.zeros(2400), noise * np.exp(-samples / decay)])
        path.parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(path, 48000, (sig / np.abs(sig).max() * 30000).astype(np.int16))

    write_hit(tmp_path / rel, 1500)
    for nb in nbs:
        write_hit(tmp_path / nb, 1800)
    write_hit(tmp_path / "ours" / "BD.wav", 3000)
    if drop_neighbour:
        (tmp_path / nbs[0]).unlink()
    tabs = {"BD": {"target": rel, "rows": [{"rel": "x/take.wav", "d": _d(0.5)}]}}
    return tabs, nbs[0]


def _run_old_bar_cli(tmp_path, monkeypatch, cli, selection, *subcommand, drop_neighbour):
    tabs, gone = _old_bar_fixture(tmp_path, drop_neighbour)
    tpath = tmp_path / "tables.json"
    tpath.write_text(json.dumps({"tables": tabs}))
    # Hold the independent qualification decision fixed while exercising the real
    # WAV lookup, bar, render comparison, JSON writer and exit path in each CLI.
    monkeypatch.setattr(cli, "calibrate", lambda tabs, override: {"status": "CALIBRATED", **selection})
    monkeypatch.setattr(gc, "qualify", lambda cal, refs, ours: {"sounds_with_bar": ["BD"]})
    monkeypatch.setattr(cli, "decide", lambda q, injected: {"status": "QUALIFIED"})
    out = tmp_path / "out.json"
    rc = cli.main([*subcommand, "--refs", str(tmp_path), "--tables", str(tpath),
                   "--ours", str(tmp_path / "ours"), "--out", str(out)])
    return rc, out, gone


def test_old_bar_context_records_refused_row_naming_the_missing_wav(tmp_path):
    tabs, gone = _old_bar_fixture(tmp_path, drop_neighbour=True)
    out = gc.old_bar_context(tabs, tmp_path, None)
    assert out["BD"]["old_bar"] == "REFUSED"
    assert gone in out["BD"]["why"]  # not a blanket swallow: names the defect


def test_old_bar_context_refusal_does_not_hide_other_sounds(tmp_path, monkeypatch):
    tabs, _ = _old_bar_fixture(tmp_path, drop_neighbour=True)
    real = pg.bar_for

    def bar_for(s, refs, T=None):
        if s == "OK":
            return {"kind": "stub", "bar": {}}
        return real(s, refs, T)

    monkeypatch.setattr(pg, "bar_for", bar_for)
    monkeypatch.setattr(pg, "verdict", lambda d, b: {"verdict": "PASS", "worst_ratio": 0.0, "worst_feature": None, "failing": []})
    tabs["OK"] = {"target": tabs["BD"]["target"], "rows": [{"rel": "r", "d": {}}]}
    out = gc.old_bar_context(tabs, tmp_path, None)
    assert out["BD"]["old_bar"] == "REFUSED" and out["OK"]["old_bar"] == "stub"


def test_main_exits_refused_and_still_writes_out(tmp_path, monkeypatch):
    import exit_contract

    rc, out, gone = _run_old_bar_cli(tmp_path, monkeypatch, gc, {"k": 1}, "calibrate", drop_neighbour=True)
    report = json.loads(out.read_text())
    assert report["decision"]["status"] == "QUALIFIED"
    row = report["calibration"]["old_bar_context"]["BD"]
    assert row["old_bar"] == "REFUSED" and gone in row["why"]
    assert rc == exit_contract.REFUSED == 2


def test_main_exits_zero_when_every_old_bar_builds(tmp_path, monkeypatch):
    rc, out, _ = _run_old_bar_cli(tmp_path, monkeypatch, gc, {"k": 1}, "calibrate", drop_neighbour=False)
    assert rc == 0
    report = json.loads(out.read_text())
    assert report["decision"]["status"] == "QUALIFIED"
    ctx = report["calibration"]["old_bar_context"]
    assert ctx["BD"]["old_bar"] == "808-neighbour" and ctx["BD"]["n_takes"] == 1   # the real function ran
