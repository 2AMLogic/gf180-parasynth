"""Controls for tools/performance_suite.py (issue #338).

The suite is only worth its green if each seeded defect turns it red. The
performer that makes the audio (`reference_render`) is independent of the
voice and drum models, so these are not decision-record tests of our own model.
"""
from __future__ import annotations

import dataclasses
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import performance_suite as ps  # noqa: E402

PH = ps.phrases()
SR = ps.SR


def verdicts(defect, names=None):
    out = {}
    for r in ps.run_suite(lambda p: ps.reference_render(p, defect=defect), names):
        out[r["phrase"]] = r
    return out


def test_clean_reference_is_green_on_every_phrase():
    res = verdicts("")
    assert {k: v["verdict"] for k, v in res.items()} == {k: "PASS" for k in PH}, \
        {k: v["failures"] for k, v in res.items() if v["failures"]}


def test_phrase_families_are_all_present():
    assert {"bass", "lead", "drums", "mixed_automation", "repeated_notes", "dense",
            "dense_drums"} <= set(PH)
    mixed = PH["mixed_automation"]
    assert {e.kind for e in mixed.events} == {"note", "drum"} and mixed.controls and mixed.automation


# defect -> (substring that must appear in a failure, phrases that must go red)
RED = {
    "clip": ("clipping", ("drums", "dense_drums", "mixed_automation")),
    "soft_clip": ("flat-topped", ("drums", "mixed_automation")),   # below the rail: only the run test sees it
    "dropout": ("dropout", ("bass", "lead", "mixed_automation")),
    "stuck": ("stuck", ("bass",)),
    "late_one": ("timing", tuple(PH)),
    "late_all": ("timing", tuple(PH)),
    "missing": ("inaudible|no onset", tuple(PH)),
    "legato": ("no onset", ("repeated_notes", "dense")),
    "frozen_automation": ("automation", ("mixed_automation",)),
    "truncate": ("hard-truncated", ("bass", "lead", "repeated_notes")),
    "quiet": ("inaudible", ("bass", "lead", "repeated_notes", "dense")),
}


@pytest.mark.parametrize("defect", sorted(RED))
def test_seeded_defect_turns_the_suite_red(defect):
    needle, must = RED[defect]
    res = verdicts(defect)
    for name in must:
        r = res[name]
        assert r["verdict"] == "FAIL", (defect, name, r)
        assert any(n in f for f in r["failures"] for n in needle.split("|")), (defect, name, r["failures"])
    assert ps.worst(list(res.values())) == "FAIL"


def test_defects_are_localised_not_blanket():
    """Clipping must not be reported as a timing fault and vice versa."""
    clip = verdicts("clip", ["bass"])["bass"]
    assert clip["verdict"] == "PASS" or not any("timing" in f for f in clip["failures"])
    late = verdicts("late_all", ["lead"])["lead"]
    assert all(f.startswith("timing") for f in late["failures"])


def test_silence_is_refused_not_failed_or_passed():
    for r in verdicts("silent").values():
        assert r["verdict"] == "REFUSED"
    assert ps.worst(list(verdicts("silent").values())) == "REFUSED"


def test_going_quiet_cannot_pass_headroom():
    """A candidate that 'fixes' clipping by being 26 dB quieter must still be red."""
    p = PH["lead"]
    x = ps.reference_render(p) * 0.05
    r = ps.evaluate(x, p)
    assert r["verdict"] == "FAIL" and any("inaudible" in f for f in r["failures"])


def test_onset_detector_known_answer():
    """A planted burst at a known time, independent of any renderer."""
    p = ps.Phrase("kat", 1.0, (ps.Event(0.3, "drum", drum="sd", ring_s=0.3),))
    for delay in (0.0, 0.004, 0.030):
        x = np.zeros(SR)
        a = int((0.3 + delay) * SR)
        n = 4800
        rng = np.random.default_rng(1)
        x[a:a + n] = 0.3 * rng.standard_normal(n) * np.exp(-np.arange(n) / 600)
        r = ps.evaluate(x, p)
        got = r["info"]["events"][0]["lateness_ms"]
        assert abs(got - delay * 1000) <= 3.0, (delay, got)
        assert (r["verdict"] == "FAIL") == (delay > ps.LATE_TOL_S)


def test_ringing_tail_does_not_hide_a_missing_retrigger():
    """The rise rule: a held tone with a second onset scheduled over it is not
    credited with an onset just because it is loud there."""
    p = ps.Phrase("held", 2.0, (ps.Event(0.3, "note", 0.2, 60), ps.Event(0.8, "note", 0.2, 60)),
                  release_s=0.3)
    t = np.arange(2 * SR) / SR
    x = 0.3 * np.sin(2 * np.pi * 261.6 * t) * ((t > 0.3) & (t < 1.2))     # one long tone
    r = ps.evaluate(x, p)
    assert any("event 1" in f and "no onset" in f for f in r["failures"])


@pytest.mark.parametrize("mutate,needle", [
    (lambda x: np.where(np.arange(len(x)) == 5, np.nan, x), "non-finite"),
    (lambda x: x[: len(x) // 2], "shorter"),
    (lambda x: x * 0.0, "silent"),
    (lambda x: np.stack([x, x]), "mono"),
])
def test_apparatus_preconditions_refuse(mutate, needle):
    p = PH["bass"]
    with pytest.raises(ps.Refused, match=needle):
        ps.evaluate(mutate(ps.reference_render(p)), p)


def test_refuses_unresolvable_schedule_and_unjudgeable_tail():
    close = ps.Phrase("c", 1.0, (ps.Event(0.2, "drum", drum="bd"), ps.Event(0.21, "drum", drum="bd")))
    with pytest.raises(ps.Refused, match="closer"):
        ps.evaluate(np.ones(SR) * 0.1, close)
    short = ps.Phrase("s", 0.5, (ps.Event(0.3, "note", 0.15, 60),), release_s=0.35)
    with pytest.raises(ps.Refused, match="tail"):
        ps.evaluate(np.ones(SR) * 0.1, short)


def test_every_default_phrase_has_a_judgeable_release_tail():
    for p in PH.values():
        ps.evaluate(ps.reference_render(p), p)      # would raise Refused otherwise


def test_cli_exit_codes(tmp_path):
    assert ps.main([]) == 0
    assert ps.main(["--inject", "clip"]) == 1
    assert ps.main(["--inject", "clip", "--expect-fail"]) == 0
    assert ps.main(["--inject", "silent", "--expect-fail"]) == 2      # REFUSED is never a pass
    assert ps.main(["--backend", "model", "--inject", "clip"]) == 2
    out = tmp_path / "r.json"
    ps.main(["--out", str(out), "--phrase", "bass"])
    assert '"PASS"' in out.read_text()


# ---------------------------------------------------------------- review #606 controls
def _sine_phrase(midi):
    return ps.Phrase("clean_sine", 1.0, (ps.Event(0.2, "note", 0.3, midi),), release_s=0.35)


def _sine(midi, amp, clip=None):
    t = np.arange(SR) / SR - 0.2
    f = 440.0 * 2 ** ((midi - 69) / 12)
    x = amp * np.sin(2 * np.pi * f * t) * np.clip(t / 0.003, 0, 1) * np.exp(-np.maximum(t - 0.3, 0) / 0.04)
    return x if clip is None else np.clip(x, -clip, clip)


@pytest.mark.parametrize("midi", [24, 36, 48, 60, 72, 84, 96])
@pytest.mark.parametrize("amp", [0.35, 0.5, 0.8])
def test_clean_sine_is_not_flat_topped_across_the_playing_range(midi, amp):
    """Known answer: a clean sine has no plateau (issue: 261.6 Hz at 0.5 was flagged)."""
    r = ps.evaluate(_sine(midi, amp), _sine_phrase(midi))
    assert not any("clipping" in f for f in r["failures"]), (midi, amp, r["failures"])


@pytest.mark.parametrize("midi", [24, 36, 60, 84])
def test_below_rail_clip_of_the_same_sine_is_flat_topped(midi):
    """Paired mutant: the identical sine clipped at 0.4 of 0.5 (below the rail) must be red."""
    r = ps.evaluate(_sine(midi, 0.5, clip=0.4), _sine_phrase(midi))
    assert any("flat-topped" in f for f in r["failures"]), (midi, r["failures"])


@pytest.mark.parametrize("argv", [["--phrase", "typo"], ["--phrase", "typo", "--expect-fail"],
                                  ["--phrase", "bass", "--phrase", "typo"],
                                  ["--phrase", "bass", "--phrase", "typo", "--expect-fail"],
                                  ["--inject", "clip", "--phrase", "typo", "--expect-fail"]])
def test_unknown_phrase_is_refused_not_passed(argv):
    assert ps.main(argv) == 2


def test_empty_population_is_never_a_pass():
    assert ps.worst([]) == "REFUSED"
    with pytest.raises(ps.Refused):
        ps.run_suite(lambda p: ps.reference_render(p), [])
    with pytest.raises(ps.Refused):
        ps.run_suite(lambda p: ps.reference_render(p), ["typo"])


def test_stuck_drum_is_caught_by_the_drum_stuck_check_itself():
    """The seeded drone sits below the headroom guard, so only `stuck: drum` can catch it."""
    r = verdicts("stuck", ["drums"])["drums"]
    assert not any(f.startswith(("headroom", "clipping")) for f in r["failures"]), r["failures"]
    assert any(f.startswith("stuck: drum") for f in r["failures"]), r["failures"]


# defect -> properties it must move (intended); the full published matrix is pinned in MATRIX
INTENDED = {
    "clip": {"headroom"}, "soft_clip": {"headroom"}, "dropout": {"dropout"},
    "stuck": {"release"}, "late_one": {"timing"}, "late_all": {"timing"},
    "missing": {"presence"}, "legato": {"presence"}, "frozen_automation": {"automation"},
    "truncate": {"release"}, "quiet": {"presence"},
}
# MOVED set per defect on the reference performer, collateral included. BLIND is the
# complement within ps.PROPERTIES. Any change here is a change in what a check can see.
# 2026-11-18 (#338 synthtop backend): clip/soft_clip/late_all lose their `release`
# collateral -- it was dense_drums' final sd sitting -28.x dB against the -30 dB
# stuck deadline at ring_s 0.3, a knife-edge on the REFERENCE performer itself that
# each defect's level/offset perturbation tipped; ring_s is now 0.5 (the R1 kit's
# measured sd decay: -22.2 dB at +0.30 s, -34.3 dB at +0.50 s). soft_clip also loses
# `presence`: its masked hat is now found by the high-band onset path instead of
# reading "no onset found".
MATRIX = {
    "": set(),
    "clip": {"headroom"},
    "soft_clip": {"headroom"},
    "dropout": {"dropout"},
    "stuck": {"automation", "presence", "release", "timing"},
    "late_one": {"presence", "timing"},
    "late_all": {"timing"},
    "missing": {"automation", "presence"},
    "legato": {"presence"},
    "frozen_automation": {"automation"},
    "truncate": {"release"},
    "quiet": {"automation", "presence"},
    "silent": set(),                     # REFUSED: no property is judged
}


def test_properties_by_defects_matrix_is_published_and_pinned():
    m = ps.defect_matrix()
    assert {k: set(v["moved"]) for k, v in m.items()} == MATRIX
    assert m[""]["verdict"] == "PASS" and m["silent"]["verdict"] == "REFUSED"
    for d, want in INTENDED.items():
        assert want <= set(m[d]["moved"]), (d, "intended property did not move", m[d])
        assert set(m[d]["moved"]) | set(m[d]["blind"]) == set(ps.PROPERTIES)
        assert not set(m[d]["moved"]) & set(m[d]["blind"])
    for prop in ps.PROPERTIES:                     # every property is exercised by some defect
        assert any(prop in m[d]["moved"] for d in INTENDED), prop


def test_every_failure_message_belongs_to_a_property():
    for d in ps.DEFECTS:
        for r in verdicts(d).values():
            for f in (r["failures"] if r["verdict"] == "FAIL" else ()):
                ps.failed_property(f)
    with pytest.raises(ValueError):
        ps.failed_property("something new")


# ------------------------------------------- review #606 (2nd): automation declarations
# An automation declaration that cannot be evaluated must be REFUSED, never
# narrowed to the IDs that happen to be valid and never skipped into an "ok".
# Every row below PASSED with props["automation"] == "ok" at 300f9ac9 against
# the frozen-automation mutant, which the valid declaration catches.
def _with_automation(**over):
    ph = PH["mixed_automation"]
    au = dict(ph.automation[0])
    au.update(over)
    for k in [k for k, v in au.items() if v is _DROP]:
        del au[k]
    return dataclasses.replace(ph, automation=(au,))


_DROP = object()
_FROZEN = ps.reference_render(PH["mixed_automation"], defect="frozen_automation")
_CLEAN = ps.reference_render(PH["mixed_automation"])

DEFEATING_DECLARATIONS = {
    "no events": dict(events=()),                          # Judge repro
    "one event": dict(events=(0,)),                        # Judge repro
    "all out of range": dict(events=(100, 101)),           # Judge repro
    "mixed valid/invalid": dict(events=(0, 2, 100)),       # must not be narrowed to (0, 2)
    "negative id": dict(events=(-2, 0, 2)),                # -2 would silently index from the end
    "only negative": dict(events=(-4, -2)),
    "duplicate id": dict(events=(0, 0)),                   # two values, one event
    "out of order": dict(events=(4, 2, 0)),
    "bool id": dict(events=(False, True)),
    "float id": dict(events=(0.0, 2.0)),
    "events not a sequence": dict(events=2),
    "id is a drum, declared of note": dict(events=(0, 1)),
    "unknown kind": dict(kind="brightness"),               # used to fall through to RMS
    "direction zero": dict(direction=0),                   # makes the monotone test vacuous
    "direction two": dict(direction=2),
    "min_ratio one": dict(min_ratio=1.0),                  # ratio test vacuous
    "min_ratio nan": dict(min_ratio=float("nan")),         # ratio < nan is always False
    "min_ratio inf": dict(min_ratio=float("inf")),
    "missing min_ratio": dict(min_ratio=_DROP),
    "missing direction": dict(direction=_DROP),
    "unknown key": dict(min_ratoi=1.5),
    "unknown of": dict(of="voice"),
}


@pytest.mark.parametrize("label", sorted(DEFEATING_DECLARATIONS))
@pytest.mark.parametrize("audio", ["frozen", "clean"])
def test_unevaluable_automation_declaration_is_refused(label, audio):
    ph = _with_automation(**DEFEATING_DECLARATIONS[label])
    x = _FROZEN if audio == "frozen" else _CLEAN
    with pytest.raises(ps.Refused, match="automation"):
        ps.evaluate(x, ph)


def test_judge_repro_reports_refused_through_the_suite_path():
    """The exact Judge #606 repro, read the way a caller reads it."""
    for ids in ((), (0,), (100, 101)):
        ph = dataclasses.replace(PH["mixed_automation"], automation=({
            "kind": "centroid", "events": ids, "direction": 1, "min_ratio": 1.5,
        },))
        with pytest.raises(ps.Refused):
            ps.evaluate(_FROZEN, ph)


def test_valid_declaration_still_fails_frozen_and_passes_clean():
    ph = PH["mixed_automation"]
    f = ps.evaluate(_FROZEN, ph)
    assert f["verdict"] == "FAIL" and f["props"]["automation"] == "FAIL", f["failures"]
    c = ps.evaluate(_CLEAN, ph)
    assert c["verdict"] == "PASS" and c["props"]["automation"] == "ok", c["failures"]
    assert len(c["info"]["automation"][0]) == len(ph.automation[0]["events"])


def test_two_valid_events_is_the_minimum_that_is_evaluated():
    ph = _with_automation(events=(0, 10))
    assert ps.evaluate(_FROZEN, ph)["props"]["automation"] == "FAIL"
    assert ps.evaluate(_CLEAN, ph)["props"]["automation"] == "ok"


def test_window_that_overlaps_the_next_event_is_refused():
    """The 30-110 ms centroid window must sit before the next onset, or it measures a neighbour."""
    ev = (ps.Event(0.3, "note", 0.25, 40), ps.Event(0.36, "note", 0.25, 40),
          ps.Event(0.9, "note", 0.25, 40))
    ph = ps.Phrase("tight", 1.6, ev, release_s=0.3,
                   automation=({"kind": "centroid", "events": (0, 2), "direction": 1, "min_ratio": 1.5},))
    with pytest.raises(ps.Refused, match="window"):
        ps.evaluate(ps.reference_render(ph), ph)


def test_silent_measurement_window_is_refused_not_measured():
    """A window with no energy has no centroid; 0 Hz must not enter a ratio."""
    ph = PH["mixed_automation"]
    x = _CLEAN.copy()
    on = ps.evaluate(_CLEAN, ph)["info"]["events"][2]
    a = int(round((ph.events[2].onset_s + on["lateness_ms"] / 1000) * SR))
    x[a + int(0.02 * SR):a + int(0.12 * SR)] = 0.0
    with pytest.raises(ps.Refused, match="automation"):
        ps.evaluate(x, ph)


def test_unknown_event_kind_is_refused_not_judged_as_a_drum():
    """Same shape found by the #606 search: kind 'Note' fell through to the drum
    branch, skipping the note-only dropout and truncation checks."""
    ph = PH["bass"]
    x = ps.reference_render(ph, defect="dropout")
    assert ps.evaluate(x, ph)["props"]["dropout"] == "FAIL"
    bad = dataclasses.replace(ph, events=tuple(dataclasses.replace(e, kind="Note") for e in ph.events))
    with pytest.raises(ps.Refused, match="kinds"):
        ps.evaluate(x, bad)


def test_model_render_cutoff_control_reaches_the_patch(monkeypatch):
    """Wrong-then-right (#338): model_render passed cutoff=(fc, 20000); with
    sustain 1.0 the filter envelope held 20000, so every commanded cutoff was
    ignored and the automation check saw a flat centroid. Both ends must be the
    commanded cutoff. Cheap: captures the patch, no render of consequence."""
    import mono_artifact_probe as mp
    seen = []
    real = mp.held_patch

    def spy(**kw):
        seen.append(kw["cutoff"])
        return real(**kw)

    monkeypatch.setattr(mp, "held_patch", spy)
    ph = ps.phrases()["mixed_automation"]
    ps.model_render(dataclasses.replace(ph, duration_s=1.0, events=ph.events[:1], controls=ph.controls[:1]))
    assert seen and all(lo == hi for lo, hi in seen), seen
    assert seen[0][0] == ph.controls[0][2]


# ------------------------------------------------------------------ synth_top backend (#338)
# The production-path gate: the phrase becomes the chip's WRITE STREAM and is
# played through model/synth_top_model.py in the R1 release configuration.
# The controls here seed defects in the STREAM -- a dropped gate-off, a floored
# volume, a late note, a full-scale gain -- because a defect the chip ships is
# a control move or an omission, not an edit to the judged audio.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "model"))
import drums_fx as dx  # noqa: E402
import synth_top_model as st  # noqa: E402

SHORT = ps.Phrase("short", 1.1, (ps.Event(0.1, "note", 0.25, 52),
                                 ps.Event(0.55, "note", 0.25, 55)), release_s=0.2)
CONTROLLED = ps.Phrase("controlled", 0.9,
                       (ps.Event(0.1, "note", 0.25, 52), ps.Event(0.55, "note", 0.25, 52)),
                       release_s=0.2,
                       controls=((0.1, "cutoff", 400.0), (0.55, "cutoff", 3200.0)))


def _synthtop(ph, writes=None, **kw):
    return ps.render_writes(writes if writes is not None else ps.phrase_writes(ph, **kw),
                            ph.duration_s)


def _vwrite(stream, addr):
    return [w for w in stream if w[2] == st.SEC_VOICE and w[3] == addr]


def test_phrase_writes_programs_the_register_interface():
    w = ps.phrase_writes(SHORT)
    # the boot-time image carries the osc mixer weights AND the drum gains --
    # and the drum gains live on the VOICE page (streams missing these were
    # exactly how this backend's first debug render came out silent)
    assert _vwrite(w, st.A_W)[0][4] > 0
    assert _vwrite(w, st.A_VOL)[0][4] > 0
    assert _vwrite(w, st.A_DVOL)[0][4] == dx.accent_reg(ps.SYNTHTOP_DRUM_GAIN)
    assert _vwrite(w, st.A_BVOL)[0][4] == dx.accent_reg(ps.SYNTHTOP_DRUM_GAIN)
    # every note: three INC jumps + TRACK + GATE_ON at its onset frame,
    # GATE_OFF at its gate frame
    for e in SHORT.events:
        f, fg = round(e.onset_s * ps.SR), round((e.onset_s + e.gate_s) * ps.SR)
        incs = [x for x in w if x[2] == st.SEC_VOICE and x[0] == f
                and st.A_INC <= x[3] <= st.A_INC + 2]
        assert len(incs) == 3 and all(x[1] == 1 for x in incs), (e, incs)
        assert any(x[0] == f and x[3] == st.A_TRACK for x in w)
        assert any(x[0] == f and x[3] == st.A_GATE_ON for x in w)
        assert any(x[0] == fg and x[3] == st.A_GATE_OFF for x in w)
    # drums pulse STOPS with their bit and drop it the next frame (edge-triggered)
    drums = ps.phrases()["drums"]
    dw = ps.phrase_writes(drums)
    f0 = round(drums.events[0].onset_s * ps.SR)
    stops = [x for x in dw if x[2] == st.SEC_DRUM and x[3] == dx.A_STOPS]
    assert stops[0][0] == f0 and stops[0][4] == 1 << dx.SOUND_STOP["BD"]
    assert stops[1][0] == f0 + 1 and stops[1][4] == 0


def test_cutoff_automation_writes_both_cut_registers_before_the_gate():
    w = ps.phrase_writes(CONTROLLED)
    for e, (_t, _k, hz) in zip(CONTROLLED.events, CONTROLLED.controls):
        f = round(e.onset_s * ps.SR)
        lo, hi = _vwrite(w, st.A_CUT_LO), _vwrite(w, st.A_CUT_HI)
        lo = [x for x in lo if x[0] == f]
        hi = [x for x in hi if x[0] == f]
        assert len(lo) == len(hi) == 1
        assert lo[0][4] == hi[0][4] == round(hz)      # one-sided cut is silently ignored (6c94bdb2)
        order = [x[3] for x in w if x[2] == st.SEC_VOICE and x[0] == f]
        assert order.index(st.A_CUT_LO) < order.index(st.A_GATE_ON)   # before the gate


def test_phrase_writes_refuses_unsendable_schedules():
    with pytest.raises(ps.Refused, match="drum names"):
        ps.phrase_writes(ps.Phrase("bad", 1.0, (ps.Event(0.2, "drum", drum="cowbell"),)))
    with pytest.raises(ps.Refused, match="gate-off"):
        ps.phrase_writes(ps.Phrase("long", 0.4, (ps.Event(0.1, "note", 0.5, 52),)))
    # the drum half of the same record bound, with the input that defeats it:
    # a hit 1 ms before the end whose stop drop and attack coefficient window
    # land past the record. Before the bound this raised AssertionError from
    # inside synth_top_model.run, not Refused (rule 8, judge on PR #987).
    with pytest.raises(ps.Refused, match="past the"):
        ps.phrase_writes(ps.Phrase("late_bd", 0.5, (ps.Event(0.499, "drum", drum="bd"),)))


def test_synthtop_backend_clean_phrase_passes_the_suite():
    x = _synthtop(SHORT)
    r = ps.evaluate(x, SHORT)
    assert r["verdict"] == "PASS", r["failures"]
    assert r["info"]["peak_dbfs"] < -1.0          # and with headroom, not at the rail


def test_synthtop_backend_full_suite_passes():
    # the production-path gate, standing: every phrase through the shipped
    # mixer, re-derived by the tools/ CI job and make verify's broad pytest
    # run -- not a point measurement (judge on PR #987).
    res = ps.run_suite(lambda p: ps.synthtop_render(p))
    assert len(res) == len(ps.phrases())
    bad = [(r["phrase"], r["verdict"], r.get("failures"))
           for r in res if r["verdict"] != "PASS"]
    assert not bad, bad


def test_control_dropping_the_last_note_gate_off_is_stuck():
    # the LAST note's: a gate-off dropped under a FOLLOWING note reads as that
    # note's masked onset (presence), not as stuck -- the retrigger resets the
    # envelope before any tail can be judged. The last note has no follower.
    w = ps.phrase_writes(SHORT)
    fg_last = round((SHORT.events[-1].onset_s + SHORT.events[-1].gate_s) * ps.SR)
    w = [x for x in w
         if not (x[2] == st.SEC_VOICE and x[3] == st.A_GATE_OFF and x[0] == fg_last)]
    r = ps.evaluate(_synthtop(SHORT, writes=w), SHORT)
    assert r["verdict"] == "FAIL"
    assert r["props"]["release"] == "FAIL"
    assert any(f.startswith("stuck:") for f in r["failures"]), r["failures"]


def test_control_flooring_the_volume_mid_note_is_a_dropout():
    w = ps.phrase_writes(SHORT)
    vol = _vwrite(w, st.A_VOL)[0][4]
    w = sorted(w + [(round(0.20 * ps.SR), 0, st.SEC_VOICE, st.A_VOL, 0),
                    (round(0.22 * ps.SR), 0, st.SEC_VOICE, st.A_VOL, vol)], key=lambda t: t[0])
    r = ps.evaluate(_synthtop(SHORT, writes=w), SHORT)
    assert r["verdict"] == "FAIL"
    assert r["props"]["dropout"] == "FAIL"
    assert r["props"]["timing"] == "ok" and r["props"]["presence"] == "ok"


def test_control_late_note_writes_read_as_late_events():
    lag = round(0.025 * ps.SR)
    w = [(t[0] + (lag if t[2] == st.SEC_VOICE and t[0] > 0 else 0),) + t[1:]
         for t in ps.phrase_writes(SHORT)]
    r = ps.evaluate(_synthtop(SHORT, writes=w), SHORT)
    assert r["verdict"] == "FAIL"
    assert r["props"]["timing"] == "FAIL"
    assert r["props"]["release"] == "ok"          # schedule-based tail checks must not move


def test_control_full_scale_gains_clip_and_headroom_catches_it():
    w = ps.phrase_writes(SHORT, patch=dict(ps.SYNTHTOP_PATCH, vol=2.0))
    x = _synthtop(SHORT, writes=w)
    rail = int(np.count_nonzero(np.abs(x) >= 0.999))
    assert rail > 0, "the clip control never reached the rail: not a clip control"
    r = ps.evaluate(x, SHORT)
    assert r["verdict"] == "FAIL"
    assert r["props"]["headroom"] == "FAIL"
    assert any(f.startswith("clipping") for f in r["failures"]), r["failures"]


def test_unknown_engine_is_refused_not_defaulted():
    with pytest.raises(ps.Refused, match="engine"):
        ps.render_writes(ps.phrase_writes(SHORT), SHORT.duration_s, engine="turbo")


def test_render_writes_refuses_stream_past_the_record():
    # render_writes is the second door into the record bound (issue #995):
    # the issue's exact input -- a gate-off at n+10 on a 0.5 s record. Before
    # the bound this raised AssertionError from inside synth_top_model.run,
    # not Refused (phrase_writes got the same shape in #987).
    n = int(0.5 * ps.SR)
    w = [(0, 0, st.SEC_VOICE, st.A_VOL, 100),
         (n + 10, 0, st.SEC_VOICE, st.A_GATE_OFF, 0)]
    with pytest.raises(ps.Refused, match="outside 0.."):
        ps.render_writes(w, 0.5)
    # negative frames refuse the same way
    with pytest.raises(ps.Refused, match="outside 0.."):
        ps.render_writes([(-1, 0, st.SEC_VOICE, st.A_VOL, 100)], 0.5)
    # duration_s=0 is not "outside 0..-1": the record is empty and the
    # refusal must say so
    with pytest.raises(ps.Refused, match="empty record"):
        ps.render_writes([(0, 0, st.SEC_VOICE, st.A_GATE_OFF, 0)], 0.0)


def test_render_writes_generator_stream_renders_same_as_list():
    # rule 8 control for the bound check: render_writes must materialize the
    # stream before checking it. If the check consumed a one-pass iterable,
    # run() would render exact silence with no error -- output that looks
    # exactly like data. Asserted as list-vs-generator equality, not
    # non-silence: a legitimately quiet stream would defeat a "not silent"
    # assertion the same way the eaten stream would.
    w = ps.phrase_writes(SHORT)
    x_list = ps.render_writes(w, SHORT.duration_s)
    assert np.count_nonzero(x_list) > 0, \
        "the fixture stream renders silence: this control is vacuous"
    x_gen = ps.render_writes(iter(w), SHORT.duration_s)
    assert np.array_equal(x_list, x_gen)


def test_render_writes_accepts_write_at_last_frame():
    # boundary control so the bound is not off by one: n-1 is the last
    # sendable frame and must render, not refuse
    n = int(0.5 * ps.SR)
    x = ps.render_writes([(n - 1, 0, st.SEC_VOICE, st.A_GATE_OFF, 0)], 0.5)
    assert len(x) == n


def test_cli_refuses_engine_on_non_synthtop_backend(capsys):
    assert ps.main(["--engine", "baseline", "--phrase", "bass"]) == 2
    assert "REFUSED" in capsys.readouterr().err
    # the old value-comparison guard was satisfied by its own default: naming
    # the default engine on a non-synthtop backend must refuse the same way
    assert ps.main(["--engine", "r1", "--backend", "reference"]) == 2
    assert "REFUSED" in capsys.readouterr().err
