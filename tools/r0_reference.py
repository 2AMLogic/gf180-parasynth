#!/usr/bin/env python3
"""The simulated R0 reference for each diagnostic command: what the published
Arty image (fpga/release/baseline-2025.1.json) should put on the I2S wire when
the release CLI sends exactly the bytes the manifest pins.

    python tools/r0_reference.py render --out build/r0-reference            # all commands
    python tools/r0_reference.py render --out DIR --commands held-default
    python tools/r0_reference.py check  fpga/release/evidence/r0-reference  # re-verify a set

    # R1 (#324): the same tool, the other image. R0 stays the default.
    python tools/r0_reference.py --image r1 bytes                  # the host emits R1's pinned bytes
    python tools/r0_reference.py --image r1 stage --dest DIR       # a tree holding R1's frozen sources
    (in DIR)  python tools/r0_reference.py --image r1 render --out build/r1-reference
    python tools/r0_reference.py --image r1 check                  # re-verify the committed R1 set

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
R1_MANIFEST = ROOT / "fpga" / "release" / "r1-2025.1.json"
SR = 48000
SCHEMA = "r0-reference/1"
# The two images this tool renders and checks. Everything that names one image
# lives HERE, so a reference can never be bound to "whichever manifest the
# caller happened to pass": the manifest declares its image (load_manifest) and
# a reference declares its own (`schema` + `image_id`), and the two must agree.
IMAGES = {
    "r0": {"label": "R0", "manifest": MANIFEST, "ref_schema": SCHEMA,
           "refdir": ROOT / "fpga" / "release" / "evidence" / "r0-reference"},
    "r1": {"label": "R1", "manifest": R1_MANIFEST, "ref_schema": "r1-reference/1",
           "refdir": ROOT / "fpga" / "release" / "evidence" / "r1-reference"},
}
R0_MANIFEST_SCHEMA = "gf180-parasynth release manifest v1"
R1_MANIFEST_SCHEMA = "gf180-parasynth R1 release manifest v1"
R1_RUN_IDENTITIES = ROOT / "fpga" / "reports" / "r1-candidate" / "rtl-run-identities"
# R1's RTL evidence replayed each command's bytes; the identity of that replay
# (its `stimulus` digest) is what a rolling fixture's replay is bound to.
R1_RUN_IDENTITY = {
    "demo": "T-PLAY-DIGITAL__phrase-demo__rtl-replay__demo.run_identity.json",
    "bar808-full": "T-PLAY-DIGITAL__phrase-bar808-full__rtl-replay__bar808-full.run_identity.json",
}
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
    """The release manifest, normalised to {release, image, commands} plus the
    image it declares (`image_id`, `label`) and the file's own hash. R0's
    manifest keeps its commands at the top level; R1's under `host.commands`,
    and `live-midi` (no pinned bytes: it is not a playback command) is left
    out. An unrecognised schema REFUSES: the image is stated by the manifest,
    never inferred from the path it was loaded from."""
    try:
        raw = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise Refused(f"release manifest {path} unreadable: {exc}")
    schema = raw.get("schema")
    if schema == R1_MANIFEST_SCHEMA:
        iid = "r1"
        cmds = {k: v for k, v in ((raw.get("host") or {}).get("commands") or {}).items()
                if isinstance(v, dict) and v.get("cmds_sha256")}
        out = dict(raw, commands=cmds)
    elif schema == R0_MANIFEST_SCHEMA:
        iid = "r0"
        out = dict(raw)
    else:
        raise Refused(f"release manifest {path}: schema {schema!r} is neither R0's nor R1's "
                      "(no image can be bound)")
    if not out.get("commands") or "image" not in out:
        raise Refused(f"release manifest {path}: no image or no command with pinned bytes")
    out["image_id"] = iid
    out["label"] = IMAGES[iid]["label"]
    out["manifest_sha256"] = sha256_file(path)
    return out


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


def _host_image(manifest: dict) -> str:
    """uart_host's `--image` selector for the manifest's image."""
    return {"r0": "release", "r1": "r1"}[manifest["image_id"]]


def rolling_bound(manifest: dict, fixture: str) -> tuple:
    """(sha256, description) of the replay stimulus the RELEASE bound for a
    rolling fixture: R0's committed rtl-replay capture, or R1's recorded run
    identity (fpga/reports/r1-candidate, whose own hash r1-candidate.json pins).
    A missing or unreadable bound record REFUSES: nothing to compare with is
    not a match."""
    if manifest["image_id"] == "r0":
        bound = (ROOT / "fpga/reports/arty/rolling-playback" / fixture / "rtl-replay"
                 / f"{fixture}.cmds")
        if not bound.is_file():
            raise Refused(f"{fixture}: the release-bound replay capture {bound} is missing")
        return sha256_file(bound), str(bound.relative_to(ROOT))
    name = R1_RUN_IDENTITY.get(fixture)
    if name is None:
        raise Refused(f"{fixture}: R1 binds no replay stimulus for it")
    path = R1_RUN_IDENTITIES / name
    cand = json.loads((ROOT / "fpga/release/r1-candidate.json").read_text())
    pinned = (cand.get("evidence") or {}).get("files", {}).get(
        str(path.relative_to(ROOT)))
    if not path.is_file() or pinned is None or sha256_file(path) != pinned:
        raise Refused(f"{fixture}: R1's run identity {name} is missing or is not the one "
                      "r1-candidate.json pins")
    stim = (json.loads(path.read_text()).get("identity") or {}).get("stimulus")
    if not stim:
        raise Refused(f"{fixture}: R1's run identity {name} records no stimulus digest")
    return stim, f"{path.relative_to(ROOT)} (stimulus {stim[:12]})"


def command_bytes_problems(manifest: dict, keys=None) -> list:
    """Run the shipped CLI (dry run) for each command and compare the bytes it
    emits with the manifest's pin. This is the check that the host SHIPPED
    under `--image r1` sends R1's bytes -- not the tree's default selection --
    and it needs no simulator."""
    import tempfile
    import uart_host as uh
    probs = []
    for key in (keys or list(manifest["commands"])):
        spec = manifest["commands"][key]
        with tempfile.TemporaryDirectory() as td:
            prefix = pathlib.Path(td) / "capture"
            try:
                argv = ["--dry-run"] + cli_argv(spec["command"]) + ["--capture", str(prefix)]
            except Refused as exc:
                probs.append(f"{key}: {exc}")
                continue
            with contextlib.redirect_stdout(io.StringIO()):
                rc = uh.main(argv)
            if rc != 0:
                probs.append(f"{key}: the CLI refused the manifest command (exit {rc})")
                continue
            got = sha256_file(f"{prefix}.cmds")
        if got != spec["cmds_sha256"]:
            probs.append(f"{key}: the CLI emits {got[:12]}, the manifest pins "
                         f"{spec['cmds_sha256'][:12]}")
    return probs


def stage_plan(manifest: dict, root=ROOT) -> list:
    """The source files of `root` that are not the image's (the ones a staged
    tree must take from image.source_commit)."""
    return sorted(p.split(":")[0] for p in image_source_problems(manifest, root))


def stage(manifest: dict, dest: pathlib.Path) -> list:
    """A detached worktree at HEAD under `dest` whose simulated sources are
    then taken, file by file, from image.source_commit -- and VERIFIED equal to
    the image's hashes, else REFUSED. The host (uart_host, the validator, the
    frozen R1 kit) stays at HEAD: it is the shipped one, and the dry-run bytes
    check refuses the render if it no longer emits the pinned bytes."""
    import subprocess
    commit = manifest["image"].get("source_commit")
    if not commit:
        raise Refused("the manifest names no image.source_commit")
    dest = pathlib.Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise Refused(f"{dest} exists and is not empty")
    def git(*a, cwd=ROOT):
        r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
        if r.returncode:
            raise Refused(f"git {' '.join(a)}: {r.stderr.strip()[-300:]}")
        return r.stdout
    git("cat-file", "-e", f"{commit}^{{commit}}")
    git("worktree", "add", "--detach", str(dest), "HEAD")
    moved = stage_plan(manifest, dest)
    if moved:
        git("checkout", commit, "--", *moved, cwd=dest)
    left = image_source_problems(manifest, dest)
    if left:
        raise Refused(f"{dest}: still not the image's sources after checkout of {commit[:7]}: "
                      + "; ".join(left[:4]))
    return moved


def render_one(key: str, spec: dict, manifest: dict, out: pathlib.Path,
               tail_s: float) -> dict:
    import uart_host as uh
    import verify_uart_bridge as vub
    work = out / "work" / key
    work.mkdir(parents=True, exist_ok=True)
    prefix = work / "capture"
    iid = manifest["image_id"]
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
    if fixture:
        # A MUSICAL-length fixture is delivered in rolling windows, each
        # anchored on a STATUS answer. The dry-run's virtual anchors are not a
        # replayable stimulus (the first box run: all 306 events off-frame),
        # so the replayed bytes are the CLI's transmit log against the
        # scripted device -- exactly fpga/verify_rolling_playback.py's
        # stimulus, which the release bound: REFUSE unless byte-identical.
        import verify_rolling_playback as vrp
        # the SENDER and the TARGET are both the manifest's image: R1's kit
        # and known-state start are what `--image r1` sends (and R0's what the
        # default sends); never the other way round, never inferred
        run_cli = vrp.run_cli(fixture, image=_host_image(manifest), target=_host_image(manifest))
        chk = vrp.check(run_cli)
        if not chk.get("ok"):
            raise Refused(f"{key}: the CLI's run against the scripted device is not clean: "
                          f"{chk.get('reasons')}")
        replay_prefix = work / "rolling"
        vrp.write_rtl_capture(run_cli, replay_prefix)
        bound_sha, bound_what = rolling_bound(manifest, fixture)
        if sha256_file(f"{replay_prefix}.cmds") != bound_sha:
            raise Refused(f"{key}: the transmit log differs from the release-bound replay "
                          f"capture {bound_what}")
    with vub.image_config(_host_image(manifest)):
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
    i2s = read_i2s(run["files"]["i2s"])
    lr = int(np.count_nonzero(i2s[:, 0] != i2s[:, 1]))
    rec = {"schema": IMAGES[iid]["ref_schema"], "image_id": iid,
           "command_id": key, "command": spec["command"],
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
    if not ok:
        rec["verdict"] = "FAIL"
        return rec
    if lr:
        raise Refused(f"{key}: L != R on {lr} periods; {manifest['label']} is dual-mono "
                      f"(i2s_tx.v sends one sample on both channels), so this is not "
                      f"{manifest['label']}")
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


def silence_record(held: np.ndarray | None, manifest: dict | None = None) -> dict:
    manifest = manifest or load_manifest(MANIFEST)
    iid = manifest["image_id"]
    lead = None
    if held is not None:
        nz = np.flatnonzero(held)
        lead = int(nz[0]) if nz.size else int(len(held))
    return {"schema": IMAGES[iid]["ref_schema"], "image_id": iid,
            "release": manifest["release"], "command_id": "silence", "command": None,
            "declared": "after BTN0 reset no write has been applied; the core's sample "
                        "register resets to 0 and the wire carries 0 on both channels",
            "support": {"held_default_leading_zero_periods": lead},
            "expected_lsb": 0, "verdict": "PASS" if (lead or 0) > 0 else "REFUSED"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", choices=sorted(IMAGES), default="r0",
                    help="which published image's references (default r0)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("--out", type=pathlib.Path, required=True)
    r.add_argument("--commands", nargs="*", default=None)
    r.add_argument("--tail-s", type=float, default=1.0)
    c = sub.add_parser("check")
    c.add_argument("dir", type=pathlib.Path, nargs="?", default=None,
                   help="default: the image's committed reference directory")
    sub.add_parser("bytes", help="the shipped CLI emits the manifest's pinned bytes")
    g = sub.add_parser("stage", help="a tree holding the image's frozen sources")
    g.add_argument("--dest", type=pathlib.Path, required=True)
    a = ap.parse_args(argv)
    try:
        idm = IMAGES[a.image]
        manifest = load_manifest(idm["manifest"])
        if manifest["image_id"] != a.image:
            raise Refused(f"{idm['manifest']} declares image {manifest['image_id']}, not {a.image}")
        if a.cmd == "check":
            return check(a.dir or idm["refdir"], manifest)
        if a.cmd == "bytes":
            probs = command_bytes_problems(manifest)
            for k in manifest["commands"]:
                print(f"r0_reference: {manifest['label']} {k}: "
                      f"{'BAD' if any(x.startswith(k + ':') for x in probs) else 'OK'}")
            for x in probs:
                print(f"r0_reference:   {x}")
            if probs:
                print(f"r0_reference: REFUSED -- the host does not emit {manifest['label']}'s "
                      "pinned bytes")
                return 2
            print(f"r0_reference: the host emits every pinned {manifest['label']} command byte-"
                  "for-byte")
            return 0
        if a.cmd == "stage":
            moved = stage(manifest, a.dest)
            print(f"r0_reference: staged {a.dest}: {len(moved)} source(s) taken from "
                  f"{manifest['image']['source_commit'][:7]}: {', '.join(moved)}")
            return 0
        probs = image_source_problems(manifest)
        if probs:
            raise Refused("this tree's RTL is not the image's (render on a staged tree: "
                          f"`r0_reference.py --image {a.image} stage --dest DIR`): "
                          + "; ".join(probs))
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
            rec = render_one(key, manifest["commands"][key], manifest, a.out, a.tail_s)
            print(f"r0_reference: {manifest['label']} {key}: {rec['verdict']} -- "
                  f"{rec['periods']} periods, peak {rec['peak_lsb']} LSB, replay "
                  f"{rec['replay']['comparison']}")
            if rec["verdict"] != "PASS":
                worst = 1
            if key == "held-default" and rec["verdict"] == "PASS":
                _, x = read_wav_int16(a.out / rec["wav"])
                srec = silence_record(x[:, 0], manifest)
                (a.out / "silence.json").write_text(json.dumps(srec, indent=1) + "\n")
        return worst
    except Refused as exc:
        print(f"r0_reference: REFUSED -- {exc}")
        return 2


def identity_label_problems(rec: dict, manifest: dict, key: str) -> list:
    """The record's own statement of which image it is for -- its schema and
    image id (and release string, where it carries one) -- against the
    manifest's. A record from before R1 has no `image_id`: that is R0's, and
    only R0's. Labels alone prove nothing (a relabelled R0 set carries R1's);
    they are checked FIRST so a mix-up is named for what it is, and the bytes
    and sources are checked after, so relabelling does not help."""
    iid, label = manifest["image_id"], manifest["label"]
    want_schema = IMAGES[iid]["ref_schema"]
    why = []
    if rec.get("schema") != want_schema:
        why.append(f"schema {rec.get('schema')!r} is not {label}'s ({want_schema})")
    rid = rec.get("image_id", "r0" if rec.get("schema") == SCHEMA else None)
    if rid != iid:
        why.append(f"reference is for image {rid!r}, the procedure is {label}'s")
    if "release" in rec and rec["release"] != manifest["release"]:
        why.append(f"reference is for release {rec['release']!r}, not {manifest['release']!r}")
    return why


def silence_problems(d: pathlib.Path, manifest: dict) -> list:
    """silence.json is declared, not rendered, and carries no bytes or sources:
    the only thing there is to bind is whose it is."""
    p = d / "silence.json"
    if not p.is_file():
        return [f"silence: no reference at {p}"]
    try:
        rec = json.loads(p.read_text())
    except ValueError as exc:
        return [f"silence: reference record unreadable ({exc})"]
    why = identity_label_problems(rec, manifest, "silence")
    if rec.get("verdict") != "PASS":
        why.append(f"declared silence is {rec.get('verdict')!r}, not PASS")
    return [f"silence: {w}" for w in why]


def reference_problems(d: pathlib.Path, key: str, manifest: dict) -> list:
    """Why the committed reference for `key` is NOT the release's, or []:
    which image it declares, the pinned bytes, the image's bitstream and every
    simulated source, the replay verdict, the wav's hash. tools/r0_capture.py
    calls this inside the analysis the trial runs (not only as a CI step), so a
    reference that has drifted from the release -- or belongs to the other
    image -- REFUSES the verdict it would otherwise support."""
    if key == "silence":
        return silence_problems(d, manifest)
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
    why = identity_label_problems(rec, manifest, key)
    if rec.get("command_id") != key:
        why.append(f"record is for command {rec.get('command_id')!r}")
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
    simulating. Prints one line per command (and silence) and a summary."""
    bad = []
    for key in [*manifest["commands"], "silence"]:
        why = reference_problems(d, key, manifest)
        print(f"r0_reference: {manifest['label']} {key}: "
              f"{'OK' if not why else 'BAD -- ' + '; '.join(why)}")
        bad += why
    if bad:
        print(f"r0_reference: REFUSED -- {len(bad)} problem(s)")
        return 2
    print(f"r0_reference: every {manifest['label']} release command has a verified reference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
