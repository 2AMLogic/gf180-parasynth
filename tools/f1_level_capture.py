#!/usr/bin/env python3
"""Matched-level Surge XT Type 2 references for F1A/F1B/F1C, stored SEPARATELY
from the frozen profile.

    tools/f1_level_capture.py --capture     render every take (needs Surge + dawdreamer)
    tools/f1_level_capture.py               verify the committed captures (no plugin)

WHY
---
#228 found our selected-path corner error is LEVEL-DEPENDENT on our side, but
the Surge reference existed at ONE level (amp 0.25, -12.04 dBFS). The
reference's level independence was documented (`thermal = 1/70`) and NOT
re-verified. This tool renders the same stepped-tone stimulus through the same
rig at three amplitudes -- 0.25 (nominal), 0.125 and 0.0625 -- at the three F1
cutoffs plus the wide-open condition, TWICE each, every take in its own process
so the VST3 is reloaded and the rig re-qualified between takes.

It never touches `refprofile/`: the frozen profile and its archive are read
only, to take the plugin identity the captures must match and to compare the
nominal-level takes against the frozen bytes.

PRECONDITIONS, asserted at the point of use; each one REFUSES (exit 2)
----------------------------------------------------------------------
  * dawdreamer imports; the Surge bundle exists
  * bundle version AND binary sha256 equal the ones the frozen profile records
    -- a different Surge build is a different reference, not a second take
  * the rig's own qualification: every pinned parameter holds its NAME and its
    READBACK after a render (`check_pins_post_render`: the rig's pins, plus the
    measured Audio-In name alias for 259/260/264/265). This is the check that
    catches Surge's by-oscillator-type renaming of 259-267 (the Audio In high
    cut); it is re-run after EVERY clip, not only at construction
  * filter type/subtype read back 'LP Vintage Ladder' / 'Type 2'
  * cutoff readback within 0.5 % of the commanded Hz
  * host block and sample rate equal the frozen profile's (512 / 48 kHz). No
    clip automates a parameter, so the block rate cannot enter a number; it is
    asserted anyway because it is part of the rig's recorded state
  * the rendered clip is finite, not silent, and its wide-open low band sits
    within 6 dB of the frozen wide-open clip's (a gross wrong-state guard: a
    stray high cut or mute reads tens of dB down; it is NOT a level-dependence
    test, which is what the captures are for)

Licence: Surge XT is GPL-3 open source; no licence state exists to assert.
Octave: the stimulus enters through the Audio In oscillator, so no oscillator
pitch/range setting is in the path; the held MIDI note only opens the gate.

Exit: 0 verified, 1 a clip is present and is not what the manifest says,
2 refused.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import pathlib
import subprocess
import sys
import zipfile

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "tools"))

import refprofile as rp        # noqa: E402

OK, FAIL, REFUSED = 0, 1, 2
OUT_DIR = ROOT / "docs" / "scorecard" / "f1-level" / "captures"
MANIFEST = OUT_DIR / "manifest.json"
ARCHIVE = OUT_DIR / "audio.zip"

#: The three stimulus amplitudes. 0.25 is the frozen profile's PROBE_AMP; the
#: other two are -6 and -12 dB below it.
AMPS = (0.25, 0.125, 0.0625)
TAKES = (1, 2)
#: condition -> (commanded cutoff Hz, the frozen clip it matches at amp 0.25)
CONDITIONS = {
    "open20k": (20000.0, "surge-type2/lp-open20k-res0.00"),
    "cut250": (250.0, "surge-type2/lp-cut250-res0.00"),
    "cut1000": (1000.0, "surge-type2/lp-cut1000-res0.00"),
    "cut4000": (4000.0, "surge-type2/lp-cut4000-res0.00"),
}
RES = 0.0
READBACK_TOL = 0.005
OPEN_GUARD_DB = 6.0


class Refused(Exception):
    pass


def amp_tag(amp: float) -> str:
    return f"a{amp:.4f}"


def clip_key(cond: str, amp: float, take: int) -> str:
    return f"{cond}/{amp_tag(amp)}/take{take}"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def frozen_rig() -> dict:
    return rp.load_profile()["rigs"]["surge-type2"]


def assert_plugin_identity() -> dict:
    """REFUSE unless the installed Surge is byte-for-byte the frozen one."""
    import reference_rigs as rr
    want = frozen_rig()["plugin"]
    got = rp.plugin_identity(rr.PATH_SURGE)
    if not got.get("present"):
        raise Refused(f"Surge XT is not installed at {rr.PATH_SURGE}")
    for k in ("bundle_version", "binary_sha256"):
        if got.get(k) != want.get(k):
            raise Refused(f"Surge {k} is {got.get(k)!r}; the frozen profile was built with "
                          f"{want.get(k)!r}. A different build is a different reference.")
    return got


#: Surge's TRUE names for the Audio In oscillator's parameters. `SurgeRig.PINS`
#: carries the Classic oscillator's names because that is what dawdreamer
#: reports straight after construction; measured here (2026-09-25), the names
#: flip to these after the NEXT render -- 0.1 s of silence is enough -- with
#: every readback unchanged. The refprofile renderer never saw this because it
#: checks pins once, before rendering any clip, and records
#: `pins_held_after_render: true` as a constant. A post-render check must
#: therefore accept either name for these four indices and still demand the
#: readback; any other index keeps its single expected name.
AUDIO_IN_NAMES = {259: "A Osc 1 Audio In Channel", 260: "A Osc 1 Audio In Gain",
                  264: "A Osc 1 Low Cut", 265: "A Osc 1 High Cut"}


def check_pins_post_render(dev) -> list:
    """`SurgeRig.check_pins` with the one measured name alias above. Readback
    is required for every pin exactly as the rig states it."""
    bad = []
    for idx, _val, name, want in dev.PINS:
        got_name = dev.p.get_parameter_name(idx)
        ok_names = {name, AUDIO_IN_NAMES.get(idx, name)}
        if name is not None and got_name not in ok_names:
            bad.append((idx, "NAME", sorted(ok_names), got_name))
        elif want is not None and dev.text(idx) != want:
            bad.append((idx, "VALUE", want, dev.text(idx)))
    return bad


def _projected_low_db(y, parts, amp, n=4) -> float:
    import reference_rigs as rr
    g = rr.SurgeRig.tone_project(y, parts[:n], amp, "guard")
    return float(np.median(g))


# ---------------------------------------------------------------------------
# the worker: ONE take, one process, one freshly loaded and qualified plugin
# ---------------------------------------------------------------------------
def worker(amp: float, take: int, out: pathlib.Path) -> dict:
    try:
        import dawdreamer
    except ImportError as e:
        raise Refused(f"no plugin host: {e}")
    import reference_rigs as rr
    ident = assert_plugin_identity()
    frozen = frozen_rig()
    dev = rr.SurgeRig(subtype="Type 2")          # qualify() runs inside; raises if pins fail
    if dev.block != frozen["host"]["block"] or rr.SR != frozen["host"]["sr"]:
        raise Refused(f"host block/sr {dev.block}/{rr.SR} != frozen "
                      f"{frozen['host']['block']}/{frozen['host']['sr']}")
    prof = rp.load_profile()
    freqs = [float(f) for f in prof["clips"][CONDITIONS["open20k"][1]]["freqs_hz"]]
    if freqs != rp._freqs():
        raise Refused("the frozen stepped-tone grid differs from reference_compare.FREQS")
    frozen_open_y, _, frozen_open_meta = rp.load_clip(CONDITIONS["open20k"][1], prof)
    fparts = [(int(a), int(b), float(f)) for a, b, f in frozen_open_meta["parts"]]
    frozen_open_low = _projected_low_db(frozen_open_y, fparts, float(frozen_open_meta["amp"]))

    rec = {"amp": amp, "take": take, "plugin": ident,
           "dawdreamer": getattr(dawdreamer, "__version__", "unknown"),
           "block": dev.block, "sr": rr.SR, "latency_samples": int(dev.p.get_latency_samples()),
           "pinned_readback": None, "clips": {}}
    for cond, (cut, _fz) in CONDITIONS.items():
        y, parts, read = dev.tone_render(freqs, cut, RES, amp)
        bad = check_pins_post_render(dev)
        if bad:
            raise Refused(f"{cond}: pinned settings did not hold after render: {bad}")
        if dev.text(dev.I["f1_type"]) != "LP Vintage Ladder" or dev.text(dev.I["f1_sub"]) != "Type 2":
            raise Refused(f"{cond}: filter reads {dev.text(dev.I['f1_type'])!r}/"
                          f"{dev.text(dev.I['f1_sub'])!r}")
        if abs(read - cut) / cut > READBACK_TOL:
            raise Refused(f"{cond}: cutoff readback {read} Hz vs commanded {cut} Hz")
        if not np.all(np.isfinite(y)) or float(np.abs(y).max()) <= 1e-9:
            raise Refused(f"{cond}: non-finite or silent render")
        if cond == "open20k":
            low = _projected_low_db(y, parts, amp)
            if abs(low - frozen_open_low) > OPEN_GUARD_DB:
                raise Refused(f"open low band {low:.2f} dB vs frozen {frozen_open_low:.2f} dB: "
                              f"the path is not in the frozen rig's state")
        dest = out / f"{cond}.wav"
        rp.write_clip(dest, y)
        rec["clips"][cond] = {
            "commanded": {"cut_hz": cut, "res": RES, "amp": amp},
            "cutoff_readback_hz": read,
            "parts": [[int(a), int(b), float(f)] for a, b, f in parts],
            "freqs_hz": freqs,
            "frames": int(len(y)), "sha256": rp.file_sha256(dest),
            "bytes": dest.stat().st_size,
            "peak": round(float(np.abs(y).max()), 9),
            "rms": round(float(np.sqrt(np.mean(np.square(y)))), 9)}
        print(f"take {take} amp {amp} {cond}: readback {read} Hz peak {np.abs(y).max():.5f}",
              flush=True)
    rec["pinned_readback"] = dev.pinned_report()
    (out / "take.json").write_text(json.dumps(rec, indent=1) + "\n")
    return rec


# ---------------------------------------------------------------------------
# the orchestrator
# ---------------------------------------------------------------------------
def capture(work: pathlib.Path) -> dict:
    try:
        import dawdreamer
    except ImportError as e:
        raise Refused(f"no plugin host: {e}")
    ident = assert_plugin_identity()
    code, lines = rp.verify()
    if code != rp.OK:
        raise Refused("the frozen profile cache does not verify; restore it first "
                      "(tools/refprofile_restore.py): " + lines[-1])
    takes = {}
    for amp in AMPS:
        for take in TAKES:
            d = work / amp_tag(amp) / f"take{take}"
            d.mkdir(parents=True, exist_ok=True)
            r = subprocess.run([sys.executable, __file__, "--worker", "--amp", repr(amp),
                                "--take", str(take), "--out", str(d)], cwd=str(ROOT))
            if r.returncode != 0:
                raise Refused(f"take {take} at amp {amp} did not complete (exit {r.returncode})")
            takes[(amp, take)] = json.loads((d / "take.json").read_text())

    prof = rp.load_profile()
    clips, blobs = {}, {}
    for (amp, take), rec in takes.items():
        for cond, c in rec["clips"].items():
            b = (work / amp_tag(amp) / f"take{take}" / f"{cond}.wav").read_bytes()
            assert _sha(b) == c["sha256"]
            blobs.setdefault(c["sha256"], b)
            clips[clip_key(cond, amp, take)] = {**c, "condition": cond, "take": take,
                                                "archive_member": c["sha256"][:16] + ".wav"}
    # take-to-take
    t2t = {}
    for cond in CONDITIONS:
        for amp in AMPS:
            a, b = clips[clip_key(cond, amp, 1)], clips[clip_key(cond, amp, 2)]
            ya, _ = rp.read_clip_file(work / amp_tag(amp) / "take1" / f"{cond}.wav")
            yb, _ = rp.read_clip_file(work / amp_tag(amp) / "take2" / f"{cond}.wav")
            t2t[f"{cond}/{amp_tag(amp)}"] = {
                "bit_identical": a["sha256"] == b["sha256"],
                "max_abs_diff": float(np.max(np.abs(ya - yb))) if len(ya) == len(yb) else None}
    # nominal takes against the frozen bytes
    vs_frozen = {}
    for cond, (_cut, fz) in CONDITIONS.items():
        want = prof["clips"][fz]["sha256"]
        yf, _, _ = rp.load_clip(fz, prof)
        for take in TAKES:
            c = clips[clip_key(cond, 0.25, take)]
            y, _ = rp.read_clip_file(work / amp_tag(0.25) / f"take{take}" / f"{cond}.wav")
            vs_frozen[clip_key(cond, 0.25, take)] = {
                "frozen_clip": fz, "frozen_sha256": want[:16], "sha256": c["sha256"][:16],
                "bit_identical": c["sha256"] == want,
                "max_abs_diff": float(np.max(np.abs(y - yf))) if len(y) == len(yf) else None}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ARCHIVE.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for h in sorted(blobs):
            zi = zipfile.ZipInfo(h[:16] + ".wav", date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, blobs[h])
    tmp.replace(ARCHIVE)
    first = takes[(AMPS[0], 1)]
    manifest = {
        "schema": "f1-level-captures/1",
        "what": "matched-level Surge XT Type 2 stepped-tone captures for F1A-F1C, "
                "separate from and never replacing refprofile/",
        "built": {"at": rp._now(), "worktree": rp.worktree_state(),
                  "command": "tools/f1_level_capture.py --capture",
                  "python": sys.version.split()[0], "numpy": np.__version__,
                  "dawdreamer": getattr(dawdreamer, "__version__", "unknown"),
                  "platform": sys.platform,
                  "rig_source_sha256": rp.file_sha256(ROOT / "model" / "reference_rigs.py"),
                  "capture_tool_sha256": rp.file_sha256(pathlib.Path(__file__))},
        "plugin": ident,
        "host": {"sr": first["sr"], "block": first["block"],
                 "latency_samples": first["latency_samples"]},
        "rig": "reference_rigs.SurgeRig(subtype='Type 2'), LP Vintage Ladder, Audio In "
               "oscillator, resonance 0, every other setting pinned by name and readback",
        "pinned_readback": first["pinned_readback"],
        "stimulus": {"kind": "stepped sine, integer periods per step (reference_rigs.tone_train)",
                     "settle_s": 0.06, "window_s": 0.20, "n_tones": len(rp._freqs()),
                     "grid_hz": [rp._freqs()[0], rp._freqs()[-1]],
                     "amps": list(AMPS),
                     "levels_dbfs": [round(20 * math.log10(a), 2) for a in AMPS]},
        "takes_per_condition": len(TAKES),
        "take_independence": "each take is a separate process: VST3 reloaded, rig "
                             "re-qualified, all four conditions rendered in the order "
                             + ", ".join(CONDITIONS),
        "frozen_profile_sha256": rp.file_sha256(rp.PROFILE_JSON)[:16],
        "archive_sha256": rp.file_sha256(ARCHIVE),
        "clips": clips,
        "take_to_take": t2t,
        "nominal_vs_frozen": vs_frozen,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


# ---------------------------------------------------------------------------
# reading: no plugin; every read hash-checked
# ---------------------------------------------------------------------------
def load_manifest() -> dict:
    if not MANIFEST.exists() or not ARCHIVE.exists():
        raise Refused(f"no level captures at {MANIFEST.relative_to(ROOT)}")
    return json.loads(MANIFEST.read_text())


def load_capture(cond: str, amp: float, take: int, manifest: dict | None = None,
                 inject: str = "") -> tuple[np.ndarray, dict]:
    """(audio, clip meta). REFUSES if the archived bytes do not hash to the
    manifest's sha256 (`inject='TAMPERED'` moves the expected hash, for the
    control)."""
    m = manifest or load_manifest()
    key = clip_key(cond, amp, take)
    if key not in m["clips"]:
        raise Refused(f"{key} is not a capture in the manifest")
    meta = m["clips"][key]
    want = meta["sha256"]
    if inject == "TAMPERED":
        want = ("0" if want[0] != "0" else "1") + want[1:]
    with zipfile.ZipFile(ARCHIVE) as z:
        try:
            b = z.read(meta["archive_member"])
        except KeyError:
            raise Refused(f"{key}: {meta['archive_member']} is absent from the archive")
    if _sha(b) != want:
        raise Refused(f"{key} hashes {_sha(b)[:16]}, the manifest says {want[:16]}")
    from scipy.io import wavfile
    sr, y = wavfile.read(io.BytesIO(b))
    if sr != rp.SR or len(y) != meta["frames"]:
        raise Refused(f"{key}: sr {sr} / {len(y)} frames, manifest says {rp.SR} / {meta['frames']}")
    return np.asarray(y, dtype=np.float64), meta


def capture_curve(cond: str, amp: float, take: int, manifest=None, inject="") -> tuple:
    """(freqs, gain_db) of one capture, by the frozen reference's projection."""
    import reference_rigs as rr
    y, meta = load_capture(cond, amp, take, manifest, inject)
    parts = [(int(a), int(b), float(f)) for a, b, f in meta["parts"]]
    g = rr.SurgeRig.tone_project(y, parts, amp, clip_key(cond, amp, take))
    return np.asarray(meta["freqs_hz"], dtype=np.float64), np.asarray(g, dtype=np.float64)


def verify() -> int:
    try:
        m = load_manifest()
    except Refused as e:
        print(f"REFUSED  {e}")
        return REFUSED
    bad = 0
    for key, meta in sorted(m["clips"].items()):
        cond, tag, take = key.split("/")
        try:
            load_capture(cond, float(tag[1:]), int(take[4:]), m)
            print(f"ok       {key}  {meta['sha256'][:12]}")
        except Refused as e:
            bad += 1
            print(f"FAIL     {e}")
    print(f"{len(m['clips']) - bad} verified, {bad} failed")
    return FAIL if bad else OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", action="store_true")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--amp", type=float)
    ap.add_argument("--take", type=int)
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        if a.worker:
            worker(a.amp, a.take, pathlib.Path(a.out))
            return OK
        if a.capture:
            import tempfile
            work = pathlib.Path(a.workdir or tempfile.mkdtemp(prefix="f1-level-"))
            m = capture(work)
            print(f"\nwrote {MANIFEST.relative_to(ROOT)}: {len(m['clips'])} clips, "
                  f"{len(set(c['sha256'] for c in m['clips'].values()))} unique")
            for k, v in m["nominal_vs_frozen"].items():
                print(f"nominal {k}: bit_identical={v['bit_identical']} "
                      f"max_abs_diff={v['max_abs_diff']}")
            for k, v in m["take_to_take"].items():
                print(f"take-to-take {k}: bit_identical={v['bit_identical']} "
                      f"max_abs_diff={v['max_abs_diff']}")
            return OK
    except (Refused, rp.Refused) as e:
        print(f"REFUSED  {e}")
        return REFUSED
    return verify()


if __name__ == "__main__":
    sys.exit(main())
