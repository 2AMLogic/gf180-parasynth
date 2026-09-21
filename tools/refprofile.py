#!/usr/bin/env python3
"""The frozen reference profile: reference audio rendered ONCE, cached, hashed,
and described in a file that is committed.

    tools/refprofile_restore.py         restore the exact committed audio archive
    tools/refprofile.py                 verify the cache against the profile
    tools/refprofile.py --list          what the profile holds
    tools/refprofile.py --render        re-render it (an explicit act, a visible diff)

WHY A PROFILE AND NOT A RENDER-ON-DEMAND
----------------------------------------
A reference that is re-rendered on demand is not a reference. It moves when the
plugin updates, when the host block rate changes, when a preset drifts -- and
it moves silently, because the number that comes out looks the same. Every
comparison made against it before the move is then a comparison against
something nobody can reconstruct.

So the reference side is rendered once and frozen:

  * the AUDIO is cached (`refprofile/cache/`, gitignored) and hashed
  * the HASHES, the plugin identity, every parameter the rig set, the rig's own
    qualification verdict and the commit it was built at live in
    `refprofile/profile.json`, which IS committed
  * `load_clip` reads the cache and REFUSES if it is absent or if its content
    hash is not the one in the profile. It never renders. Re-rendering is
    `--render` and nothing else, so it always produces a diff somebody reviews

This is the shape `refaudio/` already uses (index committed, audio fetched),
with one difference: this audio has no upstream to fetch from. It exists
because a plugin on an operator's machine produced it, and the profile is the
record of what that was.

THREE OUTCOMES, kept apart on purpose -- `tools/refaudio_fetch.py`'s convention
--------------------------------------------------------------------------
    exit 0  OK       every clip in the profile is on disk and hashes correctly
    exit 1  FAIL     a clip is there and is NOT what the profile describes
    exit 2  REFUSED  a precondition is unmet, so nothing was attempted -- no
                     cache at all, or (for --render) no plugin host on this box

REFUSED is not a failure of the profile and must never be read as one. Most
hosts in this fleet have neither the plugins nor `dawdreamer`; on those, every
case that depends on the profile is a stated no-verdict, which is the correct
answer and not a hole in the instrument.

WHAT IS AND IS NOT IN HERE, AND WHY
-----------------------------------
`--list` prints the qualification table. It has entries that say **no**, and
those are the load-bearing ones:

  * **Model D renders exact silence headlessly.** Measured here, not inherited:
    oscillator 1 on at full level with the filter wide open, and with the
    filter self-oscillating, both produce a buffer whose peak is 0.0. Moog's
    own Model D is therefore NOT in this profile and cannot be the cross-check
    the Mono cases name.
  * **Mini V3's Range control defaults an octave down.** Measured: MIDI note
    48 sounds at 65.42 Hz, exactly half of 130.81 Hz, until parameter 45 is
    written. `reference_rigs.MiniV3Rig.osc_tone` already writes it; anything
    else driving that plugin has to as well.
  * **Surge renames parameters 259-267 by oscillator type** and dawdreamer
    keeps reporting the Classic oscillator's names, so only the readback can
    tell them apart. The rig pins them by readback; this profile records the
    readback it was built at, so a Surge update that moves them is a diff and
    not a silent change of what "the reference" means.

THE ESTIMATOR FLOOR, AND WHY THE PROBE LEVEL IS WHAT IT IS
----------------------------------------------------------
Issue #92: a published floor that was not actually constant withdrew a whole
column of #61. So the floor here is a MEASURED window, quoted with the
measurement, and the profile refuses to be built outside it.

Surge XT's Huovilainen ladder scales its `tanh` argument by `thermal = 1/70`,
so it is linear over every level tested: its corner, plateau, peak and slope
at 250 Hz are identical to two decimal places at -60, -36 and -12 dBFS. Our
fixed-point ladder is not, in both directions:

    input      our corner at res 0      our corner at res 1.20 (resonant)
    -60 dBFS        --                   55.7 Hz   (quantisation noise)
    -36 dBFS        --                  282.6 Hz
    -24 dBFS      125.2 Hz                --
    -18 dBFS      124.3 Hz                --
    -12 dBFS      117.8 Hz              361.2 Hz
     -6 dBFS      105.0 Hz                --

Below about -24 dBFS the stepped-tone probe on our 16-bit ladder is reading
truncation noise -- at -60 dBFS `slope_db_oct` refuses every resonant row and
the corner moves by 300 Hz. Above -12 dBFS our own saturation moves the corner
(105 Hz at -6, against 125 Hz small-signal). **-12 dBFS is the only level
inside the window at every resonance in the grid**, and it is also the level
`reference_compare.response_curve` has probed at since #87, so it is not a
level chosen after seeing an answer.

That window is a property of OUR side, and it is stated here because it bounds
what the reference can be compared against -- not because the reference has it.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import plistlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))

PROFILE_DIR = ROOT / "refprofile"
PROFILE_JSON = PROFILE_DIR / "profile.json"
CACHE = PROFILE_DIR / "cache"
SCHEMA = "refprofile/1"

OK, FAIL, REFUSED = 0, 1, 2


class Refused(Exception):
    """A precondition of the apparatus failed. REFUSED is a first-class
    outcome here, distinct from pass and from fail."""


def _rel(p) -> str:
    """A path for a human, and one that CANNOT raise. `relative_to` throws for
    anything outside the repository, and every use of it here is inside a
    REFUSAL message -- a formatter that raises turns a stated refusal into a
    traceback, which is the one thing a refusal must never become."""
    p = pathlib.Path(p)
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


# ===========================================================================
# 1. What is frozen. This table is the profile's source; `--render` executes it
#    and writes the hashes, and nothing else may write into the cache.
# ===========================================================================
SR = 48000

#: The probe level, in dBFS, and the one number in this file that a later
#: change would invalidate every clip over. See the module docstring: it is the
#: level `reference_compare.response_curve` has used since #87 and the only one
#: inside our own side's measured window at every resonance.
PROBE_AMP = 0.25
PROBE_LEVEL_DBFS = -12.04

#: The stepped-tone grid, taken from `reference_compare.FREQS` rather than
#: copied, so the profile and the study cannot drift apart.
def _freqs():
    import reference_compare as rc
    return [float(f) for f in rc.FREQS]


#: The nominal cutoff the First-32 filter cases state. "Verified by
#: measurement" is their own wording: what the profile freezes is the audio,
#: and the corner is read off it.
CUT_HZ = 250.0

#: The OTHER cutoff regions the Filters cases state, in Hz. `docs/scorecard/
#: cases.csv` names three for the cutoff-response family -- "nominal cutoff
#: region 250 Hz / 1000 Hz / 4000 Hz, verified by measurement" -- and the
#: profile froze only the first, which is what `run_case.py`'s twenty
#: `out of scope for this reference profile` entries were about.
#:
#: Only resonance ZERO is rendered at the new regions, and that is a deliberate
#: limit rather than an oversight: F1B and F1C are the only cases these clips
#: can be read by. F2B/F2C want a resonance ladder and are blocked on a
#: matched-drive DEFINITION (see run_case.NOT_RUN["F2A"]), not on audio, so
#: freezing eighteen more ladder clips here would freeze audio no case can
#: consume and would move every consumer's profile hash to do it. Whoever
#: writes that definition renders the ladder the definition asks for.
CUT_REGIONS_HZ = (1000.0, 4000.0)

#: Wide open, for the passband insertion loss the cases call "low-band gain".
#: A gain against the SAME device with its filter out of the way is a ratio
#: inside one instrument; a plateau in dBFS compared across two instruments is
#: a level mismatch wearing a measurement's clothes.
CUT_OPEN_HZ = 20000.0

#: Surge's own resonance grid, from `reference_compare.RES_GRID`, plus its
#: zero. Resonance controls are NOT commensurable between devices and nothing
#: here averages across them; the grid is frozen so the trajectory can be
#: re-read from the same audio later.
def _res_ladder():
    import reference_compare as rc
    return [0.0] + list(rc.RES_GRID["surge"])


def clip_specs() -> list[dict]:
    """Every clip in the profile, as data. One entry, one cached WAV."""
    fr = _freqs()
    specs = [
        dict(clip_id="surge-type2/lp-open20k-res0.00", rig="surge-type2", kind="tone_train",
             cut_hz=CUT_OPEN_HZ, res=0.0, amp=PROBE_AMP, freqs=fr,
             why="the filter effectively out of the way: the passband reference "
                 "that makes 'low-band gain' a ratio inside one instrument"),
    ]
    for res in _res_ladder():
        specs.append(dict(
            clip_id=f"surge-type2/lp-cut{CUT_HZ:.0f}-res{res:.2f}", rig="surge-type2",
            kind="tone_train", cut_hz=CUT_HZ, res=res, amp=PROBE_AMP, freqs=fr,
            why=("the cutoff-response anchor: resonance zero, the setting F1A states"
                 if res == 0.0 else
                 "one rung of Surge's own resonance ladder at the same cutoff")))
    # The drive clips. They are frozen because the audio is cheap and the next
    # agent should not have to re-render it -- NOT because F3A can be scored off
    # them. See `tools/run_case.py`'s NOT_RUN entry for why it cannot.
    for lv in (-12.0, -6.0, 0.0):
        specs.append(dict(
            clip_id=f"surge-type2/drive-100hz-cut{CUT_HZ:.0f}-res0.50-in{lv:+.0f}dbfs",
            rig="surge-type2", kind="drive_tone", cut_hz=CUT_HZ, res=0.5,
            f_in=100.0, amp=float(10 ** (lv / 20.0)), level_dbfs=lv,
            why="a steady 100 Hz tone into the filter at a stated input level"))
    # APPENDED, not interleaved with the 250 Hz block above. The fourteen
    # original clips keep their identity and their render ORDER, so a re-render
    # on any host reproduces their committed sha256 or does not -- which is the
    # control that says this host's rig is the one that built the profile.
    # Grouping the regions together would read better and would throw that
    # control away on the same commit that needed it.
    for cut in CUT_REGIONS_HZ:
        specs.append(dict(
            clip_id=f"surge-type2/lp-cut{cut:.0f}-res0.00", rig="surge-type2",
            kind="tone_train", cut_hz=cut, res=0.0, amp=PROBE_AMP, freqs=fr,
            why=f"the cutoff-response anchor for the {cut:.0f} Hz region, at the "
                f"resonance zero F1B and F1C state"))
    return specs


#: Rigs this profile knows about, and the verdict on each. A rig that is not
#: qualified is named here with the measurement that disqualified it, because
#: "we did not use Model D" and "Model D cannot be used" are different facts
#: and only one of them tells the next person not to try.
RIG_VERDICTS = {
    "surge-type2": dict(
        qualified=True,
        builder="reference_rigs.SurgeRig(subtype='Type 2')",
        why="Surge XT is open source and its LP Vintage Ladder subtype Type 2 is "
            "sst-filters' VintageLadder::Huov -- Huovilainen's DAFx-04 model, the "
            "same paper DR 0001 implements. It is the only reference here whose "
            "cutoff is commanded in Hz and reads back in Hz."),
    "modeld": dict(
        qualified=False,
        builder="reference_rigs.ModelDRig()",
        why="renders exact silence headlessly -- measured, see `--render`'s "
            "disqualification probe. The rig BUILDS (its pins hold); it produces "
            "no audio, so nothing downstream of it can be a reference."),
    "miniv3": dict(
        qualified=False,
        builder="reference_rigs.MiniV3Rig()",
        why="the rig qualifies and makes sound, but every parameter is a bare "
            "0..1 with no units and no readback. Its cutoff can be calibrated "
            "against its own self-oscillation (reference_compare.calibrate_knob); "
            "its ENVELOPE knobs cannot -- nothing in this repository maps a Mini "
            "V3 envelope knob to a time. The Mono cases require envelope timing, "
            "so a Mini V3 patch frozen here would compare our envelope against an "
            "arbitrary knob position and report the difference as a result."),
    "diva": dict(
        qualified=False,
        builder="reference_rigs.DivaRig()",
        why="found running unlicensed and inserting clicks (docs/reference-integrity.md "
            "section 1); it is also a general analogue-modelling synth rather than a "
            "Minimoog emulation, and is excluded from the oscillator study for that "
            "reason already."),
}

#: The estimator floors this profile's clips are read through, stated here so a
#: consumer can refuse a row inside one. Issue #92: a floor that is published
#: and not actually constant is worse than none.
ESTIMATOR_FLOORS = {
    "probe level": {
        "value_dbfs": PROBE_LEVEL_DBFS,
        "basis": "the only input level inside our own fixed-point ladder's "
                 "measured stability window at every resonance in the grid "
                 "(-24 dBFS quantisation noise below, -6 dBFS self-saturation "
                 "above); Surge is level-independent across the whole range, "
                 "so the window is ours and is stated as ours",
        "measured": True,
    },
    "audio_measure.slope_db_oct": {
        "refuses_past_db": 1.5,
        "basis": "its own max_residual_db: a curve that is not a straight line "
                 "over the band has no slope. It refused every resonant row at "
                 "-36 and -60 dBFS on our side, which is how the window above "
                 "was found",
    },
    "audio_measure.harmonic_signature": {
        "floor": "measured per harmonic, per clip, from four noise draws either "
                 "side of k*f0; the largest is used and a harmonic within "
                 "floor_margin_db of it is reported as None, never as a number",
        "basis": "audio_measure.harmonic_signature's own documented behaviour; "
                 "it is not a constant and is deliberately not quoted as one",
    },
    "stepped-tone stopband": {
        "value_db": -70.0,
        "relative_to": "the passband plateau of the same curve",
        "basis": "below it the coherent projection is reading the path's own "
                 "truncation noise and a fitted slope reads first too steep and "
                 "then too shallow. `reference_compare.response_row` already caps "
                 "its fit band there; this profile records the same number so a "
                 "consumer can apply it without re-deriving it",
    },
}


# ===========================================================================
# 2. Identity and provenance
# ===========================================================================
def _git(*args) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True,
                                       stderr=subprocess.DEVNULL)
    except Exception:
        return ""


def worktree_state() -> dict:
    """The commit AND a hash of everything uncommitted. A clean SHA that
    silently means 'plus whatever was in the working tree' is worse than no
    SHA; `tools/run_case.py` says the same and for the same reason."""
    h = hashlib.sha256()
    diff = _git("diff", "HEAD")
    h.update(diff.encode())
    untracked = [f for f in _git("ls-files", "--others", "--exclude-standard").split("\n") if f]
    for rel in sorted(untracked):
        try:
            h.update(rel.encode())
            h.update(hashlib.sha256((ROOT / rel).read_bytes()).digest())
        except OSError:
            h.update(b"?")
    return {"commit": (_git("rev-parse", "--short", "HEAD").strip() or "?"),
            "branch": (_git("rev-parse", "--abbrev-ref", "HEAD").strip() or "?"),
            "dirty": bool(diff.strip() or untracked),
            "uncommitted_sha256": h.hexdigest()[:16],
            "untracked_files": len(untracked)}


def file_sha256(path) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def plugin_identity(vst3_path: str) -> dict:
    """What produced the audio, in enough detail that a plugin update is a
    visible change rather than a silent one. The version string is what the
    bundle claims; the binary hash is what it actually is, and they answer
    different questions -- a vendor who ships a fix without bumping the version
    moves only the second."""
    p = pathlib.Path(vst3_path)
    out = {"path": str(p), "present": p.exists()}
    if not p.exists():
        return out
    info = p / "Contents" / "Info.plist"
    if info.exists():
        try:
            d = plistlib.loads(info.read_bytes())
            out["bundle_version"] = d.get("CFBundleShortVersionString") or d.get("CFBundleVersion")
            out["bundle_id"] = d.get("CFBundleIdentifier")
        except Exception as e:                                   # pragma: no cover
            out["bundle_version_error"] = f"{type(e).__name__}: {e}"
    binaries = sorted((p / "Contents" / "MacOS").glob("*")) if (p / "Contents" / "MacOS").exists() else []
    if binaries:
        out["binary"] = binaries[0].name
        out["binary_sha256"] = file_sha256(binaries[0])
        out["binary_bytes"] = binaries[0].stat().st_size
    return out


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===========================================================================
# 3. The cache: float32 mono WAV, written whole or not at all
# ===========================================================================
def write_clip(path: pathlib.Path, y: np.ndarray, sr: int = SR) -> None:
    """float32, because that is the precision dawdreamer hands back: writing
    int16 would freeze a LOSSY copy of the reference and call it the reference.

    Written through a `.part` and renamed, so an interrupted render can never
    leave a short file that every downstream tool will happily open --
    `tools/refaudio_fetch.py` was built around exactly that defect."""
    from scipy.io import wavfile
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    try:
        wavfile.write(str(part), sr, np.asarray(y, dtype=np.float32))
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def read_clip_file(path: pathlib.Path) -> tuple[np.ndarray, int]:
    from scipy.io import wavfile
    sr, y = wavfile.read(str(path))
    y = np.asarray(y, dtype=np.float64)
    if y.ndim > 1:
        y = y.mean(axis=1)
    return y, int(sr)


# ===========================================================================
# 4. Reading the profile -- no plugin, no host, no render
# ===========================================================================
def load_profile() -> dict:
    if not PROFILE_JSON.exists():
        raise Refused(f"no frozen reference profile at {_rel(PROFILE_JSON)}")
    d = json.loads(PROFILE_JSON.read_text(encoding="utf-8"))
    if d.get("schema") != SCHEMA:
        raise Refused(f"profile schema is {d.get('schema')!r}, this tool reads {SCHEMA!r}")
    return d


def clip_path(meta: dict) -> pathlib.Path:
    return PROFILE_DIR / meta["file"]


def load_clip(clip_id: str, profile: dict | None = None) -> tuple[np.ndarray, int, dict]:
    """The frozen audio for one clip, or a REFUSAL.

    This function never renders. That is the whole point: if the cache is not
    there, the answer is "no evidence, here is why", and the fix is an explicit
    `--render` that somebody reviews the diff of -- not a silent re-render that
    quietly redefines what the reference was.

    The hash is checked on every read. A cache file that has drifted from the
    profile is a FAILURE, not a warning: it is audio that no committed record
    describes, and a number measured off it would carry the profile's
    provenance block while not having come from the profile at all."""
    prof = load_profile() if profile is None else profile
    meta = prof.get("clips", {}).get(clip_id)
    if meta is None:
        raise Refused(f"{clip_id!r} is not a clip in the frozen reference profile "
                      f"({len(prof.get('clips', {}))} clips; see tools/refprofile.py --list)")
    p = clip_path(meta)
    if not p.exists():
        raise Refused(
            f"the frozen reference audio for {clip_id!r} is not in the cache "
            f"({_rel(p)}). Restore the frozen audio without a plugin: "
            f"tools/refprofile_restore.py")
    got_bytes = p.stat().st_size
    if got_bytes != meta["bytes"]:
        raise Refused(f"{clip_id!r} is {got_bytes} bytes, the profile says {meta['bytes']}: "
                      f"this is not the file the profile describes")
    got = file_sha256(p)
    if got != meta["sha256"]:
        raise Refused(f"{clip_id!r} hashes {got[:16]}, the profile says "
                      f"{meta['sha256'][:16]}: this is not the audio the profile describes")
    y, sr = read_clip_file(p)
    if sr != meta["sr"]:
        raise Refused(f"{clip_id!r} is at {sr} Hz, the profile says {meta['sr']}")
    if len(y) != meta["frames"]:
        raise Refused(f"{clip_id!r} holds {len(y)} frames, the profile says {meta['frames']}")
    # BEFORE the silence test, because a non-finite sample DEFEATS it: NaN and
    # Inf both compare False against the threshold, so `all NaN` and `all Inf`
    # audio passed every check here -- profile membership, byte count, sha256,
    # rate, frame count and silence -- and loaded as a reference.
    #
    # A matching hash says the bytes are the ones the profile describes. It says
    # nothing about whether those bytes are numbers. Found by review, reproduced
    # against this function before it was fixed.
    bad = int(np.count_nonzero(~np.isfinite(y)))
    if bad:
        where = int(np.argmax(~np.isfinite(y)))
        raise Refused(f"{clip_id!r} holds {bad} non-finite samples "
                      f"(first at index {where}): the file hashes correctly, so "
                      f"this is what was frozen -- it is not usable as audio")
    if float(np.abs(y).max()) <= 1e-9:
        raise Refused(f"{clip_id!r} is silent")
    return y, sr, meta


def verify(profile: dict | None = None) -> tuple[int, list[str]]:
    """Every clip, against the profile. Returns (exit code, lines)."""
    try:
        prof = load_profile() if profile is None else profile
    except Refused as why:
        return REFUSED, [f"REFUSED  {why}"]
    clips = prof.get("clips", {})
    if not clips:
        return REFUSED, ["REFUSED  the profile holds no clips"]
    if not CACHE.exists():
        return REFUSED, [
            f"REFUSED  no reference-audio cache at {_rel(CACHE)}: this host "
            f"has never rendered the profile.",
            "         The profile is committed and the audio is not, by design. Every "
            "case that depends on it is a stated no-verdict here, which is the correct "
            "answer and not a hole in the instrument."]
    lines, bad, missing = [], 0, 0
    for cid in sorted(clips):
        try:
            load_clip(cid, prof)
            lines.append(f"OK       {cid}")
        except Refused as why:
            s = str(why)
            if "not in the cache" in s:
                missing += 1
                lines.append(f"ABSENT   {cid}")
            else:
                bad += 1
                lines.append(f"FAIL     {cid}\n         {s}")
    lines.append(f"{len(clips) - bad - missing} verified, {bad} failed, {missing} absent")
    if bad:
        return FAIL, lines
    if missing:
        return REFUSED, lines
    return OK, lines


# ===========================================================================
# 5. Rendering -- the explicit act
# ===========================================================================
def _param_dump(p) -> dict:
    """Every parameter the plugin exposes, by index, with its name and its
    readback text. This is what "record every parameter set" means: not the
    handful the rig writes, but the whole state, so a default that moves under
    a plugin update is a diff in a committed file."""
    out = {}
    try:
        n = len(p.get_parameters_description())
    except Exception:                                            # pragma: no cover
        return out
    for i in range(n):
        try:
            out[str(i)] = [p.get_parameter_name(i), p.get_parameter_text(i),
                           round(float(p.get_parameter(i)), 6)]
        except Exception:                                        # pragma: no cover
            out[str(i)] = ["?", "?", None]
    return out


def disqualification_probe() -> dict:
    """The measurements behind the `qualified: false` verdicts, taken here so
    they are evidence in a committed file rather than lore in a docstring.

    Each of these is a plugin that BUILDS -- its pinned settings hold, the rig
    does not refuse it -- and is still unusable. A rig that qualifies is not the
    same as a reference that works, and the gap between the two is where a
    silent wrong answer lives."""
    import audio_measure as am
    import reference_rigs as rr
    out = {}

    try:
        md = rr.ModelDRig()
        md.set(md.I['o1_on'], 1.0)
        md.set(md.I['o1_vol'], 0.9)
        md.set(md.I['o1_wave'], 0.4)
        md.set(md.I['cutoff'], 1.0)
        md.set(md.I['emphasis'], 0.0)
        md.note = 48
        y = md.render(np.zeros(1), 2.0)
        ring = md.ring(0.75, 0.95, 1.5)
        out["modeld"] = {
            "rig_builds": True,
            "osc1_on_note48_peak": float(np.abs(y).max()),
            "osc1_on_note48_silent": bool(am.is_silent(y)),
            "self_oscillation_peak": float(np.abs(ring).max()),
            "self_oscillation_silent": bool(am.is_silent(ring)),
            "verdict": "renders exact silence headlessly, with the oscillator on AND "
                       "with the filter self-oscillating. Not usable as a reference.",
        }
        del md
    except Exception as e:                                       # pragma: no cover
        out["modeld"] = {"rig_builds": False, "error": f"{type(e).__name__}: {e}"}

    try:
        import voice_fx as vf
        mv = rr.MiniV3Rig()
        got = {}
        for label, rng in (("range_default", None), ("range_8ft", 0.575)):
            mv.set(48, mv.WAVES["saw"])
            mv.set(mv.I['lvl_ext'], 0.0)
            mv.set(mv.I['ext_sw'], 0.0)
            if rng is not None:
                mv.set(45, rng)
            mv.set(mv.I['lvl_o1'], 0.9)
            mv.set(mv.I['o1'], 1.0)
            mv.set(mv.I['cutoff'], 1.0)
            mv.set(mv.I['emphasis'], 0.0)
            mv.note = 48
            y = mv.render(np.zeros(1), 1.5)[int(0.3 * SR):]
            f = am.dominant_frequency(y, 10.0, 4000.0)
            got[label] = {"peak": float(np.abs(y).max()),
                          "f0_hz": (float(f.value) if f.ok else None),
                          "f0_why": (None if f.ok else f.reason)}
        out["miniv3"] = {
            "rig_builds": True, "note": 48, "note_hz": float(vf.note_hz(48)),
            "measured": got,
            "verdict": "makes sound, and its Range control defaults an octave down: "
                       "note 48 sounds at 65.42 Hz until parameter 45 is written. "
                       "Not disqualifying on its own -- what disqualifies it for the "
                       "Mono cases is that its envelope knobs are unitless and "
                       "nothing here calibrates one to a time.",
        }
        del mv
    except Exception as e:                                       # pragma: no cover
        out["miniv3"] = {"rig_builds": False, "error": f"{type(e).__name__}: {e}"}
    return out


def render(probe_disqualified: bool = True) -> dict:
    """Render every clip once, write the cache, return the profile dict.

    Refuses rather than reports when the host cannot do it: no `dawdreamer`, no
    plugin bundle. An empty or partial profile written on a host that could not
    render would be indistinguishable from a real one that had shrunk."""
    try:
        import dawdreamer                                        # noqa: F401
    except ImportError as e:
        raise Refused(f"no plugin host on this machine ({e}). Rendering the reference "
                      f"profile needs dawdreamer and the VST3 bundles; verifying it "
                      f"does not.")
    import reference_rigs as rr
    if not pathlib.Path(rr.PATH_SURGE).exists():
        raise Refused(f"Surge XT is not installed at {rr.PATH_SURGE}")

    prof = {
        "schema": SCHEMA,
        "what": "reference audio rendered once through a qualified rig, cached and "
                "hashed. The audio is not committed; this file is.",
        "sr": SR,
        "probe_level_dbfs": PROBE_LEVEL_DBFS,
        "estimator_floors": ESTIMATOR_FLOORS,
        "built": {
            "at": _now(),
            "worktree": worktree_state(),
            "command": " ".join([os.path.relpath(sys.argv[0], ROOT)] + sys.argv[1:])
                       if sys.argv and sys.argv[0] else "(imported)",
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "dawdreamer": getattr(dawdreamer, "__version__", "unknown"),
            "platform": sys.platform,
            "rig_source_sha256": file_sha256(ROOT / "model" / "reference_rigs.py"),
            "builder_sha256": file_sha256(pathlib.Path(__file__)),
        },
        "rigs": {},
        "clips": {},
    }

    devices, specs = {}, clip_specs()
    needed = sorted({s["rig"] for s in specs})
    for rig_name in needed:
        verdict = dict(RIG_VERDICTS[rig_name])
        if not verdict["qualified"]:                             # pragma: no cover
            raise Refused(f"{rig_name} is marked not qualified; it cannot hold clips")
        dev = rr.SurgeRig(subtype="Type 2")
        # `qualify()` already ran inside the constructor and raises if the pins
        # did not hold. Re-reading them here puts the verdict ON THE RECORD:
        # "the rig refused to build" and "the rig built and these are the
        # settings it built with" are different claims and the profile makes
        # the second one checkable.
        still_bad = dev.check_pins()
        if still_bad:                                            # pragma: no cover
            raise Refused(f"{rig_name}: pinned settings did not hold after setup: {still_bad}")
        verdict.update({
            "plugin": plugin_identity(dev.path),
            "host": {"sr": SR, "block": dev.block,
                     "note": "the host block size matters for automation only; no clip "
                             "in this profile automates a parameter, so no number here "
                             "can be the host's 93.75 Hz block rate in disguise"},
            "qualification": {
                "pins_held_after_render": True,
                "pinned_readback": dev.pinned_report(),
                "n_pins": len(dev.PINS),
                "how": "reference_rigs._Plugin.qualify(): render once, then hold every "
                       "pinned setting to its NAME and its READBACK. 7/7 deliberately "
                       "wrong setups are rejected (#87).",
            },
            "parameters_after_setup": _param_dump(dev.p),
        })
        prof["rigs"][rig_name] = verdict
        devices[rig_name] = dev

    for spec in specs:
        dev = devices[spec["rig"]]
        cid = spec["clip_id"]
        rel = pathlib.Path("cache") / (cid + ".wav")
        dest = PROFILE_DIR / rel
        t0 = datetime.datetime.now()
        if spec["kind"] == "tone_train":
            y, parts, read = dev.tone_render(spec["freqs"], spec["cut_hz"],
                                             spec["res"], spec["amp"])
            stim = {"stimulus": "stepped sine, an integer number of periods per step",
                    "freqs_hz": spec["freqs"], "amp": spec["amp"],
                    "settle_s": dev.TONE_SETTLE_S, "window_s": dev.TONE_WINDOW_S,
                    "parts": [[int(a), int(b), float(f)] for a, b, f in parts],
                    "parts_note": "(start sample, window length, Hz) into THIS file"}
        elif spec["kind"] == "drive_tone":
            y = dev.drive_tone(spec["f_in"], spec["cut_hz"], spec["res"], spec["amp"])
            read = float(dev.text(dev.I['f1_cut']).split()[0])
            stim = {"stimulus": f"steady {spec['f_in']:.0f} Hz sine",
                    "amp": spec["amp"], "level_dbfs": spec["level_dbfs"],
                    "f_in_hz": spec["f_in"]}
        else:                                                    # pragma: no cover
            raise Refused(f"unknown clip kind {spec['kind']!r}")
        if float(np.abs(y).max()) <= 1e-9:
            raise Refused(f"{cid}: the rig rendered silence -- refusing to freeze it")
        write_clip(dest, y)
        prof["clips"][cid] = {
            "rig": spec["rig"], "kind": spec["kind"], "why": spec["why"],
            "file": str(rel),
            "commanded": {k: v for k, v in spec.items()
                          if k not in ("clip_id", "rig", "kind", "freqs", "why")},
            "cutoff_readback_hz": read,
            "readback_note": "Surge's own cutoff readback, in Hz, at the moment of the "
                             "render. The COMMANDED cutoff is in `commanded`; the corner "
                             "this produces is neither and is read off the audio.",
            **stim,
            "sr": SR, "frames": int(len(y)), "dtype": "float32",
            "bytes": dest.stat().st_size,
            "sha256": file_sha256(dest),
            "peak": round(float(np.abs(y).max()), 9),
            "rms": round(float(np.sqrt(np.mean(np.square(y)))), 9),
        }
        # How long the render took is a stopwatch reading, not provenance of the
        # audio, and putting it in the file would make every re-render show a
        # diff whether or not anything about the reference changed. The diff IS
        # the review here, so it carries only what a reader must act on.
        print(f"rendered {cid}  {len(y)} frames  peak {np.abs(y).max():.4f}  "
              f"{(datetime.datetime.now() - t0).total_seconds():.2f} s", flush=True)

    for d in devices.values():
        del d
    if probe_disqualified:
        print("probing the rigs that do NOT qualify ...", flush=True)
        prof["disqualified"] = disqualification_probe()
    for name, v in RIG_VERDICTS.items():
        prof["rigs"].setdefault(name, dict(v))
    return prof


# ===========================================================================
# 6. CLI
# ===========================================================================
def cmd_list() -> int:
    try:
        prof = load_profile()
    except Refused as why:
        print(f"REFUSED  {why}")
        return REFUSED
    b = prof.get("built", {})
    w = b.get("worktree", {})
    print(f"profile   {_rel(PROFILE_JSON)}   schema {prof['schema']}")
    print(f"built     {b.get('at')} at {w.get('commit')} "
          f"({'DIRTY ' + str(w.get('uncommitted_sha256')) if w.get('dirty') else 'clean'})")
    print(f"probe     {prof.get('probe_level_dbfs')} dBFS, {prof.get('sr')} Hz")
    print()
    print(f"{'rig':<14}{'qualified':<11}why")
    print("-" * 100)
    for name, r in sorted(prof.get("rigs", {}).items()):
        q = "yes" if r.get("qualified") else "NO"
        pl = r.get("plugin", {})
        ver = f" [{pl.get('bundle_version')}]" if pl.get("bundle_version") else ""
        print(f"{name:<14}{q:<11}{(r.get('why', '') + ver)[:74]}")
    print()
    print(f"{'clip':<46}{'frames':>9}{'sha256':>14}  what")
    print("-" * 100)
    for cid, c in sorted(prof.get("clips", {}).items()):
        here = "" if clip_path(c).exists() else "  (NOT CACHED HERE)"
        print(f"{cid:<46}{c['frames']:>9}{c['sha256'][:12]:>14}  {c['why'][:28]}{here}")
    print("-" * 100)
    print(f"{len(prof.get('clips', {}))} clips")
    return OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--render", action="store_true",
                   help="re-render every clip and rewrite the profile. Needs the "
                        "plugins and dawdreamer; produces a diff on purpose")
    g.add_argument("--list", action="store_true", help="what the profile holds")
    ap.add_argument("--out", default=None,
                    help="write the rendered profile here instead of refprofile/profile.json")
    a = ap.parse_args(argv)

    if a.list:
        return cmd_list()

    if a.render:
        try:
            prof = render()
        except Refused as why:
            print(f"REFUSED  {why}")
            return REFUSED
        dest = pathlib.Path(a.out) if a.out else PROFILE_JSON
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(prof, indent=1, sort_keys=False) + "\n", encoding="utf-8")
        print(f"\nwrote {dest} -- {len(prof['clips'])} clips. "
              f"Review the diff: this file IS the reference.")
        return OK

    code, lines = verify()
    for ln in lines:
        print(ln)
    return code


if __name__ == "__main__":
    sys.exit(main())
