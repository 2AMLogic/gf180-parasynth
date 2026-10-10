#!/usr/bin/env python3
"""Run the #163 preparation contract against the CURRENT state of every real
drum pair, before trusting it as a gate.

`docs/failure-modes.md`, "gates that cannot be satisfied": an unsatisfiable
gate is worse than none. So this renders each of the sixteen voices with the
fixed model, loads its Fischer reference exactly as `run_case.run_drum_case`
does, prepares both, and reports what `preparation_contract.check_prepared_pair`
says about the pair -- ACCEPTED with each side's measured lead, or REFUSED with
the reason. It changes nothing and writes nothing.

Usage:
    GF180_TR808_REFS=/path/to/sounds-tr808-fischer \
        python3 tools/probes/preparation_contract_current_state.py

Exit status: 0 only if EVERY mapped pair reached the contract and was accepted;
1 if any pair is refused by the CONTRACT; 2 if the apparatus could not run
(corpus missing) or any pair never reached the contract because loading,
rendering or `prepare()` refused (REFUSED / NO-VERDICT -- a skipped pair is not
an accepted one, even when that refusal predates #163). The tally of accepted,
contract-refused and unavailable pairs is always printed.
"""
from __future__ import annotations

import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "model"))

import preparation_contract as pc                                   # noqa: E402
import run_case as rc                                               # noqa: E402


def run(refdir: pathlib.Path) -> tuple[int, int, int, int]:
    """Return (accepted, contract_refused, unavailable, expected)."""
    accepted = contract_refused = unavailable = 0
    voices = sorted(rc.REF_MAIN)
    for voice in voices:
        try:
            ref_x, ref_sr, rel, _setting = rc.load_reference(voice, refdir)
            ours_x, ours_sr = rc.render_drum_solo(voice)
            ref_y = rc.prepare(ref_x, ref_sr, side=f"the reference recording {rel}")
            ours_y = rc.prepare(ours_x, ours_sr, side=f"our {voice} render")
        except rc.Refused as e:
            unavailable += 1
            print(f"{voice:3s} UNAVAILABLE (never reached the contract): {e}")
            continue
        try:
            st = pc.check_prepared_pair(pc.Side("reference", ref_y, ref_sr),
                                        pc.Side("ours", ours_y, ours_sr))
        except pc.Refused as e:
            contract_refused += 1
            print(f"{voice:3s} CONTRACT-REFUSED: {e}")
            continue
        accepted += 1
        r, o = st["reference"], st["ours"]
        print(f"{voice:3s} ACCEPTED  reference {rel} {ref_sr} Hz lead {r['lead_samples']}"
              f"/{r['lead_samples_required']}  ours {ours_sr} Hz lead {o['lead_samples']}"
              f"/{o['lead_samples_required']}")
    return accepted, contract_refused, unavailable, len(voices)


def main() -> int:
    import os
    refdir = pathlib.Path(os.environ.get(rc.REFS_ENV) or rc.REFS_DEFAULT)
    if not refdir.exists():
        print(f"REFUSED: reference corpus not at {refdir}; set {rc.REFS_ENV}")
        return 2
    accepted, refused, unavailable, expected = run(refdir)
    print(f"accepted {accepted} of {expected}; contract-refused {refused}; "
          f"unavailable {unavailable}")
    if refused:
        return 1
    if unavailable or accepted != expected:
        print("REFUSED (NO-VERDICT): not every expected pair reached the contract")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
