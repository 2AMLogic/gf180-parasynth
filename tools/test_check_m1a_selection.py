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


# ---- a NaN in any numeric leaf of either events list is REFUSED, naming the
# record and the leaf (#2107). `event_deviation` feeds `abs(a - b)` of every
# numeric leaf to `max(parts)`, which drops a NaN that is not the first part
# and returns one that is, and `nan > EVENT_ATOL` is False either way: the
# cross-check answered MATCH (exit 0) for a record carrying a reading nobody
# can read. The fields `candidate_event_deviation` strips before comparing
# (`attack_10_90_ms`, `attack_fit`) were not read by it at all. Observed red
# first against the unmodified reader at 8d4adf6e.
NAN, INF = float("nan"), float("inf")
H8 = (1, "harmonic_error_db_model_minus_reference", "h8")
NOT_A_FINITE_NUMBER = "carries a value that is not a finite number: "
RECORDS = {"record": ("the M1A board record", "diagnostics.events"),
           "candidate": ("the -4 dB candidate record", "measurements.events")}


def _events(which, record, candidate):
    return record["diagnostics"]["events"] if which == "record" \
        else candidate["measurements"]["events"]


def _leaf(where, path):
    return where + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in path)


def _set_leaf(root, path, value):
    for p in path[:-1]:
        root = root[p]
    root[path[-1]] = value


def _numeric_leaves(node, path=()):
    """`(path, value)` of every numeric leaf of a JSON tree; a bool is not one."""
    if isinstance(node, bool) or node is None:
        return
    if isinstance(node, (int, float)):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _numeric_leaves(v, path + (k,))
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _numeric_leaves(v, path + (i,))


def _main_with(monkeypatch, tmp_path, capsys, record, candidate):
    """The tool's own main() over a record and candidate written by `json.dumps`,
    which spells a NaN `NaN` and an infinity `Infinity` and reads both back."""
    (tmp_path / "record.json").write_text(json.dumps(record))
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps(candidate))
    monkeypatch.setattr(sel, "CANDIDATE", candidate_path)
    code = sel.main(["--record", str(tmp_path / "record.json")])
    return code, capsys.readouterr().out


def test_a_nan_in_every_numeric_leaf_of_either_events_list_is_refused():
    """One NaN at a time in each numeric leaf of both events lists, through
    `check`: the leaves the comparison reads AND the ones it strips."""
    record, candidate, baseline, manifest = _inputs()
    not_refused, probed = [], {}
    for which, (what, _) in RECORDS.items():
        probed[which] = [path for path, _ in _numeric_leaves(_events(which, record, candidate))]
        for path in probed[which]:
            damaged = copy.deepcopy(record), copy.deepcopy(candidate)
            _set_leaf(_events(which, *damaged), path, NAN)
            try:
                sel.check(*damaged, baseline, manifest)
            except sel.mf.Refused as exc:
                assert what in str(exc)
            else:
                not_refused.append(f"{which} {_leaf('', path)}")
    # the sweep walked both lists; an empty one would pass above
    assert H8 in probed["record"] and H8 in probed["candidate"]
    assert not not_refused, f"{len(not_refused)} leaves were not refused: {not_refused[:8]}"


NON_FINITE_LEAVES = [
    pytest.param("record", H8, NAN, id="record-h8-not-first"),
    pytest.param("record", (0,) + H8[1:], NAN, id="record-event0-h8"),
    pytest.param("record", (2, "release_t20_ms", "model"), NAN, id="record-release-model"),
    pytest.param("candidate", H8, NAN, id="candidate-h8"),
    pytest.param("record", (1, "attack_fit", "model", "ramp_ms"), NAN, id="record-attack-fit"),
    pytest.param("candidate", (1, "attack_10_90_ms", "model"), NAN, id="candidate-attack"),
    pytest.param("record", H8, INF, id="record-h8-inf"),
    pytest.param("record", H8, -INF, id="record-h8-neg-inf"),
]


@pytest.mark.parametrize("which, path, value", NON_FINITE_LEAVES)
def test_a_non_finite_event_leaf_is_refused_naming_the_record_and_the_leaf(
        monkeypatch, tmp_path, capsys, which, path, value):
    record, candidate, baseline, manifest = _inputs()
    _set_leaf(_events(which, record, candidate), path, value)
    what, where = RECORDS[which]
    leaf = f"{_leaf(where, path)} = {value!r}"
    with pytest.raises(sel.mf.Refused) as refused:
        sel.check(record, candidate, baseline, manifest)
    assert what in str(refused.value) and leaf in str(refused.value)
    code, out = _main_with(monkeypatch, tmp_path, capsys, record, candidate)
    assert code == 2 and out.startswith("REFUSED:") and what in out and leaf in out


def test_the_same_infinity_on_both_sides_is_refused(monkeypatch, tmp_path, capsys):
    """`abs(inf - inf)` is nan: the leaves are equal and neither is a reading."""
    record, candidate, baseline, manifest = _inputs()
    for events in (_events("record", record, candidate), _events("candidate", record, candidate)):
        _set_leaf(events, H8, INF)
    with pytest.raises(sel.mf.Refused) as refused:
        sel.check(record, candidate, baseline, manifest)
    assert (f"the M1A board record {NOT_A_FINITE_NUMBER}"
            f"{_leaf('diagnostics.events', H8)} = inf") in str(refused.value)
    code, out = _main_with(monkeypatch, tmp_path, capsys, record, candidate)
    assert code == 2 and out.startswith("REFUSED:")


@pytest.mark.parametrize("a, b, leaf", [
    pytest.param([NAN, 1.], [1., 1.], "[0] = nan", id="first-max-returns-the-nan"),
    pytest.param([1., NAN], [1., 1.], "[1] = nan", id="not-first-max-drops-the-nan"),
    pytest.param({"events": [{"h8": 1.}, {"h8": NAN}]},
                 {"events": [{"h8": 1.}, {"h8": 1.}]}, "events[1].h8 = nan", id="nested"),
    pytest.param(INF, INF, "the tree = inf", id="both-inf"),
    pytest.param(1., -INF, "the tree = -inf", id="on-the-right"),
])
def test_event_deviation_refuses_a_leaf_that_is_not_finite(a, b, leaf):
    with pytest.raises(sel.mf.Refused) as refused:
        sel.event_deviation(a, b)
    assert f"{leaf} is not a finite number" in str(refused.value)


def test_finite_leaves_are_compared_however_far_apart():
    """Green before and after the repair: the boundary of the guard above. It
    reads the operands, not their difference -- two finite readings 2e308 apart
    overflow to inf, which is a MISMATCH where `check` reads it, never a
    refusal -- and a NaN spelled as text is a structure difference, never a match."""
    assert sel.event_deviation(1e308, -1e308) == INF
    assert sel.event_deviation({"a": "nan"}, {"a": 1.}) is None
