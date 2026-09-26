#!/usr/bin/env python3
"""The reference drift measurement, reproduced from frozen audio with no plugin.

Two of these tests are the only EXTERNAL validation this probe has: the two
frozen manifests carry pitch offsets measured by a different session with a
different estimator, and `model/osc_drift_probe.py` has to reproduce them. A
probe validated only against signals it generated itself is calibrated on its
own model (CLAUDE.md), which is exactly what these two avoid.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "model"))

import measure_osc_drift_reference as mod                             # noqa: E402
import osc_drift_probe as odp                                         # noqa: E402

M1A = os.path.join(ROOT, "docs", "scorecard", "mono-m1a-miniv3")
M5A = os.path.join(ROOT, "docs", "scorecard", "mono-m5a-miniv3")
have_frozen = os.path.exists(os.path.join(M1A, "control-osc1_open.wav")) \
    and os.path.exists(os.path.join(M5A, "m5a-miniv3-raw.wav"))
needs_frozen = pytest.mark.skipif(
    not have_frozen, reason="frozen Mini V3 audio absent from this checkout")


@pytest.fixture(scope="module")
def measured():
    return mod.measure_miniv3()


def _by(measured, prefix):
    return [r for r in measured["reports"] if r["label"].startswith(prefix)]


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
    """THE reference finding. Every single-oscillator window in the frozen set
    wanders by under 0.05 cents rms -- 30x below the smallest drift this issue
    would consider audible. The references as frozen do not supply a drift
    magnitude; they bound it."""
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
def test_across_note_baselines_bound_long_term_drift(measured):
    """Same commanded note, 4.0 s and 13.6 s apart in one continuous render."""
    ac = measured["across_note"]["m5a-midi84-13.6s-apart"]
    assert ac["verdict"] == odp.STABLE, ac
    assert ac["span_cents"] < 0.02, ac
    ac = measured["across_note"]["m1a-osc2-midi48-4.0s-apart"]
    assert ac["span_cents"] < 0.05, ac


@needs_frozen
def test_a_tampered_hash_refuses_rather_than_measuring(tmp_path):
    """The frozen clip's hash is a precondition. A clip that is not the clip the
    manifest describes must refuse, because a number measured from it is not
    reproducible by anyone reading the manifest."""
    got = mod._clip("mono-m1a-miniv3", "control-osc1_open.wav", "0" * 64)
    assert isinstance(got, dict) and got["refusal"] == "CLIP_HASH", got
    got = mod._clip("mono-m1a-miniv3", "not-a-file.wav", None)
    assert isinstance(got, dict) and got["refusal"] == "CLIP_ABSENT", got
