#!/usr/bin/env python3
"""Is the 1-2.5 kHz energy/decay discrepancy present on the CURRENT PRODUCTION
cymbal path, against the Fischer recordings? (#400; sound requirement #282/#369)

    python3 tools/probes/cymbal_400_production_tail.py freeze  --refs DIR --out FROZEN.json
    python3 tools/probes/cymbal_400_production_tail.py measure --refs DIR --frozen FROZEN.json --out R.json
    python3 -m pytest tools/probes/test_cymbal_400_production_tail.py -q   # the controls

WHAT THIS IS. A thin, frozen-before-use COMPARISON layer over the qualified
instruments `tools/cymbal_low_tail.py` (decay ratio rho) and
`tools/cymbal_mid.py` (band energy + skirt-leak gate). It changes neither. It
renders the production CY through `run_case.render_drum_solo` (the register
interface of the kit that ships) and compares it with Fischer CY5025.

TWO "M" BANDS, stated because they differ. Energy uses cymbal_mid's `M25`
(891-2818 Hz); decay uses cymbal_low_tail's `M` (891-2828 Hz). Both are the
1-2.5 kHz union of the five thirds the #369 step-5 finding names; they are not
the same filter and numbers from one are never quoted for the other.

PROPERTIES ARE REPORTED SEPARATELY (energy, decay) and `joint` is True only if
both are DISCREPANT. Outcomes are MATCH / DISCREPANT / REFUSED; REFUSED is a
first-class outcome and is never folded into either of the others.

NO NEW NUMERIC DIAL. The discrepancy thresholds are run_case.TOLERANCE_POLICY's
existing ones (energy ratio 3 dB; time 50 %), imported not re-chosen, so nothing
is registered in tools/sensitivity.py by this probe. If a later stage-isolation
step proposes a dial it must be registered when proposed (docs/sensitivity-sweeps.md).

PRODUCTION KNOB SUPPORT. `render_drum_solo` takes no CY TONE/DECAY argument, so
the production path renders ONE setting, the D14A anchor CY5025. The other 24
Fischer settings are REFUSED for production. `kit_at` or a diagnostic candidate
is never substituted (tests/test_..::test_production_path_has_no_knob_parameters
fails if the signature gains knobs, forcing the refusal to be revisited).

Hh1: the shipped kit omits it (`model/drums_fx.py`: "Hh1 is the documented
omission"); the machine has it. The declaration changes the leak prediction by
8.63 dB and is an explicit argument, never a default guess.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (ROOT / "tools", ROOT / "model"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cymbal_bands as cb  # noqa: E402
import cymbal_low_tail as lt  # noqa: E402
import cymbal_mid as cm  # noqa: E402
import run_case as rc  # noqa: E402


class Refused(RuntimeError):
    """The apparatus cannot answer. Distinct from pass and from fail."""


ENERGY_TOL_DB = 3.0      # run_case.TOLERANCE_POLICY["energy ratio"]: "3 dB, the half-power convention"
TIME_TOL_REL = 0.5       # run_case.TOLERANCE_POLICY["time"]: "50 % of the reference value"
assert "3 dB" in rc.TOLERANCE_POLICY["energy ratio"] and "50 %" in rc.TOLERANCE_POLICY["time"]

SOURCE_FILES = ("tools/cymbal_low_tail.py", "tools/cymbal_mid.py", "tools/cymbal_bands.py",
                "tools/run_case.py", "model/audio_measure.py", "tools/preparation_contract.py",
                "model/drums_fx.py", "model/modal_fixed.py", "audition/dsp.py", "model/fixed.py",
                "model/voice_fx.py", "tools/probes/cymbal_400_production_tail.py")
REF_FILE = "cy8/CY5025.WAV"

#: Every refusal class the measured path can raise, normalized at the CLI boundary. The first
#: freeze pinned only six files, none of the model modules the render path executes
#: (`drums_fx` imports `modal_fixed`, `dsp`, `fixed`, `voice_fx`) nor the preparation
#: dependencies (`run_case.prepare` asserts finiteness through `audio_measure` and refuses
#: through `preparation_contract.Refused`) -- an edit to the modal bank after freeze changed
#: the render under an unchanged guard. The file list is the direct dependency closure; the
#: source-tree rule below is the enforcement for everything else the interpreter loads.
REFUSED_TYPES = (Refused, rc.Refused, cb.Refused, lt.Refused)

FROZEN = {
    "issue": 400,
    "supported_settings": ["CY5025"],
    "supported_reason": "run_case.render_drum_solo has no CY knob parameter; D14A anchor only",
    "development_settings": ["CY5025"],
    "confirmation_settings": [f"CY{t}{d}" for t in cb.CODES for d in cb.CODES if f"CY{t}{d}" != "CY5025"],
    "confirmation_note": ("production cannot render any confirmation setting, so no untouched confirmation "
                          "exists on this path: a confirmed MECHANISM claim is withheld by construction"),
    "windows_s": [1.5, 2.0],
    "tolerances": {"energy_db": ENERGY_TOL_DB, "time_rel": TIME_TOL_REL,
                   "source": "run_case.TOLERANCE_POLICY (imported, not re-chosen)"},
    "bands": {"energy": "cymbal_mid.MID['M25'] 891-2818 Hz", "decay": "cymbal_low_tail.BANDS['M'] 891-2828 Hz vs Ln",
              "decay_depth_db": -10.0},
    "prod_has_hh1": False,
    "candidate_budget": 0,
    "candidate_budget_note": "no candidate is rendered or promoted under #400; #369 owns delivery",
    "source_tree": ("freeze and measure refuse a working tree with uncommitted changes to tracked "
                    "files: the render must be attributable to a named, committed state. result.json "
                    "records the commit that actually executed as measured_at_commit"),
    "preservation_set": ["D15A", "D16A", "OH00", "OH10", "OH25", "OH50", "OH75"],
    "stage_isolation": {
        "run_only_if": "energy or decay verdict is DISCREPANT on CY5025 in both windows",
        "stages": ["source (mode amplitudes/decay)", "band routing / filter realization",
                   "envelope", "output stage"],
        "predictions": [
            {"claim": "energy deficit persists with the envelope stage bypassed (flat gate)",
             "if_true": "source or routing, not envelope", "status": "unverified"},
            {"claim": "decay deficit disappears with the envelope stage bypassed",
             "if_true": "envelope realization owns the tail", "status": "unverified"},
            {"claim": "output stage (hard 16-bit rail) contributes < 1 dB; vca-clip/vca-drive already "
                      "recorded zero saturation events", "status": "inferred"},
        ],
        "ablations_touch": "CY only; D15A/D16A/OH00-OH75 asserted bit-identical before and after",
    },
}


# ------------------------------------------------------------------ guards
def assert_prepared(y, sr: int, side: str) -> None:
    """`run_case.prepare` guarantees `required_lead_samples` of TRUE silence before the strike.
    A record without it was not prepared (or was cut into the strike) and its band-passes start
    mid-strike (#101). REFUSE."""
    y = np.asarray(y, dtype=np.float64)
    o = rc.required_lead_samples(sr)
    if len(y) <= o or float(np.max(np.abs(y[:o]))) > 1e-9:
        raise Refused(f"{side}: record did not come through run_case.prepare (no {o}-sample true-silence "
                      f"lead); preparation state mismatch")


def _check(y, sr, side, defect):
    y = np.asarray(y, dtype=np.float64)
    if defect != "NO_FINITE_CHECK" and not np.all(np.isfinite(y)):
        raise Refused(f"{side}: record is not finite")
    if defect != "NO_PREP_ASSERT":
        assert_prepared(y, sr, side)
    return y


def production_supported(setting: str) -> None:
    if setting not in FROZEN["supported_settings"]:
        raise Refused(f"{setting}: unsupported knob setting on the production path "
                      f"({FROZEN['supported_reason']}); not substituting kit_at or a candidate")


def sha256(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def require_corpus(refs, frozen) -> dict:
    refs = pathlib.Path(refs)
    if not (refs / REF_FILE).is_file():
        raise Refused(f"reference corpus missing: {refs / REF_FILE} (set GF180_TR808_REFS / --refs); "
                      f"NO VERDICT without the Fischer recordings")
    got = {p.name: sha256(p) for p in sorted((refs / "cy8").glob("CY*.WAV"))}
    for name, want in ((frozen or {}).get("reference_sha256") or {}).items():
        if got.get(name) != want:
            raise Refused(f"reference hash mismatch for {name}: corpus is not the one frozen")
    return got


# ------------------------------------------------------------------ comparison
def classify(prod, ref, kind: str) -> dict:
    if kind not in ("energy_db", "time_ratio"):
        raise Refused(f"unknown comparison kind {kind!r}")
    vals = (prod, ref)
    if any(v is None or not math.isfinite(v) for v in vals):
        return {"verdict": "REFUSED", "prod": prod, "ref": ref, "reason": "a side is missing or non-finite"}
    if kind == "energy_db":
        d = prod - ref
        bad = abs(d) > ENERGY_TOL_DB
    else:
        if ref <= 0:
            return {"verdict": "REFUSED", "prod": prod, "ref": ref, "reason": "reference ratio is not positive"}
        d = (prod - ref) / ref
        bad = abs(d) > TIME_TOL_REL
    return {"verdict": "DISCREPANT" if bad else "MATCH", "prod": prod, "ref": ref, "delta": round(d, 4)}


def _refused(reason, prod=None, ref=None, detail=None):
    return {"verdict": "REFUSED", "prod": prod, "ref": ref, "reason": reason, "detail": detail or {}}


def _refused_by_side(p_val, p_reason, r_val, r_reason, *, undefined, detail=None):
    """REFUSED with the reason ATTRIBUTED to the side(s) whose gate tripped. An unlabelled
    `p_reason or r_reason` misattributed the #400 decay refusal (README wrong-then-right #4)."""
    by = {"production": None if p_val is not None else (p_reason or undefined),
          "reference": None if r_val is not None else (r_reason or undefined)}
    reason = "; ".join(f"{side}: {why}" for side, why in by.items() if why is not None)
    out = _refused(reason, p_val, r_val, detail)
    out["refused_by"] = by
    return out


def _side(y, sr, hh1, trim_s, side):
    try:
        m = cm.measure_mid(y, sr, hh1, bands=["M25"])["M25"]
    except cb.Refused as e:                # a silent record: cymbal_mid's precondition, not ours
        raise Refused(f"{side}: {e}") from e
    out = {"mid": m, "energy": None, "energy_refusal": None, "rho": None, "rho_all": None, "decay_refusal": None}
    rec_s = (len(y) - rc.required_lead_samples(sr)) / sr
    if rec_s < cb.ENERGY_S:
        out["energy_refusal"] = f"record is {rec_s:.2f} s after t=0, shorter than the {cb.ENERGY_S:.1f} s energy window"
    elif m["over_leak_1s_db"] is None or m["over_leak_1s_db"] < cm.LEAK_MARGIN_DB:
        out["energy_refusal"] = (f"band is only {m['over_leak_1s_db']} dB above the 3.45 kHz band's predicted "
                                 f"skirt leakage (need {cm.LEAK_MARGIN_DB:.0f}): leak-dominated")
    else:
        out["energy"] = m["energy_share_db"]
    if "refused" in m and "skirt" in m["refused"]:
        out["decay_refusal"] = m["refused"]
    elif m["over_leak_edt_db"] is None or m["over_leak_edt_db"] < cm.LEAK_MARGIN_DB:
        out["decay_refusal"] = f"EDT window is leak-dominated (over-leak {m['over_leak_edt_db']} dB)"
    else:
        try:
            r = lt.measure(y, sr, trim_s=trim_s)
            out["rho_all"] = r["rho"]["M"]
            out["rho"] = r["rho"]["M"].get("-10")
            if out["rho"] is None:
                out["decay_refusal"] = ("rho(-10) refused: " + r["bands"]["M"]["refused"].get("-10", "reason not recorded")
                                        if "-10" in r["bands"]["M"]["refused"] else "rho(-10) undefined (Ln side)")
        except lt.Refused as e:
            out["decay_refusal"] = str(e)
    return out


def compare_records(prod, sr_p, ref, sr_r, *, prod_has_hh1, trim_s=2.0, defect=None) -> dict:
    """Energy and decay of PROD vs REF, separately. Both must be run_case.prepare output."""
    prod = _check(prod, sr_p, "production", defect)
    ref = _check(ref, sr_r, "reference", defect)
    p = _side(prod, sr_p, bool(prod_has_hh1), trim_s, "production")
    r = _side(ref, sr_r, True, trim_s, "reference")   # the machine has Hh1 (reference §10)
    if p["energy"] is None or r["energy"] is None:
        energy = _refused_by_side(p["energy"], p["energy_refusal"], r["energy"], r["energy_refusal"],
                                  undefined="energy undefined",
                                  detail={"predicted_leak_db_re_L": p["mid"]["predicted_leak_db_re_L"]})
    else:
        energy = classify(p["energy"], r["energy"], "energy_db")
    if p["rho"] is None or r["rho"] is None:
        decay = _refused_by_side(p["rho"], p["decay_refusal"], r["rho"], r["decay_refusal"], undefined="rho undefined")
    else:
        decay = classify(p["rho"], r["rho"], "time_ratio")
    energy.setdefault("detail", {})["predicted_leak_db_re_L"] = p["mid"]["predicted_leak_db_re_L"]
    # UNQUALIFIED raw values, kept so a REFUSED is diagnosable and a near-boundary refusal visible.
    # They are NOT verdict inputs and must never be quoted as measurements of the sound.
    energy["detail"]["unqualified_raw"] = {
        side: {"energy_share_db": x["mid"]["energy_share_db"], "over_leak_1s_db": x["mid"]["over_leak_1s_db"],
               "over_leak_edt_db": x["mid"]["over_leak_edt_db"], "leak_margin_required_db": cm.LEAK_MARGIN_DB}
        for side, x in (("prod", p), ("ref", r))}
    decay.setdefault("detail", {})["rho_all_prod"] = p["rho_all"]
    decay["detail"]["rho_all_ref"] = r["rho_all"]
    return {"energy": energy, "decay": decay, "trim_s": trim_s, "prod_has_hh1": bool(prod_has_hh1),
            "joint": energy["verdict"] == "DISCREPANT" and decay["verdict"] == "DISCREPANT"}


# ------------------------------------------------------------------ CLI
def _git(*a):
    return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def source_hashes() -> dict:
    return {f: sha256(ROOT / f) for f in SOURCE_FILES}


def require_tree_identity() -> str:
    """The render must be attributable to a named, committed state. A working tree with
    uncommitted changes to tracked files is refused at freeze AND at measure: the hash list
    cannot see a file outside its closure, but a dirty tree is not ANY commit's source."""
    head = _git("rev-parse", "HEAD")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise Refused("the working tree has uncommitted changes to tracked files, so the render is "
                      "not attributable to a commit; commit or stash (per-worktree, never a bare "
                      "git stash) and re-freeze before measuring:\n" + dirty)
    return head


def cmd_freeze(a) -> int:
    require_tree_identity()
    hashes = require_corpus(a.refs, None)
    try:
        corpus_commit = subprocess.run(["git", "-C", str(a.refs), "rev-parse", "HEAD"], capture_output=True,
                                       text=True, check=True).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        corpus_commit = None            # not a git checkout: the file hashes are then the only pin
    rec = dict(FROZEN, corpus_git_commit=corpus_commit, source_commit=_git("rev-parse", "HEAD"), source_hashes=source_hashes(),
               reference_sha256=hashes)
    a.out.write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(f"FROZEN {a.out} at {rec['source_commit'][:10]}")
    return 0


def cmd_measure(a) -> int:
    try:
        frozen = json.loads(pathlib.Path(a.frozen).read_text())
    except FileNotFoundError:
        print(f"REFUSED: no frozen record at {a.frozen}; run `freeze` BEFORE measuring")
        return 2
    try:
        require_corpus(a.refs, frozen)
        measured_at = require_tree_identity()
        now = source_hashes()
        for f, h in frozen["source_hashes"].items():
            if now.get(f) != h:
                raise Refused(f"{f} changed since freeze (dependency closure): re-freeze before measuring")
        ok, lines = lt.check()
        if not ok:
            raise Refused("cymbal_low_tail's own controls do not pass:\n" + "\n".join(lines))
        x, sr_r = cb._load(pathlib.Path(a.refs) / REF_FILE)
        ref = rc.prepare(x, sr_r, side="CY5025")
        y, sr_p = rc.render_drum_solo("CY")
        prod = rc.prepare(y, sr_p, side="production CY")
        res = {"frozen": str(a.frozen), "source_commit": frozen["source_commit"],
               "measured_at_commit": measured_at, "settings": {}}
        for s in frozen["supported_settings"]:
            production_supported(s)
            res["settings"][s] = {f"{w:g}s": compare_records(prod, sr_p, ref, sr_r, prod_has_hh1=frozen["prod_has_hh1"],
                                                            trim_s=w) for w in frozen["windows_s"]}
        for s in frozen["confirmation_settings"]:
            try:
                production_supported(s)
            except Refused as e:
                res["settings"][s] = {"verdict": "REFUSED", "reason": str(e)}
    except Refused as e:
        print(f"REFUSED / NO VERDICT: {e}")
        return 2
    a.out.write_text(json.dumps(res, indent=1, sort_keys=True, default=str) + "\n")
    for w, r in res["settings"]["CY5025"].items():
        print(f"CY5025 {w}: energy {r['energy']['verdict']}  decay {r['decay']['verdict']}  joint {r['joint']}")
        for k in ("energy", "decay"):
            if r[k]["verdict"] == "REFUSED":
                print(f"  {k} REFUSED by {r[k]['reason']}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("freeze", "measure"):
        s = sub.add_parser(n)
        s.add_argument("--refs", default=str(rc.configured_refs()))
        s.add_argument("--out", type=pathlib.Path, required=True)
        if n == "measure":
            s.add_argument("--frozen", required=True)
    a = ap.parse_args(argv)
    try:
        return {"freeze": cmd_freeze, "measure": cmd_measure}[a.cmd](a)
    except REFUSED_TYPES as e:
        # Downstream preconditions are normalized HERE, by class -- never by catching
        # Exception: an implementation error must still be a traceback, not a refusal.
        print(f"REFUSED / NO VERDICT: {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
