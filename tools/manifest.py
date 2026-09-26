#!/usr/bin/env python3
"""render / analyse / accept: three separately-hashed, separately-versioned
stages, so a case can answer **"did the sound change, or only the
measurement?"** (issue #68).

    render    source + config             -> raw WAV + numerical traces
    analyse   WAVs + reference recordings  -> measurements
    accept    measurements + criteria      -> verdict

Each stage writes its own plain-file record under `runs/<render_id>/` or
`jobs/<job_id>/` (no service, no dashboard -- issue #68's own Scope note),
linked to its inputs by content hash rather than by trust:

  * `render()` writes `runs/<render_id>/manifest.json` + `raw.wav` (+ any
    numerical traces the caller wants retained). `render_id` is derived from
    the case id, the exact render config, and the worktree state (commit +
    uncommitted-tree hash) that produced it -- two renders of different
    source or config never collide, and a case can be RE-analysed against a
    retained WAV without re-rendering it.
  * `analyse()` reads a render manifest (refusing if the retained WAV's
    content no longer matches the hash recorded at render time -- CLAUDE.md:
    "assert your apparatus's preconditions ... and REFUSE rather than report
    when they fail"), and writes `jobs/<job_id>/analysis.json`: one record
    per named measurement, each carrying the full audit trail a bare number
    cannot: units, the analyser's own name and version, the selected
    hit/onset/analysis interval, channel/resample/filter/normalisation,
    FFT/window settings, fit quality and noise-floor treatment, and the
    exact reference recording identity it was measured against.
  * `accept()` reads an analysis record plus named acceptance criteria (a
    bound and a mandatory rationale for each) and writes
    `jobs/<job_id>/accept.json`: a pass/fail/no-verdict per metric. A bound
    that differs from the last one accepted for the same metric is recorded
    with BOTH the old and new bound and both their rationales -- the gap
    `model/sound_report.py`'s `LOCK`/`LOCKS` leaves today, where a lock can
    be overwritten with no record of why.
  * `diagnose()` answers the title question directly: given two `analyse()`
    records for the same metric, it says whether the render differed, the
    analyser differed, both, or neither -- and REFUSES rather than guess when
    both differ at once, because that comparison genuinely cannot separate
    the two contributions.

Schema note: the provenance fields these records carry (commit, uncommitted
tree hash, content hashes, exact command/config) are `tools/run_case.py`'s
own `provenance()` block (see its module docstring, "PROVENANCE, AND THE EXIT
CODE"), reused via `tools/provenance.py` rather than re-derived -- issue #68's
Implementation Guidance is explicit that a second, competing format is the
wrong outcome here.

Scope, deliberately: plain files only. `runs/` and `jobs/` are the literal
paths issue #68's Scope note names; wiring their upload as CI artifacts with
retention rules (reference fixtures kept longer than smoke-test output) is
follow-up work, not part of this module -- see the tracking issue referenced
from the PR that introduced this file.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import wave
from typing import Callable

import numpy as np

import provenance

ROOT = provenance.ROOT
RUNS_DIR = ROOT / "runs"
JOBS_DIR = ROOT / "jobs"


class Refused(Exception):
    """A precondition of this stage was not met. `REFUSED` is a first-class
    outcome here (CLAUDE.md), distinct from a measurement that ran and
    failed its bounds -- never silently absorbed into a number."""


def _hash_json(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _write_wav16(path: pathlib.Path, x, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    y = np.clip(np.asarray(x) * 32768.0, -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())


def _read_wav16(path: pathlib.Path):
    with wave.open(str(path), "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    return x, sr


def _write_traces(out_dir: pathlib.Path, traces: dict | None) -> dict:
    """Write each trace under `out_dir/traces/` and return paths relative to
    `out_dir` -- relative to the record's OWN directory, not the repo root,
    so a manifest is portable to wherever its `runs_dir`/`jobs_dir` actually
    lives (a tmp dir in a test, a CI artifact root, ...)."""
    paths = {}
    for name, arr in (traces or {}).items():
        tp = out_dir / "traces" / f"{name}.json"
        tp.parent.mkdir(parents=True, exist_ok=True)
        tp.write_text(json.dumps(np.asarray(arr).tolist()))
        paths[name] = str(tp.relative_to(out_dir))
    return paths


# =============================================================================
# render: source + config -> raw WAV + traces
# =============================================================================
def render(case_id: str, config: dict, render_fn: Callable[[], tuple], *,
           runs_dir: pathlib.Path = RUNS_DIR, root: pathlib.Path = ROOT,
           traces: dict | None = None) -> dict:
    """Call `render_fn()` -> `(samples, sample_rate)`, write the raw WAV and
    any numerical traces, and write the render-stage manifest.

    `render_fn` takes no arguments -- the caller closes over whatever it
    needs (voice, injected control, seconds, ...) and states all of it again,
    as data, in `config`. `render()` does not introspect `render_fn`, so
    `config` is the only thing a later re-render can be compared against; an
    empty `config` on a render whose behaviour actually depends on an
    argument is a schema violation the caller made, not one this function can
    catch for them.

    `render_id` folds in the exact worktree state (commit + a hash of the
    uncommitted tree) as well as `config`, so two renders that differ only in
    uncommitted source changes -- the common case while iterating on a model
    file -- are never mistaken for the same render.
    """
    result = render_fn()
    x, sr = result[0], result[1]
    wt = provenance.worktree_state(root=root)
    cfg_sha = _hash_json(config)
    rid = f"{case_id}-{wt['commit']}-{wt['uncommitted_sha256'][:8]}-{cfg_sha}"
    out_dir = runs_dir / rid
    wav_path = out_dir / "raw.wav"
    _write_wav16(wav_path, x, sr)
    wav_hash = provenance.file_sha(wav_path, root=root)
    trace_paths = _write_traces(out_dir, traces)
    manifest = {
        "stage": "render",
        "render_id": rid,
        "case_id": case_id,
        "config": config,
        "sample_rate": sr,
        "n_samples": int(len(x)),
        # Relative to the render's OWN directory (`runs_dir/render_id/`), not
        # the repo root -- portable to wherever `runs_dir` actually lives.
        "wav_path": str(wav_path.relative_to(out_dir)),
        "wav_sha256": wav_hash,
        "trace_paths": trace_paths,
        "provenance": {"worktree": wt, "run_at": provenance.now()},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def load_render(render_id: str, *, runs_dir: pathlib.Path = RUNS_DIR,
                 root: pathlib.Path = ROOT):
    """Load a previously-written render manifest and its retained WAV,
    re-verifying the WAV's content hash at the point of use rather than
    trusting the path. Returns `(manifest, samples, sample_rate)`.

    This is what makes retroactive reanalysis safe: an `analyse()` call
    reads through here, so a WAV silently edited (or truncated, or replaced)
    after render time is refused rather than quietly analysed as though it
    were still what `render()` produced.
    """
    out_dir = runs_dir / render_id
    manifest_path = out_dir / "manifest.json"
    if not manifest_path.exists():
        raise Refused(f"no render manifest at {manifest_path} -- render the case "
                       f"before analysing it")
    manifest = json.loads(manifest_path.read_text())
    wav_path = out_dir / manifest["wav_path"]
    have = provenance.file_sha(wav_path, root=root)
    want = manifest["wav_sha256"]
    if have != want:
        raise Refused(f"{wav_path} does not match the hash its render manifest "
                       f"recorded ({want} recorded, {have} on disk) -- the retained "
                       f"WAV changed after render() wrote it; refusing to analyse it "
                       f"as though it still were what render() produced")
    x, sr = _read_wav16(wav_path)
    return manifest, x, sr


# =============================================================================
# analyse: WAVs + reference recordings -> measurements
# =============================================================================
# Every field a measurement must carry to be re-derivable rather than
# re-trusted (issue #68 acceptance criterion 3). `value` and `name` are the
# number and what it is of; everything else is the audit trail. `fft` and
# `fit_quality` must still be PRESENT even when not applicable to a given
# method -- state "not used", do not omit the key.
MEASUREMENT_FIELDS = (
    "name", "units", "value",
    "analyser", "analyser_version", "method",
    "interval_s", "channel", "resample_hz", "filter", "normalisation",
    "fft", "fit_quality", "reference_identity",
)


def measurement(name: str, units: str, value: float | None, *, analyser: str,
                 analyser_version: str, method: str, interval_s, channel: str,
                 resample_hz, filter, normalisation, fft, fit_quality,
                 reference_identity: str, why: str | None = None) -> dict:
    """Build one measurement record. Every field is a required keyword with
    no default, so an omission is a `TypeError` at the call site -- not a key
    quietly absent from JSON six months later. `value=None` is a valid,
    explicit refusal (`why` should then say why); it is not the same as the
    key being missing."""
    rec = {
        "name": name, "units": units, "value": value,
        "analyser": analyser, "analyser_version": analyser_version,
        "method": method, "interval_s": list(interval_s), "channel": channel,
        "resample_hz": resample_hz, "filter": filter,
        "normalisation": normalisation, "fft": fft, "fit_quality": fit_quality,
        "reference_identity": reference_identity,
    }
    if why is not None:
        rec["why"] = why
    return rec


def validate_measurement(record: dict) -> None:
    """REFUSE a measurement record missing any required field -- the check
    that makes a manifest re-loaded from disk (as opposed to one just built
    in-process by `measurement()`) trustworthy. Names every missing field,
    not just the first, because a reader fixing this by hand should not have
    to re-run it once per field."""
    missing = [f for f in MEASUREMENT_FIELDS if f not in record]
    if missing:
        raise Refused(
            f"measurement record {record.get('name', '(unnamed)')!r} is missing "
            f"required field(s): {', '.join(missing)} -- a measurement without "
            f"them cannot later be told apart from one whose interval, filter, "
            f"or reference nobody recorded")


def analysis_id(render_manifest: dict, analyser: str, analyser_version: str,
                 config: dict) -> str:
    cfg_sha = _hash_json(config)
    return f"{render_manifest['render_id']}-{analyser}-{analyser_version}-{cfg_sha}"


def analyse(render_manifest: dict, measurements: list, *, analyser: str,
            analyser_version: str, config: dict, jobs_dir: pathlib.Path = JOBS_DIR,
            root: pathlib.Path = ROOT, traces: dict | None = None) -> dict:
    """WAVs + reference recordings -> measurements. `measurements` is a list
    of dicts, normally built with `measurement()`; every one is validated
    (see `validate_measurement`) before anything is written -- an analysis
    that would produce one incomplete record writes none of them, so a
    partial `analysis.json` is never mistaken for a complete one.

    Separately hashed/versioned from the render it reads: the job id folds in
    the render id, the analyser's own name and version, and this call's
    config, so re-running `analyse()` with a different `analyser_version`
    against the SAME retained WAV produces a distinct record rather than
    overwriting the first one -- that pair is exactly what `diagnose()` below
    needs to answer "did the sound change, or only the measurement?".
    """
    for m in measurements:
        validate_measurement(m)
    aid = analysis_id(render_manifest, analyser, analyser_version, config)
    out_dir = jobs_dir / aid
    trace_paths = _write_traces(out_dir, traces)
    record = {
        "stage": "analyse",
        "job_id": aid,
        "render_id": render_manifest["render_id"],
        "render_wav_sha256": render_manifest["wav_sha256"],
        "analyser": analyser,
        "analyser_version": analyser_version,
        "config": config,
        "measurements": {m["name"]: m for m in measurements},
        "trace_paths": trace_paths,
        "provenance": {"worktree": provenance.worktree_state(root=root),
                        "run_at": provenance.now()},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(json.dumps(record, indent=2, sort_keys=True))
    return record


def diagnose(a: dict, b: dict, metric: str) -> dict:
    """The question this whole module exists to answer, for one metric
    across two `analyse()` records: did the RENDER differ, the ANALYSER
    differ, both, or neither?

    REFUSES rather than guess when both differ at once -- that comparison
    cannot separate the two contributions; re-analyse one leg holding the
    other fixed and compare again."""
    ma, mb = a["measurements"].get(metric), b["measurements"].get(metric)
    if ma is None or mb is None:
        raise Refused(f"{metric!r} is not present in both analyses")
    if ma["value"] is None or mb["value"] is None:
        raise Refused(f"{metric!r} was refused in at least one analysis "
                       f"({ma.get('why')!r} / {mb.get('why')!r}) -- there is no "
                       f"distance to diagnose")
    same_render = (a["render_id"] == b["render_id"]
                   and a["render_wav_sha256"] == b["render_wav_sha256"])
    same_analyser = ((a["analyser"], a["analyser_version"])
                      == (b["analyser"], b["analyser_version"]))
    delta = mb["value"] - ma["value"]
    if same_render and not same_analyser:
        verdict, why = "measurement changed", (
            f"same audio (render {a['render_id']}); analyser "
            f"{a['analyser']}@{a['analyser_version']} -> "
            f"{b['analyser']}@{b['analyser_version']}")
    elif not same_render and same_analyser:
        verdict, why = "sound changed", (
            f"same analyser ({a['analyser']}@{a['analyser_version']}); render "
            f"{a['render_id']} -> {b['render_id']}")
    elif same_render and same_analyser:
        verdict, why = "no change", "same render and same analyser"
    else:
        raise Refused(
            f"{metric!r}: both the audio (render {a['render_id']} -> "
            f"{b['render_id']}) and the analyser ({a['analyser']}@"
            f"{a['analyser_version']} -> {b['analyser']}@{b['analyser_version']}) "
            f"differ between these two analyses -- this comparison cannot "
            f"separate the two contributions; re-analyse one leg holding the "
            f"other fixed")
    return {"metric": metric, "verdict": verdict, "why": why,
            "value_a": ma["value"], "value_b": mb["value"], "delta": delta}


# =============================================================================
# accept: measurements + criteria -> verdict
# =============================================================================
def accept(analysis: dict, criteria: dict, *, jobs_dir: pathlib.Path = JOBS_DIR,
           history_path: pathlib.Path | None = None) -> dict:
    """measurements + criteria -> verdict.

    `criteria` is `{metric_name: {"lo": float, "hi": float, "rationale": str}}`.
    A bound with no `rationale` is REFUSED at the point of use -- a bound
    with no stated reason is a number nobody can argue with later, which is
    the exact gap `model/sound_report.py`'s `LOCK`/`LOCKS` mechanism leaves
    today (a lock can be overwritten with no record of why it moved).

    A bound that differs from the last one `accept()`-ed for the same metric
    (tracked in `history_path`, a small append-in-place ledger keyed by
    metric name) is recorded in this call's `bound_changes`, carrying BOTH
    the previous and the new bound and both their rationales.
    """
    history_path = history_path or (jobs_dir / "_bounds_history.json")
    history = json.loads(history_path.read_text()) if history_path.exists() else {}

    for name, crit in criteria.items():
        if not str(crit.get("rationale", "")).strip():
            raise Refused(
                f"acceptance bound for {name!r} has no rationale -- a bound with "
                f"no stated reason is a number nobody can argue with later, which "
                f"is the exact gap this stage exists to close")

    verdicts, changes = {}, []
    for name, crit in criteria.items():
        lo, hi, rationale = crit["lo"], crit["hi"], crit["rationale"]
        m = analysis["measurements"].get(name)
        prev = history.get(name)
        if prev is not None and (prev["lo"], prev["hi"]) != (lo, hi):
            changes.append({
                "metric": name,
                "previous": {"lo": prev["lo"], "hi": prev["hi"],
                             "rationale": prev["rationale"]},
                "new": {"lo": lo, "hi": hi, "rationale": rationale},
            })
        if m is None:
            verdicts[name] = {"state": "no verdict",
                               "why": "no such measurement in this analysis",
                               "bounds": {"lo": lo, "hi": hi}, "rationale": rationale}
        elif not isinstance(m.get("value"), (int, float)):
            verdicts[name] = {"state": "no verdict",
                               "why": m.get("why", "invalid measurement"),
                               "bounds": {"lo": lo, "hi": hi}, "rationale": rationale,
                               "measurement": m}
        else:
            v = m["value"]
            verdicts[name] = {"state": "pass" if lo <= v <= hi else "fail",
                               "value": v, "bounds": {"lo": lo, "hi": hi},
                               "rationale": rationale, "measurement": m}
        history[name] = {"lo": lo, "hi": hi, "rationale": rationale,
                          "job_id": analysis["job_id"], "accepted_at": provenance.now()}

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history, indent=2, sort_keys=True))

    record = {
        "stage": "accept",
        "job_id": analysis["job_id"],
        "render_id": analysis["render_id"],
        "verdicts": verdicts,
        "bound_changes": changes,
        "provenance": {"run_at": provenance.now()},
    }
    out_dir = jobs_dir / analysis["job_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "accept.json").write_text(json.dumps(record, indent=2, sort_keys=True))
    return record
