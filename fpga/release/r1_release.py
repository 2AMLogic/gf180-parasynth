#!/usr/bin/env python3
"""fpga/release/r1_release.py -- the R1 release manifest: ONE record binding
the published R1 Arty image to its frozen revision-14 sources, the host's
`--image tree` bytes, the presets, the supported domain and the evidence,
with R0 kept as the named rollback (#280, plan087 Milestone C).

Naming (plan087/plan088). **R0** is `arty-a7-100t baseline 2025.1, r1`
(fpga/release/RELEASE.md, baseline-2025.1.json, contract revision 11,
bitstream a66c9349...). **R1** is the player preview this module releases:
the revision-14 tree, OSC2X=1 FILTER2X=1 PULSE2X=0, published in
fpga/reports/arty/r1-player-preview-2025.1. This module never writes an R0
file; it only reads R0's to pin the rollback.

    python fpga/release/r1_release.py              # BOUND (0) / STALE (1) / REFUSED (2)
    python fpga/release/r1_release.py --write      # re-derive and write r1-2025.1.json
    python fpga/release/r1_release.py stale-control {image,host} --out DIR

BOUND means the committed r1-2025.1.json equals a fresh derivation AND every
selected artifact agrees with every other:

  * the image: arty.bit <-> publication.json <-> report.json <-> the DSP
    evidence's routed.dcp digest (and a fresh re-derivation of the DSP
    disposition from the shipped evidence alone) <-> the shipped reports,
    re-parsed by the publisher's own inspect_reports (fit, internal timing,
    external-I/O classification) <-> the per-port external-I/O extraction
    of the same routed.dcp;
  * the sources: the publication's 25 compiled inputs equal the frozen R1
    candidate's RTL/ROM/XDC set (r1-candidate.json) and each is verified
    byte-for-byte at the freeze commit (git show) -- the working tree may
    move on later without changing THIS image (reported, not bound);
  * the host: the exact bytes every supported R1 command emits NOW
    (`--image tree`), checked against the frozen revision-14 target
    (known-state preamble, kit by digest, ENV_FRATE[8] = 68) and equal to
    the bytes the candidate's RTL evidence replayed; the presets' image
    bytes; the kit digest and contract revision 14;
  * the rollback: R0's bitstream, publication and manifest, by digest.

STALE: derivation succeeded but differs from the committed manifest. REFUSED:
the artifacts disagree or an input is missing; no release can be bound.
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

MANIFEST = HERE / "r1-2025.1.json"
PUB_DIR = ROOT / "fpga/reports/arty/r1-player-preview-2025.1"
EXT_IO = "ext-io/ext-io-extract.json"
VERIFICATION = ROOT / "fpga/reports/arty/rev14-clean/verification.json"
CANDIDATE = HERE / "r1-candidate.json"
R0_MANIFEST = HERE / "baseline-2025.1.json"
R0_PUB_DIR = ROOT / "fpga/reports/arty/integrated-baseline-2025.1"
RELEASE = "arty-a7-100t R1 player preview 2025.1"
PERMITTED_EXCEPTIONS = ["i2s_bclk"]
PREFIX = "r1_release: "


class Refused(RuntimeError):
    """Selected artifacts disagree, or an input is missing."""


def sha(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(p) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def _need(path) -> Path:
    if not Path(path).exists():
        raise Refused(f"missing input: {_rel(path)}")
    return Path(path)


def _json(path) -> dict:
    try:
        return json.loads(_need(path).read_text())
    except ValueError as exc:
        raise Refused(f"unreadable: {_rel(path)}: {exc}") from None


def _git_blob_sha(commit: str, rel: str) -> str | None:
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel}"], capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest() if r.returncode == 0 else None


# ---- the image ------------------------------------------------------------------
def image_identity(pub_dir: Path = PUB_DIR) -> dict:
    import build_arty as ba
    import ext_io_timing as iot
    import publish_arty as pa
    import r1_candidate as rc

    pub = _json(pub_dir / "publication.json")
    rep = _json(pub_dir / "report.json")
    if pub.get("configuration") != ba.CONFIG or rep.get("configuration") != ba.CONFIG:
        raise Refused(f"publication configuration {pub.get('configuration')} is not {ba.CONFIG}")
    if pub.get("part") != ba.PART:
        raise Refused(f"publication part {pub.get('part')} is not {ba.PART}")
    if pub.get("state") != "BUILT_INTERNAL_TIMING_PASS_REVIEW_REQUIRED":
        raise Refused(f"publication state {pub.get('state')}")
    bit = sha(_need(pub_dir / "arty.bit"))
    if bit != pub["bitstream_sha256"] or bit != rep["artifact_sha256"]["arty.bit"]:
        raise Refused(f"arty.bit hashes to {bit}, the publication names {pub['bitstream_sha256']}")
    for name, digest in pub["published_sha256"].items():
        if sha(_need(pub_dir / name)) != digest:
            raise Refused(f"published {name} no longer matches publication.json")
    for name, digest in pub["published_evidence_sha256"].items():
        if sha(_need(pub_dir / "dsp-dpreg-evidence" / name)) != digest:
            raise Refused(f"DSP evidence {name} no longer matches publication.json")
    dcp = pub["original_artifact_sha256"]["routed.dcp"]
    for where, v in (("report.json artifact_sha256", rep["artifact_sha256"].get("routed.dcp")),
                     ("dsp_disposition", pub["dsp_disposition"].get("routed_dcp_sha256"))):
        if v != dcp:
            raise Refused(f"routed.dcp digest disagrees: publication {dcp}, {where} {v}")
    if rep["source_sha256"] != pub["source_sha256"]:
        raise Refused("report.json and publication.json name different source sets")
    if rep.get("exit_code") != 0 or rep.get("state") != "BUILT_REQUIRES_TIMING_REVIEW":
        raise Refused("report.json is not a successful build")

    # fit + timing: the publisher's own parser over the SHIPPED reports must
    # reproduce what the publication claims (publish_arty.inspect_reports
    # refuses a failing endpoint, a wrong clock, an overflow or a DRC error)
    try:
        fresh = pa.inspect_reports(pub_dir)
    except ValueError as exc:
        raise Refused(f"the shipped reports do not pass the publisher's checks: {exc}") from None
    for k in ("timing", "resources", "drc", "core_period_ns", "internal_timing_pass",
              "external_io_timing_qualified", "output_delay_exceptions",
              "missing_output_delays"):
        if fresh[k] != pub[k]:
            raise Refused(f"publication.{k} is not what its shipped reports say")
    if not (pub["internal_timing_pass"] and pub["external_io_timing_qualified"]):
        raise Refused("the image does not pass internal and external timing")
    if pub["output_delay_exceptions"] != PERMITTED_EXCEPTIONS:
        raise Refused(f"output-delay exceptions {pub['output_delay_exceptions']} are not "
                      f"exactly the permitted {PERMITTED_EXCEPTIONS}")

    # DSP: the shipped evidence alone must reproduce the published disposition
    dsp = pa.dsp_disposition(pub_dir, {"artifact_sha256": pub["original_artifact_sha256"]})
    if dsp != pub["dsp_disposition"] or not dsp.get("complete"):
        raise Refused(f"the DSP disposition does not re-derive from the shipped evidence: "
                      f"{dsp.get('reason') or 'differs from publication.json'}")

    # the digital proof the publisher bound
    if sha(_need(VERIFICATION)) != pub["verification"]["record_sha256"]:
        raise Refused("the publication's verification record is not rev14-clean on disk")

    # the sources: exactly the frozen R1 candidate's, verified at the freeze
    cand = _json(CANDIDATE)
    frozen = {**cand["rtl"]["sources"], **cand["rtl"]["roms"], **cand["rtl"]["constraints"]}
    if pub["source_sha256"] != frozen:
        diff = sorted(k for k in set(frozen) | set(pub["source_sha256"])
                      if frozen.get(k) != pub["source_sha256"].get(k))
        raise Refused(f"the image's compiled inputs are not the frozen R1 candidate's: {diff}")
    if cand["rtl"]["frozen_at"] != rc.RTL_FROZEN_AT:
        raise Refused("r1-candidate.json names a different freeze commit than r1_candidate.py")
    for f, h in pub["source_sha256"].items():
        got = _git_blob_sha(rc.RTL_FROZEN_AT, f)
        if got != h:
            raise Refused(f"{f}: the image was built from {h[:12]}, but the freeze commit "
                          f"{rc.RTL_FROZEN_AT[:12]} holds {got[:12] if got else 'nothing'}")
    xdc = subprocess.run(["git", "-C", str(ROOT), "show",
                          f"{rc.RTL_FROZEN_AT}:fpga/boards/arty-a7-100.xdc"],
                         capture_output=True, text=True).stdout
    drift = iot.xdc_contract_drift(xdc, exceptions=pub["output_delay_exceptions"]) \
        + iot.uart_gate_drift(xdc)
    if drift:
        raise Refused(f"the image's XDC is not the approved external-I/O contract: {drift}")

    return {
        "publication": _rel(pub_dir / "publication.json"),
        "publication_sha256": sha(pub_dir / "publication.json"),
        "state": pub["state"],
        "configuration": pub["configuration"],
        "part": pub["part"],
        "tool": [ln for ln in pub["tool"].splitlines() if ln.startswith(("vivado", "SW Build"))],
        "build_seconds": pub["build_seconds"],
        "bitstream": _rel(pub_dir / "arty.bit"),
        "bitstream_sha256": bit,
        "bitstream_bytes": (pub_dir / "arty.bit").stat().st_size,
        "routed_dcp_sha256": dcp,
        "build_script_sha256": rep["script_sha256"],
        "timing": {k: pub["timing"][k] for k in ("wns_ns", "tns_ns", "setup_failing",
                                                 "setup_endpoints", "whs_ns", "ths_ns",
                                                 "hold_failing", "hold_endpoints")},
        "core_period_ns": pub["core_period_ns"],
        "resources": pub["resources"],
        "drc": pub["drc"],
        "internal_timing_pass": pub["internal_timing_pass"],
        "external_io_timing_qualified": pub["external_io_timing_qualified"],
        "output_delay_exceptions": pub["output_delay_exceptions"],
        "dsp_feedback_review": {"complete": dsp["complete"], "verdict": dsp["verdict"],
                                "targets": dsp["targets"],
                                "routed_dcp_sha256": dsp["routed_dcp_sha256"]},
        "digital_verification": {"record": _rel(VERIFICATION),
                                 "record_sha256": pub["verification"]["record_sha256"],
                                 "periods": pub["verification"]["periods"]},
        "remaining_review": pub["remaining_review"],
        "source_sha256": pub["source_sha256"],
        "source_commit": rc.RTL_FROZEN_AT,
        "source_commit_verified": True,
    }


def external_io(image: dict, pub_dir: Path = PUB_DIR) -> dict:
    """The per-port extraction of the SAME routed.dcp (fpga/ext_io_extract.py)."""
    path = pub_dir / EXT_IO
    rec = _json(path)
    if rec.get("dcp_sha256") != image["routed_dcp_sha256"]:
        raise Refused(f"{EXT_IO} measured routed.dcp {str(rec.get('dcp_sha256'))[:12]}, the "
                      f"image's is {image['routed_dcp_sha256'][:12]}")
    if rec.get("instrument_sha256") != sha(ROOT / "fpga/ext_io_extract.py"):
        raise Refused(f"{EXT_IO} was produced by a different fpga/ext_io_extract.py")
    for name in ("ext_io_paths.txt",):
        if sha(_need(path.parent / name)) != rec.get("paths_sha256"):
            raise Refused(f"{EXT_IO}: {name} does not hash to the record's paths_sha256")
    if rec.get("state") != "PASS" or not rec.get("control", {}).get("caught"):
        raise Refused(f"{EXT_IO}: state {rec.get('state')}, control caught "
                      f"{rec.get('control', {}).get('caught')}")
    if not rec["spi_miso"]["readback_qualified"]:
        raise Refused(f"{EXT_IO}: spi_miso readback is not qualified at "
                      f"{rec['spi_miso']['readback_qualified_mhz']} MHz on this image")
    # the design WNS is the minimum over every endpoint, ports included, so a
    # per-port slack below it cannot come from the same routed design
    if rec["worst_setup_slack_ns"] < image["timing"]["wns_ns"]:
        raise Refused("the per-port worst setup slack is below the design WNS: the two "
                      "measurements are not of the same routed design")
    return {"record": _rel(path), "sha256": sha(path), "state": rec["state"],
            "worst_setup_slack_ns": rec["worst_setup_slack_ns"],
            "worst_hold_slack_ns": rec["worst_hold_slack_ns"],
            "ports": {p: {"setup_slack_ns": v["setup_slack_ns"],
                          "hold_slack_ns": v["hold_slack_ns"]}
                      for p, v in rec["ports"].items()},
            "spi_miso": {k: rec["spi_miso"][k] for k in
                         ("sta_co_bound_ns", "co_budget_ns", "max_guaranteed_readback_mhz",
                          "readback_qualified_mhz", "write_ceiling_mhz")},
            "control": {"id": rec["control"]["id"], "caught": True,
                        "spi_miso_setup_slack_ns": rec["control"]["spi_miso_setup_slack_ns"]},
            "uart_rx_xdc_constraints_bound": {
                k: rec["synchronisers"]["patterns"][k]["matched"]
                for k in ("xdc:uart_async_reg", "xdc:uart_false_path_d")},
            "uart_rx_async_reg_from_rtl": sorted(
                c for c, v in rec["synchronisers"]["async_reg"].items()
                if "u_uart" in c and v == "1")}


# ---- the host, presets, kit, domain ------------------------------------------
def host_binding() -> dict:
    """Fresh R1 bytes of every supported command (each checked against the
    frozen revision-14 target by r1_candidate.commands) must equal the bytes
    the candidate record -- and so its RTL evidence -- names."""
    import r1_candidate as rc
    import release_manifest as rm
    import uart_host as uh
    cand = _json(CANDIDATE)
    try:
        cmds = rc.commands()
        kit = rc.kit_identity()
    except rc.Refused as exc:
        raise Refused(f"the host's R1 bytes are not the frozen R1 target: {exc}") from None
    for name, c in cmds.items():
        want = cand["commands"].get(name, {})
        for k in ("cmds_sha256", "packets", "init_writes"):
            if k in c and c[k] != want.get(k):
                raise Refused(f"{name}: the CLI emits {k} {c[k]}, the R1 candidate's evidence "
                              f"replayed {want.get(k)}")
    if kit != cand["kit"]:
        raise Refused("the host's R1 kit is not the candidate's")
    presets = rm.presets()
    if {p: v["image_sha256"] for p, v in presets.items()} != \
            {p: v["image_sha256"] for p, v in cand["presets"].items()}:
        raise Refused("the presets' image bytes are not the candidate's")
    return {
        "selector": f"--image {rc.HOST_IMAGE}",
        "contract_revision": uh.IMAGE_REVISION[rc.HOST_IMAGE],
        "default_image": uh.DEFAULT_IMAGE,
        "default_image_is_r1": uh.DEFAULT_IMAGE == rc.HOST_IMAGE,
        "kit": kit,
        "known_state_preamble": [list(w) for w in rc.PREAMBLE],
        "presets": {p: {"image_sha256": v["image_sha256"], "image_writes": v["image_writes"],
                        "playable_midi": v["playable_midi"]} for p, v in presets.items()},
        "commands": cmds,
        "domain": rc.domain(),
        "candidate": {"record": _rel(CANDIDATE), "sha256": sha(CANDIDATE),
                      "evidence_dir": cand["evidence"]["dir"],
                      "rtl_runs_bound": cand["evidence"]["rtl_runs_bound"]},
    }


def rollback_identity() -> dict:
    r0 = _json(R0_MANIFEST)
    pub = _json(R0_PUB_DIR / "publication.json")
    bit = sha(_need(R0_PUB_DIR / "arty.bit"))
    if not (bit == pub["bitstream_sha256"] == r0["image"]["bitstream_sha256"]):
        raise Refused("R0's arty.bit, publication and manifest disagree")
    if sha(R0_PUB_DIR / "publication.json") != r0["image"]["publication_sha256"]:
        raise Refused("R0's publication.json is not the one its manifest binds")
    return {"name": "R0", "release": r0["release"], "contract_revision": 11,
            "manifest": _rel(R0_MANIFEST), "manifest_sha256": sha(R0_MANIFEST),
            "publication": _rel(R0_PUB_DIR / "publication.json"),
            "publication_sha256": sha(R0_PUB_DIR / "publication.json"),
            "bitstream": _rel(R0_PUB_DIR / "arty.bit"), "bitstream_sha256": bit,
            "routed_dcp_sha256": r0["image"]["routed_dcp_sha256"],
            "host_selector": "--image release",
            "check": "python fpga/release/release_manifest.py (T-RELEASE-BOUND)"}


DECLARED = {
    "status": "RELEASE CANDIDATE: a built, timing-closed, DSP-reviewed image bound to its frozen "
              "revision-14 sources, host bytes and digital evidence. No physical programming, "
              "control or audio capture has been performed on it.",
    "default_switch": {
        "today": "fpga/uart_host.py DEFAULT_IMAGE = \"release\": a command without --image "
                 "targets R0; R1 is selected with --image tree",
        "one_line": "fpga/uart_host.py: DEFAULT_IMAGE = \"tree\"",
        "consequences": "fpga/midi_session.py follows (resolve_image falls back to "
                        "uart_host.DEFAULT_IMAGE). R0's manifest pins the bytes of its commands "
                        "WITHOUT --image, so T-RELEASE-BOUND then reports R0 STALE at commands.* "
                        "-- the gate doing its job: the same change must give R0's documented "
                        "commands an explicit --image release. This manifest records "
                        "host.default_image and goes STALE too, and is re-bound with --write.",
        "decided_by": "the operator (#280 keeps release as the default)",
    },
    "physical_capture": {"status": "NONE", "hardware_playback_tested": False,
                         "precondition": "the board is programmed with THIS bitstream "
                                         "(544499e2...); the host cannot read the image identity "
                                         "back, so it is declared, not verified"},
    "excluded": [
        "PULSE2X=1 (#205, #248): not in this image",
        "#247's domain (glides touching 2^23; ROUTE = 1): refused by the host, issue open",
        "five of sixteen 808 sounds from live MIDI (#298): congas, rimshot, maracas unmapped",
        "`play --fixture m5a` from the R1 quick-start (sends no image; see R1.md)",
        "spi_miso status readback above 1.4 MHz (qualified rate; writes to 2.0 MHz)",
    ],
    "cannot_bind": [
        "which image is on a connected board: the host cannot read it back",
        "physical audio: no capture exists",
        "a numeric host/protocol version: none exists; bytes are pinned instead",
        "the routed checkpoint itself is not shipped (9.9 MB): it is bound by digest in "
        "report.json, publication.json, the DSP evidence and the external-I/O extraction",
    ],
}


def build() -> dict:
    import r1_candidate as rc
    image = image_identity()
    return {
        "schema": "gf180-parasynth R1 release manifest v1",
        "release": RELEASE,
        "alias": "plan087 R1 (player preview)",
        "status": DECLARED["status"],
        "contract_revision": rc.CONTRACT_REVISION,
        "configuration": image["configuration"],
        "image": image,
        "external_io": external_io(image),
        "host": host_binding(),
        "rollback": rollback_identity(),
        "default_switch": DECLARED["default_switch"],
        "physical_capture": DECLARED["physical_capture"],
        "excluded": DECLARED["excluded"],
        "cannot_bind": DECLARED["cannot_bind"],
    }


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [path]


def tree_drift(image: dict) -> list:
    return sorted(f for f, h in image["source_sha256"].items()
                  if not (ROOT / f).exists() or sha(ROOT / f) != h)


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
    diffs = _diff(committed, fresh)
    if diffs:
        return "STALE", "differs from a fresh derivation at: " + ", ".join(diffs[:20])
    moved = tree_drift(fresh["image"])
    note = (f"; NOTE the working tree has moved past this image in {moved} -- its sources "
            f"are verified at {fresh['image']['source_commit'][:12]}") if moved else ""
    img = fresh["image"]
    return "BOUND", (f"{_rel(manifest_path)} equals a fresh derivation; artifacts agree "
                     f"(bitstream {img['bitstream_sha256'][:12]}, routed.dcp "
                     f"{img['routed_dcp_sha256'][:12]}, WNS {img['timing']['wns_ns']:+.3f} / "
                     f"WHS {img['timing']['whs_ns']:+.3f} ns, DSP "
                     f"{img['dsp_feedback_review']['targets']}/"
                     f"{img['dsp_feedback_review']['targets']} dismissed, rollback R0 "
                     f"{fresh['rollback']['bitstream_sha256'][:12]}){note}")


# ---- the STALE controls (T-RELEASE-BOUND-R1) ----------------------------------------
def stale_control(case: str, out: Path) -> int:
    """A copy of the committed manifest bound to a REAL wrong state must be
    STALE at exactly the substituted fields, while the unmodified copy is
    BOUND. Both are the confusion plan087 names -- "an L2 host writing
    ENV_FRATE to an older image is not an L2 demonstration":

      image  the R1 manifest naming R0's image (bitstream a66c9349..., routed
             checkpoint 6c3c22c5..., R0's publication): the revision-14 host
             bound to the revision-11 image;
      host   the R1 manifest naming the bytes of `run --fixture demo` as the
             host emits them for R0 (`--image release`: no known-state
             preamble, the revision-11 kit): R0's host bound to the R1 image.
    """
    def say(word, detail):
        print(f"stale_control: {word} -- {detail}", flush=True)
        return {"STALE": 1, "BOUND": 0}.get(word, 2)

    try:
        raw = _need(MANIFEST).read_bytes()
        committed = json.loads(raw)
        stale = json.loads(raw)
        if case == "image":
            r0 = _json(R0_PUB_DIR / "publication.json")
            sub = {"image.bitstream_sha256": r0["bitstream_sha256"],
                   "image.routed_dcp_sha256": r0["original_artifact_sha256"]["routed.dcp"],
                   "image.publication_sha256": sha(R0_PUB_DIR / "publication.json")}
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
        changed = sorted(k for k, v in sub.items()
                         if _get(committed, k) != v)
        if not changed:
            raise Refused("the substitution changes nothing")
        out.mkdir(parents=True, exist_ok=True)
        fresh = build()
        faithful = out / "faithful-r1-manifest.json"
        faithful.write_bytes(raw)
        verdict, detail = check(faithful, fresh)
        print(f"  | unmodified copy: {PREFIX}{verdict} -- {detail}")
        if verdict != "BOUND":
            raise Refused(f"the unmodified copy is {verdict}, not BOUND: a STALE on the "
                          "substituted copy would prove nothing")
        stale_path = out / f"stale-r1-manifest-{case}.json"
        stale_path.write_text(json.dumps(stale, indent=2) + "\n")
        verdict, detail = check(stale_path, fresh)
        print(f"  | substituted:     {PREFIX}{verdict} -- {detail}")
    except Refused as exc:
        return say("REFUSED", str(exc))
    if verdict == "BOUND":
        return say("BOUND", f"r1_release ACCEPTED a manifest with {changed} substituted: "
                            "not caught")
    if verdict != "STALE":
        return say("REFUSED", f"r1_release said {verdict}, not STALE: {detail}")
    named = set(detail.split(" at: ", 1)[1].split(", ")) if " at: " in detail else set()
    if named != set(changed):
        return say("REFUSED", f"STALE for a different reason: it names {sorted(named)}, the "
                              f"counterexample changed {changed}")
    return say("STALE", f"case {case}: the R1 manifest with {changed} taken from R0 is STALE "
                        f"at exactly those fields")


def _get(d, dotted):
    for part in dotted.split("."):
        d = d.get(part) if isinstance(d, dict) else None
    return d


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
