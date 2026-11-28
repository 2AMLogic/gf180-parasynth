#!/usr/bin/env python3
"""Issue #2066, site 2: `f1_level_capture.worker` must REFUSE a render that
holds no sample, instead of raising `ValueError` at `np.abs(y).max()`.

    .venv/bin/python -m pytest tools/test_f1_level_capture_empty.py -q

THE FAILURE MODE THIS FILE EXISTS FOR
-------------------------------------
The worker's guard on each condition's render was one line:

    if not np.all(np.isfinite(y)) or float(np.abs(y).max()) <= 1e-9:
        raise Refused(f"{cond}: non-finite or silent render")

An empty buffer is finite -- it holds no sample that is not a number -- so the
first half is False and the second half runs, and `np.abs(y).max()` raises
`ValueError: zero-size array to reduction operation maximum which has no
identity` on zero samples. A SILENT render was refused correctly; only the
empty buffer was missing. Same shape as #2044 (`qualify_modeld_pedalboard`)
and site 1 of #2066 (`refprofile.render`).

Started red: on the pre-#2066 worker both empty-render cases below raise
`ValueError` out of `worker` as a traceback.

**No plugin and no host.** The worker's plugin and host boundaries are
replaced -- `dawdreamer` by an empty module, the Surge rig by a stand-in whose
`tone_render` hands back the REAL stepped-tone stimulus (`reference_rigs
.tone_train`) with `swap` applied: an identity filter whose output is whatever
the test chose. The projection that compares the open filter against the
frozen one, and `write_clip`, are the real ones, so the control shows the
whole worker still captures a take and not merely that it returns.
"""
from __future__ import annotations

import pathlib
import sys
import types

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))

import reference_rigs as rr                                         # noqa: E402
import refprofile as rp                                             # noqa: E402
import f1_level_capture as cap                                      # noqa: E402

AMP = cap.AMPS[0]
FREQS = rp._freqs()
OPEN_CLIP = cap.CONDITIONS["open20k"][1]

#: The real stimulus and its windows, built once: what the real `tone_render`
#: feeds the filter, and so what an identity filter hands back.
STIMULUS, PARTS = rr.tone_train(FREQS, AMP, rr.SurgeRig.TONE_SETTLE_S,
                                rr.SurgeRig.TONE_WINDOW_S)
_REAL_TONE_PROJECT = rr.SurgeRig.tone_project

#: Pinned here rather than read from the module, so a rewording that merged
#: the empty refusal back into the silent one fails.
EMPTY = "empty buffer"
NON_FINITE_OR_SILENT = "non-finite or silent render"


class _FakeP:
    """The part of a dawdreamer processor the worker reads."""

    def get_latency_samples(self):
        return 0


def _fake_surge(swap):
    """A `SurgeRig` stand-in for `worker`: no pins, the two filter texts the
    worker insists on, the REAL coherent projection, and a `tone_render` that
    hands back `swap(stimulus, cut)`."""

    class FakeSurge:
        block = rr.BLOCK
        PINS = ()
        I = {"f1_type": "f1_type", "f1_sub": "f1_sub", "f1_cut": "f1_cut"}
        tone_project = _REAL_TONE_PROJECT

        def __init__(self, subtype="Type 2"):
            self.p = _FakeP()

        def text(self, idx):
            return {"f1_type": "LP Vintage Ladder", "f1_sub": "Type 2"}.get(idx, "0")

        def pinned_report(self):
            return {}

        def tone_render(self, freqs, cut, res, amp):
            return np.asarray(swap(STIMULUS, cut)), PARTS, float(cut)

    return FakeSurge


def _files(out):
    return sorted(p.name for p in out.iterdir())


@pytest.fixture
def worker_with(tmp_path, monkeypatch):
    """Run `cap.worker` over the identity-filter stand-in, with `swap(y, cut)`
    deciding what the condition commanded at `cut` Hz hands back. The frozen
    open-filter clip the live render is compared against is the same stimulus,
    so the comparison reads 0 dB and nothing but `swap` can refuse."""
    out = tmp_path / "take1"
    out.mkdir()
    monkeypatch.setitem(sys.modules, "dawdreamer", types.ModuleType("dawdreamer"))
    monkeypatch.setattr(cap, "assert_plugin_identity", lambda: {"stub": True})
    monkeypatch.setattr(cap, "frozen_rig",
                        lambda: {"host": {"block": rr.BLOCK, "sr": rr.SR}})
    monkeypatch.setattr(rp, "load_profile",
                        lambda: {"clips": {OPEN_CLIP: {"freqs_hz": FREQS}}})
    monkeypatch.setattr(rp, "load_clip",
                        lambda cid, prof=None: (STIMULUS, None,
                                                {"parts": PARTS, "amp": AMP}))

    def run(swap):
        monkeypatch.setattr(rr, "SurgeRig", _fake_surge(swap))
        return cap.worker(AMP, 1, out)

    run.out = out
    return run


def test_an_empty_render_is_REFUSED_and_no_take_is_written(worker_with):
    """Every condition's render comes back with no samples. Until #2066 that
    escaped `worker` as `ValueError: zero-size array to reduction operation
    maximum which has no identity`, a traceback and not a refusal.

    What is asserted, and why each one could otherwise pass by accident:

      * `cap.Refused` -- the worker's own refusal, not a `ValueError`.
      * `open20k:` -- the FIRST condition, named: the refusal is about this
        condition's render and not a later one's.
      * the empty-buffer words, and NOT `non-finite or silent render`: that is
        what a silent render is refused with, and an empty buffer has no
        sample to be at zero. The two must not share a message.
      * nothing in the take directory -- no `.wav`, no `.part`, no `take.json`
        -- because the refusal comes before `write_clip`."""
    with pytest.raises(cap.Refused) as e:
        worker_with(lambda y, cut: y[:0])
    why = str(e.value)
    assert why.startswith("open20k:"), why
    assert EMPTY in why
    assert NON_FINITE_OR_SILENT not in why
    assert _files(worker_with.out) == []


def test_an_empty_render_after_good_conditions_refuses_at_that_condition(
        worker_with):
    """The guard is per condition, inside the loop. The open filter renders
    and is captured; the 250 Hz condition hands back nothing and is refused
    by name; the two conditions after it are never reached, and the take --
    written only after every condition -- is not."""
    with pytest.raises(cap.Refused) as e:
        worker_with(lambda y, cut: y[:0] if cut == 250.0 else y)
    why = str(e.value)
    assert why.startswith("cut250:"), why
    assert EMPTY in why
    assert "open20k" not in why
    assert _files(worker_with.out) == ["open20k.wav"], _files(worker_with.out)
    assert not (worker_with.out / "take.json").exists()


def test_the_empty_refusal_and_the_silent_refusal_are_different_words(worker_with):
    """The acceptance criterion, stated directly: through the same stand-in,
    a render with no sample and a render whose samples are all zero are
    refused with different reasons, and only the first says empty."""
    with pytest.raises(cap.Refused) as empty:
        worker_with(lambda y, cut: y[:0])
    with pytest.raises(cap.Refused) as silent:
        worker_with(lambda y, cut: np.zeros_like(y))
    assert str(empty.value) != str(silent.value)
    assert EMPTY in str(empty.value) and EMPTY not in str(silent.value)
    assert NON_FINITE_OR_SILENT in str(silent.value)


@pytest.mark.parametrize("name, swap", [
    ("all zeros", lambda y, cut: np.zeros_like(y)),
    ("one zero sample", lambda y, cut: np.zeros(1)),
    ("all NaN", lambda y, cut: np.full_like(y, np.nan)),
], ids=["all zeros", "one zero sample", "all NaN"])
def test_silent_and_non_finite_renders_keep_their_own_refusal(worker_with, name,
                                                             swap):
    """The new guard must not absorb the refusal that already worked. A silent
    render has samples and they are all zero; a NaN render is not audio.
    Both were refused by the one line, in its one combined message, and both
    still are -- and neither comes out as "empty"."""
    with pytest.raises(cap.Refused) as e:
        worker_with(swap)
    why = str(e.value)
    assert why.startswith("open20k:"), why
    assert NON_FINITE_OR_SILENT in why, f"{name}: refused for the wrong reason: {why}"
    assert EMPTY not in why
    assert _files(worker_with.out) == []


def test_a_sounding_render_still_captures_the_take_through_the_same_swap(
        worker_with):
    """The control for the tests above: the same stand-in, and a `swap` that
    hands the stimulus back untouched. The harness must not be what refuses --
    a guard that refuses every render passes all of them -- so all four
    conditions are captured at the stimulus's amplitude, each written to
    disk, and the take is recorded."""
    rec = worker_with(lambda y, cut: y)
    assert list(rec["clips"]) == list(cap.CONDITIONS)
    for cond, c in rec["clips"].items():
        assert c["frames"] == len(STIMULUS), cond
        assert c["peak"] == pytest.approx(AMP, abs=1e-6), cond
        assert c["rms"] > 0.0, cond
        assert (worker_with.out / f"{cond}.wav").exists(), cond
    assert (worker_with.out / "take.json").exists()
    assert _files(worker_with.out) == sorted(
        [f"{cond}.wav" for cond in cap.CONDITIONS] + ["take.json"])
