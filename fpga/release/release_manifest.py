#!/usr/bin/env python3
"""fpga/release/release_manifest.py -- ONE source-bound release manifest for
the published Arty baseline image, its presets/control frontend and its
evidence (plan080 T2, plan075 Milestone 1, plan076 section 5).

This is a CONFIGURATION manifest over the existing publication machinery
(fpga/publish_arty.py's publication.json), not a deployment system. Every
identity in it is DERIVED from the tree by `build()`; the only hand-written
parts are the declarations (qualification scope, exclusions, rollback,
physical-capture status) in DECLARED below, and those name the evidence they
rest on.

    .venv/bin/python fpga/release/release_manifest.py            # check: BOUND / STALE / REFUSED
    .venv/bin/python fpga/release/release_manifest.py --write    # re-derive and write the JSON

VERDICTS (exit status):
  0 BOUND    the committed manifest equals a fresh derivation, and every
             selected artifact agrees with every other (bitstream <-> the
             publication <-> report.json <-> the DSP evidence <-> the tree's
             sources <-> the evidence runs' identities <-> the CLI's bytes)
  1 STALE    derivation succeeded but differs from the committed manifest
             (an RTL/ROM/host/preset change that alters what ships): re-bind
             deliberately with --write, or cut a new release
  2 REFUSED  the selected artifacts DISAGREE with each other, or an input is
             missing: no manifest can be bound
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in ("fpga/release", "fpga", "model", "audition", "tools", "rtl-sketch"):
    if str(ROOT / _p) not in sys.path:
        sys.path.insert(0, str(ROOT / _p))

MANIFEST = HERE / "baseline-2025.1.json"
PUB_DIR = ROOT / "fpga/reports/arty/integrated-baseline-2025.1"
ROLLBACK_DIR = ROOT / "fpga/reports/arty/vivado-2025.1"
ROLLING_DIR = ROOT / "fpga/reports/arty/rolling-playback"
EVIDENCE_DIR = HERE / "evidence"
CONFIG = {"OSC2X": 1, "FILTER2X": 1, "PULSE2X": 0}
PRESETS = ("default", "m5a-saw", "m5a-pulse")

# The supported player-facing commands: (name, argv after `uart_host.py`,
# evidence). Each one's exact bytes are captured by the shipped CLI (dry run)
# and pinned by digest.
COMMANDS = (
    ("held-default", ["run", "--note", "45", "--fixture", "none"],
     "evidence/held-note/default"),
    ("held-m5a-saw", ["run", "--preset", "m5a-saw", "--note", "72", "--fixture", "none"],
     "evidence/held-note/m5a-saw"),
    ("held-m5a-pulse", ["run", "--preset", "m5a-pulse", "--note", "72", "--fixture", "none"],
     "evidence/held-note/m5a-pulse"),
    ("phrase-m5a", ["play", "--fixture", "m5a"],
     "fpga/reports/arty/uart-clean (scenario phrase: bench-built from the same "
     "uart_host.phrase_events('m5a'); not a capture of these bytes)"),
    ("demo", ["run", "--fixture", "demo"], "fpga/reports/arty/rolling-playback/demo"),
    ("bar808-full", ["run", "--fixture", "bar808-full"],
     "fpga/reports/arty/rolling-playback/bar808-full"),
)
ROLLING = ("demo", "bar808-full")


class Refused(RuntimeError):
    """Selected artifacts disagree, or an input is missing."""


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sha_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=list).encode()).hexdigest()


def _rel(p: Path) -> str:
    return str(Path(p).resolve().relative_to(ROOT))


def _need(path: Path) -> Path:
    if not Path(path).exists():
        raise Refused(f"missing input: {_rel(path) if str(path).startswith(str(ROOT)) else path}")
    return Path(path)


# ---- the image --------------------------------------------------------------
def image_identity() -> dict:
    pub = json.loads(_need(PUB_DIR / "publication.json").read_text())
    rep = json.loads(_need(PUB_DIR / "report.json").read_text())
    if pub["configuration"] != CONFIG:
        raise Refused(f"publication configuration {pub['configuration']} is not {CONFIG}")
    bit = sha(_need(PUB_DIR / "arty.bit"))
    if bit != pub["bitstream_sha256"]:
        raise Refused(f"arty.bit hashes to {bit}, the publication names {pub['bitstream_sha256']}")
    for name, digest in pub["published_sha256"].items():
        if sha(_need(PUB_DIR / name)) != digest:
            raise Refused(f"published {name} no longer matches publication.json")
    dcp = pub["original_artifact_sha256"]["routed.dcp"]
    for where, v in (("report.json artifact_sha256", rep["artifact_sha256"].get("routed.dcp")),
                     ("dsp_disposition", pub["dsp_disposition"].get("routed_dcp_sha256"))):
        if v != dcp:
            raise Refused(f"routed.dcp digest disagrees: publication {dcp}, {where} {v}")
    ev = PUB_DIR / "dsp-dpreg-evidence"
    for name, digest in pub["published_evidence_sha256"].items():
        if sha(_need(ev / name)) != digest:
            raise Refused(f"DSP evidence {name} no longer matches publication.json")
    if rep["source_sha256"] != pub["source_sha256"]:
        raise Refused("report.json and publication.json name different source sets")
    ver = ROOT / "fpga/reports/arty/uart-clean/verification.json"
    if sha(_need(ver)) != pub["verification"]["record_sha256"]:
        raise Refused("the publication's verification record is not the uart-clean record on disk")
    drift = {f: {"published": h, "tree": sha(ROOT / f) if (ROOT / f).exists() else None}
             for f, h in pub["source_sha256"].items()
             if not (ROOT / f).exists() or sha(ROOT / f) != h}
    return {
        "publication": _rel(PUB_DIR / "publication.json"),
        "publication_sha256": sha(PUB_DIR / "publication.json"),
        "state": pub["state"],
        "configuration": pub["configuration"],
        "part": pub["part"],
        "tool": pub["tool"].splitlines()[0],
        "bitstream": _rel(PUB_DIR / "arty.bit"),
        "bitstream_sha256": bit,
        "bitstream_bytes": (PUB_DIR / "arty.bit").stat().st_size,
        "routed_dcp_sha256": dcp,
        "timing": {k: pub["timing"][k] for k in ("wns_ns", "tns_ns", "whs_ns", "ths_ns",
                                                 "setup_failing", "hold_failing")},
        "core_period_ns": pub["core_period_ns"],
        "internal_timing_pass": pub["internal_timing_pass"],
        "external_io_timing_qualified": pub["external_io_timing_qualified"],
        "output_delay_exceptions": pub["output_delay_exceptions"],
        "dsp_feedback_review": {"complete": pub["dsp_disposition"]["complete"],
                                "verdict": pub["dsp_disposition"]["verdict"]},
        "digital_verification": {"record": _rel(ver), "record_sha256": pub["verification"]["record_sha256"],
                                 "periods": pub["verification"]["periods"],
                                 "scope": pub["verification"]["scope"]},
        "remaining_review": pub["remaining_review"],
        # the RTL and ROMs the bitstream was built from; the tree must still
        # hold exactly these bytes for the release to be bound to it
        "source_sha256": pub["source_sha256"],
        "tree_source_drift": drift,
    }


def rollback_identity() -> dict:
    pub = json.loads(_need(ROLLBACK_DIR / "publication.json").read_text())
    bit = sha(_need(ROLLBACK_DIR / "arty.bit"))
    if bit != pub["bitstream_sha256"]:
        raise Refused("rollback arty.bit does not match its publication")
    return {"publication": _rel(ROLLBACK_DIR / "publication.json"),
            "bitstream": _rel(ROLLBACK_DIR / "arty.bit"), "bitstream_sha256": bit,
            "configuration": pub["configuration"], "state": pub["state"],
            "dsp_feedback_review_complete": pub.get("dsp_feedback_review_complete", False),
            "control": "SPI only (pre-UART wrapper): fpga/uart_host.py CANNOT drive it; "
                       "fpga/spi_host.py / fpga/play.py over the SPI header",
            "status": "known-built fallback, NOT a qualified release: its DPREG-4 DSP review "
                      "is incomplete on that image and it has no UART control path"}


# ---- presets, domain, commands -----------------------------------------------
def presets() -> dict:
    import qualified_domain as qd
    import uart_host as uh
    out = {}
    for p in PRESETS:
        regs = uh.preset_regs(None if p == "default" else p)
        summary = qd.check_patch(regs, name=p)          # raises: an unqualified preset REFUSES
        image = uh.voice_image_writes(None if p == "default" else p) + \
            uh.voice_mixer_writes(None if p == "default" else p)
        out[p] = {**summary, "registers_sha256": sha_json(regs),
                  "image_writes": len(image), "image_sha256": sha_json(image)}
    return out


def domain() -> dict:
    import qualified_domain as qd
    import voice_fx as vf
    return {
        "enforced_by": "fpga/release/qualified_domain.py, called by fpga/uart_host.py main() "
                       "on the final register writes of every player-facing command",
        "inc_lo": qd.INC_LO, "inc_hi": qd.INC_HI,
        "hz_lo": round(qd.inc_hz(qd.INC_LO), 4), "hz_hi": round(qd.inc_hz(qd.INC_HI), 4),
        "nyquist_inc": qd.NYQUIST_INC,
        "margin_octaves_below_nyquist": round(__import__("math").log2(qd.NYQUIST_INC / qd.INC_HI), 4),
        "derivation": "phase_inc(note_hz(0)) .. phase_inc(note_hz(127)) at SR 48000, 24-bit phase: "
                      "no oscillator is PROGRAMMED below MIDI 0's pitch or above MIDI 127's, "
                      "whatever the note and per-oscillator transposition/detune. A glide is "
                      "admitted only from a known in-range increment to an in-range increment; the "
                      "slew is monotone between them (voice_fx.OscFx.slew, voice_dp.v S_SL2), so "
                      "every increment it passes through is in range.",
        "rules": list(qd.RULES),
        "supported_audible_waveforms": sorted(str(w) for w in qd.SUPPORTED_WAVES),
        "glide_register": "any value 0..2^24-1 (the bench covers 1, 2692 (preset), 12118 "
                          "(0.02 s/oct) and 2^24-1 (an octave per frame) at the range ends)",
        "modulation": "oscillator pitch modulation (MROUTE bit 0, wheel and depth nonzero) is "
                      "checked SEPARATELY (rule MOD_EXCURSION): its worst-case excursion "
                      f"(|octaves| <= min(4, min(1, mwheel/2^15) * mpd/2^{vf.OCT_Q})) must keep "
                      "the effective increment in range. No supported preset or fixture enables "
                      "it (mwheel 0 everywhere). It is NOT the #247 glide slew and is not "
                      "claimed to share its defect.",
        "never_clamps": "a write outside the domain is REFUSED (exit 2) naming the write, "
                        "pitch and rule; nothing is re-pitched to fit",
        "engineering_interface": "fpga/uart_host.py --engineering, fpga/spi_host.py, the benches: "
                                 "available, and OUTSIDE the qualified player-facing domain",
        "precondition_not_verified": "the modulation registers are assumed at their reset value "
                                     "(0) on fixture runs, which never write them; the host "
                                     "cannot read them back. A board driven through the "
                                     "engineering interface must be reset (BTN0) first.",
        "playable_midi": {p: s["playable_midi"] for p, s in presets().items()},
    }


def _capture(argv, outdir: Path) -> dict:
    import uart_host as uh
    prefix = str(outdir / "cap")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = uh.main(["--dry-run", *argv, "--capture", prefix])
    if rc != 0:
        raise Refused(f"the CLI refused a supported command {argv}: {err.getvalue().strip()}")
    return {"cmds_sha256": sha(prefix + ".cmds"),
            "packets": sum(1 for _ in open(prefix + ".cmds")),
            "validator": next((l for l in out.getvalue().splitlines()
                               if "release domain" in l), None)}


def rolling_binding(fx: str) -> dict:
    """The recorded rolling RTL replay applies to what the CLI emits NOW only
    if the CLI's transmit log still reproduces the recorded capture byte for
    byte and the replay ran on the published RTL."""
    import verify_rolling_playback as vrp
    rec_dir = ROLLING_DIR / fx
    rec = json.loads(_need(rec_dir / "verification.json").read_text())
    recorded = _need(rec_dir / "rtl-replay" / f"{fx}.cmds")
    ident = json.loads(_need(rec_dir / "rtl-replay" / f"{fx}.run_identity.json").read_text())
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        run = vrp.run_cli(fx)
        ok = vrp.check(run)["ok"]
        with tempfile.TemporaryDirectory() as d:
            vrp.write_rtl_capture(run, Path(d) / fx)
            same = (Path(d) / f"{fx}.cmds").read_bytes() == recorded.read_bytes()
    return {"record": _rel(rec_dir / "verification.json"), "state": rec["state"],
            "rtl_state": rec.get("rtl", {}).get("state") if isinstance(rec.get("rtl"), dict) else None,
            "cli_sim_clean": ok, "cli_bytes_equal_recorded_replay": same,
            "replay_rtl_sources": ident["identity"]["sources"]}


def commands() -> dict:
    out = {}
    with tempfile.TemporaryDirectory() as d:
        for name, argv, evidence in COMMANDS:
            sub = Path(d) / name
            sub.mkdir()
            out[name] = {"command": ".venv/bin/python fpga/uart_host.py --port /dev/cu.usbserial-XXXX "
                                    + " ".join(argv),
                         "evidence": evidence, **_capture(argv, sub)}
    return out


# ---- evidence ------------------------------------------------------------------
def _sources_agree(sources: dict, pub_sources: dict, what: str) -> None:
    for f, h in sources.items():
        if f in pub_sources and pub_sources[f] != h:
            raise Refused(f"{what} ran on {f} {h[:12]}, the image was built from {pub_sources[f][:12]}")


def evidence(image: dict) -> dict:
    pub_sources = image["source_sha256"]
    out = {"rolling_playback": {}, "held_note": {}, "glide_boundary": None}

    for fx in ROLLING:
        b = rolling_binding(fx)
        if not (b["cli_sim_clean"] and b["cli_bytes_equal_recorded_replay"] and b["state"] == "PASS"):
            raise Refused(f"rolling-playback {fx}: the recorded RTL replay no longer applies "
                          f"to the CLI's bytes ({b})")
        _sources_agree(b.pop("replay_rtl_sources"), pub_sources, f"rolling-playback {fx}")
        b["replay_rtl_sources_match_image"] = True
        out["rolling_playback"][fx] = b
    for p in PRESETS:
        d = EVIDENCE_DIR / "held-note" / p
        rec = json.loads(_need(d / "held_note_audible.json").read_text())
        ident = json.loads(_need(d / "run_identity.json").read_text())
        _sources_agree(ident["identity"]["sources"], pub_sources, f"held-note {p}")
        name = f"held-{p}"
        cap_sha = sha(_need(d / "capture.cmds"))
        out["held_note"][p] = {"record": _rel(d / "held_note_audible.json"),
                               "verdict": rec["verdict"], "i2s_peak_lsb": rec["i2s_peak_lsb"],
                               "replay_capture_sha256": cap_sha, "command": name}
    dl = ROOT / "docs/deadline"
    out["deadline"] = {"readme": _rel(dl / "README.md"), "readme_sha256": sha(_need(dl / "README.md")),
                       "runs": _rel(dl / "runs/run_all.json"),
                       "runs_sha256": sha(_need(dl / "runs/run_all.json"))}
    pr = EVIDENCE_DIR / "probe-247" / "run_all.json"
    probes = json.loads(_need(pr).read_text())
    out["probe_247"] = {"record": _rel(pr), "sha256": sha(pr),
                        "rc": {r["cmd"].split("--variant ")[1].split()[0]: r["rc"] for r in probes}}
    gb = EVIDENCE_DIR / "glide-boundary" / "summary.json"
    s = json.loads(_need(gb).read_text())
    for case, r in s["wrapper_identities"].items():
        _sources_agree(r, pub_sources, f"glide-boundary wrapper {case}")
    if not s["all_expectations_met"]:
        raise Refused("glide-boundary evidence: not every expectation was met")
    out["glide_boundary"] = {"record": _rel(gb), "sha256": sha(gb),
                             "verdicts": {k: {"rc": v["rc"], "expected": v["expected"], "met": v["met"]}
                                          for k, v in s["verdicts"].items()}}
    return out


# ---- the declarations ------------------------------------------------------------
DECLARED = {
    "release": "arty-a7-100t baseline 2025.1, r1",
    "status": "CANDIDATE: digital evidence only; no physical programming, control or audio "
              "capture has been performed on this image",
    "host_protocol": {
        "cli": "fpga/uart_host.py",
        "link": "USB-UART 115200 8N1 (FTDI; FPGA RX A9, TX D10)",
        "packets": "W write-now, E scheduled (16-bit due frame), Q status, X abort; "
                   "checksum = two's complement of the byte sum (rtl-sketch/uart_bridge.v)",
        "register_frames": "DR 0007 revision 2: {F, 6'b0, SEC, A[7:0], D[31:0]}",
        "device_contract": "2 UART write slots/frame, event queue 64, write queue 8",
        "version": "no numeric protocol version exists; the protocol is pinned by the "
                   "bytes of every supported command (commands.*.cmds_sha256) and the "
                   "bridge RTL's source hash",
    },
    "runtime_qualification": {
        "deadline": {
            "pr": "2AMLogic/gf180-parasynth#248 (merged d089c67, head b45bc5d)",
            "record": "docs/deadline/README.md (runs: docs/deadline/runs/, reanalysis: "
                      "docs/deadline/reanalysis/)",
            "status": "LANDED",
            "establishes": "the published baseline (OSC2X=1 FILTER2X=1 PULSE2X=0) met every "
                           "production frame deadline in the tested runs: 0 missed, 0 overrun; "
                           "worst observed slack 14 cycles in musical stress (SPI full kit and "
                           "Arty UART), 6 in extreme-register runs; every musical-range I2S "
                           "comparison exact and complete; cost model reconciled (C = 87 across "
                           "30,333 frames, 0 overlap exceptions); incomplete I2S/schedule "
                           "evidence returns NO VERDICT",
            "does_not_establish": "a formal whole-register-space bound (the per-term cost-model "
                                  "bound, 248 cycles, is 'not a formal proof'); model agreement "
                                  "in the extreme runs (#247); PULSE2X=1 (observed misses above "
                                  "Nyquist, zero-slack musical-range bound: excluded)",
        },
        "rolling_playback_rtl": {
            "pr": "2AMLogic/gf180-parasynth#210 (merged aa130175f9ada9afc4ac8b795eb3aaeed1e45fde)",
            "establishes": "the CLI's actual bytes for demo and bar808-full, replayed through the "
                           "UART wrapper RTL: every write on its frame, zero I2S mismatches "
                           "(fpga/ARTY.md 'MUSICAL-LENGTH PLAYBACK')",
        },
        "build_timing_publication_dsp": "image.* above: routed, internal timing pass, external "
                                        "I/O qualified with the i2s_bclk exception, DPREG-4 "
                                        "review complete on this routed.dcp",
    },
    "exclusions": [
        {"what": "PULSE2X=1", "why": "not in this image; #248 records missed deadlines at extreme "
                                     "increments; needs its own correction and rebuilt image (#205)",
         "enforced": "the image is PULSE2X=0; qualified_domain.check_patch refuses pulse2x"},
        {"what": "#247 as filed: a glide with an endpoint at or above 2^23",
         "why": "exact model/RTL mismatch, open defect",
         "enforced": "rules INC_RANGE (every programmed increment <= INC_HI < 2^23) and GLIDE_247"},
        {"what": "#247 as measured: the voice routed through the drum filter (ROUTE = 1)",
         "why": "on #247's own bench its 1809-period mismatch disappears with route 0 or without "
                "strikes, and is unchanged by jumps instead of glides or by in-range increments "
                "(fpga/release/probe_247.py, evidence/probe-247/); the exact trigger inside the "
                "drum-filter path is not isolated",
         "enforced": "rule ROUTE_DRUMFILTER; no player-facing path writes ROUTE (reset 0)"},
        {"what": "resonance use of calibration surge-type2-clean-v1",
         "why": "qualified at resonance 0 only (plan076 section 3)",
         "enforced": "rule CALIBRATION_RESONANCE in check_patch; no release preset or fixture "
                     "selects a calibration"},
        {"what": "--preset combined with a fixture", "why": "the fixture loads its own patch; the "
                 "player would hear the fixture's sound under the preset's name",
         "enforced": "uart_host main() refuses (exit 2)"},
        {"what": "standalone note-on/note-off without the image",
         "why": "the device's image is unknown to that command (waveforms, weights)",
         "enforced": "rule WAVES refuses note-on; use `run`"},
    ],
    "physical_capture": {"status": "NONE", "hardware_playback_tested": False,
                         "next": "plan076 section 6: silence, held note and release, isolated "
                                 "drum hits, the musical fixture, a repeat capture; MOTU M4, "
                                 "fixed gain, raw captures preserved"},
    "cannot_bind": [
        "the m5a phrase: its RTL evidence (uart-clean 'phrase') is bench-built from the same "
        "phrase_events function, not a replay of the CLI's captured bytes",
        "the modulation and ROUTE registers on fixture runs: assumed at reset (not readable)",
        "the voice-level glide runs: verify_voice records no run identity (the wrapper runs do)",
        "a numeric host/protocol version: none exists; bytes are pinned instead",
        "physical audio: no capture exists",
        "the rollback image cannot be driven by the release CLI (SPI only)",
    ],
}


def build() -> dict:
    image = image_identity()
    if image["tree_source_drift"]:
        raise Refused(f"the tree's RTL/ROM sources differ from the image's: "
                      f"{sorted(image['tree_source_drift'])}")
    cmds = commands()
    ev = evidence(image)
    for p, h in ev["held_note"].items():
        if h["replay_capture_sha256"] != cmds[h["command"]]["cmds_sha256"]:
            raise Refused(f"held-note {p}: the replayed capture is not what the CLI emits now")
        if h["verdict"] != "PASS":
            raise Refused(f"held-note {p}: evidence verdict {h['verdict']}")
    return {
        "schema": "gf180-parasynth release manifest v1",
        **{k: DECLARED[k] for k in ("release", "status")},
        "configuration": CONFIG,
        "image": image,
        "rollback": rollback_identity(),
        "host_protocol": DECLARED["host_protocol"],
        "presets": presets(),
        "commands": cmds,
        "domain": domain(),
        "evidence": ev,
        "runtime_qualification": DECLARED["runtime_qualification"],
        "exclusions": DECLARED["exclusions"],
        "physical_capture": DECLARED["physical_capture"],
        "cannot_bind": DECLARED["cannot_bind"],
    }


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [path]


def check(manifest_path: Path = MANIFEST) -> tuple:
    """(verdict, detail)."""
    try:
        fresh = json.loads(json.dumps(build()))
    except Refused as exc:
        return "REFUSED", str(exc)
    if not Path(manifest_path).exists():
        return "REFUSED", f"no manifest at {manifest_path}"
    committed = json.loads(Path(manifest_path).read_text())
    diffs = _diff(committed, fresh)
    if diffs:
        return "STALE", "differs from a fresh derivation at: " + ", ".join(diffs[:20])
    return "BOUND", f"{_rel(manifest_path)} equals a fresh derivation; artifacts agree"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help="re-derive and write the manifest")
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    a = ap.parse_args(argv)
    if a.write:
        try:
            m = build()
        except Refused as exc:
            print(f"release_manifest: REFUSED -- {exc}")
            return 2
        a.manifest.write_text(json.dumps(m, indent=2, sort_keys=False) + "\n")
        print(f"release_manifest: wrote {a.manifest}")
    verdict, detail = check(a.manifest)
    print(f"release_manifest: {verdict} -- {detail}")
    return {"BOUND": 0, "STALE": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
