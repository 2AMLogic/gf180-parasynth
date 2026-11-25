"""Known answers for tools/gate_calibrate.py (#379). Fast: no audio is read."""
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


def _tab(dists_by_group, n_keys=9):
    rows = []
    for g, ds in dists_by_group.items():
        for i, sp in enumerate(ds):
            rows.append({"rel": f"x/{g}/{i} Digital.wav", "chain": "Digital", "key": f"k{g}{i}", "group": g,
                         "d": _d(sp)})
    return {"sound": "BD", "target": "t", "floor": {f: 0.0 for f in pg.FEATURES}, "rows": rows,
            "missing": [], "keys": [f"k{i}" for i in range(n_keys)]}


def test_make_bar_takes_max_over_matched_set_and_applies_floors():
    t = _tab({1: [0.2, 0.3, 0.4]})
    bar = gc.make_bar(gc.matched(t["rows"], 1, 2), t["floor"])
    assert bar["spec"] == pytest.approx(0.3)             # nearest two by spec, max of them
    assert bar["attack"] == pytest.approx(1.0)           # never below the perceptual floor
    assert bar["pitch"] is None and bar["modulation"] is None
    assert gc.make_bar([], t["floor"]) is None


def test_selection_refuses_rather_than_widen(monkeypatch):
    """Injected defect for the selection rule: the development group sits far
    from the calibration group, so no k reaches coverage. The answer is a
    REFUSED calibration with no bars, not a looser k."""
    # keep the rows' own groups: make regroup a no-op for the synthetic table
    monkeypatch.setattr(gc, "regroup", lambda tabs: tabs)
    far = {0: [0.1, 0.1, 0.1, 0.1, 0.1], 1: [5.0, 5.0, 5.0, 5.0, 5.0], 2: [5.0]}
    cal = gc.calibrate({"BD": _tab(far)})
    assert cal["k"] is None and cal["status"].startswith("REFUSED")
    assert "sounds" in cal and cal["sounds"] == {}


def test_calibrates_when_groups_agree_and_marks_unvalidated(monkeypatch):
    monkeypatch.setattr(gc, "regroup", lambda tabs: tabs)
    near = {0: [0.5, 0.6, 0.7], 1: [0.5, 0.55, 0.6], 2: [0.45]}
    cal = gc.calibrate({"BD": _tab(near), "RS": _tab({0: [0.5], 1: [0.5]}, n_keys=2)})
    assert cal["k"] == 1 and cal["status"] == "CALIBRATED"
    assert cal["sounds"]["BD"]["status"] == "VALIDATED"
    assert cal["sounds"]["RS"]["status"].startswith("UNVALIDATED")   # < 3 keys: cannot hold anything out


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
def _old_bar_fixture(tmp_path, monkeypatch, drop_neighbour):
    """Refs dir where BD's target exists and, unless `drop_neighbour`, so do its
    neighbours. The ONLY defect in the incomplete case is the missing WAV."""
    import numpy as np

    rel = pg.target_rel("BD")
    nbs = pg.neighbours("BD", rel)
    assert nbs, "BD must be a multi-take sound for this control to mean anything"
    present = [rel, *nbs[1:]] if drop_neighbour else [rel, *nbs]
    for p in present:
        (tmp_path / p).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / p).write_bytes(b"x")
    rng = np.random.default_rng(0)
    sig = np.concatenate([np.zeros(2400), rng.standard_normal(9600) * np.exp(-np.arange(9600) / 1500)])
    monkeypatch.setattr(pg, "load_wav", lambda p: (sig.copy(), 48000))
    # One valid take row, so the COMPLETE fixture runs the real function end to end.
    tabs = {"BD": {"target": rel, "rows": [{"rel": "x/take.wav", "d": _d(0.5)}]}}
    return tabs, nbs[0]


def test_old_bar_context_records_refused_row_naming_the_missing_wav(tmp_path, monkeypatch):
    tabs, gone = _old_bar_fixture(tmp_path, monkeypatch, drop_neighbour=True)
    out = gc.old_bar_context(tabs, tmp_path, None)
    assert out["BD"]["old_bar"] == "REFUSED"
    assert gone in out["BD"]["why"]  # not a blanket swallow: names the defect


def test_old_bar_context_refusal_does_not_hide_other_sounds(tmp_path, monkeypatch):
    tabs, _ = _old_bar_fixture(tmp_path, monkeypatch, drop_neighbour=True)
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

    tabs, _ = _old_bar_fixture(tmp_path, monkeypatch, drop_neighbour=True)
    tpath = tmp_path / "tables.json"
    tpath.write_text(json.dumps({"tables": tabs}))
    monkeypatch.setattr(gc, "calibrate", lambda t, k=None: {"k": None, "status": "stub"})
    monkeypatch.setattr(gc, "regroup", lambda t: t)
    monkeypatch.setattr(pg, "provenance", lambda a: {})
    out = tmp_path / "out.json"
    rc = gc.main(["calibrate", "--refs", str(tmp_path), "--tables", str(tpath), "--out", str(out)])
    assert rc == exit_contract.REFUSED == 2
    assert json.loads(out.read_text())["calibration"]["old_bar_context"]["BD"]["old_bar"] == "REFUSED"


def test_main_exits_zero_when_every_old_bar_builds(tmp_path, monkeypatch):
    tabs, _ = _old_bar_fixture(tmp_path, monkeypatch, drop_neighbour=False)
    tpath = tmp_path / "tables.json"
    tpath.write_text(json.dumps({"tables": tabs}))
    monkeypatch.setattr(gc, "calibrate", lambda t, k=None: {"k": None, "status": "stub"})
    monkeypatch.setattr(gc, "regroup", lambda t: t)
    monkeypatch.setattr(pg, "provenance", lambda a: {})
    out = tmp_path / "out.json"
    assert gc.main(["calibrate", "--refs", str(tmp_path), "--tables", str(tpath), "--out", str(out)]) == 0
    ctx = json.loads(out.read_text())["calibration"]["old_bar_context"]
    assert ctx["BD"]["old_bar"] != "REFUSED" and ctx["BD"]["n_takes"] == 1   # the real function ran
