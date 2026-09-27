#!/usr/bin/env python3
"""What model/drums_fx.py's constants rest on, as a query (#114).

    python model/drums_provenance.py                  every constant by status
    python model/drums_provenance.py --status fitted   one status, with detail
    python model/drums_provenance.py --no-holdout      fits with nothing held out
    python model/drums_provenance.py --json            the same as data
    python model/drums_provenance.py --registers       the register-write stream

The listing is the point: "which of these is inferred?" and "which fit was
never checked out of sample?" are the two questions the prose comments could
only answer by grep plus judgement.

`--registers` is this migration's own control. The registry is a change of
medium, so no register write may move; the stream printed here is every write
the host sends -- `kit_808()`, the frozen revision-11 kit, all sixteen
`preset_writes` and a full PATTERN_808 pass including the BD attack window and
the toms' pitch-drop sequences at four accents -- ending in its sha256. Compare
two trees with:

    python model/drums_provenance.py --registers > /tmp/after.txt
    # same command run from a checkout of the baseline commit, with this file
    # copied in, then: diff /tmp/before.txt /tmp/after.txt

A diff of zero lines is the evidence; the sha256 is a shorthand for it, not a
substitute, because a hash tells you nothing about WHAT moved when it moves.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import drums_fx as dx


def register_stream() -> list[str]:
    """Every register write the reference host sends, as `addr value` text."""
    out = []

    def emit(section: str, writes, framed: bool = False):
        out.append(f"# {section}")
        for item in writes:
            if framed:
                f, a, v = item
                out.append(f"{f} {a:#04x} {v}")
            else:
                a, v = item
                out.append(f"{a:#04x} {v}")

    kit = dx.kit_808()
    emit("kit_808", kit)
    emit("kit_808_rev11", dx.kit_808_rev11())
    for name in dx.SOUND_NAMES:
        emit(f"preset_writes {name}", dx.preset_writes(name))
    for name in dx.SOUND_NAMES:
        emit(f"kit_with_sounds {name}", dx.kit_with_sounds(name, kit=kit))
    emit("bd_attack_writes", dx.bd_attack_writes(0), framed=True)
    positions = (("LT", dx.M_LT), ("MT", dx.M_MT), ("HT", dx.M_HT),
                 ("LC", dx.M_LT), ("MC", dx.M_MT), ("HC", dx.M_HT))
    for accent in (0.5, 1.0, 1.4, 2.0):
        for name, mode in positions:
            f0, q, _ = dx.TOM_PRESET[name]
            emit(f"tom_pitch_drop_writes {name} accent {accent}",
                 dx.tom_pitch_drop_writes(0, mode, f0, q, dx.AMP_TOM[name], accent),
                 framed=True)
    hits = dx.pattern_hits(dx.PATTERN_808)
    emit("hit_writes PATTERN_808", dx.hit_writes(hits, kit), framed=True)
    emit("hit_writes PATTERN_808 no coef_seq",
         dx.hit_writes(hits, kit, coef_seq=False), framed=True)
    return out


def _entry_lines(name: str, p: dx.Provenance) -> list[str]:
    lines = [f"  {name}"]
    lines.append(f"      source      {p.source}")
    lines.append(f"      because     {p.justification}")
    if p.prose_tag:
        lines.append(f"      prose tag   {p.prose_tag}")
    if p.status == dx.PROV_MEASURED:
        lines.append(f"      sample      n {p.n}, {p.date}; spread: {p.spread}")
    elif p.spread:
        lines.append(f"      spread      {p.spread}")
    if p.status == dx.PROV_FITTED:
        lines.append(f"      fitted on   {p.fitted_on}")
        mark = "HELD OUT" if p.has_holdout else "NO HOLDOUT"
        lines.append(f"      holdout     [{mark}] {p.holdout}")
    if p.docs:
        lines.append(f"      docs        {', '.join(p.docs)}")
    if p.notes:
        lines.append(f"      notes       {p.notes}")
    return lines


def listing(status: str = None, detail: bool = True) -> list[str]:
    out = []
    by_status = dx.constants_by_status()
    for s in dx.PROV_STATUSES:
        if status and s != status:
            continue
        names = by_status[s]
        out.append(f"{s} ({len(names)})")
        for n in names:
            out += _entry_lines(n, dx.PROVENANCE[n]) if detail else [f"  {n}"]
    if not status:
        no_holdout = dx.fits_without_holdout()
        out.append(f"fits with nothing held out ({len(no_holdout)})")
        out += [f"  {n}" for n in no_holdout]
        unreg = dx.unregistered_constants()
        out.append(f"module constants with NO provenance record ({len(unreg)})")
        out.append("  " + " ".join(unreg))
    return out


def as_json() -> dict:
    return {
        "statuses": list(dx.PROV_STATUSES),
        "by_status": {s: list(n) for s, n in dx.constants_by_status().items()},
        "constants": {n: dataclasses.asdict(p) | {"has_holdout": p.has_holdout}
                      for n, p in dx.PROVENANCE.items()},
        "fits_without_holdout": list(dx.fits_without_holdout()),
        "unregistered": list(dx.unregistered_constants()),
        "prose_tagged": {n: list(t) for n, t in dx.prose_tagged_constants().items()},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # `choices` is read defensively so that --registers still runs against a
    # checkout that predates the registry: a control you cannot run on the
    # baseline measures nothing.
    ap.add_argument("--status", choices=getattr(dx, "PROV_STATUSES", None))
    ap.add_argument("--no-holdout", action="store_true",
                    help="only the fitted constants with nothing held out")
    ap.add_argument("--names-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--registers", action="store_true",
                    help="the register-write stream and its sha256")
    a = ap.parse_args(argv)
    if a.registers:
        text = "\n".join(register_stream()) + "\n"
        sys.stdout.write(text)
        sys.stdout.write(f"# sha256 {hashlib.sha256(text.encode()).hexdigest()}\n")
        return 0
    if a.json:
        print(json.dumps(as_json(), indent=1, sort_keys=True))
        return 0
    if a.no_holdout:
        for n in dx.fits_without_holdout():
            print("\n".join(_entry_lines(n, dx.PROVENANCE[n])))
        return 0
    print("\n".join(listing(a.status, detail=not a.names_only)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
