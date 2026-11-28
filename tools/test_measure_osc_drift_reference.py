#!/usr/bin/env python3
"""The reference drift measurement, reproduced from frozen audio with no plugin.

Two of these tests are the only EXTERNAL validation this probe has: the two
frozen manifests carry pitch offsets measured by a different session with a
different estimator, and `model/osc_drift_probe.py` has to reproduce them. A
probe validated only against signals it generated itself is calibrated on its
own model (CLAUDE.md), which is exactly what these two avoid.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))

import exit_contract                                               # noqa: E402
import measure_osc_drift_reference as mod                             # noqa: E402
import osc_drift_probe as odp                                         # noqa: E402

M1A = os.path.join(ROOT, "docs", "scorecard", "mono-m1a-miniv3")
M5A = os.path.join(ROOT, "docs", "scorecard", "mono-m5a-miniv3")
have_frozen = os.path.exists(os.path.join(M1A, "control-osc1_open.wav")) \
    and os.path.exists(os.path.join(M5A, "m5a-miniv3-raw.wav"))
needs_frozen = pytest.mark.skipif(
    not have_frozen, reason="frozen Mini V3 audio absent from this checkout")

SAW_PULSE = "m5a-midi84-saw-vs-pulse"
SAW = "m5a/seg0-saw/n0-midi84"
PULSE = "m5a/seg1-pulse/n0-midi84"
M1A_MIDI36_PAIRS = ("m1a-midi36-4.0s-apart", "m1a-osc1-midi36-4.0s-apart")
M1A_OSC2_PAIR = "m1a-osc2-midi48-4.0s-apart"
DRIFT_BOUND = ("span_cents", "floor_cents", "cents_vs_first")


@pytest.fixture(scope="module")
def measured():
    return mod.measure_miniv3()


@pytest.fixture
def scorecard(tmp_path, monkeypatch):
    for source in (Path(M1A), Path(M5A)):
        case = tmp_path / source.name
        case.mkdir()
        for path in (source / "manifest.json", *source.glob("*.wav")):
            shutil.copyfile(path, case / path.name)
    monkeypatch.setattr(mod, "SCOREBOARD", tmp_path)
    return tmp_path


@pytest.fixture
def published_pairs():
    path = Path(ROOT) / "docs/scorecard/mono-osc-drift/reference-drift-v1.json"
    return json.loads(path.read_text())["miniv3"]["across_note"]


def _by(measured, prefix):
    return [r for r in measured["reports"] if r["label"].startswith(prefix)]


def _usable(report):
    """Everything the probe asks of a window before it will compare it."""
    return (report["verdict"] in (odp.STABLE, odp.PERIODIC, odp.DRIFTING)
            and 0 < report["f0_measured_hz"] < math.inf
            and 0 <= report["floor_cents"] < math.inf)


def _first_difference(a, b, path="", rel_tol=0.0):
    """Where `a` and `b` first differ, or None when they do not. A NaN counts
    as the same NaN and nothing else is forgiven: `==` on two reports holding
    `repeat_r: NaN` is False, and a comparison that is False for identical
    input proves nothing either way (#134). For finite floats from different
    machines, `rel_tol` is the relative tolerance and the absolute tolerance
    near zero."""
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return f"{path}: keys {sorted(a.keys() ^ b.keys())}"
        for k in a:
            got = _first_difference(a[k], b[k], f"{path}/{k}", rel_tol)
            if got:
                return got
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} vs {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b)):
            got = _first_difference(x, y, f"{path}[{i}]", rel_tol)
            if got:
                return got
        return None
    if isinstance(a, float) and isinstance(b, float):
        if math.isnan(a) and math.isnan(b):
            return None
        if math.isclose(a, b, rel_tol=rel_tol, abs_tol=rel_tol):
            return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def _assert_refused_for_independent_captures(ac):
    """Issue #1433's whole assertion, written once: the control below has to be
    shown failing THIS, not something beside it."""
    assert ac["verdict"] == odp.REFUSED, ac
    assert ac["refusal"] == mod.INDEPENDENT_CAPTURES, ac
    assert ac["labels"] == [SAW, PULSE], ac
    assert ac["n_windows"] == 2, ac
    for label in (SAW, PULSE):
        assert label in ac["refusal_detail"], ac
    for key in DRIFT_BOUND:
        assert key not in ac, (key, ac)


@needs_frozen
def test_the_two_references_without_frozen_audio_refuse_with_a_reason():
    """Surge and Diva must be REFUSED with a named missing precondition, not
    absent from the report -- a gap reads as "no drift found"."""
    refused = {r["reference"]: r for r in mod.refused_references()}
    assert set(refused) == {"Surge XT", "u-he Diva"}
    for r in refused.values():
        assert r["verdict"] == odp.REFUSED
        assert r["refusal"] and len(r["refusal_detail"]) > 40


@needs_frozen
def test_m5a_pitch_offset_matches_the_manifests_own_estimator(measured):
    """EXTERNAL cross-check. `docs/scorecard/mono-m5a-miniv3/manifest.json`
    records f0_cents = 0.14315 for MIDI 84 and 0.14328 for MIDI 96, measured in
    another session by another method. This probe must land on the same
    number."""
    man = json.load(open(os.path.join(M5A, "manifest.json")))
    want = {m["note"]: m["f0_cents"]
            for seg in man["timeline"]["segments"] for m in seg["measurements"]}
    got = [r for r in _by(measured, "m5a/") if "f0_offset_cents" in r]
    assert got, "no M5A window produced an f0"
    for r in got:
        assert abs(r["f0_offset_cents"] - want[r["note"]]) < 0.01, (r, want)


@needs_frozen
def test_m1a_octave_control_offset_matches_the_manifest(measured):
    """The second EXTERNAL cross-check: the M1A manifest measures the octave
    oscillator at 130.5467 Hz, which is 3.52 cents below MIDI 48."""
    man = json.load(open(os.path.join(M1A, "manifest.json")))
    f_man = man["controls"]["osc2_open"]["measurement"]["f0"]
    import dsp
    want = float(odp.cents(f_man, dsp.note_hz(48)))
    got = [r for r in _by(measured, "m1a/osc2_open/") if "f0_offset_cents" in r]
    assert got
    for r in got:
        assert abs(r["f0_offset_cents"] - want) < 0.05, (r["f0_offset_cents"], want)


@needs_frozen
def test_isolated_reference_oscillators_show_no_musical_drift(measured):
    """Each usable single-oscillator window in the frozen set measures less
    than 0.05 cents rms of pitch variation."""
    got = [r for r in measured["reports"]
           if ("osc1_open" in r["label"] or "osc2_open" in r["label"]
               or r["label"].startswith("m5a/"))
           and "drift_rms_cents" in r]
    assert len(got) >= 6, [r["label"] for r in got]
    worst = max(r["drift_rms_cents"] for r in got)
    assert worst < 0.05, [(r["label"], r["drift_rms_cents"]) for r in got]


@needs_frozen
def test_the_full_phrase_is_not_called_drifting(measured):
    """The M1A phrase is two oscillators an octave apart THROUGH a moving
    filter, and reads 1.33 cents rms of pitch movement -- 100x the isolated
    oscillator's. The probe must not call that drift. This is the static-detune
    control of issue #138, on real reference audio rather than a synthetic
    stimulus."""
    got = [r for r in _by(measured, "m1a/phrase0/") if "drift_rms_cents" in r]
    assert got, "no phrase window produced a trajectory"
    for r in got:
        assert r["verdict"] != odp.DRIFTING, r
        assert r["drift_rms_cents"] > 0.3, r      # the movement is real and large


@needs_frozen
def test_m5a_saw_and_pulse_takes_refuse_as_a_drift_measurement(measured):
    """Issue #1433. The M5A manifest's one WAV is two takes, a saw and a pulse,
    each rendered by its own plugin instance and joined afterwards by inserted
    silence. Nothing ran between them, so their pitch difference bounds drift
    over no elapsed time at all. The pair must be REFUSED, must name both
    takes, and must carry neither a stability class nor a numerical bound."""
    _assert_refused_for_independent_captures(measured["across_note"][SAW_PULSE])


@needs_frozen
def test_the_refusal_is_about_the_captures_not_the_windows(measured):
    """What makes #1433 sharp: both windows are perfectly usable, so the
    refusal cannot have come from a window. It comes from the two captures,
    which each window names, and the difference between the takes is still
    quoted -- under its own name, as a comparison and as nothing else."""
    ac = measured["across_note"][SAW_PULSE]
    by = {r["label"]: r for r in measured["reports"]}
    saw, pulse = by[SAW], by[PULSE]
    assert _usable(saw) and _usable(pulse), (saw, pulse)
    assert saw["capture"] != pulse["capture"], (saw["capture"], pulse["capture"])
    assert ac["captures"] == [saw["capture"], pulse["capture"]], ac
    got = ac["between_captures"]["f0_cents_vs_first"]
    want = odp.cents(pulse["f0_measured_hz"], saw["f0_measured_hz"])
    assert got[0] == 0.0 and abs(got[1] - want) < 1e-12, (got, want)
    assert abs(abs(got[1]) - 0.0011) < 1e-4, got
    assert not (set(ac["between_captures"]) & set(DRIFT_BOUND)), ac


@needs_frozen
def test_control_the_former_note_only_comparison_is_reported_as_drift(measured):
    """CONTROL. It goes green only while the reinstated defect goes red. This
    is the comparison the driver made before #1433 -- every report at note 84,
    handed straight to the probe -- and on the same audio it still comes back
    STABLE with a span, because nothing in a report distinguishes one render
    from two. The last two lines are the point: the assertion written for
    #1433 rejects it, and this test fails the day that stops being true."""
    reports = measured["reports"]
    former = odp.across_note_drift([r for r in reports if r.get("note") == 84])
    assert former["labels"] == [SAW, PULSE], former
    assert former["verdict"] == odp.STABLE, former
    assert former["span_cents"] < 0.02, former
    assert "refusal" not in former and "floor_cents" in former, former
    with pytest.raises(AssertionError):
        _assert_refused_for_independent_captures(former)


@needs_frozen
def test_m1a_pairs_keep_their_own_outcomes(measured):
    """The three M1A pairs have their own provenance: the M1A producer queues
    the whole phrase before one render call, so each pair sits inside one
    render. Both MIDI-36 pairs refuse on a window that is too short. Only
    the isolated MIDI-48 pair yields a pitch difference."""
    across = measured["across_note"]
    for name in M1A_MIDI36_PAIRS:
        assert across[name]["verdict"] == odp.REFUSED, across[name]
        assert across[name]["refusal"] == "TOO_FEW_PERIODS", across[name]
    osc2 = across[M1A_OSC2_PAIR]
    assert osc2["verdict"] == odp.STABLE, osc2
    assert abs(osc2["span_cents"] - 0.0102) < 1e-4, osc2


@needs_frozen
def test_unusable_window_and_independent_captures_are_two_different_refusals(measured):
    """Both are REFUSED and neither has a bound, and a reader has to be able
    to tell them apart. A pair refused for a WINDOW names that window and
    carries its own code, inside one capture. A pair refused for its CAPTURES
    names no such window, because nothing is wrong with either."""
    across = measured["across_note"]
    by = {r["label"]: r for r in measured["reports"]}
    for name in M1A_MIDI36_PAIRS:
        bad = across[name]
        window = by[bad["labels"][bad["unusable_window"]]]
        assert window["verdict"] == odp.REFUSED, (name, window)
        assert bad["refusal"] == window["refusal"] == "TOO_FEW_PERIODS", (name, bad)
        assert len(set(bad["captures"])) == 1, (name, bad)
    independent = across[SAW_PULSE]
    assert independent["verdict"] == odp.REFUSED, independent
    assert not (set(independent) & set(DRIFT_BOUND)), independent
    assert "unusable_window" not in independent, independent
    assert all(_usable(by[label]) for label in independent["labels"]), independent
    assert len(set(independent["captures"])) == 2, independent


@needs_frozen
def test_clean_m1a_pair_measures_the_recorded_span(measured):
    """The isolated MIDI-48 pair, 4.0 s apart inside one render, is the one
    pair that yields a bound. Tighter than the pair-outcome test above."""
    ac = measured["across_note"][M1A_OSC2_PAIR]
    assert ac["verdict"] == odp.STABLE, ac
    assert ac["span_cents"] == pytest.approx(0.01018, abs=0.00001), ac


@needs_frozen
def test_every_across_note_pair_accounts_for_all_of_its_windows(measured):
    """#1405, on real reference audio. A pair the probe accepts is built from
    windows that each carry a reported verdict, a finite pitch and a floor. A
    pair refused for a window names a window that is itself REFUSED and carries
    that window's own code. A pair refused for its captures is a third kind
    (#1433): nothing is wrong with the windows, so it may name none, and it
    says which captures it spans. No refused pair carries a bound. The
    comparison used to drop a refused window and compare whatever was left, so
    a pair could be accepted, or report TOO_FEW_WINDOWS, without saying which
    of its windows it had used."""
    reports = measured["reports"]
    by_label = {r["label"]: r for r in reports}
    assert len(by_label) == len(reports)
    accepted = []
    for name, ac in measured["across_note"].items():
        wins = [by_label[label] for label in ac.get("labels", ())]
        assert ac.get("n_windows") == len(wins), (name, ac)
        assert len(wins) >= 2, (name, ac)
        assert ac.get("captures") == [w.get("capture") for w in wins], (name, ac)
        if ac["verdict"] == odp.REFUSED and "unusable_window" in ac:
            bad = wins[ac["unusable_window"]]
            assert bad["verdict"] == odp.REFUSED, (name, ac)
            assert ac["refusal"] == bad["refusal"], (name, ac, bad)
        elif ac["verdict"] == odp.REFUSED:
            assert ac["refusal"] in (mod.INDEPENDENT_CAPTURES,
                                     mod.CAPTURE_UNKNOWN), (name, ac)
        else:
            accepted.append(name)
            for r in wins:
                assert _usable(r), (name, r)
            assert len(set(ac["captures"])) == 1, (name, ac)
        if ac["verdict"] == odp.REFUSED:
            assert not (set(ac) & set(DRIFT_BOUND)), (name, ac)
    assert accepted, ("no pair accepted on the frozen audio, so the branch "
                      "above never ran")


@needs_frozen
def test_the_console_the_json_file_and_the_published_result_agree(
        measured, published_pairs, tmp_path, capsys):
    """Three places a reader meets this pair, and one thing said. The console
    line and the `--json` file must carry what `measure_miniv3` worked out,
    and so must the published `reference-drift-v1.json`: a report corrected in
    the tool and not on disk still tells every reader of the file that the
    pair is stable over elapsed time. The published pairs are compared on
    decisions and numbers, the latter to nine digits; prose and last digits
    belong to the machine that wrote the file."""
    out = tmp_path / "drift.json"
    assert mod.main(["--json", str(out)]) == 0
    console = capsys.readouterr().out
    line = next(l for l in console.splitlines() if SAW_PULSE in l)
    assert "REFUSED INDEPENDENT_CAPTURES" in line, line
    assert SAW in line and PULSE in line, line
    assert " span " not in line and "STABLE" not in line, line
    assert "between the two captures, and not drift" in console
    on_disk = json.load(open(out))["miniv3"]
    assert _first_difference(on_disk, measured) is None, \
        _first_difference(on_disk, measured)
    _assert_refused_for_independent_captures(published_pairs[SAW_PULSE])
    _assert_published_pairs_agree(published_pairs, measured)


def _assert_published_pairs_agree(published_pairs, measured):
    difference = _first_difference(published_pairs, measured["across_note"], rel_tol=1e-9)
    assert difference is None, difference


@needs_frozen
def test_publication_check_rejects_infinite_between_capture_pitch(published_pairs, measured):
    _assert_published_pairs_agree(published_pairs, measured)
    published_pairs[SAW_PULSE]["between_captures"]["f0_cents_vs_first"][1] = math.inf
    with pytest.raises(AssertionError, match=r"between_captures/f0_cents_vs_first\[1\]"):
        _assert_published_pairs_agree(published_pairs, measured)


@needs_frozen
def test_every_window_names_the_render_it_came_from(measured):
    """A capture is one render. In M1A that is one file: the producer builds a
    rig, queues the whole phrase and renders once per file, the three
    byte-identical phrase repeats included -- identical output is still three
    renders. In M5A it is one SEGMENT of the single WAV, because that WAV is
    an assembly of takes from separate instances. The expectation is built
    here from the manifests, not from the driver's own pickers."""
    m1a = json.load(open(os.path.join(M1A, "manifest.json")))
    m5a = json.load(open(os.path.join(M5A, "manifest.json")))
    want = {}
    for i, r in enumerate(m1a["renders"]):
        for n, ev in enumerate(m1a["events"]):
            want[f"m1a/phrase{i}/n{n}-midi{ev['note']}"] = r["file"]
    for key, c in m1a["controls"].items():
        semis = 12 if key == "osc2_open" else 0
        for n, ev in enumerate(m1a["events"]):
            want[f"m1a/{key}/n{n}-midi{ev['note'] + semis}"] = c["file"]
    for s, seg in enumerate(m5a["timeline"]["segments"]):
        for n, ev in enumerate(seg["midi_events"]):
            label = f"m5a/seg{s}-{seg['wave']}/n{n}-midi{ev['note']}"
            want[label] = f"{m5a['audio']['file']}#seg{s}"
    got = {r["label"]: r["capture"] for r in measured["reports"] if "capture" in r}
    assert got == want
    assert len(got) == len(measured["reports"]), "a window names no capture"


@needs_frozen
@pytest.mark.parametrize("digest", [None, "0" * 64], ids=["missing", "mismatched"])
def test_control_without_the_producer_pin_no_pair_will_call_itself_drift(
        digest, measured, tmp_path, monkeypatch):
    """Keep the original commits and audio. Change only the producer digest."""
    for case, section, producer in [
        (M1A, "provenance", "tools/measure_mono_m1a_reference.py"),
        (M5A, "source", "tools/measure_mono_m5a_reference.py"),
    ]:
        source = Path(case)
        dest = tmp_path / source.name
        dest.mkdir()
        for wav in source.glob("*.wav"):
            (dest / wav.name).symlink_to(wav)
        man = json.loads((source / "manifest.json").read_text())
        hashes = man[section]["files_sha256"]
        if digest is None:
            del hashes[producer]
        else:
            hashes[producer] = digest
        (dest / "manifest.json").write_text(json.dumps(man))

    monkeypatch.setattr(mod, "SCOREBOARD", tmp_path)
    without = mod.measure_miniv3()
    assert {name: ac.get("refusal") for name, ac in without["across_note"].items()} == {
        name: mod.CAPTURE_UNKNOWN for name in measured["across_note"]}
    stripped = [{k: v for k, v in r.items() if k != "capture"} for r in measured["reports"]]
    assert _first_difference(stripped, without["reports"]) is None, \
        _first_difference(stripped, without["reports"])
    assert all("capture" not in r for r in without["reports"])
    for name, ac in without["across_note"].items():
        assert ac["verdict"] == odp.REFUSED, (name, ac)
        assert not (set(ac) & set(DRIFT_BOUND)), (name, ac)
    assert measured["across_note"][M1A_OSC2_PAIR]["verdict"] == odp.STABLE


def test_the_comparison_the_pin_control_relies_on_still_sees_a_difference():
    """Its own control. Were `_first_difference` to answer None for everything,
    the pin control above would be vacuous and still green."""
    nan = float("nan")
    assert _first_difference([{"a": nan}], [{"a": nan}]) is None
    assert _first_difference([{"a": 1.0}], [{"a": 1.0 + 1e-12}]), "last digit"
    assert _first_difference([{"a": nan}], [{"a": 0.0}]), "NaN is not a number"
    assert _first_difference([{"a": 1.0}], [{"a": 1.0, "b": 2.0}]), "a key"
    assert _first_difference([{"a": 1.0}], [{"a": 1.0}, {"a": 1.0}]), "a window"
    near = 1.0 + 1e-12
    assert _first_difference([{"a": 1.0}], [{"a": near}]), "no tolerance is exact"
    assert _first_difference([{"a": 1.0}], [{"a": near}], rel_tol=1e-9) is None
    assert _first_difference([{"a": 1.0}], [{"a": 1.0 + 1e-6}], rel_tol=1e-9), \
        "a tolerance still has an edge"
    assert _first_difference([{"a": nan}], [{"a": 1.0}], rel_tol=1.0), \
        "no tolerance rescues a NaN"
    assert _first_difference(["STABLE"], ["DRIFTING"], rel_tol=1.0), \
        "a tolerance never forgives a decision"


def _window(label, hz, capture):
    """What the probe reads of a report, and nothing else."""
    return {"label": label, "verdict": odp.STABLE, "f0_nominal": 100.0,
            "sr": 48000, "f0_measured_hz": hz, "floor_cents": 0.001,
            "capture": capture}


def test_inside_one_capture_the_probe_is_asked_and_its_answer_stands():
    """And stated beside it: this is the input that defeats the gate. Two
    windows that NAME the same capture are compared, whatever the truth of
    that name, so a capture label that is wrong is wrong all the way through.
    The label is bounded, not checked: the driver writes it only for a
    manifest that pins the producer it was read from."""
    got = mod._across_pair([_window("a", 100.0, "one.wav"),
                            _window("b", 100.1, "one.wav")])
    assert got["verdict"] == odp.DRIFTING, got
    assert got["span_cents"] == pytest.approx(odp.cents(100.1, 100.0)), got
    assert got["captures"] == ["one.wav", "one.wav"], got


def test_across_two_captures_a_large_pitch_difference_is_still_not_drift():
    """The former bug in miniature: the same two pitches, now in two captures.
    The probe would call 1.7 cents DRIFTING; the gate refuses and quotes the
    difference as a between-capture comparison only."""
    got = mod._across_pair([_window("a", 100.0, "one.wav#seg0"),
                            _window("b", 100.1, "one.wav#seg1")])
    assert got["verdict"] == odp.REFUSED, got
    assert got["refusal"] == mod.INDEPENDENT_CAPTURES, got
    assert got["labels"] == ["a", "b"] and got["n_windows"] == 2, got
    assert got["captures"] == ["one.wav#seg0", "one.wav#seg1"], got
    assert "a" in got["refusal_detail"] and "b" in got["refusal_detail"], got
    assert not (set(got) & set(DRIFT_BOUND)), got
    assert got["between_captures"]["f0_cents_vs_first"] == pytest.approx(
        [0.0, odp.cents(100.1, 100.0)]), got


def test_two_windows_that_each_name_no_capture_do_not_thereby_share_one():
    """The shape of #134: matching missing values are not agreement. Two
    windows that both lack a capture compare equal on it, and a gate that
    asked only "are they the same?" would pass them to the probe."""
    a, b = _window("a", 100.0, None), _window("b", 100.1, None)
    for r in (a, b):
        del r["capture"]
    got = mod._across_pair([a, b])
    assert got["verdict"] == odp.REFUSED, got
    assert got["refusal"] == mod.CAPTURE_UNKNOWN, got
    assert got["labels"] == ["a", "b"] and got["window_without_capture"] == 0, got
    assert not (set(got) & set(DRIFT_BOUND)), got


@pytest.mark.parametrize("case, where, commit", [
    ("mono-m1a-miniv3", "provenance", "91123a05807213ad767f9d0fca80a2720b13feae"),
    ("mono-m5a-miniv3", "source", "899e86d68cf37bacf79188bce142b42ea2d13564"),
])
def test_each_frozen_manifest_still_pins_the_producer_its_capture_history_came_from(
        case, where, commit):
    """The capture history was read from the producer at this commit: M1A
    renders a whole file in one call, M5A a whole segment in one instance. If
    a manifest is re-cut at another commit that reading is worth nothing until
    it is done again, and the driver must stop lending captures until then."""
    man = json.load(open(os.path.join(ROOT, "docs", "scorecard", case,
                                      "manifest.json")))
    assert man[where]["commit"] == commit, (case, man[where]["commit"])
    assert mod._pinned(case, man), case
    assert not mod._pinned(case, {where: None})
    assert not mod._pinned(case, {where: {"commit": commit}})
    assert not mod._pinned(case, {where: {"commit": commit, "files_sha256": []}})
    man[where]["commit"] = "0" * 40
    assert not mod._pinned(case, man), case
    del man[where]["commit"]
    assert not mod._pinned(case, man), case


@needs_frozen
def test_a_tampered_hash_refuses_rather_than_measuring(tmp_path):
    """The frozen clip's hash is a precondition. A clip that is not the clip the
    manifest describes must refuse, because a number measured from it is not
    reproducible by anyone reading the manifest."""
    got = mod._clip("mono-m1a-miniv3", "control-osc1_open.wav", "0" * 64)
    assert isinstance(got, dict) and got["refusal"] == "CLIP_HASH", got
    got = mod._clip("mono-m1a-miniv3", "not-a-file.wav", None)
    assert isinstance(got, dict) and got["refusal"] == "CLIP_ABSENT", got


def _assert_clip_hash_refusals(report, prefix, suffixes):
    rows = _by(report, prefix)
    assert [r["label"] for r in rows] == [prefix + suffix for suffix in suffixes]
    for row in rows:
        assert row["verdict"] == odp.REFUSED, row
        assert row["refusal"] == "CLIP_HASH", row


@needs_frozen
@pytest.mark.parametrize(
    "digest",
    ["original", None, "", "omitted", []],
    ids=["mismatch", "null", "empty", "omitted", "non-string"],
)
def test_altered_osc2_wav_refuses_each_window_and_the_pair(scorecard, digest):
    case = scorecard / "mono-m1a-miniv3"
    manifest = case / "manifest.json"
    man = json.loads(manifest.read_text())
    entry = man["controls"]["osc2_open"]
    wav = case / entry["file"]
    data = bytearray(wav.read_bytes())
    assert hashlib.sha256(data).hexdigest() == entry["sha256"]
    data[-4] ^= 1
    wav.write_bytes(data)
    if digest == "omitted":
        del entry["sha256"]
    elif digest != "original":
        entry["sha256"] = digest
    manifest.write_text(json.dumps(man))

    report = mod.measure_miniv3()
    _assert_clip_hash_refusals(
        report, "m1a/osc2_open/", ["n0-midi48", "n1-midi55", "n2-midi48"]
    )
    pair = report["across_note"]["m1a-osc2-midi48-4.0s-apart"]
    assert pair["verdict"] == odp.REFUSED, pair
    assert pair["labels"] == ["m1a/osc2_open/n0-midi48", "m1a/osc2_open/n2-midi48"]
    assert "span_cents" not in pair
    assert any("drift_rms_cents" in r for r in _by(report, "m1a/osc1_open/"))


@needs_frozen
def test_each_render_and_m5a_audio_need_their_own_digest(scorecard):
    m1a = scorecard / "mono-m1a-miniv3" / "manifest.json"
    man = json.loads(m1a.read_text())
    man["renders"][1]["file"] = man["renders"][0]["file"]
    del man["renders"][1]["sha256"]
    m1a.write_text(json.dumps(man))
    m5a = scorecard / "mono-m5a-miniv3" / "manifest.json"
    man = json.loads(m5a.read_text())
    del man["audio"]["sha256"]
    m5a.write_text(json.dumps(man))

    report = mod.measure_miniv3()
    assert any("drift_rms_cents" in r for r in _by(report, "m1a/phrase0/"))
    _assert_clip_hash_refusals(
        report, "m1a/phrase1/", ["n0-midi36", "n1-midi43", "n2-midi36"]
    )
    _assert_clip_hash_refusals(
        report,
        "m5a/",
        [
            "seg0-saw/n0-midi84",
            "seg0-saw/n1-midi96",
            "seg1-pulse/n0-midi84",
            "seg1-pulse/n1-midi96",
        ],
    )


# ---- #1857: the exit status when no window answered --------------------------
#
# `main` printed every window's verdict, the across-note pairs and the two
# static refusals, wrote `--json` if asked, and returned 0. Read to the bottom
# on origin/main @ 528a228b (2026-11-28) and then run: with no frozen Mini V3
# audio in the checkout every window was MANIFEST_ABSENT, there was no
# across-note measurement, and the process exited 0 -- PASS, a RESULT.
#
# A window ANSWERED when its report's verdict is not REFUSED, whatever a
# refusal's code: audio the host cannot reach (CLIP_ABSENT, CLIP_HASH) and a
# window the probe cannot read (TOO_FEW_PERIODS) both left no drift estimate.
# An across-note pair cannot answer where its windows did not --
# `odp.across_note_drift` refuses unless every window carries a reported
# verdict -- so the windows alone decide. Surge XT and Diva are refused by
# construction (`refused_references`) and are not counted either way.
#
# Red-first on that tree, 2026-11-28: 3 of the 4 controls here failed -- both
# `main` cases with rc 0, the unit case for want of `windows_exit` -- and
# the control was green.
def _verdicts(out):
    """The lines that BEGIN with `REFUSED:`. A refused window's own row is
    indented, so it is never the run's verdict."""
    return [ln for ln in out.splitlines() if ln.startswith("REFUSED:")]


def _rows(out, needle):
    return [ln for ln in out.splitlines() if ln.startswith("   ") and needle in ln]


def test_the_window_count_is_on_the_verdict_whatever_the_refusals_code(capsys):
    silent = [dict(verdict=odp.REFUSED, refusal=code)
              for code in ("CLIP_ABSENT", "TOO_FEW_PERIODS", "ESTIMATOR_DISAGREE")]
    assert mod.windows_exit([]) == exit_contract.REFUSED
    assert mod.windows_exit(silent) == exit_contract.REFUSED
    out = capsys.readouterr().out
    assert "0 of 0 window(s) answered; no window was requested" in out, out
    assert "0 of 3 window(s) answered; CLIP_ABSENT x1, ESTIMATOR_DISAGREE x1, TOO_FEW_PERIODS x1" in out, out
    assert mod.windows_exit([silent[0], dict(silent[1], verdict=odp.STABLE)]) == exit_contract.PASS
    assert _verdicts(capsys.readouterr().out) == []


def test_no_frozen_audio_at_all_is_refused_not_a_pass(tmp_path, monkeypatch, capsys):
    """A checkout holding neither manifest: two MANIFEST_ABSENT reports, one
    per case, and the Surge and Diva refusals -- and nothing measured."""
    monkeypatch.setattr(mod, "SCOREBOARD", str(tmp_path / "no-scoreboard"))
    out = tmp_path / "drift.json"

    assert mod.main(["--json", str(out)]) == exit_contract.REFUSED
    console = capsys.readouterr().out
    assert len(_rows(console, "MANIFEST_ABSENT")) == 2, console
    assert "== Surge XT: REFUSED" in console and "== u-he Diva: REFUSED" in console, console
    verdicts = _verdicts(console)
    assert len(verdicts) == 1 and console.splitlines()[-1] == verdicts[0], console
    assert "0 of 2 window(s) answered" in verdicts[0] and "MANIFEST_ABSENT x2" in verdicts[0], verdicts[0]

    on_disk = json.load(open(out))
    assert [r["refusal"] for r in on_disk["miniv3"]["reports"]] == ["MANIFEST_ABSENT"] * 2, \
        "the --json record is still written on a refused run"
    assert [r["reference"] for r in on_disk["refused"]] == ["Surge XT", "u-he Diva"]


def _zero_every_digest(node):
    if isinstance(node, dict):
        return {k: ("0" * 64 if k == "sha256" else _zero_every_digest(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [_zero_every_digest(v) for v in node]
    return node


@needs_frozen
def test_frozen_audio_none_of_which_hashes_is_refused_not_a_pass(scorecard, capsys):
    """The audio is here and is not the audio the manifests describe: every
    window is CLIP_HASH, which is the precondition `_clip` exists to state."""
    for case in ("mono-m1a-miniv3", "mono-m5a-miniv3"):
        path = scorecard / case / "manifest.json"
        path.write_text(json.dumps(_zero_every_digest(json.loads(path.read_text()))))
    out = scorecard / "drift.json"

    assert mod.main(["--json", str(out)]) == exit_contract.REFUSED
    console = capsys.readouterr().out
    reports = json.load(open(out))["miniv3"]["reports"]
    assert reports and all(r["verdict"] == odp.REFUSED and r["refusal"] == "CLIP_HASH" for r in reports), reports
    verdicts = _verdicts(console)
    assert len(verdicts) == 1 and f"0 of {len(reports)} window(s) answered" in verdicts[0], console
    assert f"CLIP_HASH x{len(reports)}" in verdicts[0], verdicts[0]


@needs_frozen
def test_the_control_a_partly_refused_run_with_a_real_measurement_still_passes(scorecard, capsys):
    """THE CONTROL. The M5A manifest is gone, so its case is MANIFEST_ABSENT;
    the M1A windows are the real frozen audio and answer. Widening the rule to
    "any refusal" turns this red -- and so would counting Surge and Diva, which
    refuse on every host."""
    shutil.rmtree(scorecard / "mono-m5a-miniv3")

    assert mod.main([]) == exit_contract.PASS
    console = capsys.readouterr().out
    assert len(_rows(console, "MANIFEST_ABSENT")) == 1, console
    assert [ln.split()[1] for ln in _rows(console, "m1a/")
            if ln.split()[1] in (odp.STABLE, odp.PERIODIC, odp.DRIFTING)], console
    assert "== Surge XT: REFUSED" in console, console
    assert _verdicts(console) == [], console
