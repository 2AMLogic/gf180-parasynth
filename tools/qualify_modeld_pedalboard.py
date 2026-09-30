#!/usr/bin/env python3
"""Render ONE Model D clip through the pedalboard-hosted rig and qualify it --
issue #124's sequencing gate, as a command.

    python3 tools/qualify_modeld_pedalboard.py
    python3 tools/qualify_modeld_pedalboard.py --json docs/pedalboard-rig-run.json
    python3 tools/qualify_modeld_pedalboard.py --wav /tmp/modeld-pedalboard.wav

THREE OUTCOMES, the same convention `tools/refprofile.py` uses
--------------------------------------------------------------
    exit 0  OK       the rig qualified. The clip may be frozen, and the rest of
                     #124's scope -- the 8 Mono family anchor cases -- is
                     unblocked
    exit 1  FAIL     the rig was measured and is NOT usable. Every check that
                     could answer did; the record says which ones said no
    exit 2  REFUSED  a precondition of the apparatus is unmet, so nothing was
                     attempted: no `pedalboard`, no Model D bundle, or a check
                     that could not answer at all

**On most hosts in this fleet this exits 2, and that is the correct answer.**
`pedalboard` and the Model D bundle live on an operator's macOS machine; Linux
CI has neither. A REFUSED here is a stated no-verdict, not a hole in the
instrument, and it is why `RIG_VERDICTS["modeld-pedalboard"]["qualified"]` is
`None` rather than `False` -- "nobody has run it" and "it cannot be used" are
different facts.

WHAT A NON-ZERO EXIT MEANS FOR THE REST OF THE WORK
---------------------------------------------------
Issue #124 gates its own scope on this one clip, and the failure branch is a
COMPLETE outcome rather than a bug to route around. If the octave default or the
clipping cannot be corrected through Model D's own parameters, this tool prints
the sweep table that says so and says explicitly that the Mono
re-specification question in #122 (Route 1 / 2 / 3) reopens. It does not keep an
octave-down clipped Model D as the Mono reference in order to report more scope
completed.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(ROOT / "audition"))
sys.path.insert(0, str(ROOT / "tools"))

import refprofile as rp                                             # noqa: E402

OK, FAIL, REFUSED = rp.OK, rp.FAIL, rp.REFUSED

RIG = "modeld-pedalboard"
HOST = "pedalboard"

REOPEN_122 = """
The gate's failure branch is a COMPLETE outcome, not an error to route around.
Model D under pedalboard cannot be the Mono cases' reference, and the
re-specification question in issue #122 (Route 1 / Route 2 / Route 3) REOPENS.
Do not freeze a clip from this rig, and do not record `qualified: true` for it.
The sweep tables above are the evidence; keep them with the finding.
""".strip()

#: What a REFUSAL FOR LACK OF APPARATUS means, which is a different thing and
#: must not be confused with the one above. An earlier draft printed the #122
#: note on both, so a Linux host with no plugin announced that Model D cannot be
#: the Mono reference -- a conclusion about a plugin it had never loaded, which
#: is precisely the tool-that-answers-when-it-cannot defect this file is built
#: against.
NO_APPARATUS = """
This is a stated NO-VERDICT and nothing follows from it about the plugin. No
measurement was attempted, so issue #122's re-specification question is NOT
reopened by this run and `RIG_VERDICTS['modeld-pedalboard']` stays
`qualified: None`. Run this on a host with pedalboard and the Model D bundle to
get a verdict either way.
""".strip()


def host_version() -> str:
    import pedalboard
    return str(getattr(pedalboard, "__version__", "unknown"))


def build():
    """Construct the rig, or raise. Imports are inside so this module can be
    imported (and tested) on a host with neither the plugin nor the host."""
    try:
        import pedalboard                                           # noqa: F401
    except ImportError as e:
        raise rp.Refused(
            f"no pedalboard on this machine ({e}). Rendering through the "
            f"pedalboard-hosted rig needs `pedalboard` and the Model D VST3 "
            f"bundle; verifying the frozen profile needs neither. See "
            f"docs/pedalboard-rig.md for the dependency")
    import reference_rigs as rr
    if not pathlib.Path(rr.PATH_MODELD).exists():
        raise rp.Refused(f"Moog Model D is not installed at {rr.PATH_MODELD}")
    return rr.ModelDPedalboardRig()


def report(dev=None, qual=None, *, refusal: str | None = None) -> dict:
    """The record, whatever the outcome. A refusal with no record is a refusal
    nobody can act on."""
    out = {
        "what": "one Model D clip rendered through the pedalboard-hosted rig and "
                "qualified before anything is frozen (issue #124)",
        "rig": RIG,
        "host": HOST,
        "at": rp._now(),
        "worktree": rp.worktree_state(),
        "rig_source_sha256": rp.file_sha256(ROOT / "model" / "reference_rigs.py"),
        "qualification_source_sha256": rp.file_sha256(ROOT / "model" / "rig_qualification.py"),
        "builder_sha256": rp.file_sha256(pathlib.Path(__file__)),
        "python": sys.version.split()[0],
        "numpy": np.__version__,
        "platform": sys.platform,
        "verdict_recorded_in": "tools/refprofile.py RIG_VERDICTS['modeld-pedalboard']",
    }
    if refusal is not None:
        out["outcome"] = "REFUSED"
        out["why"] = refusal
        return out
    if dev is not None:
        # The full #123 tuple, through the SAME recorder the dawdreamer path
        # uses. It refuses rather than reports if a field is unstated.
        out["environment"] = rp.environment_tuple(
            host=HOST, host_version=host_version(),
            plugin=rp.plugin_identity(dev.path), block=dev.block, sr=dev.sr,
            licence=dev.licence,
            preset=rp.preset_identity(dev.plugin,
                                      source="the plugin's own defaults, plus "
                                             "ModelDPedalboardRig.setup and the two "
                                             "calibrations in its qualification"),
            automates=False,
            note="pedalboard has no parameter automation, so nothing here can be a "
                 "per-block ramp artefact; the block is pinned anyway because "
                 "pedalboard's own default is 8192")
        out["parameters_after_qualification"] = rp._param_dump(dev.p)
        out["note_midi"] = int(dev.note)
        out["pinned_readback"] = dev.pinned_report()
    if qual is not None:
        out["outcome"] = qual.verdict.upper()
        out["qualification"] = qual.as_dict()
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", default=None, metavar="PATH",
                    help="write the full record here, whatever the outcome")
    ap.add_argument("--wav", default=None, metavar="PATH",
                    help="also write the qualifying clip. Only on exit 0: a clip "
                         "from a rig that did not qualify is not evidence and this "
                         "tool will not put one on disk")
    a = ap.parse_args(argv)

    import rig_qualification as rq
    dev, qual, rec = None, None, None
    try:
        dev = build()
        qual = dev.qualification
    except rp.Refused as why:
        print(f"REFUSED  {why}")
        rec, code = report(refusal=str(why)), REFUSED
    except rq.RigRefusal as why:
        # The rig refused to be built. Its record is attached to the exception,
        # which is the whole reason `RigRefusal` carries one.
        qual = why.qualification
        print(f"the rig REFUSED to be built: {why}\n")
        if qual is not None:
            print(qual.table())
        rec = report(qual=qual, refusal=None if qual is not None else str(why))
        code = FAIL if (qual is not None and qual.verdict == rq.FAIL) else REFUSED
    else:
        print(qual.table())
        rec = report(dev, qual)
        code = OK if qual.qualified else (
            FAIL if qual.verdict == rq.FAIL else REFUSED)

    if code == OK and dev is not None:
        print("\nthe clip that qualified:")
        y = dev.render_note(dev.note)
        print(f"  {len(y)} frames  peak {float(np.abs(y).max()):.6f}  "
              f"note {dev.note}  block {dev.block}  sr {dev.sr}")
        if a.wav:
            rp.write_clip(pathlib.Path(a.wav), y, dev.sr)
            print(f"  wrote {a.wav}")
        print("\nNEXT: record the verdict in tools/refprofile.py "
              "RIG_VERDICTS['modeld-pedalboard'] (qualified=True, with this run's "
              "environment tuple), fill in the readback column of "
              "ModelDPedalboardRig.PINS from `pinned_readback` above, update "
              "docs/pedalboard-rig.md, and only then extend to the 8 Mono anchors.")
    elif a.wav:
        print("\nNOT writing --wav: the rig did not qualify, so its audio is not "
              "evidence.")

    # The #122 note belongs ONLY to a run that measured the plugin and found it
    # uncorrectable. A host with no pedalboard has measured nothing, and saying
    # otherwise would be the tool answering a question it could not ask.
    if code != OK:
        print("\n" + (REOPEN_122 if qual is not None else NO_APPARATUS))

    if a.json:
        p = pathlib.Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, indent=1, sort_keys=False) + "\n", encoding="utf-8")
        print(f"\nwrote {p}")
    return code


if __name__ == "__main__":
    sys.exit(main())
