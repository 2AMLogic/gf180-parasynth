#!/usr/bin/env python3
r"""area_provenance.py -- was this run's die area an INPUT, or its own
utilisation target restated?

This exists because of one shipped bug, reinstated below as a permanent
injection (`docs/verification-rules.md` rule 5, issue #245).
`docs/pnr-synth-top.md` section 2, verbatim:

    A previous run reported "cell area x 2.00 = die area" as a finding; its
    `par_request.json` said `{"method": "utilization", "utilization_pct": 50}`,
    so dividing the cell area by 0.50 only recovered the input. **If you set a
    target utilisation, the die area you get back is your assumption.**

The fix at the time was a CONVENTION: `CORE_UTILIZATION` is not set in
`pnr/orfs/synth_top/config.mk`, `DIE_AREA`/`CORE_AREA` are fixed rectangles, and
the utilisation is the measured quantity. Nothing enforced it, and
`pnr/orfs/summarize.py` printed `die / synth cell area` unconditionally -- the
same confident ratio it would print from a run whose die came from a target.

    WHAT ENFORCING IT IMMEDIATELY FOUND

Two of this repository's three ORFS designs set the target that `synth_top`'s
config warns against, and `summarize.py` would quote a die/cell ratio for both
without a word:

    pnr/orfs/ladder_dp/config.mk:28   export CORE_UTILIZATION  = 50
    pnr/orfs/synth_core/config.mk:31  export CORE_UTILIZATION  = 50

Those two runs' die and core areas are `cell area / 0.50` with margins, so a
`die / synth cell area` of about 2 would have been the input read back. The
convention held only where somebody had written a paragraph about it. This is
the whole argument for a check over a comment.

    THREE OUTCOMES, AND THE THIRD IS THE POINT

    exit 0  OK       -- the die area is an input; the utilisation is measured
    exit 1  the `--expect` was not met (a control that did not fire)
    exit 2  REFUSED  -- no ratio, and none will be given:
                        `circular-die-area`  a utilisation target was set, so
                                             die/cell only recovers 1/target
                        `no-die-input`       neither a fixed die nor a target:
                                             the die's origin is not recorded
                        `no-provenance`      no config/request artefact at all,
                                             so the question cannot be answered

REFUSED is not a pass and not a fail. A tool that answers "1.87" when it cannot
know what 1.87 is made of is worse than one that is absent, because its output
looks exactly like data.

    THE ARITHMETIC IS CORROBORATION, NOT THE TRIGGER

The refusal is triggered by the ARTEFACT -- a utilisation target exists -- and
never by the numbers agreeing. A run whose target is set but whose areas do not
match it is refused too, and the mismatch is printed: the target was set, so the
die is not attributable either way. Making the refusal conditional on
`die/cell == 1/target` would be a gate that goes quiet exactly when the
provenance is most confused.

    USE

    pnr/orfs/area_provenance.py --work <orfs work dir> --design synth_top
    pnr/orfs/area_provenance.py --design ladder_dp            # the live config
    pnr/orfs/area_provenance.py --inject UTILIZATION_TARGET --expect refused-circular

The `--inject` form is the control: it stages a COPY of an archived ORFS run
(`pnr/orfs/evidence/synth_top/placeholder-2a88c35`, a genuinely fixed-die run
that summarises cleanly), writes the shipped artefact into it, runs the REAL
`summarize.py` against it as a subprocess, and requires that the tool exits 2
and that no line of its output ASSIGNS a value to `die / synth cell area`. It also
requires `summarize.py` to name the injected artefact as the provenance it read,
so a refusal for some unrelated reason cannot be mistaken for the control
firing -- condition 3 of the three-condition rule.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent

OK_EXIT, EXPECTATION_UNMET, REFUSED_EXIT = 0, 1, 2

#: The archived run the control stages. A fixed-die run, so the clean baseline
#: passes -- a control whose clean case refuses proves nothing (rule 5, cond. 1).
CONTROL_EVIDENCE = "pnr/orfs/evidence/synth_top/placeholder-2a88c35"

#: Verbatim from docs/pnr-synth-top.md section 2: the request that shipped.
SHIPPED_PAR_REQUEST = {"method": "utilization", "utilization_pct": 50}


@dataclasses.dataclass(frozen=True)
class Provenance:
    """Where the die area in a report came from, and whether it may be quoted."""

    state: str                     # "OK" | "REFUSED"
    reason: str                     # fixed-die | circular-die-area | …
    source: str                     # the artefact this was decided from
    detail: str
    utilisation_target: float | None = None
    corroboration: str | None = None

    @property
    def quotable(self) -> bool:
        return self.state == "OK"


# --------------------------------------------------------------- pure parsing

def parse_config_mk(text: str) -> dict[str, str]:
    r"""The `export NAME = value` assignments of an ORFS design config.

    COMMENTS ARE NOT ASSIGNMENTS, and that distinction is the whole reason this
    is a function with a test rather than a grep: `synth_top/config.mk` says
    "CORE_UTILIZATION is deliberately NOT set" in a comment, and a grep for the
    name finds it there. GNU make treats an unescaped `#` as a comment anywhere
    on the line (see `docs/pnr-first-run.md` 1.1 finding 6 for what that costs),
    so the value is truncated at the first `#`.
    """
    out: dict[str, str] = {}
    for line in text.splitlines():
        body = line.split("#", 1)[0].strip()
        if not body:
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*[:?+]?=\s*(.*)",
                             body)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def parse_par_request(text: str) -> dict:
    """A klt-style place-and-route request. Malformed JSON is not "no target"."""
    try:
        request = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"par_request.json is not JSON ({exc}); its die-area "
                         f"method cannot be read") from exc
    if not isinstance(request, dict):
        raise ValueError("par_request.json is not an object")
    return request


def target_from_config_mk(variables: dict[str, str]) -> float | None:
    """A floorplan utilisation target, as a fraction, or None."""
    raw = variables.get("CORE_UTILIZATION")
    if raw is None or raw == "":
        return None
    try:
        return float(raw) / 100.0
    except ValueError:
        return None


def target_from_par_request(request: dict) -> float | None:
    if str(request.get("method", "")).lower() != "utilization":
        return None
    for key in ("utilization_pct", "utilisation_pct", "utilization", "target"):
        if key in request:
            value = float(request[key])
            return value / 100.0 if value > 1 else value
    return None


def fixed_die(variables: dict[str, str]) -> bool:
    """True when the die AND core are explicit rectangles in the config."""
    return bool(variables.get("DIE_AREA")) and bool(variables.get("CORE_AREA"))


def corroborate(target: float, synth_cell_area: float | None,
                core_area: float | None, die_area: float | None) -> str:
    """What the numbers say about a run whose target was set. Never a verdict."""
    if not (synth_cell_area and core_area):
        return ("no synth cell area or core area in the metrics, so the "
                "arithmetic cannot be shown either way")
    implied = synth_cell_area / target
    ratio = core_area / synth_cell_area
    consistent = abs(implied - core_area) / core_area < 0.02
    lead = (f"core / synth cell area = {ratio:.2f} against 1 / {target:.2f} = "
            f"{1 / target:.2f}")
    if consistent:
        return (f"{lead}: the ratio IS the target read back. cell area "
                f"{synth_cell_area:,.0f} / {target:.2f} = {implied:,.0f} um2, "
                f"core area {core_area:,.0f} um2"
                + (f", die area {die_area:,.0f} um2" if die_area else ""))
    return (f"{lead}, which do NOT agree (cell area / target = {implied:,.0f} "
            f"um2 against a reported core area of {core_area:,.0f} um2). The "
            f"target was still set, so the die is not attributable either way: "
            f"refused, not excused")


def classify(artefacts: list[tuple[str, str, dict]]) -> Provenance:
    """Decide from the artefacts, most run-local first.

    `artefacts` is `[(kind, source, payload)]` where kind is "par_request" or
    "config_mk" and payload is the parsed content. An empty list is
    `no-provenance`: the question was not answered, which is not the same as
    answering "fine".
    """
    if not artefacts:
        return Provenance(
            "REFUSED", "no-provenance", "(none)",
            "no par_request.json and no config.mk could be found for this run, "
            "so nothing records whether the die area was an input or a target. "
            "A ratio computed from it would be uninterpretable.")
    kind, source, payload = artefacts[0]
    if kind == "par_request":
        target = target_from_par_request(payload)
        if target is not None:
            return Provenance(
                "REFUSED", "circular-die-area", source,
                f'the request sets method "utilization" at '
                f"{target * 100:.0f} %, so the die and core areas are the cell "
                f"area divided by {target:.2f}. Dividing them back by the cell "
                f"area only recovers {1 / target:.2f}: that is the input, not a "
                f"measurement.",
                utilisation_target=target)
        return Provenance(
            "OK", "fixed-die", source,
            f'the request\'s method is {payload.get("method")!r}, not '
            f'"utilization", so the die area is not derived from a target.')
    variables = payload
    target = target_from_config_mk(variables)
    if target is not None:
        return Provenance(
            "REFUSED", "circular-die-area", source,
            f"CORE_UTILIZATION = {target * 100:.0f} is set, so ORFS sized the "
            f"core from the cell area and that target. die / synth cell area "
            f"would be about {1 / target:.2f} whatever the design is -- the "
            f"assumption wearing the clothes of a measurement that this "
            f"config's own header warns about.",
            utilisation_target=target)
    if fixed_die(variables):
        return Provenance(
            "OK", "fixed-die", source,
            f"DIE_AREA = {variables['DIE_AREA']} and CORE_AREA = "
            f"{variables['CORE_AREA']} are fixed inputs and no CORE_UTILIZATION "
            f"is set, so the utilisation is the measured quantity.")
    return Provenance(
        "REFUSED", "no-die-input", source,
        "neither a fixed DIE_AREA/CORE_AREA pair nor a CORE_UTILIZATION target "
        "is set, so this config does not record where the die area came from.")


# ------------------------------------------------------------ artefact lookup

def find_artefacts(work: pathlib.Path | None, design: str, variant: str,
                   design_dir: pathlib.Path | None = None
                   ) -> list[tuple[str, str, dict]]:
    """Run-local artefacts first, then the config `run-orfs.sh` feeds ORFS.

    The fall back to `pnr/orfs/<design>/config.mk` is correct for a LIVE run
    (that file is the one ORFS was handed) and is labelled as not run-local,
    because for an archived run it is only the config as it stands today.
    """
    found: list[tuple[str, str, dict]] = []

    def rel(path: pathlib.Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    if work is not None:
        run = pathlib.Path(work) / "logs" / "gf180" / design / variant
        request = run / "par_request.json"
        if request.is_file():
            found.append(("par_request", rel(request),
                          parse_par_request(request.read_text())))
        config = run / "config.mk"
        if config.is_file():
            found.append(("config_mk", rel(config),
                          parse_config_mk(config.read_text())))
    fallback = (design_dir or (HERE / design)) / "config.mk"
    if fallback.is_file():
        found.append(("config_mk", rel(fallback) + " (the config run-orfs.sh "
                      "feeds ORFS; not archived with this run)",
                      parse_config_mk(fallback.read_text())))
    return found


def die_area_provenance(work: pathlib.Path | None, design: str, variant: str,
                        synth_cell_area: float | None = None,
                        core_area: float | None = None,
                        die_area: float | None = None) -> Provenance:
    """The one entry point `summarize.py` calls before quoting a die area."""
    try:
        artefacts = find_artefacts(work, design, variant)
    except ValueError as exc:
        return Provenance("REFUSED", "no-provenance", "(unreadable)", str(exc))
    provenance = classify(artefacts)
    if provenance.utilisation_target is not None:
        provenance = dataclasses.replace(
            provenance,
            corroboration=corroborate(provenance.utilisation_target,
                                      synth_cell_area, core_area, die_area))
    return provenance


# ------------------------------------------------------------------- controls

@dataclasses.dataclass(frozen=True)
class Injection:
    shipped_as: str
    artefact: str
    write: str | None          # content to write, or None to delete


INJECTIONS = {
    "UTILIZATION_TARGET": Injection(
        shipped_as=(
            "the exact par_request.json behind docs/pnr-synth-top.md section 2 -- "
            'a run that reported "cell area x 2.00 = die area" as a finding when '
            "its request had asked for 50 % utilisation."),
        artefact="par_request.json",
        write=json.dumps(SHIPPED_PAR_REQUEST, indent=2) + "\n"),
    "CORE_UTILIZATION_SET": Injection(
        shipped_as=(
            "the ORFS spelling of the same bug, which pnr/orfs/ladder_dp and "
            "pnr/orfs/synth_core ACTUALLY SHIP: export CORE_UTILIZATION = 50, "
            "the setting synth_top/config.mk's header warns against."),
        artefact="config.mk",
        write=None),                       # appended, see stage()
}

#: The claim being withheld, as it appears when it is MADE -- a value assigned,
#: not the phrase mentioned. The first version of this control looked for the
#: bare phrase and reported NOT MET for `CORE_UTILIZATION_SET`, because the
#: refusal's own explanation says what the ratio "would be": a detector that
#: fires on prose about the defect rather than the defect.
RATIO_CLAIM = re.compile(r"die / synth cell area = [0-9]")
RATIO_TEXT = "die / synth cell area = <value>"


class ControlRefused(Exception):
    """Nothing was measured. Distinct from both a pass and a failure."""


def stage(dest: pathlib.Path, injection: Injection | None) -> pathlib.Path:
    """A COPY of the archived run in an ORFS-shaped work tree, never the live one.

    `make controls` runs its jobs in parallel; a control that edited the
    evidence in place could turn a concurrent job red.
    """
    evidence = ROOT / CONTROL_EVIDENCE
    if not evidence.is_dir():
        raise ControlRefused(f"{CONTROL_EVIDENCE} is absent: there is no "
                             f"archived run to stage, so nothing was measured")
    logs = dest / "logs/gf180/synth_top/base"
    reports = dest / "reports/gf180/synth_top/base"
    logs.mkdir(parents=True)
    reports.mkdir(parents=True)
    for path in sorted(evidence.iterdir()):
        if path.suffix == ".json" or path.name == "sta_corners.log":
            shutil.copyfile(path, logs / path.name)
        elif path.name == "config.mk":
            shutil.copyfile(path, logs / path.name)
        elif path.name == "synth_stat.txt":
            shutil.copyfile(path, reports / path.name)
    if not (logs / "6_report.json").is_file():
        raise ControlRefused("the staged run has no 6_report.json, so "
                             "summarize.py would have nothing to summarise")
    if not (logs / "config.mk").is_file():
        raise ControlRefused("the archived run has no config.mk, so the clean "
                             "case would refuse for lack of provenance and the "
                             "control could not discriminate")
    if injection is not None:
        target = logs / injection.artefact
        if injection.artefact == "config.mk" and injection.write is None:
            target.write_text(target.read_text()
                              + "\n# injected by area_provenance.py\n"
                                "export CORE_UTILIZATION  = 50\n")
        else:
            target.write_text(injection.write or "")
        if not target.is_file() or not target.read_text().strip():
            raise ControlRefused(f"the injection wrote nothing to {target}")
    return dest


def run_summarize(work: pathlib.Path) -> tuple[int, str]:
    done = subprocess.run(
        [sys.executable, str(HERE / "summarize.py"), "synth_top", "base",
         "--work", str(work)],
        capture_output=True, text=True)
    return done.returncode, done.stdout + done.stderr


def run_control(name: str | None, keep: pathlib.Path | None = None) -> dict:
    """Stage, inject, run the REAL summarize.py, and read what it did.

    Raises `ControlRefused` when nothing was measured: the staged tree was not
    what this control needs, or the tool did not name the artefact the
    injection wrote -- in which case a refusal in its output is not evidence
    that THIS defect was caught.
    """
    injection = INJECTIONS[name] if name else None
    with tempfile.TemporaryDirectory(prefix="area-provenance-") as tmp:
        work = stage(pathlib.Path(tmp) / "work", injection)
        code, output = run_summarize(work)
        if keep:
            keep.mkdir(parents=True, exist_ok=True)
            (keep / f"summarize-{name or 'clean'}.log").write_text(output)
        if "### synth_top (base)" not in output:
            raise ControlRefused(
                f"summarize.py printed no report header, so it did not run "
                f"against the staged tree (exit {code}): {output[-600:]!r}")
        if injection is not None and injection.artefact not in output:
            raise ControlRefused(
                f"summarize.py never named {injection.artefact}, so the guard "
                f"did not read the artefact this control wrote. A refusal here "
                f"would be some other refusal (exit {code}): {output[-600:]!r}")
    quoted = bool(RATIO_CLAIM.search(output))
    refused = "REFUSED" in output
    return {"injection": name, "exit": code, "quoted_ratio": quoted,
            "printed_refusal": refused, "output": output,
            "state": "REFUSED" if code == REFUSED_EXIT else
                     "OK" if code == 0 else "ERROR"}


def check_expectation(result: dict, expect: str) -> tuple[int, str]:
    """0 met / 1 not met / 2 nothing measured."""
    if expect == "ok":
        if result["state"] == "OK" and result["quoted_ratio"]:
            return OK_EXIT, "met: an area ratio was reported for a fixed-die run"
        if result["state"] == "REFUSED":
            return EXPECTATION_UNMET, ("NOT MET: the clean run REFUSED, so this "
                                       "control cannot discriminate")
        return REFUSED_EXIT, f"NO VERDICT: summarize.py exited {result['exit']}"
    if expect == "refused-circular":
        if result["state"] != "REFUSED":
            return EXPECTATION_UNMET, ("NOT MET: summarize.py exited "
                                       f"{result['exit']} and did not refuse")
        if result["quoted_ratio"]:
            return EXPECTATION_UNMET, (f"NOT MET: summarize.py refused but still "
                                       f"printed {RATIO_TEXT!r}")
        if not result["printed_refusal"]:
            return REFUSED_EXIT, ("NO VERDICT: exit 2 with no REFUSED in the "
                                  "output -- that is not this refusal")
        return OK_EXIT, "met: REFUSED, and no ratio was printed"
    return REFUSED_EXIT, f"NO VERDICT: unknown expectation {expect!r}"


# ----------------------------------------------------------------------- main

def print_provenance(provenance: Provenance) -> None:
    print(f"area_provenance: {provenance.state} ({provenance.reason})")
    print(f"  source: {provenance.source}")
    print(f"  {provenance.detail}")
    if provenance.corroboration:
        print(f"  arithmetic: {provenance.corroboration}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=pathlib.Path, help="an ORFS work directory")
    ap.add_argument("--design", default="synth_top")
    ap.add_argument("--variant", default="base")
    ap.add_argument("--inject", choices=sorted(INJECTIONS))
    ap.add_argument("--expect", choices=["ok", "refused-circular"])
    ap.add_argument("--outdir", type=pathlib.Path,
                    help="keep the control's summarize.py output here")
    args = ap.parse_args(argv)

    if args.inject or args.expect:
        try:
            result = run_control(args.inject, args.outdir)
        except ControlRefused as exc:
            print(f"area_provenance: REFUSED -- {exc}")
            print("  nothing was measured: this is not a pass and not a failure")
            return REFUSED_EXIT
        name = args.inject or "clean"
        print(f"area_provenance control: {name} -- summarize.py exited "
              f"{result['exit']}, ratio {'printed' if result['quoted_ratio'] else 'withheld'}")
        if args.inject:
            print(f"  shipped as: {INJECTIONS[args.inject].shipped_as}")
        for line in result["output"].splitlines():
            if "REFUSED" in line or RATIO_CLAIM.search(line) or "provenance" in line:
                print(f"  | {line.strip()}")
        if not args.expect:
            return OK_EXIT if result["state"] == "OK" else REFUSED_EXIT
        code, why = check_expectation(result, args.expect)
        print(f"  --expect {args.expect}: {why}")
        return code

    provenance = die_area_provenance(args.work, args.design, args.variant)
    print_provenance(provenance)
    return OK_EXIT if provenance.quotable else REFUSED_EXIT


if __name__ == "__main__":
    sys.exit(main())
