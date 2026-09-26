#!/usr/bin/env python3
"""Three activities, versioned separately, so a number that moved can be
attributed: `render` (source + config -> raw WAV and traces), `analyse`
(retained WAV + reference -> measurements), `accept` (measurements + criteria
-> verdict).

    render    id = hash(render code version, case, config, inputs)
    analyse   id = hash(analyser version + method, render id, artefact sha256)
    accept    id = hash(criteria id, analysis id)

WHY THIS FILE EXISTS, in one sentence: when the snare went 8.8 -> 7.6 -> 3.4,
part of that was a real fix and part was a wrong TONE law in the measurement
harness, and the only reason anyone could separate the two was that a person
thought to look. `classify_delta` below is that question asked mechanically,
and the reason it can be asked at all is that the render id does not contain
the analyser and the analysis id does not contain the render's clock time:

  * the same source and config re-rendered gets the SAME render id, so a
    differing render id means the SOUND changed;
  * the same retained audio re-analysed by a different estimator gets a
    different analysis id over an unchanged render id, so that means the
    MEASUREMENT changed;
  * when both differ, `classify_delta` REFUSES to attribute the delta. That is
    the honest answer and it is the one this project kept not getting.

WHAT A MEASUREMENT MUST CARRY, and why each field is not optional
-----------------------------------------------------------------
`decay = 127.4` is not a measurement. `MEASUREMENT_SCHEMA` below is the minimum
that makes one, and every entry is here because of a specific wrong answer this
repository shipped:

  units + metric        `CP decay tau 47 ms` was the `E_CPTAIL` REGISTER, not
                        the voice's decay, and the voice was right all along
  analyser + method     a Hann-windowed 700 Hz split off by 15x; an amplitude-
                        weighted centroid reading a quiet wideband floor as
                        bright. The number is uninterpretable without the
                        method that produced it
  selection             which hit, which onset, which interval -- #101 was a
                        window cut into the strike
  conditioning          channel, rate, resampling, filtering, normalisation:
                        window leakage was once reported as 25 dB of separation
  spectral              FFT size and window, for the same reason
  quality               a fit with R^2 0.82 and one with 0.996 are not the same
                        evidence, and the noise floor's treatment decides where
                        a decay stops being a decay
  reference             the EXACT recording or document, by hash, with the
                        control settings it was captured at
  bound + rationale     a test that turns green because a tolerance moved is
                        not a test that passed

A FIELD THAT IS PRESENT IS NOT A FIELD THAT IS STATED. `stated()` rejects "",
None, "n/a", "unknown", "TBD" and a bare "not applicable", because the cheap
way to satisfy a schema is to fill it with words that mean nothing and the
result looks exactly like a complete record. A genuinely inapplicable field
must say why -- `not applicable: this metric is a time-domain envelope fit and
takes no transform` passes; `n/a` does not.

REFUSED IS A FIRST-CLASS OUTCOME. An incomplete measurement record gets no
verdict, never a pass: `accept()` returns outcome `REFUSED` with outcome code 2
(this repository's "no evidence", per `tools/run_case.py`'s OUTCOME_CODE), and
that is red. A tool that answers when it cannot is worse than one that is
absent, because its output looks exactly like data.

Relation to what already exists: `tools/run_case.py`'s `provenance` block is
the source of the worktree/command/input-hash fields here and is reused, not
re-derived -- `provenance_block()` calls into it when it can be imported. This
file adds the staging and the attribution, which `run_case.py` does not have
because it renders, measures and scores in one pass with one id.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import pathlib

SCHEMA_VERSION = 1

REFUSED = "REFUSED"

STAGES = ("render", "analyse", "accept")

# Retention CLASSES, not durations in the workflow file: a reference fixture and
# a smoke-test render are both "artifacts" to CI and must not expire together.
# The workflow reads these so the number lives with the thing it describes.
RETENTION_DAYS = {
    "reference-fixture": 90,      # the audio a bound was derived from
    "release-evidence": 90,       # what a claim in docs/ rests on
    "smoke": 7,                   # a per-push render nobody will re-read
}

# `tools/run_case.py`'s convention, kept identical on purpose: a case that
# produced no evidence and a case that was measured and scored badly need
# opposite responses, so they are never the same code.
OUTCOME_CODE = {"pass": 0, "fail": 1, "no verdict": 2, REFUSED: 2}
OUTCOME_MEANING = "0 match, 1 mismatch (a result), 2 did not run (no evidence)"

# Words that look like a statement and are not. A schema satisfied with these
# is a schema that has been defeated.
NOT_A_STATEMENT = {"", "n/a", "na", "none", "null", "unknown", "tbd", "todo",
                   "?", "-", "not applicable", "not stated", "default"}

NOT_APPLICABLE_PREFIX = "not applicable"


def stated(v) -> bool:
    """Is this field a statement, or a placeholder wearing one's clothes?

    Numbers and bools are statements. `None` is not. A string is a statement
    unless it is in `NOT_A_STATEMENT`, and `not applicable` on its own is NOT:
    an inapplicable field must say why it is inapplicable, in at least a few
    words, or the schema is satisfiable by typing "n/a" into every box."""
    if v is None:
        return False
    if isinstance(v, bool):
        return True
    if isinstance(v, (int, float)):
        return math.isfinite(float(v))
    if isinstance(v, str):
        s = v.strip().lower()
        if s in NOT_A_STATEMENT:
            return False
        if s.startswith(NOT_APPLICABLE_PREFIX):
            rest = s[len(NOT_APPLICABLE_PREFIX):].lstrip(" :;,-")
            return len(rest) >= 8
        return True
    if isinstance(v, (list, tuple, dict, set)):
        return len(v) > 0
    return True


# The minimum record. Keys map to the sub-keys each one must itself state; an
# empty tuple means the value is a scalar and is checked with `stated` directly.
MEASUREMENT_SCHEMA: dict[str, tuple] = {
    "metric": (),
    "units": (),
    "valid": (),
    "analyser": ("name", "version", "method"),
    "selection": ("hit_index", "onset_s", "interval_s", "interval_basis"),
    "conditioning": ("channel", "sample_rate_hz", "resampled_from_hz", "filter",
                     "normalisation"),
    "spectral": ("transform", "window", "nfft", "hop_samples"),
    "quality": ("fit", "uncertainty", "uncertainty_basis", "noise_floor_db",
                "noise_floor_treatment"),
    "reference": ("kind", "identity", "sha256", "matched_settings"),
    "bound": ("value", "tolerance", "units", "kind", "rationale", "history"),
}

REFERENCE_KINDS = ("recording", "document", "model-lock", "schedule")
BOUND_KINDS = ("target", "lock")
BOUND_HISTORY_FIELDS = ("at", "from", "to", "reason")


def validate_bound(b, *, where: str = "bound") -> list[str]:
    """A bound with no rationale is a number someone chose, and a bound whose
    value does not match the last recorded change is a number someone moved.

    `model/sound_report.py` already separates a `target` (a document says so,
    outside it means WRONG) from a `lock` (this model at a named commit,
    outside it means CHANGED). What it has no room for is *when this one moved
    and why*, which is the gap issue #68 names. `history` is that room, and the
    last entry's `to` must equal the live value -- so a silent overwrite is a
    detectable state rather than an invisible one."""
    p: list[str] = []
    if not isinstance(b, dict):
        return [f"{where}: not a record at all ({type(b).__name__})"]
    for k in MEASUREMENT_SCHEMA["bound"]:
        if k not in b:
            p.append(f"{where}: no {k} field at all")
        elif not stated(b[k]):
            p.append(f"{where}.{k} is present but not stated ({b[k]!r})")
    if b.get("kind") not in BOUND_KINDS and "kind" in b:
        p.append(f"{where}.kind is {b.get('kind')!r}, not one of {BOUND_KINDS} -- "
                 f"a document's figure and this model's pinned value mean "
                 f"opposite things when a run is outside them")
    hist = b.get("history")
    if isinstance(hist, list) and hist:
        for i, h in enumerate(hist):
            if not isinstance(h, dict):
                p.append(f"{where}.history[{i}]: not a record")
                continue
            for k in BOUND_HISTORY_FIELDS:
                if k == "from":
                    # `from` may legitimately be null for the first entry: the
                    # bound did not exist before. It must still be PRESENT.
                    if k not in h:
                        p.append(f"{where}.history[{i}]: no from field at all")
                    continue
                if k not in h or not stated(h[k]):
                    p.append(f"{where}.history[{i}].{k} is missing or not stated")
        last = hist[-1]
        if isinstance(last, dict) and "to" in last and "value" in b:
            try:
                moved = abs(float(last["to"]) - float(b["value"])) > 1e-9
            except (TypeError, ValueError):
                moved = last["to"] != b["value"]
            if moved:
                p.append(f"{where}: the bound is {b['value']!r} but the last recorded "
                         f"change set it to {last['to']!r} -- it moved with no reason "
                         f"recorded. A test that goes green because a tolerance moved "
                         f"is not a test that passed")
    return p


def validate_measurement(m, *, where: str = "measurement") -> list[str]:
    """Every problem with this record, as sentences. Empty list means complete.

    Deliberately returns ALL problems rather than raising on the first: a
    record with four holes reported as one hole gets fixed four times."""
    if not isinstance(m, dict):
        return [f"{where}: not a record at all ({type(m).__name__})"]
    name = m.get("metric") if stated(m.get("metric")) else "(unnamed)"
    at = f"{where} {name}"
    p: list[str] = []
    for field, subkeys in MEASUREMENT_SCHEMA.items():
        if field not in m:
            p.append(f"{at}: no {field} field at all")
            continue
        if not subkeys:
            if not stated(m[field]):
                p.append(f"{at}.{field} is present but not stated ({m[field]!r})")
            continue
        sub = m[field]
        if not isinstance(sub, dict):
            p.append(f"{at}.{field}: not a record ({type(sub).__name__})")
            continue
        if field == "bound":
            p += validate_bound(sub, where=f"{at}.bound")
            continue
        for k in subkeys:
            if k not in sub:
                p.append(f"{at}.{field}: no {k} field at all")
            elif not stated(sub[k]):
                p.append(f"{at}.{field}.{k} is present but not stated ({sub[k]!r})")

    # The value, and the run_case.py rule it follows: an invalid measurement has
    # no distance, not a zero one, and carries no `error` key at all.
    valid = bool(m.get("valid"))
    if valid:
        if not isinstance(m.get("value"), (int, float)) or not math.isfinite(
                float(m.get("value", float("nan")))):
            p.append(f"{at}: valid is true but value is {m.get('value')!r} -- "
                     f"json writes NaN happily and a board must never be handed one")
    else:
        if not stated(m.get("why")):
            p.append(f"{at}: invalid with no stated reason. A refusal that does not "
                     f"say what refused is indistinguishable from a bug")
        if "error" in m:
            p.append(f"{at}: invalid but carries an error key -- zero distance reads "
                     f"on a board as a perfect match")

    ref = m.get("reference")
    if isinstance(ref, dict):
        if ref.get("kind") not in REFERENCE_KINDS and "kind" in ref:
            p.append(f"{at}.reference.kind is {ref.get('kind')!r}, not one of "
                     f"{REFERENCE_KINDS}")
        if ref.get("kind") == "recording" and not stated(ref.get("matched_settings")):
            p.append(f"{at}.reference: a recording with no matched control settings "
                     f"-- the study that drove our snare with the wrong TONE law "
                     f"passed exactly this check by not having it")

    sel = m.get("selection")
    if isinstance(sel, dict) and "interval_s" in sel:
        iv = sel["interval_s"]
        ok = (isinstance(iv, (list, tuple)) and len(iv) == 2
              and all(isinstance(v, (int, float)) for v in iv) and iv[1] > iv[0])
        if not ok:
            p.append(f"{at}.selection.interval_s is {iv!r}, not an ordered "
                     f"[t0, t1] pair in seconds")

    bound = m.get("bound")
    if isinstance(bound, dict) and stated(m.get("units")) and stated(bound.get("units")):
        if str(m["units"]) != str(bound["units"]):
            p.append(f"{at}: measured in {m['units']!r} and bounded in "
                     f"{bound['units']!r}. `CP decay tau 47 ms` was a register read "
                     f"as a decay; this is that category error, mechanised")
    return p


def validate_measurements(ms) -> list[str]:
    if not isinstance(ms, list) or not ms:
        return ["no measurements at all: an analysis with no measurement is not "
                "an analysis"]
    p: list[str] = []
    seen: set[str] = set()
    for i, m in enumerate(ms):
        p += validate_measurement(m, where=f"measurement[{i}]")
        n = (m or {}).get("metric") if isinstance(m, dict) else None
        if isinstance(n, str):
            if n in seen:
                p.append(f"measurement[{i}]: {n!r} appears twice -- which one is "
                         f"the measurement?")
            seen.add(n)
    return p


# ---------------------------------------------------------------- identity ----
def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest(obj) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def stage_id(stage: str, identity: dict) -> str:
    """Content-addressed, and the CONTENT is `identity` only.

    What is deliberately NOT in here: the clock time, the command line, the
    worktree's dirty hash, the host. Those go in `provenance` and would make
    every re-run a new id, which would destroy the only thing this scheme is
    for -- re-rendering the same source and config must land on the same id so
    that a DIFFERENT id means the sound really moved."""
    if stage not in STAGES:
        raise ValueError(f"{stage!r} is not one of {STAGES}")
    return f"{stage}-{digest(identity)[:16]}"


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()[:32]


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def provenance_block(extra: dict | None = None) -> dict:
    """Reuse `tools/run_case.py`'s provenance rather than inventing a second
    format: the commit AND the uncommitted tree's hash, because a clean SHA
    that silently means "plus whatever was in the working tree" is worse than
    no SHA. Degrades to a stated refusal if run_case cannot be imported --
    never to a bare commit, which would be the lying record."""
    block = {"schema_version": SCHEMA_VERSION, "recorded_at": now()}
    try:
        import run_case
        block["worktree"] = run_case.worktree_state()
        block["python"] = __import__("sys").version.split()[0]
    except Exception as e:                                          # noqa: BLE001
        block["worktree"] = {"refused": f"run_case.worktree_state() unavailable: "
                                        f"{type(e).__name__}: {e}"}
    block.update(extra or {})
    return block


# ------------------------------------------------------------------ store ----
# Plain files, per issue #68's own scope note. No service, no dashboard.
#
#   <root>/runs/<render id>/manifest.json      what was rendered, and from what
#   <root>/runs/<render id>/audio/*.wav        the raw audio, retained
#   <root>/runs/<render id>/traces/*           internal traces, retained
#   <root>/jobs/<analysis id>/manifest.json    the measurements
#   <root>/jobs/<analysis id>/traces/*         what the fit actually ran on
#   <root>/jobs/<accept id>/verdict.json       the verdict, and the criteria
#   <root>/index.json                          append-only, for a human
def stage_dir(root, rec: dict) -> pathlib.Path:
    root = pathlib.Path(root)
    sub = "runs" if rec["stage"] == "render" else "jobs"
    return root / sub / rec["id"]


def write_stage(root, rec: dict) -> pathlib.Path:
    d = stage_dir(root, rec)
    d.mkdir(parents=True, exist_ok=True)
    name = "verdict.json" if rec["stage"] == "accept" else "manifest.json"
    (d / name).write_text(json.dumps(rec, indent=1, default=str) + "\n")
    idx = pathlib.Path(root) / "index.json"
    entries = []
    if idx.exists():
        try:
            entries = json.loads(idx.read_text())
        except ValueError:
            entries = []
    line = {"stage": rec["stage"], "id": rec["id"], "at": rec.get("recorded_at")
            or rec.get("provenance", {}).get("recorded_at"),
            "case": rec.get("case"), "outcome": rec.get("outcome"),
            "retention_class": rec.get("retention_class"),
            "retention_days": rec.get("retention_days")}
    entries = [e for e in entries if not (e.get("id") == rec["id"])] + [line]
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps(entries, indent=1) + "\n")
    return d / name


def read_stage(root, stage: str, sid: str) -> dict:
    sub = "runs" if stage == "render" else "jobs"
    name = "verdict.json" if stage == "accept" else "manifest.json"
    p = pathlib.Path(root) / sub / sid / name
    if not p.exists():
        raise FileNotFoundError(f"no {stage} record {sid} under {root} ({p})")
    rec = json.loads(p.read_text())
    if rec.get("stage") != stage:
        raise ValueError(f"{p} says stage {rec.get('stage')!r}, not {stage!r}")
    return rec


def make_render(*, case: str, code_version: dict, config: dict, inputs: dict,
                retention_class: str = "smoke") -> dict:
    """The identity is code version + case + config + inputs. Nothing else."""
    identity = {"case": case, "code_version": code_version, "config": config,
                "inputs": inputs, "schema_version": SCHEMA_VERSION}
    return {"schema_version": SCHEMA_VERSION, "stage": "render",
            "id": stage_id("render", identity),
            "case": case, "identity": identity,
            "code_version": code_version, "config": config, "inputs": inputs,
            "artefacts": {}, "outcome": "produced",
            "retention_class": retention_class,
            "retention_days": RETENTION_DAYS[retention_class],
            "provenance": provenance_block()}


def make_analysis(*, render: dict, analyser: dict, measurements: list,
                  artefacts: dict | None = None,
                  retention_class: str = "smoke") -> dict:
    """The identity is the analyser plus the EXACT audio it read, by hash.

    `audio_sha256` is taken from the render manifest, so an analysis cannot
    claim a render it did not read: `bind_to_retained` asserts the file on disk
    still hashes to this before any estimator runs."""
    audio = {k: v.get("sha256") for k, v in (render.get("artefacts") or {}).items()}
    identity = {"analyser": analyser, "render_id": render["id"], "audio": audio,
                "schema_version": SCHEMA_VERSION}
    problems = validate_measurements(measurements)
    return {"schema_version": SCHEMA_VERSION, "stage": "analyse",
            "id": stage_id("analyse", identity),
            "case": render.get("case"), "identity": identity,
            "of_render": render["id"], "render_audio": audio,
            "render_code_version": render.get("code_version"),
            "analyser": analyser,
            "measurements": measurements,
            "schema_problems": problems,
            "outcome": "measured" if not problems else REFUSED,
            "artefacts": artefacts or {},
            "retention_class": retention_class,
            "retention_days": RETENTION_DAYS[retention_class],
            "provenance": provenance_block()}


def bind_to_retained(root, render: dict, artefact: str = "audio") -> tuple:
    """Assert the precondition at the point of use: the retained file must
    still hash to what the render manifest recorded.

    An analyse stage that re-derives its own audio and calls the result a
    re-analysis of last week's render is the whole failure this scheme exists
    to prevent, and it is indistinguishable from the real thing unless somebody
    checks the bytes. Returns `(path, sha)` or raises."""
    a = (render.get("artefacts") or {}).get(artefact)
    if not a:
        raise FileNotFoundError(f"render {render['id']} retained no {artefact!r} "
                                f"artefact, so there is nothing to re-analyse")
    p = pathlib.Path(root) / "runs" / render["id"] / a["path"]
    if not p.exists():
        raise FileNotFoundError(f"render {render['id']} names {a['path']} and it is "
                                f"not on disk. The audio was not retained, so the "
                                f"history is gone: re-render, and do not pretend "
                                f"this is a re-analysis")
    have = sha256_file(p)
    if have != a["sha256"]:
        raise ValueError(f"REFUSED: {p} hashes {have}, the render manifest says "
                         f"{a['sha256']}. The retained artefact is not the one that "
                         f"was rendered, so any measurement off it is attributed to "
                         f"the wrong sound")
    return p, have


# ----------------------------------------------------------------- accept ----
def accept(analysis: dict, *, criteria_id: str, criteria_rationale: str) -> dict:
    """Measurements + criteria -> verdict, as a SEPARATE stage with its own id.

    Separate because the two fail differently. An analysis is wrong when the
    method is wrong; a verdict is wrong when the bound is wrong, and a bound
    can be moved without touching a line of measurement code. Keeping them in
    one pass is how "did it go green because the implementation improved, or
    because a tolerance moved" became unanswerable."""
    problems = validate_measurements(analysis.get("measurements") or [])
    per: dict[str, dict] = {}
    worst = "pass"
    if problems:
        worst = REFUSED
    else:
        for m in analysis["measurements"]:
            b = m["bound"]
            row = {"bound_kind": b["kind"], "bound_rationale": b["rationale"],
                   "bound_value": b["value"], "tolerance": float(b["tolerance"]),
                   "bound_last_changed": (b["history"] or [{}])[-1]}
            if not m.get("valid"):
                # No distance, not a zero one, and no `error` key: the
                # run_case.py rule, because zero reads as a perfect match.
                row.update(state="no verdict", why=m.get("why", ""))
            else:
                err = float(m["value"]) - float(b["value"])
                row.update(state="pass" if abs(err) <= float(b["tolerance"]) else "fail",
                           value=m["value"], error=round(err, 6),
                           why=(f"{m['value']:.4g} vs {b['value']:.4g} {m['units']} "
                                f"(error {err:+.4g}, tolerance "
                                f"+/-{float(b['tolerance']):.4g}, {b['kind']})"))
            per[m["metric"]] = row
            # no verdict beats fail beats pass: no evidence is not a result
            order = {"pass": 0, "fail": 1, "no verdict": 2}
            if order.get(row["state"], 2) > order.get(worst, 0):
                worst = row["state"]
    identity = {"criteria_id": criteria_id, "analysis_id": analysis["id"],
                "schema_version": SCHEMA_VERSION}
    return {"schema_version": SCHEMA_VERSION, "stage": "accept",
            "id": stage_id("accept", identity),
            "case": analysis.get("case"), "identity": identity,
            "of_analysis": analysis["id"], "of_render": analysis.get("of_render"),
            "criteria_id": criteria_id, "criteria_rationale": criteria_rationale,
            "schema_problems": problems,
            "outcome": worst,
            "outcome_code": OUTCOME_CODE[worst],
            "outcome_code_meaning": OUTCOME_MEANING,
            "why": (f"{len(problems)} incomplete measurement record(s): no verdict, "
                    f"not a pass" if problems else "every record was complete"),
            "metrics": per,
            "retention_class": analysis.get("retention_class", "smoke"),
            "retention_days": RETENTION_DAYS[analysis.get("retention_class", "smoke")],
            "provenance": provenance_block()}


# -------------------------------------------------------------- attribution ---
NEITHER = "nothing changed: same render, same analyser"
MEASUREMENT_ONLY = "THE MEASUREMENT CHANGED, THE SOUND DID NOT"
SOUND_ONLY = "THE SOUND CHANGED, THE MEASUREMENT DID NOT"
BOTH = ("REFUSED: both the render and the analyser changed, so this pair cannot "
        "attribute the delta to either. Re-analyse the older render with the newer "
        "analyser and compare that instead")


def classify_delta(a: dict, b: dict) -> dict:
    """Did the sound change, or only the measurement?

    Answered from the identities alone, which is the only way it can be
    answered without trusting whoever wrote the commit message. Also reports,
    per metric, MOVED or BLIND -- because a metric that never moves for any
    defect is decoration wearing the costume of coverage
    (docs/verification-rules.md 4), and a delta attributed to a method change
    that only ONE of four metrics can see is a different claim from one all
    four see."""
    ra, rb = a.get("of_render"), b.get("of_render")
    aa, ab = a.get("analyser"), b.get("analyser")
    audio_a, audio_b = a.get("render_audio"), b.get("render_audio")
    render_changed = (ra != rb) or (audio_a != audio_b)
    analyser_changed = aa != ab
    if render_changed and analyser_changed:
        attribution, attributable = BOTH, False
    elif analyser_changed:
        attribution, attributable = MEASUREMENT_ONLY, True
    elif render_changed:
        attribution, attributable = SOUND_ONLY, True
    else:
        attribution, attributable = NEITHER, True

    def by_name(rec):
        return {m["metric"]: m for m in (rec.get("measurements") or [])
                if isinstance(m, dict) and "metric" in m}

    ma, mb = by_name(a), by_name(b)
    metrics: dict[str, dict] = {}
    for n in sorted(set(ma) | set(mb)):
        x, y = ma.get(n), mb.get(n)
        row: dict = {"units": (y or x or {}).get("units")}
        if x is None or y is None:
            row.update(moved=None, seen="only in one of the two analyses")
            metrics[n] = row
            continue
        va, vb = x.get("value"), y.get("value")
        tol = (y.get("bound") or {}).get("tolerance")
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            d = float(vb) - float(va)
            # MOVED means moved by enough to change a verdict. A metric that
            # wobbles inside its own tolerance has not seen anything.
            moved = abs(d) > float(tol) if isinstance(tol, (int, float)) else d != 0
            row.update(before=va, after=vb, delta=round(d, 6),
                       tolerance=tol, moved=bool(moved),
                       saw=("MOVED" if moved else "BLIND"))
        else:
            row.update(before=va, after=vb, moved=None,
                       saw="no verdict: one side has no value")
        row["bound_changed"] = (x.get("bound") != y.get("bound"))
        metrics[n] = row
    return {"schema_version": SCHEMA_VERSION,
            "before": a["id"], "after": b["id"],
            "render_changed": bool(render_changed),
            "analyser_changed": bool(analyser_changed),
            "renders": [ra, rb], "analysers": [aa, ab],
            "attributable": attributable,
            "attribution": attribution,
            "metrics": metrics,
            "bounds_changed": sorted(n for n, r in metrics.items()
                                     if r.get("bound_changed")),
            "recorded_at": now()}
