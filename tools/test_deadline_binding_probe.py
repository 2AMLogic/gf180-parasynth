"""The simulator-free parts of tools/deadline_binding_probe.py: its perturbations
land where they say, and its verdict table has no false green (#443)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import deadline_binding_probe as p  # noqa: E402

vd = p.vd


def _cmds():
    return vd.SPI_SCENARIOS["stress-saw"](False)[0]


def test_each_probe_changes_what_it_claims_and_the_binding_predicts_it():
    cur = _cmds()
    rows = p.base_rows()
    rec = json.load(open(os.path.join(p.ROOT, "docs", "deadline", "runs", p.RECORD + ".json")))
    want = {"drum-426": "drum-values", "drum-all-params": "drum-values", "drum-env-ctl": "drum-values",
            "voice-inc": "refused"}
    assert set(want) == set(p.PROBES)
    for name in p.PROBES:
        pert, expect = p.perturb(cur, name)
        changed = [(a, b) for a, b in zip(cur, pert) if a != b]
        assert changed and len(pert) == len(cur), name
        drum_only = all(a[2] == vd.SEC_D and a[3] != vd.dx.A_STOPS for a, _ in changed)
        assert drum_only == (name != "voice-inc") and expect == (name == "voice-inc"), name
        assert vd.stimulus_binding(cur, pert, sched_rows=rows, record=rec)[0] == want[name], name
    assert len([1 for a, b in zip(cur, p.perturb(cur, "drum-426")[0]) if a != b]) == 2


def test_the_verdict_table_has_no_false_green():
    base = [{"frame": 0, "strobe": 200}]
    moved = [{"frame": 0, "strobe": 201}]
    assert p.classify("drum-values", base, base, False)[0] == 0
    assert p.classify("drum-values", base, moved, False)[0] == 1          # false admission
    assert p.classify("identical", base, moved, False)[0] == 1
    assert p.classify("refused", base, moved, True)[0] == 0
    assert p.classify("refused", base, base, False)[0] == 0               # conservative
    assert p.classify("refused", base, base, True)[0] == 2                # a control that did not bite
    assert p.classify("drum-values", base, [], False)[0] == 2             # no trace, no verdict
