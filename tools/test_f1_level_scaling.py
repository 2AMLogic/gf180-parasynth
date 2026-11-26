"""Controls for the F1 input-scaling experiment (tools/probes/f1_level_scaling.py)
and the matched-level captures (tools/f1_level_capture.py).

The load-bearing control is the register trap: a voice constructed with a
different `volts_per_unit` gets the GLOBAL configuration's gain/ogain from
`patch_regs`, so it renders the baseline's audio. The experiment must record
the words that actually enter the ladder and REFUSE a candidate whose words
equal the baseline's.
"""
from __future__ import annotations

import math
import pathlib
import sys

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "probes"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import f1_level_scaling as fls       # noqa: E402
import f1_level_capture as cap       # noqa: E402
import f1_selected_path as f1        # noqa: E402

CUT = 1000.0
FREQS = [200.0, 1000.0, 3000.0]      # a short stimulus: the controls need words, not curves


def _words(r):
    return (r["gain"], r["ogain"])


def _render(voice, regs, amp=0.25):
    xq, parts = fls.stimulus(FREQS, amp)
    cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
    with fls.instrument(voice) as rec:
        y, _g, _k = f1.render_path(voice, xq, cut, regs)
    return y, rec


# --- the baseline assumption ------------------------------------------------
def test_baseline_vpu_is_asserted_not_assumed():
    assert fls.base_vpu_asserted() == 0.13


def test_candidate_one_is_exactly_the_host_conversion():
    assert fls.candidate_regs(1.0, CUT) == f1.host_regs(CUT, 0.0, 1.0)


@pytest.mark.parametrize("s", [0.5, 0.25])
def test_scaled_candidates_change_both_words_and_compensate_exactly(s):
    b, c = fls.candidate_regs(1.0, CUT), fls.candidate_regs(s, CUT)
    assert c["gain"] != b["gain"] and c["ogain"] != b["ogain"]
    assert c["gain"] == pytest.approx(b["gain"] * s, abs=1)
    # small-signal level: gain*ogain constant to register rounding
    assert c["gain"] * c["ogain"] == pytest.approx(b["gain"] * b["ogain"], rel=1e-4)
    for k in ("k", "cut_lo", "res", "drive"):
        assert c[k] == b[k]


# --- THE TRAP ----------------------------------------------------------------
@pytest.mark.parametrize("s", [0.5, 0.25])
def test_trap_constructor_only_vpu_leaves_the_host_words_unchanged(s):
    voice, regs = fls.constructor_only_regs(s, CUT)
    inner = voice.ladder._ladder
    assert inner.vpu == pytest.approx(0.13 * s)            # the constructor DID change
    assert _words(regs) == _words(fls.candidate_regs(1.0, CUT))   # the words did not


def test_trap_constructor_only_candidate_renders_the_baseline_audio_bit_for_bit():
    base_voice = f1.build_voice("selected")[1]
    y0, _ = _render(base_voice, fls.candidate_regs(1.0, CUT))
    trap_voice, regs = fls.constructor_only_regs(0.25, CUT)
    y1, _ = _render(trap_voice, regs)
    assert np.array_equal(y0, y1)


def test_trap_is_refused_by_the_register_assertion():
    voice, regs = fls.constructor_only_regs(0.5, CUT)
    _y, rec = _render(voice, regs)
    base = _words(fls.candidate_regs(1.0, CUT))
    with pytest.raises(fls.Refused, match="equal the baseline"):
        fls.assert_registers("half", 0.5, rec["words"], _words(regs), base)


def test_real_candidate_words_enter_the_ladder_and_change_the_audio():
    base_voice = f1.build_voice("selected")[1]
    y0, r0 = _render(base_voice, fls.candidate_regs(1.0, CUT))
    v = f1.build_voice("selected")[1]
    regs = fls.candidate_regs(0.25, CUT)
    y1, r1 = _render(v, regs)
    fls.assert_registers("quarter", 0.25, r1["words"], _words(regs),
                         _words(fls.candidate_regs(1.0, CUT)))     # does not raise
    assert r1["words"] == {_words(regs)}
    assert int(np.count_nonzero(y0 != y1)) > 0.5 * len(y0)


def test_words_predicted_but_not_entering_are_refused():
    v = f1.build_voice("selected")[1]
    predicted = _words(fls.candidate_regs(0.5, CUT))
    _y, rec = _render(v, fls.candidate_regs(1.0, CUT))           # baseline words go in
    with pytest.raises(fls.Refused, match="not the candidate's predicted"):
        fls.assert_registers("half", 0.5, rec["words"], predicted,
                             _words(fls.candidate_regs(1.0, CUT)))


# --- instrumentation is counts only ------------------------------------------
def test_instrumentation_does_not_change_a_word_and_restores_itself():
    import fixed
    orig_sat = fixed.sat
    v1 = f1.build_voice("selected")[1]
    v2 = f1.build_voice("selected")[1]
    regs = fls.candidate_regs(1.0, CUT)
    xq, _ = fls.stimulus(FREQS, 0.5)
    cut = np.full(len(xq), regs["cut_lo"], dtype=np.int64)
    y_plain, _, _ = f1.render_path(v1, xq, cut, regs)
    with fls.instrument(v2) as rec:
        y_inst, _, _ = f1.render_path(v2, xq, cut, regs)
    assert np.array_equal(y_plain, y_inst)
    assert rec["tanh_calls"] > 0
    assert fixed.sat is orig_sat and "process" not in vars(v2.ladder._ladder)


def test_clamp_counter_sees_an_overdriven_input():
    v = f1.build_voice("selected")[1]
    regs = dict(fls.candidate_regs(1.0, CUT))
    regs["gain"] = regs["gain"] * 4                        # 4x the input: past the tanh domain
    _y, rec = _render(v, regs, amp=0.9)
    assert rec["tanh_clamp"] > 0


# --- the noise estimator: a known answer --------------------------------------
def test_noise_estimator_reads_a_known_noise_floor_and_ignores_harmonics():
    sr, amp = 48000, 1000.0
    rng = np.random.default_rng(1)
    parts, segs, t0 = [], [], 0
    for f in (200.0, 1000.0):
        n = 9600
        t = np.arange(n) / sr
        s = amp * np.sin(2 * np.pi * f * t) + 0.1 * amp * np.sin(2 * np.pi * 3 * f * t)
        segs.append(s + rng.normal(0, amp / math.sqrt(2) * 10 ** (-70 / 20), n))
        parts.append((t0, n, f))
        t0 += n
    r = fls.noise_thd(np.concatenate(segs), parts, amp)
    assert r["noise_db_re_stimulus_median"] == pytest.approx(-70.0, abs=0.5)
    assert r["thd_db_median"] == pytest.approx(-20.0, abs=0.1)


# --- the captures ------------------------------------------------------------
def test_captures_verify_and_a_tampered_hash_refuses():
    m = cap.load_manifest()
    y, meta = cap.load_capture("cut250", 0.0625, 1, m)
    assert len(y) == meta["frames"]
    with pytest.raises(cap.Refused, match="hashes"):
        cap.load_capture("cut250", 0.0625, 1, m, inject="TAMPERED")


def test_captures_are_separate_from_the_frozen_profile():
    m = cap.load_manifest()
    assert all(not c["archive_member"].startswith("refprofile") for c in m["clips"].values())
    assert cap.OUT_DIR.relative_to(ROOT).parts[:3] == ("docs", "scorecard", "f1-level")


def test_post_render_pin_alias_is_limited_to_the_four_audio_in_indices():
    assert set(cap.AUDIO_IN_NAMES) == {259, 260, 264, 265}


# --- the rule itself, on synthetic tables ------------------------------------
def _fake_table(corner_err, spread=0.0, base_words=(1, 2), cand_words=(3, 4)):
    def lv(err, words, extra=0.0):
        cases = {c: {"score": {"corner_hz": {"valid": True, "error_pct": err + extra},
                               "rolloff_db_oct": {"valid": True, "error": 0.5},
                               "lowband_db": {"valid": True, "error": 0.1}},
                     "regs": {"entering": [list(words)], "baseline": list(base_words)},
                     "clipping": {"reconstruction_would_clip": 0, "output_rail_words": 0,
                                  "tanh_domain_clamps": 0, "sat_state_events": 0}}
                 for c in fls.CASES}
        return {"cases": cases, "open": {"noise": {"noise_db_re_stimulus_median": -80.0},
                                         "clipping": {"reconstruction_would_clip": 0,
                                                      "output_rail_words": 0,
                                                      "tanh_domain_clamps": 0,
                                                      "sat_state_events": 0}}}
    tags = [cap.amp_tag(a) for a in fls.AMPS]
    base = {"s": 1.0, "levels": {t: lv(-16.0, base_words, i * 5.0) for i, t in enumerate(tags)}}
    cand = {"s": 0.5, "levels": {t: lv(corner_err, cand_words, i * spread)
                                 for i, t in enumerate(tags)}}
    return {"baseline": base, "half": cand}


def test_rule_selects_a_candidate_that_meets_every_clause():
    ev = fls.evaluate(_fake_table(-5.0))
    assert ev["verdicts"]["half"]["eligible"] and ev["selection"] == "half"


def test_rule_refuses_selection_when_words_equal_the_baseline():
    ev = fls.evaluate(_fake_table(-5.0, cand_words=(1, 2)))
    assert not ev["verdicts"]["half"]["checks"]["1_registers"]
    assert ev["selection"] is None


def test_rule_rejects_an_insufficient_corner_gain():
    ev = fls.evaluate(_fake_table(-14.0))
    assert not ev["verdicts"]["half"]["checks"]["3_corner"] and ev["selection"] is None


# --- the worker's grid check, through a recording rig and no plugin (#1391) --
#: Tone 12 of the open20k grid: what the frozen profile holds and what the
#: same `np.geomspace` call regenerates as on Linux x86_64 with Python 3.12.3
#: and NumPy 2.5.5. One binary64 step apart; the same float32 stimulus.
ULP_PAIR = (float.fromhex("0x1.6bde96b0d5dc1p+8"), float.fromhex("0x1.6bde96b0d5dc0p+8"))
GRID_REFUSAL = "the frozen stepped-tone grid differs from reference_compare.FREQS: "


def _frozen_grid():
    clip = cap.CONDITIONS["open20k"][1]
    return [float(f) for f in cap.rp.load_profile()["clips"][clip]["freqs_hz"]]


def _reported_pair(frozen):
    """The open20k grid with tone 12 set to the OTHER of the two reported
    values, whichever of them the frozen profile holds."""
    assert frozen[12] in ULP_PAIR, "the profile this pair was reported against"
    return frozen[:12] + [ULP_PAIR[1 - ULP_PAIR.index(frozen[12])]] + frozen[13:]


def _tone(f, amp=0.25):
    """(float32 samples, integer window start and length) of one grid tone."""
    import reference_rigs as rr
    settle, window = rr.SurgeRig.TONE_SETTLE_S, rr.SurgeRig.TONE_WINDOW_S
    x, [(start, n, _f)] = rr.tone_train([f], amp, settle, window)
    return x.astype("float32"), (int(start), int(n))


def _same_tone(a, b, amp):
    (x, p), (y, q) = _tone(a, amp), _tone(b, amp)
    return x.tobytes() == y.tobytes() and p == q


@pytest.fixture
def recording_rig(monkeypatch):
    """`worker` with no plugin behind it. `SurgeRig` keeps its pin table,
    stimulus constants and projection, and the filter is a wire: the clip is
    the stimulus `tone_render` was handed, and an open filter reads 0 dB
    against the frozen wide-open clip, which is the same wire at 0.25. Every
    `tone_render` call is recorded. `regenerate` sets the grid that
    `reference_compare.FREQS` appears to produce."""
    import types
    import reference_rigs as rr
    frozen = _frozen_grid()
    names = {idx: name for idx, _val, name, _want in rr.SurgeRig.PINS}
    texts = {idx: want for idx, _val, _name, want in rr.SurgeRig.PINS}
    texts[rr.SurgeRig.I["f1_type"]] = "LP Vintage Ladder"
    texts[rr.SurgeRig.I["f1_sub"]] = "Type 2"
    frozen_y, frozen_parts = rr.tone_train(
        frozen, 0.25, rr.SurgeRig.TONE_SETTLE_S, rr.SurgeRig.TONE_WINDOW_S)
    calls = []

    class RecordingRig(rr.SurgeRig):
        def __init__(self, subtype="Type 2"):
            self.block = rr.BLOCK
            self.p = types.SimpleNamespace(
                get_parameter_name=names.__getitem__,
                get_parameter_text=texts.__getitem__,
                get_latency_samples=lambda: 0)

        def tone_render(self, freqs, cut, res, amp):
            calls.append((list(freqs), cut, res, amp))
            x, parts = rr.tone_train(freqs, amp, self.TONE_SETTLE_S, self.TONE_WINDOW_S)
            return x, parts, cut

    monkeypatch.setitem(sys.modules, "dawdreamer", types.ModuleType("dawdreamer"))
    monkeypatch.setattr(rr, "SurgeRig", RecordingRig)
    monkeypatch.setattr(cap, "assert_plugin_identity", dict)
    monkeypatch.setattr(
        cap.rp, "load_clip",
        lambda clip_id, prof=None: (frozen_y, rr.SR, {"amp": 0.25, "parts": frozen_parts}))

    def regenerate(grid):
        monkeypatch.setattr(cap.rp, "_freqs", lambda: list(grid))

    return types.SimpleNamespace(calls=calls, frozen=frozen, regenerate=regenerate)


def _assert_take_used_the_frozen_tones(rig, tmp_path, rec, amp):
    import json
    conditions = [(cut, cap.RES, amp) for cut, _fz in cap.CONDITIONS.values()]
    assert rig.calls == [(rig.frozen, *command) for command in conditions]
    assert json.loads((tmp_path / "take.json").read_text()) == rec
    assert set(rec["clips"]) == set(cap.CONDITIONS)
    for clip in rec["clips"].values():
        assert clip["freqs_hz"] == rig.frozen
        assert [f for _start, _n, f in clip["parts"]] == rig.frozen


def _assert_refused_before_any_render(rig, tmp_path, grid, diagnostic):
    """The worker refuses with exactly `diagnostic`, renders nothing and
    writes nothing."""
    rig.regenerate(grid)
    with pytest.raises(cap.Refused) as refused:
        cap.worker(0.25, 1, tmp_path)
    assert str(refused.value) == diagnostic
    assert rig.calls == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("amp", cap.AMPS)
def test_worker_takes_every_condition_on_the_exact_grid_with_the_frozen_tones(
    recording_rig, tmp_path, amp
):
    rig = recording_rig
    rig.regenerate(rig.frozen)
    rec = cap.worker(amp, 1, tmp_path)
    _assert_take_used_the_frozen_tones(rig, tmp_path, rec, amp)


@pytest.mark.parametrize("amp", cap.AMPS)
def test_worker_takes_every_condition_on_the_one_ulp_grid_with_the_frozen_tones(
    recording_rig, tmp_path, amp
):
    """The reported pair, with the regenerated side set to whichever of the
    two literals this host does not produce on its own."""
    rig = recording_rig
    regenerated = _reported_pair(rig.frozen)
    assert _same_tone(regenerated[12], rig.frozen[12], amp)
    rig.regenerate(regenerated)
    rec = cap.worker(amp, 1, tmp_path)
    _assert_take_used_the_frozen_tones(rig, tmp_path, rec, amp)


def _tone_differs(index, frozen, regenerated, why):
    """The worker's diagnostic for one tone that is not the frozen one."""
    return f"{index}: {frozen.hex()} frozen, {regenerated.hex()} regenerated: {why}"


NOT_ADJACENT = "not an adjacent binary64 value"
STIMULUS_DIFFERS = ("adjacent values, but not the same float32 stimulus and windows "
                    "at amp 0.25")


def _refused_grid(*tones):
    """The whole diagnostic for a grid whose entries `tones_differs` describe."""
    return GRID_REFUSAL + "; ".join(tones)


def test_issue_1391_control_the_exact_list_comparison_still_refuses_the_reported_pair(
    recording_rig, monkeypatch, tmp_path
):
    """The comparison that shipped, kept red. Put back as exact list equality,
    it refuses the reported pair, so the take accepted above is the input the
    bug refused and not a copy of the exact grid."""
    rig = recording_rig

    def exact_list_comparison(frozen, regenerated, *_amp_settle_window):
        return [] if frozen == regenerated else [""]

    monkeypatch.setattr(cap, "grid_differences", exact_list_comparison)
    _assert_refused_before_any_render(rig, tmp_path, _reported_pair(rig.frozen), GRID_REFUSAL)


def test_worker_refuses_a_tone_that_keeps_its_window_and_moves_its_samples(
    recording_rig, tmp_path
):
    """The near miss from #1391: `np.isclose` accepts it, the window is the
    frozen tone's, and most of the float32 samples move."""
    rig = recording_rig
    a, moved = rig.frozen[12], rig.frozen[12] + 1e-6
    frozen_tone, moved_tone = _tone(a), _tone(moved)
    assert np.isclose(moved, a) and frozen_tone[1] == moved_tone[1]
    assert np.count_nonzero(frozen_tone[0] != moved_tone[0]) > len(frozen_tone[0]) // 2
    _assert_refused_before_any_render(
        rig, tmp_path, rig.frozen[:12] + [moved] + rig.frozen[13:],
        _refused_grid(_tone_differs(12, a, moved, NOT_ADJACENT)))


def test_worker_refuses_an_adjacent_value_whose_float32_tone_differs(recording_rig, tmp_path):
    """One step ABOVE tone 12: adjacent, the step the reported pair does not
    take, and one float32 sample moves. Adjacency alone must not admit it."""
    rig = recording_rig
    a, adjacent = rig.frozen[12], float(np.nextafter(rig.frozen[12], math.inf))
    frozen_tone, adjacent_tone = _tone(a), _tone(adjacent)
    assert frozen_tone[1] == adjacent_tone[1]
    assert adjacent_tone[0].tobytes() != frozen_tone[0].tobytes()
    _assert_refused_before_any_render(
        rig, tmp_path, rig.frozen[:12] + [adjacent] + rig.frozen[13:],
        _refused_grid(_tone_differs(12, a, adjacent, STIMULUS_DIFFERS)))


def test_worker_refuses_a_tone_two_steps_away_whose_float32_tone_is_unchanged(
    recording_rig, tmp_path
):
    """The bound is ONE binary64 step, and it is not implied by the stimulus
    comparison: two steps below tone 12 is still the same float32 tone in the
    same window, and it is refused all the same."""
    rig = recording_rig
    a = rig.frozen[12]
    two_steps = float(np.nextafter(np.nextafter(a, -math.inf), -math.inf))
    assert _same_tone(two_steps, a, 0.25)
    _assert_refused_before_any_render(
        rig, tmp_path, rig.frozen[:12] + [two_steps] + rig.frozen[13:],
        _refused_grid(_tone_differs(12, a, two_steps, NOT_ADJACENT)))


@pytest.mark.parametrize("case", ["missing", "reordered", "nan", "infinite"])
def test_worker_refuses_a_grid_with_a_missing_reordered_or_non_finite_tone(
    recording_rig, tmp_path, case
):
    rig = recording_rig
    a, b = rig.frozen[12], rig.frozen[13]
    grids = {
        "missing": (rig.frozen[:-1], f"{GRID_REFUSAL}{len(rig.frozen)} frozen tones, "
                                      f"{len(rig.frozen) - 1} regenerated"),
        "reordered": (rig.frozen[:12] + [b, a] + rig.frozen[14:],
                      _refused_grid(_tone_differs(12, a, b, NOT_ADJACENT),
                                    _tone_differs(13, b, a, NOT_ADJACENT))),
        "nan": (rig.frozen[:12] + [math.nan] + rig.frozen[13:],
                _refused_grid(_tone_differs(12, a, math.nan, NOT_ADJACENT))),
        "infinite": (rig.frozen[:12] + [math.inf] + rig.frozen[13:],
                     _refused_grid(_tone_differs(12, a, math.inf, NOT_ADJACENT))),
    }
    grid, diagnostic = grids[case]
    _assert_refused_before_any_render(rig, tmp_path, grid, diagnostic)
