"""Known answers and controls for tools/probes/chcp_559.py (#559).

Each instrument the probe reports through is shown to fail on the thing it
exists to catch: the reference pin on a different file, the tail fit on an
injected estimator defect, the gate on a start-red stub and on silence, the
fast path on a register it was not asserted for, the regression detector on an
injected bad candidate. Tests that need the Fischer corpus skip without it,
unless GF180_REQUIRE_TR808_REFS=1, where they fail (a required gate that goes
green through skips has checked nothing)."""
from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parents[1] / "model")]

import chcp_559 as c  # noqa: E402
import chcp_559_select as sel  # noqa: E402


def _refs():
    r = c.default_refs()
    try:
        c.check_refs(r)
        return r
    except c.Refused as e:
        if os.environ.get("GF180_REQUIRE_TR808_REFS") == "1":
            pytest.fail(f"REFUSED: {e}")
        pytest.skip(f"Fischer corpus unavailable: {e}")


# ---- the reference pin ------------------------------------------------------
def test_refs_refuse_missing(tmp_path):
    with pytest.raises(c.Refused, match="missing"):
        c.check_refs(tmp_path)


def test_refs_refuse_a_different_file(tmp_path):
    for rel in c.PINNED:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"RIFF not the take")
    with pytest.raises(c.Refused, match="different file"):
        c.check_refs(tmp_path)


# ---- frozen conditions -------------------------------------------------------
def test_conditions_are_frozen_and_disjoint():
    assert c.DEV_OFFSETS == (22630, 24803, 24929, 28047)
    assert c.CONFIRM_OFFSETS == (3875, 7876, 13171, 18039, 28311, 29687)
    dev = {h for h, _ in c.conditions("dev")}
    conf = {h for h, _ in c.conditions("confirm")}
    assert not dev & conf, "a CONFIRM strike was also used to select"


# ---- the tail estimator ------------------------------------------------------
def test_tail_fit_known_answer():
    ka = c.known_answer_tail()
    assert ka["within_10pct"], ka


def test_tail_fit_control_amplitude_for_energy(monkeypatch):
    """Injected estimator defect: reading 10 log10 of an AMPLITUDE (a 20 log
    quantity) halves every slope, so tau doubles. The known answer must go red."""
    real = np.log10
    monkeypatch.setattr(c.np, "log10", lambda v: real(np.sqrt(np.abs(v))))
    ka = c.known_answer_tail()
    assert not ka["within_10pct"], f"the control did not fire: {ka}"


# ---- the gate on CH and CP: start red, refuse silence -------------------------
@pytest.mark.parametrize("sound", ["CH", "CP"])
def test_gate_start_red_and_refuse_silence(sound):
    refs = _refs()
    g = c.gate(sound, *c.pg.stub_candidate(sound), refs)
    assert g["verdict"] == "FAIL"
    with pytest.raises(c.pg.Refused):
        c.gate(sound, np.zeros(48000), 48000, refs)
    with pytest.raises(c.Refused, match="non-finite"):
        y = np.zeros(48000)
        y[100] = np.nan
        c.gate(sound, y, 48000, refs)


@pytest.mark.parametrize("sound", ["CH", "CP"])
def test_gate_target_through_candidate_path_passes(sound):
    """Stays green: the take itself through the candidate path is within the bar."""
    refs = _refs()
    x, sr = c.pg.load_wav(refs / c.pg.target_rel(sound))
    assert c.gate(sound, *c.pg.candidate_path(x, sr), refs)["verdict"] == "PASS"


# ---- the CP fast path is the block --------------------------------------------
def test_cp_fast_path_is_the_block_on_swept_registers():
    dx = c._dx()
    kit = sel.cp_p_kit(250, -12.0)
    hit = c.BASE_HIT + 1234
    full = c.render_block("CP", kit, hit, 1.5, seconds=0.25)
    fast = c.cp_fast(kit, hit, 1.5, seconds=0.25)
    assert np.array_equal(full, fast)
    assert not np.array_equal(fast, c.cp_fast(dx.kit_with_sounds("CP"), hit, 1.5, seconds=0.25))


def test_cp_fast_path_control_unasserted_register():
    """The fast path models ONLY the envelopes. A kit that moves the clap's
    band-pass (a register it was never asserted for) must not match the block
    -- which is why it is only used for envelope candidates."""
    dx = c._dx()
    img = dict(dx.kit_with_sounds("CP"))
    for a, v in dx.mode_writes(dx.M_CPBP, 1500.0, 1.6, 0.0, dx.BP):
        img[a] = v
    kit = sorted(img.items())
    full = c.render_block("CP", kit, c.BASE_HIT, 1.0, seconds=0.1)
    assert not np.array_equal(full, c.cp_fast(kit, c.BASE_HIT, 1.0, seconds=0.1))


def test_cp_candidate_kit_leaves_ma_bit_identical():
    """CP-P moves only E_CPTAIL's registers, and MA's preset rewrites them, so
    MA must be bit-identical (CP/MA share circuit 6)."""
    dx = c._dx()
    shipped = c.render_block("MA", dx.kit_with_sounds("MA"), c.BASE_HIT, 1.0, seconds=0.15)
    cand_img = dx.kit_with_sounds("MA", kit=sel.cp_p_kit(250, -12.0, sound="CP"))
    assert np.array_equal(shipped, c.render_block("MA", cand_img, c.BASE_HIT, 1.0, seconds=0.15))


# ---- the regression detector ---------------------------------------------------
def test_regressions_control():
    base = [{"ratios": {"spec": 2.0, "attack": 0.9, "decay": 10.0}}]
    assert sel.regressions(base, base) == []
    worse = [{"ratios": {"spec": 2.3, "attack": 0.9, "decay": 5.0}}]
    assert sel.regressions(base, worse) and "spec" in sel.regressions(base, worse)[0]
    crossed = [{"ratios": {"spec": 2.0, "attack": 1.05, "decay": 5.0}}]
    assert "crossed" in sel.regressions(base, crossed)[0]


# ---- the confirm precondition (#595) -----------------------------------------
def _good_prov():
    return {"model_sha16": "m", "engine_fingerprint": "e", "probe_sha16": "p", "measure_sha16": "q",
            "commit": "c", "sources_dirty": False, "sources_moved_during_run": {}}


def test_sweep_provenance_accepts_a_matching_clean_sweep():
    c.check_sweep_provenance(_good_prov(), _good_prov())


@pytest.mark.parametrize("field,value", [
    ("sources_dirty", True),                              # dirty sweep, nominal commit would match
    ("sources_moved_during_run", {"model_sha16": ["a", "b"]}),
    ("engine_fingerprint", "other"),                      # changed imported engine dependency
    ("probe_sha16", "other"),                             # changed selection/measurement code
    ("model_sha16", "other"),
    ("measure_sha16", "other"),                           # changed gate/loader/metrics code
])
def test_sweep_provenance_refuses_each_defeating_input(field, value):
    sw = _good_prov()
    sw[field] = value
    with pytest.raises(c.Refused):
        c.check_sweep_provenance(sw, _good_prov())


@pytest.mark.parametrize("field", c.REQUIRED_FIELDS)
def test_sweep_provenance_refuses_absent_records(field):
    sw = _good_prov()
    del sw[field]
    with pytest.raises(c.Refused):
        c.check_sweep_provenance(sw, _good_prov())


# ---- provenance: what a record names must be what produced it (#595 review) ----
_SRC = ("model/drums_fx.py", "model/fixed.py", "model/modal_fixed.py", "tools/probes/chcp_559.py",
        "tools/probes/chcp_559_select.py", *c.MEASURE_FILES)


def _tree(tmp_path, monkeypatch):
    """A copy of every source the provenance hashes, with c.ROOT pointed at it,
    so a test can edit a source the way a mid-run edit would."""
    for rel in _SRC:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes((c.ROOT / rel).read_bytes())
    monkeypatch.setattr(c, "ROOT", tmp_path)
    return tmp_path


def _clean(prov: dict) -> dict:
    return {**prov, "commit": "f" * 40, "sources_dirty": False, "sources_moved_during_run": {},
            "provenance_ok": True}


def test_model_file_changed_mid_run_is_flagged_not_clean(tmp_path, monkeypatch):
    """THE defeating input of ch-confirm-hpq05.json: the model file changed
    while the run was going. Provenance taken at start and end must differ, the
    verdict must not be ok, and model_sha16 must be among what moved."""
    root = _tree(tmp_path, monkeypatch)
    p0 = c._provenance()
    with open(root / "model/drums_fx.py", "a") as fh:
        fh.write("\n# edited while a run was rendering\n")
    p1 = c._provenance()
    moved, ok = c.provenance_verdict(p0, p1)
    assert not ok and "model_sha16" in moved, moved
    assert c.provenance_verdict(p0, dict(p0)) == ({}, not p0["sources_dirty"])


def test_main_flags_record_and_exits_nonzero_when_sources_move(tmp_path, monkeypatch):
    """End to end through main(): the record is written, says provenance_ok
    false, carries the END provenance, and the exit is nonzero (the first
    version wrote end-state provenance with exit 0)."""
    p0 = {"model_sha16": "a" * 16, "engine_fingerprint": "e" * 16, "probe_sha16": "p" * 16,
          "measure_sha16": "m" * 16, "commit": "f" * 40, "sources_dirty": False}
    seq = iter([p0, {**p0, "model_sha16": "b" * 16}])
    monkeypatch.setattr(c, "_provenance", lambda: next(seq))
    monkeypatch.setattr(c, "check_refs", lambda refs: {})
    out = tmp_path / "r.json"
    assert c.main(["refs", "--out", str(out)]) == c.EXIT_PROVENANCE_MOVED
    rec = __import__("json").loads(out.read_text())
    assert rec["provenance_ok"] is False and rec["provenance_end"]["model_sha16"] == "b" * 16
    assert rec["sources_moved_during_run"] == {"model_sha16": ["a" * 16, "b" * 16]}
    assert c.record_provenance_problems(rec)


def test_main_unmoved_clean_run_is_ok():
    """Stays green: same provenance at start and end on a clean tree."""
    p0 = {"model_sha16": "a" * 16, "engine_fingerprint": "e" * 16, "probe_sha16": "p" * 16,
          "measure_sha16": "m" * 16, "commit": "f" * 40, "sources_dirty": False}
    assert c.provenance_verdict(p0, dict(p0)) == ({}, True)


def test_committed_dirty_sweep_is_refused_for_confirmation():
    """The real defeating input: the committed ch-sweep-dev.json (dirty tree,
    uncommitted model 730264ba, no engine fingerprint, no probe hash). The old
    guard let it through by reconstructing its engine from the nominal commit."""
    import json
    sw = json.loads((c.ROOT / "docs/scorecard/chcp-559/ch-sweep-dev.json").read_text())
    now = _clean(c._provenance())
    with pytest.raises(c.Refused, match="unreproducible"):
        c.check_sweep_compatible(sw, now)


def test_sweep_compatibility_controls():
    now = _clean(c._provenance())
    c.check_sweep_compatible(dict(now), now)          # stays green: identical clean provenance
    cases = {
        "dirty sweep": ({**now, "sources_dirty": True}, now, "dirty"),
        "moved sweep": ({**now, "sources_moved_during_run": {"model_sha16": ["a", "b"]}}, now, "moved"),
        "absent field": ({k: v for k, v in now.items() if k != "measure_sha16"}, now, "no measure_sha16"),
        "flagged sweep": ({**now, "provenance_ok": False}, now, "provenance_ok"),
        "dirty confirm tree": (now, {**now, "sources_dirty": True}, "clean tools"),
        "changed engine": ({**now, "engine_fingerprint": "0" * 16}, now, "engine_fingerprint"),
        "changed probe/selection": ({**now, "probe_sha16": "0" * 16}, now, "probe_sha16"),
        "changed measurement": ({**now, "measure_sha16": "0" * 16}, now, "measure_sha16"),
        "changed model file": ({**now, "model_sha16": "0" * 16}, now, "model_sha16"),
    }
    for label, (sw, cur, why) in cases.items():
        with pytest.raises(c.Refused, match=why):
            c.check_sweep_compatible(sw, cur)
        assert label


def test_engine_dependency_and_measurement_edits_move_provenance(tmp_path, monkeypatch):
    """An edit to an imported engine dependency (fixed.py) moves the engine
    fingerprint, and an edit to the gate (perceptual_gate.py) moves
    measure_sha16, so check_sweep_compatible sees both."""
    root = _tree(tmp_path, monkeypatch)
    p0 = c._provenance()
    with open(root / "model/fixed.py", "a") as fh:
        fh.write("\n# changed dependency\n")
    p1 = c._provenance()
    assert p1["engine_fingerprint"] != p0["engine_fingerprint"]
    with open(root / "tools/perceptual_gate.py", "a") as fh:
        fh.write("\n# changed measurement\n")
    p2 = c._provenance()
    assert p2["measure_sha16"] != p1["measure_sha16"]
    with pytest.raises(c.Refused, match="engine_fingerprint"):
        c.check_sweep_compatible(_clean(p0), _clean(p1))
    with pytest.raises(c.Refused, match="measure_sha16"):
        c.check_sweep_compatible(_clean(p1), _clean(p2))


def test_confirm_refuses_before_rendering(monkeypatch):
    """The refusal happens before sel.confirm renders anything."""
    called = []
    monkeypatch.setattr(sel, "confirm", lambda *a, **k: called.append(a) or {})
    monkeypatch.setattr(c, "check_refs", lambda refs: {})
    monkeypatch.setattr(c, "_provenance", lambda: _clean({"model_sha16": "a" * 16, "engine_fingerprint": "e" * 16,
                                                         "probe_sha16": "p" * 16, "measure_sha16": "m" * 16}))
    rc = c.main(["confirm", "--sound", "CH", "--sweep", str(c.ROOT / "docs/scorecard/chcp-559/ch-sweep-dev.json"),
                 "--cand", '{"hpq": 0.5, "bpq": 6.0}'])
    assert rc == 2 and not called


def test_every_unreproducible_record_is_labelled_in_the_sidecar():
    """A record whose own fields show it cannot be reproduced must be listed in
    provenance-status.json as not clean, so no reader takes it for clean
    evidence. Not defeated by: a record whose fields lie about the tree it came
    from -- no check here can see that; the build-box rerun from a clean clone
    is what removes it."""
    import json
    side = json.loads((c.ROOT / c.RECORD_DIR / c.STATUS_SIDECAR).read_text())["records"]
    au = c.audit()
    for name, probs in au["records"].items():
        if probs:
            assert name in side and side[name]["status"] != "clean", f"{name} is unreproducible but unlabelled"
