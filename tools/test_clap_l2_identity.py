"""Render identity across the L2 change, from two `drum_render_goldens.py`
records: `goldens-pre.json` (the tree before, commit named inside) and
`goldens-post.json` (this tree). No rendering here -- the renders ran on the
build box; this compares their hashes.

  * every sound except CP: output, both buses and EVERY envelope's state trace
    bit-identical, at accent 1 and 2 (MA included: its preset now writes
    FRATE = 0 explicitly, which must change nothing);
  * CP: the production kit reproduces the frozen L2 experiment render bit for
    bit at accents 0.5 / 1 / 2 and at every DEV and FRESH offset;
  * CP's other sixteen envelopes are untouched.
"""
import json
import pathlib

import pytest

D = pathlib.Path(__file__).resolve().parent.parent / "docs/scorecard/clap-l2"
PRE, POST = D / "goldens-pre.json", D / "goldens-post.json"
pytestmark = pytest.mark.skipif(not POST.exists(), reason="goldens-post.json not rendered yet")


def _load():
    return json.loads(PRE.read_text()), json.loads(POST.read_text())


def test_the_two_records_are_different_trees_and_clean():
    pre, post = _load()
    assert pre["head"] != post["head"]
    assert pre["drums_fx_sha256"] != post["drums_fx_sha256"]
    assert not [p for p in pre["dirty"] + post["dirty"] if "model/" in p or "rtl-sketch/" in p]


def test_every_other_sound_is_bit_identical():
    pre, post = _load()
    diff = [k for k in pre["solo"] if not k.startswith("CP@") and pre["solo"][k] != post["solo"][k]]
    assert not diff


def test_cp_other_envelopes_are_untouched():
    import sys
    sys.path.insert(0, str(D.parent.parent.parent / "model"))
    import drums_fx as dx
    pre, post = _load()
    for k in ("CP@1.0", "CP@2.0"):
        a, b = pre["solo"][k]["env_trace_sha256"], post["solo"][k]["env_trace_sha256"]
        changed = {i for i in range(len(a)) if a[i] != b[i]}
        assert changed == {dx.E_CPBURST, dx.E_CPTAIL}, (k, changed)


def test_production_cp_reproduces_the_frozen_l2_experiment():
    pre, post = _load()
    exp = pre["l2_experiment"]
    got = post["cp_production"]
    assert set(exp) == set(got) and len(got) == 18
    bad = [k for k in exp if (exp[k]["float64_sha256"], exp[k]["burst_env_trace_sha256"])
           != (got[k]["float64_sha256"], got[k]["burst_env_trace_sha256"])]
    assert not bad


def test_control_the_pre_change_cp_is_not_l2():
    """The identity above must be able to fail: the tree BEFORE the change,
    rendered through the same production path, is not the L2 render."""
    pre, _ = _load()
    assert pre["cp_production"]["L2@acc1.0@off0"]["float64_sha256"] != \
        pre["l2_experiment"]["L2@acc1.0@off0"]["float64_sha256"]
