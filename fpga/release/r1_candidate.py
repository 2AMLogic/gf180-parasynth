#!/usr/bin/env python3
"""fpga/release/r1_candidate.py -- the FROZEN R1 player-preview candidate
(#279, plan087 section 4 and B2, plan088).

Naming (plan087/plan088): **R0** is the published Arty image, whose immutable
release string is `arty-a7-100t baseline 2025.1, r1` (fpga/release/RELEASE.md,
contract revision 11, bitstream a66c9349...). **R1** is this candidate: the
revision-14 tree in the Arty configuration OSC2X=1 FILTER2X=1 PULSE2X=0, driven
by the host with `--image tree`. Its image is published and bound separately by
fpga/release/r1_release.py (r1-2025.1.json, #280); this record stays the frozen
source/host/evidence identity that manifest binds. Neither identity's evidence
stands in for the other's.

WHY THE TARGET IS FROZEN HERE, NOT READ FROM THE SENDER (plan088). A playback
verifier that takes its expectation from the same image selector the sender
used cannot see a sender that picked the wrong kit: the revision-11 kit sent
through revision-14 RTL and compared with a revision-11 expectation agrees
perfectly and never plays the L2 clap. So this module owns the R1 target
independently of any CLI flag:

  * contract revision 14 and the drum kit's digest (KIT_R14_SHA256): the
    tree's `drums_fx.kit_808()` must still hash to it or `frozen_kit()`
    REFUSES -- a kit edit is a new candidate, never a silent change;
  * the clap's final strike: ENV_FRATE[8] (address 0x63) must be written with
    the frozen NONZERO rate. "Some write to the new address" is not enough;
  * the known-state preamble every R1 session starts with (voice RESET 0x23,
    drum RESET 0xFF: every register and state of both sections to 0), so
    routing, modulation, drift and every unwritten drum register are KNOWN
    rather than assumed from an earlier (possibly engineering) session.

`check_init(static_writes, fixture)` compares a sender's actual emitted setup
writes against that frozen target, in order. The playback verifiers call it
BEFORE any RTL run, so a wrong kit is a host-correctness FAIL in seconds, not
a multi-hour simulation of the wrong thing.

THE RECORD. `fpga/release/r1-candidate.json` is derived from the tree:

    python fpga/release/r1_candidate.py            # BOUND (0) / STALE (1) / REFUSED (2)
    python fpga/release/r1_candidate.py --write    # re-derive deliberately
    python fpga/release/r1_candidate.py init-check --fixture demo --image tree

BOUND means the committed record equals a fresh derivation: the compiled RTL
and ROM bytes (also verified byte-identical at RTL_FROZEN_AT), the compile
options, the kit digest, the presets' image bytes, the host files and the
exact bytes of every supported R1 command, and the digest of every committed
evidence file -- whose RTL run identities must equal the candidate's sources.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, ROOT / "fpga", ROOT / "model", ROOT / "audition", ROOT / "rtl-sketch",
           ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

RECORD = HERE / "r1-candidate.json"
EVIDENCE_DIR = ROOT / "fpga/reports/r1-candidate"

NAME = "R1 player preview"
R0_ALIAS = "arty-a7-100t baseline 2025.1, r1 (plan087 R0; contract revision 11; image a66c9349...)"
CONTRACT_REVISION = 14
HOST_IMAGE = "tree"                 # uart_host / midi_session `--image` for this candidate
# main at the freeze; the compiled RTL/ROM set last changed at 36e1f83 (#273's
# merge of polyBLAMP #244 and clap L2). Every source is verified at this commit.
RTL_FROZEN_AT = "6864435aa6eb3ecb406d13cf86f6a6bb79b2b8db"
# drums_fx._kit_sha256(kit_808()) at the freeze: revision 14, 148 writes
KIT_R14_SHA256 = "321a93546cfa5ffab03b3cf91557580ea7655ada933ce380c81cd07597a9b683"
KIT_R14_WRITES = 148
SEC_VOICE, SEC_DRUM = 0, 1
A_VOICE_RESET, A_DRUM_RESET = 0x23, 0xFF
#: every R1 session's first two writes, before its image
PREAMBLE = ((0, SEC_VOICE, A_VOICE_RESET, 0), (0, SEC_DRUM, A_DRUM_RESET, 0))

# The supported R1 quick-start (the R0 set with `--image tree`, minus
# `play --fixture m5a`: it sends no image, so what it plays depends on
# whatever the device held -- `run --note 45 --fixture m5a` plays the same
# phrase from the known state).
COMMANDS = (
    ("held-default", ["run", "--note", "45", "--fixture", "none"]),
    ("held-m5a-saw", ["run", "--preset", "m5a-saw", "--note", "72", "--fixture", "none"]),
    ("held-m5a-pulse", ["run", "--preset", "m5a-pulse", "--note", "72", "--fixture", "none"]),
    ("run-m5a", ["run", "--note", "45", "--fixture", "m5a"]),
    ("demo", ["run", "--fixture", "demo"]),
    ("bar808-full", ["run", "--fixture", "bar808-full"]),
)
REMOVED_FROM_QUICKSTART = {
    "play --fixture m5a": "sends no patch image, so its sound depends on the device's prior "
                          "state (unknown after any other session); `run --note 45 --fixture m5a` "
                          "plays the same phrase from the known state and is qualified instead",
}
HOST_FILES = ("fpga/uart_host.py", "fpga/midi_session.py", "fpga/live_midi_contract.py",
              "fpga/release/qualified_domain.py", "fpga/release/r1_candidate.py",
              "fpga/spi_host.py", "fpga/fixtures.py", "fpga/selected_preset.py",
              "model/voice_fx.py", "model/drums_fx.py")


class Refused(RuntimeError):
    """The candidate cannot be derived or the evidence does not belong to it."""


def sha(path) -> str:
    import hashlib
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rel(p) -> str:
    p = Path(p).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


# ---- the frozen target -------------------------------------------------------
def frozen_kit() -> list:
    """kit_808() as revision 14 froze it, or REFUSED."""
    import drums_fx as dx
    kit = dx.kit_808()
    got = dx._kit_sha256(kit)
    if got != KIT_R14_SHA256 or len(kit) != KIT_R14_WRITES:
        raise Refused(f"drums_fx.kit_808() hashes to {got[:12]} ({len(kit)} writes), not the "
                      f"frozen R1 kit {KIT_R14_SHA256[:12]} ({KIT_R14_WRITES}): a kit change is a "
                      "new candidate -- re-freeze deliberately")
    return [(int(a), int(v)) for a, v in kit]


def clap_final_strike() -> tuple:
    """(address, value) of ENV_FRATE[8] in the frozen kit; the value is nonzero."""
    import drums_fx as dx
    addr = dx.A_ENV + dx.E_CPBURST * dx.ENV_STRIDE + 3
    vals = [v for a, v in frozen_kit() if a == addr]
    if len(vals) != 1 or not vals[0]:
        raise Refused(f"the frozen kit writes ENV_FRATE[8] (0x{addr:02X}) {vals}: R1 needs one "
                      "nonzero final-strike rate")
    return addr, vals[0]


def check_init(static_writes, *, kit_expected: bool) -> list:
    """Problems (empty = OK) with a sender's SETUP writes, (flag, sec, addr,
    data) in send order, against the frozen R1 target: the known-state
    preamble first; and, when the command loads a kit, the kit's writes in
    order equal to the frozen revision-14 kit, ending with the clap's nonzero
    final-strike rate."""
    ws = [(int(f), int(s), int(a) & 0xFF, int(d) & 0xFFFFFFFF) for f, s, a, d in static_writes]
    problems = []
    if tuple(ws[:len(PREAMBLE)]) != PREAMBLE:
        problems.append(f"known-state preamble missing: first writes {ws[:len(PREAMBLE)]}, "
                        f"expected {list(PREAMBLE)} (voice RESET, drum RESET)")
    if not kit_expected:
        return problems
    kit = frozen_kit()
    kaddr = {a for a, _ in kit}
    sent = [(a, d) for f, s, a, d in ws if s == SEC_DRUM and a in kaddr]
    if sent != kit:
        first = next((i for i, (x, y) in enumerate(zip(sent, kit)) if x != y),
                     min(len(sent), len(kit)))
        got = sent[first] if first < len(sent) else None
        want = kit[first] if first < len(kit) else None
        problems.append(f"drum kit differs from the frozen revision-14 kit at kit write {first}: "
                        f"sent {len(sent)} kit writes, expected {len(kit)}; first difference "
                        f"sent {got} vs frozen {want}")
    addr, val = clap_final_strike()
    fr = [d for f, s, a, d in ws if s == SEC_DRUM and a == addr]
    if not fr:
        problems.append(f"no ENV_FRATE[8] (0x{addr:02X}) write: the clap's final strike is off "
                        "(a revision-11 kit)")
    elif fr[-1] != val:
        problems.append(f"ENV_FRATE[8] (0x{addr:02X}) = {fr[-1]}, frozen R1 value {val}")
    return problems


def emitted_setup(argv: list) -> tuple:
    """(setup writes, kit_expected) of a CLI command as it emits them now
    (dry run): the live writes before the first scheduled event."""
    import uart_host as uh
    with tempfile.TemporaryDirectory() as d:
        prefix = str(Path(d) / "cap")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = uh.main(["--dry-run", *argv, "--capture", prefix])
        if rc != 0:
            raise Refused(f"the CLI refused {argv} (exit {rc})")
        plan = json.loads(Path(prefix + ".plan.json").read_text())
    ws = []
    for r in plan["rows"]:
        if r["kind"] != "write":
            continue
        e = r["expect"]
        ws.append((e["flag"], e["sec"], e["addr"], e["data"]))
    fixture = argv[argv.index("--fixture") + 1] if "--fixture" in argv else "none"
    return ws, fixture not in ("none", "m5a")


# ---- the record ---------------------------------------------------------------
def _frozen_blob(rel: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "show", f"{RTL_FROZEN_AT}:{rel}"],
                              capture_output=True, check=True).stdout
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise Refused(f"cannot read {rel} at {RTL_FROZEN_AT[:12]} ({exc}); a shallow "
                      "clone cannot verify the freeze") from None


def rtl_identity() -> dict:
    """R1's compiled sources, ROMs and constraints AS FROZEN: read from git at
    RTL_FROZEN_AT, never from the working tree. The tree may move on (the
    next image, R2, changes voice_dp.v) without touching R1's identity; how
    far it has moved is reported by tree_drift(), not bound here."""
    import hashlib
    import build_arty as ba
    h = lambda rel: hashlib.sha256(_frozen_blob(rel)).hexdigest()      # noqa: E731
    srcs = {_rel(p): h(_rel(p)) for p in ba.sources()}
    roms = {_rel(p): h(_rel(p)) for p in ba.roms()}
    synth = next(ln for ln in ba.tcl_script(Path("OUT"), []).splitlines()
                 if ln.startswith("synth_design"))
    return {"frozen_at": RTL_FROZEN_AT, "configuration": dict(ba.CONFIG), "part": ba.PART,
            "synth_design": synth, "constraints": {_rel(ba.XDC): h(_rel(ba.XDC))},
            "sources": srcs, "roms": roms}


def tree_drift(rtl: dict | None = None) -> list:
    """Frozen sources the WORKING TREE no longer holds (informational: the
    tree has moved past R1; a new image is a new candidate, never this one)."""
    rtl = rtl or rtl_identity()
    frozen = {**rtl["sources"], **rtl["roms"], **rtl["constraints"]}
    return sorted(rel for rel, want in frozen.items()
                  if not (ROOT / rel).exists() or sha(ROOT / rel) != want)


def kit_identity() -> dict:
    import drums_fx as dx
    import uart_host as uh
    if uh.IMAGE_REVISION.get(HOST_IMAGE) != CONTRACT_REVISION:
        raise Refused(f"uart_host.IMAGE_REVISION[{HOST_IMAGE!r}] is "
                      f"{uh.IMAGE_REVISION.get(HOST_IMAGE)}, not R1's {CONTRACT_REVISION}")
    if uh.image_kit(HOST_IMAGE) != dx.kit_808():
        raise Refused("uart_host.image_kit('tree') is not drums_fx.kit_808()")
    addr, val = clap_final_strike()
    return {"revision": CONTRACT_REVISION, "sha256": KIT_R14_SHA256, "writes": KIT_R14_WRITES,
            "clap_final_strike": {"register": "ENV_FRATE[8]", "address": addr, "value": val},
            "digest_rule": "drums_fx._kit_sha256: sha256 of the decimal words addr<<32|value, "
                           "comma-joined"}


def commands() -> dict:
    import release_manifest as rm
    out = {}
    with tempfile.TemporaryDirectory() as d:
        for name, argv in COMMANDS:
            sub = Path(d) / name
            sub.mkdir()
            full = [*argv, "--image", HOST_IMAGE]
            cap = rm._capture(full, sub)
            setup, kit = emitted_setup(full)
            probs = check_init(setup, kit_expected=kit)
            if probs:
                raise Refused(f"{name}: emitted setup is not the frozen R1 target: {probs}")
            out[name] = {"command": ".venv/bin/python fpga/uart_host.py --port "
                                   "/dev/cu.usbserial-XXXX " + " ".join(full),
                         **cap, "setup_writes": len(setup), "loads_kit": kit}
    out["live-midi"] = {"command": ".venv/bin/python fpga/midi_session.py --port "
                                   "/dev/ttyUSB1 --midi-in /dev/snd/midiC1D0 --image tree",
                        "sim_start": ".venv/bin/python fpga/midi_session.py --port sim "
                                     "--midi-in scripted:coverage",
                        "init_writes": len(live_midi_init())}
    return out


def live_midi_init() -> list:
    """The live session's known-state image for image tree, as the session builds it."""
    import midi_session as ms

    class _Nul:
        def write(self, b): return len(b)
        def read(self, n=1): return b""
        def flush(self): pass
        timeout = 0
    s = ms.MidiSession(_Nul(), image=HOST_IMAGE)
    ws = s.init_writes()
    probs = check_init(ws, kit_expected=True)
    if probs:
        raise Refused(f"live MIDI known state is not the frozen R1 target: {probs}")
    return ws


def evidence() -> dict:
    """Every committed evidence file's digest; every RTL run identity must
    name exactly this candidate's compiled sources and ROMs."""
    if not EVIDENCE_DIR.is_dir():
        return {"dir": _rel(EVIDENCE_DIR), "files": {}, "rtl_runs_bound": 0}
    rtl = rtl_identity()
    files, bound = {}, 0
    for p in sorted(q for q in EVIDENCE_DIR.rglob("*") if q.is_file()):
        files[_rel(p)] = sha(p)
        if p.name.endswith("run_identity.json"):
            ident = json.loads(p.read_text()).get("identity", {})
            for kind in ("sources", "roms"):
                for rel, h in (ident.get(kind) or {}).items():
                    want = rtl[kind].get(rel)
                    if want is not None and want != h:
                        raise Refused(f"{_rel(p)}: {rel} is {h[:12]}, the candidate's is "
                                      f"{want[:12]}: that run is not R1 evidence")
            bound += 1
    return {"dir": _rel(EVIDENCE_DIR), "files": files, "rtl_runs_bound": bound}


def domain() -> dict:
    import qualified_domain as qd
    import release_manifest as rm
    d = rm.domain()
    d.pop("precondition_not_verified", None)
    d["session_start"] = (
        "every R1 image-loading command (uart_host run/load --image tree, midi_session "
        "--image tree) sends the known-state preamble first -- voice RESET (0x23) and drum "
        "RESET (0xFF), which zero every register and state of both sections -- then its whole "
        "image; ROUTE, modulation, drift and unwritten drum registers are therefore KNOWN (0), "
        "not assumed. Before sending, the host REFUSES if the device reports queued events or "
        "writes left from an earlier session.")
    d["physical_precondition"] = (
        "the board is programmed with the R1 bitstream (fpga/release/r1-2025.1.json, #280). "
        "The host cannot read the image identity back, so this is declared, not verified.")
    d["open_defect"] = ("#247 stays OPEN; its restrictions stay in force: INC_RANGE, GLIDE_247 "
                        "and ROUTE_DRUMFILTER are refused, with a reason, before any byte is sent")
    d["drift"] = ("DRIFT (0x2D) is compiled into R1 (#252) but not in the qualified player "
                  "domain: a nonzero write is refused (NOT_IN_IMAGE rule, R1 wording)")
    d["rules"] = list(qd.RULES)
    return d


DECLARED = {
    "status": "CANDIDATE: digital (RTL-simulation) evidence. The R1 bitstream built from it is "
              "published and bound in fpga/release/r1-2025.1.json (#280); no physical "
              "programming, control or audio capture.",
    "r0": R0_ALIAS,
    "included": [
        "clap L2 final strike (#261/#273, contract revision 14: ENV_FRATE in the drum engine)",
        "polyBLAMP on the shark-tooth corner (#244, revision 13) -- compiled in; the shark-tooth "
        "is not in the advertised waveform set",
        "per-oscillator drift (#252) -- compiled in; outside the player domain",
        "the UART control bridge and the rolling scheduler (#210)",
        "the release-domain validator (#255) and the live MIDI session (#281/#286)",
    ],
    "excluded": [
        "PULSE2X=1 (#205, #248: missed deadlines above Nyquist; needs its own image)",
        "#247's domain (glides touching 2^23; ROUTE = 1): refused, the issue stays open",
        "any open PR or experiment not needed for the advertised experience",
        "five of the sixteen 808 sounds from live MIDI: the three congas, rimshot and maracas "
        "are refused as unmapped (#298); the live player maps 11: BD SD LT MT HT CH OH CP CB CL CY",
        "`play --fixture m5a` from the quick-start (see removed_from_quickstart)",
    ],
    "host_version": "no numeric protocol version exists; the host is pinned by the sha256 of "
                    "its files (host.files) and the exact bytes of every supported command "
                    "(commands.*.cmds_sha256)",
}


def build() -> dict:
    import release_manifest as rm
    return {
        "schema": "gf180-parasynth R1 candidate v1",
        "candidate": NAME,
        "status": DECLARED["status"],
        "r0_is": DECLARED["r0"],
        "contract_revision": CONTRACT_REVISION,
        "host_image_selector": f"--image {HOST_IMAGE}",
        "rtl": rtl_identity(),
        "kit": kit_identity(),
        "known_state_preamble": [list(w) for w in PREAMBLE],
        "presets": rm.presets(),
        "host": {"version": DECLARED["host_version"],
                 "files": {f: sha(ROOT / f) for f in HOST_FILES}},
        "commands": commands(),
        "removed_from_quickstart": REMOVED_FROM_QUICKSTART,
        "domain": domain(),
        "included": DECLARED["included"],
        "excluded": DECLARED["excluded"],
        "evidence": evidence(),
    }


def _diff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        return out
    return [] if a == b else [path]


def check(record: Path = RECORD) -> tuple:
    try:
        fresh = json.loads(json.dumps(build()))
    except Refused as exc:
        return "REFUSED", str(exc)
    if not Path(record).exists():
        return "REFUSED", f"no candidate record at {_rel(record)}"
    try:
        committed = json.loads(Path(record).read_text())
    except (OSError, ValueError) as exc:
        return "REFUSED", f"the candidate record is unreadable: {exc}"
    diffs = _diff(committed, fresh)
    if diffs:
        return "STALE", "differs from a fresh derivation at: " + ", ".join(diffs[:20])
    moved = tree_drift(fresh["rtl"])
    note = (f"; NOTE the working tree has moved past R1 in {moved} -- R1's sources are "
            f"verified at {RTL_FROZEN_AT[:12]}") if moved else ""
    return "BOUND", (f"{_rel(record)} equals a fresh derivation (RTL frozen at "
                     f"{RTL_FROZEN_AT[:12]}, kit {KIT_R14_SHA256[:12]}, "
                     f"{fresh['evidence']['rtl_runs_bound']} RTL runs bound){note}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="check", choices=("check", "init-check"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--record", type=Path, default=RECORD)
    ap.add_argument("--fixture", default="demo")
    ap.add_argument("--image", default=HOST_IMAGE)
    a = ap.parse_args(argv)
    if a.action == "init-check":
        argv_ = ["run", "--fixture", a.fixture, "--image", a.image]
        if a.fixture in ("none", "m5a"):
            argv_[1:1] = ["--note", "45"]
        try:
            setup, kit = emitted_setup(argv_)
            probs = check_init(setup, kit_expected=kit or a.fixture not in ("none", "m5a"))
        except Refused as exc:
            print(f"r1_candidate: REFUSED -- {exc}")
            return 2
        print(f"r1_candidate: init {'OK' if not probs else 'FAIL'} -- "
              f"{' '.join(argv_)}: {len(setup)} setup writes" + "".join(f"\n  {p}" for p in probs))
        return 0 if not probs else 1
    if a.write:
        try:
            m = build()
        except Refused as exc:
            print(f"r1_candidate: REFUSED -- {exc}")
            return 2
        a.record.write_text(json.dumps(m, indent=2) + "\n")
        print(f"r1_candidate: wrote {_rel(a.record)}")
    verdict, detail = check(a.record)
    print(f"r1_candidate: {verdict} -- {detail}")
    return {"BOUND": 0, "STALE": 1}.get(verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
