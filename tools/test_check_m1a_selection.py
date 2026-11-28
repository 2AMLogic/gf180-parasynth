"""The selection check must be able to fail: controls on the committed records."""
import copy
import json

import pytest

import check_m1a_selection as sel


def _inputs():
    record = json.loads(sel.RECORD.read_text())
    candidate = json.loads(sel.CANDIDATE.read_text())
    baseline = json.loads(sel.BASELINE.read_text())
    manifest, _ = sel.bass.load_reference()
    return record, candidate, baseline, manifest


def test_promoted_record_is_the_selected_candidate():
    assert sel.main([]) == 0


def test_controls_turn_it_red():
    record, candidate, baseline, manifest = _inputs()
    # the pre-selection baseline presented as the record
    stale = copy.deepcopy(record)
    stale["diagnostics"]["configuration"]["patch_id"] = "m1a-provisional-v1"
    stale["diagnostics"]["model_audio_sha256"] = baseline["sha256"]
    assert any("patch_id" in p for p in sel.check(stale, candidate, baseline, manifest))
    # one property moved in the fifth decimal
    moved = copy.deepcopy(record)
    moved["diagnostics"]["properties"]["Gain"]["error"] += 1e-5
    assert any("Gain" in p for p in sel.check(moved, candidate, baseline, manifest))
    # one partial moved beyond the event tolerance
    drift = copy.deepcopy(record)
    drift["diagnostics"]["events"][2]["harmonic_error_db_model_minus_reference"]["h8"] += 1e-3
    assert any("events" in p for p in sel.check(drift, candidate, baseline, manifest))
    # a candidate that lost a baseline pass violates the preservation policy
    lossy = copy.deepcopy(candidate)
    lossy["measurements"]["properties"]["Pitch"]["error"] = 2.0
    assert any("preservation" in p for p in sel.check(record, lossy, baseline, manifest))


def test_event_deviation_refuses_structure_changes():
    assert sel.event_deviation({"a": [1., 2.]}, {"a": [1., 2.5]}) == .5
    assert sel.event_deviation({"a": 1.}, {"b": 1.}) is None
    assert sel.event_deviation({"a": None}, {"a": 0.}) is None
    assert sel.event_deviation({"a": "x"}, {"a": "y"}) is None


# ---- a non-finite attack reading in the v3 event list is REFUSED before
# max() drops it (#2081). On the v3 path `check` names "its own worst event"
# by the largest |model - reference| over the board record's
# `diagnostics.events[*].attack_10_90_ms.{model,reference}`. A NaN compares
# with nothing, so a NaN that is not the first pair is never the greatest:
# another event was named, and with a NaN at event 1 the tool printed MATCH
# (exit 0) for a record carrying a reading nobody can read.
WORST_EVENT = "record attack reading is not its own worst event"
NOT_A_FINITE_NUMBER = "the M1A board record carries a value that is not a finite number: "
EVENT_ATTACKS = [  # (event index, side, a finite value that moves the worst pair)
    pytest.param(0, "model", 20.0, id="event0-model"),
    pytest.param(0, "reference", -9.0, id="event0-reference"),
    pytest.param(1, "model", 20.0, id="event1-model"),
    pytest.param(1, "reference", -9.0, id="event1-reference"),
    pytest.param(2, "model", 8.0, id="event2-model"),
    pytest.param(2, "reference", 5.0, id="event2-reference"),
]
# where one non-finite value goes. The committed record's worst pair is event
# 2; event 1 is neither first nor worst, the position that printed MATCH.
EVENT_POSITIONS = [
    pytest.param((1, "model"), id="non-first"),
    pytest.param((1, "reference"), id="non-first-reference"),
    pytest.param((2, "model"), id="non-first-worst"),
    pytest.param((2, "reference"), id="non-first-worst-reference"),
    pytest.param((0, "model"), id="first"),
    pytest.param((0, "reference"), id="first-reference"),
]


def _event(index, side):
    return f"diagnostics.events[{index}].attack_10_90_ms.{side}"


def _record_with(replacements):
    """The committed inputs with the board record's event attacks replaced:
    `{(event index, side): value}`."""
    record, candidate, baseline, manifest = _inputs()
    for (index, side), value in replacements.items():
        record["diagnostics"]["events"][index]["attack_10_90_ms"][side] = value
    return record, candidate, baseline, manifest


def _answer(replacements):
    """`check` on that record: its `mf.Refused` message, or else the problems
    it returned -- [] being MATCH."""
    try:
        return sel.check(*_record_with(replacements))
    except sel.mf.Refused as exc:
        return str(exc)


@pytest.mark.parametrize("index, side, finite", EVENT_ATTACKS)
def test_the_worst_event_cross_check_reads_each_event_attack(index, side, finite):
    """The witness for the refusals below, green before and after the repair: a
    FINITE value in that key moves the worst pair and the cross-check answers,
    so the key is one `max()` reads."""
    assert WORST_EVENT in _answer({(index, side): finite})


@pytest.mark.parametrize("position", EVENT_POSITIONS)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf")], ids=["nan", "inf", "-inf"])
def test_a_non_finite_event_attack_is_refused_naming_the_field(position, bad):
    """NaN, +inf and -inf in ONE event attack of the board record: REFUSED
    (`mf.Refused`), naming the record and the field, at every position -- the
    builtin keeps a FIRST NaN and drops any other, and both must refuse. Red
    before the repair: `_answer` returned [] (MATCH) at event 1 and a mismatch
    everywhere else; neither is a refusal."""
    assert _answer({position: bad}) == NOT_A_FINITE_NUMBER + f"{_event(*position)} = {bad!r}"


@pytest.mark.parametrize("value", [True, None, "9.95"], ids=["bool", "null", "string"])
def test_an_event_attack_that_is_not_a_number_at_all_is_refused(value):
    """`is_finite_number` is the one definition (#718): a flag, `null` and a
    numeric string are not readings either. The old code read the flag as 1.0
    and printed MATCH; the other two were a traceback (exit 1), which the exit
    contract reads as a mismatch."""
    assert _answer({(1, "model"): value}) == NOT_A_FINITE_NUMBER + f"{_event(1, 'model')} = {value!r}"


@pytest.mark.parametrize("replacements, named", [
    pytest.param({(1, "model"): float("nan")}, "diagnostics.events[1].attack_10_90_ms.model = nan",
                 id="non-first"),
    pytest.param({(0, "model"): float("nan"), (0, "reference"): float("nan")},
                 "diagnostics.events[0].attack_10_90_ms.model = nan, "
                 "diagnostics.events[0].attack_10_90_ms.reference = nan", id="first-both-sides"),
])
def test_main_refuses_a_record_on_disk_whose_event_attack_is_nan(tmp_path, capsys, replacements,
                                                                 named):
    """Through the tool's own reader: `json.dumps` writes a bare NaN,
    `load_json_refused` reads it back, and the answer is REFUSED (2) and
    nothing else. Before the repair both records printed MATCH and exited 0:
    at the first pair the builtin returns the NaN, both comparisons against it
    are False, and "its own worst event" passed."""
    record, _, _, _ = _record_with(replacements)
    path = tmp_path / "M1A.json"
    path.write_text(json.dumps(record))
    assert path.read_text().count("NaN") == len(replacements)
    code = sel.main(["--record", str(path)])
    out = capsys.readouterr().out
    assert code == 2, out
    assert out.splitlines() == ["REFUSED: " + NOT_A_FINITE_NUMBER + named], out


# ---- a record missing a key the check reads with [] is REFUSED, not a
# traceback (#1604). One control per key: each deletes ONE key from the
# committed record and runs the tool's own main().
def _main_over(monkeypatch, tmp_path, capsys, which, key):
    record, candidate, baseline, _ = _inputs()
    records = {"record": record, "candidate": candidate, "baseline": baseline}
    del records[which][key]
    paths = {}
    for name, obj in records.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(obj))
    monkeypatch.setattr(sel, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(sel, "BASELINE", paths["baseline"])
    code = sel.main(["--record", str(paths["record"])])
    return code, capsys.readouterr().out


@pytest.mark.parametrize("which, key", [
    ("record", "diagnostics"), ("record", "audio"), ("record", "metrics"),
    ("candidate", "patch"), ("candidate", "sha256"),
    ("candidate", "measurements"), ("candidate", "metrics"),
    ("baseline", "measurements"),
])
def test_a_record_missing_a_key_the_check_reads_is_refused(monkeypatch, tmp_path, capsys,
                                                            which, key):
    code, out = _main_over(monkeypatch, tmp_path, capsys, which, key)
    assert code == 2 and "REFUSED" in out and f"'{key}'" in out


NON_FINITE = [float("nan"), float("inf"), -float("inf")]
ATTACK = ("properties", "Envelope attack")
ATTACK_READINGS = [
    ("unqualified-value", False, ATTACK + ("unqualified_value",),
     "diagnostics.properties[Envelope attack].unqualified_value", 9.96),
    ("unqualified-reference", False, ATTACK + ("unqualified_reference",),
     "diagnostics.properties[Envelope attack].unqualified_reference", 1.16),
    ("graded-value", True, ATTACK + ("value",),
     "diagnostics.properties[Envelope attack].value", 9.96),
    ("graded-reference", True, ATTACK + ("reference",),
     "diagnostics.properties[Envelope attack].reference", 1.16),
    ("event0-model", False, ("events", 0, "attack_10_90_ms", "model"),
     "diagnostics.events[0].attack_10_90_ms.model", 20.0),
    ("event2-reference", False, ("events", 2, "attack_10_90_ms", "reference"),
     "diagnostics.events[2].attack_10_90_ms.reference", 1.16),
]
ATTACK_IDS = [reading[0] for reading in ATTACK_READINGS]
NOT_ITS_WORST = "record attack reading is not its own worst event"


def _check_record_with(reading, value):
    """`check` on the committed inputs with ONE reading in the board record
    replaced. The committed record's attack is asserted unqualified, because
    which key the comparison reads depends on it."""
    _, graded, path, _, _ = reading
    record, candidate, baseline, manifest = _inputs()
    attack = record["diagnostics"]["properties"]["Envelope attack"]
    assert attack["valid"] is False and "value" not in attack, attack.keys()
    if graded:
        attack.update(valid=True, value=attack["unqualified_value"],
                      reference=attack["unqualified_reference"],
                      error=attack["unqualified_error"])
    target = record["diagnostics"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return sel.check(record, candidate, baseline, manifest)


def test_the_committed_record_is_not_refused_and_matches():
    """The control for everything below (#1994). The committed record is on
    the v3 path, where `abs(raw - worst) > 1e-5` is live, and it matches.

    That comparison is False against a NaN (#134), so a NaN reading passed
    "the record's attack reading is its own worst event" and the tool printed
    MATCH. The committed attack is UNQUALIFIED: the reading the comparison
    takes is `unqualified_value` / `unqualified_reference`, not `value` /
    `reference`. Both branches are driven below, each by the key it reads."""
    assert sel.check(*_inputs()) == []


@pytest.mark.parametrize("reading", ATTACK_READINGS, ids=ATTACK_IDS)
def test_the_comparison_reads_each_of_these_keys(reading):
    """The witness for the refusal below. A reading is (id, is the attack
    presented as graded, path from record["diagnostics"], the name the refusal
    must give it, a finite value this comparison reports as a mismatch): with
    that value in that key the comparison answers, so it reads the key. Green
    before and after the repair."""
    assert NOT_ITS_WORST in _check_record_with(reading, reading[4])


@pytest.mark.parametrize("bad", NON_FINITE, ids=["nan", "inf", "-inf"])
@pytest.mark.parametrize("reading", ATTACK_READINGS, ids=ATTACK_IDS)
def test_a_non_finite_attack_reading_in_the_record_is_refused_naming_the_field(reading, bad):
    """NaN, +inf and -inf in only the record operand: refused, naming the key
    the comparison reads."""
    with pytest.raises(sel.mf.Refused) as info:
        _check_record_with(reading, bad)
    assert str(info.value) == ("the M1A board record carries a value that is not a finite number: "
                               + reading[3] + " = " + repr(bad))


def test_an_absent_attack_reading_stays_the_mismatch_it_was():
    """`None` is "no reading", not a NaN: it is still reported, as before, as a
    record whose attack reading is not its own worst event -- a MISMATCH, not
    a refusal."""
    problems = _check_record_with(ATTACK_READINGS[0], None)
    assert NOT_ITS_WORST in problems


def test_main_refuses_a_record_on_disk_whose_attack_reading_is_nan(tmp_path, capsys):
    """Through the tool's own reader (`load_json_refused`): `json.dumps` writes
    a bare `NaN`, the loader reads it back, and the answer is REFUSED (2), not
    the MATCH the comparison used to print."""
    record, _, _, _ = _inputs()
    record["diagnostics"]["properties"]["Envelope attack"]["unqualified_value"] = float("nan")
    path = tmp_path / "M1A.json"
    path.write_text(json.dumps(record))
    assert "NaN" in path.read_text()
    assert sel.main(["--record", str(path)]) == 2
    out = capsys.readouterr().out
    assert "MISMATCH" not in out and "MATCH" not in out, out
    assert ("REFUSED: the M1A board record carries a value that is not a finite number: "
            "diagnostics.properties[Envelope attack].unqualified_value = nan") in out.splitlines(), out
