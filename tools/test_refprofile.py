#!/usr/bin/env python3
"""The frozen reference profile hands audio to measurements, so it gets verified.

    .venv/bin/python -m pytest tools/test_refprofile.py -q

**No plugin and no host.** Every test here builds a throwaway profile over a
throwaway cache, so what is tested is this tool's handling of each way a
reference can stop being the reference -- on any machine, including the ones
that will never have Surge installed.

THE FAILURE MODE THIS FILE EXISTS FOR
-------------------------------------
A frozen reference's only failure mode that matters is **audio that is not what
the committed profile describes, read as though it were.** That is worse than a
missing reference, because a missing one produces a stated no-verdict and a
drifted one produces a number, with the profile's provenance block attached to
it, that nobody can reproduce.

So the cases below are the drifts:

  * the cache is absent entirely (the normal case on most hosts) -- REFUSED,
    and REFUSED is not FAIL
  * the file is there and its bytes hash differently -- refused, never read
  * the file is there and is the right length but a byte was changed -- the
    size check alone would pass this one, which is why there is a hash
  * the file is there and is SHORTER than the profile says -- the
    zero-byte-`.wav` hazard `tools/refaudio_fetch.py` was built around
  * the file is there and is silent
  * the file is there, is NOT silent, and is far below the level every
    consumer reads it at -- #481, the case a 1e-9 silence floor passed
  * the file is there at the wrong sample rate, or with the wrong frame count
  * the clip id is not in the profile at all
  * a write that is interrupted leaves no partial file behind

And one structural test: the stepped-tone RENDER and the PROJECTION that reads
it were split apart in `model/reference_rigs.py` so a profile could cache the
audio. `tone_gain_db` must still be the measurement it was, and that is pinned
here on a closed-form signal with no plugin in the path.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import sys

import numpy as np
import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

import refprofile as rp                                             # noqa: E402

SR = rp.SR


# ===========================================================================
# a throwaway profile over a throwaway cache
# ===========================================================================
def _tone(hz=200.0, seconds=0.5, amp=0.25, sr=SR):
    """An integer number of periods, so the coherent projection is exact."""
    n = int(round(seconds * hz)) * int(round(sr / hz))
    return amp * np.sin(2 * math.pi * hz * np.arange(n) / sr)


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """A one-clip profile whose cache is real audio on disk."""
    pdir = tmp_path / "refprofile"
    cache = pdir / "cache"
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)
    monkeypatch.setattr(rp, "CACHE", cache)
    monkeypatch.setattr(rp, "PROFILE_JSON", pdir / "profile.json")

    y = _tone()
    rel = pathlib.Path("cache") / "fake" / "clip.wav"
    dest = pdir / rel
    rp.write_clip(dest, y)
    prof = {
        "schema": rp.SCHEMA, "sr": SR, "probe_level_dbfs": rp.PROBE_LEVEL_DBFS,
        "built": {"worktree": {"commit": "deadbee", "dirty": False}},
        "rigs": {"fake": {"qualified": True}},
        "clips": {"fake/clip": {
            "rig": "fake", "kind": "tone_train", "why": "a test tone",
            "file": str(rel), "sr": SR, "frames": int(len(y)), "dtype": "float32",
            "bytes": dest.stat().st_size, "sha256": rp.file_sha256(dest),
            "amp": 0.25, "freqs_hz": [200.0],
            "parts": [[0, int(len(y)), 200.0]],
        }},
    }
    (pdir / "profile.json").write_text(json.dumps(prof))
    return prof, dest


def test_a_clip_that_matches_the_profile_reads_back(profile):
    _, dest = profile
    y, sr, meta = rp.load_clip("fake/clip")
    assert sr == SR
    assert len(y) == meta["frames"]
    assert np.abs(y).max() == pytest.approx(0.25, abs=1e-6)


def test_no_cache_at_all_is_refused_and_not_a_failure(tmp_path, monkeypatch):
    """The normal answer on most hosts in this fleet, and it must not look like
    a broken reference. REFUSED (2), never FAIL (1)."""
    pdir = tmp_path / "refprofile"
    pdir.mkdir()
    monkeypatch.setattr(rp, "PROFILE_DIR", pdir)
    monkeypatch.setattr(rp, "CACHE", pdir / "cache")
    monkeypatch.setattr(rp, "PROFILE_JSON", pdir / "profile.json")
    (pdir / "profile.json").write_text(json.dumps(
        {"schema": rp.SCHEMA, "clips": {"a/b": {"file": "cache/a/b.wav"}}}))
    code, lines = rp.verify()
    assert code == rp.REFUSED
    assert any("no reference-audio cache" in ln for ln in lines)


def test_a_clip_missing_from_the_cache_is_refused_with_the_way_to_fix_it(profile):
    _, dest = profile
    dest.unlink()
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "not in the cache" in str(e.value)
    assert "tools/refprofile_restore.py" in str(e.value)


def test_a_clip_that_is_not_in_the_profile_is_refused(profile):
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/NOPE")
    assert "is not a clip" in str(e.value)


def test_one_changed_byte_is_refused_although_the_size_still_matches(profile):
    """The whole reason there is a hash and not only a size. A plugin update
    that renders the same number of samples differently changes no byte count
    at all."""
    prof, dest = profile
    b = bytearray(dest.read_bytes())
    b[-3] ^= 0x01
    dest.write_bytes(bytes(b))
    assert dest.stat().st_size == prof["clips"]["fake/clip"]["bytes"]
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "hashes" in str(e.value)
    assert "not the audio the profile describes" in str(e.value)


def test_a_truncated_clip_is_refused_on_size_before_it_is_ever_opened(profile):
    """`tools/refaudio_fetch.py` exists because a zero-byte `.wav` opens fine
    in every downstream tool. A short one is the same hazard with a pulse."""
    _, dest = profile
    b = dest.read_bytes()
    dest.write_bytes(b[: len(b) // 2])
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "bytes" in str(e.value)


def test_an_empty_clip_is_refused(profile):
    _, dest = profile
    dest.write_bytes(b"")
    with pytest.raises(rp.Refused):
        rp.load_clip("fake/clip")


def _refreeze(prof, dest, y):
    """Put `y` in the cache and make the profile describe it, so the only thing
    left that can refuse the clip is its CONTENT. Every byte-level check --
    size, hash -- passes after this by construction."""
    rp.write_clip(dest, np.asarray(y, dtype=np.float32))
    prof["clips"]["fake/clip"]["bytes"] = dest.stat().st_size
    prof["clips"]["fake/clip"]["sha256"] = rp.file_sha256(dest)
    rp.PROFILE_JSON.write_text(json.dumps(prof))


def test_a_silent_clip_is_refused(profile):
    """A reference that is silence is not a reference. Model D renders exactly
    this, headlessly, which is why the check is here and not assumed away.

    Since #481 the GATE is the level floor, but the refusal must still say
    *silent* when that is what it is: "the host returned zeros" and "the level
    is wrong" have different causes and only one of them is an apparatus
    fault."""
    prof, dest = profile
    _refreeze(prof, dest, np.zeros(prof["clips"]["fake/clip"]["frames"]))
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "silent" in str(e.value)
    assert "silence floor" in str(e.value)
    assert "level floor" in str(e.value)


def test_a_clip_below_the_level_floor_but_not_silent_is_refused(profile):
    """**The #481 control.** A clip peaking at 1e-7 is NOT silent by the 1e-9
    floor this check used to carry: it passed, it hashes correctly, and it is
    140 dB below the level every consumer reads this profile at. Every number
    measured off it would be the path's own truncation noise wearing the
    profile's provenance block.

    It must now refuse, and it must refuse AS A LEVEL DEFECT -- not as silence,
    which it is not."""
    prof, dest = profile
    y = _tone(amp=1e-7)
    assert np.abs(y).max() > 1e-9, "the control must clear the OLD floor to be a control"
    _refreeze(prof, dest, y)

    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    why = str(e.value)
    assert "level floor" in why, why
    assert "1e-06" in why, f"the refusal must name the floor it enforced: {why}"
    assert "not usable as a reference" in why, why
    assert "silent" not in why, f"1e-7 is not silence and must not be reported as it: {why}"


def test_a_clip_at_the_level_floor_is_refused_and_one_above_it_loads(profile):
    """The boundary, in both directions, because `<=` and `<` are one character
    apart and a floor nothing is ever exactly at is a floor nobody has tested.

    `float32(1e-6)` lands a hair BELOW 1e-6 (9.99999997e-07), which is itself
    the point: the comparison is on the audio as stored, not on the float the
    test asked for."""
    prof, dest = profile
    n = prof["clips"]["fake/clip"]["frames"]

    at = np.zeros(n)
    at[n // 2] = np.float32(rp.LEVEL_FLOOR)
    _refreeze(prof, dest, at)
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "level floor" in str(e.value)

    above = np.zeros(n)
    above[n // 2] = np.float32(2 * rp.LEVEL_FLOOR)
    _refreeze(prof, dest, above)
    y, sr, _ = rp.load_clip("fake/clip")        # must NOT raise
    assert float(np.abs(y).max()) > rp.LEVEL_FLOOR


def test_the_freeze_and_read_floors_are_the_same_number():
    """#481's other half. A freeze-time floor below the read-time one would let
    `--render` write, hash and commit a clip that every consumer refuses
    forever after -- a render that reports success and is already unusable. The
    two are one constant on purpose.

    Asserted from the SOURCE because the freeze path needs a plugin host and
    `dawdreamer`, which no machine running this file is required to have. A
    weak assertion that runs everywhere beats a strong one that is skipped on
    every host that would notice the divergence."""
    src = pathlib.Path(rp.__file__).read_text()
    assert src.count("<= LEVEL_FLOOR") >= 2, \
        "the read-time and the freeze-time gate must both be LEVEL_FLOOR"
    assert "<= 1e-9" not in src, "no bare silence floor should survive #481"
    assert rp.SILENCE_FLOOR < rp.LEVEL_FLOOR


def test_the_wrong_sample_rate_is_refused(profile):
    prof, dest = profile
    rp.write_clip(dest, _tone(), sr=44100)
    prof["clips"]["fake/clip"]["bytes"] = dest.stat().st_size
    prof["clips"]["fake/clip"]["sha256"] = rp.file_sha256(dest)
    rp.PROFILE_JSON.write_text(json.dumps(prof))
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "44100" in str(e.value)


def test_the_wrong_frame_count_is_refused(profile):
    prof, dest = profile
    rp.write_clip(dest, _tone(seconds=0.25))
    prof["clips"]["fake/clip"]["bytes"] = dest.stat().st_size
    prof["clips"]["fake/clip"]["sha256"] = rp.file_sha256(dest)
    rp.PROFILE_JSON.write_text(json.dumps(prof))
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "frames" in str(e.value)


def test_verify_reports_a_drifted_clip_as_FAIL_not_as_REFUSED(profile):
    """The two exit codes are not interchangeable: REFUSED means nothing was
    attempted and says nothing about the reference; FAIL means the reference on
    this disk is not the one in the profile, and somebody has to look."""
    prof, dest = profile
    b = bytearray(dest.read_bytes())
    b[-3] ^= 0x01
    dest.write_bytes(bytes(b))
    code, lines = rp.verify()
    assert code == rp.FAIL
    assert any(ln.startswith("FAIL") for ln in lines)


def test_verify_is_OK_when_the_cache_matches(profile):
    code, lines = rp.verify()
    assert code == rp.OK
    assert lines[-1].startswith("1 verified, 0 failed")


def test_a_profile_from_a_future_schema_is_refused(profile):
    prof, _ = profile
    prof["schema"] = "refprofile/99"
    rp.PROFILE_JSON.write_text(json.dumps(prof))
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert "schema" in str(e.value)


def test_no_profile_at_all_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(rp, "PROFILE_JSON", tmp_path / "nope.json")
    with pytest.raises(rp.Refused):
        rp.load_profile()


def test_an_interrupted_write_leaves_no_partial_file(tmp_path, monkeypatch):
    """A `.part` that survives is a file the next run could mistake for audio."""
    dest = tmp_path / "x" / "clip.wav"

    def boom(*a, **kw):
        raise OSError("disk full")
    import scipy.io.wavfile as wf
    monkeypatch.setattr(wf, "write", boom)
    with pytest.raises(OSError):
        rp.write_clip(dest, _tone())
    assert not dest.exists()
    assert not list(dest.parent.glob("*.part"))


def test_render_refuses_rather_than_writing_an_empty_profile(monkeypatch):
    """A host with no plugin host must produce a refusal, never a profile with
    fewer clips in it -- a shrunken profile is indistinguishable from a real
    one that got smaller on purpose."""
    real = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def no_dawdreamer(name, *a, **kw):
        if name == "dawdreamer":
            raise ImportError("no module named dawdreamer")
        return real(name, *a, **kw)
    monkeypatch.setattr("builtins.__import__", no_dawdreamer)
    with pytest.raises(rp.Refused) as e:
        rp.render()
    assert "no plugin host" in str(e.value)


# ===========================================================================
# the per-(rig, host, capability) verdict (#136)
# ===========================================================================
#
# One boolean per rig forced the MOST RESTRICTIVE capability to gate every other
# one, and that cost 32 cases: Mini V3's uncalibrated envelope knobs were read as
# blocking a waveform comparison and a sustained-filter comparison that need no
# envelope timing at all. These tests are about the two ways the fix can be
# undone -- the map collapsing back into the boolean, and `None` collapsing into
# `False` one axis down from where #123 fixed it.

def test_every_rig_records_every_capability():
    """A missing entry is not the same as a False, and `verdict_for` refuses one
    rather than guessing -- so an author who adds a rig and forgets the map gets
    a refusal at every call site instead of a wrong answer at one. This is the
    gate that says the omission is caught here instead."""
    for name, v in rp.RIG_VERDICTS.items():
        caps = v.get("capabilities")
        assert isinstance(caps, dict), f"{name} has no capability map"
        assert set(caps) == set(rp.CAPABILITIES), \
            f"{name} records {sorted(caps)}, the axis is {sorted(rp.CAPABILITIES)}"
        for c, q in caps.items():
            assert q is True or q is False or q is None, f"{name}/{c} is {q!r}"


def test_every_capability_verdict_says_which_measurement_produced_it():
    """`test_every_rig_the_profile_rejects_says_why` one axis down. A verdict
    with no reason is what this table exists to prevent: "we did not use Model D"
    and "Model D cannot be used" are different facts and only one of them tells
    the next person not to try."""
    for name, v in rp.RIG_VERDICTS.items():
        why = v.get("capability_why") or {}
        for c in rp.CAPABILITIES:
            assert why.get(c), f"{name}/{c} has a verdict and no reason"


def test_the_capability_map_is_not_collapsible_to_the_rig_boolean():
    """**The regression this issue is about.** If every capability always agreed
    with its rig's overall verdict, the map would be a more expensive way to
    write one boolean and the next simplification pass would be right to delete
    it. At least one rig must disagree with itself -- today Mini V3 does, which
    is the entire reason #136 exists."""
    disagree = [(n, c) for n, v in rp.RIG_VERDICTS.items()
                for c, q in (v.get("capabilities") or {}).items()
                if q is not v.get("qualified")]
    assert disagree, ("every capability agrees with its rig's overall verdict, so "
                      "the map carries no information the boolean did not. #136 "
                      "was filed because Mini V3 disagrees with itself")


def test_an_overall_yes_never_hides_a_measured_no():
    """The flattening in the other direction. `qualified is True` is the
    conservative AND -- "this rig's clips may be frozen, full stop" -- so a rig
    carrying a known-bad capability must not read as qualified overall, or
    `qualified_rigs()` is an overclaim."""
    for name, v in rp.RIG_VERDICTS.items():
        if v.get("qualified") is not True:
            continue
        bad = [c for c, q in (v["capabilities"]).items() if q is False]
        assert not bad, f"{name} is qualified overall and {bad} is measured False"


def test_a_rig_with_no_verdict_at_all_claims_no_capability():
    """`None` at the rig level means nobody has run it. Nothing measured means
    nothing qualified, so a `True` under a `None` rig would be a measurement that
    exists for a rig that has not been measured."""
    for name, v in rp.RIG_VERDICTS.items():
        if v.get("qualified") is not None:
            continue
        yes = [c for c, q in v["capabilities"].items() if q is True]
        assert not yes, f"{name} has no rig-level verdict and claims {yes}"


def test_a_rig_disqualified_at_the_apparatus_level_is_false_everywhere():
    """A capability-scoped query must not be able to route around an apparatus
    that answers nothing. Model D under dawdreamer renders peak 0.0 and Diva was
    found unlicensed and inserting clicks; both are written out as a measured
    False on every capability rather than left blank, so
    `qualified_rigs(capability=...)` cannot pick them up."""
    for name in ("modeld", "diva"):
        caps = rp.RIG_VERDICTS[name]["capabilities"]
        assert set(caps.values()) == {False}, f"{name}: {caps}"
    for c in rp.CAPABILITIES:
        got = rp.qualified_rigs(capability=c)
        assert "modeld" not in got and "diva" not in got, (c, got)


def test_a_capability_query_finds_a_rig_its_rig_level_verdict_rejects():
    """**#136's payoff, as an assertion.** Mini V3's rig-level verdict is False
    -- on envelope timing alone -- and it must still come back as qualified for
    waveform, pitch and the sustained filter, or the refactor bought nothing."""
    assert rp.RIG_VERDICTS["miniv3"]["qualified"] is False
    assert "miniv3" not in rp.qualified_rigs()
    for c in ("pitch", "waveform", "sustained_filter"):
        assert "miniv3" in rp.qualified_rigs(capability=c), c
    assert "miniv3" not in rp.qualified_rigs(capability="envelope_timing")
    assert rp.qualified_rigs(capability="envelope_timing") == []


def test_qualified_rigs_still_answers_the_old_question_unchanged():
    """The additive half of the contract: a caller that only cares whether a rig
    can be a reference full stop keeps the answer it had."""
    assert rp.qualified_rigs() == ["surge-type2"]
    assert rp.qualified_rigs(host="pedalboard") == []
    assert rp.qualified_rigs(host="dawdreamer") == ["surge-type2"]


def test_qualified_rigs_composes_host_and_capability():
    """#123's axis and #136's axis are independent, and the key is the triple.
    Mini V3 is qualified for waveform UNDER DAWDREAMER and the same question
    under pedalboard must not pick it up -- it is silent there."""
    assert rp.qualified_rigs(host="dawdreamer", capability="waveform") == \
        ["miniv3", "surge-type2"]
    assert rp.qualified_rigs(host="pedalboard", capability="waveform") == []


def test_verdict_for_with_a_capability_reports_that_capabilitys_verdict():
    v = rp.verdict_for("miniv3", "dawdreamer", "waveform")
    assert v["qualified"] is True
    assert v["capability"] == "waveform"
    assert v["rig_qualified"] is False, "the rig-level AND must survive, not be overwritten"
    assert "pulse:47.9%" in v["why"], "the why must be the CAPABILITY's, not the rig's"

    e = rp.verdict_for("miniv3", "dawdreamer", "envelope_timing")
    assert e["qualified"] is False
    assert e["rig_qualified"] is False


def test_verdict_for_without_a_capability_is_byte_for_byte_the_old_answer():
    """Additive, per the comment the old `RIG_VERDICTS` carried: every existing
    caller keeps working untouched."""
    v = rp.verdict_for("modeld", "dawdreamer")
    assert v["qualified"] is False
    assert "capability" not in v and "rig_qualified" not in v
    assert v["why"] == rp.RIG_VERDICTS["modeld"]["why"]


def test_an_unmeasured_capability_is_REFUSED_and_never_returned_as_False():
    """`None` must not collapse into `False` one axis down from where #123 fixed
    it. Surge's pitch has never been measured here, and a caller that reads that
    as "Surge is not qualified for pitch" would publish a rejection nobody
    measured."""
    assert rp.capability_verdict("surge-type2", "dawdreamer", "pitch") is None
    with pytest.raises(rp.Refused) as e:
        rp.require_capability("surge-type2", "dawdreamer", "pitch")
    why = str(e.value)
    assert "NO VERDICT" in why, why
    assert "nobody has measured it" in why, why
    assert "NOT QUALIFIED" not in why, f"an unmeasured capability is not a rejection: {why}"


def test_a_capability_nobody_recorded_is_REFUSED_and_not_a_False():
    """A rig whose map is missing an entry entirely -- the shape an added
    capability or an added rig has before somebody fills it in. The refusal must
    say the verdict is ABSENT; returning False here would invent one."""
    patched = dict(rp.RIG_VERDICTS["miniv3"])
    patched["capabilities"] = {k: v for k, v in patched["capabilities"].items()
                               if k != "waveform"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setitem(rp.RIG_VERDICTS, "miniv3", patched)
        with pytest.raises(rp.Refused) as e:
            rp.verdict_for("miniv3", "dawdreamer", "waveform")
    why = str(e.value)
    assert "NO RECORDED VERDICT" in why, why
    assert "waveform" in why and "miniv3" in why, why


def test_a_capability_that_is_not_on_the_axis_is_REFUSED():
    with pytest.raises(rp.Refused) as e:
        rp.verdict_for("miniv3", "dawdreamer", "envelope-timing")
    assert "not a capability" in str(e.value)
    with pytest.raises(rp.Refused):
        rp.qualified_rigs(capability="loudness")


def test_the_host_refusal_still_fires_before_the_capability_is_even_read():
    """The triple is (rig, host, capability) and the host is still part of the
    KEY, not a field beside it: asking Mini V3 about waveform under pedalboard
    must refuse on the host, because under pedalboard Mini V3 is silent."""
    with pytest.raises(rp.Refused) as e:
        rp.verdict_for("miniv3", "pedalboard", "waveform")
    assert "scoped to host" in str(e.value)


def test_a_capability_refusal_names_the_capability_and_what_does_work():
    """**#136's item 3.** "Mini V3 is not qualified" sent a reader looking for a
    missing qualification run and parked 32 cases. The refusal has to name the
    capability, and naming what the rig IS good for is what tells the next person
    which cases were never blocked."""
    with pytest.raises(rp.Refused) as e:
        rp.require_capability("miniv3", "dawdreamer", "envelope_timing")
    why = str(e.value)
    assert "envelope_timing" in why, why
    assert "NOT QUALIFIED" in why, why
    assert "waveform" in why and "sustained_filter" in why, \
        f"the refusal must name what IS qualified: {why}"
    assert why != "miniv3 is not qualified"


def test_require_capability_returns_the_verdict_when_it_holds():
    v = rp.require_capability("miniv3", "dawdreamer", "sustained_filter")
    assert v["qualified"] is True and v["capability"] == "sustained_filter"


def test_capability_prose_is_split_out_of_the_hashed_file():
    """#129 one axis down. `split_profile` pops TOP-LEVEL keys only, so a `why`
    nested inside the `capabilities` map would ride into `profile.json` -- the
    file that IS a hashed input -- and correcting a capability's sentence would
    start invalidating measurements again."""
    assert "capability_why" in rp.RIG_PROSE_KEYS
    combined = {
        "schema": rp.SCHEMA, "clips": {}, "rigs": {
            "a": {"qualified": False, "why": "a rig reason", "host": "x",
                  "capabilities": {"pitch": True},
                  "capability_why": {"pitch": "a capability reason"}}},
    }
    evidence, notes = rp.split_profile(combined)
    assert "capability_why" not in evidence["rigs"]["a"]
    assert evidence["rigs"]["a"]["capabilities"] == {"pitch": True}, \
        "the VERDICTS are evidence and stay in the hashed file"
    assert notes["rigs"]["a"]["capability_why"]["pitch"] == "a capability reason"
    assert rp.merge_notes(evidence, notes) == combined


def test_no_capability_verdict_carries_prose_nested_beside_it():
    """The gate for the mistake the test above describes: a future author adding
    `capabilities={"pitch": {"qualified": True, "why": "..."}}` would put prose
    back inside the hashed file, and `split_profile` could not reach it."""
    for name, v in rp.RIG_VERDICTS.items():
        for c, q in v["capabilities"].items():
            assert not isinstance(q, (dict, str)), \
                (f"{name}/{c} is {q!r}: a capability verdict is a bool or None, "
                 f"and its prose belongs in capability_why where the #129 split "
                 f"can reach it")


def test_the_capability_matrix_prints_a_None_as_no_verdict_and_never_as_NO():
    """The renderer defect that leaked #123's whole point straight back out, one
    axis down: `"yes" if q else "NO"` announces a rejection nobody measured in
    the one place a reader goes to find out what is rejected."""
    rows = {r["rig"]: r for r in rp.capability_matrix()}
    assert rp.verdict_word(rows["surge-type2"]["capabilities"]["pitch"]) == "no verdict"
    assert rp.verdict_word(rows["miniv3"]["capabilities"]["envelope_timing"]) == "NO"
    assert rp.verdict_word(rows["miniv3"]["capabilities"]["waveform"]) == "yes"
    # an unrecorded entry must render as `?`, not as a verdict
    assert rp.verdict_word("unrecorded") == "?"


def test_cmd_capabilities_answers_with_no_profile_and_no_cache(tmp_path, monkeypatch):
    """Most hosts in this fleet have neither the plugins nor the cache. The
    matrix is read from `RIG_VERDICTS`, so it must still answer there -- a tool
    that refuses to say what it knows is a tool nobody consults."""
    monkeypatch.setattr(rp, "PROFILE_JSON", tmp_path / "nope.json")
    assert rp.main(["--capabilities"]) == rp.OK


# ===========================================================================
# the split in model/reference_rigs.py, pinned with no plugin in the path
# ===========================================================================
import reference_rigs as rr                                           # noqa: E402


class _StubFilter:
    """Just enough of a rig for `SurgeRig.tone_render` to run: a one-pole
    low-pass with a known corner, and no plugin anywhere.

    The three methods under test are taken straight off `SurgeRig`, so this is
    the shipping code running over a stub host, not a copy of it."""
    name = "stub"
    TONE_SETTLE_S, TONE_WINDOW_S, TONE_PRE_S = 0.06, 0.20, 0.30
    tone_render = rr.SurgeRig.tone_render
    tone_project = staticmethod(rr.SurgeRig.tone_project)
    tone_gain_db = rr.SurgeRig.tone_gain_db

    def __init__(self, fc=300.0):
        self.fc = fc

    def set_point(self, cut, res):
        return float(cut)

    def render(self, x, seconds, note_at=0.02, note_len=None):
        a = math.exp(-2 * math.pi * self.fc / SR)
        y = np.zeros(len(x))
        s = 0.0
        for i, v in enumerate(x):
            s = a * s + (1 - a) * v
            y[i] = s
        return y


def test_tone_gain_db_equals_the_projection_of_tone_render():
    """`tone_gain_db` was split into a render and a projection so a profile
    could freeze the audio. It has to be the same measurement afterwards, and
    the two halves have to compose back into it exactly."""
    dev = _StubFilter()
    freqs = [100.0, 300.0, 900.0]
    direct = dev.tone_gain_db(freqs, 300.0, 0.0, 0.25)
    y, parts, read = dev.tone_render(freqs, 300.0, 0.0, 0.25)
    viaparts = dev.tone_project(y, parts, 0.25, "stub")
    assert read == 300.0
    np.testing.assert_allclose(direct, viaparts, rtol=0, atol=0)


def test_tone_project_reads_a_one_poles_known_gain():
    """Ground truth: a one-pole at its own corner is -3.01 dB, and an octave
    above it is -7.0 dB. If the projection or the windowing were wrong this is
    where it shows."""
    dev = _StubFilter(fc=300.0)
    y, parts, _ = dev.tone_render([300.0, 600.0], 300.0, 0.0, 0.25)
    g = dev.tone_project(y, parts, 0.25, "stub")
    assert g[0] == pytest.approx(-3.01, abs=0.15)
    assert g[1] == pytest.approx(-6.99, abs=0.20)


def test_the_parts_windows_lie_inside_the_render():
    dev = _StubFilter()
    y, parts, _ = dev.tone_render([100.0, 400.0], 300.0, 0.0, 0.25)
    for i0, nw, _f in parts:
        assert 0 <= i0 and i0 + nw <= len(y)


# ===========================================================================
# the committed profile itself, where it is present
# ===========================================================================
def test_the_committed_profile_is_internally_consistent():
    """Read the real `refprofile/profile.json` -- no cache needed. Every clip
    must name a rig the profile calls qualified, and every tone-train clip's
    analysis windows must lie inside the frames it says it has."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    assert prof["schema"] == rp.SCHEMA
    assert prof["clips"], "a profile with no clips is not a profile"
    for cid, c in prof["clips"].items():
        assert prof["rigs"][c["rig"]]["qualified"] is True, cid
        assert len(c["sha256"]) == 64, cid
        assert c["bytes"] > 0 and c["frames"] > 0, cid
        assert c["sr"] == prof["sr"], cid
        if c["kind"] == "tone_train":
            assert len(c["parts"]) == len(c["freqs_hz"]), cid
            for i0, nw, _f in c["parts"]:
                assert 0 <= i0 and i0 + nw <= c["frames"], cid


def test_every_rig_the_profile_rejects_says_why():
    """#129: `why` no longer lives in `profile.json` itself -- it is in the
    `profile-notes.json` sibling, which `load_profile()` merges back in. Going
    through the real accessor here, rather than a raw `json.loads`, is the
    point: this test must keep passing however the two files are shaped
    underneath it."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = rp.load_profile()
    for name, r in prof["rigs"].items():
        assert r.get("why"), f"{name} has a verdict and no reason"
        if not r["qualified"]:
            assert name not in {c["rig"] for c in prof["clips"].values()}


def test_the_committed_profile_json_carries_no_prose_the_split_moved_out():
    """The other half of #129: `profile.json` -- the file that is actually a
    hashed input (`tools/run_case.py`'s `DEPENDENCIES`/`MODEL_INPUTS`) -- must
    not have regrown a prose key the split removed, or correcting a sentence
    silently starts invalidating measurements again."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    for name, r in prof.get("rigs", {}).items():
        for k in rp.RIG_PROSE_KEYS:
            assert k not in r, (name, k)
    for name, d in prof.get("disqualified", {}).items():
        for k in rp.DISQUALIFIED_PROSE_KEYS:
            assert k not in d, (name, k)
    for cid, c in prof.get("clips", {}).items():
        for k in rp.CLIP_PROSE_KEYS:
            assert k not in c, (cid, k)


def test_the_committed_profile_notes_carries_the_prose_the_split_moved_out():
    """The split's other direction: every rejected rig and every clip must have
    SOMEWHERE its `why` lives, or the prose was dropped rather than moved."""
    notes_path = ROOT / "refprofile" / "profile-notes.json"
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists() or not notes_path.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    notes = json.loads(notes_path.read_text())
    assert notes["schema"] == rp.SCHEMA_NOTES
    for name in prof.get("rigs", {}):
        assert notes["rigs"].get(name, {}).get("why"), name
    for cid in prof.get("clips", {}):
        assert notes["clips"].get(cid, {}).get("why"), cid


def test_split_profile_and_merge_notes_are_exact_inverses():
    """The property the whole split depends on: separating evidence from prose
    and folding the prose back must reproduce the original dict exactly, or
    some field is quietly duplicated or lost on every `--render`."""
    combined = {
        "schema": rp.SCHEMA, "clips": {
            "a/b": {"rig": "a", "why": "a reason", "readback_note": "a caption",
                     "sha256": "deadbeef"}},
        "rigs": {"a": {"qualified": True, "why": "a rig reason", "host": "x"}},
        "disqualified": {"a": {"verdict": "not usable", "peak": 0.0}},
    }
    evidence, notes = rp.split_profile(combined)
    assert "why" not in evidence["rigs"]["a"]
    assert "why" not in evidence["clips"]["a/b"]
    assert "readback_note" not in evidence["clips"]["a/b"]
    assert "verdict" not in evidence["disqualified"]["a"]
    assert evidence["rigs"]["a"]["host"] == "x"
    assert evidence["clips"]["a/b"]["sha256"] == "deadbeef"
    assert evidence["disqualified"]["a"]["peak"] == 0.0
    assert notes["rigs"]["a"]["why"] == "a rig reason"
    assert notes["clips"]["a/b"]["why"] == "a reason"
    assert notes["clips"]["a/b"]["readback_note"] == "a caption"
    assert notes["disqualified"]["a"]["verdict"] == "not usable"
    assert rp.merge_notes(evidence, notes) == combined


def test_merge_notes_is_read_only_and_never_touches_disk(profile):
    """`load_profile()` merges in memory only. A hand-edited verdict in
    `profile-notes.json` must never rewrite `profile.json`'s bytes as a side
    effect of merely reading it."""
    prof, _dest = profile
    before = rp.PROFILE_JSON.read_bytes()
    notes = {"schema": rp.SCHEMA_NOTES, "rigs": {"fake": {"why": "a stub reason"}},
              "disqualified": {}, "clips": {}}
    rp.notes_path_for(rp.PROFILE_JSON).write_text(json.dumps(notes), encoding="utf-8")
    merged = rp.load_profile()
    assert merged["rigs"]["fake"]["why"] == "a stub reason"
    assert rp.PROFILE_JSON.read_bytes() == before


def test_the_profile_records_what_produced_it():
    """A reference whose provenance is 'a plugin, once' is not frozen. Plugin
    identity, the rig's qualification, the commit, and a hash of the uncommitted
    tree all have to be on the record."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    b = prof["built"]
    assert b["worktree"]["commit"] != "?"
    assert "uncommitted_sha256" in b["worktree"]
    assert b["rig_source_sha256"] and b["builder_sha256"]
    for name, r in prof["rigs"].items():
        if not r["qualified"]:
            continue
        assert r["plugin"]["present"] is True, name
        assert r["plugin"]["binary_sha256"], name
        q = r["qualification"]
        if b["builder_sha256"] == "4bbd8e90c0a58e52a2f38d68174e71c8a73cd62180b2c6b018687a0d61136f77":
            # #233: the frozen profile's renderer wrote the flag as a constant
            # and never checked after a render. It must not be read as having
            # made the per-clip check; a re-render replaces this branch.
            assert not rp.post_render_checked(r), name
        else:
            assert rp.post_render_checked(r), name
            chk = q["post_render_check"]
            n = sum(c["rig"] == name for c in prof["clips"].values())
            assert q["pins_held_after_render"] is True, name
            assert chk["clips_checked"] == chk["clips_rendered"] == n, name
        assert q["n_pins"] > 0, name
        assert r["parameters_after_setup"], name


def test_every_committed_clip_clears_the_level_floor():
    """#481 raised the floor by three orders of magnitude, and "nothing in the
    profile is anywhere near it" stopped being self-evident the moment it
    moved. A gate rather than a one-off check, because the next clip frozen
    here is the one this is for.

    Reads the RECORDED peaks, so it answers on the hosts with no cache -- which
    is most of them. Where the audio IS present, `verify()` reads it through
    `load_clip` and the same floor applies to the samples themselves."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    clips = json.loads(real.read_text())["clips"]
    assert clips, "a profile with no clips"
    missing = [cid for cid, m in clips.items() if m.get("peak") is None]
    assert not missing, f"clips with no recorded peak, so nothing to check: {missing}"
    quiet = {cid: m["peak"] for cid, m in clips.items() if m["peak"] <= rp.LEVEL_FLOOR}
    assert not quiet, (f"committed clips at or below the level floor "
                       f"{rp.LEVEL_FLOOR:g}: {quiet}")


def test_the_profile_states_its_estimator_floors():
    """Issue #92: a published floor that was not actually constant withdrew a
    whole column of #61. Every floor here has to say what it is based on, and
    the ones that are not constants have to say so rather than quote a number."""
    real = ROOT / "refprofile" / "profile.json"
    if not real.exists():
        pytest.skip("no committed profile in this tree")
    prof = json.loads(real.read_text())
    floors = prof["estimator_floors"]
    assert floors, "a profile with no stated floors"
    for name, f in floors.items():
        assert f.get("basis"), f"{name} states a floor with no basis"


# --- non-finite audio, found by review ------------------------------------
#
# A matching sha256 says the bytes are the ones the profile describes. It says
# NOTHING about whether those bytes are numbers. NaN and Inf both compare False
# against the silence threshold, so they defeated the one content check there
# was and loaded as references.

@pytest.mark.parametrize("name,fill", [
    ("all NaN", np.nan),
    ("all +Inf", np.inf),
    ("all -Inf", -np.inf),
])
def test_non_finite_audio_is_refused_even_though_it_hashes_correctly(
        profile, name, fill):
    prof, dest = profile
    y = np.full(prof["clips"]["fake/clip"]["frames"], fill, dtype=np.float32)
    rp.write_clip(dest, y)
    # re-freeze the integrity fields so ONLY finiteness can refuse it
    meta = json.loads((rp.PROFILE_DIR / "profile.json").read_text())
    c = meta["clips"]["fake/clip"]
    c["bytes"], c["sha256"] = dest.stat().st_size, rp.file_sha256(dest)
    (rp.PROFILE_DIR / "profile.json").write_text(json.dumps(meta))

    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "non-finite" in str(e.value), f"{name}: refused for the wrong reason"


def test_one_non_finite_sample_among_good_audio_is_refused(profile):
    """Not just wholly-bad files. A single NaN mid-clip poisons every estimator
    downstream and is exactly what a partial write or a denormal blow-up looks
    like."""
    prof, dest = profile
    y = _tone().astype(np.float32)
    y[len(y) // 2] = np.nan
    rp.write_clip(dest, y)
    meta = json.loads((rp.PROFILE_DIR / "profile.json").read_text())
    c = meta["clips"]["fake/clip"]
    c["bytes"], c["sha256"] = dest.stat().st_size, rp.file_sha256(dest)
    (rp.PROFILE_DIR / "profile.json").write_text(json.dumps(meta))

    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "non-finite" in str(e.value)
    assert "index" in str(e.value), "the refusal should say WHERE"


def test_ordinary_audio_and_silence_are_unaffected_by_the_finite_check(profile):
    """The control in the other direction: the new check must not change the
    two verdicts that already worked."""
    _, dest = profile
    y, sr, meta = rp.load_clip("fake/clip")          # ordinary tone still loads
    assert np.abs(y).max() == pytest.approx(0.25, abs=1e-6)

    rp.write_clip(dest, np.zeros(meta["frames"], dtype=np.float32))
    m = json.loads((rp.PROFILE_DIR / "profile.json").read_text())
    c = m["clips"]["fake/clip"]
    c["bytes"], c["sha256"] = dest.stat().st_size, rp.file_sha256(dest)
    (rp.PROFILE_DIR / "profile.json").write_text(json.dumps(m))
    with pytest.raises(rp.Refused) as e:
        rp.load_clip("fake/clip")
    assert "silent" in str(e.value), "silence must still refuse AS silence"
