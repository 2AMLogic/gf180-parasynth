#!/usr/bin/env python3
"""The bounded first experiment of #158: a frozen challenge set, a detector
coverage matrix, and one report per estimator (#521).

    python3 tools/probes/challenge_set.py check           # definition + staleness
    python3 tools/probes/challenge_set.py run             # the experiment
    python3 tools/probes/challenge_set.py report          # re-render the committed results
    python3 tools/probes/challenge_set.py freeze          # fill hashes; a visible re-freeze

WHAT THIS IS
------------
Integration, not a new instrument. It calls the four pieces #517-#520 built
and adds only the selection and the bookkeeping:

  #517  tools/probes/estimator_ground_truth.py   known answers (bias, error)
  #518  tools/probes/perturbation_ladder.py      perturbations and the five
                                                 questions, on REAL anchors
  #519  tools/probes/permitted_differences.py    permitted transforms, and the
                                                 synthetic false-alarm suite
  #520  docs/corpus-lineage.json                 which split a recording may be in

plus `tools/probes/audio_distance_floor.py` for `mel_dac`, its floor and #158's
three named bass-drum escapes. Fixture generation, perturbation and permitted-
difference logic are imported from those files, never copied; where a sibling
lacks something this file needed, the gap is stated below rather than filled.

The challenge set itself is DATA: `docs/challenge-set.json`. It names ~24 real
anchors over four categories, freezes each by the sha256 of its bytes, assigns
each to ONE split by source recording, and freezes the perturbation strengths,
permitted transforms, corrections and estimator resolutions it was run with.

OUTCOMES
--------
A matrix cell is one of

    MOVED          the estimator's reading moved beyond its declared resolution
                   (or, for mel_dac, beyond 1 x its floor) on at least one
                   scored rung of an injected defect -- it protects against it
    ABSTAINS       nothing moved but the defect made it REFUSE on some rung.
                   That is protection: a refused required metric makes a
                   scorecard case NO_VERDICT, never PASS (scorecard.evaluate)
    BLIND          scored rungs exist, none moved and none refused -- it does
                   not see this defect
    REFUSES        a permitted difference made it refuse: a false no-verdict
    STILL          a permitted difference, and nothing moved -- correct
    FALSE-ALARM    a permitted difference moved it
    TRACKS         a genuine pitch correction moved it by the closed-form
                   amount, within resolution
    MISTRACKS      ...by some other amount: an invented artefact
    NOT-DERIVABLE  a correction whose expected effect has no closed form for
                   this estimator (perturbation_ladder.RELATION_OVERRIDES)
    FLAGS-CORRECTION  mel_dac read a genuine correction above its floor
    NO-VERDICT     nothing was scored -- every rung refused, or the transform
                   did not change the record

and every cell carries its counts (`moved/scored`, `no_verdict`), so a label
is never read without the population it summarises. NO-VERDICT is not BLIND:
a tail-noise injection that starts after a 0.25 s closed hat has ended changes
nothing, and counting it as a miss would blame the estimator for a defect that
was never there.

`run` exits 0 when every frozen anchor was measured, 2 when some were REFUSED
(a partial run, labelled PARTIAL in its own results), and 3 when the
definition itself fails `check` -- in which case nothing is run, because a
challenge set that is not the frozen one answers a different question.

WHAT IT DOES NOT CLAIM
----------------------
* **Not validation of the instrument.** It validates the JUDGES. And the
  per-property columns are graded against the PHYSICS of each perturbation
  (perturbation_ladder.py's frame), not against an external truth about the
  recording, which nobody has.
* **Not one quality number.** There is no aggregate anywhere in the output, by
  design (#158: "one report per estimator -- not an overall quality number").
* **"The challenge set stays meaningful as estimators evolve" is OUT-OF-BAND.**
  It is a claim about the future and a green run today cannot establish it.
  What this file does establish is the mechanism: the results carry the
  apparatus identity they were measured under, and `check` reports them STALE
  the moment an estimator file changes. Whether the anchors, strengths and
  columns are still the right ones after the next estimator repair is a
  judgement somebody has to make then.

THE RE-MEASUREMENT RULE (#157/#159's, extended)
-----------------------------------------------
`tools/scorecard.py` `compare()` already refuses to compare two results
measured under different apparatus: INCOMPARABLE, "re-measure the baseline
before comparing". The apparatus there is identified by content hash, and
`model/audio_measure.py` and `tools/run_case.py` are its two required members.
This file uses the same identity -- those two files, asserted present in
`scorecard.APPARATUS` -- plus the probe files that define the challenge, and
applies the same rule to itself: results whose apparatus differs from the
live tree are STALE, and the remedy is `run`, which re-measures the RETAINED
anchors (frozen by sha256, so they are the same audio) under the repaired
estimator. `check` exits 2 on STALE. It is deliberately not a pytest gate:
every estimator PR would then go red on a host without the corpus, which is
the unsatisfiable-gate failure docs/failure-modes.md records.

START RED
---------
`tools/probes/test_challenge_set.py` was written first and run against a stub
with these ports and no behaviour: 31 failed, 5 passed. The five are a data
check on the definition and the negative halves of pairs whose positive half
failed. One more had passed VACUOUSLY on the stub (`{} == {}` for the
determinism test) and was tightened before any implementation existed.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import io
import json
import math
import pathlib
import re
import subprocess
import sys
import zipfile

import numpy as np
from scipy.io import wavfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (ROOT / "model", ROOT / "tools", ROOT / "tools" / "probes"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import audio_distance_floor as adf                                    # noqa: E402
import audio_measure as am                                            # noqa: E402
import estimator_fixtures as ef                                       # noqa: E402
import permitted_differences as pdiff                                 # noqa: E402
import perturbation_ladder as pl                                      # noqa: E402
import run_case as rc                                                 # noqa: E402
import scorecard as sc                                                # noqa: E402

DEFINITION = ROOT / "docs" / "challenge-set.json"
RESULTS = ROOT / "docs" / "challenge-set-results.json"
LINEAGE = ROOT / "docs" / "corpus-lineage.json"

#: The scorecard's apparatus members the estimators under test live in. Asserted
#: to BE in `scorecard.APPARATUS` at import: if the scorecard ever stopped
#: treating them as apparatus, the two re-measurement rules would quietly mean
#: different things.
SHARED_APPARATUS = ("model/audio_measure.py", "tools/run_case.py")
assert all(p in sc.APPARATUS for p in SHARED_APPARATUS), \
    "scorecard.APPARATUS no longer names the estimator modules"
PROBE_APPARATUS = (
    "tools/probes/perturbation_ladder.py",
    "tools/probes/permitted_differences.py",
    "tools/probes/estimator_ground_truth.py",
    "tools/probes/estimator_fixtures.py",
    "tools/probes/estimator_domains.py",
    "tools/probes/audio_distance_floor.py",
    "tools/probes/challenge_set.py",
    "docs/challenge-set.json",
)

LEGOWELT_ROOT = pathlib.Path("/tmp/legowelt")          # model/moog_probe.py --set default
REFPROFILE_ZIP = ROOT / "refprofile" / "frozen-cache.zip"

#: Report lists are capped so the committed results stay readable; the cap is
#: printed beside every list it truncates, with the full count.
EXAMPLES = 6

DEFECT, PERMITTED, CORRECTION = "defect", "permitted", "correction"
SIG = 9            # significant digits kept in results


class Refused(Exception):
    """A precondition failed, so nothing was measured. Not a failure."""


def _r(v):
    if v is None:
        return None
    v = float(v)
    if not math.isfinite(v):
        return None
    return float(f"{v:.{SIG}g}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ===========================================================================
# 1. the definition, its freeze and its rules
# ===========================================================================
def load_definition(path: pathlib.Path = DEFINITION) -> dict:
    path = pathlib.Path(path)
    if not path.exists():
        raise Refused(f"no challenge-set definition at {path}")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise Refused(f"{path} is not JSON: {e}") from e


def definition_sha256(defn: dict) -> str:
    """Everything but the `freeze` block, canonically serialised. An anchor's
    sha256 is INSIDE the digest, so filling one in is a re-freeze."""
    body = {k: v for k, v in defn.items() if k != "freeze"}
    return _sha(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def fixture_digest(fixtures=None) -> tuple[int, str]:
    fixtures = ef.catalogue() if fixtures is None else fixtures
    h = hashlib.sha256()
    for fx in fixtures:
        h.update(fx.kind.encode())
        h.update(fx.label.encode())
        h.update(np.ascontiguousarray(np.asarray(fx.x, dtype=np.float64)).tobytes())
    return len(fixtures), h.hexdigest()


def _finding(rule: str, ok: bool, what: str) -> dict:
    return dict(rule=rule, ok=bool(ok), what=what)


def _source_kind(defn: dict, pack: str) -> str | None:
    s = defn.get("sources", {}).get(pack)
    return s.get("kind") if s else None


def check_definition(defn: dict, lineage: dict | None = None, *,
                     with_fixtures: bool = True) -> list[dict]:
    """Seven rules. Every one reports, pass or fail, so an empty list can never
    be mistaken for a clean bill."""
    lineage = json.loads(LINEAGE.read_text()) if lineage is None else lineage
    packs = {p["id"]: p for p in lineage.get("packs", [])}
    out: list[dict] = []
    anchors = defn.get("anchors", [])
    splits = defn.get("splits", {})
    cats = defn.get("categories", {})

    # C1 -- the freeze
    want = (defn.get("freeze") or {}).get("definition_sha256")
    got = definition_sha256(defn)
    out.append(_finding("C1", want == got,
                        "definition_sha256 matches the content" if want == got else
                        f"definition edited without a re-freeze: frozen {want}, content {got}"))

    # C2 -- size and coverage
    ids = [a.get("id") for a in anchors]
    counts = {c: sum(1 for a in anchors if a.get("category") == c) for c in cats}
    bad_cat = sorted({a.get("category") for a in anchors} - set(cats))
    ok2 = (20 <= len(anchors) <= 28 and all(n >= 3 for n in counts.values())
           and not bad_cat and len(set(ids)) == len(ids) and len(cats) == 4)
    out.append(_finding("C2", ok2, f"{len(anchors)} anchors, per category {counts}"
                        + (f", unknown categories {bad_cat}" if bad_cat else "")
                        + ("" if len(set(ids)) == len(ids) else ", DUPLICATE ids")))

    # C3 -- each anchor is well-formed: a known split, and frozen bytes or a
    # stated reason it is not
    bad3 = []
    for a in anchors:
        if a.get("split") not in splits:
            bad3.append(f"{a.get('id')}: split {a.get('split')!r}")
        sha = a.get("sha256")
        if sha is None:
            if not str(a.get("unfrozen_why") or "").strip():
                bad3.append(f"{a.get('id')}: unfrozen with no unfrozen_why")
        elif not re.fullmatch(r"[0-9a-f]{64}", str(sha)):
            bad3.append(f"{a.get('id')}: sha256 {sha!r} is not a sha256")
        if a.get("pack") not in defn.get("sources", {}):
            bad3.append(f"{a.get('id')}: pack {a.get('pack')!r} has no source")
    out.append(_finding("C3", not bad3, "; ".join(bad3) or
                        f"every anchor has a split and frozen bytes or a stated reason "
                        f"({sum(1 for a in anchors if a.get('sha256') is None)} unfrozen)"))

    # C4 -- the split is one the anchor's lineage may be read in
    bad4 = []
    for a in anchors:
        split, pack = a.get("split"), a.get("pack")
        group = splits.get(split)
        if _source_kind(defn, pack) == "zip":
            # a committed software reference: outside corpus-lineage by that
            # manifest's own out_of_scope entry, and development-only here
            if split != "development":
                bad4.append(f"{a.get('id')}: software reference in {split}")
            continue
        p = packs.get(pack)
        if p is None:
            bad4.append(f"{a.get('id')}: pack {pack!r} is not in {LINEAGE.name}")
            continue
        if group not in (p.get("roles") or []):
            bad4.append(f"{a.get('id')}: {pack} has no {group} role")
        status = p.get("lineage_status")
        if group == "held-out-validation" and status != "documented":
            bad4.append(f"{a.get('id')}: a {status} lineage may not back a verdict")
        if status == "unknown" and group != "analyzer-development":
            bad4.append(f"{a.get('id')}: an unknown lineage is development-only")
    out.append(_finding("C4", not bad4, "; ".join(bad4) or
                        "every split is admitted by the anchor's lineage group"))

    # C5 -- one recording, one place. Identity is CONTENT: the same bytes under
    # a second path are the same recording (a re-pressing), and a path test
    # alone passes them.
    bad5, by_sha, by_path = [], {}, {}
    for a in anchors:
        key = (_source_kind(defn, a.get("pack")), a.get("pack"), a.get("path"))
        if key in by_path:
            bad5.append(f"{a.get('id')} and {by_path[key]} are the same path")
        by_path[key] = a.get("id")
        sha = a.get("sha256")
        if sha:
            if sha in by_sha:
                bad5.append(f"{a.get('id')} and {by_sha[sha]} are the same bytes")
            by_sha[sha] = a.get("id")
    out.append(_finding("C5", not bad5, "; ".join(bad5) or
                        "no recording appears twice, by path or by content"))

    # C6 -- nothing excluded is an anchor
    bad6 = []
    for ex in defn.get("excluded", []):
        for a in anchors:
            if a.get("pack") == ex.get("pack") and ex.get("path") in ("*", a.get("path")):
                bad6.append(f"{a.get('id')} is excluded: {ex.get('why')}")
    out.append(_finding("C6", not bad6, "; ".join(bad6) or
                        f"none of {len(defn.get('excluded', []))} excluded recordings is an anchor"))

    # C7 -- the frozen apparatus is the live apparatus
    bad7 = []
    live_cases = {c.name: c for c in pl.ESTIMATOR_CASES}
    for name, spec in defn.get("estimators", {}).items():
        if spec.get("source") == "perturbation_ladder.ESTIMATOR_CASES":
            c = live_cases.get(name)
            if c is None:
                bad7.append(f"{name}: not in perturbation_ladder.ESTIMATOR_CASES")
            elif (c.resolution != spec.get("resolution")
                  or c.relative != spec.get("relative")):
                bad7.append(f"{name}: frozen resolution {spec.get('resolution')} "
                            f"relative={spec.get('relative')}, live {c.resolution} "
                            f"relative={c.relative}")
    mel = defn.get("estimators", {}).get("mel_dac", {})
    if [list(t) for t in adf.DAC_MEL] != mel.get("dac_mel"):
        bad7.append(f"mel_dac: frozen DAC_MEL {mel.get('dac_mel')}, live {adf.DAC_MEL}")
    if adf.LOG_FLOOR_REL != mel.get("log_floor_rel"):
        bad7.append(f"mel_dac: frozen log floor {mel.get('log_floor_rel')}, "
                    f"live {adf.LOG_FLOOR_REL}")
    for cls in (DEFECT, PERMITTED, CORRECTION):
        for col, spec in defn.get("columns", {}).get(cls, {}).items():
            src = spec.get("source", "")
            if src == "perturbation_ladder.PERTURBATIONS":
                live = pl.PERTURBATIONS.get(col)
                if live is None or list(live.strengths) != spec.get("strengths"):
                    bad7.append(f"{col}: frozen {spec.get('strengths')}, live "
                                f"{None if live is None else list(live.strengths)}")
            elif src == "permitted_differences.TRANSFORMS":
                if spec.get("transform") not in pdiff.TRANSFORMS:
                    bad7.append(f"{col}: no transform {spec.get('transform')!r}")
            elif src.startswith("audio_distance_floor."):
                if not callable(getattr(adf, src.split(".", 1)[1], None)):
                    bad7.append(f"{col}: {src} does not exist")
            elif src == "perturbation_ladder.perturb_resample":
                spread = adf.MACHINE_FLOOR["f0_pct"] / 100.0
                wide = [r for r in spec.get("ratios", []) if abs(r - 1.0) > spread + 1e-4]
                if wide:
                    bad7.append(f"{col}: ratios {wide} are outside the machine's own "
                                f"{adf.MACHINE_FLOOR['f0_pct']} % f0 spread, so they are "
                                f"not corrections the board passes")
            else:
                bad7.append(f"{col}: unknown source {src!r}")
    if with_fixtures:
        syn = defn.get("synthetic", {})
        n, dig = fixture_digest()
        if n != syn.get("fixture_count") or dig != syn.get("fixture_digest"):
            bad7.append(f"synthetic fixtures: frozen {syn.get('fixture_count')} / "
                        f"{syn.get('fixture_digest')}, live {n} / {dig}")
    out.append(_finding("C7", not bad7, "; ".join(bad7) or
                        "frozen strengths, transforms, resolutions, mel_dac "
                        "configuration and fixture digest equal the live modules"))
    return out


# ===========================================================================
# 2. the apparatus identity, and staleness
# ===========================================================================
def apparatus_identity() -> dict:
    ident = {}
    for rel in sorted(set(SHARED_APPARATUS) | set(PROBE_APPARATUS)):
        p = ROOT / rel
        ident[rel] = _sha(p.read_bytes()) if p.exists() else "absent"
    return ident


def staleness(results: dict, identity: dict | None = None) -> list[str]:
    """Paths whose content differs between the results' apparatus and the live
    one. A results file with no apparatus record is stale, not fresh: it cannot
    say what measured it."""
    identity = apparatus_identity() if identity is None else identity
    old = results.get("apparatus")
    if not old:
        return ["<results carry no apparatus record>"]
    return sorted(k for k in set(old) | set(identity) if old.get(k) != identity.get(k))


# ===========================================================================
# 3. anchors -- loaded only if they are the frozen bytes
# ===========================================================================
def _default_root(defn: dict, pack: str) -> pathlib.Path:
    if _source_kind(defn, pack) == "zip":
        return REFPROFILE_ZIP
    if pack == "legowelt-minimoog-5529":
        return LEGOWELT_ROOT
    return rc.configured_refs()


def _decode(data: bytes, what: str) -> tuple[np.ndarray, int]:
    sr, x = wavfile.read(io.BytesIO(data))
    if np.issubdtype(x.dtype, np.integer):
        scale = float(np.iinfo(x.dtype).max) + 1.0
        x = (x.astype(np.float64) - (scale / 2.0 if x.dtype == np.uint8 else 0.0)) / (
            scale / 2.0 if x.dtype == np.uint8 else scale)
    else:
        x = x.astype(np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    return x, int(sr)


def read_anchor_bytes(anchor: dict, defn: dict, roots: dict | None = None) -> bytes:
    pack = anchor["pack"]
    root = pathlib.Path((roots or {}).get(pack) or _default_root(defn, pack))
    if _source_kind(defn, pack) == "zip":
        if not root.exists():
            raise Refused(f"{anchor['id']}: archive {root} is absent")
        with zipfile.ZipFile(root) as z:
            try:
                return z.read(anchor["path"])
            except KeyError:
                raise Refused(f"{anchor['id']}: {anchor['path']} is not in {root}") from None
    if not root.exists():
        raise Refused(f"{anchor['id']}: corpus for {pack} not at {root}")
    p = root / anchor["path"]
    if not p.exists():
        raise Refused(f"{anchor['id']}: {anchor['path']} missing under {root}")
    return p.read_bytes()


def load_anchor(anchor: dict, defn: dict, roots: dict | None = None) -> tuple[np.ndarray, int]:
    if not anchor.get("sha256"):
        raise Refused(f"{anchor['id']}: not frozen -- {anchor.get('unfrozen_why', '')}")
    data = read_anchor_bytes(anchor, defn, roots)
    got = _sha(data)
    if got != anchor["sha256"]:
        raise Refused(f"{anchor['id']}: sha256 {got} is not the frozen "
                      f"{anchor['sha256']} -- a different recording under the same name")
    x, sr = _decode(data, anchor["id"])
    try:
        am.require_finite(x, anchor["id"])
    except am.NonFiniteAudio as e:
        raise Refused(str(e)) from e
    if am.is_silent(x):
        raise Refused(f"{anchor['id']}: silent")
    return x, sr


# ===========================================================================
# 4. mel_dac and its floor
# ===========================================================================
def prepare_for_distance(x: np.ndarray, sr: int, *, strike: bool = True) -> np.ndarray:
    """audio_distance_floor.py E8's convention: run_case.prepare, then peak
    normalisation -- for a STRIKE. `prepare` trims to a guaranteed pre-onset
    lead and REFUSES a record that begins inside its own event, which every
    held tone does ("cut into the strike"); the first run lost the whole
    sustained category to that refusal. A held tone is therefore peak-
    normalised only, as the definition's `prepare_categories` states."""
    x = np.asarray(x, dtype=np.float64)
    try:
        am.require_finite(x, "the anchor")
    except am.NonFiniteAudio as e:
        raise Refused(str(e)) from e
    if not strike:
        return adf.norm(x)
    try:
        return adf.norm(rc.prepare(x, sr, side="the anchor"))
    except rc.Refused as e:
        raise Refused(str(e)) from e


def mel_distance(p: np.ndarray, y: np.ndarray, sr: int) -> float:
    """`_mel_dac` at the anchor's OWN rate, on the pair zero-padded to one
    length the way `audio_distance_floor.distances()` pairs them (`_pair`)."""
    a, b = adf._pair(np.asarray(p, dtype=np.float64), np.asarray(y, dtype=np.float64))
    return float(adf._mel_dac(a, b, sr=sr))


def mel_floor(p: np.ndarray, sr: int) -> float:
    """floor = mel_dac(p, shift(p, 1)). REFUSES a floor that is not a positive
    finite number: every reading is reported against it, and a zero floor makes
    every ratio infinite and every rung 'detected' (the defeating input is
    digital silence, whose one-sample shift is itself)."""
    p = np.asarray(p, dtype=np.float64)
    if not np.all(np.isfinite(p)):
        raise Refused("the prepared anchor is not finite, so it has no floor")
    f = mel_distance(p, adf.shift(p, 1), sr)
    if not (math.isfinite(f) and f > 0.0):
        raise Refused(f"mel_dac floor is {f!r}: a ratio to it is undefined")
    return f


# ===========================================================================
# 5. one anchor
# ===========================================================================
@dataclasses.dataclass(frozen=True)
class _Tone:
    tone_hz: float


def _columns(defn: dict):
    for cls in (DEFECT, PERMITTED, CORRECTION):
        for col, spec in defn["columns"][cls].items():
            yield cls, col, spec


def _pd_draws(anchor_id: str, spec: dict, defn: dict, amp: float):
    """The permitted_differences transform, drawn deterministically in this
    file's own seed namespace. Yields (trial, info, post)."""
    fn = pdiff.TRANSFORMS[spec["transform"]][0]
    for trial in range(int(spec.get("trials", 1))):
        rng = np.random.default_rng([defn["seeds"]["pd_transform_base"], trial,
                                     pdiff.case_seed(f"{anchor_id}/{spec['transform']}")])
        applied = fn({"amp": amp}, rng)
        yield trial, {k: _r(v) for k, v in applied.info.items()}, applied.post


def _variants(cls, col, spec, x, sr, anchor_id, defn, tone_case):
    """Every (label, transformed record) of one column, in strength order. The
    transform functions are the siblings' own; this only enumerates them."""
    src = spec["source"]
    if src == "perturbation_ladder.PERTURBATIONS":
        pert = pl.PERTURBATIONS[col]
        for s in pert.strengths:
            if s == pert.no_op:
                yield dict(strength=_r(s), no_op=True), None
                continue
            yield dict(strength=_r(s)), pert.apply(x, sr, s, tone_case)
    elif src.startswith("audio_distance_floor."):
        fn = getattr(adf, src.split(".", 1)[1])
        yield dict(strength="named"), fn(x, sr, **spec.get("args", {}))
    elif src == "permitted_differences.TRANSFORMS":
        amp = float(np.max(np.abs(x))) if len(x) else 0.0
        for trial, info, post in _pd_draws(anchor_id, spec, defn, amp):
            yield dict(strength=f"trial {trial}", info=info), post(pdiff.Signal(x, sr, {})).x
    elif src == "perturbation_ladder.perturb_resample":
        for a in spec["ratios"]:
            yield dict(strength=_r(a)), pl.perturb_resample(x, sr, a)
    else:                                               # pragma: no cover
        raise ValueError(src)


#: A transform that changes less than this much of the record has not
#: applied the defect it names. 1 ms, stated and not tuned.
MIN_CHANGED_S = 1e-3


def _unchanged(x, y, sr: int) -> str | None:
    """Why this transform is not a test of anything, or None.

    The first version asked only `np.array_equal`, and the run defeated it:
    the ladder's tail noise is confined to after the record's sounding extent,
    which on seven prepared Fischer anchors is the last ONE OR TWO SAMPLES.
    `mel_dac`'s STFT frames never reach them, so it read exactly 0.0 on 42
    rungs and the matrix called it BLIND to a defect that had changed two
    samples. A change confined to less than `MIN_CHANGED_S` of the record is
    now a NO-VERDICT, and that input is a committed test. Stated adversary it
    does not stop: a change spread over more than 1 ms at negligible level
    (the 80 dB SNR rung) is scored -- correctly, as a weak rung, not a no-op."""
    if y is None or len(y) != len(x):
        return None
    k = int(np.count_nonzero(np.asarray(x) != np.asarray(y)))
    if k == 0:
        return "the transform did not change the record"
    if k < max(1, int(round(MIN_CHANGED_S * sr))):
        return f"the transform changed only {k} sample(s), under {MIN_CHANGED_S * 1e3:g} ms"
    return None


def run_property(case: pl.EstimatorCase, anchor: dict, x: np.ndarray, sr: int,
                 defn: dict) -> dict:
    base = case.measure(x, sr)
    if not base.ok:
        return dict(status="BASE_REFUSED", why=base.reason)
    out = dict(status="OK", base_value=_r(base.value), resolution=case.resolution,
               relative=case.relative, ladder={}, columns={})

    # (a) #518's five questions, every perturbation, reused as they are
    for pname, pert in pl.PERTURBATIONS.items():
        relation, rows = pl.sweep_one(case, pert, x, sr, base)
        q = pl.five_questions(relation, case.resolution, case.relative, base.value, rows)
        out["ladder"][pname] = dict(relation=relation, questions=q,
                                    rows=[{k: (_r(v) if isinstance(v, float) else v)
                                           for k, v in r.items()} for r in rows])

    # (b) the matrix columns. A peak-normalised copy carries the named escapes,
    # which are defined in dBFS of a unit-peak record.
    pk = float(np.max(np.abs(x)))
    xn = x / pk
    base_n = case.measure(xn, sr)
    for cls, col, spec in _columns(defn):
        src = spec["source"]
        use, b = (xn, base_n) if src.startswith("audio_distance_floor.") else (x, base)
        rows = []
        if not b.ok:
            rows.append(dict(strength="all", why=f"base refused on the unit-peak copy: {b.reason}"))
            out["columns"][col] = rows
            continue
        for label, y in _variants(cls, col, spec, use, sr, anchor["id"], defn, case):
            row = dict(label)
            if row.get("no_op"):
                row["why"] = "no-op rung (the sweep's own identity point)"
                rows.append(row)
                continue
            why = _unchanged(use, y, sr)
            if why:
                row["why"] = why
                rows.append(row)
                continue
            got = case.measure(y, sr)
            if not got.ok:
                row["why"] = f"refused: {got.reason}"
                row["refused"] = True
                rows.append(row)
                continue
            delta = got.value - b.value
            row.update(value=_r(got.value), delta=_r(delta))
            if cls == CORRECTION:
                rel = pl.relation_for(case.name, "resample_ratio")
                exp = pl.expected_delta(rel, b.value, float(row["strength"]))
                row["expected"] = _r(exp)
                row["moved"] = pl._exceeds(delta, case.resolution, case.relative, b.value)
                row["tracks"] = (None if exp is None else not pl._exceeds(
                    delta - exp, case.resolution, case.relative, b.value))
            else:
                row["moved"] = pl._exceeds(delta, case.resolution, case.relative, b.value)
            rows.append(row)
        out["columns"][col] = rows
    return out


def run_mel(anchor: dict, x: np.ndarray, sr: int, defn: dict) -> dict:
    strike = anchor.get("category") in defn["estimators"]["mel_dac"]["prepare_categories"]
    try:
        p = prepare_for_distance(x, sr, strike=strike)
        floor = mel_floor(p, sr)
    except Refused as e:
        return dict(status="REFUSED", why=str(e))
    tone = _Tone(defn["estimators"]["mel_dac"]["ladder_tone_hz"])
    out = dict(status="OK", floor=_r(floor), columns={})
    for cls, col, spec in _columns(defn):
        rows = []
        for label, y in _variants(cls, col, spec, p, sr, anchor["id"], defn, tone):
            row = dict(label)
            if row.get("no_op"):
                row["why"] = "no-op rung (the sweep's own identity point)"
            elif _unchanged(p, y, sr):
                row["why"] = _unchanged(p, y, sr)
            else:
                d = mel_distance(p, adf.norm(y), sr)
                row.update(distance=_r(d), floor=_r(floor), ratio=_r(d / floor))
            rows.append(row)
        out["columns"][col] = rows
    return out


def applies(spec: dict, anchor: dict) -> bool:
    return (anchor.get("category") in (spec.get("categories") or [])
            or anchor.get("voice") in (spec.get("voices") or []))


def run_anchor(anchor: dict, x: np.ndarray, sr: int, defn: dict, *,
               estimators: list[str] | None = None, measures: dict | None = None,
               with_mel: bool = True) -> dict:
    """One anchor through every column. `estimators` overrides the declared
    remit; `measures` substitutes an estimator's measure function -- the hook
    the start-red controls use, never the shipped run."""
    cases = {c.name: c for c in pl.ESTIMATOR_CASES}
    if estimators is None:
        estimators = [n for n, s in defn["estimators"].items()
                      if n in cases and applies(s, anchor)]
    out = dict(id=anchor["id"], category=anchor["category"], split=anchor["split"],
               voice=anchor.get("voice"), pack=anchor["pack"], sr=int(sr),
               seconds=_r(len(x) / sr), status="OK", estimators={})
    for name in estimators:
        case = cases[name]
        if measures and name in measures:
            case = dataclasses.replace(case, measure=measures[name])
        out["estimators"][name] = run_property(case, anchor, x, sr, defn)
    if with_mel:
        out["mel_dac"] = run_mel(anchor, x, sr, defn)
    return out


# ===========================================================================
# 6. the matrix
# ===========================================================================
def _label(cls: str, moved: int, scored: int, tracked: int, mistracked: int,
           mel: bool, refused: int = 0) -> str:
    if cls == DEFECT:
        if moved:
            return "MOVED"
        if refused:
            # the defect turns the reading into a refusal, and a refused
            # required metric makes a scorecard case NO_VERDICT, never PASS
            return "ABSTAINS"
        return "BLIND" if scored else "NO-VERDICT"
    if cls == PERMITTED:
        if moved:
            return "FALSE-ALARM"
        if refused:
            return "REFUSES"
        return "STILL" if scored else "NO-VERDICT"
    if scored == 0:
        return "NO-VERDICT"
    if mel:
        return "FLAGS-CORRECTION" if moved else "STILL"
    if tracked + mistracked == 0:
        return "NOT-DERIVABLE"
    return "MISTRACKS" if mistracked else "TRACKS"


def _blank():
    return dict(scored=0, moved=0, no_verdict=0, refused=0, tracked=0, mistracked=0,
                anchors_refused=0)


def _tally(t: dict, row: dict, cls: str, mel: bool, k: float = 1.0):
    """A no-op rung is not counted at all: it is the sweep's identity point,
    not a test. A refusal is a no-verdict AND is counted separately, because
    on a defect column it is how an estimator protects by abstaining."""
    if row.get("no_op"):
        return
    if row.get("refused"):
        t["refused"] += 1
    if mel:
        if row.get("distance") is None:
            t["no_verdict"] += 1
            return
        t["scored"] += 1
        t["moved"] += int(row["distance"] > k * row["floor"])
        return
    if "moved" not in row:
        t["no_verdict"] += 1
        return
    t["scored"] += 1
    t["moved"] += int(bool(row["moved"]))
    if cls == CORRECTION and row.get("tracks") is not None:
        t["tracked" if row["tracks"] else "mistracked"] += 1


def coverage_matrix(anchor_results: list[dict], defn: dict) -> dict:
    """estimator x column. Each cell: label, counts, and the same counts per
    category and per split, so the instrument-family breakdown #158 asks for is
    the same table read sideways rather than a second computation."""
    names = [n for n in defn["estimators"]]
    m: dict = {}
    for name in names:
        mel = name == "mel_dac"
        m[name] = {}
        for cls, col, _ in _columns(defn):
            cell = dict(cls=cls, **_blank(), nv_reasons={}, by_category={}, by_split={})
            for ar in anchor_results:
                if ar.get("status") != "OK":
                    continue
                src = ar.get("mel_dac") if mel else ar.get("estimators", {}).get(name)
                if not src:
                    continue                     # outside this estimator's remit
                if src.get("status") != "OK":
                    # refused at BASE on this anchor: every column of it is a
                    # no-verdict, and the cell must say so rather than read nv0
                    for t in (cell, cell["by_category"].setdefault(ar["category"], _blank()),
                              cell["by_split"].setdefault(ar["split"], _blank())):
                        t["anchors_refused"] += 1
                    continue
                for row in src["columns"].get(col, []):
                    for t in (cell, cell["by_category"].setdefault(ar["category"], _blank()),
                              cell["by_split"].setdefault(ar["split"], _blank())):
                        _tally(t, row, cls, mel)
                    if row.get("why") and not row.get("no_op"):
                        cell["nv_reasons"][row["why"][:90]] = \
                            cell["nv_reasons"].get(row["why"][:90], 0) + 1
            cell["label"] = _label(cls, cell["moved"], cell["scored"], cell["tracked"],
                                   cell["mistracked"], mel, cell["refused"])
            m[name][col] = cell
    return m


# ===========================================================================
# 7. rank reversals
# ===========================================================================
def rank_reversals(readings: list[tuple], tol: float) -> list[dict]:
    """`readings` in increasing severity. A reversal is a stronger rung reading
    SMALLER than the largest weaker one by more than `tol` -- more defect,
    less error, beyond the estimator's own resolution."""
    out, best_s, best_v = [], None, -math.inf
    for s, v in readings:
        if v is None:
            continue
        if best_s is not None and v < best_v - tol:
            out.append(dict(weaker=best_s, stronger=s, weaker_reading=_r(best_v),
                            stronger_reading=_r(v)))
        if v > best_v:
            best_s, best_v = s, v
    return out


def reversals_for(anchor_results: list[dict], defn: dict) -> dict:
    ordered = [c for c, s in defn["columns"][DEFECT].items() if s.get("severity_ordered")]
    out: dict = {n: [] for n in defn["estimators"]}
    for ar in anchor_results:
        if ar.get("status") != "OK":
            continue
        for name, src in list(ar.get("estimators", {}).items()) + [("mel_dac", ar.get("mel_dac"))]:
            if not src or src.get("status") != "OK":
                continue
            for col in ordered:
                rows = src["columns"].get(col, [])
                if name == "mel_dac":
                    seq = [(r["strength"], r.get("distance")) for r in rows]
                    tol = src["floor"]
                else:
                    seq = [(r["strength"], None if r.get("delta") is None else abs(r["delta"]))
                           for r in rows]
                    tol = (src["resolution"] * abs(src["base_value"]) if src["relative"]
                           else src["resolution"])
                for rev in rank_reversals(seq, tol):
                    out[name].append(dict(anchor=ar["id"], column=col, tolerance=_r(tol), **rev))
        mel = ar.get("mel_dac")
        if mel and mel.get("status") == "OK":
            # cross-class: a correction the board passes reading at least as far
            # as one of #158's named escapes, on the same anchor
            corr = [r for c in defn["columns"][CORRECTION] for r in mel["columns"][c]
                    if r.get("distance") is not None]
            if corr:
                worst = max(corr, key=lambda r: r["distance"])
                for col in defn["columns"][DEFECT]:
                    if not col.startswith("escape_"):
                        continue
                    for r in mel["columns"][col]:
                        if r.get("distance") is not None and worst["distance"] >= r["distance"]:
                            out["mel_dac"].append(dict(
                                anchor=ar["id"], column=f"genuine_pitch vs {col}",
                                tolerance=mel["floor"], weaker=f"escape {col}",
                                stronger=f"correction x{worst['strength']}",
                                weaker_reading=r["distance"],
                                stronger_reading=worst["distance"]))
    return out


# ===========================================================================
# 8. the synthetic halves: known answers (#517), permitted differences (#519)
# ===========================================================================
_ERR = re.compile(r"got ([-+0-9.eE]+) want ([-+0-9.eE]+) \(([-+0-9.]+) %\)")


def known_answers(defn: dict) -> dict:
    import estimator_ground_truth as gt
    fixtures = ef.catalogue()
    out: dict = {}
    names = {s["known_answer_suite"] for s in defn["estimators"].values()
             if s.get("known_answer_suite") and not s["known_answer_suite"].startswith("audio_")}
    verdicts = gt.run(estimators=sorted(names), fixtures=fixtures)
    for name in sorted(names):
        vs = [v for v in verdicts if v.estimator == name]
        counts: dict = {}
        errs = []
        fails = []
        for v in vs:
            counts[v.status] = counts.get(v.status, 0) + 1
            mt = _ERR.search(v.detail or "")
            if mt and v.status in (gt.PASS, gt.FAIL):
                errs.append(float(mt.group(3)))
            if v.status in gt.RED:
                fails.append(f"{v.kind} [{v.label}]: {v.detail}")
        out[name] = dict(
            fixtures=len(fixtures), counts=dict(sorted(counts.items())),
            parsed_errors=len(errs),
            bias_pct=_r(np.mean(errs)) if errs else None,
            mean_abs_err_pct=_r(np.mean(np.abs(errs))) if errs else None,
            max_abs_err_pct=_r(np.max(np.abs(errs))) if errs else None,
            failures=fails[:EXAMPLES], failures_total=len(fails))
    e0 = adf.e0_ground_truth()
    out["mel_dac"] = dict(
        identical=_r(e0["identical -> 0"]["mel_dac"]), identical_want=0.0,
        x2_gain=_r(e0["x2 gain"]["mel_dac"]), x2_gain_want=_r(math.log(2.0)),
        log_clamp_deviation=_r(e0["log_clamp_deviation"]["mel_dac"]), ok=bool(e0["ok"]))
    return out


def permitted_suite(defn: dict) -> dict:
    spec = defn["synthetic"]["permitted_differences"]
    base = getattr(pdiff, spec["seed_base"])
    rows = pdiff.run_suite(int(spec["trials"]), base, None)
    return dict(trials=int(spec["trials"]), seed_base=spec["seed_base"],
                rows=[dict(case=r.case.cid, estimator=r.case.estimator,
                           policy=r.case.policy.kind, verdict=r.verdict,
                           exceedances=r.exceedances, refusals=r.refusals,
                           threshold=r.case.policy.threshold,
                           residual_worst=_r(r.worst) if r.residuals else None)
                      for r in rows])


# ===========================================================================
# 9. the mel_dac report
# ===========================================================================
def mel_report(anchor_results: list[dict], defn: dict) -> dict:
    spec = defn["estimators"]["mel_dac"]
    grid = spec["k_grid"]
    rows = []
    for ar in anchor_results:
        mel = ar.get("mel_dac") if ar.get("status") == "OK" else None
        if not mel or mel.get("status") != "OK":
            continue
        for cls, col, _ in _columns(defn):
            for r in mel["columns"][col]:
                if r.get("distance") is not None:
                    rows.append(dict(anchor=ar["id"], category=ar["category"],
                                     split=ar["split"], cls=cls, col=col, **r))
    floors = {ar["id"]: ar["mel_dac"]["floor"] for ar in anchor_results
              if ar.get("status") == "OK" and ar.get("mel_dac", {}).get("status") == "OK"}

    def rate(sel, k):
        n = len(sel)
        hit = sum(1 for r in sel if r["distance"] > k * r["floor"])
        return dict(detected=hit, of=n)

    by_k = {}
    for k in grid:
        e = {}
        for split in sorted({r["split"] for r in rows}):
            e[split] = {cls: rate([r for r in rows if r["split"] == split and r["cls"] == cls], k)
                        for cls in (DEFECT, PERMITTED, CORRECTION)}
        e["by_category"] = {cat: {cls: rate([r for r in rows if r["category"] == cat
                                             and r["cls"] == cls], k)
                                  for cls in (DEFECT, PERMITTED, CORRECTION)}
                            for cat in defn["categories"]}
        by_k[str(k)] = e

    cal = [r for r in rows if r["split"] == "calibration" and r["cls"] != DEFECT]
    k_cal = next((k for k in grid if cal and not any(r["distance"] > k * r["floor"]
                                                     for r in cal)), None)
    # what forbids each K: the calibration false alarm with the largest ratio
    worst_fa = sorted(cal, key=lambda r: -r["ratio"])[:EXAMPLES]
    validation = None
    if k_cal is not None:
        validation = by_k[str(k_cal)].get("validation")
    cond = spec.get("k_selection_conditional") or {}
    excl = set(cond.get("exclude_columns") or [])
    cal_c = [r for r in cal if r["col"] not in excl]
    k_cond = next((k for k in grid if cal_c and not any(r["distance"] > k * r["floor"]
                                                        for r in cal_c)), None)
    per_col = {}
    for cls, col, _ in _columns(defn):
        sel = [r for r in rows if r["col"] == col]
        if sel:
            d = np.array([r["distance"] for r in sel])
            q = np.array([r["ratio"] for r in sel])
            per_col[col] = dict(cls=cls, n=len(sel), distance_min=_r(d.min()),
                                distance_median=_r(np.median(d)), distance_max=_r(d.max()),
                                ratio_min=_r(q.min()), ratio_median=_r(np.median(q)),
                                ratio_max=_r(q.max()))
    fl = np.array(list(floors.values())) if floors else np.array([])
    return dict(
        floor_definition=spec["floor_definition"], detection_rule=spec["detection_rule"],
        k_selection=spec["k_selection"], k_grid=grid,
        floors={k: v for k, v in sorted(floors.items())},
        floor_min=_r(fl.min()) if len(fl) else None,
        floor_max=_r(fl.max()) if len(fl) else None,
        rates_by_k=by_k, k_cal=k_cal,
        k_cal_refused_why=(None if k_cal is not None else
                           "no K in the frozen grid gives zero calibration false alarms "
                           "over the permitted and correction columns; the largest "
                           "offenders are listed"),
        calibration_false_alarms_worst=[{k: r[k] for k in ("anchor", "col", "strength",
                                                           "distance", "floor", "ratio")}
                                        for r in worst_fa],
        validation_at_k_cal=validation,
        conditional=dict(exclude_columns=sorted(excl), provenance=cond.get("provenance"),
                         k=k_cond,
                         validation_at_k=(by_k[str(k_cond)].get("validation")
                                          if k_cond is not None else None)),
        per_column=per_col)


# ===========================================================================
# 10. per-estimator reports
# ===========================================================================
def estimator_report(name: str, anchor_results: list[dict], matrix: dict,
                     reversals: dict, known: dict, pdsuite: dict, remit_probe: dict,
                     defn: dict) -> dict:
    spec = defn["estimators"][name]
    mel = name == "mel_dac"
    cells = matrix[name]
    rep: dict = dict(estimator=name, unit=spec.get("unit"), remit=spec.get("remit"))
    if mel:
        rep["known_answers"] = known.get("mel_dac")
    elif spec.get("known_answer_suite"):
        rep["known_answers"] = known.get(spec["known_answer_suite"])
    else:
        rep["known_answers"] = dict(not_in_suite=spec.get("known_answer_note"))
    rep["resolution"] = None if mel else dict(value=spec.get("resolution"),
                                              relative=spec.get("relative"))

    # detectable change size, per severity-ordered defect column: the weakest
    # rung that moved, per anchor
    det, missed = {}, {}
    for col, cs_ in defn["columns"][DEFECT].items():
        weakest, miss = {}, []
        for ar in anchor_results:
            src = (ar.get("mel_dac") if mel else ar.get("estimators", {}).get(name)) \
                if ar.get("status") == "OK" else None
            if not src or src.get("status") != "OK":
                continue
            for r in src["columns"].get(col, []):
                hit = (r.get("distance") is not None and r["distance"] > r["floor"]) if mel \
                    else bool(r.get("moved"))
                scored = (r.get("distance") is not None) if mel else ("moved" in r)
                if hit and ar["id"] not in weakest:
                    weakest[ar["id"]] = r["strength"]
                if scored and not hit:
                    miss.append(f"{ar['id']}@{r['strength']}")
        det[col] = dict(weakest_detected=weakest, severity_ordered=bool(cs_.get("severity_ordered")))
        missed[col] = dict(count=len(miss), examples=miss[:EXAMPLES])
    rep["detectable_change"] = det
    rep["missed_defects"] = missed
    rep["false_alarms"] = {col: dict(moved=cells[col]["moved"], scored=cells[col]["scored"])
                           for col in defn["columns"][PERMITTED]}
    rep["false_alarms_synthetic_519"] = [r for r in pdsuite["rows"] if r["estimator"] == name] \
        or f"no #519 row exercises {name}"
    rep["corrections"] = {col: {k: cells[col][k] for k in
                                ("label", "scored", "moved", "tracked", "mistracked")}
                          for col in defn["columns"][CORRECTION]}

    # no-verdicts: base refusals per anchor and rung reasons
    base_ref, total_nv = {}, {}
    for ar in anchor_results:
        if ar.get("status") != "OK":
            continue
        src = ar.get("mel_dac") if mel else ar.get("estimators", {}).get(name)
        if src and src.get("status") not in ("OK", None):
            base_ref[ar["id"]] = src.get("why", "")[:160]
    for col, cell in cells.items():
        for why, n in cell["nv_reasons"].items():
            total_nv[why] = total_nv.get(why, 0) + n
    rep["no_verdicts"] = dict(
        anchors_refused=base_ref,
        anchor_level_refusals=len(base_ref),
        rung_level=sum(c["no_verdict"] for c in cells.values()),
        reasons=dict(sorted(total_nv.items(), key=lambda kv: -kv[1])))
    rv = reversals.get(name, [])
    rep["rank_reversals"] = dict(count=len(rv), examples=rv[:EXAMPLES])
    if not mel:
        five = {}
        for ar in anchor_results:
            src = ar.get("estimators", {}).get(name) if ar.get("status") == "OK" else None
            if not src or src.get("status") != "OK":
                continue
            for pname, sw in src["ladder"].items():
                for qn, q in sw["questions"].items():
                    a = q.get("answer", q.get("any_refusal"))
                    key = "yes" if a is True else "no" if a is False else "n/a"
                    five.setdefault(pname, {}).setdefault(qn, {"yes": 0, "no": 0, "n/a": 0})
                    five[pname][qn][key] += 1
        rep["five_questions"] = five
        rep["out_of_remit_answers"] = remit_probe.get(name, {})
    return rep


def remit_probe(anchors_loaded: dict, defn: dict) -> dict:
    """Each property estimator's BASE reading on the anchors OUTSIDE its remit.
    An estimator that declares a domain should refuse there; one that answers
    is reported, not judged -- whether the answer means anything is exactly
    what nobody has measured."""
    cases = {c.name: c for c in pl.ESTIMATOR_CASES}
    out: dict = {}
    for name, spec in defn["estimators"].items():
        if name not in cases:
            continue
        ans, ref = {}, {}
        for aid, (anchor, x, sr) in anchors_loaded.items():
            if applies(spec, anchor):
                continue
            e = cases[name].measure(x, sr)
            if e.ok:
                ans[aid] = _r(e.value)
            else:
                ref[aid] = e.reason[:80]
        out[name] = dict(answered=ans, refused=len(ref))
    return out


# ===========================================================================
# 11. the run
# ===========================================================================
def _git(*args) -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception as e:                                          # noqa: BLE001
        return f"unavailable: {e}"


def run(defn: dict, roots: dict | None = None, *, anchor_ids: list[str] | None = None,
        progress=print) -> dict:
    findings = check_definition(defn)
    bad = [f for f in findings if not f["ok"]]
    if bad:
        raise Refused("the definition fails its own check: "
                      + "; ".join(f"{f['rule']} {f['what']}" for f in bad))
    anchors = [a for a in defn["anchors"] if anchor_ids is None or a["id"] in anchor_ids]
    results, loaded = [], {}
    for a in anchors:
        try:
            x, sr = load_anchor(a, defn, roots)
        except Refused as e:
            results.append(dict(id=a["id"], category=a["category"], split=a["split"],
                                pack=a["pack"], status="REFUSED", why=str(e)))
            progress(f"  REFUSED  {a['id']:24s} {e}")
            continue
        loaded[a["id"]] = (a, x, sr)
        r = run_anchor(a, x, sr, defn)
        results.append(r)
        progress(f"  measured {a['id']:24s} {a['category']:15s} {a['split']:11s} "
                 f"{len(x) / sr:5.2f} s @ {sr}")
    matrix = coverage_matrix(results, defn)
    rev = reversals_for(results, defn)
    known = known_answers(defn)
    pds = permitted_suite(defn)
    probe = remit_probe(loaded, defn)
    reports = {n: estimator_report(n, results, matrix, rev, known, pds, probe, defn)
               for n in defn["estimators"]}
    body = dict(
        schema="challenge-set-results/1",
        definition_sha256=defn["freeze"]["definition_sha256"],
        apparatus=apparatus_identity(),
        status="COMPLETE" if all(r["status"] == "OK" for r in results) else "PARTIAL",
        anchors={r["id"]: dict(status=r["status"], category=r["category"], split=r["split"],
                               pack=r["pack"], why=r.get("why"),
                               seconds=r.get("seconds"), sr=r.get("sr"))
                 for r in results},
        matrix={n: {c: {k: v for k, v in cell.items()} for c, cell in cols.items()}
                for n, cols in matrix.items()},
        mel_dac=mel_report(results, defn),
        reports=reports,
        permitted_suite_519=pds,
    )
    full = json.dumps(dict(body, anchor_results=results), sort_keys=True, default=str)
    body["full_results_sha256"] = _sha(full.encode())
    body["provenance"] = dict(commit=_git("rev-parse", "HEAD"),
                              dirty=bool(_git("status", "--porcelain")),
                              command="tools/probes/challenge_set.py run",
                              refs=str(rc.configured_refs()),
                              note="excluded from full_results_sha256: the same inputs "
                                   "give the same digest on any commit")
    body["_anchor_results"] = results          # dropped before the committed write
    return body


# ===========================================================================
# 12. the report
# ===========================================================================
OUT_OF_BAND = (
    "OUT-OF-BAND: whether this challenge set stays meaningful as the estimators "
    "evolve is a claim about the future. No run, green or otherwise, establishes "
    "it. What is established is the mechanism: these results carry the apparatus "
    "they were measured under, `check` reports them STALE when any of it changes, "
    "and `run` re-measures the same frozen anchors.")


def _cell(c: dict) -> str:
    if c["label"] == "NO-VERDICT":
        return (f"NO-VERDICT nv{c['no_verdict']}"
                + (f" A{c['anchors_refused']}" if c["anchors_refused"] else ""))
    tag = {"MOVED": "MOVED", "BLIND": "BLIND", "STILL": "still", "FALSE-ALARM": "FALSE",
           "ABSTAINS": "abstains", "REFUSES": "REFUSES",
           "TRACKS": "tracks", "MISTRACKS": "MISTRACK", "NOT-DERIVABLE": "n/derive",
           "FLAGS-CORRECTION": "FLAGS"}[c["label"]]
    return (f"{tag} {c['moved']}/{c['scored']}" + (f" r{c['refused']}" if c["refused"] else "")
            + (f" A{c['anchors_refused']}" if c["anchors_refused"] else ""))


def report_text(res: dict) -> str:
    L: list[str] = []
    w = L.append
    defn_note = res.get("definition_sha256", "?")
    w("=" * 100)
    w("ESTIMATOR CHALLENGE SET -- detector coverage matrix and per-estimator reports (#521, #158)")
    w("=" * 100)
    w(f"definition_sha256 {defn_note}")
    w(f"status {res.get('status')}   full_results_sha256 {res.get('full_results_sha256')}")
    anchors = res.get("anchors", {})
    n_ok = sum(1 for a in anchors.values() if a["status"] == "OK")
    w(f"anchors measured {n_ok} of {len(anchors)}")
    for aid, a in anchors.items():
        if a["status"] != "OK":
            w(f"   REFUSED {aid}: {a.get('why')}")
    w("")
    w(OUT_OF_BAND)
    w("")
    w("THIS VALIDATES THE JUDGES, NOT THE INSTRUMENT. Property columns are graded against "
      "the physics of each perturbation (perturbation_ladder.py), not against an external "
      "truth about the recording.")
    w("")
    w("-" * 100)
    w("1. COVERAGE MATRIX (estimator x failure mode). Cell = label moved/scored over "
      "(anchor, rung) pairs; r = rungs refused,")
    w("   nv = no-verdict rungs, A = anchors refused at BASE (no rung of them was scored).")
    w("   defect columns: MOVED protects, BLIND misses. permitted: still is right, FALSE "
      "is a false alarm. correction: tracks / MISTRACK / n/derive / FLAGS.")
    w("   mel_dac uses distance > 1 x its floor here; section 2 sweeps K.")
    w("-" * 100)
    mat = res.get("matrix", {})
    cols = list(next(iter(mat.values())).keys()) if mat else []
    for col in cols:
        cls = next(iter(mat.values()))[col]["cls"]
        line = f"  {col:24s} [{cls[:4]}]"
        for name in mat:
            line += f" | {name[:12]:12s} {_cell(mat[name][col]):18s}"
        w(line)
    w("")
    mel = res.get("mel_dac", {})
    w("-" * 100)
    w("2. mel_dac -- detection and false-alarm rates, ABSOLUTE values and the floor's definition")
    w("-" * 100)
    w("FLOOR: " + str(mel.get("floor_definition")))
    w("RULE:  " + str(mel.get("detection_rule")))
    w(f"floors measured: min {mel.get('floor_min')}  max {mel.get('floor_max')} nats")
    for aid, f in (mel.get("floors") or {}).items():
        w(f"   {aid:24s} floor {f}")
    w("")
    w(f"  per column (distance in nats, then ratio to that anchor's floor):")
    w(f"  {'column':24s} {'cls':10s} {'n':>4s} {'d min':>10s} {'d median':>10s} "
      f"{'d max':>10s} {'x min':>9s} {'x median':>9s} {'x max':>9s}")
    for col, s in (mel.get("per_column") or {}).items():
        w(f"  {col:24s} {s['cls']:10s} {s['n']:>4d} {s['distance_min']:>10.4g} "
          f"{s['distance_median']:>10.4g} {s['distance_max']:>10.4g} {s['ratio_min']:>9.3g} "
          f"{s['ratio_median']:>9.3g} {s['ratio_max']:>9.3g}")
    w("")
    w("  rates by K (detected / scored), per split and class:")
    for k, e in (mel.get("rates_by_k") or {}).items():
        parts = []
        for split in ("calibration", "validation", "development"):
            if split in e:
                parts.append(split[:3] + " " + " ".join(
                    f"{c[:3]} {e[split][c]['detected']}/{e[split][c]['of']}"
                    for c in (DEFECT, PERMITTED, CORRECTION)))
        w(f"   K={k:>5s}  " + "   ".join(parts))
    w("")
    w("  instrument families at K=1 (detected / scored):")
    for cat, e in ((mel.get("rates_by_k") or {}).get("1", {}).get("by_category") or {}).items():
        w(f"   {cat:16s} " + "  ".join(f"{c} {e[c]['detected']}/{e[c]['of']}"
                                       for c in (DEFECT, PERMITTED, CORRECTION)))
    w("")
    w(f"  K_cal = {mel.get('k_cal')}" + ("" if mel.get("k_cal") is not None
                                         else f"  REFUSED -- {mel.get('k_cal_refused_why')}"))
    for r in mel.get("calibration_false_alarms_worst") or []:
        w(f"     {r['anchor']:16s} {r['col']:20s} {str(r['strength']):>10s} "
          f"distance {r['distance']:.4g}  floor {r['floor']:.4g}  ({r['ratio']:.3g} x)")
    if mel.get("validation_at_k_cal") is not None:
        w(f"  validation at K_cal: {mel['validation_at_k_cal']}")
    cond = mel.get("conditional") or {}
    if cond:
        w(f"  CONDITIONAL, not a result: excluding {cond.get('exclude_columns')} from the "
          f"calibration false alarms, K = {cond.get('k')}; validation there: "
          f"{cond.get('validation_at_k')}")
        w(f"     {cond.get('provenance')}")
    w("")
    w("-" * 100)
    w("3. ONE REPORT PER ESTIMATOR -- no overall quality number exists in this output")
    w("-" * 100)
    for name, rep in (res.get("reports") or {}).items():
        w(f"\n### {name}  ({rep.get('unit')})")
        w(f"  remit: {rep.get('remit')}")
        if rep.get("resolution"):
            w(f"  declared resolution: {rep['resolution']}")
        w(f"  known answers: {json.dumps(rep.get('known_answers'), sort_keys=True)[:600]}")
        w("  detectable change (weakest rung that moved, per anchor):")
        for col, d in rep["detectable_change"].items():
            wd = d["weakest_detected"]
            w(f"     {col:24s} {len(wd)} anchors: "
              + ", ".join(f"{a}@{s}" for a, s in list(wd.items())[:8])
              + (" ..." if len(wd) > 8 else ""))
        w("  missed injected defects (scored rungs that did not move): "
          + ", ".join(f"{c} {m['count']}" for c, m in rep["missed_defects"].items()))
        w("  false alarms on permitted differences (moved/scored): "
          + ", ".join(f"{c} {f['moved']}/{f['scored']}" for c, f in rep["false_alarms"].items()))
        syn = rep.get("false_alarms_synthetic_519")
        if isinstance(syn, list):
            w("  #519 synthetic rows: " + ", ".join(f"{r['case']} {r['verdict']}" for r in syn))
        else:
            w(f"  #519 synthetic rows: {syn}")
        w(f"  genuine corrections: {json.dumps(rep['corrections'], sort_keys=True)}")
        nv = rep["no_verdicts"]
        w(f"  no-verdicts: {nv['anchor_level_refusals']} anchors refused at base, "
          f"{nv['rung_level']} rungs")
        for aid, why in nv["anchors_refused"].items():
            w(f"     {aid:24s} {why[:110]}")
        for why, n in list(nv["reasons"].items())[:EXAMPLES]:
            w(f"     x{n:<5d} {why}")
        rr = rep["rank_reversals"]
        w(f"  unexpected rank reversals: {rr['count']}"
          + (f" (first {EXAMPLES} shown)" if rr["count"] > EXAMPLES else ""))
        for e in rr["examples"]:
            w(f"     {e['anchor']:16s} {e['column']:34s} {e['weaker']} reads "
              f"{e['weaker_reading']}, {e['stronger']} reads {e['stronger_reading']} "
              f"(tol {e['tolerance']})")
        if rep.get("out_of_remit_answers"):
            o = rep["out_of_remit_answers"]
            w(f"  outside its remit: answered on {len(o['answered'])}, refused on "
              f"{o['refused']}: " + ", ".join(f"{a}={v}" for a, v in list(o["answered"].items())[:8]))
    return "\n".join(L) + "\n"


# ===========================================================================
# 13. freeze
# ===========================================================================
def freeze(defn: dict, roots: dict | None = None, today: str | None = None) -> tuple[dict, list[str]]:
    """Fill in what can be measured from this host: anchor sha256s for files
    present, the fixture digest, and the definition digest. REFUSES to replace
    a frozen sha256 that disagrees with the file -- that is a different
    recording, and swapping it silently is the one thing a freeze exists to
    stop."""
    notes = []
    for a in defn["anchors"]:
        try:
            data = read_anchor_bytes(a, defn, roots)
        except Refused as e:
            notes.append(f"left unfrozen: {e}")
            continue
        got = _sha(data)
        if a.get("sha256") and a["sha256"] != got:
            raise Refused(f"{a['id']}: frozen {a['sha256']}, file {got} -- refusing to re-freeze")
        if not a.get("sha256"):
            a["sha256"] = got
            a.pop("unfrozen_why", None)
            notes.append(f"froze {a['id']} {got[:16]}")
    n, dig = fixture_digest()
    defn["synthetic"]["fixture_count"] = n
    defn["synthetic"]["fixture_digest"] = dig
    defn["freeze"]["definition_sha256"] = definition_sha256(defn)
    defn["freeze"]["frozen_on"] = today or defn["freeze"].get("frozen_on")
    return defn, notes


# ===========================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="definition rules + staleness of the committed results")
    c.add_argument("--results", type=pathlib.Path, default=RESULTS)
    r = sub.add_parser("run", help="the experiment")
    r.add_argument("--json", type=pathlib.Path, default=None,
                   help="write the results here (the committed file is docs/challenge-set-results.json)")
    r.add_argument("--full-json", type=pathlib.Path, default=None,
                   help="also write every anchor's every row (large; not committed)")
    r.add_argument("--anchor", action="append", default=None)
    p = sub.add_parser("report", help="re-render a results file")
    p.add_argument("--results", type=pathlib.Path, default=RESULTS)
    f = sub.add_parser("freeze", help="fill hashes from this host; a visible re-freeze")
    f.add_argument("--date", required=True)
    a = ap.parse_args(argv)

    try:
        defn = load_definition()
    except Refused as e:
        print(f"REFUSED: {e}")
        return 3

    if a.cmd == "check":
        findings = check_definition(defn)
        for fd in findings:
            print(f"  {'OK  ' if fd['ok'] else 'FAIL'} {fd['rule']} {fd['what']}")
        if any(not fd["ok"] for fd in findings):
            return 1
        if not a.results.exists():
            print(f"REFUSED: no results at {a.results}")
            return 3
        res = json.loads(a.results.read_text())
        if res.get("definition_sha256") != defn["freeze"]["definition_sha256"]:
            print("STALE: the results describe a different definition -- run again")
            return 2
        st = staleness(res)
        if st:
            print("STALE: the apparatus changed since these results were measured -- "
                  "`challenge_set.py run` re-measures the frozen anchors under it:")
            for s in st:
                print(f"     {s}")
            return 2
        print("results are current for this definition and this apparatus")
        print(OUT_OF_BAND)
        return 0

    if a.cmd == "report":
        if not a.results.exists():
            print(f"REFUSED: no results at {a.results}")
            return 3
        print(report_text(json.loads(a.results.read_text())), end="")
        return 0

    if a.cmd == "freeze":
        defn, notes = freeze(defn, today=a.date)
        for n in notes:
            print(f"  {n}")
        DEFINITION.write_text(json.dumps(defn, indent=2) + "\n")
        print(f"definition_sha256 {defn['freeze']['definition_sha256']}")
        return 0

    try:
        res = run(defn, anchor_ids=a.anchor)
    except Refused as e:
        print(f"REFUSED: {e}")
        return 3
    full = res.pop("_anchor_results")
    print(report_text(res), end="")
    if a.json:
        a.json.write_text(json.dumps(res, indent=1, sort_keys=True) + "\n")
        print(f"wrote {a.json}")
    if a.full_json:
        a.full_json.write_text(json.dumps(dict(res, anchor_results=full), sort_keys=True) + "\n")
        print(f"wrote {a.full_json}")
    return 0 if res["status"] == "COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
