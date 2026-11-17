#!/usr/bin/env python3
"""characterize_synth_top.py -- one characterization report from one archived
ORFS run's committed evidence, for T1 item 8 (issues #790, #724).

Reads a single run directory under `pnr/orfs/evidence/synth_top/` -- named as
an explicit argument, NEVER globbed (three run dirs live there; only one is the
routed run) -- and writes `docs/characterization-synth-top.md` plus its JSON
twin. It runs no synthesis, no place-and-route and no STA (#33): the committed
evidence is the whole point.

    python3 tools/characterize_synth_top.py \
        pnr/orfs/evidence/synth_top/joined-d1e5068

OUTCOMES (the house REFUSED convention, as `pnr/orfs/summarize.py`):

    exit 0   report written (both files)
    exit 2   REFUSED -- nothing written. A missing evidence file, a missing
             required field, an ambiguous duplicated JSON key, provenance that
             does not attribute the die area, a corner mismatch, or a
             cross-check outside its stated tolerance is a precondition of the
             report, not a row to omit. A tool that answers when it cannot is
             worse than one that is absent, because its output looks exactly
             like data.

STRICTNESS

  * Every figure in the report carries the file AND the metric key or log line
    it was read from. In the JSON twin that is a `source` object on every
    figure; in the markdown it is a Source column.
  * `pnr/orfs/summarize.py`'s `load()` returns `{}` on a missing file and its
    `pick()` returns `None` on a missing key -- exactly the silent-omission
    behaviour this tool forbids. Reuse here is of `classify()` and the parsers
    from `pnr/orfs/area_provenance.py`, wrapped so absence raises.
  * `6_report.json` literally contains duplicate keys (`finish__design__instance__count`
    twice; `finish__design__instance__area` twice with DIFFERENT values).
    Python's `json.load` silently keeps the last. This tool records every
    duplicate, refuses when a REQUIRED key is duplicated with differing values,
    and carries the rest as evidence in the twin.
  * Per-corner timing is read from `sta_corners.log`'s machine-parseable
    `=== CORNER` lines, not re-derived from `SUMMARY.md` prose. Implied min
    period and Fmax are read, then cross-checked against the clock period and
    setup WNS with a stated rounding tolerance; disagreement REFUSES rather
    than silently preferring one number.
  * Slack is parsed through `float()`: a negative WNS is a normal input, not
    an error.

DIE-AREA PROVENANCE IS LOAD-BEARING (issue #245)

  The die / synth-cell-area ratio is only quotable when
  `area_provenance.classify()` attributes the die to a FIXED input.
  `joined-d1e5068`'s archived `config.mk` fixes DIE_AREA/CORE_AREA and
  deliberately does not set CORE_UTILIZATION, so the ratio is quotable here --
  and the classification itself is carried in the report as evidence. A run
  whose config sets `CORE_UTILIZATION` (as `pnr/orfs/ladder_dp` and
  `pnr/orfs/synth_core` do) is REFUSED outright: that ratio would be the
  target read back, which is the shipped bug of #245.

NOT AN ATTESTATION

  Writing this report does not attest T1 item 8. The JSON twin's `status` key
  says the report is COMPLETE -- it is not a `klt signoff` verdict, and
  `manifests/verdict.txt` is not touched by this tool. At pinned klt 0.8.0
  there is no `--attest 8` producer at all, so no evidence envelope for item 8
  can be produced here.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "pnr" / "orfs"))
from area_provenance import classify, parse_config_mk, parse_par_request  # noqa: E402
import refusal

OK_EXIT, REFUSED_EXIT = 0, 2

DEFAULT_OUT_DIR = ROOT / "docs"

#: Evidence the report is built from. All five are committed for joined-d1e5068.
REQUIRED_FILES = (
    "6_report.json",
    "sta_corners.log",
    "synth_stat.txt",
    "blockarea.md",
    "config.mk",
)

#: Required `6_report.json` metric keys, by section. Missing = REFUSED.
REQUIRED_METRICS = {
    "area": (
        "finish__design__instance__count",
        "finish__design__instance__area__stdcell",
        "finish__design__instance__utilization",
        "finish__design__core__area",
        "finish__design__die__area",
        "finish__design__rows",
        "finish__design__sites",
        "finish__design__io",
        "finish__design__nets",
    ),
    "finish_timing": (
        "finish__timing__setup__ws",
        "finish__timing__setup__tns",
        "finish__timing__hold__ws",
        "finish__timing__hold__tns",
        "finish__timing__fmax",
    ),
    "power": (
        "finish__power__total",
        "finish__power__internal__total",
        "finish__power__switching__total",
        "finish__power__leakage__total",
    ),
    "drv": (
        "finish__timing__drv__setup_violation_count",
        "finish__timing__drv__hold_violation_count",
        "finish__timing__drv__max_slew",
        "finish__timing__drv__max_cap",
        "finish__timing__drv__max_fanout",
    ),
}

#: Corner fields the `=== CORNER` line must carry.
REQUIRED_CORNER_FIELDS = (
    "setup_wns_ns", "setup_tns_ns", "hold_wns_ns", "hold_tns_ns",
    "implied_min_period_ns", "implied_fmax_mhz",
)

# Rounding half-ULPs, each derived from the field's own logged precision.
#: `implied_min_period_ns` is logged rounded to 2 dp -- half a ULP is
#: 0.005 ns. This is the one constant for that half-ULP: both
#: MIN_PERIOD_TOLERANCE_NS below and the per-corner Fmax budget
#: (`per_corner_fmax_tolerance_mhz`) derive from it, so the two cannot
#: drift apart.
MIN_PERIOD_HALF_ULP_NS = 0.005
#: `setup_wns_ns` / `hold_wns_ns` are logged rounded to 3 dp -- half a ULP
#: is 0.0005 ns. `clock_period_ns` carries 6 dp and the finish-stage ws
#: floats are full precision, so neither contributes rounding of its own.
WNS_TOLERANCE_NS = 0.0005
#: `clock_period - setup_wns` vs `implied_min_period`: BOTH operands are
#: log-rounded, so rounding alone allows the sum of the two half-ULPs,
#: 0.005 + 0.0005 = 0.0055 ns.
MIN_PERIOD_TOLERANCE_NS = MIN_PERIOD_HALF_ULP_NS + WNS_TOLERANCE_NS
#: `implied_fmax_mhz` is logged rounded to 1 dp -- half a ULP is 0.05 MHz.
#: Flat ONLY where it is the sole rounding source: the finish-stage Fmax
#: check compares against the full-precision `finish__timing__fmax` float.
#: The per-corner reciprocal check adds the propagated min-period term on
#: top -- see `per_corner_fmax_tolerance_mhz` (issue #834).
FMAX_TOLERANCE_MHZ = 0.05
#: utilisation key vs std-cell-area / core-area recomputation, absolute.
UTILIZATION_TOLERANCE = 1e-4

#: AC3: the report states its own limits. Each is a fact about what the
#: evidence holds, not a hedge.
NOT_COVERED = (
    "no LVS",
    "no sign-off DRC",
    "no gate-level simulation",
    "power at one corner only (the finish-stage metrics are tt_025C_5v00; "
    "sta_corners.log carries no power figures)",
    "attestation status is not claimed by this report: klt 0.8.0 (the CI pin) "
    "has no --attest 8 producer, so T1 item 8 remains UNMET regardless of "
    "this report",
)

HEADER_RE = re.compile(
    r"^=== STA_CORNERS design=(\S+) clock_period_ns=(\S+) lib=(\S+) "
    r"(\S+) rcx=(\S+)$")
CORNER_RE = re.compile(r"^=== CORNER (\S+) (.+)$")
CELLS_RE = re.compile(
    r"^\s*(\d+)\s+(\S+)\s+(\d+)\s+(\S+)\s+cells$", re.M)
CHIP_AREA_RE = re.compile(r"Chip area for module '\\synth_top': ([0-9.]+)")
BLOCKAREA_HEAD_RE = re.compile(
    r"^instances (\d+), area ([\d,]+)\.(\d) um2 "
    r"\(excluding fillers: ([\d,]+)\.(\d) um2\)$", re.M)
BLOCKAREA_FLOPS_RE = re.compile(
    r"sequential cells only \(([\d,]+) flops, ([\d,]+)\.(\d) um2\)")


class Refused(refusal.Refused, Exception):
    """A precondition of this report was not met. Nothing is written."""


def _rel(path: pathlib.Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _num(value, file: str, key: str):
    """A required numeric metric. Absent or non-numeric REFUSES."""
    if key not in value:
        raise Refused(f"{file}: required metric key '{key}' is missing")
    v = value[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Refused(f"{file}: metric '{key}' is {type(v).__name__}, "
                      f"not a number ({v!r})")
    return v if isinstance(v, int) else float(v)


def _line_no(lines, pattern, file, what):
    hits = [(i, m) for i, l in enumerate(lines, 1)
            if (m := pattern.search(l))]
    if not hits:
        raise Refused(f"{file}: no {what} line found")
    if len(hits) > 1:
        raise Refused(f"{file}: {len(hits)} {what} lines found, expected 1")
    return hits[0]


# ------------------------------------------------------------------- parsing

def load_6_report(path: pathlib.Path) -> dict:
    """Strict read of 6_report.json, with duplicate keys made visible.

    `json.load` keeps the LAST value of a duplicated key. That default is
    kept, but every duplicate is recorded, and a required key duplicated with
    differing values is an ambiguity this tool will not silently resolve.
    """
    duplicates: dict[str, list] = {}

    def hook(pairs):
        seen: dict[str, list] = {}
        for k, v in pairs:
            seen.setdefault(k, []).append(v)
        for k, vs in seen.items():
            if len(vs) > 1:
                duplicates[k] = vs
        return {k: vs[-1] for k, vs in seen.items()}

    data = json.loads(path.read_text(), object_pairs_hook=hook)
    figures: dict[str, dict] = {}
    for section, keys in REQUIRED_METRICS.items():
        for key in keys:
            figures[key] = {
                "value": _num(data, path.name, key),
                "source": {"file": path.name, "key": key},
            }
    # Duplicates touching required keys must be benign (identical values).
    for key, vs in duplicates.items():
        differing = len({json.dumps(v) for v in vs}) > 1
        if differing and key in figures:
            raise Refused(
                f"{path.name}: required metric '{key}' appears {len(vs)} times "
                f"with differing values {vs} -- the evidence is ambiguous, "
                f"not something to resolve by keeping the last")
    # Differing duplicates among non-required keys are carried as evidence.
    ambiguities = [
        {"key": k, "values": vs, "kept": "last (json.load semantics)"}
        for k, vs in sorted(duplicates.items())
        if len({json.dumps(v) for v in vs}) > 1
    ]
    return {"figures": figures, "ambiguities": ambiguities,
            "duplicate_keys": sorted(duplicates)}


def parse_sta_corners(path: pathlib.Path) -> dict:
    """The `=== STA_CORNERS` header and the per-corner `=== CORNER` lines.

    Corner short names (tt/ss/ff) map onto the header's slash-separated
    full names (tt_025C_5v00/...) by prefix; the mapping must be
    unambiguous and complete in BOTH directions or the run is refused.
    """
    lines = path.read_text().splitlines()
    headers = [(i, m) for i, l in enumerate(lines, 1)
               if (m := HEADER_RE.match(l))]
    if not headers:
        raise Refused(f"{path.name}: no === STA_CORNERS header line found")
    if len(headers) > 1:
        raise Refused(f"{path.name}: {len(headers)} === STA_CORNERS headers, "
                      f"expected 1")
    hline, hmatch = headers[0]
    full_names = hmatch.group(4).split("/")
    shorts = [f.split("_")[0] for f in full_names]
    if len(set(shorts)) != len(shorts):
        raise Refused(f"{path.name}: corner list {full_names} has a prefix "
                      f"collision; short names would be ambiguous")

    corners: dict[str, dict] = {}
    for i, line in enumerate(lines, 1):
        m = CORNER_RE.match(line)
        if not m:
            continue
        name, rest = m.group(1), m.group(2)
        if name in corners:
            raise Refused(f"{path.name}:{i}: corner '{name}' appears twice")
        fields: dict[str, str] = {}
        for token in rest.split():
            k, eq, v = token.partition("=")
            if not eq:
                raise Refused(f"{path.name}:{i}: token '{token}' is not k=v")
            fields[k] = v
        missing = [f for f in REQUIRED_CORNER_FIELDS if f not in fields]
        if missing:
            raise Refused(f"{path.name}:{i}: corner '{name}' is missing "
                          f"{missing}")
        try:
            values = {f: float(fields[f]) for f in REQUIRED_CORNER_FIELDS}
        except ValueError as exc:
            raise Refused(f"{path.name}:{i}: corner '{name}' has a "
                          f"non-numeric field ({exc})") from exc
        if name not in shorts:
            raise Refused(f"{path.name}:{i}: corner '{name}' is not in the "
                          f"header's corner list {full_names}")
        corners[name] = {
            "values": values,
            "source": {"file": path.name, "line": i, "line_text": line},
        }
    for full, short in zip(full_names, shorts):
        if short not in corners:
            raise Refused(f"{path.name}: header corner '{full}' has no "
                          f"=== CORNER line -- refusing rather than emitting "
                          f"a report with a missing row")
    try:
        clock = float(hmatch.group(2))
    except ValueError as exc:
        raise Refused(f"{path.name}:{hline}: clock_period_ns is not numeric "
                      f"({hmatch.group(2)!r})") from exc
    return {
        "design": hmatch.group(1),
        "clock_period_ns": clock,
        "lib": hmatch.group(3),
        "corner_full_names": full_names,
        "rcx": hmatch.group(5),
        "corners": corners,
        "header_source": {"file": path.name, "line": hline,
                          "line_text": lines[hline - 1]},
    }


def parse_synth_stat(path: pathlib.Path) -> dict:
    """The yosys baseline: cell count and chip area from synth_stat.txt."""
    text = path.read_text()
    i, m = _line_no(text.splitlines(), CELLS_RE, path.name, "yosys 'cells'")
    chip = [(j, cm) for j, l in enumerate(text.splitlines(), 1)
            if (cm := CHIP_AREA_RE.search(l))]
    if not chip:
        raise Refused(f"{path.name}: no 'Chip area for module' line found")
    if len(chip) > 1:
        raise Refused(f"{path.name}: {len(chip)} 'Chip area' lines, expected 1")
    cj, cm = chip[0]
    return {
        "cell_count": {
            "value": int(m.group(1)),
            "source": {"file": path.name, "line": i, "line_text": m.group(0).strip()},
        },
        "chip_area_um2": {
            "value": float(cm.group(1)),
            "source": {"file": path.name, "line": cj,
                       "line_text": cm.group(0).strip()},
        },
    }


def parse_blockarea(path: pathlib.Path) -> dict:
    """The headline and sequential-flop figures from blockarea.md."""
    text = path.read_text()
    i, m = _line_no(text.splitlines(), BLOCKAREA_HEAD_RE, path.name,
                    "instances/area headline")
    j, fm = _line_no(text.splitlines(), BLOCKAREA_FLOPS_RE, path.name,
                     "sequential-flops summary")
    return {
        "instances": {
            "value": int(m.group(1)),
            "source": {"file": path.name, "line": i, "line_text": m.group(0)},
        },
        "total_area_um2": {
            "value": float(m.group(2).replace(",", "") + "." + m.group(3)),
            "source": {"file": path.name, "line": i, "line_text": m.group(0)},
        },
        "non_filler_area_um2": {
            "value": float(m.group(4).replace(",", "") + "." + m.group(5)),
            "source": {"file": path.name, "line": i, "line_text": m.group(0)},
        },
        "sequential_flops": {
            "value": int(fm.group(1).replace(",", "")),
            "source": {"file": path.name, "line": j, "line_text": fm.group(0)},
        },
        "sequential_area_um2": {
            "value": float(fm.group(2).replace(",", "") + "." + fm.group(3)),
            "source": {"file": path.name, "line": j, "line_text": fm.group(0)},
        },
    }


def die_provenance(run_dir: pathlib.Path) -> dict:
    """Classify the die area via area_provenance, from run-local artefacts.

    Only artefacts INSIDE the run dir are read -- the fallback to the live
    `pnr/orfs/<design>/config.mk` is correct for a live run and wrong for an
    archived one, so it is not used here.
    """
    artefacts = []
    request = run_dir / "par_request.json"
    if request.is_file():
        artefacts.append(("par_request", _rel(request),
                          parse_par_request(request.read_text())))
    config = run_dir / "config.mk"
    if config.is_file():
        artefacts.append(("config_mk", _rel(config),
                          parse_config_mk(config.read_text())))
    provenance = classify(artefacts)
    if provenance.state != "OK":
        raise Refused(
            f"die-area provenance {provenance.reason} ({provenance.source}): "
            f"{provenance.detail} The die/cell-area ratio would not be "
            f"attributable, so no report is written (issue #245).")
    return {
        "state": provenance.state,
        "reason": provenance.reason,
        "source": provenance.source,
        "detail": provenance.detail,
    }


# --------------------------------------------------------------- cross-checks

def _check(checks, name, a, b, tol, detail, sources):
    diff = abs(a - b)
    if diff > tol + 1e-12:
        raise Refused(
            f"cross-check '{name}' failed: {a} vs {b} differ by {diff} "
            f"beyond the stated tolerance {tol} ({detail}). Refusing rather "
            f"than silently preferring one number.")
    checks.append({"check": name, "values": [a, b], "tolerance": tol,
                   "diff": round(diff, 9), "pass": True,
                   "detail": detail, "sources": sources})


def per_corner_fmax_tolerance_mhz(implied_min_period_ns: float) -> float:
    """Budget for the `1000/implied_min_period vs implied_fmax` check.

    Both operands are log-rounded: `implied_fmax_mhz` to 1 dp (half-ULP
    FMAX_TOLERANCE_MHZ) and `implied_min_period_ns` to 2 dp (half-ULP
    MIN_PERIOD_HALF_ULP_NS). The min-period rounding propagates through the
    reciprocal: d(1000/T)/dT = -1000/T^2, so a +-0.005 ns perturbation of
    the logged period moves 1000/T by up to 1000/T^2 * 0.005 MHz. At the
    committed evidence's T = 37.79 ns that term is 0.0035 MHz (budget
    0.0535); at T = 5 ns it is 0.2 MHz -- four times the flat half-ULP
    alone -- so no single constant is right for all T and the budget is
    computed per corner from the operand (issue #834).
    """
    return FMAX_TOLERANCE_MHZ \
        + (1000.0 / implied_min_period_ns ** 2) * MIN_PERIOD_HALF_ULP_NS


def cross_checks(report6, sta) -> list:
    checks = []
    clock = sta["clock_period_ns"]
    tt = sta["corners"]["tt"]["values"]
    tt_src = sta["corners"]["tt"]["source"]
    for name, corners in sta["corners"].items():
        v = corners["values"]
        src = corners["source"]
        _check(
            checks, f"{name}: clock_period - setup_wns vs implied_min_period",
            clock - v["setup_wns_ns"], v["implied_min_period_ns"],
            MIN_PERIOD_TOLERANCE_NS,
            f"implied_min_period_ns is logged rounded to 2 dp and "
            f"setup_wns_ns to 3 dp, so rounding alone allows "
            f"{MIN_PERIOD_TOLERANCE_NS} ns",
            [sta["header_source"], src])
        t_ns = v["implied_min_period_ns"]
        _check(
            checks, f"{name}: 1000/implied_min_period vs implied_fmax",
            1000.0 / t_ns, v["implied_fmax_mhz"],
            per_corner_fmax_tolerance_mhz(t_ns),
            f"implied_fmax_mhz is logged rounded to 1 dp and "
            f"implied_min_period_ns to 2 dp; the min-period half-ULP "
            f"propagates through the reciprocal as "
            f"{(1000.0 / t_ns ** 2) * MIN_PERIOD_HALF_ULP_NS:.4f} MHz "
            f"at T = {t_ns:g} ns",
            [src])
    _check(
        checks, "tt setup WNS vs finish-stage metrics",
        tt["setup_wns_ns"], report6["finish__timing__setup__ws"]["value"],
        WNS_TOLERANCE_NS,
        "the finish-stage metrics run at the config's CORNER (TC = "
        "tt_025C_5v00); the log rounds to 3 dp",
        [tt_src, report6["finish__timing__setup__ws"]["source"]])
    _check(
        checks, "tt hold WNS vs finish-stage metrics",
        tt["hold_wns_ns"], report6["finish__timing__hold__ws"]["value"],
        WNS_TOLERANCE_NS,
        "same rounding as the setup check",
        [tt_src, report6["finish__timing__hold__ws"]["source"]])
    # Flat FMAX_TOLERANCE_MHZ here, NOT per_corner_fmax_tolerance_mhz: the
    # other operand, finish__timing__fmax, is a full-precision Hz float, so
    # implied_fmax_mhz's 1-dp rounding is the ONLY rounding source and the
    # flat half-ULP is already the exact budget (issue #834).
    _check(
        checks, "tt implied Fmax vs finish-stage fmax",
        tt["implied_fmax_mhz"],
        report6["finish__timing__fmax"]["value"] / 1e6,
        FMAX_TOLERANCE_MHZ,
        "6_report.json carries fmax in Hz",
        [tt_src, report6["finish__timing__fmax"]["source"]])
    for kind in ("setup", "hold"):
        _check(
            checks, f"tt {kind} TNS vs finish-stage metrics",
            tt[f"{kind}_tns_ns"],
            report6[f"finish__timing__{kind}__tns"]["value"], 0.0,
            "both carry TNS 0 for this run; exact comparison",
            [tt_src, report6[f"finish__timing__{kind}__tns"]["source"]])
    util = report6["finish__design__instance__utilization"]["value"]
    stdcell = report6["finish__design__instance__area__stdcell"]["value"]
    core = report6["finish__design__core__area"]["value"]
    _check(
        checks, "utilisation key vs std-cell/core-area recomputation",
        util, stdcell / core, UTILIZATION_TOLERANCE,
        "finish__design__instance__utilization against "
        "instance__area__stdcell / core__area",
        [report6["finish__design__instance__utilization"]["source"],
         report6["finish__design__instance__area__stdcell"]["source"],
         report6["finish__design__core__area"]["source"]])
    return checks


# ------------------------------------------------------------------ emission

def _src(entry) -> str:
    s = entry["source"]
    where = f"{s['file']}:{s['line']}" if "line" in s else s["file"]
    return f"{where} ({s.get('key', s.get('line_text', '').strip())})"


def build_report(run_dir: pathlib.Path, out_dir: pathlib.Path) -> dict:
    """Read everything, cross-check, and return the twin payload.

    Raises Refused before anything is written.
    """
    for name in REQUIRED_FILES:
        if not (run_dir / name).is_file():
            raise Refused(f"{run_dir / name}: required evidence file is "
                          f"missing -- refusing rather than omitting a row")
    report6 = load_6_report(run_dir / "6_report.json")
    sta = parse_sta_corners(run_dir / "sta_corners.log")
    synth = parse_synth_stat(run_dir / "synth_stat.txt")
    blocks = parse_blockarea(run_dir / "blockarea.md")
    provenance = die_provenance(run_dir)
    checks = cross_checks(report6["figures"], sta)

    figures = report6["figures"]
    synth_cell = synth["chip_area_um2"]["value"]
    die = figures["finish__design__die__area"]["value"]
    core = figures["finish__design__core__area"]["value"]
    derived = {
        "die_over_synth_cell_area": {
            "value": round(die / synth_cell, 2),
            "operands": ["finish__design__die__area",
                         "synth_stat.txt chip area"],
            "note": "quotable only because the provenance above attributes "
                    "the die to a fixed input",
        },
        "core_over_synth_cell_area": {
            "value": round(core / synth_cell, 2),
            "operands": ["finish__design__core__area",
                         "synth_stat.txt chip area"],
        },
    }

    source_files = {}
    for name in REQUIRED_FILES:
        blob = (run_dir / name).read_bytes()
        source_files[name] = {"sha256": hashlib.sha256(blob).hexdigest(),
                              "bytes": len(blob)}

    twin = {
        "schema_version": 1,
        "kind": "characterization-report",
        "status": "pass",
        "verdict_scope": (
            "this key says the characterization report is COMPLETE (all "
            "required evidence present, all cross-checks inside their stated "
            "tolerances). It is not a klt signoff verdict for any item and "
            "attests nothing; T1 item 8 remains UNMET"),
        "run_dir": run_dir.name,
        "design": sta["design"],
        "clock_period_ns": sta["clock_period_ns"],
        "lib": sta["lib"],
        "corner_full_names": sta["corner_full_names"],
        "rcx": sta["rcx"],
        "source_files": source_files,
        "area": {k: figures[k] for k in REQUIRED_METRICS["area"]},
        "finish_timing": {k: figures[k] for k in REQUIRED_METRICS["finish_timing"]},
        "power": {k: figures[k] for k in REQUIRED_METRICS["power"]},
        "drv": {k: figures[k] for k in REQUIRED_METRICS["drv"]},
        "per_corner_timing": {
            name: dict(corners["values"], source=corners["source"])
            for name, corners in sorted(sta["corners"].items())
        },
        "sta_header": sta["header_source"],
        "synth_baseline": synth,
        "blockarea": blocks,
        "derived_ratios": derived,
        "die_area_provenance": provenance,
        "cross_checks": checks,
        "tolerances": {
            "min_period_ns": MIN_PERIOD_TOLERANCE_NS,
            "fmax_mhz": FMAX_TOLERANCE_MHZ,
            "wns_ns": WNS_TOLERANCE_NS,
            "utilization": UTILIZATION_TOLERANCE,
        },
        "duplicate_json_keys_in_6_report": report6["duplicate_keys"],
        "ambiguous_duplicate_values_in_6_report": report6["ambiguities"],
        "not_covered": list(NOT_COVERED),
    }
    return twin


def render_markdown(twin: dict) -> str:
    """The markdown twin of the JSON. Same figures, same sources, prose."""
    a, p, d = twin["area"], twin["power"], twin["drv"]
    s, b = twin["synth_baseline"], twin["blockarea"]
    prov = twin["die_area_provenance"]
    out = []
    w = out.append
    w(f"# {twin['design']} characterization -- run `{twin['run_dir']}`")
    w("")
    w("Generated by `tools/characterize_synth_top.py` from the committed "
      "evidence in `pnr/orfs/evidence/synth_top/` only; no synthesis, "
      "place-and-route or STA was run (#33). Regenerate with "
      "`python3 tools/characterize_synth_top.py "
      "pnr/orfs/evidence/synth_top/<run-dir>`.")
    w("")
    w(f"Clock period {twin['clock_period_ns']:g} ns, lib {twin['lib']}, "
      f"corners {'/'.join(twin['corner_full_names'])}, "
      f"RCX {twin['rcx']} "
      f"-- source {twin['sta_header']['file']}:"
      f"{twin['sta_header']['line']}.")
    w("")
    w("## Area and utilisation")
    w("")
    w("| Figure | Value | Source |")
    w("|---|---|---|")
    w(f"| instances (finish) | {a['finish__design__instance__count']['value']} "
      f"| {_src(a['finish__design__instance__count'])} |")
    w(f"| std-cell area (finish, um2) "
      f"| {a['finish__design__instance__area__stdcell']['value']:,.1f} "
      f"| {_src(a['finish__design__instance__area__stdcell'])} |")
    w(f"| core area (um2) | {a['finish__design__core__area']['value']:,.1f} "
      f"| {_src(a['finish__design__core__area'])} |")
    w(f"| die area (um2) | {a['finish__design__die__area']['value']:,.1f} "
      f"| {_src(a['finish__design__die__area'])} |")
    w(f"| utilisation (finish) "
      f"| {a['finish__design__instance__utilization']['value'] * 100:.1f} % "
      f"| {_src(a['finish__design__instance__utilization'])} |")
    w(f"| rows / sites | {a['finish__design__rows']['value']} / "
      f"{a['finish__design__sites']['value']} "
      f"| {_src(a['finish__design__rows'])}, "
      f"{_src(a['finish__design__sites'])} |")
    w(f"| IO / nets | {a['finish__design__io']['value']} / "
      f"{a['finish__design__nets']['value']} "
      f"| {_src(a['finish__design__io'])}, "
      f"{_src(a['finish__design__nets'])} |")
    w(f"| yosys cells (synth) | {s['cell_count']['value']} "
      f"| {_src(s['cell_count'])} |")
    w(f"| yosys chip area (um2) | {s['chip_area_um2']['value']:,.1f} "
      f"| {_src(s['chip_area_um2'])} |")
    w(f"| blockarea instances / total area um2 / non-filler um2 "
      f"| {b['instances']['value']} / "
      f"{b['total_area_um2']['value']:,.1f} / "
      f"{b['non_filler_area_um2']['value']:,.1f} "
      f"| {_src(b['total_area_um2'])} |")
    w(f"| sequential flops / flop area um2 | {b['sequential_flops']['value']} / "
      f"{b['sequential_area_um2']['value']:,.1f} "
      f"| {_src(b['sequential_flops'])} |")
    w("")
    w(f"**Die-area provenance ({prov['state']}/{prov['reason']}, from "
      f"{prov['source']}):** {prov['detail']}")
    w("")
    for name, entry in twin["derived_ratios"].items():
        w(f"- `{name}` = **{entry['value']}** "
          f"({'; '.join(entry['operands'])})"
          + (f" -- {entry['note']}" if "note" in entry else ""))
    w("")
    w("## Per-corner timing")
    w("")
    w("Read from `sta_corners.log`'s `=== CORNER` lines; implied min period "
      "and Fmax are read, not recomputed. Slack signs are carried as logged.")
    w("")
    w("| Corner | setup WNS ns | setup TNS ns | hold WNS ns | hold TNS ns "
      "| implied min period ns | implied Fmax MHz | Source |")
    w("|---|---|---|---|---|---|---|---|")
    for name, c in twin["per_corner_timing"].items():
        v = c["source"]
        w(f"| {name} | {c['setup_wns_ns']:+.3f} | {c['setup_tns_ns']:+.3f} "
          f"| {c['hold_wns_ns']:+.3f} | {c['hold_tns_ns']:+.3f} "
          f"| {c['implied_min_period_ns']:.2f} "
          f"| {c['implied_fmax_mhz']:.1f} "
          f"| {v['file']}:{v['line']} |")
    w("")
    w("## Power (finish stage)")
    w("")
    w("| Figure | Value | Source |")
    w("|---|---|---|")
    for key, label in (("finish__power__total", "total power (W)"),
                       ("finish__power__internal__total",
                        "internal (W)"),
                       ("finish__power__switching__total",
                        "switching (W)"),
                       ("finish__power__leakage__total", "leakage (W)")):
        w(f"| {label} | {p[key]['value']} | {_src(p[key])} |")
    w("")
    w("These figures are the finish-stage metrics at **tt_025C_5v00 only**; "
      "`sta_corners.log` carries no power figures at all, so no other corner "
      "can be reported from this evidence.")
    w("")
    w("## DRV (finish stage)")
    w("")
    w("| Figure | Value | Source |")
    w("|---|---|---|")
    for key, label in (("finish__timing__drv__setup_violation_count",
                        "setup violations"),
                       ("finish__timing__drv__hold_violation_count",
                        "hold violations"),
                       ("finish__timing__drv__max_slew", "max slew viol."),
                       ("finish__timing__drv__max_cap", "max cap viol."),
                       ("finish__timing__drv__max_fanout",
                        "max fanout viol.")):
        w(f"| {label} | {d[key]['value']} | {_src(d[key])} |")
    w("")
    w("## Cross-checks (all must hold, or nothing is written)")
    w("")
    for c in twin["cross_checks"]:
        w(f"- `{c['check']}`: {c['values'][0]:g} vs {c['values'][1]:g}, "
          f"diff {c['diff']:g} <= tolerance {c['tolerance']:g}")
    w("")
    dup = twin["duplicate_json_keys_in_6_report"]
    if dup:
        w(f"Note: `6_report.json` contains duplicate JSON keys ({', '.join(dup)}); "
          "Python keeps the last. The ambiguous ones with differing values "
          "are listed in the JSON twin. None of them is a required metric of "
          "this report (the std-cell area is read from the unambiguous "
          "`__stdcell` key).")
        w("")
    w("## What this report does not cover")
    w("")
    for item in twin["not_covered"]:
        w(f"- {item}.")
    w("")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Build the synth_top characterization report from one "
                    "archived ORFS run's committed evidence.")
    parser.add_argument("run_dir", type=pathlib.Path,
                        help="evidence run directory, e.g. "
                             "pnr/orfs/evidence/synth_top/joined-d1e5068 "
                             "(explicit; never globbed)")
    parser.add_argument("--out-dir", type=pathlib.Path, default=DEFAULT_OUT_DIR,
                        help="output directory (default: docs/)")
    args = parser.parse_args(argv)
    run_dir = args.run_dir
    if not run_dir.is_dir():
        print(f"REFUSED: {run_dir} is not a directory", file=sys.stderr)
        return REFUSED_EXIT
    try:
        twin = build_report(run_dir.resolve(), args.out_dir.resolve())
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSED_EXIT
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "characterization-synth-top"
    (out_dir / f"{stem}.json").write_text(
        json.dumps(twin, indent=2) + "\n")
    (out_dir / f"{stem}.md").write_text(render_markdown(twin))
    return OK_EXIT


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
