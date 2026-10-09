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
