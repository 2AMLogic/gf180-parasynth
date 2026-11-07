#!/usr/bin/env python3
"""Controls for tools/probes/modal_deadband.py (#350).

    python3 -m pytest tools/probes/test_modal_deadband.py -q

Every guard ships with the input that defeats it; every mutant must fail the
INTENDED assertion (KnownAnswerMismatch / Refused), not crash. The one-mode
known-answer tests are sub-second; the two production-path tests render 0.3 s
of one voice.
"""
from __future__ import annotations

import json
import math
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import modal_deadband as md          # noqa: E402
import drums_fx as dx                # noqa: E402
import modal_fixed as mf             # noqa: E402


# ---- helpers: a synthetic "render" around TwinBank so mode_row can be driven ----
def synth(a1, a2, ping, n=96000, amp=65535, nums_kw=None, drive=None, coefs_at=None):
    b = md.TwinBank(n, modes=1, nums=1, headroom=0, out_bits=28)
    for t in range(n):
        e = 0
        if drive is not None:
            e = drive(t)
        elif t == 10:
            e = ping
        co = coefs_at(t) if coefs_at else (a1, a2, amp)
        b.step([e], [co], [mf.RAW])
    return dict(n=n, bank=b)


POLE = mf.pole_regs(90.0, 25.0)


# ---- 1. known answer -------------------------------------------------------
def test_known_answer_reproduces_committed_table():
    t = md.deadband_table()
    assert md.check_known_answer(t) == [f"ping {k}" for k in md.KA_PINGS]


@pytest.mark.parametrize("name", sorted(md.DEFECTS))
def test_each_mutant_fails_the_intended_assertion(name):
    t = md.deadband_table(levels=(100, 10000), n=48000, fixed_fn=md.DEFECTS[name])
    with pytest.raises(md.KnownAnswerMismatch):
        md.check_known_answer(t, md._committed_subset())


def test_tolerance_is_not_vacuous():
    """A table moved by exactly 2 blocks, or 2 LSB of tail, must fail."""
    t = md.deadband_table(levels=(100, 10000), n=48000)
    c = md._committed_subset()
    bad = json.loads(json.dumps(c))
    bad["100"]["departs_at_ms"] += 20
    with pytest.raises(md.KnownAnswerMismatch):
        md.check_known_answer(t, bad)
    bad = json.loads(json.dumps(c))
    bad["10000"]["fixed_tail_peak_lsb"] += 2
    with pytest.raises(md.KnownAnswerMismatch):
        md.check_known_answer(t, bad)
    md.check_known_answer(t, c)          # and the clean one passes


@pytest.mark.parametrize("f0,q", [(90.0, 25.0), (56.0, 22.3), (2000.0, 10.0)])
def test_float_shadow_matches_closed_form_at_several_poles(f0, q):
    a1, a2 = mf.pole_regs(f0, q)
    exc = np.zeros(6000)
    exc[0] = 1.0
    got = md.float_recursion(exc, a1, a2) * 65536 / 65535
    want = md.analytic_impulse(a1, a2, 6000)
    assert np.max(np.abs(got - want)) <= 1e-9 * np.max(np.abs(want))


@pytest.mark.parametrize("f0,q", [(90.0, 25.0), (56.0, 22.3)])
def test_fixed_matches_closed_form_while_far_above_the_floor(f0, q):
    a1, a2 = mf.pole_regs(f0, q)
    exc = np.zeros(600, dtype=np.int64)
    exc[0] = 100_000
    y = md.fixed_recursion(exc, a1, a2)
    want = md.analytic_impulse(a1, a2, 600, amp=100_000 * 65535 / 65536)
    assert np.max(np.abs(y - want)) < 2e-3 * np.max(np.abs(want)) + 50


def test_silence_is_exact_zero_and_not_driven():
    r = synth(*POLE, 0, n=4800)
    assert np.all(r["bank"].rec_y == 0)
    assert md.mode_row(r, 0, "X", 1.0, [])["status"] == "NOT-DRIVEN"


def test_polarity_is_not_symmetric_under_floor_and_is_reported_signed():
    """floor() is not odd: +ping and -ping must NOT be mirror images. The tail
    is reported with its sign so a reader can see the pedestal's direction."""
    a1, a2 = POLE
    n = 96000
    pos = np.zeros(n, dtype=np.int64); pos[10] = 100000
    neg = -pos
    yp, yn = md.fixed_recursion(pos, a1, a2), md.fixed_recursion(neg, a1, a2)
    assert not np.array_equal(yp, -yn)
    tp, tn = md.tail_shape(yp.astype(np.int64)), md.tail_shape(yn.astype(np.int64))
    assert tp["kind"] == tn["kind"] == "stuck"
    # floor rounds toward -inf: whichever polarity pings, the stuck value is on
    # the negative side or exactly zero (measured: +100000 -> -7201, -100000 -> 0)
    assert tp["value"] == -7201 and tn["value"] == 0 and tp["value"] != -tn["value"]
    assert md.tail_shape(md.fixed_recursion(-np.roll(pos, 0) // 1000, a1, a2).astype(np.int64))["value"] <= 0


def test_tail_shape_distinguishes_stuck_cycle_aperiodic():
    assert md.tail_shape(np.full(5000, -7))["kind"] == "stuck"
    cyc = np.tile([1, 2, 3], 2000)
    assert md.tail_shape(cyc) == dict(kind="limit-cycle", period=3, p2p=2)
    rng = np.random.default_rng(1)
    assert md.tail_shape(rng.integers(-5, 5, 5000))["kind"] == "aperiodic"


# ---- 2. mode_row guards, each with its defeating input -----------------------
def test_mode_row_measures_a_clean_decaying_ping():
    r = synth(*POLE, 100000)
    row = md.mode_row(r, 0, "X", 1.0, ["body-bus"])
    assert row["status"] == "MEASURED"
    assert row["tail"]["kind"] == "stuck" and row["tail"]["value"] == -7201
    assert row["departure_ms"] == 640
    assert row["int16_residual_peak_lsb"] == pytest.approx(7201 * md.GAIN_REG / 32768 * 65535 / 65536, rel=0.01)


def test_mode_row_refuses_saturation():
    r = synth(*POLE, 1 << 28)                 # beyond the 28-bit rail
    assert md.mode_row(r, 0, "X", 1.0, [])["status"] == "REFUSED"


def test_mode_row_no_tail_below_one_lsb():
    r = synth(*POLE, 1, n=96000)              # ping 1 LSB at Q0 -> state 1 << 0
    row = md.mode_row(r, 0, "X", 1.0, [])
    # the float shadow is 1 LSB or so: either NO-TAIL or an honest measurement,
    # never a silent drop
    assert row["status"] in ("NO-TAIL", "MEASURED")
    r2 = synth(*POLE, 0, n=96000, drive=lambda t: 1 if t == 10 else 0, amp=65535)
    assert md.mode_row(r2, 0, "X", 1.0, [])["status"] in ("NO-TAIL", "MEASURED")


def test_mode_row_refuses_a_mode_driven_to_the_end():
    r = synth(*POLE, 0, n=48000, drive=lambda t: 5 if t % 7 == 0 else 0)
    assert md.mode_row(r, 0, "X", 1.0, [])["status"] == "REFUSED"


def test_mode_row_waits_for_the_last_retune():
    """A coefficient change near the end must move the tail start and, if too
    late, refuse; the departure is never read across a retune."""
    a1, a2 = POLE
    late = lambda t: (a1, a2 + (1 if t > 95500 else 0), 65535)
    r = synth(a1, a2, 100000, coefs_at=late)
    assert md.mode_row(r, 0, "X", 1.0, [])["status"] == "REFUSED"


def test_tap_only_mode_refuses_bus_but_keeps_state():
    r = synth(*POLE, 100000, amp=0)
    row = md.mode_row(r, 0, "X", 1.0, ["tap->mix"])
    assert row["status"] == "MEASURED" and row["bus_residual_peak_lsb"] is None
    assert row["bus"].startswith("REFUSED") and row["state_residual_peak_lsb"] > 1000


def test_nonfinite_shadow_is_refused():
    r = synth(*POLE, 100000, n=2000)
    r["bank"].rec_f[5, 0] = float("nan")
    assert md.mode_row(r, 0, "X", 1.0, [])["status"] == "REFUSED"


# ---- 3. the twin is the shipped bank ----------------------------------------
def test_twin_is_bit_identical_to_the_stock_bank_on_random_drive():
    rng = np.random.default_rng(3)
    n = 3000
    kw = dict(modes=3, nums=2, headroom=0, out_bits=19)
    twin, stock = md.TwinBank(n, **kw), mf.ModalFx(**kw)
    co = [mf.pole_regs(90.0, 25.0) + (60000,), mf.pole_regs(300.0, 8.0) + (40000,), mf.pole_regs(56.0, 22.0) + (5000,)]
    num = [mf.BP, mf.HP, mf.RAW]
    for t in range(n):
        e = [int(v) for v in rng.integers(-2000, 2000, 3)]
        assert twin.step(e, co, num) == stock.step(e, co, num)
        if t == 1500:
            co[0] = (co[0][0], co[0][1] + 3, co[0][2])


def test_production_render_with_twin_equals_stock_render():
    r = md.render_twin("LT", 1.0, seconds=0.3)
    assert md.bank_is_stock("LT", 1.0, r)


def test_stock_check_is_not_vacuous():
    r = md.render_twin("LT", 1.0, seconds=0.3)
    r["out"] = r["out"].copy(); r["out"][12345] += 1
    assert not md.bank_is_stock("LT", 1.0, r)


def test_config_guard_refuses_every_wrong_bank():
    d = dx.DrumsFx()
    md.assert_production_config(d, md.TwinBank(2, **md.PROD_KW))
    for name, kw in md.CONFIG_DEFECTS.items():
        with pytest.raises(md.Refused):
            md.assert_production_config(d, md.TwinBank(2, **kw))
    d2 = dx.DrumsFx(couple=dx.COUPLE_BUS)
    with pytest.raises(md.Refused):
        md.assert_production_config(d2, md.TwinBank(2, **md.PROD_KW))


# ---- 3b. the shadow that PRODUCES the tables is the one checked --------------
def test_twin_shadow_matches_closed_form_at_several_poles():
    """TwinBank's own rec_f (what the production tables read), driven by an
    impulse, equals the analytic answer. Not float_recursion: that is a second
    implementation the tables never touch."""
    md.guard_twin_shadow_independent()


@pytest.mark.parametrize("name", sorted(md.SHADOW_DEFECTS))
def test_shadow_only_mutant_is_refused_while_stock_integer_equality_stays_green(name):
    """The defect is confined to the float shadow: the integer return value is
    still bit-identical to the stock bank (so the stock-equivalence test is
    blind to it), and the shadow guard must refuse."""
    cls = md.SHADOW_DEFECTS[name]
    rng = np.random.default_rng(5)
    kw = dict(modes=2, nums=1, headroom=0, out_bits=19)
    twin, stock = cls(2000, **kw), mf.ModalFx(**kw)
    co = [mf.pole_regs(90.0, 25.0) + (60000,), mf.pole_regs(300.0, 8.0) + (40000,)]
    for _ in range(2000):
        e = [int(v) for v in rng.integers(-2000, 2000, 2)]
        assert twin.step(e, co, [mf.RAW]) == stock.step(e, co, [mf.RAW])
    assert not np.allclose(twin.rec_f, md.TwinBank(2000, **kw).rec_f)        # the mutant is ACTIVE
    with pytest.raises(md.Refused):
        md.guard_twin_shadow_independent(cls)


def test_twin_independence_guard_refuses_a_twin_that_is_the_fixed_path():
    md.guard_twin_not_fixed(md.deadband_table(levels=(100,), n=48000))
    with pytest.raises(md.Refused):
        md.guard_twin_not_fixed(md.deadband_table(levels=(100,), n=48000, fixed_fn=md._mut_float_stub))


def test_controls_matrix_has_no_blind_where_it_must_move():
    res = md.controls_matrix()
    assert md.controls_failures(res) == []


# ---- 4. population / counts --------------------------------------------------
def test_mode_reach_covers_every_mode_of_every_sound_and_finds_the_bodies():
    for s in dx.SOUND_NAMES:
        reach = md.mode_reach(s)
        assert sorted(reach) == list(range(dx.N_MODES))
    assert md.mode_reach("BD")[dx.M_BD] == ["body-bus"]
    assert md.mode_reach("SD")[dx.M_SDLO] == ["body-bus"] and md.mode_reach("SD")[dx.M_SDHI] == ["body-bus"]
    for s, m in (("LT", dx.M_LT), ("LC", dx.M_LT), ("MT", dx.M_MT), ("MC", dx.M_MT), ("HT", dx.M_HT), ("HC", dx.M_HT)):
        assert md.mode_reach(s)[m] == ["body-bus"]
    assert any(r.startswith("tap") for r in md.mode_reach("RS")[dx.M_RS1])


def test_reconcile_adds_up_and_catches_a_dropped_row():
    row = lambda st: dict(status=st)
    t = dict(rows=[row("MEASURED")] * dx.N_MODES)
    assert md.reconcile([t])["cells"] == dx.N_MODES
    with pytest.raises(AssertionError):
        md.reconcile([dict(rows=t["rows"][:-1])])


def test_conditions_are_disjoint_and_confirmation_is_untouched_by_dev():
    dev, conf = set(map(tuple, md.CONDITIONS["dev"])), set(map(tuple, md.CONDITIONS["confirm"]))
    assert not dev & conf
    assert {a for _, a in dev}.isdisjoint({a for s, a in conf if s in md.SOUNDS_DEV})
    assert {s for s, _ in conf} - {s for s, _ in dev} == set(md.SOUNDS_CONF_NEW)


# ---- 5. the freeze -----------------------------------------------------------
def test_confirmation_refuses_without_a_committed_matching_freeze(tmp_path, monkeypatch):
    with pytest.raises(md.Refused):
        md.check_freeze(tmp_path / "freeze.json")                       # absent
    p = tmp_path / "freeze.json"
    spec = md.write_freeze(p)
    md.check_freeze(p, committed_check=False)                           # self-consistent
    with pytest.raises(md.Refused):
        md.check_freeze(p)                                              # not committed (outside the repo's index)
    d = json.loads(p.read_text()); d["thresholds"]["DEPART_DB"] = 6.0
    p.write_text(json.dumps(d))
    with pytest.raises(md.Refused):
        md.check_freeze(p, committed_check=False)                       # edited after writing
    md.write_freeze(p)
    monkeypatch.setattr(md, "DEPART_DB", 6.0)
    with pytest.raises(md.Refused):
        md.check_freeze(p, committed_check=False)                       # threshold moved after the freeze
    monkeypatch.setattr(md, "DEPART_DB", 3.0)
    monkeypatch.setitem(md.CONDITIONS, "confirm", md.CONDITIONS["confirm"][:-1])
    with pytest.raises(md.Refused):
        md.check_freeze(p, committed_check=False)                       # the set shrank after the freeze


# ---- 6. grounding refuses without a usable corpus ------------------------------
def _wav(path, x, sr=48000, dtype=np.int16):
    from scipy.io import wavfile
    wavfile.write(str(path), sr, np.asarray(x).astype(dtype))
    return path


def _decay(n=48000, amp=0.5, floor=0.001, seed=0):
    """A decaying ring over a flat noise floor: the last two 100 ms windows match."""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 48000
    return amp * np.exp(-t * 30) * np.sin(2 * np.pi * 100 * t) + floor * rng.standard_normal(n)


def test_capture_levels_agree_across_encodings(tmp_path):
    """The same signal stored as int16, int32 and float32 reads as the same dBFS."""
    x = _decay()
    got = {}
    for name, (arr, dt) in {"i16": (x * 32767, np.int16), "i32": (x * 2147483647, np.int32), "f32": (x, np.float32)}.items():
        got[name] = md.measure_capture(_wav(tmp_path / f"{name}.wav", arr, dtype=dt), 48000)
        assert got[name]["status"] == "MEASURED", got[name]
    ref = got["i16"]["tail_ac_peak_dbfs"]
    assert abs(got["i32"]["tail_ac_peak_dbfs"] - ref) < 0.1 and abs(got["f32"]["tail_ac_peak_dbfs"] - ref) < 0.1


@pytest.mark.parametrize("why,arr,dt,sr", [
    ("uint8", lambda x: (x * 127 + 128), np.uint8, 48000),
    ("wrong rate", lambda x: x * 32767, np.int16, 44100),
    ("silent", lambda x: x * 0, np.int16, 48000),
    ("float out of range", lambda x: x * 40000, np.float32, 48000),
])
def test_capture_refuses_unqualified_encoding_rate_or_content(tmp_path, why, arr, dt, sr):
    r = md.measure_capture(_wav(tmp_path / "c.wav", arr(_decay()), sr=sr, dtype=dt), 48000)
    assert r["status"] == "REFUSED" and r["reason"], (why, r)


def test_capture_refuses_non_finite_float(tmp_path):
    x = _decay().astype(np.float32)
    x[1000] = np.nan
    assert md.measure_capture(_wav(tmp_path / "n.wav", x, dtype=np.float32), 48000)["status"] == "REFUSED"


def test_unpinned_corpus_never_yields_measured_voices(tmp_path):
    """A perfectly good WAV in a corpus that is not the pinned, clean checkout."""
    _wav(tmp_path / "v.wav", _decay() * 32767)
    out = md._ground_voices(tmp_path, {"BD": ("v.wav", None)}, 48000, "corpus is not the pinned, clean checkout")
    assert out["BD"]["status"] == "REFUSED" and "pinned" in out["BD"]["reason"]
    ok = md._ground_voices(tmp_path, {"BD": ("v.wav", None)}, 48000, None)
    assert ok["BD"]["status"] == "MEASURED"



def test_ground_refuses_a_missing_corpus(tmp_path):
    g = md.ground(str(tmp_path / "nope"))
    assert g["gate"]["status"] == "REFUSED" and g["gate"]["dc"] == "REFUSED"
    assert g["pinned_ok"] is False
    assert all(v["status"] == "REFUSED" for v in g["voices"].values())


def test_ground_record_carries_provenance(tmp_path):
    """AC6: the ground record is stamped like every other measurement record."""
    import subprocess
    root = pathlib.Path(md.ROOT)
    subprocess.run([sys.executable, str(root / "tools/probes/modal_deadband.py"), "--ground",
                    "--refs", str(tmp_path / "nope"), "--out", str(tmp_path / "g.json")], check=True, capture_output=True)
    g = json.loads((tmp_path / "g.json").read_text())
    for k in ("source_commit", "dirty", "command", "inputs", "uncommitted_sha256"):
        assert k in g["provenance"], k
    assert "--ground" in g["provenance"]["command"]


def test_candidates_record_carries_provenance(tmp_path):
    import subprocess
    root = pathlib.Path(md.ROOT)
    src = (root / "tools/probes/modal_deadband_candidates.py").read_text()
    assert 'res["provenance"] = md.provenance(' in src and "pv.file_sha" in src


def test_ground_inputs_hash_full_sha256(tmp_path):
    _wav(tmp_path / "v.wav", _decay() * 32767)
    voices = md._ground_voices(tmp_path, {"BD": ("v.wav", None)}, 48000, None)
    assert len(voices["BD"]["sha256"]) == 64
    assert md.ground_inputs(dict(voices=voices, refdir=str(tmp_path)))["capture:BD"] == voices["BD"]["sha256"]
