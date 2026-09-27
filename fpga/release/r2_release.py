#!/usr/bin/env python3
"""fpga/release/r2_release.py -- the R2 release manifest: ONE record binding
the built R2 Arty image to its frozen sources, the host's `--image r2` bytes,
the presets, the supported domain and the evidence, with R1 kept as the named
rollback and R0 pinned behind it (fpga/release/R2.md).

    python fpga/release/r2_release.py              # BOUND (0) / STALE (1) / REFUSED (2)
    python fpga/release/r2_release.py --write      # re-derive and write r2-2025.1.json
    python fpga/release/r2_release.py stale-control {image,host} --out DIR

R2 is OSC2X=1 FILTER2X=1 PULSE2X=1 with the reviewed RTL set
(r2_candidate.EXPECTED_CHANGES). It includes pulse2x by OPERATOR OVERRIDE of
the acceptance rule (#282); the manifest carries the known limitations.

BOUND means the committed r2-2025.1.json equals a fresh derivation AND:

  * the image: arty.bit <-> publication.json <-> report.json <-> the DSP
    evidence's routed.dcp digest <-> the shipped reports (re-parsed by the
    publisher) <-> the per-port external-I/O extraction of the same
    checkpoint -- r1_release.image_identity / external_io with R2's
    configuration, digital proof and frozen sources;
  * the sources: read from git at R2_FROZEN_AT, never from the working tree;
    they differ from R1's freeze exactly in the reviewed set;
  * the #315 constraint reports shipped with the image: every XDC query bound
    exactly its objects, the UART synchroniser is excepted by exactly the
    async arc, and stage 1 -> 2 and downstream are still timed;
  * the host: every supported command's exact bytes under `--image r2`, each
    checked against the frozen revision-14 target, and equal to R1's bound
    bytes (R2 has no kit or preset change); the presets' image bytes;
  * the RTL evidence: every committed run identity under EVIDENCE_DIR names
    R2's frozen sources and the VOICE_PULSE_2X define;
  * the rollback: R1's committed manifest is BOUND (r1_release.check) and
    pinned here by digest; R0 by digest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, ROOT / "fpga", ROOT / "model", ROOT / "audition", ROOT / "rtl-sketch",
           ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import r1_release as r1r                                    # noqa: E402

MANIFEST = HERE / "r2-2025.1.json"
PUB_DIR = ROOT / "fpga/reports/arty/r2-2025.1"
VERIFICATION = ROOT / "fpga/reports/arty/r2-clean/verification.json"
EVIDENCE_DIR = ROOT / "fpga/reports/r2"
R1_MANIFEST = HERE / "r1-2025.1.json"
RELEASE = "arty-a7-100t R2 2025.1"
HOST_IMAGE = "r2"
IMAGE_KEY = "r2"                                            # build_arty.IMAGE_CONFIGS
PREFIX = "r2_release: "
# The commit R2 was built from. Every compiled source is verified here with
# `git show`, and the per-port ext-I/O instrument is pinned here by version.
R2_FROZEN_AT = "3182638c4349d174bff0b3d127fb13ab3f14a560"   # the build ran on this commit (build/r2-arty, 2026-09-27)
PULSE2X_DEFINE = "VOICE_PULSE_2X"

Refused = r1r.Refused
sha, _rel, _need, _json = r1r.sha, r1r._rel, r1r._need, r1r._json


def _blob(commit: str, rel: str) -> bytes:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel}"], capture_output=True)
    if r.returncode:
        raise Refused(f"cannot read {rel} at {commit[:12]} (a shallow clone cannot verify "
                      "the freeze)")
    return r.stdout


# ---- the frozen sources ---------------------------------------------------------
def frozen_rtl(frozen_at: str | None = None) -> dict:
    """R2's compiled sources, ROMs and XDC read from git at R2_FROZEN_AT, and
    how they differ from R1's freeze: exactly the reviewed set."""
    import build_arty as ba
    import r1_candidate as r1c
    import r2_candidate as r2c
    frozen_at = frozen_at or R2_FROZEN_AT
    if not frozen_at:
        raise Refused("R2_FROZEN_AT is not set: no R2 image has been built")
    h = lambda rel: hashlib.sha256(_blob(frozen_at, rel)).hexdigest()      # noqa: E731
    srcs = {_rel(p): h(_rel(p)) for p in ba.sources()}
    roms = {_rel(p): h(_rel(p)) for p in ba.roms()}
    xdc = {_rel(ba.XDC): h(_rel(ba.XDC))}
    changed = [rel for rel, digest in {**srcs, **roms, **xdc}.items()
               if hashlib.sha256(_blob(r1c.RTL_FROZEN_AT, rel)).hexdigest() != digest]
    if sorted(changed) != sorted(r2c.EXPECTED_CHANGES):
        raise Refused(f"R2's frozen sources differ from R1's freeze in {sorted(changed)}, "
                      f"not exactly the reviewed set {sorted(r2c.EXPECTED_CHANGES)}")
    return {"frozen_at": frozen_at, "configuration": dict(ba.IMAGE_CONFIGS[IMAGE_KEY]),
            "part": ba.PART, "sources": srcs, "roms": roms, "constraints": xdc,
            "differs_from_r1_freeze": {k: r2c.EXPECTED_CHANGES[k] for k in sorted(changed)},
            "r1_freeze": r1c.RTL_FROZEN_AT}


# ---- the image ------------------------------------------------------------------
def image_identity(pub_dir: Path | None = None, frozen_at: str | None = None) -> dict:
    import build_arty as ba
    rtl = frozen_rtl(frozen_at)
    frozen = {**rtl["sources"], **rtl["roms"], **rtl["constraints"]}
    pub_dir = Path(pub_dir or PUB_DIR)
    img = r1r.image_identity(pub_dir, config=ba.IMAGE_CONFIGS[IMAGE_KEY],
                             verification=VERIFICATION, frozen=frozen,
                             frozen_at=rtl["frozen_at"], frozen_name="R2 image")
    pub = _json(pub_dir / "publication.json")
    if pub.get("image") != IMAGE_KEY:
        raise Refused(f"the publication names image {pub.get('image')!r}, not {IMAGE_KEY!r}")
    img["image"] = IMAGE_KEY
    img["differs_from_r1_freeze"] = rtl["differs_from_r1_freeze"]
    img["constraint_binding"] = constraint_binding(pub_dir, pub)
    return img


def constraint_binding(pub_dir: Path, pub: dict) -> dict:
    """#315 on THIS image: the build's own query counts and routed exception
    report, shipped and bound by digest, re-checked by fpga/xdc_bindings
    against the image's XDC as frozen."""
    import xdc_bindings as xb
    out = {}
    for name in (xb.REPORT, xb.EXCEPTIONS):
        p = _need(pub_dir / name)
        if sha(p) != pub["published_sha256"].get(name):
            raise Refused(f"{name} is not the one publication.json binds")
        out[name] = sha(p)
    xdc = _blob(R2_FROZEN_AT, "fpga/boards/arty-a7-100.xdc").decode()
    report = (pub_dir / xb.REPORT).read_text()
    probs = xb.check_report(report, xdc) + \
        xb.check_route(report, (pub_dir / xb.EXCEPTIONS).read_text(), xdc)
    if probs:
        raise Refused(f"the #315 constraints did not bind on this image: {probs}")
    return {"reports_sha256": out, "checked_by": "fpga/xdc_bindings.check_report + check_route",
            "verdict": "every XDC object query bound exactly its objects; the UART-RX "
                       "synchroniser is excepted by the async input arc only; stage 1 -> 2 "
                       "and downstream are timed"}


# ---- the host -------------------------------------------------------------------
def host_binding() -> dict:
    """Every supported command's bytes under --image r2, each checked against
    the frozen revision-14 target and equal to R1's bound bytes."""
    import tempfile
    import drums_fx as dx
    import qualified_domain as qd
    import r1_candidate as r1c
    import release_manifest as rm
    import uart_host as uh
    r1 = _json(r1r.CANDIDATE)
    if uh.IMAGE_REVISION.get(HOST_IMAGE) != r1c.CONTRACT_REVISION:
        raise Refused(f"uart_host.IMAGE_REVISION[{HOST_IMAGE!r}] is not revision "
                      f"{r1c.CONTRACT_REVISION}")
    if HOST_IMAGE not in qd.PULSE2X_IMAGES:
        raise Refused(f"qualified_domain does not admit PULSE2X on {HOST_IMAGE!r}")
    try:
        kit = uh.image_kit(HOST_IMAGE)
        if kit != r1c.frozen_kit():
            raise Refused(f"uart_host.image_kit({HOST_IMAGE!r}) is not R1's frozen kit")
        cmds = {}
        with tempfile.TemporaryDirectory() as d:
            for name, argv in r1c.COMMANDS:
                sub = Path(d) / name
                sub.mkdir()
                full = [*argv, "--image", HOST_IMAGE]
                cap = rm._capture(full, sub)
                setup, loads_kit = r1c.emitted_setup(full)
                probs = r1c.check_init(setup, kit_expected=loads_kit)
                if probs:
                    raise Refused(f"{name}: emitted setup is not the frozen target: {probs}")
                want = r1["commands"][name]
                for k in ("cmds_sha256", "packets"):
                    if cap[k] != want[k]:
                        raise Refused(f"{name}: --image r2 emits {k} {cap[k]}, R1's bound "
                                      f"bytes are {want[k]}")
                cmds[name] = {"command": ".venv/bin/python fpga/uart_host.py --port "
                                         "/dev/cu.usbserial-XXXX " + " ".join(full),
                              **cap, "setup_writes": len(setup), "loads_kit": loads_kit,
                              "equals_r1_bytes": True}
    except (dx.KitRefused, r1c.Refused, rm.Refused) as exc:
        raise Refused(f"the host's R2 bytes are not the frozen target: {exc}") from None
    presets = rm.presets()
    if {p: v["image_sha256"] for p, v in presets.items()} != \
            {p: v["image_sha256"] for p, v in r1["presets"].items()}:
        raise Refused("the presets' image bytes are not R1's (R2 has no preset change)")
    cmds["live-midi"] = {"command": ".venv/bin/python fpga/midi_session.py --port /dev/ttyUSB1 "
                                    f"--midi-in /dev/snd/midiC1D0 --image {HOST_IMAGE}"}
    return {
        "selector": f"--image {HOST_IMAGE}",
        "contract_revision": uh.IMAGE_REVISION[HOST_IMAGE],
        "default_image": uh.DEFAULT_IMAGE,
        "kit": {"sha256": dx._kit_sha256(kit), "writes": len(kit), "is_r1_frozen_kit": True},
        "known_state_preamble": [list(w) for w in r1c.PREAMBLE],
        "presets": {p: {"image_sha256": v["image_sha256"], "image_writes": v["image_writes"],
                        "playable_midi": v["playable_midi"]} for p, v in presets.items()},
        "commands": cmds,
        "pulse2x_admitted_on": sorted(qd.PULSE2X_IMAGES),
        "same_bytes_as_r1": "every supported command emits R1's bound bytes: the host cannot "
                            "tell R1 from R2 by what it sends; which image is on the board is "
                            "declared, not verified",
    }


def evidence() -> dict:
    """Every committed RTL run identity must name R2's frozen sources and the
    PULSE2X define: a run of another configuration or source set is not R2
    evidence."""
    if not EVIDENCE_DIR.is_dir():
        raise Refused(f"no R2 RTL evidence at {_rel(EVIDENCE_DIR)}")
    rtl = frozen_rtl()
    files, bound = {}, 0
    for p in sorted(q for q in EVIDENCE_DIR.rglob("*") if q.is_file()):
        files[_rel(p)] = sha(p)
        if p.name.endswith("run_identity.json"):
            ident = json.loads(p.read_text()).get("identity", {})
            for kind in ("sources", "roms"):
                for rel, h in (ident.get(kind) or {}).items():
                    want = rtl[kind].get(rel)
                    if want is not None and want != h:
                        raise Refused(f"{_rel(p)}: {rel} is {h[:12]}, R2's is {want[:12]}: "
                                      "that run is not R2 evidence")
            if PULSE2X_DEFINE not in (ident.get("defines") or []):
                raise Refused(f"{_rel(p)}: defines {ident.get('defines')} lack "
                              f"{PULSE2X_DEFINE}: that run is not R2's configuration")
            bound += 1
    if not bound:
        raise Refused(f"{_rel(EVIDENCE_DIR)} holds no RTL run identity")
    return {"dir": _rel(EVIDENCE_DIR), "files": files, "rtl_runs_bound": bound}


def rollback_identity() -> dict:
    verdict, detail = r1r.check()
    if verdict != "BOUND":
        raise Refused(f"the rollback R1 is not BOUND: {verdict} -- {detail}")
    r1 = _json(R1_MANIFEST)
    return {"name": "R1", "release": r1["release"], "manifest": _rel(R1_MANIFEST),
            "manifest_sha256": sha(R1_MANIFEST),
            "bitstream": r1["image"]["bitstream"],
            "bitstream_sha256": r1["image"]["bitstream_sha256"],
            "routed_dcp_sha256": r1["image"]["routed_dcp_sha256"],
            "host_selector": "--image r1",
            "check": "python fpga/release/r1_release.py (T-RELEASE-BOUND-R1)",
            "behind_it": {k: v for k, v in r1["rollback"].items()
                          if k in ("name", "manifest_sha256", "bitstream_sha256")}}


DECLARED = {
    "status": "RELEASE CANDIDATE: a built, timing-closed, DSP-reviewed image bound to its frozen "
              "sources, host bytes and digital evidence. pulse2x is included by OPERATOR "
              "OVERRIDE of its acceptance rule, not by passing it (#282). No physical "
              "programming, control or audio capture has been performed on it.",
    "operator_override": {
        "decision": "#282, 2026-09-27: include pulse2x (rectangles at 0.74) in R2",
        "rule_verdict": "NOT ACCEPTED: 6 of 107 untouched conditions fail the 1.00 dB rule "
                        "(docs/scorecard/m5-artifacts-333/README.md, r2-compare/r2-074.json); "
                        "the bounded repair also failed (PR #370)",
        "gained": "M5A/M5B foldback 10.27/8.85 dB -> 2.21/1.91 dB (3 dB limit)",
        "known_limitations": [
            "square, MIDI 36, cutoff 400, q 0.5, drive 1.6: brightness -4.82 dB vs R1",
            "pulse29, MIDI 36, cutoff 400, q 0.5, drive 1.6: brightness -3.46 dB",
            "square, MIDI 96, cutoff 20 kHz, q 0.5, drive 1.6: brightness -2.46 dB",
            "pulse29, MIDI 96, cutoff 20 kHz, q 0.5, drive 1.6: brightness -2.45 dB",
            "pulse29, MIDI 84, cutoff 400, q 0, drive 1.6: brightness -1.64 dB",
            "pulse29, MIDI 120, cutoff 400, q 0.5, drive 0.75: unwanted energy +1.35 dB "
            "absolute / +2.92 dB relative, at -113 dBFS (near-silent)",
        ],
    },
    "physical_capture": {"status": "NONE", "hardware_playback_tested": False,
                         "precondition": "the board is programmed with THIS bitstream; the host "
                                         "cannot read the image identity back, so it is "
                                         "declared, not verified"},
    "excluded": [
        "#247's domain (glides touching 2^23; ROUTE = 1): refused by the host, issue open",
        "`play --fixture m5a` from the quick-start (sends no image; see R1.md)",
        "spi_miso status readback above the qualified rate (see external_io.spi_miso)",
    ],
    "cannot_bind": [
        "which image is on a connected board: the host cannot read it back, and R2's host "
        "bytes equal R1's",
        "physical audio: no capture exists",
        "the routed checkpoint itself is not shipped: it is bound by digest in report.json, "
        "publication.json, the DSP evidence and the external-I/O extraction",
    ],
}


def build() -> dict:
    image = image_identity()
    return {
        "schema": "gf180-parasynth R2 release manifest v1",
        "release": RELEASE,
        "status": DECLARED["status"],
        "operator_override": DECLARED["operator_override"],
        "contract_revision": 14,
        "configuration": image["configuration"],
        "image": image,
        "external_io": r1r.external_io(image, PUB_DIR, R2_FROZEN_AT),
        "host": host_binding(),
        "evidence": evidence(),
        "rollback": rollback_identity(),
        "physical_capture": DECLARED["physical_capture"],
        "excluded": DECLARED["excluded"],
        "cannot_bind": DECLARED["cannot_bind"],
    }


def check(manifest_path: Path = MANIFEST, fresh: dict | None = None) -> tuple:
    try:
        fresh = json.loads(json.dumps(fresh if fresh is not None else build()))
    except Refused as exc:
        return "REFUSED", str(exc)
    if not Path(manifest_path).exists():
        return "REFUSED", f"no manifest at {_rel(manifest_path)}"
    try:
        committed = json.loads(Path(manifest_path).read_text())
    except (OSError, ValueError) as exc:
        return "REFUSED", f"the manifest at {_rel(manifest_path)} is unreadable: {exc}"
    diffs = r1r._diff(committed, fresh)
    if diffs:
        return "STALE", "differs from a fresh derivation at: " + ", ".join(diffs[:20])
    moved = r1r.tree_drift(fresh["image"])
    note = (f"; NOTE the working tree has moved past this image in {moved} -- its sources "
            f"are verified at {fresh['image']['source_commit'][:12]}") if moved else ""
    img = fresh["image"]
    return "BOUND", (f"{_rel(manifest_path)} equals a fresh derivation; artifacts agree "
                     f"(bitstream {img['bitstream_sha256'][:12]}, routed.dcp "
                     f"{img['routed_dcp_sha256'][:12]}, WNS {img['timing']['wns_ns']:+.3f} / "
                     f"WHS {img['timing']['whs_ns']:+.3f} ns, DSP "
                     f"{img['dsp_feedback_review']['targets']}/"
                     f"{img['dsp_feedback_review']['targets']} dismissed, rollback R1 "
                     f"{fresh['rollback']['bitstream_sha256'][:12]}){note}")


# ---- the STALE controls (T-RELEASE-BOUND-R2) ------------------------------------
def stale_control(case: str, out: Path) -> int:
    """A copy of the committed manifest bound to a REAL wrong state must be
    STALE at exactly the substituted fields, while the unmodified copy is BOUND:

      image  the R2 manifest naming R1's image (bitstream, routed checkpoint,
             publication): the PULSE2X=1 host domain bound to the PULSE2X=0
             image -- the confusion plan098 names ("do not describe a
             pulse-enabled model result as sound delivered by a PULSE2X=0
             bitstream");
      host   the R2 manifest naming the bytes of `run --fixture demo` as the
             host emits them for R0 (`--image release`: no known-state
             preamble, the revision-11 kit).
    """
    def say(word, detail):
        print(f"stale_control: {word} -- {detail}", flush=True)
        return {"STALE": 1, "BOUND": 0}.get(word, 2)

    try:
        raw = _need(MANIFEST).read_bytes()
        committed = json.loads(raw)
        stale = json.loads(raw)
        if case == "image":
            r1 = _json(R1_MANIFEST)["image"]
            sub = {"image.bitstream_sha256": r1["bitstream_sha256"],
                   "image.routed_dcp_sha256": r1["routed_dcp_sha256"],
                   "image.publication_sha256": r1["publication_sha256"]}
            for k, v in sub.items():
                stale["image"][k.split(".", 1)[1]] = v
        elif case == "host":
            import release_manifest as rm
            import tempfile
            with tempfile.TemporaryDirectory() as d:
                cap = rm._capture(["run", "--fixture", "demo", "--image", "release"], Path(d))
            sub = {"host.commands.demo.cmds_sha256": cap["cmds_sha256"],
                   "host.commands.demo.packets": cap["packets"]}
            stale["host"]["commands"]["demo"].update(cmds_sha256=cap["cmds_sha256"],
                                                     packets=cap["packets"])
        else:
            raise Refused(f"unknown case {case!r}")
        changed = sorted(k for k, v in sub.items() if r1r._get(committed, k) != v)
        if not changed:
            raise Refused("the substitution changes nothing")
        out.mkdir(parents=True, exist_ok=True)
        fresh = build()
        faithful = out / "faithful-r2-manifest.json"
        faithful.write_bytes(raw)
        verdict, detail = check(faithful, fresh)
        print(f"  | unmodified copy: {PREFIX}{verdict} -- {detail}")
        if verdict != "BOUND":
            raise Refused(f"the unmodified copy is {verdict}, not BOUND: a STALE on the "
                          "substituted copy would prove nothing")
        stale_path = out / f"stale-r2-manifest-{case}.json"
        stale_path.write_text(json.dumps(stale, indent=2) + "\n")
        verdict, detail = check(stale_path, fresh)
        print(f"  | substituted:     {PREFIX}{verdict} -- {detail}")
    except Refused as exc:
        return say("REFUSED", str(exc))
    if verdict == "BOUND":
        return say("BOUND", f"r2_release ACCEPTED a manifest with {changed} substituted: "
                            "not caught")
    if verdict != "STALE":
        return say("REFUSED", f"r2_release said {verdict}, not STALE: {detail}")
    named = set(detail.split(" at: ", 1)[1].split(", ")) if " at: " in detail else set()
    if named != set(changed):
        return say("REFUSED", f"STALE for a different reason: it names {sorted(named)}, the "
                              f"counterexample changed {changed}")
    src = "R1" if case == "image" else "R0"
    return say("STALE", f"case {case}: the R2 manifest with {changed} taken from {src} is "
                        "STALE at exactly those fields")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="check", choices=("check", "stale-control"))
    ap.add_argument("case", nargs="?", choices=("image", "host"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    if a.action == "stale-control":
        if not a.case or not a.out:
            ap.error("stale-control needs a case and --out")
        return stale_control(a.case, a.out)
    if a.write:
        try:
            m = build()
        except Refused as exc:
            print(f"{PREFIX}REFUSED -- {exc}")
            return 2
        a.manifest.write_text(json.dumps(m, indent=2) + "\n")
        print(f"{PREFIX}wrote {_rel(a.manifest)}")
    verdict, detail = check(a.manifest)
    print(f"{PREFIX}{verdict} -- {detail}")
    return {"BOUND": 0, "STALE": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
