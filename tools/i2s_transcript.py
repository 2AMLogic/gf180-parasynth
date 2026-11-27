#!/usr/bin/env python3
"""The one precondition contract for an SPI-to-I2S transcript.

`rtl-sketch/verify_synth_top.py` drives the chip over the SPI pins and decodes
BCLK, LRCLK and SDATA the way a DAC does. Three scorers turn a run's captured
output into `integrated-rtl` board records -- `score_m5a_i2s`,
`score_drum_i2s` and `score_ensemble_i2s` -- and each used to carry its own
hand-copied answer to one question: is this a clean, un-injected PASS from a
known simulator, binding this WAV? The copies drifted (#1556). One accepted a
transcript that said `FAIL --` and then `PASS --`, another accepted a bare
`REFUSED`, and no two of them agreed. This module is now the only place that
question is asked. A scorer keeps the questions that are its own -- is this MY
case, part, voice and configuration -- and asks them beside the gate, never
instead of it.

WHAT IS REFUSED, in the order `validate_clean_pass` asks it:

  * `INJECT_BUG_` anywhere. A mutation run is a control whatever verdict it
    reached. `docs/scorecard/drum-d02a-i2s/controls-run_all.log` is three
    injected runs, and `INJECT_BUG_DRUM_ENV_FLOOR` ended in `PASS --` with the
    SD drum path "verified from SPI pins"; only its compile define separates it
    from evidence. It is also `refuse_mutation` on its own, because a scorer
    asks it before it asks anything else, its own questions included.
  * `REFUSED`, `FAIL --` or `NO-VERDICT` anywhere. The bench states a defect it
    can count with `FAIL --` and still ends the run in `PASS --` when the wire
    matches (#1376), so "it says PASS" has never meant "it says only PASS".
  * no `PASS --` at all.
  * a decoded-WAV digest that is not the offered WAV's, when one is offered.
  * no `simulator backend verilator` or `simulator backend iverilog` line: an
    apparatus nobody named.

WHAT IT CANNOT SEE, stated rather than left to be found (verification rule 8).
It reads text. A `PASS --` printed by any tool satisfies it, so a file holding
two captures -- one failing, one passing -- is caught through the failing
run's tokens and through nothing else. What keeps a passing run of some OTHER
stimulus from scoring is what the scorers add: their own case's
`... verified from SPI pins ...` line, which the bench prints only on its
exit-0 path, and the digest of the very WAV they were offered.
"""
from __future__ import annotations

FORBIDDEN = ("REFUSED", "FAIL --", "NO-VERDICT")
SIMULATORS = ("verilator", "iverilog")


def refuse_mutation(text: str, *, refuse=ValueError, prefix: str = "") -> None:
    """Refuse `text` if it names an `INJECT_BUG_` define, whatever else it says.

    `validate_clean_pass` asks this first. A scorer that recognises its own run
    before it asks the rest of the gate asks this on its own, first, so a
    control report is never refused as merely some other run."""
    if "INJECT_BUG_" in text:
        raise refuse(f"{prefix}mutation runs cannot provide sound evidence")


def validate_clean_pass(text: str, *, wav_sha256: str | None = None,
                        refuse=ValueError, prefix: str = "") -> str:
    """Refuse `text` unless it is a clean, un-injected PASS from a known
    simulator that binds `wav_sha256`; return the simulator's name.

    `refuse` is the exception class to raise -- a scorer's own `Refused` --
    and `prefix` goes in front of every reason, so a scorer reading several
    transcripts says which one it refused.
    """
    refuse_mutation(text, refuse=refuse, prefix=prefix)
    offending = next((token for token in FORBIDDEN if token in text), None)
    if offending is not None:
        raise refuse(f"{prefix}the report is not a clean PASS of the whole-chip comparison: "
                     f"it contains {offending}")
    if "PASS --" not in text:
        raise refuse(f"{prefix}the report is not a clean PASS of the whole-chip comparison: "
                     "no PASS -- line")
    if wav_sha256 is not None and f"decoded I2S WAV sha256 {wav_sha256}" not in text:
        raise refuse(f"{prefix}the report does not bind this decoded I2S WAV")
    simulator = next((name for name in SIMULATORS if f"simulator backend {name}" in text), None)
    if simulator is None:
        raise refuse(f"{prefix}the report omits the simulator backend")
    return simulator
