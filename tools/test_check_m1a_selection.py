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


# ---- a number that is not finite in a numeric leaf of the PRE-SELECTION
# BASELINE is REFUSED, naming the record and the leaf (#2122). The
# preservation policy reads that record through `_passes` and
# `volume.passed_partials`, both `abs(x) <= tol`, and asks only what the
# candidate LOST: `passes(baseline) - passes(candidate)`. A NaN on the
# baseline side is "not passed" there, so it is in neither set and cannot be
# lost: one unreadable leaf made the check strictly weaker, and a candidate
# that had lost that pass printed MATCH (exit 0). Observed red first against
# the unmodified reader at 9384985e: 267 of 267 numeric leaves (243 events,
# 24 properties) were not refused.
BASELINE_H8 = ("events", 0, "harmonic_error_db_model_minus_reference", "h8")
BASELINE_PITCH_ERROR = ("properties", "Pitch", "error")
BASELINE_UNREADABLE = ("the pre-selection baseline record carries a value that is "
                       "not a finite number: ")


def _spelled(path):
    """The test's own spelling of a leaf path below `measurements`:
    `events[0].harmonic_error_db_model_minus_reference.h8`."""
    return "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path).lstrip(".")


def _put(root, path, value):
    for p in path[:-1]:
        root = root[p]
    root[path[-1]] = value


def _number_leaves(node, path=()):
    """Path of every numeric leaf of a JSON tree; a bool and null are not one."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        yield path
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _number_leaves(v, path + (k,))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _number_leaves(v, path + (i,))


def _main_over_all(monkeypatch, tmp_path, capsys, record, candidate, baseline):
    """The tool's own main() over three records written by `json.dumps`, which
    spells a NaN `NaN` and an infinity `Infinity` and reads both back."""
    paths = {}
    for name, obj in (("record", record), ("candidate", candidate), ("baseline", baseline)):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(obj))
    monkeypatch.setattr(sel, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(sel, "BASELINE", paths["baseline"])
    code = sel.main(["--record", str(paths["record"])])
    return code, capsys.readouterr().out


def _lose_the_h8_partial(record, candidate):
    """Record and candidate agree on a partial beyond the 1 dB tolerance: the
    candidate no longer holds the pass the committed baseline holds."""
    for measured in (record["diagnostics"], candidate["measurements"]):
        _put(measured, BASELINE_H8, 1.5)


def _lose_the_pitch_pass(record, candidate):
    """Record and candidate agree on a Pitch error beyond its tolerance."""
    for measured in (record["diagnostics"], candidate["measurements"]):
        _put(measured, BASELINE_PITCH_ERROR, 2.0)


LOST_PASSES = [
    pytest.param(_lose_the_h8_partial, BASELINE_H8,
                 "preservation: per-note harmonic pass lost: [(0, 'h8')]", id="events-h8"),
    pytest.param(_lose_the_pitch_pass, BASELINE_PITCH_ERROR,
                 "preservation: property pass lost: Pitch", id="properties-Pitch-error"),
]
UNREADABLE = [pytest.param(float("nan"), id="nan"), pytest.param(float("inf"), id="inf"),
              pytest.param(float("-inf"), id="neg-inf")]


@pytest.mark.parametrize("value", UNREADABLE)
@pytest.mark.parametrize("lose, path, clean", LOST_PASSES)
def test_a_lost_pass_hidden_by_an_unreadable_baseline_leaf_is_refused(
        monkeypatch, tmp_path, capsys, lose, path, clean, value):
    """The issue's two rows: the candidate lost a pass, and the ONE baseline
    leaf that held it is made unreadable."""
    record, candidate, baseline, manifest = _inputs()
    lose(record, candidate)
    assert sel.check(record, candidate, baseline, manifest) == [clean]
    _put(baseline["measurements"], path, value)
    leaf = f"measurements.{_spelled(path)} = {value!r}"
    with pytest.raises(sel.mf.Refused) as refused:
        sel.check(record, candidate, baseline, manifest)
    assert str(refused.value) == BASELINE_UNREADABLE + leaf
    code, out = _main_over_all(monkeypatch, tmp_path, capsys, record, candidate, baseline)
    assert code == 2 and out == f"REFUSED: {BASELINE_UNREADABLE}{leaf}\n"


def test_every_numeric_leaf_of_the_baseline_measurements_is_refused_when_unreadable():
    """One NaN at a time in each numeric leaf of the committed baseline's
    `measurements`: the leaves the policy reads AND the ones it does not."""
    record, candidate, baseline, manifest = _inputs()
    # a sweep is evidence only if the undamaged baseline is not refused
    assert sel.check(record, candidate, baseline, manifest) == []
    probed = list(_number_leaves(baseline["measurements"]))
    not_refused = []
    for path in probed:
        damaged = copy.deepcopy(baseline)
        _put(damaged["measurements"], path, float("nan"))
        expected = f"measurements.{_spelled(path)} = nan"
        try:
            sel.check(record, candidate, damaged, manifest)
        except sel.mf.Refused as exc:
            if BASELINE_UNREADABLE + expected not in str(exc):
                not_refused.append(f"{expected} was refused without being named: {exc}")
        else:
            not_refused.append(expected)
    # the sweep walked both parts and reached the two leaves the policy reads
    assert BASELINE_H8 in probed and BASELINE_PITCH_ERROR in probed
    assert not not_refused, (f"{len(not_refused)} of {len(probed)} numeric leaves were not "
                             f"refused: {not_refused[:8]}")


def test_a_baseline_leaf_that_was_never_measured_is_not_refused():
    """`None` is the records' own spelling of "not measured", which
    `passed_partials` already reads so: the guard refuses a number nobody can
    read, not an absent reading."""
    record, candidate, baseline, manifest = _inputs()
    _put(baseline["measurements"], BASELINE_H8, None)
    assert sel.check(record, candidate, baseline, manifest) == []


def test_an_unreadable_baseline_is_refused_before_the_policy_reads_it(monkeypatch):
    """"Before", not merely "somewhere in `check`": with both readers of the
    baseline replaced by one that fails, the refusal is still the outcome."""
    def reader_must_not_run(measured):
        raise AssertionError("the preservation policy read the unreadable baseline")

    monkeypatch.setattr(sel, "_passes", reader_must_not_run)
    monkeypatch.setattr(sel.volume, "passed_partials", reader_must_not_run)
    record, candidate, baseline, manifest = _inputs()
    _put(baseline["measurements"], BASELINE_H8, float("nan"))
    with pytest.raises(sel.mf.Refused) as refused:
        sel.check(record, candidate, baseline, manifest)
    assert str(refused.value) == (BASELINE_UNREADABLE
                                  + f"measurements.{_spelled(BASELINE_H8)} = nan")


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
