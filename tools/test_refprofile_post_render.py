#!/usr/bin/env python3
"""Issue #233: `refprofile.render` must re-check every pinned Surge parameter
AFTER every clip it renders, and derive `pins_held_after_render` from those
checks rather than writing it as a constant.

    .venv/bin/python -m pytest tools/test_refprofile_post_render.py -q

**No plugin and no host.** `reference_rigs.SurgeRig` is replaced by a stub
that subclasses the real `_Plugin` (so the real `check_pins` and
`pinned_report` run) over a fake parameter table. `dawdreamer` is replaced by
an empty module. Each test decides what the stub's parameters do after the
Nth render, which is the one thing a real Surge cannot be made to do on
demand.

The cases, from plan074 section P:

  * a clean stub passes, and the record says every clip was checked
  * a pinned READBACK that changes after the first render refuses at that clip
  * a change that happens only after a LATER clip refuses at that clip, and
    that clip is never written to the cache
  * the documented Classic -> Audio In rename at 259/260/264/265, with the
    readbacks unchanged, passes and is recorded as an alias
  * the same rename with a WRONG readback refuses
  * the alias is per index: another index renamed, or an Audio In name on the
    wrong index, refuses

Started red: on the pre-#233 renderer every refusal case here renders all
sixteen clips and returns a profile with `pins_held_after_render: True`.
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
sys.path.insert(0, str(ROOT / "audition"))

import reference_rigs as rr                                         # noqa: E402
import refprofile as rp                                             # noqa: E402

#: The Audio In names Surge reports after the first post-construction render,
#: measured in #231 (`tools/f1_level_capture.AUDIO_IN_NAMES`). Written out here
#: rather than imported so the test states the expectation independently.
AUDIO_IN = {259: "A Osc 1 Audio In Channel", 260: "A Osc 1 Audio In Gain",
            264: "A Osc 1 Low Cut", 265: "A Osc 1 High Cut"}

N_CLIPS = len(rp.clip_specs())


class _StubP:
    """The part of a dawdreamer processor that refprofile and _Plugin read."""

    def __init__(self, names: dict, texts: dict, n: int):
        self.names, self.texts, self.n = names, texts, n

    def get_parameter_name(self, i):
        return self.names.get(int(i), f"param {i}")

    def get_parameter_text(self, i):
        return self.texts.get(int(i), "0")

    def get_parameter(self, i):
        return 0.0

    def get_parameters_description(self):
        return [{}] * self.n

    def set_parameter(self, i, v):
        pass


def _make_stub(mutate, path: str):
    """A SurgeRig stand-in. `mutate(names, texts, n_render)` runs after every
    clip render, n_render counting from 1."""

    class StubSurge(rr._Plugin):
        name = "surge"
        PINS = rr.SurgeRig.PINS
        I = rr.SurgeRig.I
        TONE_SETTLE_S, TONE_WINDOW_S = rr.SurgeRig.TONE_SETTLE_S, rr.SurgeRig.TONE_WINDOW_S

        def __init__(self, subtype="Type 2"):
            self.block = rr.BLOCK
            self.path = path
            names = {i: n for i, _v, n, _w in self.PINS}
            texts = {i: w for i, _v, _n, w in self.PINS if w is not None}
            self.p = _StubP(names, texts, 1 + max(names))
            self.renders = 0

        def _after_render(self, cut):
            self.renders += 1
            self.p.texts[self.I["f1_cut"]] = f"{cut:.2f} Hz"
            mutate(self.p.names, self.p.texts, self.renders)

        def tone_render(self, freqs, cut, res, amp):
            y = amp * np.sin(2 * np.pi * 1000.0 * np.arange(480) / rr.SR)
            parts = [(0, 480, float(f)) for f in freqs]
            self._after_render(cut)
            return y, parts, float(cut)

        def drive_tone(self, f, cut, res, amp):
            y = amp * np.sin(2 * np.pi * f * np.arange(480) / rr.SR)
            self._after_render(cut)
            return y

    return StubSurge


@pytest.fixture
def stub_render(tmp_path, monkeypatch):
    """Run `rp.render` against a stub whose parameters `mutate` changes."""
    pdir = tmp_path / "refprofile"
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)
    monkeypatch.setattr(rp, "CACHE", pdir / "cache")
    monkeypatch.setattr(rp, "PROFILE_JSON", pdir / "profile.json")
    bundle = tmp_path / "Surge XT.vst3"
    bundle.mkdir()
    monkeypatch.setattr(rr, "PATH_SURGE", str(bundle))
    monkeypatch.setitem(sys.modules, "dawdreamer", types.ModuleType("dawdreamer"))

    def run(mutate=lambda names, texts, n: None):
        monkeypatch.setattr(rr, "SurgeRig", _make_stub(mutate, str(bundle)))
        return rp.render(probe_disqualified=False)

    run.cache = pdir / "cache"
    return run


def _rename_to_audio_in(names, texts, n):
    """What #231 measured: the names flip after the first render, the readbacks
    do not."""
    names.update(AUDIO_IN)


def _qual(prof):
    return prof["rigs"]["surge-type2"]["qualification"]


# ---------------------------------------------------------------------------
def test_a_clean_stub_renders_and_every_clip_is_checked(stub_render):
    prof = stub_render()
    assert len(prof["clips"]) == N_CLIPS
    q = _qual(prof)
    assert q["pins_held_after_render"] is True
    chk = q["post_render_check"]
    assert chk["clips_checked"] == N_CLIPS
    assert chk["clips_rendered"] == N_CLIPS
    assert chk["name_aliases_observed"] == []
    for cid, c in prof["clips"].items():
        assert c["pins_after_render"]["held"] is True, cid


def test_a_readback_that_changes_after_the_first_render_refuses(stub_render):
    first = rp.clip_specs()[0]["clip_id"]

    def drift(names, texts, n):
        texts[234] = "12.00 %"                      # A Osc Drift moved

    with pytest.raises(rp.Refused, match="after rendering") as e:
        stub_render(drift)
    assert first in str(e.value)
    assert "234" in str(e.value)
    assert not (stub_render.cache / (first + ".wav")).exists()


def test_a_change_after_a_later_clip_refuses_at_that_clip(stub_render):
    specs = rp.clip_specs()
    k = 7                                           # the 7th render, 1-based
    bad_clip = specs[k - 1]["clip_id"]

    def late(names, texts, n):
        if n >= k:
            texts[19] = "Phaser"                    # FX A1 turned on

    with pytest.raises(rp.Refused, match="after rendering") as e:
        stub_render(late)
    assert bad_clip in str(e.value)
    for s in specs[:k - 1]:
        assert s["clip_id"] not in str(e.value)
        assert (stub_render.cache / (s["clip_id"] + ".wav")).exists(), s["clip_id"]
    assert not (stub_render.cache / (bad_clip + ".wav")).exists(), \
        "the clip that failed its check was written to the cache anyway"


def test_the_documented_audio_in_rename_with_unchanged_readbacks_passes(stub_render):
    prof = stub_render(_rename_to_audio_in)
    q = _qual(prof)
    assert q["pins_held_after_render"] is True
    chk = q["post_render_check"]
    assert chk["clips_checked"] == N_CLIPS
    assert sorted(chk["name_aliases_observed"]) == [259, 260, 264, 265]


@pytest.mark.parametrize("idx", sorted(AUDIO_IN))
def test_the_audio_in_rename_with_a_wrong_readback_refuses(stub_render, idx):
    def rename_and_move(names, texts, n):
        _rename_to_audio_in(names, texts, n)
        texts[idx] = "13.75 Hz"                     # e.g. a high cut engaged

    with pytest.raises(rp.Refused, match="after rendering") as e:
        stub_render(rename_and_move)
    assert str(idx) in str(e.value)


def test_a_rename_on_an_index_outside_the_four_refuses(stub_render):
    def rename_277(names, texts, n):
        _rename_to_audio_in(names, texts, n)
        names[277] = "A Osc 2 High Cut"

    with pytest.raises(rp.Refused, match="after rendering") as e:
        stub_render(rename_277)
    assert "277" in str(e.value)


def test_the_permitted_aliases_are_exactly_the_four_measured_ones():
    """The renderer's alias table and #231's capture tool's must not drift."""
    import f1_level_capture as cap
    assert {i: v[1] for i, v in rp.SURGE_AUDIO_IN_ALIASES.items()} == AUDIO_IN
    assert {i: v[1] for i, v in rp.SURGE_AUDIO_IN_ALIASES.items()} == cap.AUDIO_IN_NAMES
    pinned = {i: n for i, _v, n, _w in rr.SurgeRig.PINS}
    assert all(pinned[i] == v[0] for i, v in rp.SURGE_AUDIO_IN_ALIASES.items())


def test_an_audio_in_name_on_the_wrong_index_refuses(stub_render):
    def swapped(names, texts, n):
        _rename_to_audio_in(names, texts, n)
        names[259], names[260] = AUDIO_IN[260], AUDIO_IN[259]

    with pytest.raises(rp.Refused, match="after rendering") as e:
        stub_render(swapped)
    assert "259" in str(e.value)
