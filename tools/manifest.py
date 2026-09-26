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
    retained WAV without re-rendering it. It asserts its own preconditions
    (non-finite samples, exact silence and clipping are REFUSED unless the
    call says they are intended, and the requested peak plus clipped-sample
    count are recorded either way), and refuses to overwrite an existing
    `runs/<render_id>/` whose recorded `wav_sha256` differs from what it just
    produced.
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
    that differs from the last one accepted for the same **(case, metric)**
    pair is recorded with BOTH the old and new bound and both their
    rationales -- the gap `model/sound_report.py`'s `LOCK`/`LOCKS` leaves
    today, where a lock can be overwritten with no record of why. That ledger
    lives in committed source (`spec/acceptance-bounds-history.json`), not
    under the gitignored `jobs/`, because a previous bound that does not
    survive a fresh checkout detects nothing.
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
# The acceptance-bound ledger. Deliberately NOT under `jobs/`: `runs/` and
# `jobs/` are gitignored CI artifacts, and a bound-change ledger that does not
# survive a fresh checkout detects no bound change at all. See `accept()`.
BOUNDS_HISTORY = ROOT / "spec" / "acceptance-bounds-history.json"


class Refused(Exception):
    """A precondition of this stage was not met. `REFUSED` is a first-class
    outcome here (CLAUDE.md), distinct from a measurement that ran and
    failed its bounds -- never silently absorbed into a number."""


def _canonical(obj):
    """Rewrite `obj` into a structure whose JSON encoding is a *faithful and
    stable* identity for it, and REFUSE anything for which no such encoding
    exists.

    This function is the whole reason `render_id` / `job_id` mean anything, so
    the two ways it can be wrong are worth naming. The first implementation
    here used `json.dumps(..., default=str)`, which was wrong in BOTH
    directions at once:

      * **Collision.** `str(np.arange(10000))` elides the middle of the array,
        so `np.arange(10000)` and the same array with element 5000 changed
        produced the *same* hash -- two distinct renders sharing one
        `runs/<render_id>/` directory.
      * **Non-determinism.** For any object whose `repr` carries its memory
        address, `str()` differs on every call, so the same source and the
        same config produced a NEW `render_id` every time -- which defeats
        retroactive reanalysis, the one thing this module exists for.

    So: arrays are identified by their exact bytes (plus dtype and shape),
    and a type with no faithful encoding is `Refused` rather than
    approximated. A config whose identity cannot be computed is a precondition
    failure, not a number to be guessed at.
    """
    if isinstance(obj, np.ndarray):
        if obj.dtype.hasobject:
            raise Refused(
                "config contains an object-dtype numpy array, whose bytes are "
                "pointers: no reproducible identity can be computed for it -- "
                "restate it as plain data in `config`")
        a = np.ascontiguousarray(obj)
        return {"__ndarray__": {"dtype": a.dtype.str, "shape": list(a.shape),
                                 "bytes_sha256": hashlib.sha256(a.tobytes()).hexdigest()}}
    if isinstance(obj, np.generic):
        return _canonical(obj.item())
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _canonical(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_canonical(v) for v in obj]
    raise Refused(
        f"config value of type {type(obj).__name__!r} has no reproducible JSON "
        f"identity (its repr may carry a memory address, and str() may elide "
        f"content) -- restate it as plain data in `config` so two renders of the "
        f"same thing hash the same and two renders of different things do not")


def _hash_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(_canonical(obj), sort_keys=True).encode()).hexdigest()[:12]


def _dumps(obj) -> str:
    """Serialise a record for disk using the SAME identity rules as
    `_hash_json`: an array that the id hashed by its exact bytes is written to
    the record as those bytes' hash, never as an elided `str()`. A value with
    no faithful encoding is `Refused` here too, so a record on disk can never
    disagree with the id that names it."""
    return json.dumps(obj, indent=2, sort_keys=True, default=_canonical)


def _write_wav16(path: pathlib.Path, x, sr: int) -> dict:
    """Write `x` as the 16-bit retained artefact and REPORT what quantising it
    cost -- the peak it was handed and how many samples did not fit.

    `render()` records both in the manifest. A render stage that hard-clips
    silently is CLAUDE.md's "correct instrument in a wrong state" with a new
    instrument, and the clip is invisible in the WAV afterwards: every sample
    that was over full scale reads back as exactly +/-1.0.

    16 bits is a real floor. This is the *retained* artefact and everything
    downstream re-derives from it; anything needing more resolution than
    -96 dBFS (decay tails into the noise floor, in particular) should be
    retained as a float trace via `traces=`, not read back out of this WAV.

    Rounds rather than truncates: `astype("<i2")` alone truncates toward zero,
    a half-LSB *biased* quantisation applied to the one artefact everything
    else is derived from. `np.rint` is free.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    scaled = np.asarray(x, dtype=np.float64) * 32768.0
    clipped = int(np.count_nonzero((scaled < -32768.0) | (scaled > 32767.0)))
    y = np.rint(np.clip(scaled, -32768, 32767)).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())
    peak = float(np.max(np.abs(np.asarray(x, dtype=np.float64)))) if len(np.asarray(x)) else 0.0
    return {"peak": peak, "clipped_samples": clipped}


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
           traces: dict | None = None, allow_silence: bool = False,
           allow_clipping: bool = False) -> dict:
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

    PRECONDITIONS, asserted here rather than assumed (CLAUDE.md's canonical
    examples of an instrument in a wrong state are "a Model D rendering exact
    silence" and "an unlicensed Diva inserting clicks", both of which a render
    stage that accepts anything handed to it would have retained as data):

      * non-finite samples (NaN/inf) are REFUSED -- they quantise to garbage
        and every measurement downstream is then a number about nothing;
      * exact silence is REFUSED unless `allow_silence=True` says so;
      * clipping is REFUSED unless `allow_clipping=True` says so, and the
        requested peak plus the clipped-sample count are recorded in the
        manifest EITHER WAY (a clip is invisible in the WAV afterwards);
      * an existing `runs/<render_id>/manifest.json` whose `wav_sha256`
        differs from what this call just produced is REFUSED rather than
        overwritten. `load_render()` enforces "a retained WAV is what
        `render()` produced" on read; this enforces it on WRITE, which is the
        half that can actually prevent the loss -- an `analysis.json` already
        referencing that WAV by `render_wav_sha256` would otherwise be left
        pointing at different audio with nothing noticing.
    """
    result = render_fn()
    x, sr = result[0], result[1]
    x = np.asarray(x, dtype=np.float64)
    if x.ndim != 1:
        raise Refused(f"render_fn returned a {x.ndim}-dimensional array; this "
                       f"stage retains a single mono channel")
    if x.size == 0:
        raise Refused("render_fn returned no samples -- an empty render is not a "
                       "measurement of anything")
    n_bad = int(np.count_nonzero(~np.isfinite(x)))
    if n_bad:
        raise Refused(f"render_fn returned {n_bad} non-finite sample(s) of "
                       f"{x.size} (NaN or inf) -- refusing to retain a WAV in "
                       f"which those quantise to arbitrary values and every "
                       f"measurement taken from it would look like data")
    requested_peak = float(np.max(np.abs(x)))
    if requested_peak == 0.0 and not allow_silence:
        raise Refused(
            "render_fn returned exact silence (peak 0.0) -- pass "
            "allow_silence=True if silence is what this case is testing; "
            "otherwise this is the apparatus in a wrong state, not a result")
    wt = provenance.worktree_state(root=root)
    cfg_sha = _hash_json(config)
    rid = f"{case_id}-{wt['commit']}-{wt['uncommitted_sha256'][:8]}-{cfg_sha}"
    out_dir = runs_dir / rid
    wav_path = out_dir / "raw.wav"
    # Write to a sibling first: nothing already retained under this render id
    # is touched until the new audio has been hashed and compared against it.
    pending = out_dir / "raw.wav.pending"
    try:
        wav_stats = _write_wav16(pending, x, sr)
        if wav_stats["clipped_samples"] and not allow_clipping:
            raise Refused(
                f"render_fn returned a peak of {requested_peak:.6g} -- "
                f"{wav_stats['clipped_samples']} of {x.size} samples do not fit "
                f"the 16-bit retained WAV and would be hard-clipped to full "
                f"scale, invisibly. Scale the render, or pass "
                f"allow_clipping=True if the clipping is the thing under test")
        wav_hash = provenance.file_sha(pending)
        prior_path = out_dir / "manifest.json"
        if prior_path.exists():
            prior = json.loads(prior_path.read_text())
            if prior.get("wav_sha256") != wav_hash:
                raise Refused(
                    f"{prior_path} already records a DIFFERENT retained WAV for "
                    f"render id {rid} ({prior.get('wav_sha256')} recorded, "
                    f"{wav_hash} produced now) -- refusing to overwrite it. Two "
                    f"different renders share this id, so something the audio "
                    f"depends on is not restated in `config`; any analysis.json "
                    f"referencing the recorded hash would silently come to point "
                    f"at different audio")
        pending.replace(wav_path)
    finally:
        pending.unlink(missing_ok=True)
    trace_paths = _write_traces(out_dir, traces)
    manifest = {
        "stage": "render",
        "render_id": rid,
        "case_id": case_id,
        "config": config,
        "sample_rate": sr,
        "n_samples": int(len(x)),
        # What quantising to the 16-bit retained artefact cost, recorded
        # whether or not it was allowed: a clip is invisible afterwards.
        "requested_peak": requested_peak,
        "clipped_samples": wav_stats["clipped_samples"],
        "allow_silence": bool(allow_silence),
        "allow_clipping": bool(allow_clipping),
        # Relative to the render's OWN directory (`runs_dir/render_id/`), not
        # the repo root -- portable to wherever `runs_dir` actually lives.
        "wav_path": str(wav_path.relative_to(out_dir)),
        "wav_sha256": wav_hash,
        "trace_paths": trace_paths,
        "provenance": {"worktree": wt, "run_at": provenance.now()},
    }
    (out_dir / "manifest.json").write_text(_dumps(manifest))
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
    have = provenance.file_sha(wav_path)
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
    """The job id is a single path component under `jobs/`, so an analyser name
    carrying a path separator would silently nest directories (or, with `..`,
    escape `jobs_dir` entirely). REFUSED rather than quietly rewritten: an id
    that is not the name it was asked for is a worse outcome than an error."""
    for label, value in (("analyser", analyser), ("analyser_version", analyser_version)):
        if not str(value).strip():
            raise Refused(f"{label} is empty -- an analysis record with no named "
                           f"analyser cannot be told apart from any other")
        if any(c in str(value) for c in ("/", "\\", "\n")) or str(value) in (".", ".."):
            raise Refused(
                f"{label}={value!r} would not be a single path component under "
                f"jobs/ -- refusing to silently nest or escape the jobs directory")
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
    if "case_id" not in render_manifest:
        raise Refused(
            "render manifest carries no case_id -- it predates case_id being "
            "recorded, and accept()'s bound ledger cannot be keyed by case "
            "without it; re-render the case rather than analysing it under an "
            "unknown case id")
    for m in measurements:
        validate_measurement(m)
    aid = analysis_id(render_manifest, analyser, analyser_version, config)
    out_dir = jobs_dir / aid
    trace_paths = _write_traces(out_dir, traces)
    record = {
        "stage": "analyse",
        "job_id": aid,
        # The case this analysis is OF. Carried here because it is the field a
        # human reads first, and because `accept()` needs it: its bound ledger
        # must be keyed by (case_id, metric), not by metric alone -- sixteen
        # drum voices each carry a `T20`, and a ledger keyed by metric name
        # reports a fabricated bound change every time the case changes.
        "case_id": render_manifest["case_id"],
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
    (out_dir / "analysis.json").write_text(_dumps(record))
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

    A bound that differs from the last one `accept()`-ed **for the same
    (case, metric) pair** (tracked in `history_path`, nested `{case_id:
    {metric: bound}}`) is recorded in this call's `bound_changes`, carrying
    both bounds, both rationales, and the `job_id` that set the previous one --
    "which run set that bound" being the first thing a reader wants.

    The (case_id, metric) key is load-bearing, not tidiness: keyed by metric
    alone, a scorecard's sixteen drum voices -- each carrying a `T20` -- made
    every `accept()` report a bound change that had not happened, case B's
    bound "replacing" case A's. A ledger that cries wolf is exactly CLAUDE.md's
    "trains everyone to ignore gates, including the ones that work".

    WHERE THE LEDGER LIVES, and why it is not under `jobs/`. Bound-change
    detection is only worth anything if the previous bound is still there on
    the next run, so the ledger defaults to `BOUNDS_HISTORY`, a **tracked**
    file in committed source -- the same property that makes
    `model/sound_report.py`'s `LOCKS` work. Defaulting it into the gitignored
    `jobs/` tree (as this module first did) left detection working only inside
    one long-lived worktree: on a fresh checkout or any CI run the history is
    empty, so no bound change is ever detected and the gate silently passes
    everything. A bound moving shows up as a diff in review, which is the
    point. Pass `history_path=` explicitly for a scratch ledger (tests do).
    """
    history_path = history_path or BOUNDS_HISTORY
    history = json.loads(history_path.read_text()) if history_path.exists() else {}
    if "case_id" not in analysis:
        raise Refused(
            "analysis record carries no case_id -- the bound ledger is keyed by "
            "(case_id, metric), and keying it by metric alone fabricates bound "
            "changes between unrelated cases; re-run analyse() on a render "
            "manifest that records its case")
    case_id = analysis["case_id"]

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
        prev = history.get(case_id, {}).get(name)
        if prev is not None and (prev["lo"], prev["hi"]) != (lo, hi):
            changes.append({
                "case_id": case_id,
                "metric": name,
                "previous": {"lo": prev["lo"], "hi": prev["hi"],
                             "rationale": prev["rationale"],
                             "job_id": prev.get("job_id"),
                             "accepted_at": prev.get("accepted_at")},
                "new": {"lo": lo, "hi": hi, "rationale": rationale,
                        "job_id": analysis["job_id"]},
            })
        if m is None:
            verdicts[name] = {"state": "no verdict",
                               "why": "no such measurement in this analysis",
                               "bounds": {"lo": lo, "hi": hi}, "rationale": rationale}
        elif isinstance(m.get("value"), bool) or not isinstance(m.get("value"), (int, float)):
            # `bool` subclasses `int`: True would otherwise compare against the
            # bounds as 1.0 and be reported as a measured value.
            verdicts[name] = {"state": "no verdict",
                               "why": m.get("why", "invalid measurement"),
                               "bounds": {"lo": lo, "hi": hi}, "rationale": rationale,
                               "measurement": m}
        else:
            v = m["value"]
            verdicts[name] = {"state": "pass" if lo <= v <= hi else "fail",
                               "value": v, "bounds": {"lo": lo, "hi": hi},
                               "rationale": rationale, "measurement": m}
        history.setdefault(case_id, {})[name] = {
            "lo": lo, "hi": hi, "rationale": rationale,
            "job_id": analysis["job_id"], "accepted_at": provenance.now()}

    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(_dumps(history))

    record = {
        "stage": "accept",
        "job_id": analysis["job_id"],
        "case_id": case_id,
        "render_id": analysis["render_id"],
        "verdicts": verdicts,
        "bound_changes": changes,
        "bounds_history_path": str(history_path),
        "provenance": {"run_at": provenance.now()},
    }
    out_dir = jobs_dir / analysis["job_id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "accept.json").write_text(_dumps(record))
    return record
