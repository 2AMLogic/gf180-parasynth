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
MATRIX = {
    "": set(),
    "clip": {"headroom", "release"},
    "soft_clip": {"headroom", "presence", "release"},
    "dropout": {"dropout"},
    "stuck": {"automation", "presence", "release", "timing"},
    "late_one": {"presence", "timing"},
    "late_all": {"release", "timing"},
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
