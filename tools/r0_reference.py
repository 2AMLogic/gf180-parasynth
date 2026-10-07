#!/usr/bin/env python3
"""The simulated R0 reference for each diagnostic command: what the published
Arty image (fpga/release/baseline-2025.1.json) should put on the I2S wire when
the release CLI sends exactly the bytes the manifest pins.

    python tools/r0_reference.py render --out build/r0-reference            # all commands
    python tools/r0_reference.py render --out DIR --commands held-default
    python tools/r0_reference.py check  fpga/release/evidence/r0-reference  # re-verify a set
    python tools/r0_reference.py render --out build/r0-reference-live --schedule live \
        --commands held-default held-m5a-saw held-m5a-pulse       # #306, build box only

WHAT RUNS, per command in the manifest's `commands` table:

  1. the shipped CLI, `uart_host.main(["--dry-run", ..., "--capture", P])`,
     with the manifest's own argument list (the `--port` placeholder removed);
  2. REFUSE unless sha256(P.cmds) == the manifest's `cmds_sha256`: a reference
     for bytes the release does not pin is a reference for a different command;
  3. `fpga/verify_uart_bridge.py`'s replay (the Arty wrapper at its UART pins,
     I2S decoded from BCLK/LRCLK/SDATA only), with `--tail-s` of decay after
     the last scheduled write, compared bit-exactly with the integer model;
  4. REFUSE unless every RTL/ROM source the replay compiled has the sha256
     the image's publication records (`image.source_sha256`). Current main
     has moved past the image (#252 drift changed rtl-sketch/voice_dp.v), so
     the renderer must run on a tree whose sources equal the IMAGE's -- e.g.
     `git checkout <image.source_commit> -- rtl-sketch/voice_dp.v` in a
     scratch clone. A reference rendered from main's RTL would describe a
     bitstream nobody built;
  5. REFUSE unless the replay PASSES (wire == model, every write on its
     frame, no X, 32-bit slots) and L == R on every period (R0 is dual-mono:
     rtl-sketch/i2s_tx.v sends one sample on both channels);
  6. write <cmd>.wav (16-bit mono, 48 kHz, the decoded wire words verbatim)
     and <cmd>.json (identity: command, bytes, image, sources, replay
     comparison, wav sha256, and the declared calibration window).

`silence` has no command. Its reference is declared, not rendered: after
BTN0 reset the core's sample register resets to 0 and every replay above
decodes 0 on the wire until the first note (checked here from the held-note
render: its periods before the first applied write are all 0).

Exit: 0 every requested reference rendered/verified, 1 a replay ran and
FAILED (the RTL disagreed with the model), 2 REFUSED (a precondition).
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import pathlib
import shlex
import sys
import wave

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in ("fpga", "model", "rtl-sketch", "audition", "tools", "fpga/release"):
    sys.path.insert(0, str(ROOT / _p))

MANIFEST = ROOT / "fpga" / "release" / "baseline-2025.1.json"
SR = 48000
SCHEMA = "r0-reference/1"
# Sources whose bytes do not reach the simulation (constraints only).
NOT_SIMULATED = {"fpga/boards/arty-a7-100.xdc"}
# The declared calibration window of each reference: from the first period
# whose |sample| reaches CAL_ONSET_LSB, CAL_WINDOW_S long. Declared here, once,
# for every command -- never chosen per take or per candidate.
CAL_ONSET_LSB = 256
CAL_WINDOW_S = 0.030


class Refused(Exception):
    pass


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_manifest(path=MANIFEST) -> dict:
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"release manifest {path} unreadable: {exc}")


def cli_argv(command: str) -> list:
    """The manifest's command line -> uart_host.main argv (no interpreter,
    no script, no --port placeholder)."""
    argv = shlex.split(command)
    if len(argv) < 3 or not argv[1].endswith("fpga/uart_host.py"):
        raise Refused(f"not a uart_host.py command: {command!r}")
    argv = argv[2:]
    if "--port" in argv:
        i = argv.index("--port")
        del argv[i:i + 2]
    return argv


def rolling_fixture(command: str) -> str | None:
    """The musical-length fixture a release command plays, if any."""
    argv = shlex.split(command)
    if "--fixture" in argv:
        fx = argv[argv.index("--fixture") + 1]
        if fx in ("demo", "bar808-full"):
            return fx
    return None


def image_source_problems(manifest: dict, root=ROOT) -> list:
    """Every simulated source of the image, hashed in THIS tree."""
    probs = []
    for rel, want in sorted(manifest["image"]["source_sha256"].items()):
        if rel in NOT_SIMULATED:
            continue
        p = pathlib.Path(root) / rel
        got = sha256_file(p) if p.is_file() else "missing"
        if got != want:
            probs.append(f"{rel}: tree {got[:12]}, image {want[:12]}")
    return probs


def identity_problems(run_identity: dict, manifest: dict) -> list:
    """The replay's own record of what it compiled, against the image."""
    ident = run_identity.get("identity") or {}
    compiled = dict(ident.get("sources") or {})
    compiled.update(ident.get("roms") or {})
    probs = []
    for rel, want in manifest["image"]["source_sha256"].items():
        if rel in NOT_SIMULATED:
            continue
        got = compiled.get(rel)
        if got is None:
            probs.append(f"{rel}: not in the replay's compiled set")
        elif got != want:
            probs.append(f"{rel}: replay compiled {got[:12]}, image {want[:12]}")
    return probs


def read_i2s(path) -> np.ndarray:
    """uart_i2s.txt rows `period L R nbits_l nbits_r` -> int16 (n, 2). Any X,
    gap or non-32-bit slot REFUSES: a reference must be complete."""
    rows = []
    for ln, line in enumerate(pathlib.Path(path).read_text().splitlines()):
        parts = line.split()
        if len(parts) != 5:
            raise Refused(f"{path}:{ln + 1}: malformed I2S row {line!r}")
        try:
            p, left, right, nbl, nbr = (int(x) for x in parts)
        except ValueError:
            raise Refused(f"{path}:{ln + 1}: X on the I2S wire {line!r}")
        if p != len(rows):
            raise Refused(f"{path}:{ln + 1}: period {p} follows {len(rows) - 1}")
        if nbl != 32 or nbr != 32:
            raise Refused(f"{path}:{ln + 1}: slot widths {nbl}/{nbr}, not 32")
        rows.append((left, right))
    if not rows:
        raise Refused(f"{path}: no I2S periods")
    return np.asarray(rows, dtype=np.int16)


def write_wav(path, mono_int16: np.ndarray, sr: int = SR) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.asarray(mono_int16, dtype="<i2").tobytes())


def read_wav_int16(path) -> tuple:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise Refused(f"{path}: {8 * w.getsampwidth()}-bit, reference must be 16-bit")
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        x = np.frombuffer(w.readframes(n), dtype="<i2").reshape(-1, ch)
    return sr, x


def calibration_window(x: np.ndarray, sr: int = SR) -> dict:
    """The declared window: first |x| >= CAL_ONSET_LSB, CAL_WINDOW_S long,
    backed off 2 ms so the onset itself is inside it."""
    idx = np.flatnonzero(np.abs(x.astype(np.int32)) >= CAL_ONSET_LSB)
    if idx.size == 0:
        return {"start": None, "stop": None, "rule": "no sample reaches the onset level"}
    start = max(0, int(idx[0]) - int(0.002 * sr))
    stop = min(len(x), start + int(CAL_WINDOW_S * sr))
    return {"start": start, "stop": stop,
            "rule": f"first |sample| >= {CAL_ONSET_LSB} LSB, minus 2 ms, "
                    f"{CAL_WINDOW_S * 1e3:.0f} ms long"}


FROZEN_REFERENCES = ROOT / "fpga" / "release" / "evidence" / "r0-reference"


def render_one(key: str, spec: dict, manifest: dict, out: pathlib.Path,
               tail_s: float, schedule: str = "dry-run") -> dict:
    import uart_host as uh
    import verify_uart_bridge as vub
    work = out / "work" / key
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "capture"
    argv = ["--dry-run"] + cli_argv(spec["command"]) + ["--capture", str(prefix)]
    with contextlib.redirect_stdout(io.StringIO()) as so:
        rc = uh.main(argv)
    if rc != 0:
        raise Refused(f"{key}: the CLI refused its own manifest command (exit {rc})")
    got = sha256_file(f"{prefix}.cmds")
    if got != spec["cmds_sha256"]:
        raise Refused(f"{key}: CLI bytes {got[:12]} differ from the manifest's pinned "
                      f"{spec['cmds_sha256'][:12]} -- not the released command")
    replay_prefix = prefix
    fixture = rolling_fixture(spec["command"])
    live = None
    if schedule == "live":
        # #306: a held note's LIVE schedule -- the bytes the CLI really sends
        # (the gate bracket's STATUS queries, the gate-off at estimate + hold)
        # from its run against the scripted device -- replayed through the
        # RTL. The command identity above (cmds_sha256) is still the pinned
        # dry-run's; the replayed stimulus is bound separately, beside the
        # host's hold record and the RTL's own measured hold.
        import hold_timing as ht
        if key not in ht.HELD_COMMANDS:
            raise Refused(f"{key}: a live-schedule reference is defined for held notes only")
        m = ht.measure(ht.HELD_COMMANDS[key])
        v, why = ht.hold_verdict(m)
        if v != ht.PASS:
            raise Refused(f"{key}: the live run against the scripted device is not clean: {why}")
        replay_prefix = work / "live"
        ht.write_held_rtl_capture(m["_harness"], replay_prefix)
        live = {"kind": "live", "live_cmds_sha256": sha256_file(f"{replay_prefix}.cmds"),
                "hold_timing": m["host_record"],
                "release_tolerance_frames": uh.HOLD_BOUND_MAX_FRAMES}
    if fixture:
        # A MUSICAL-length fixture is delivered in rolling windows, each
        # anchored on a STATUS answer. The dry-run's virtual anchors are not a
        # replayable stimulus (the first box run: all 306 events off-frame),
        # so the replayed bytes are the CLI's transmit log against the
        # scripted device -- exactly fpga/verify_rolling_playback.py's
        # stimulus, which the release bound: REFUSE unless byte-identical.
        import verify_rolling_playback as vrp
        run_cli = vrp.run_cli(fixture)
        chk = vrp.check(run_cli)
        if not chk.get("ok"):
            raise Refused(f"{key}: the CLI's run against the scripted device is not clean: "
                          f"{chk.get('reasons')}")
        replay_prefix = work / "rolling"
        vrp.write_rtl_capture(run_cli, replay_prefix)
        bound = (ROOT / "fpga/reports/arty/rolling-playback" / fixture / "rtl-replay"
                 / f"{fixture}.cmds")
        if sha256_file(f"{replay_prefix}.cmds") != sha256_file(bound):
            raise Refused(f"{key}: the transmit log differs from the release-bound replay "
                          f"capture {bound.relative_to(ROOT)}")
    run = vub.simulate_replay(str(replay_prefix), work / "replay",
                              tail_frames=int(tail_s * SR), timeout_s=4 * 3600)
    if run is None:
        raise Refused(f"{key}: the replay did not run (see its output)")
    rid = json.loads((work / "replay" / "run_identity.json").read_text())
    probs = identity_problems(rid, manifest)
    if probs:
        raise Refused(f"{key}: the replay did not compile the image's sources: "
                      + "; ".join(probs[:4]))
    ok, comp, detail = vub.analyze(run)
    if live is not None:
        import hold_timing as ht
        live["rtl_hold"] = ht.rtl_hold_verdict(vub._rows(run["files"]["wrs"]),
                                               live["hold_timing"]["requested_hold_frames"],
                                               live["hold_timing"]["hold_bound_frames"])
        if live["rtl_hold"]["verdict"] != ht.PASS:
            raise Refused(f"{key}: the RTL did not deliver the live schedule's hold: "
                          f"{live['rtl_hold']['reason']}")
    i2s = read_i2s(run["files"]["i2s"])
    lr = int(np.count_nonzero(i2s[:, 0] != i2s[:, 1]))
    rec = {"schema": SCHEMA, "command_id": key, "command": spec["command"],
           "cmds_sha256": got, "packets": spec.get("packets"),
           "replayed_stimulus_sha256": sha256_file(f"{replay_prefix}.cmds"),
           "image": {k: manifest["image"][k] for k in ("bitstream_sha256", "routed_dcp_sha256",
                                                        "source_commit")},
           "release": manifest["release"],
           "rtl_sources_sha256": {**rid["identity"].get("sources", {}),
                                  **rid["identity"].get("roms", {})},
           "defines": rid["identity"].get("defines"),
           "replay": {"state": "PASS" if ok else "FAIL",
                      "comparison": {k: comp.get(k) for k in (
                          "periods", "wire_mismatch", "swap", "width", "writes_sent",
                          "writes_seen", "frame_pred_bad", "overrun", "worst_strobe_cycle")},
                      "detail": list(detail)[:5]},
           "tail_s": tail_s, "periods": int(len(i2s)), "l_ne_r": lr,
           "peak_lsb": int(np.abs(i2s[:, 0].astype(np.int32)).max())}
    if live is not None:
        rec["schedule"] = live
    if not ok:
        rec["verdict"] = "FAIL"
        return rec
    if lr:
        raise Refused(f"{key}: L != R on {lr} periods; R0 is dual-mono, so this is not R0")
    wav = out / f"{key}.wav"
    write_wav(wav, i2s[:, 0])
    attach_plan(out, key)
    rec["wav"] = wav.name
    rec["wav_sha256"] = sha256_file(wav)
    rec["calibration"] = calibration_window(i2s[:, 0])
    rec["verdict"] = "PASS"
    (out / f"{key}.json").write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    return rec


def attach_plan(out: pathlib.Path, key: str) -> str:
    """The CLI's own plan for the pinned bytes, beside the reference: the
    capture analysis compares the operator's host log (uart_host --capture)
    with it, so a take of some other command cannot be scored as this one."""
    src = out / "work" / key / "capture.plan.json"
    dst = out / f"{key}.plan.json"
    dst.write_bytes(src.read_bytes())
    return sha256_file(dst)


def silence_record(held: np.ndarray | None) -> dict:
    lead = None
    if held is not None:
        nz = np.flatnonzero(held)
        lead = int(nz[0]) if nz.size else int(len(held))
    return {"schema": SCHEMA, "command_id": "silence", "command": None,
            "declared": "after BTN0 reset no write has been applied; the core's sample "
                        "register resets to 0 and the wire carries 0 on both channels",
            "support": {"held_default_leading_zero_periods": lead},
            "expected_lsb": 0, "verdict": "PASS" if (lead or 0) > 0 else "REFUSED"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=pathlib.Path, required=True)
    r.add_argument("--commands", nargs="*", default=None)
    r.add_argument("--tail-s", type=float, default=1.0)
    r.add_argument("--schedule", choices=("dry-run", "live"), default="dry-run",
                   help="live (#306): held notes only, rendered from the CLI's live "
                        "bytes against the scripted device; never into the frozen set")
    c = sub.add_parser("check")
    c.add_argument("dir", type=pathlib.Path)
    a = ap.parse_args(argv)
    try:
        manifest = load_manifest()
        if a.cmd == "check":
            return check(a.dir, manifest)
        if a.schedule == "live" and a.out.resolve() == FROZEN_REFERENCES.resolve():
            # the frozen set is R0's legacy evidence: a live render goes beside it
            raise Refused(f"a live-schedule render may not overwrite the frozen R0 "
                          f"references in {FROZEN_REFERENCES.relative_to(ROOT)}")
        probs = image_source_problems(manifest)
        if probs:
            raise Refused("this tree's RTL is not the image's (render on a scratch clone "
                          f"holding image.source_commit's files): " + "; ".join(probs))
        keys = a.commands or list(manifest["commands"])
        unknown = [k for k in keys if k not in manifest["commands"]]
        if unknown:
            raise Refused(f"not release commands: {unknown}; known {list(manifest['commands'])}")
        # absolute: the replay runs vvp with cwd=rtl-sketch, so a relative
        # output path names a file that is not there (the first box run did)
        a.out = a.out.resolve()
        a.out.mkdir(parents=True, exist_ok=True)
        worst = 0
        for key in keys:
            rec = render_one(key, manifest["commands"][key], manifest, a.out, a.tail_s,
                             schedule=a.schedule)
            print(f"r0_reference: {key}: {rec['verdict']} -- {rec['periods']} periods, "
                  f"peak {rec['peak_lsb']} LSB, replay {rec['replay']['comparison']}")
            if rec["verdict"] != "PASS":
                worst = 1
            if key == "held-default" and rec["verdict"] == "PASS" and a.schedule != "live":
                _, x = read_wav_int16(a.out / rec["wav"])
                srec = silence_record(x[:, 0])
                (a.out / "silence.json").write_text(json.dumps(srec, indent=1) + "\n")
        return worst
    except Refused as exc:
        print(f"r0_reference: REFUSED -- {exc}")
        return 2


def reference_problems(d: pathlib.Path, key: str, manifest: dict) -> list:
    """Why the committed reference for `key` is NOT the release's, or []:
    the pinned bytes, the image's bitstream and every simulated source, the
    replay verdict, the wav's hash. tools/r0_capture.py calls this inside the
    analysis the trial runs (not only as a CI step), so a reference that has
    drifted from the release REFUSES the verdict it would otherwise support."""
    spec = manifest["commands"].get(key)
    if spec is None:
        return [f"{key}: not a command of release {manifest.get('release')!r}"]
    p = d / f"{key}.json"
    if not p.is_file():
        return [f"{key}: no reference at {p}"]
    try:
        rec = json.loads(p.read_text())
    except ValueError as exc:
        return [f"{key}: reference record unreadable ({exc})"]
    why = []
    if rec.get("cmds_sha256") != spec["cmds_sha256"]:
        why.append("bytes differ from the manifest's pinned command")
    img = rec.get("image") or {}
    if img.get("bitstream_sha256") != manifest["image"]["bitstream_sha256"]:
        why.append(f"rendered for bitstream {str(img.get('bitstream_sha256'))[:12]}, the "
                   f"release is {manifest['image']['bitstream_sha256'][:12]}")
    if img.get("source_commit") != manifest["image"].get("source_commit"):
        why.append(f"rendered for source commit {img.get('source_commit')}, the release is "
                   f"{manifest['image'].get('source_commit')}")
    ids = identity_problems({"identity": {"sources": rec.get("rtl_sources_sha256", {})}},
                            manifest)
    if ids:
        why.append("sources: " + "; ".join(ids[:3]))
    if (rec.get("replay") or {}).get("state") != "PASS":
        why.append("replay not PASS")
    wav = d / str(rec.get("wav"))
    if not wav.is_file() or sha256_file(wav) != rec.get("wav_sha256"):
        why.append("wav missing or altered")
    return [f"{key}: {w}" for w in why]


def check(d: pathlib.Path, manifest: dict) -> int:
    """Re-verify a committed reference set against the manifest, without
    simulating. Prints one line per command and a summary."""
    bad = []
    for key in manifest["commands"]:
        why = reference_problems(d, key, manifest)
        print(f"r0_reference: {key}: {'OK' if not why else 'BAD -- ' + '; '.join(why)}")
        bad += why
    if bad:
        print(f"r0_reference: REFUSED -- {len(bad)} problem(s)")
        return 2
    print("r0_reference: every release command has a verified reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
