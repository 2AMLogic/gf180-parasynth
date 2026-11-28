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
_MISSING = object()


def _main_over(monkeypatch, tmp_path, capsys, which, key, value=_MISSING):
    """`main()` over the committed records with ONE key of ONE record deleted
    (#1604) or replaced by `value` (#1679)."""
    record, candidate, baseline, _ = _inputs()
    records = {"record": record, "candidate": candidate, "baseline": baseline}
    if value is _MISSING:
        del records[which][key]
    else:
        records[which][key] = value
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


# ---- a key that is PRESENT but of a type the check dereferences is REFUSED
# the same way (#1679). Measured on the tree before the repair, one control
# per key and wrong type: each of these was a TypeError out of main(), exit 1.
@pytest.mark.parametrize("which, key, value", [
    ("record", "diagnostics", 5), ("record", "diagnostics", []),
    ("record", "audio", 5),
    ("record", "metrics", "x"),
    ("candidate", "sha256", 5),
    ("candidate", "measurements", []), ("candidate", "measurements", True),
    ("candidate", "metrics", 5),
    ("baseline", "measurements", 5),
])
def test_a_record_key_of_a_type_the_check_dereferences_is_refused(monkeypatch, tmp_path, capsys,
                                                                  which, key, value):
    code, out = _main_over(monkeypatch, tmp_path, capsys, which, key, value)
    assert code == 2 and "REFUSED" in out and f"'{key}'" in out
    assert "MISMATCH" not in out


@pytest.mark.parametrize("value", [5, "x", [], True])
def test_a_candidate_patch_of_the_wrong_type_is_a_mismatch_not_a_refusal(monkeypatch, tmp_path,
                                                                         capsys, value):
    # measured, and left alone on purpose: `patch` is only COMPARED (through
    # `_norm`), never dereferenced, so every wrong type is a clean exit 1 that
    # names it. The check's question -- is this the selected candidate? -- is
    # answered, not avoided, so `patch` declares no kind.
    code, out = _main_over(monkeypatch, tmp_path, capsys, "candidate", "patch", value)
    assert code == 1 and "MISMATCH" in out and "patch differs" in out
    assert "REFUSED" not in out
