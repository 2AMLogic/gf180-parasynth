#!/usr/bin/env python3
"""Every reader of a FREE ring's pitch goes through `ring_estimator` (issue #703).

    python3 -m pytest model/test_ring_estimator_readers.py -q

`ring_estimator.ring_frequency` is the one policy for what counts as a
measurement of a free ring (#548). These readers used to call
`am.dominant_frequency` directly, so a below-onset DEAD-BAND limit cycle (a
steady 6e-4 FS, 20 LSB, at 1131 Hz on the shipped filter) or a plain decay was
reported as an `f_osc` and flowed into tracking-error figures.

The controls are synthetic records whose answer is known by construction -- a
steady 6e-4 FS tone, an exponential decay, a sustained tone -- never the filter
being measured. Each routed call-site family must REFUSE the first two (a
refusal / `None`, never a frequency) and still answer the third.

Not routed, by reading (verification-rules rule 8 -- why no defeating input):
  * `rig_qualification.py` (`dominant_frequency` over the band of a commanded
    NOTE): a note-driven render, not a free ring.
  * `promoted_measures`, `measure_conga_body_spread`, `audio_measure`,
    `test_808_acceptance`, `test_moog_acceptance`: driven / struck voices.
    Assumed from names and docstrings -- skimmed, not traced.
  * `tools/measure_mono_m1a_reference.py`, `tools/measure_mono_m5a_reference.py`
    (Mini V3 free rings -- `_measure_open_cutoff`, M1A's three-ring phrase):
    STILL on `am.dominant_frequency`, now permanently. Both files are hashed
    into the Mini V3 records (`suite-v3.json` `sources_sha256`, the
    attack-context model report `source_sha256`), and #1048 froze those
    records: they cannot be regenerated, so the files cannot change either.
    Routing them for V3 (#706) is cancelled; Mini V4 references get their own
    reader when that suite produces records (#1052). The guard at the bottom
    of this file turns any edit of the two files into a named failure here,
    instead of the 19 stale-record refusals deep in the tools suite that the
    first attempt (PR #750) produced.
"""
import hashlib
import json
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "audition"))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import ladder_candidates as lc                                      # noqa: E402
import reference_compare as rc                                      # noqa: E402
import reference_integrity as ri                                    # noqa: E402
import reference_rigs as rr                                         # noqa: E402
import ring_estimator as re_                                        # noqa: E402
import sound_report as sr                                           # noqa: E402
from dsp import SR                                                  # noqa: E402



def _tone(f, seconds, amp):
    t = np.arange(int(seconds * SR)) / SR
    return amp * np.sin(2 * math.pi * f * t)


def dead_band(f=1131.0, seconds=0.8):
    """The fixed-point dead-band limit cycle: steady 6e-4 FS (20 LSB)."""
    return _tone(f, seconds, 6e-4)


def decay(f=400.0, seconds=0.8):
    """An ordinary below-onset decay: -70 dB across the window."""
    t = np.arange(int(seconds * SR)) / SR
    return 0.2 * np.exp(-t / 0.0115) * np.sin(2 * math.pi * f * t)


def sustained(f=1000.0, seconds=0.8):
    return _tone(f, seconds, 0.2)


BAD = [pytest.param(dead_band, id="dead-band"), pytest.param(decay, id="decay")]


def test_the_controls_are_what_they_claim_to_be():
    """The fixtures themselves: the estimator refuses both and answers the
    sustained one. Without this a control could 'pass' on a bad fixture."""
    for bad in (dead_band(), decay()):
        with pytest.raises(re_.Refused):
            re_.ring_frequency(bad, SR)
    assert abs(re_.ring_frequency(sustained(), SR) - 1000.0) < 1.0


@pytest.mark.parametrize("make", BAD)
def test_reference_compare_tracking_refuses(make):
    assert rc._tracking_f_osc(make()) is None


def test_reference_compare_tracking_still_answers_a_sustained_ring():
    assert abs(rc._tracking_f_osc(sustained()) - 1000.0) < 1.0


class _FakeDev:
    def __init__(self, ring_fn):
        self.ring_fn = ring_fn

    def ring(self, knob, res, seconds=0.5):
        return self.ring_fn(knob, seconds)


@pytest.mark.parametrize("make", BAD)
def test_calibrate_knob_refuses_instead_of_substituting_0_hz(make):
    dev = _FakeDev(lambda knob, seconds: make(seconds=seconds))
    with pytest.raises(re_.Refused):
        rc.calibrate_knob(dev, "miniv3", 800.0, res=0.98)


def test_calibrate_knob_still_bisects_a_sustained_ring():
    dev = _FakeDev(lambda knob, seconds: sustained(100.0 + 1900.0 * knob, seconds))
    knob = rc.calibrate_knob(dev, "miniv3", 800.0, res=0.98, iters=12)
    assert abs((100.0 + 1900.0 * knob) - 800.0) < 5.0


@pytest.mark.parametrize("make", BAD)
def test_ladder_candidates_cents_refuses(make):
    with pytest.raises(lc.Refused):
        lc._cents_of_ring(make(), 1131.0)


def test_ladder_candidates_cents_still_answers_a_sustained_ring():
    assert abs(lc._cents_of_ring(sustained(), 1000.0)) < 1.0


class _FakeLadder:
    def __init__(self, ring_fn):
        self.ring_fn = ring_fn

    def ring(self, cut, res, seconds=0.5):
        # the integrity fake's ring_fn takes (cut, res, seconds); the others (cut, seconds)
        try:
            return self.ring_fn(cut, seconds)
        except TypeError:
            return self.ring_fn(cut, res, seconds)


@pytest.mark.parametrize("make", BAD)
def test_sound_report_track_spread_is_none_on_a_non_ring(make, monkeypatch):
    fake = _FakeLadder(lambda cut, seconds: make(f=cut, seconds=seconds))
    monkeypatch.setattr(sr, "_ladder", lambda ctx: fake)
    assert sr.m_track_spread({}) is None


def test_sound_report_track_spread_still_answers_sustained_rings(monkeypatch):
    fake = _FakeLadder(lambda cut, seconds: sustained(cut * 1.01, seconds))
    monkeypatch.setattr(sr, "_ladder", lambda ctx: fake)
    spread = sr.m_track_spread({})
    assert spread is not None and spread < 0.5


@pytest.mark.parametrize("make", BAD)
def test_reference_integrity_tracking_refuses(make, monkeypatch):
    monkeypatch.setattr(rr, "OurLadder",
                        lambda: _FakeLadder(lambda cut, res, seconds: make(f=cut, seconds=seconds)),
                        raising=True)
    assert all(v is None for v in ri._tracking_once("ours", {}).values())


def test_reference_integrity_tracking_still_answers_sustained_rings(monkeypatch):
    class L:
        def ring(self, cut, res, seconds=0.8):
            return sustained(cut * 1.01, seconds)
    monkeypatch.setattr(rr, "OurLadder", L, raising=True)
    errs = ri._tracking_once("ours", {})
    assert all(v is not None and abs(v - 1.0) < 0.05 for v in errs.values()), errs


# ---- #706 re-scoped per #1048: the V3 ring-reader sources are frozen ---------
# The two Mini V3 free-ring readers above stay on `am.dominant_frequency`
# because the records that hash them are frozen (#1048): regeneration is
# cancelled, so the files cannot change. This guard turns an edit of either
# file into a named failure at this site instead of the stale-record refusals
# deep in the tools suite that PR #750's first attempt produced (19 red
# tests). Defeating input -- editing the recorded digest alongside the file:
# M1A's record (`suite-v3.json`) is covered by the committed reference-suite
# freeze (tools/check_reference_freeze.py). M5A's record is
# `mono-attack-context/model/report.json`, which the model comparison tool
# REGENERATES, writing the current reader's digest into it -- an expectation
# read from there accepts any reader bytes the regenerator once produced, so
# a paired edit (reader + regenerated report) would pass (the paired-edit
# defeat demonstrated by the Judge on PR #750). M5A's expectation is
# therefore a committed digest literal below, independent of both the report
# and the manifest; the control beside it replays the paired edit and
# requires the named refusal.

_V3_READER_RECORDS = {
    "tools/measure_mono_m1a_reference.py":
        ("docs/scorecard/mono-m1a-miniv3/attack-qualification/suite-v3.json",
         "sources_sha256"),
}

#: The M5A reader's expected digest is committed HERE, not read from
#: `mono-attack-context/model/report.json` -- see the block comment above for
#: why the report cannot carry the expectation.
_M5A_READER = "tools/measure_mono_m5a_reference.py"
_M5A_READER_FROZEN_SHA256 = (
    "5c8b5a3fa1e4f8668d1ec5baacbadf016fb68fc515b2bd658e667c763a9c4a74")

_V3_READER_FROZEN_MESSAGE = (
    "{source} changed since its Mini V3 record was written. The records are "
    "frozen (#1048) and cannot be regenerated, so this reader cannot be "
    "re-routed through ring_estimator for V3 -- edit neither side. New "
    "references go through the Mini V4 suite (#1052), whose reader routes "
    "through ring_estimator from the start.")


_V3_READER_ROOT = os.path.dirname(HERE)


def _assert_v3_reader_frozen(source, expected_digest, root=None):
    root = _V3_READER_ROOT if root is None else root
    with open(os.path.join(root, source), "rb") as fh:
        actual = hashlib.sha256(fh.read()).hexdigest()
    assert actual == expected_digest, _V3_READER_FROZEN_MESSAGE.format(source=source)


@pytest.mark.parametrize("source,record", sorted(_V3_READER_RECORDS.items()))
def test_v3_ring_reader_source_is_frozen(source, record):
    record_path, key = record
    with open(os.path.join(_V3_READER_ROOT, record_path)) as fh:
        digest = json.load(fh)[key][source]
    _assert_v3_reader_frozen(source, digest)


def test_v3_m5a_reader_source_is_frozen():
    """M5A's expectation is the committed literal, not report.json: the report
    is regenerated by tools/compare_mono_attack_context.py, which writes the
    current reader's digest into it, so it cannot carry a frozen expectation."""
    _assert_v3_reader_frozen(_M5A_READER, _M5A_READER_FROZEN_SHA256)


def test_v3_m5a_paired_edit_is_still_caught(tmp_path):
    """The paired-edit defeat: edit the reader AND move the model report with
    it -- the exact bytes tools/compare_mono_attack_context.py writes into the
    report on regeneration. The guard must refuse with the named M5A message,
    because the expectation no longer comes from the report."""
    with open(os.path.join(_V3_READER_ROOT, _M5A_READER), "rb") as fh:
        frozen = fh.read()
    edited = frozen + b"\n# paired-edit probe\n"
    reader = tmp_path / _M5A_READER
    reader.parent.mkdir(parents=True)
    reader.write_bytes(edited)
    moved = hashlib.sha256(edited).hexdigest()
    report = tmp_path / "docs/scorecard/mono-attack-context/model/report.json"
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({"source_sha256": {_M5A_READER: moved}}))
    # the paired state is genuine: the report now agrees with the edited bytes
    with open(report) as fh:
        assert json.load(fh)["source_sha256"][_M5A_READER] == moved
    with pytest.raises(AssertionError,
                       match=r"tools/measure_mono_m5a_reference\.py changed"):
        _assert_v3_reader_frozen(_M5A_READER, _M5A_READER_FROZEN_SHA256,
                                 root=str(tmp_path))
