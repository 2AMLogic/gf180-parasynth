"""Known answers for tools/gate_calibrate.py (#379). Fast: no audio is read."""
import csv
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate_calibrate as gc  # noqa: E402
import perceptual_gate as pg  # noqa: E402

INDEX = pathlib.Path(__file__).resolve().parents[1] / "refaudio" / "index" / "808-from-mars.tsv"


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


# ---- #1296: a partial record must not calibrate where the full corpus is refused

COMMITTED = pathlib.Path(__file__).resolve().parents[1] / "docs/scorecard/gate-379/mars-calibration"


def _envelope(sounds=None, keys=None):
    """The committed 16-sound record, optionally cut down. `sounds` is the
    declared set, `keys` the rows actually kept (defaults to the declared set)."""
    import json
    r = json.loads((COMMITTED / "tables.json").read_text())
    keep = list(pg.SOUNDS16) if keys is None else keys
    env = {"corrupt": r["corrupt"], "archive": r["archive"], "tables": {s: r["tables"][s] for s in keep}}
    if sounds is not None:
        env["sounds"] = sounds
    return env


def test_committed_record_declares_its_sounds():
    import json
    r = json.loads((COMMITTED / "tables.json").read_text())
    assert set(r["sounds"]) == set(r["tables"]) == set(pg.SOUNDS16)


def test_one_sound_record_is_refused_though_the_pure_function_calibrates_it():
    """The #1296 observation: BD alone calibrates (k=3) where all 16 are refused."""
    env = _envelope(sounds=["BD"], keys=["BD"])
    assert gc.calibrate(env["tables"])["status"] == "CALIBRATED"   # the hole, in the pure function
    cal = gc.calibrate_record(env)
    assert cal["status"].startswith("REFUSED") and cal["k"] is None and cal["sounds"] == {}


def test_five_sound_cymbal_subset_is_refused():
    sub = ["CY", "BD", "SD", "LC", "MT"]
    env = _envelope(sounds=sub, keys=sub)
    assert gc.calibrate(env["tables"])["k"] is not None            # it would calibrate
    assert gc.calibrate_record(env)["status"].startswith("REFUSED")


def test_full_record_outcome_unchanged():
    cal = gc.calibrate_record(_envelope(sounds=list(pg.SOUNDS16)))
    assert cal["k"] is None and cal["status"].startswith("REFUSED: no k reaches")


def test_record_without_sounds_or_empty_is_refused():
    assert gc.calibrate_record(_envelope())["status"].startswith("REFUSED")
    assert gc.calibrate_record({"tables": {}})["status"].startswith("REFUSED")
    assert gc.calibrate_record({})["status"].startswith("REFUSED")


def test_declared_set_is_compared_to_the_rows_not_to_itself():
    """Rule-8 control (set level): the input that satisfies a check of `sounds`
    alone (or of the table keys alone) while violating its intent."""
    # claims all 16, carries one table key
    lie = _envelope(sounds=list(pg.SOUNDS16), keys=["BD"])
    assert gc.calibrate_record(lie)["status"].startswith("REFUSED")
    # carries all 16 table keys, declares one
    assert gc.calibrate_record(_envelope(sounds=["BD"]))["status"].startswith("REFUSED")
    # 16 names, but not the right 16
    other = _envelope(sounds=list(pg.SOUNDS16)[:-1] + ["XX"])
    assert gc.calibrate_record(other)["status"].startswith("REFUSED")
    # extra table key beyond the 16
    extra = _envelope(sounds=list(pg.SOUNDS16))
    extra["tables"]["XX"] = extra["tables"]["BD"]
    assert gc.calibrate_record(extra)["status"].startswith("REFUSED")


def _gut_rows(env, keep, clear_keys=True):
    """Keep all 16 table keys, but empty `rows` (and `missing`) for sounds outside `keep`."""
    import copy
    out = copy.deepcopy(env)
    for s in pg.SOUNDS16:
        if s not in keep:
            out["tables"][s]["rows"] = []
            out["tables"][s]["missing"] = []
            if clear_keys:
                out["tables"][s]["keys"] = []
    return out


def test_empty_rows_for_other_sounds_is_refused():
    """Rule-8 control (row level): declares all 16 and carries all 16 table keys,
    but only a subset has measured rows (the shape `measure` writes against a
    partial recordings cache). A key-set-only guard lets this calibrate."""
    full = _envelope(sounds=list(pg.SOUNDS16))
    bd_only = _gut_rows(full, {"BD"})
    assert gc.calibrate(bd_only["tables"])["k"] == 3
    assert gc.calibrate(bd_only["tables"])["status"] == "CALIBRATED"
    cal = gc.calibrate_record(bd_only)
    assert cal["status"].startswith("REFUSED") and cal["k"] is None and cal["sounds"] == {}

    # Keeping the non-empty `keys` list while emptying `rows` must still be refused
    bd_kept_keys = _gut_rows(full, {"BD"}, clear_keys=False)
    assert gc.calibrate_record(bd_kept_keys)["status"].startswith("REFUSED")

    # 5-sound CY subset with 11 sounds' rows emptied
    cy5 = _gut_rows(full, {"CY", "BD", "SD", "LC", "MT"})
    assert gc.calibrate(cy5["tables"])["k"] == 5
    assert gc.calibrate_record(cy5)["status"].startswith("REFUSED")

    # Missing `rows` field on a single sound is refused, not a crash
    no_rows_key = _envelope(sounds=list(pg.SOUNDS16))
    del no_rows_key["tables"]["RS"]["rows"]
    assert gc.calibrate_record(no_rows_key)["status"].startswith("REFUSED")

    # Explicit diagnostic opt-in still works and labels the result not-authority
    sub = gc.calibrate_record(bd_only, allow_subset=True)
    assert sub["k"] == 3 and sub["diagnostic"].startswith("DIAGNOSTIC (not authority)")


def test_allow_subset_is_labelled_diagnostic():
    env = _envelope(sounds=["BD"], keys=["BD"])
    cal = gc.calibrate_record(env, allow_subset=True)
    assert cal["k"] == 3 and cal["diagnostic"].startswith("DIAGNOSTIC (not authority)")
