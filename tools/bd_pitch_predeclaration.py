#!/usr/bin/env python3
"""Validator for the BD pitch-envelope pre-tuning freeze (#557, acceptance 3).

    tools/bd_pitch_predeclaration.py check [--record PATH]
    tools/bd_pitch_predeclaration.py min-improvement --baseline build/bd-pitch-baseline.json [--readings R.json]

The record (`docs/bd-pitch-predeclaration.json`) is committed BEFORE any repair
candidate is proposed, rendered or measured.  This file checks that the record
is the kind of thing that can constrain a later selection, and REFUSES rather
than answers when it cannot:

  * every condition is spelled in a vocabulary the repository already has --
    Fischer codes from `tools/perceptual_gate.py:CODES` / `TWO_KNOB`, MARS file
    names that match `tools/measure_repeatability.py:CUR_RE`, model accents from
    `model/tom_drop_fit.py:ACCENT_MAP` and DECAY knobs inside
    `model/drums_fx.py:BD_DECAY_Q`'s table.  Constants are read with `ast`
    from the source, not imported, so nothing here can drift from what ships
    and nothing here executes those modules;
  * DEVELOPMENT and UNTOUCHED are disjoint, and every axis (TONE, DECAY,
    accent, retrigger) has at least one untouched value that development never
    saw -- a held-out set that repeats development's values on an axis holds
    nothing out on it;
  * every metric function and every preservation probe names a file that
    exists and a symbol that is defined in it (`path::symbol`);
  * the minimum-improvement rule is a FORMULA over named terms, at least one
    of which is a measured apparatus floor quoted verbatim from its source
    document and one of which is read from the baseline JSON at use -- never a
    bare constant;
  * no parameter is proposed, and so no sensitivity-registry record claims
    this issue.  If one is proposed later it must name its registry record;
  * the primary aggregate declares what a REFUSED Fischer reading does
    (`primary_metric.refused_readings`), its measured-condition minimum is
    derived from the predicted refusals, and those are exactly the conditions
    at the declared `unqualified_knobs` (DECAY knob 0, where glide_cents can
    answer wrongly, #602).  `measured_set` -- shared by `primary_aggregate`
    and `satisfiable` -- applies that rule at use: a condition at an
    unqualified knob is excluded whatever it reads, a refusal is never a
    number, a reference/shipped
    refusal is excluded from both medians and listed, a candidate refusal is a
    FAILURE, and too few measured conditions REFUSE (never pass).

Exit codes follow the repository's verifier convention: 0 clean, 1 the record
violates a rule (each violation printed), 2 REFUSED (a precondition such as the
record or the baseline JSON is absent / not trustworthy).

Guard and the input that defeats it (docs/verification-rules.md rule 8):
`check` cannot tell whether the conditions were frozen BEFORE candidates were
looked at -- a record written after a selection passes it identically.  Only
git history can show that (the record's commit must precede any candidate
commit), so the record carries `frozen_against` and the PR that adds it
contains no candidate.  `check` also cannot tell whether a quoted floor is the
RIGHT floor; it only proves the number is the one the cited document states.
"""
from __future__ import annotations

import argparse
import ast
import functools
import json
import math
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECORD = ROOT / "docs" / "bd-pitch-predeclaration.json"
AXES = ("tone", "decay", "accent", "retrigger")
MARS_TONES = tuple(f"{i:02d}" for i in range(1, 7))   # 6 TONE positions (measure_repeatability s"grid")
MARS_DECAYS = tuple("ABCDEF")                           # 6 DECAY letters, same source


class Refused(RuntimeError):
    """A precondition failed; nothing was checked or evaluated."""


# ------------------------------------------------------------ source reading -
@functools.lru_cache(maxsize=None)
def _text(path: pathlib.Path) -> str:
    return path.read_text()


@functools.lru_cache(maxsize=None)
def _module_constant(rel: str, name: str):
    """A top-level literal assignment `name = <literal>` in ROOT/rel, by ast.
    re.compile("...") yields its pattern string."""
    tree = ast.parse(_text(ROOT / rel))
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        if any(t.id == name for t in targets):
            v = node.value
            if (isinstance(v, ast.Call) and getattr(v.func, "attr", "") == "compile"
                    and v.args and isinstance(v.args[0], ast.Constant)):
                return v.args[0].value
            return ast.literal_eval(v)
    raise KeyError(f"{rel} defines no top-level literal {name}")


def symbol_defined(path: pathlib.Path, symbol: str) -> bool:
    """`def symbol`, `class symbol`, or a top-level `symbol =` in the file."""
    if not path.is_file():
        return False
    text = _text(path)
    s = re.escape(symbol)
    return bool(re.search(rf"^[ \t]*(?:async[ \t]+)?(?:def|class)[ \t]+{s}\b", text, re.M)
                or re.search(rf"^{s}\s*[:=]", text, re.M))


def probe_exists(ref: str) -> str | None:
    """None if `path` or `path::symbol` resolves, else the reason it does not."""
    path, _, sym = ref.partition("::")
    p = ROOT / path
    if not p.is_file():
        return f"file {path} does not exist"
    if sym and not symbol_defined(p, sym):
        return f"{path} defines no {sym}"
    return None


# ------------------------------------------------------------- vocabularies --
@functools.lru_cache(maxsize=None)
def vocab() -> dict:
    codes = tuple(_module_constant("tools/perceptual_gate.py", "CODES"))
    two = _module_constant("tools/perceptual_gate.py", "TWO_KNOB")
    q = _module_constant("model/drums_fx.py", "BD_DECAY_Q")
    return {
        "fischer_codes": codes,
        "fischer_prefix": two["BD"],                     # "bd8/BD"
        # perceptual_gate.CODES comment: 0.0, 2.5, 5.0, 7.5, 10.0 in that order
        "fischer_knob": dict(zip(codes, (0.0, 2.5, 5.0, 7.5, 10.0))),
        "ref_main": _module_constant("model/drum_verify.py", "REF_MAIN")["BD"][0],
        "mars_re": re.compile(_module_constant("tools/measure_repeatability.py", "CUR_RE")),
        "accent_map": _module_constant("model/tom_drop_fit.py", "ACCENT_MAP"),
        "decay_knob_range": (min(q), max(q)),
        "registry": _module_constant_json("docs/sensitivity/registry.json", "records"),
    }


def _module_constant_json(rel: str, key: str):
    return json.loads((ROOT / rel).read_text())[key]


# ---------------------------------------------------------------- the check --
def axis_values(c: dict) -> dict:
    """The value a condition takes on each axis, as a hashable."""
    ref = c.get("reference") or {}
    m = c.get("model") or {}
    acc = m.get("accent")
    return {"tone": (ref.get("corpus"), c.get("tone")),
            "decay": (ref.get("corpus"), c.get("decay"), m.get("decay_knob")),
            "accent": tuple(acc) if isinstance(acc, list) else acc,
            "retrigger": m.get("retrigger_ms")}


def condition_key(c: dict) -> tuple:
    ref = c.get("reference") or {}
    return (json.dumps(ref, sort_keys=True),) + tuple(axis_values(c)[a] for a in AXES)


def _check_condition(c: dict, V: dict) -> list:
    out, cid = [], c.get("id", "<no id>")
    ref, m = c.get("reference") or {}, c.get("model") or {}
    corpus = ref.get("corpus")
    k = m.get("decay_knob")
    lo, hi = V["decay_knob_range"]
    if not isinstance(k, (int, float)) or not lo <= k <= hi:
        out.append(f"{cid}: model.decay_knob {k!r} outside BD_DECAY_Q's table {lo}..{hi}")
    r = m.get("retrigger_ms")
    if r is not None and not (isinstance(r, (int, float)) and r > 0):
        out.append(f"{cid}: model.retrigger_ms must be null or > 0, got {r!r}")
    acc = m.get("accent")
    if corpus == "fischer":
        t, d = c.get("tone"), c.get("decay")
        if t not in V["fischer_codes"] or d not in V["fischer_codes"]:
            out.append(f"{cid}: Fischer tone/decay {t!r}/{d!r} not in perceptual_gate.CODES")
        else:
            want = f"{V['fischer_prefix']}{t}{d}.WAV"
            if ref.get("file") != want:
                out.append(f"{cid}: Fischer file {ref.get('file')!r} is not {want!r}")
            if isinstance(k, (int, float)) and k != V["fischer_knob"][d]:
                out.append(f"{cid}: decay code {d} is knob {V['fischer_knob'][d]}, record says {k}")
        if acc != 1.0:
            out.append(f"{cid}: Fischer has no accent axis; model accent must be 1.0, got {acc!r}")
    elif corpus == "mars":
        files = ref.get("files") or {}
        if not isinstance(acc, list) or len(acc) != len(files) or not files:
            out.append(f"{cid}: MARS condition needs one model accent per accent file")
        for i, (letter, name) in enumerate(sorted(files.items())):
            mt = V["mars_re"].match(name or "")
            if not mt:
                out.append(f"{cid}: {name!r} does not match measure_repeatability.CUR_RE")
                continue
            if mt.group("accent") != letter:
                out.append(f"{cid}: {name!r} is accent {mt.group('accent')}, keyed {letter}")
            if mt.group("decay") != c.get("decay") or mt.group("tone") != c.get("tone"):
                out.append(f"{cid}: {name!r} is not decay {c.get('decay')} tone {c.get('tone')}")
            if mt.group("decay") not in MARS_DECAYS or mt.group("tone") not in MARS_TONES:
                out.append(f"{cid}: {name!r} outside the 6 DECAY x 6 TONE grid")
            if letter not in V["accent_map"]:
                out.append(f"{cid}: accent {letter} not in tom_drop_fit.ACCENT_MAP")
            elif isinstance(acc, list) and i < len(acc) and acc[i] != V["accent_map"][letter]:
                out.append(f"{cid}: accent {letter} maps to {V['accent_map'][letter]}, record says {acc[i]}")
    elif corpus is None:
        if r is None:
            out.append(f"{cid}: a condition with no reference must be a retrigger condition")
        if acc not in V["accent_map"].values():
            out.append(f"{cid}: model accent {acc!r} not one of tom_drop_fit.ACCENT_MAP's")
    else:
        out.append(f"{cid}: unknown reference corpus {corpus!r}")
    return out


SAFE_FUNCS = {"max": max, "min": min}


def _formula_names(expr: str) -> set:
    tree = ast.parse(expr, mode="eval")
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id not in SAFE_FUNCS:
            names.add(n.id)
        elif not isinstance(n, (ast.Expression, ast.BinOp, ast.Add, ast.Mult, ast.Sub,
                                ast.Constant, ast.Call, ast.Name, ast.Load)):
            raise ValueError(f"formula uses {type(n).__name__}; only + - * max min allowed")
    return names


def eval_formula(expr: str, values: dict) -> float:
    _formula_names(expr)                         # whitelist first
    return float(eval(compile(ast.parse(expr, mode="eval"), "<rule>", "eval"),
                      {"__builtins__": {}, **SAFE_FUNCS}, dict(values)))


def _check_rule(name: str, rule, out: list):
    """A rule is a formula over named, sourced terms; never a bare number."""
    if not isinstance(rule, dict) or not isinstance(rule.get("formula"), str):
        out.append(f"{name}: bare constant {rule!r}; must be {{formula, terms}} over measured terms")
        return
    try:
        used = _formula_names(rule["formula"])
    except (SyntaxError, ValueError) as e:
        out.append(f"{name}: formula unparsable: {e}")
        return
    terms = {t.get("name"): t for t in rule.get("terms", [])}
    if not used:
        out.append(f"{name}: formula {rule['formula']!r} names no term: a bare constant")
    for u in used - set(terms):
        out.append(f"{name}: formula uses {u}, which is not a declared term")
    for t in terms.values():
        if t.get("name") not in used:
            out.append(f"{name}: term {t.get('name')} declared but unused by the formula")
        if "quote" in t:
            src = ROOT / t.get("source", "")
            if not src.is_file():
                out.append(f"{name}: term {t['name']} cites missing {t.get('source')}")
            elif t["quote"] not in _text(src):
                out.append(f"{name}: term {t['name']}: {t['quote']!r} not found in {t['source']}")
            elif not re.search(rf"(?<![\d.]){re.escape(str(t.get('value')))}(?![\d])", t["quote"]):
                out.append(f"{name}: term {t['name']} value {t.get('value')} is not the quoted number")
        elif "baseline_key" in t:
            prod = ROOT / t.get("producer", "")
            if not prod.is_file() or f'"{t["baseline_key"]}"' not in _text(prod):
                out.append(f"{name}: {t.get('producer')} does not produce {t['baseline_key']!r}")
        elif "computed" in t:
            # evaluated at use by an existing probe, e.g. the gate's own
            # target-against-itself floor for one feature at one condition
            if (why := probe_exists(t["computed"])):
                out.append(f"{name}: term {t['name']}: {why}")
        elif "json" in t:
            p = ROOT / t["json"]
            try:
                v = json.loads(p.read_text())
                for k in t["path"]:
                    v = v[k]
            except (OSError, KeyError, TypeError, ValueError):
                out.append(f"{name}: term {t['name']}: {t['json']}:{t.get('path')} unreadable")
                continue
            if v != t.get("value"):
                out.append(f"{name}: term {t['name']} says {t.get('value')}, {t['json']} says {v}")
        else:
            out.append(f"{name}: term {t.get('name')} has no source (quote / baseline_key / json)")
    return terms


REFUSED_RULE_FIELDS = ("reading", "exclude", "candidate_refusal", "min_measured_conditions",
                       "held_out_after_exclusion", "too_few_outcome", "predicted_refusals",
                       "implemented_by", "known_answer")


def untouched_fischer(rec: dict) -> list:
    return [c for c in (rec.get("conditions") or {}).get("untouched") or []
            if (c.get("reference") or {}).get("corpus") == "fischer"]


def _check_refused_rule(rec: dict) -> list:
    """The primary aggregate must say, BEFORE candidates exist, what a REFUSED
    Fischer reading does (PR #601 review): glide_cents refuses at DECAY knob 0,
    and two untouched Fischer conditions sit there."""
    out = []
    rr = (rec.get("primary_metric") or {}).get("refused_readings")
    if not isinstance(rr, dict):
        return ["primary_metric: no refused_readings rule; a REFUSED Fischer reading's "
                "effect on the median would be chosen after candidates exist"]
    for f in REFUSED_RULE_FIELDS:
        if f not in rr:
            out.append(f"primary_metric.refused_readings: missing {f}")
    ids = {c.get("id") for c in untouched_fischer(rec)}
    n = len(ids)
    k = rr.get("min_measured_conditions")
    if not isinstance(k, int) or isinstance(k, bool) or not 1 <= k <= n:
        out.append(f"primary_metric.refused_readings: min_measured_conditions {k!r} must be an "
                   f"integer in 1..{n} (the untouched Fischer conditions)")
    pred = rr.get("predicted_refusals") or []
    for p in pred:
        if p not in ids:
            out.append(f"primary_metric.refused_readings: predicted refusal {p} is not an "
                       "untouched Fischer condition")
    # The excluded set is fixed by the estimator's qualification, not chosen:
    # every untouched Fischer condition at an unqualified DECAY knob, and no
    # other (PR #601 review, #602: at knob 0 glide_cents can ANSWER, wrongly).
    uq = rr.get("unqualified_knobs")
    if not isinstance(uq, list) or not all(_finite(x) for x in uq):
        out.append("primary_metric.refused_readings: unqualified_knobs must be a list of DECAY "
                   "knobs (where glide_cents is not qualified; conditions there are excluded "
                   "whatever they read)")
    else:
        at_uq = {c.get("id") for c in untouched_fischer(rec)
                 if (c.get("model") or {}).get("decay_knob") in uq}
        if set(pred) != at_uq or len(pred) != len(set(pred)):
            out.append(f"primary_metric.refused_readings: predicted_refusals {sorted(pred)} are not "
                       f"exactly the untouched Fischer conditions at unqualified knobs "
                       f"{sorted(at_uq)}")
    if isinstance(k, int) and n - len(set(pred) & ids) < k:
        out.append(f"primary_metric.refused_readings: unsatisfiable -- {n} conditions minus "
                   f"{len(pred)} predicted refusals leaves fewer than min_measured_conditions {k}")
    if isinstance(k, int) and not isinstance(k, bool) and k != n - len(set(pred) & ids):
        out.append(f"primary_metric.refused_readings: min_measured_conditions {k} is not its "
                   f"derivation {n} untouched Fischer conditions - {len(set(pred) & ids)} "
                   "predicted refusals; a minimum lowered to admit a result is the pathology")
    if "REFUSE" not in str(rr.get("too_few_outcome", "")):
        out.append("primary_metric.refused_readings: too_few_outcome must REFUSE (never pass)")
    for ref in (rr.get("implemented_by"), rr.get("known_answer")):
        if isinstance(ref, str) and (why := probe_exists(ref)):
            out.append(f"primary_metric.refused_readings: {why}")
    return out


def check(rec: dict) -> list:
    """Every rule the record violates, as strings.  [] means clean."""
    out = []
    V = vocab()
    conds = rec.get("conditions") or {}
    dev, unt = conds.get("development") or [], conds.get("untouched") or []
    if not dev or not unt:
        out.append("conditions: development and untouched must both be non-empty")
    ids = [c.get("id") for c in dev + unt]
    if len(ids) != len(set(ids)):
        out.append(f"conditions: duplicate ids {sorted({i for i in ids if ids.count(i) > 1})}")
    for c in dev + unt:
        out += _check_condition(c, V)
    kd = {condition_key(c): c.get("id") for c in dev}
    for c in unt:
        if condition_key(c) in kd:
            out.append(f"overlap: untouched {c.get('id')} is development {kd[condition_key(c)]}")
    for a in AXES:
        dv = {axis_values(c)[a] for c in dev}
        new = {axis_values(c)[a] for c in unt} - dv
        if not new:
            out.append(f"axis {a}: no untouched value that development does not also use")
    if not any((c.get("reference") or {}).get("file") == V["ref_main"] for c in dev):
        out.append(f"development must contain the gate's own BD target {V['ref_main']}")

    pm = rec.get("primary_metric") or {}
    for f in pm.get("functions", []):
        if (why := probe_exists(f)):
            out.append(f"primary_metric: {why}")
    if not pm.get("functions"):
        out.append("primary_metric: names no estimator function")
    out += _check_refused_rule(rec)
    for s in rec.get("secondary_metrics", []):
        for f in s.get("functions", []):
            if (why := probe_exists(f)):
                out.append(f"secondary_metric {s.get('name')}: {why}")
        if "guard" in s:
            _check_rule(f"secondary_metric {s.get('name')} guard", s["guard"], out)

    terms = _check_rule("minimum_improvement", rec.get("minimum_improvement"), out) or {}
    if not any("quote" in t for t in terms.values()):
        out.append("minimum_improvement: no measured apparatus-floor term (quoted from its source)")
    if not any("baseline_key" in t for t in terms.values()):
        out.append("minimum_improvement: no term read from the baseline JSON (recording spread)")

    props = rec.get("preservation") or []
    names = {p.get("property") for p in props}
    for need in rec.get("required_properties", []):
        if need not in names:
            out.append(f"preservation: required property {need} has no limit")
    for p in props:
        if not p.get("probes"):
            out.append(f"preservation {p.get('property')}: no probe")
        for ref in p.get("probes", []) + p.get("must_still_pass", []):
            if (why := probe_exists(ref)):
                out.append(f"preservation {p.get('property')}: {why}")
        lim = p.get("limit")
        if isinstance(lim, dict) and lim.get("bit_exact") is True:
            # zero tolerance is a statement, not a bare constant; its fallback
            # (if shared arithmetic is changed on purpose) must still be a rule
            _check_rule(f"preservation {p.get('property')} fallback", lim.get("fallback"), out)
        else:
            _check_rule(f"preservation {p.get('property')} limit", lim, out)
        if not (isinstance(lim, dict) and lim.get("relation")):
            out.append(f"preservation {p.get('property')}: limit states no relation "
                       "(what is compared with what)")

    params = rec.get("parameters_proposed")
    if params is None:
        out.append("parameters_proposed: must be stated (an empty list is a statement)")
    elif not params:
        if rec.get("sensitivity_registry_entries_added"):
            out.append("no parameter proposed, yet registry entries are claimed")
        hits = [r for r in V["registry"] if "bd-pitch" in r or "557" in r]
        if hits:
            out.append(f"no parameter proposed, yet the registry carries {hits}")
    else:
        for prm in params:
            if prm.get("registry_record") not in V["registry"]:
                out.append(f"parameter {prm.get('name')}: no sensitivity-registry record")
    return out


# ------------------------------------------------ evaluation at use (later) --
def load_baseline(path: pathlib.Path) -> dict:
    if not path.is_file():
        raise Refused(f"baseline JSON {path} is absent: run tools/bd_pitch_baseline.py on the "
                      "build box first (docs/bd-pitch-baseline-request.md)")
    try:
        b = json.loads(path.read_text())
    except ValueError as e:
        raise Refused(f"baseline {path} is not valid JSON: {e}")
    if not isinstance(b, dict) or not isinstance(b.get("provenance") or {}, dict):
        raise Refused(f"baseline {path} is not a JSON object with a provenance object")
    prov = b.get("provenance") or {}
    if not prov.get("commit") or prov.get("sources_dirty") is not False:
        raise Refused(f"baseline {path} has no commit or was produced from dirty sources")
    return b


def _finite(x) -> bool:
    """A real, finite number.  bool is an int subclass and a str is coerced by
    float(): neither is a measurement, so neither is accepted."""
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def minimum_improvement_cents(rec: dict, baseline: dict | None) -> float:
    """The frozen rule evaluated: quoted terms from the record, baseline terms
    from the baseline JSON.  REFUSES without a finite, non-negative baseline
    value (a spread is a distance) and when the evaluated threshold is not a
    finite non-negative number: a negative threshold admits worsening sound
    under `improvement >= minimum_improvement`."""
    try:
        rule = rec["minimum_improvement"]
        terms = rule["terms"]
    except (KeyError, TypeError) as e:
        raise Refused(f"the record has no evaluable minimum_improvement rule: {e!r}")
    vals = {}
    for t in terms:
        if "baseline_key" in t:
            if baseline is None:
                raise Refused(f"{t['name']} is read from the baseline JSON, and there is none")
            v = baseline.get(t["baseline_key"]) if isinstance(baseline, dict) else None
            if not _finite(v) or v < 0:
                raise Refused(f"baseline {t['baseline_key']} = {v!r} is not a finite "
                              "non-negative number")
            vals[t["name"]] = float(v)
        else:
            vals[t["name"]] = float(t["value"])
    need = eval_formula(rule["formula"], vals)
    if not _finite(need) or need < 0:
        raise Refused(f"the evaluated minimum improvement {need!r} is not a finite "
                      "non-negative number")
    return need


SIDES = ("reference", "shipped", "candidate")


def _measured(x):
    """A glide reading as a float, or None if it is REFUSED.  Only a finite
    real number is a reading: a Refused, None, NaN, inf, bool or a string
    (even "0") is a refusal.  A refusal is NEVER converted to a number."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def measured_set(rec: dict, readings: dict) -> tuple:
    """(measured, excluded): the ONE definition of which untouched Fischer
    conditions enter the medians, shared by primary_aggregate and satisfiable
    so the two cannot drift.  `measured` is [(id, {side: float|None})].

      * a condition at one of `refused_readings.unqualified_knobs` is excluded
        WHATEVER it reads (reason "unqualified_knob") -- glide_cents at DECAY
        knob 0 can answer +76..+97 c on a constant-pitch tone (#602), and an
        answer there is not a measurement;
      * otherwise reference or shipped REFUSED -> excluded, listing the side;
      * fewer than min_measured_conditions measured, or no held-out TONE /
        DECAY value left -> Refused.
    Every malformed input (non-dict readings or entries, wrong ids) REFUSES."""
    rr = (rec.get("primary_metric") or {}).get("refused_readings")
    if (not isinstance(rr, dict) or not isinstance(rr.get("min_measured_conditions"), int)
            or isinstance(rr.get("min_measured_conditions"), bool)):
        raise Refused("the record declares no refused_readings rule; the aggregate cannot be formed")
    uq = rr.get("unqualified_knobs")
    if not isinstance(uq, list) or not all(_finite(x) for x in uq):
        raise Refused("the record declares no unqualified_knobs list; the aggregate cannot be formed")
    conds = {c["id"]: c for c in untouched_fischer(rec)}
    if not isinstance(readings, dict):
        raise Refused(f"readings must be a JSON object keyed by condition id, got "
                      f"{type(readings).__name__}")
    if set(readings) != set(conds):
        raise Refused(f"readings must cover exactly the untouched Fischer conditions: missing "
                      f"{sorted(set(conds) - set(readings))}, unknown {sorted(set(readings) - set(conds))}")
    bad_rows = sorted(cid for cid, v in readings.items() if not isinstance(v, dict))
    if bad_rows:
        raise Refused(f"readings entries must be objects {{reference, shipped, candidate}}; "
                      f"not an object: {bad_rows}")
    measured, excluded = [], []
    for cid in sorted(conds):
        if (conds[cid].get("model") or {}).get("decay_knob") in uq:
            excluded.append({"id": cid, "refused": ["unqualified_knob"]})
            continue
        r = {s: _measured(readings[cid].get(s)) for s in SIDES}
        bad = [s for s in ("reference", "shipped") if r[s] is None]
        if bad:
            excluded.append({"id": cid, "refused": bad})
            continue
        measured.append((cid, r))
    k = rr["min_measured_conditions"]
    if len(measured) < k:
        raise Refused(f"only {len(measured)} untouched Fischer condition(s) measurable "
                      f"(< {k}); excluded {excluded}")
    dev = (rec.get("conditions") or {}).get("development") or []
    for axis in ("tone", "decay"):
        dv = {axis_values(c)[axis] for c in dev}
        if not {axis_values(conds[cid])[axis] for cid, _ in measured} - dv:
            raise Refused(f"after exclusion the measured set holds out no {axis} value; "
                          f"excluded {excluded}")
    return measured, excluded


def primary_aggregate(rec: dict, readings: dict) -> dict:
    """The frozen primary aggregate with the frozen REFUSED rule applied.

    `readings` maps every untouched Fischer condition id to
    {"reference": r, "shipped": s, "candidate": c}, each a glide_cents value or
    a refusal (anything `_measured` rejects).  d = reference - ours.
    Which conditions enter is `measured_set` (shared with `satisfiable`):

      * a condition at an unqualified DECAY knob is excluded whatever it reads;
      * reference or shipped REFUSED -> condition excluded from BOTH medians,
        listed with the side that refused;
      * reference and shipped measured, candidate REFUSED -> verdict FAILURE
        (the candidate destroyed a measurable trajectory); no median is formed
        for the candidate, so a refusal cannot become a 0-cent |d|;
      * fewer than min_measured_conditions measured, or the measured set no
        longer holds out a TONE and a DECAY value development never used ->
        Refused (no claim either way; never a pass).
    """
    import statistics
    measured, excluded = measured_set(rec, readings)
    cand_refused = [cid for cid, r in measured if r["candidate"] is None]
    out = {"measured": [cid for cid, _ in measured], "excluded": excluded,
           "candidate_refused": cand_refused,
           "shipped_median": statistics.median(abs(r["reference"] - r["shipped"])
                                               for _, r in measured)}
    if cand_refused:
        out.update(verdict="FAILURE", candidate_median=None, improvement=None)
        return out
    out["candidate_median"] = statistics.median(abs(r["reference"] - r["candidate"])
                                                for _, r in measured)
    out["improvement"] = out["shipped_median"] - out["candidate_median"]
    out["verdict"] = "EVALUATED"
    return out


def one_take_diagnostic(rec: dict, baseline: dict) -> dict:
    """The baseline JSON's ONE pair (fischer_vs_ours, the gate's own BD target,
    which the record puts in DEVELOPMENT) against the threshold.  This is NOT
    satisfiability: acceptance uses the untouched median, a different statistic
    (PR #601 review: a deficit of 20 dev / 100 untouched and the reverse give
    opposite answers).  Reported as information only."""
    need = minimum_improvement_cents(rec, baseline)
    try:
        deficit = baseline["pairs"]["fischer_vs_ours"]["glide_deficit_cents"]
    except (KeyError, TypeError) as e:
        raise Refused(f"baseline has no pairs.fischer_vs_ours.glide_deficit_cents: {e!r}")
    if not _finite(deficit):
        raise Refused(f"baseline glide_deficit_cents = {deficit!r} is not a finite number")
    deficit = abs(float(deficit))
    return {"scope": "one_take_diagnostic_not_satisfiability", "min_improvement_cents": need,
            "development_take_deficit_cents": deficit, "take_reaches_threshold": need <= deficit}


def satisfiable(rec: dict, baseline: dict, readings: dict) -> dict:
    """Run the gate against the current state before trusting it (CLAUDE.md),
    on the statistic acceptance uses: the SHIPPED median over the frozen
    measured untouched Fischer conditions, same exclusions and holdout rules.
    The largest improvement any candidate can show is a candidate that reads
    exactly the reference (candidate median 0), so it is that median; the pass
    rule is `improvement >= minimum`, hence `<=` here.  `readings` is the same
    shape primary_aggregate takes; only reference and shipped are used.  The
    conditions are chosen by `measured_set`, the same function the aggregate
    uses, so satisfiability and acceptance cannot be over different sets."""
    import statistics
    need = one_take_diagnostic(rec, baseline)["min_improvement_cents"]   # also validates the baseline
    measured, excluded = measured_set(rec, readings)
    best = statistics.median(abs(r["reference"] - r["shipped"]) for _, r in measured)
    return {"min_improvement_cents": need, "max_possible_improvement_cents": best,
            "measured": [cid for cid, _ in measured], "excluded": excluded,
            "satisfiable": need <= best}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--record", type=pathlib.Path, default=RECORD)
    m = sub.add_parser("min-improvement")
    m.add_argument("--record", type=pathlib.Path, default=RECORD)
    m.add_argument("--baseline", type=pathlib.Path, required=True)
    m.add_argument("--readings", type=pathlib.Path, default=None,
                   help="JSON {untouched Fischer id: {reference, shipped}} of glide_cents "
                        "readings; without it satisfiability is NOT established")
    a = ap.parse_args(argv)
    try:
        if not a.record.is_file():
            raise Refused(f"record {a.record} is absent")
        rec = json.loads(a.record.read_text())
        if a.cmd == "check":
            bad = check(rec)
            for b in bad:
                print(f"VIOLATION {b}")
            print("CLEAN" if not bad else f"FAIL ({len(bad)} violation(s))")
            return 1 if bad else 0
        baseline = load_baseline(a.baseline)
        if a.readings is None:
            print(json.dumps(one_take_diagnostic(rec, baseline), indent=1))
            print("REFUSED: satisfiability is over the untouched median and no untouched "
                  "readings were given (--readings); the one-take figure above is not that "
                  "statistic, so no claim either way.")
            return 2
        if not a.readings.is_file():
            raise Refused(f"readings JSON {a.readings} is absent")
        try:
            readings = json.loads(a.readings.read_text())
        except ValueError as e:
            raise Refused(f"readings {a.readings} is not valid JSON: {e}")
        s = satisfiable(rec, baseline, readings)
        print(json.dumps(s, indent=1))
        if not s["satisfiable"]:
            print("REFUSED: the frozen minimum improvement exceeds the largest improvement the "
                  "untouched median can show; the defect is not resolvable above the floor. "
                  "Do not loosen the rule.")
            return 2
        return 0
    except Refused as e:
        print(f"REFUSED: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
