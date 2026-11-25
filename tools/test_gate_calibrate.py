"""Known answers for tools/gate_calibrate.py (#379). Fast: no audio is read."""
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


def test_non_utf8_index_refuses_rather_than_raises(real, monkeypatch, tmp_path):
    bad = tmp_path / "bad.tsv"
    bad.write_bytes(b"path\n\xff\n")
    monkeypatch.setattr(gc, "INDEX", bad)
    monkeypatch.setattr(gc.expected_takes, "__defaults__", (bad,))
    for fk in (None, 3):
        cal = gc.calibrate(_zero(real), force_k=fk)
        assert cal["k"] is None and cal["sounds"] == {}
        assert cal["status"].startswith("REFUSED: incomplete evidence: cannot read the file index bad.tsv (UnicodeDecodeError)")


def test_table_without_missing_key_refuses_naming_sound_and_field(real):
    t = _zero(real)
    assert gc.calibrate(t)["status"] == "CALIBRATED"  # control: restored input is clean
    del t["BD"]["missing"]
    for fk in (None, 3):
        cal = gc.calibrate(t, force_k=fk)
        assert cal["k"] is None and cal["sounds"] == {}
        assert cal["status"].startswith("REFUSED: incomplete evidence") and "BD: record has no 'missing'" in cal["status"]
    t["BD"]["missing"] = []
    assert gc.calibrate(t)["status"] == "CALIBRATED"


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


# --- #1329: a REFUSED decision must not exit 0 (tools/exit_contract.py) ---------

import exit_contract  # noqa: E402


def _run_calibrate_main(tmp_path, tables, extra=()):
    tp = tmp_path / "tables.json"
    tp.write_text(json.dumps({"tables": tables}))
    out = tmp_path / "out.json"
    rc = gc.main(["calibrate", "--refs", str(tmp_path), "--tables", str(tp), "--out", str(out), *extra])
    return rc, out


def test_main_exits_refused_when_no_k_reaches_coverage(tmp_path, capsys):
    """Real main(), real calibrate/decide: empty tables leave no k -> REFUSED decision."""
    rc, out = _run_calibrate_main(tmp_path, {})
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    assert json.loads(out.read_text())["decision"]["status"] == "REFUSED"   # artefact written first
    assert rc == exit_contract.REFUSED


def test_main_exits_refused_when_a_gate_is_unrun(tmp_path, monkeypatch, capsys):
    """Real main() and real decide; only the upstream measurement is faked so a
    gate (Q3 shipped-cymbal) is genuinely unrun inside decide()."""
    q = {"sounds_with_bar": ["BD"], "start_red": {"BD": "FAIL", "BD-silence": "REFUSED"},
         "seeded_summary": {"n": 10, "caught": 10, "missed": [], "rate": 1.0},
         "known_bad": {"shipped-cymbal": {"verdict": "REFUSED"}}, "clean": {"BD": "PASS"},
         "validation": {"sounds": 1, "best_passes": 1}}
    monkeypatch.setattr(gc, "calibrate", lambda tabs, force_k=None: {"k": 3, "status": "ok"})
    monkeypatch.setattr(gc, "qualify", lambda cal, refs, ours: q)
    monkeypatch.setattr(gc, "old_bar_context", lambda *a, **k: {})
    rc, _ = _run_calibrate_main(tmp_path, {})
    assert json.loads(capsys.readouterr().out)["status"] == "REFUSED"
    assert rc == exit_contract.REFUSED


@pytest.mark.parametrize("status,code", [
    ("REFUSED", 2), ("DIAGNOSTIC (not authority): REFUSED", 2),
    ("QUALIFIED", 0), ("NOT QUALIFIED", 0), ("DIAGNOSTIC (not authority): QUALIFIED", 0)])
def test_decision_exit_maps_refused_only(status, code):
    assert gc.decision_exit({"status": status}) == code
