#!/usr/bin/env python3
"""Sweep a tunable parameter before arguing about it -- and check that we did.

    sensitivity.py check                  gate every registered parameter
    sensitivity.py check --changed-since origin/main
    sensitivity.py show drum-kit-modes    the whole record, expanded
    sensitivity.py check --inject VERDICT_ASSERTED --expect fail

WHY THIS EXISTS. Hours went into 8 modes versus 12. yosys pads the mode bank's
state arrays to a power of two words, so 9 through 16 cost identically, and the
variable that actually mattered was `NUMS`, which nobody had swept. That is
`docs/failure-modes.md` mechanism 5, and until now its remedy was one sentence
of prose: "before a parameter debate is allowed to consume time, sweep it."

WHAT THIS GATES, AND WHAT IT DOES NOT. It does not detect an argument -- an
argument is prose and happens in a chat window. It gates the ARTEFACT the
argument should have been settled by:

  1. the value that SHIPS is a point on a committed grid;
  2. the grid is exactly the set of points the measurement artefact contains,
     so a plateau cannot be manufactured by leaving a point out;
  3. every recorded point is re-extracted from that artefact here, so a number
     in the record is a transcription of a measurement and not a claim;
  4. the flat/sensitive verdict is RECOMPUTED from the points under the
     record's own stated decision rule -- a stored verdict that disagrees with
     its own data is a claim that outlived its evidence;
  5. the verdict matches a PREDICTION derived independently of the measurement
     (how yosys maps the arrays, what the RTL prunes). A sweep checked only
     against itself establishes repeatability, not correctness -- this
     repository has already paid for that lesson twice.

`docs/sensitivity-sweeps.md` is the convention; this is its enforcement.

Exit 0 pass, 1 red, 2 REFUSED -- this repository's convention, where 2 means
"could not answer", which is not the same as "no problem".
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "docs" / "sensitivity"
REGISTRY = DIR / "registry.json"

RECORD_SCHEMA = "sensitivity-sweep-v1"
REGISTRY_SCHEMA = "sensitivity-registry-v1"
STATES = ("FLAT", "MIXED", "SENSITIVE")


class Refused(Exception):
    """The gate could not answer. Never reported as a pass."""


# --------------------------------------------------------------------------
# loading


def load_registry(path: Path = REGISTRY) -> dict:
    if not path.exists():
        raise Refused(f"no registry at {path}")
    reg = json.loads(path.read_text())
    if reg.get("schema") != REGISTRY_SCHEMA:
        raise Refused(f"{path.name}: schema is {reg.get('schema')!r}, not {REGISTRY_SCHEMA!r}")
    if not isinstance(reg.get("records"), list) or not reg["records"]:
        raise Refused(f"{path.name}: no records listed")
    return reg


REQUIRED = ("schema", "id", "parameter", "objective", "grid",
            "decision_rule", "evidence", "verdict")


def load_records(reg: dict, root: Path = ROOT) -> list[dict]:
    out = []
    for name in reg["records"]:
        path = root / "docs" / "sensitivity" / name
        if not path.exists():
            raise Refused(f"registry lists {name}, which does not exist")
        rec = json.loads(path.read_text())
        missing = [k for k in REQUIRED if k not in rec]
        if missing:
            raise Refused(f"{name}: record is missing {', '.join(missing)}")
        if rec["schema"] != RECORD_SCHEMA:
            raise Refused(f"{name}: schema is {rec['schema']!r}, not {RECORD_SCHEMA!r}")
        rec["_file"] = name
        out.append(rec)
    return out


# --------------------------------------------------------------------------
# the measurement artefact


NUMBER = re.compile(r"-?\d+(?:\.\d+)?$")
IDENT = re.compile(r"[A-Za-z_]\w*$")
SECTION = re.compile(r"^([A-Za-z_]\w*)\s+--")


def parse_fixed_width_tables(text: str) -> dict[str, list[dict[str, float]]]:
    """Tables out of a report written for humans.

    A section is `<name> -- prose` at column 0; its header is the first
    all-identifier line under it; its rows are the lines of numbers that
    follow, until a blank line. Prose between tables has neither shape and is
    skipped -- which is asserted in the tests, because a parser that silently
    reads a sentence as a row would turn commentary into evidence.
    """
    tables: dict[str, list[dict[str, float]]] = {}
    section: str | None = None
    header: list[str] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            header = None
            continue
        top = SECTION.match(line)
        if top:
            section = top.group(1)
            tables.setdefault(section, [])
            header = None
            continue
        tokens = stripped.split()
        if section is None:
            continue
        if header is None:
            if len(tokens) >= 2 and all(IDENT.match(t) for t in tokens):
                header = tokens
            continue
        if len(tokens) == len(header) and all(NUMBER.match(t) for t in tokens):
            tables[section].append({k: float(v) for k, v in zip(header, tokens)})
        else:
            header = None
    return tables


def extract_points(rec: dict, root: Path = ROOT) -> list[tuple[float, float]]:
    """Re-derive the record's points from the artefact it names."""
    ev = rec["evidence"]
    if ev.get("format") != "fixed-width-table":
        raise Refused(f"{rec['id']}: unknown evidence format {ev.get('format')!r}")
    path = root / ev["artifact"]
    if not path.exists():
        raise Refused(f"{rec['id']}: evidence artefact {ev['artifact']} is missing")
    tables = parse_fixed_width_tables(path.read_text())
    if ev["table"] not in tables:
        raise Refused(f"{rec['id']}: {ev['artifact']} has no table {ev['table']!r}")
    rows = tables[ev["table"]]
    held = ev.get("held_fixed") or {}
    for column in (ev["value_column"], ev["objective_column"], *held):
        if rows and column not in rows[0]:
            raise Refused(f"{rec['id']}: table {ev['table']!r} has no column {column!r}")
    keep = [r for r in rows if all(r[k] == v for k, v in held.items())]
    points = sorted((r[ev["value_column"]], r[ev["objective_column"]]) for r in keep)
    if not points:
        raise Refused(f"{rec['id']}: no rows in {ev['table']!r} hold {held}")
    return points


# --------------------------------------------------------------------------
# the verdict, recomputed


def runs(points: list[tuple[float, float]], tol: float) -> list[list[float]]:
    """Maximal contiguous groups of grid values the objective cannot separate.

    Two points are in the same group when the group's own spread stays within
    the record's stated relative tolerance. Greedy from the low end; the
    objective is a cost curve over an ordered dial, so a group is an interval.
    """
    out: list[list[float]] = []
    values: list[float] = []
    lo = hi = 0.0
    for value, objective in points:
        if values:
            nlo, nhi = min(lo, objective), max(hi, objective)
            # the epsilon makes "within 2 %" include exactly 2 %: 102/100 - 1
            # is 0.020000000000000018 in binary floating point, and a stated
            # rule should not turn on that
            if nhi / nlo - 1.0 > tol * (1.0 + 1e-9):
                out.append(values)
                values, lo, hi = [value], objective, objective
                continue
            lo, hi = nlo, nhi
            values.append(value)
        else:
            values, lo, hi = [value], objective, objective
    if values:
        out.append(values)
    return out


def state_of(groups: list[list[float]], n_points: int) -> str:
    if len(groups) == 1:
        return "FLAT"
    if len(groups) == n_points:
        return "SENSITIVE"
    return "MIXED"


def pct(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0


# --------------------------------------------------------------------------
# the parameter as it actually ships


PARAM = r"^\s*(?:parameter|localparam)\s+(?:\w+\s+)?{name}\s*=\s*(-?\d+)\s*[,;)]"


def parse_parameter(text: str, name: str, where: str) -> float:
    hits = re.findall(PARAM.format(name=re.escape(name)), text, re.MULTILINE)
    if len(hits) != 1:
        raise Refused(f"{where}: found {len(hits)} declarations of {name}, expected exactly 1")
    return float(hits[0])


def shipped_value(rec: dict, root: Path = ROOT, overrides: dict | None = None) -> float:
    p = rec["parameter"]
    key = (p["source"], p["name"])
    if overrides and key in overrides:
        return float(overrides[key])
    if p.get("kind") != "verilog-parameter":
        raise Refused(f"{rec['id']}: unknown parameter kind {p.get('kind')!r}")
    path = root / p["source"]
    if not path.exists():
        raise Refused(f"{rec['id']}: parameter source {p['source']} is missing")
    return parse_parameter(path.read_text(), p["name"], p["source"])


def value_at_ref(rec: dict, ref: str, root: Path = ROOT) -> float | None:
    """The parameter's value at a git ref, or None if the file is not there."""
    p = rec["parameter"]
    r = subprocess.run(["git", "show", f"{ref}:{p['source']}"],
                       cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        if "exists on disk, but not in" in r.stderr or "does not exist" in r.stderr:
            return None
        raise Refused(f"cannot read {p['source']} at {ref}: {r.stderr.strip()}")
    return parse_parameter(r.stdout, p["name"], f"{p['source']}@{ref}")


# --------------------------------------------------------------------------
# evaluation


def group_index(groups: list[list[float]], value: float) -> int | None:
    for i, g in enumerate(groups):
        if value in g:
            return i
    return None


def evaluate(rec: dict, root: Path = ROOT, overrides: dict | None = None) -> dict:
    """One record -> its recomputed verdict plus every problem found.

    Raises Refused when the record cannot be evaluated at all. Returns
    problems (a red gate) when it can be evaluated and is wrong.
    """
    problems: list[str] = []
    ident = rec["id"]

    rule = rec["decision_rule"]
    tol = rule.get("flat_within_relative")
    if not isinstance(tol, (int, float)) or not 0 < tol < 1:
        raise Refused(f"{ident}: decision_rule.flat_within_relative is {tol!r}, "
                      "so there is no rule to apply")
    if "stated_before_results" not in rec["grid"]:
        raise Refused(f"{ident}: grid does not say whether it was stated before results")
    retrofit = not rec["grid"]["stated_before_results"]
    if retrofit and not rec.get("retrofit"):
        raise Refused(f"{ident}: grid was stated after the results and the record "
                      "carries no retrofit block saying so")

    measured = extract_points(rec, root)
    grid = [float(v) for v in rec["grid"]["values"]]
    if sorted(grid) != [v for v, _ in measured]:
        problems.append(
            f"{ident}: grid {sorted(grid)} is not the set of points the artefact holds "
            f"{[v for v, _ in measured]} -- a grid that drops a measured point can "
            "manufacture a plateau")

    recorded = {float(p["value"]): float(p["objective"]) for p in rec.get("points", [])}
    for value, objective in measured:
        if value not in recorded:
            problems.append(f"{ident}: point {value:g} is measured but not recorded")
        elif abs(recorded[value] - objective) > 0.05:
            problems.append(f"{ident}: point {value:g} records {recorded[value]:g} but "
                            f"{rec['evidence']['artifact']} measures {objective:g}")
    for value in recorded:
        if value not in [v for v, _ in measured]:
            problems.append(f"{ident}: point {value:g} is recorded but not measured")

    groups = runs(measured, tol)
    state = state_of(groups, len(measured))
    stored = rec["verdict"].get("state")
    if stored not in STATES:
        raise Refused(f"{ident}: stored verdict state {stored!r} is not one of {STATES}")
    if stored != state:
        problems.append(f"{ident}: record states {stored} but its own points under its own "
                        f"{tol:.0%} rule are {state} -- a verdict that outlived its evidence")
    stored_groups = rec["verdict"].get("runs")
    if stored_groups is not None and [[float(v) for v in g] for g in stored_groups] != groups:
        problems.append(f"{ident}: record's runs {stored_groups} are not the runs its points "
                        f"give, {[[int(v) if v.is_integer() else v for v in g] for g in groups]}")

    prediction = rec.get("prediction")
    grounding = "none"
    if prediction:
        grounding = "predicted"
        predicted = [[float(v) for v in g] for g in prediction["runs"]]
        if predicted != groups:
            problems.append(
                f"{ident}: measured runs {groups} do not match the runs predicted "
                f"independently of the measurement, {predicted} -- "
                f"({prediction.get('basis', 'no basis given')})")

    ships = shipped_value(rec, root, overrides)
    on_grid = ships in [v for v, _ in measured]
    if not on_grid:
        problems.append(f"{ident}: {rec['parameter']['name']} ships as {ships:g}, which is not "
                        f"on the grid {sorted(grid)} -- the value that ships has no sweep evidence")

    objective = dict(measured)
    best = min(measured, key=lambda p: p[1])
    index = group_index(groups, ships) if on_grid else None
    return {
        "id": ident, "parameter": rec["parameter"]["name"],
        "source": rec["parameter"]["source"],
        "objective": rec["objective"]["name"], "units": rec["objective"]["units"],
        "tolerance": tol, "points": measured, "runs": groups, "state": state,
        "grounding": grounding, "retrofit": retrofit,
        "ships": ships, "ships_on_grid": on_grid,
        "ships_run": groups[index] if index is not None else None,
        "ships_run_index": index,
        "lower_is_better": bool(rec["objective"].get("lower_is_better")),
        "best_value": best[0], "best_objective": best[1],
        # what moving from the shipped value to the cheapest point on the grid
        # would do to the objective: the only move the sweep says buys anything
        "best_move_pct": pct(objective[ships], best[1]) if on_grid else None,
        "problems": problems,
    }


def describe(row: dict) -> str:
    groups = ["-".join(f"{v:g}" for v in (g[0], g[-1])) if len(g) > 1 else f"{g[0]:g}"
              for g in row["runs"]]
    return f"{row['state']:<9} runs [{', '.join(groups)}] at {row['tolerance']:.0%}"


def print_table(rows: list[dict], out=sys.stdout) -> None:
    print(f"{'parameter':<8} {'source':<26} {'objective':<22} {'ships':>6}  verdict", file=out)
    for r in rows:
        mark = "" if r["ships_on_grid"] else " OFF-GRID"
        tag = " [retrofit]" if r["retrofit"] else ""
        tag += " [ungrounded]" if r["grounding"] == "none" else ""
        print(f"{r['parameter']:<8} {r['source']:<26} "
              f"{r['objective'] + ' ' + r['units']:<22} {r['ships']:>6g}  "
              f"{describe(r)}{mark}{tag}", file=out)
        if r["ships_on_grid"] and len(r["ships_run"]) > 1:
            lo, hi = r["ships_run"][0], r["ships_run"][-1]
            print(f"{'':<8} -> anything in {lo:g}..{hi:g} is the same {r['objective']} to "
                  f"within {r['tolerance']:.0%}: arguing inside that range cannot move it",
                  file=out)
        if r["lower_is_better"] and r["best_move_pct"] is not None and r["best_move_pct"] < -0.0:
            print(f"{'':<8} -> cheapest point on the grid: {r['parameter']}="
                  f"{r['best_value']:g}, {r['best_move_pct']:+.1f}% of {r['objective']} "
                  f"against the shipped {r['ships']:g}", file=out)


# --------------------------------------------------------------------------
# injected controls -- a gate nobody has watched fail is not a gate


INJECTIONS = {
    "VERDICT_ASSERTED": "stored verdict flipped away from what its points say",
    "POINT_TRANSCRIBED": "one recorded point no longer matches the measured artefact",
    "GRID_CHERRY_PICKED": "a measured point dropped from the grid",
    "SHIPPED_OFF_GRID": "the shipped parameter value moved off the grid",
    "PREDICTION_WRONG": "the independent prediction contradicts the measurement",
    "RULE_UNSTATED": "the decision rule removed (must REFUSE, not pass)",
}


def inject(name: str, records: list[dict], overrides: dict) -> None:
    if name not in INJECTIONS:
        raise Refused(f"unknown injection {name!r}; have {', '.join(sorted(INJECTIONS))}")
    rec = records[0]
    if name == "VERDICT_ASSERTED":
        rec["verdict"]["state"] = "FLAT" if rec["verdict"]["state"] != "FLAT" else "SENSITIVE"
        rec["verdict"].pop("runs", None)
    elif name == "POINT_TRANSCRIBED":
        rec["points"][0]["objective"] = float(rec["points"][0]["objective"]) * 1.5
    elif name == "GRID_CHERRY_PICKED":
        dropped = rec["grid"]["values"][0]
        rec["grid"]["values"] = rec["grid"]["values"][1:]
        rec["points"] = [p for p in rec["points"] if p["value"] != dropped]
    elif name == "SHIPPED_OFF_GRID":
        grid = [float(v) for v in rec["grid"]["values"]]
        off = max(grid) + 1
        while off in grid:
            off += 1
        overrides[(rec["parameter"]["source"], rec["parameter"]["name"])] = off
    elif name == "PREDICTION_WRONG":
        if not rec.get("prediction"):
            raise Refused(f"{rec['id']} carries no prediction to contradict")
        rec["prediction"]["runs"] = [[v for v in rec["grid"]["values"]]]
    elif name == "RULE_UNSTATED":
        rec["decision_rule"].pop("flat_within_relative", None)


# --------------------------------------------------------------------------


def run_check(root: Path = ROOT, changed_since: str | None = None,
              injection: str | None = None, out=sys.stdout) -> int:
    records = load_records(load_registry(root / "docs" / "sensitivity" / "registry.json"), root)
    overrides: dict = {}
    if injection:
        inject(injection, records, overrides)
        print(f"INJECTED {injection}: {INJECTIONS[injection]}", file=out)

    rows = [evaluate(rec, root, overrides) for rec in records]
    print_table(rows, out)

    problems = [p for r in rows for p in r["problems"]]

    if changed_since is not None:
        for rec, row in zip(records, rows):
            was = value_at_ref(rec, changed_since, root)
            if was is None or was == row["ships"]:
                continue
            name = rec["parameter"]["name"]
            if not row["ships_on_grid"]:
                problems.append(f"{row['id']}: {name} changed {was:g} -> {row['ships']:g} since "
                                f"{changed_since} and {row['ships']:g} is not on the grid; "
                                "sweep it before changing it")
                continue
            if was not in [v for v, _ in row["points"]]:
                print(f"NOTE {row['id']}: {name} changed {was:g} -> {row['ships']:g}; the old "
                      f"value is not on the grid, so the sweep cannot price the move", file=out)
                continue
            objective = dict(row["points"])
            delta = pct(objective[was], objective[row["ships"]])
            same = group_index(row["runs"], was) == group_index(row["runs"], row["ships"])
            verdict = ("FLAT-MOVE" if same else "PRICED")
            print(f"{verdict} {row['id']}: {name} {was:g} -> {row['ships']:g} moves "
                  f"{row['objective']} by {delta:+.2f}%"
                  + (f" (inside the {row['tolerance']:.0%} rule -- this change is not a "
                     f"{row['objective']} optimisation; justify it on another objective)"
                     if same else ""), file=out)

    if problems:
        print("", file=out)
        for p in problems:
            print(f"RED {p}", file=out)
        return 1
    print("\nevery registered parameter ships a value its own sweep covers", file=out)
    return 0


def show(ident: str, root: Path = ROOT, out=sys.stdout) -> int:
    records = load_records(load_registry(root / "docs" / "sensitivity" / "registry.json"), root)
    match = [r for r in records if r["id"] == ident]
    if not match:
        raise Refused(f"no registered parameter {ident!r}; "
                      f"have {', '.join(r['id'] for r in records)}")
    rec = match[0]
    row = evaluate(rec, root)
    print(f"{rec['id']}: {rec['parameter']['name']} in {rec['parameter']['source']}", file=out)
    print(f"  what      {rec['parameter'].get('what', '')}", file=out)
    print(f"  objective {rec['objective']['name']} ({rec['objective']['units']})", file=out)
    print(f"  evidence  {rec['evidence']['artifact']} table {rec['evidence']['table']!r}, "
          f"held fixed {rec['evidence'].get('held_fixed', {})}", file=out)
    print(f"  regenerate {rec['evidence'].get('regenerate', '?')}", file=out)
    print(f"  rule      within {row['tolerance']:.0%} is the same value "
          f"-- {rec['decision_rule'].get('why', '')}", file=out)
    print(f"  {rec['parameter']['name']:>9} {rec['objective']['units']:>12}", file=out)
    for value, objective in row["points"]:
        flag = "  <- ships" if value == row["ships"] else ""
        print(f"  {value:>9g} {objective:>12.1f}{flag}", file=out)
    print(f"  verdict   {describe(row)}", file=out)
    for line in rec["verdict"].get("summary", "").splitlines():
        print(f"            {line}", file=out)
    if rec.get("prediction"):
        print(f"  predicted {rec['prediction']['runs']} -- {rec['prediction']['basis']}", file=out)
    for limit in rec.get("limits", []):
        print(f"  LIMIT     {limit}", file=out)
    if row["problems"]:
        for p in row["problems"]:
            print(f"RED {p}", file=out)
        return 1
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="gate every registered parameter")
    c.add_argument("--changed-since", metavar="REF",
                   help="also price any registered parameter this branch moves")
    c.add_argument("--inject", choices=sorted(INJECTIONS),
                   help="corrupt the records in memory; the gate must notice")
    c.add_argument("--expect", choices=("fail", "refused"),
                   help="invert: exit 0 only if the run came back this way")
    s = sub.add_parser("show", help="one parameter, expanded")
    s.add_argument("id")
    a = ap.parse_args(argv)

    if a.cmd == "show":
        try:
            return show(a.id)
        except Refused as exc:
            print(f"REFUSED: {exc}")
            return 2

    try:
        code = run_check(changed_since=a.changed_since, injection=a.inject)
    except Refused as exc:
        print(f"REFUSED: {exc}")
        code = 2
    if not a.expect:
        return code
    got = {0: "pass", 1: "fail", 2: "refused"}[code]
    if got == a.expect:
        print(f"control OK: expected {a.expect}, got {got}")
        return 0
    print(f"CONTROL DID NOT FIRE: expected {a.expect}, got {got}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
