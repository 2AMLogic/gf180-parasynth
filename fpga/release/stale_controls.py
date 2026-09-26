#!/usr/bin/env python3
"""fpga/release/stale_controls.py -- the STALE counterexamples T-RELEASE-BOUND
carries (issue #278): each one shows that a required child of the trial CAN
fail, on real bytes, without touching the real manifest or the real tree.

    python fpga/release/stale_controls.py manifest --out DIR
    python fpga/release/stale_controls.py binding  --out DIR

VERDICTS (own convention, printed as `stale_control: <WORD> -- detail`):
  1 STALE    the checker under test called the counterexample STALE, and for
             exactly the substituted field/file -- the control is caught
  0 BOUND    the checker ACCEPTED the counterexample: the control is NOT
             caught (docs/trials.md rule 4 -- the apparatus did not fail)
  2 REFUSED  the counterexample could not be shown: an input is missing or
             unreadable, the unmodified copy was not BOUND (so a STALE on the
             modified one would prove nothing), the substitution is a no-op,
             or the checker failed for a DIFFERENT reason than the one injected

Both counterexamples are historical states of this repository, not invented
values:

manifest  The committed manifest, copied into DIR, with the held-note command's
          bytes identity (commands.held-default.cmds_sha256 / .packets) replaced
          by the bytes the PRE-FIX CLI emitted for the same documented command
          (`run --note 45 --fixture none` before #255 added the mixer writes --
          fpga/release/evidence/held-note/default-legacy/capture.cmds, the
          capture the held-legacy-silent control of T-PLAY-DIGITAL replays).
          That is the manifest someone would have bound before the fix: it
          names bytes that no longer ship. release_manifest.py must call it
          STALE at those fields. The copy and the stale manifest are kept in
          DIR (they are the evidence); fpga/release/baseline-2025.1.json is
          only read.

binding   An isolated copy of the files tools/check_arty_evidence_binding.py
          reads (its script, fpga/*.py, the compiled sources and ROMs, the
          bound record), made in a temporary directory, with
          rtl-sketch/voice_dp.v replaced by the one the PUBLISHED IMAGE was
          built from (release_manifest.IMAGE_SOURCE_COMMIT, before #252's
          per-oscillator drift). That is the tree before the drift-clean record
          existed: the bound record does not cover it. The copy's checker must
          call it STALE naming exactly voice_dp.v. The real tree is only read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PREFIX = "stale_control: "
LEGACY_CAPTURE = HERE / "evidence" / "held-note" / "default-legacy" / "capture.cmds"
STALE_COMMAND = "held-default"
BINDING_SUBSTITUTE = "rtl-sketch/voice_dp.v"


class Refused(RuntimeError):
    pass


def say(word: str, detail: str) -> int:
    print(f"{PREFIX}{word} -- {detail}", flush=True)
    return {"STALE": 1, "BOUND": 0}.get(word, 2)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}")


# ---- manifest ------------------------------------------------------------------
def manifest_control(out: Path) -> int:
    import release_manifest as rm
    raw = _read(rm.MANIFEST)
    try:
        committed = json.loads(raw)
        entry = committed["commands"][STALE_COMMAND]
    except (ValueError, KeyError, TypeError) as exc:
        raise Refused(f"the committed manifest {rm._rel(rm.MANIFEST)} is unreadable or has no "
                      f"commands.{STALE_COMMAND}: {exc!r}")
    legacy = _read(LEGACY_CAPTURE)
    substitute = {"cmds_sha256": _sha(legacy), "packets": len(legacy.decode().splitlines())}
    changed = sorted(k for k, v in substitute.items() if entry.get(k) != v)
    if "cmds_sha256" not in changed:
        raise Refused("the pre-fix capture has the same bytes as the committed command: "
                      "the substitution would change nothing")

    faithful = out / "faithful-manifest.json"
    faithful.write_bytes(raw)
    verdict, detail = rm.check(faithful)
    print(f"  | unmodified copy: release_manifest: {verdict} -- {detail}")
    if verdict != "BOUND":
        raise Refused(f"the unmodified copy of the manifest is {verdict}, not BOUND, so a "
                      "STALE on the substituted copy would prove nothing")

    stale = json.loads(raw)
    stale["commands"][STALE_COMMAND].update({k: substitute[k] for k in changed})
    stale_path = out / "stale-manifest.json"
    stale_path.write_text(json.dumps(stale, indent=2) + "\n")
    verdict, detail = rm.check(stale_path)
    print(f"  | pre-fix bytes:   release_manifest: {verdict} -- {detail}")
    injected = {f"commands.{STALE_COMMAND}.{k}" for k in changed}
    where = "commands." + STALE_COMMAND + " " + ", ".join(
        f"{k} {entry.get(k)} -> {substitute[k]}" for k in changed)
    if verdict == "BOUND":
        return say("BOUND", f"release_manifest.py ACCEPTED a manifest bound to the pre-fix CLI "
                            f"bytes ({where}): the control is not caught")
    if verdict != "STALE":
        raise Refused(f"release_manifest.py said {verdict}, not STALE: {detail}")
    named = set(detail.split(" at: ", 1)[1].split(", ")) if " at: " in detail else set()
    if named != injected:
        raise Refused(f"STALE for a different reason: it names {sorted(named)}, the "
                      f"counterexample changed {sorted(injected)}")
    return say("STALE", f"a manifest bound to the pre-fix CLI bytes of `run --note 45 --fixture "
                        f"none` ({where}) is STALE at exactly {sorted(injected)}")


# ---- binding ----------------------------------------------------------------------
def _binding_files() -> tuple[list[str], list[str]]:
    """(every file the binding checker reads, the bound records) -- from the
    real tree's own publisher and builder, not a hand-kept list."""
    sys.path.insert(0, str(ROOT / "fpga"))
    import build_arty
    import publish_arty
    rel = lambda p: str(Path(p).resolve().relative_to(ROOT))           # noqa: E731
    records = sorted(rel(p) for p in publish_arty.VERIFICATION_BY_WRAPPER.values())
    files = {"tools/check_arty_evidence_binding.py", "rtl-sketch/verify_synth_top.py"}
    files |= {rel(p) for p in (ROOT / "fpga").glob("*.py")}
    files |= {rel(p) for p in build_arty.sources() + build_arty.roms()}
    files |= {rel(p) for p in (ROOT / "rtl-sketch").glob("*.hex")}
    return sorted(files | set(records)), records


def _run_checker(tree: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(tree / "tools/check_arty_evidence_binding.py")],
                       cwd=tree, capture_output=True, text=True, timeout=300)
    return r.returncode, (r.stdout + r.stderr).strip()


def binding_control(out: Path) -> int:
    import release_manifest as rm
    try:
        files, records = _binding_files()
    except (OSError, ValueError, ImportError) as exc:
        raise Refused(f"cannot list the binding checker's inputs: {exc!r}")
    if BINDING_SUBSTITUTE not in files:
        raise Refused(f"{BINDING_SUBSTITUTE} is not a compiled source of the Arty wrapper")
    r = subprocess.run(["git", "-C", str(ROOT), "show",
                        f"{rm.IMAGE_SOURCE_COMMIT}:{BINDING_SUBSTITUTE}"], capture_output=True)
    if r.returncode != 0:
        raise Refused(f"{BINDING_SUBSTITUTE} is not readable at the image's source commit "
                      f"{rm.IMAGE_SOURCE_COMMIT[:12]}: {r.stderr.decode().strip()}")
    old = r.stdout
    live = _read(ROOT / BINDING_SUBSTITUTE)
    if _sha(old) == _sha(live):
        raise Refused(f"{BINDING_SUBSTITUTE} at {rm.IMAGE_SOURCE_COMMIT[:12]} is the tree's own: "
                      "the substitution would change nothing")
    record = {"counterexample": f"{BINDING_SUBSTITUTE} as built into the published image "
                                f"(git show {rm.IMAGE_SOURCE_COMMIT}:{BINDING_SUBSTITUTE})",
              "substituted": {"path": BINDING_SUBSTITUTE, "tree_sha256": _sha(live),
                              "substitute_sha256": _sha(old)},
              "bound_records": records, "files_copied": len(files)}
    with tempfile.TemporaryDirectory(prefix="stale-binding-") as d:
        tree = Path(d)
        for f in files:
            src = ROOT / f
            if not src.is_file():
                raise Refused(f"the binding checker's input {f} is missing")
            (tree / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tree / f)
        rc, text = _run_checker(tree)
        record["unmodified_copy"] = {"rc": rc, "output": text}
        print("\n".join(f"  | unmodified copy: {ln}" for ln in text.splitlines()))
        if rc != 0 or not text.startswith("BOUND"):
            (out / "binding-control.json").write_text(json.dumps(record, indent=1) + "\n")
            raise Refused(f"the unmodified isolated copy is not BOUND (exit {rc}), so a STALE "
                          "on the substituted copy would prove nothing")
        (tree / BINDING_SUBSTITUTE).write_bytes(old)
        rc, text = _run_checker(tree)
        record["substituted_copy"] = {"rc": rc, "output": text}
    (out / "binding-control.json").write_text(json.dumps(record, indent=1) + "\n")
    print("\n".join(f"  | pre-drift voice_dp.v: {ln}" for ln in text.splitlines()))
    what = (f"a tree holding the published image's {BINDING_SUBSTITUTE} "
            f"({_sha(old)[:12]}, pre-#252) instead of the bound one ({_sha(live)[:12]})")
    if rc == 0:
        return say("BOUND", f"check_arty_evidence_binding.py ACCEPTED {what}: not caught")
    if rc != 1:
        raise Refused(f"check_arty_evidence_binding.py exited {rc}, not 1: {text[:300]}")
    # the checker lists the uncovered sources between its STALE line and its
    # "Re-run the bench" advice (whose commands share the indentation)
    moved = []
    for ln in text.splitlines()[1:]:
        if not ln.startswith("           "):
            break
        moved.append(ln.strip())
    if not text.startswith("STALE") or moved != [BINDING_SUBSTITUTE]:
        raise Refused(f"STALE for a different reason: it names {moved}, the counterexample "
                      f"changed [{BINDING_SUBSTITUTE!r}]")
    return say("STALE", f"{what} is STALE against {records}, naming exactly {BINDING_SUBSTITUTE}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case", choices=("manifest", "binding"))
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    try:
        return (manifest_control if a.case == "manifest" else binding_control)(a.out)
    except Refused as exc:
        return say("REFUSED", str(exc))


if __name__ == "__main__":
    sys.exit(main())
