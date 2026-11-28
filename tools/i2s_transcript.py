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
  * more than one of a record the bench prints once per run: its backend
    declaration, its final PASS verdict, its decoded-WAV digest (#1562). Two
    clean runs cleared every check above, and the scorer then read the FIRST
    run's controls against the second run's WAV. Each record is counted as a
    whole line (including inside a JSON job report's strings), so marker text
    embedded in a printed path does not look like a second run, a second run
    that prints no backend line is refused on its two verdicts and two
    digests, and two runs that agree are still two runs.
  * a decoded-WAV digest that is not the offered WAV's, when one is offered.
  * no `simulator backend verilator` or `simulator backend iverilog` line: an
    apparatus nobody named.

WHAT IT CANNOT SEE, stated rather than left to be found (verification rule 8).
It reads text. A `PASS --` printed by any tool satisfies it, and a file edited
to hold one of each record from different runs is not told from a captured
one. The record count sees a second run only once it has printed its backend
declaration. The bench prints that directly after the stimulus header the
scorers read, so a run that stopped between the two -- a stretch that only
formats -- leaves a header nothing here counts. What keeps a complete passing
run of some OTHER stimulus from scoring is what the scorers add: their own
case's `... verified from SPI pins ...` line, which the bench prints only on
its exit-0 path, and the digest of the very WAV they were offered.
"""
from __future__ import annotations

import json
import re

FORBIDDEN = ("REFUSED", "FAIL --", "NO-VERDICT")
SIMULATORS = ("verilator", "iverilog")

#: The records one run of the bench prints once each, each written as the
#: statement in `rtl-sketch/verify_synth_top.py` that prints it begins:
#: `simulator backend {a.simulator}`, `PASS -- {nper} I2S periods decoded from
#: the wire ...` and `decoded I2S WAV sha256 {wav_sha256}`.
ONE_RUN_RECORDS = (
    ("simulator backend declaration", "verify_synth_top: simulator backend"),
    ("PASS verdict", "verify_synth_top: PASS --"),
    ("decoded I2S WAV digest", "verify_synth_top: decoded I2S WAV sha256"),
)

_RECORD_SUFFIX = {
    "simulator backend declaration": r" \S+[ \t]*",
    "PASS verdict": r" \S.*",
    "decoded I2S WAV digest": r" [0-9a-f]+[ \t]*",
}

_RECORD_PATTERNS = tuple(
    (name, re.compile(r"[ \t]*" + re.escape(prefix) + _RECORD_SUFFIX[name]))
    for name, prefix in ONE_RUN_RECORDS
)


def _record_lines(text: str):
    """Lines of `text`, or of every string inside a JSON job report."""
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            stack = [parsed]
            while stack:
                item = stack.pop()
                if isinstance(item, str):
                    yield from item.splitlines()
                elif isinstance(item, list):
                    stack.extend(item)
                elif isinstance(item, dict):
                    stack.extend(item.values())
            return
    yield from text.splitlines()


def refuse_mutation(text: str, *, refuse=ValueError, prefix: str = "") -> None:
    """Refuse `text` if it names an `INJECT_BUG_` define, whatever else it says.

    `validate_clean_pass` asks this first. A scorer that recognises its own run
    before it asks the rest of the gate asks this on its own, first, so a
    control report is never refused as merely some other run."""
    if "INJECT_BUG_" in text:
        raise refuse(f"{prefix}mutation runs cannot provide sound evidence")


def record_counts(text: str) -> dict[str, int]:
    """How many whole once-per-run records `text` holds, by record name."""
    counts = {name: 0 for name, _ in ONE_RUN_RECORDS}
    for line in _record_lines(text):
        for name, pattern in _RECORD_PATTERNS:
            if pattern.fullmatch(line):
                counts[name] += 1
                break
    return counts


def validate_clean_pass(text: str, *, wav_sha256: str | None = None,
                        refuse=ValueError, prefix: str = "") -> str:
    """Refuse `text` unless it is ONE clean, un-injected PASS from a known
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
    repeated = {name: count for name, count in record_counts(text).items() if count > 1}
    if repeated:
        raise refuse(f"{prefix}ambiguous transcript: it holds "
                     f"{', '.join(f'{count} {name}s' for name, count in repeated.items())}, "
                     "and one run prints one of each")
    if wav_sha256 is not None and f"decoded I2S WAV sha256 {wav_sha256}" not in text:
        raise refuse(f"{prefix}the report does not bind this decoded I2S WAV")
    simulator = next((name for name in SIMULATORS if f"simulator backend {name}" in text), None)
    if simulator is None:
        raise refuse(f"{prefix}the report omits the simulator backend")
    return simulator
