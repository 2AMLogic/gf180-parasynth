#!/usr/bin/env python3
"""`make trial T=<id>`: one product question, answered PASS / FAIL / NO VERDICT
by EXISTING checkers, with a receipt that can be re-checked later.

    python tools/trial.py list
    python tools/trial.py run T-DEADLINE                       # the trial's default mode
    python tools/trial.py run T-DEADLINE --mode reanalyse      # retained evidence, no simulation
    python tools/trial.py run T-DEADLINE --as-candidate late160   # a known-bad DUT, as the candidate
    python tools/trial.py check-receipt build/trials/T-DEADLINE/<run>/receipt.json [--require PASS]
    python tools/trial.py check-all build/trials               # every receipt under a bundle
    python tools/trial.py compare RECEIPT_A RECEIPT_B          # same inputs -> same numbers?

A THIN DISPATCHER (docs/trials.md rule 7, plan085 section 4). It owns no
checker logic. docs/trials.json names, per trial, which checker answers each
property and how that checker's own evidence record is read; this file runs
them through tools/run_all.py (bounded, process-group kill, each child's own
exit status), hashes identities with tools/provenance.py, and fingerprints the
docs/dag.json nodes the trial depends on.

VERDICTS ARE NOT EXIT CODES. Each checker here has its own convention, and a
Python traceback exits 1 -- the same code a real mismatch uses. So a child's
verdict needs its EVIDENCE and its exit status to agree:

    PASS        the checker's record says pass, its exit status agrees, and the
                declared coverage (I2S periods, writes, frames) is complete
    FAIL        the record says fail, the exit status agrees, and the evidence is
                complete -- a conclusive result about the candidate
    NO VERDICT  anything else: preflight refused, asset missing, timeout,
                cancelled, no record, record/exit disagreement, zero or truncated
                coverage, a checker REFUSED, a control that was not caught

A composite (the trial) is FAIL if any required child is a conclusive FAIL,
PASS only if every required child PASSes AND at least one control was declared
AND every control was caught for its intended reason, and otherwise NO VERDICT
with coverage shown. A mode with no control cannot PASS: nothing showed the
apparatus able to fail (docs/verification-rules.md section 2, docs/trials.md
rule 2).

RECEIPTS. `build/trials/<id>/<run>/receipt.json` is written as NO VERDICT
(execution `in-progress`) before any child starts, and atomically replaced at
the end. So a run that is killed, cancelled or times out can never leave a
PASS behind. `check-receipt` recomputes the receipt's own hash and every
artifact's sha256, refuses files in the run directory that the receipt does not
list, RE-DERIVES each child's verdict (and each control's `caught`) by running
the child's recorded interpreter on its hash-verified evidence and recorded
exit status, recomputes the composite from those, and refuses a PASS whose
execution did not complete.

What that does NOT prove: `receipt_sha256` is an unkeyed self-hash, so it
detects accidental edits, not a forger. A forger who also edits a child's
recorded exit status or interpreter spec, or deletes a child's entry together
with its directory, is caught only by re-running the trial or comparing its
children to docs/trials.json at the recorded registry hash.

Exit: 0 PASS, 1 FAIL, 2 NO VERDICT (the repository's verifier convention; the
receipt, not this number, is the record).
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
import os
import pathlib
import shlex
import signal
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import provenance                                              # noqa: E402
import run_all                                                 # noqa: E402
import trial_env                                               # noqa: E402

ROOT = provenance.ROOT
REGISTRY = ROOT / "docs" / "trials.json"
DAG = ROOT / "docs" / "dag.json"
OUT_BASE = ROOT / "build" / "trials"
# /2: each child records its interpreter spec and directory, so a verdict can be
# re-derived from evidence rather than read back from the receipt. /1 receipts
# cannot be re-derived and are refused.
RECEIPT_SCHEMA = "trial-receipt/2"
# The keys of a child's spec that its interpreter reads; recorded in the receipt.
INTERPRETER_KEYS = ("interpret", "token_prefix", "fixtures")

PASS, FAIL, NO_VERDICT = "PASS", "FAIL", "NO VERDICT"
VERDICTS = (PASS, FAIL, NO_VERDICT)
# Keys that would make docs/trials.json a second copy of a checker's command
# line or a DAG node's inputs. The registry references; it does not restate.
FORBIDDEN_KEYS = {"cmd", "command", "covers", "verifier", "state", "result", "status"}


# ---- registry ---------------------------------------------------------------
class RegistryError(ValueError):
    pass


def _walk_keys(obj, where=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield f"{where}.{k}", k
            yield from _walk_keys(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_keys(v, f"{where}[{i}]")


def load_registry(path: pathlib.Path = REGISTRY, dag_path: pathlib.Path = DAG) -> dict:
    """Parse and check the registry against docs/dag.json. Structural errors are
    raised: a malformed registry is a defect in the repository, not a verdict."""
    reg = json.loads(pathlib.Path(path).read_text())
    dag = json.loads(pathlib.Path(dag_path).read_text())["nodes"]
    for where, key in _walk_keys(reg.get("trials", {})):
        if key in FORBIDDEN_KEYS:
            raise RegistryError(f"{path}: {where} -- trials.json references checkers and DAG "
                                f"nodes; it must not carry {key!r}")
    for tid, t in reg["trials"].items():
        for key in ("question", "criterion_version", "scope", "dag_nodes", "modes"):
            if key not in t:
                raise RegistryError(f"{tid}: missing {key!r}")
        for node in t["dag_nodes"]:
            if node not in dag:
                raise RegistryError(f"{tid}: dag node {node!r} is not in {dag_path}")
        for mname, mode in t["modes"].items():
            ids = set()
            for role in ("required", "controls"):
                for c in mode.get(role, []):
                    for key in ("id", "checker", "args", "interpret"):
                        if key not in c:
                            raise RegistryError(f"{tid}/{mname}/{c.get('id')}: missing {key!r}")
                    if c["interpret"] not in INTERPRETERS:
                        raise RegistryError(f"{tid}/{mname}/{c['id']}: unknown interpreter "
                                            f"{c['interpret']!r}")
                    if c["id"] in ids:
                        raise RegistryError(f"{tid}/{mname}: duplicate child id {c['id']!r}")
                    ids.add(c["id"])
            if not mode.get("required"):
                raise RegistryError(f"{tid}/{mname}: no required children -- nothing to answer")
    return reg


# ---- interpreters: each reads ONE checker's own evidence --------------------
def _result(verdict, reasons, *, expected=None, observed=None, metrics=None, caught=None):
    r = {"verdict": verdict, "reasons": list(reasons),
         "coverage": {"expected": expected, "observed": observed},
         "metrics": metrics or {}}
    if caught is not None:
        r["caught"] = caught
    return r


def _load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text()), None
    except FileNotFoundError:
        return None, f"the checker wrote no record ({path.name})"
    except (OSError, ValueError) as exc:
        return None, f"the checker's record {path.name} is unreadable: {exc}"


def interpret_deadline_record(spec, run, out, role):
    """rtl-sketch/verify_deadline.py --json: status PASS/FAIL/REFUSED, facts,
    schedule. Its own convention: 0 PASS, 1 FAIL, 2 REFUSED; under
    --expect-fail, 0 only when FAIL *for a deadline reason*."""
    rec, err = _load_json(out / "record.json")
    if rec is None:
        return _result(NO_VERDICT, [err])
    status = rec.get("status")
    facts = rec.get("facts") or {}
    sched = rec.get("schedule") or {}
    required, periods = facts.get("periods_required"), facts.get("periods")
    metrics = {k: sched.get(k) for k in ("frames", "missed", "worst_sample_slack",
                                         "worst_busy_slack", "worst_drum_slack")}
    metrics.update({k: facts.get(k) for k in ("periods", "periods_required", "periods_decoded",
                                              "wire_mismatch", "overrun", "busy_at_tick",
                                              "writes_sent", "writes_seen", "writes_bad")})
    metrics["deadline_failed"] = rec.get("deadline_failed")
    cov = dict(expected={"i2s_periods": required, "frames": ">0"},
               observed={"i2s_periods": periods, "frames": sched.get("frames")})
    if status not in ("PASS", "FAIL", "REFUSED"):
        return _result(NO_VERDICT, [f"record status {status!r} is not PASS/FAIL/REFUSED"],
                       metrics=metrics, **cov)
    if status == "REFUSED":
        return _result(NO_VERDICT, ["checker REFUSED: " + "; ".join(rec.get("reasons") or [])],
                       metrics=metrics, **cov, caught=False if role == "control" else None)
    gaps = []
    if not required:
        gaps.append(f"no I2S period was required ({required!r}): nothing was compared")
    elif periods != required:
        gaps.append(f"I2S coverage {periods} of {required} required periods")
    if required and (facts.get("periods_decoded") or 0) < required:
        gaps.append(f"only {facts.get('periods_decoded')} I2S periods decoded of {required}")
    if not sched.get("frames"):
        gaps.append("the schedule trace holds no frames")
    if rec.get("evidence_problems"):
        gaps.append("evidence problems: " + "; ".join(rec["evidence_problems"][:3]))
    if gaps:
        return _result(NO_VERDICT, ["coverage incomplete -- " + g for g in gaps],
                       metrics=metrics, **cov, caught=False if role == "control" else None)
    verdict = PASS if status == "PASS" else FAIL
    reasons = list(rec.get("reasons") or [])
    if role == "control":
        caught = status == "FAIL" and bool(rec.get("deadline_failed")) and run["rc"] == 0
        why = ("caught for the deadline reason" if caught else
               f"NOT caught for its reason (status {status}, deadline_failed "
               f"{rec.get('deadline_failed')}, exit {run['rc']})")
        return _result(verdict, reasons + [why], metrics=metrics, **cov, caught=caught)
    want_rc = {"PASS": 0, "FAIL": 1}[status]
    if run["rc"] != want_rc:
        return _result(NO_VERDICT, [f"exit status {run['rc']} disagrees with the record's "
                                    f"{status} (expected {want_rc})"], metrics=metrics, **cov)
    return _result(verdict, reasons, metrics=metrics, **cov)


def interpret_bound_text(spec, run, out, role):
    """BOUND / STALE / REFUSED printed by a binding checker (release_manifest.py,
    check_arty_evidence_binding.py -- neither writes a record). The printed
    verdict AND the exit status must agree; either alone is not enough."""
    prefix = spec.get("token_prefix", "")
    tokens = []
    for line in run["out"].splitlines():
        s = line.strip()
        if prefix and not s.startswith(prefix):
            continue
        word = s[len(prefix):].split(None, 1)[0].rstrip(":") if s[len(prefix):].split() else ""
        if word in ("BOUND", "STALE", "REFUSED"):
            tokens.append(word)
    cov = dict(expected={"verdict_lines": ">=1"}, observed={"verdict_lines": len(tokens)})
    if not tokens:
        return _result(NO_VERDICT, [f"the checker printed no BOUND/STALE/REFUSED verdict "
                                    f"(exit {run['rc']})"], **cov)
    word = "REFUSED" if "REFUSED" in tokens else "STALE" if "STALE" in tokens else "BOUND"
    verdict, want_rc = {"BOUND": (PASS, 0), "STALE": (FAIL, 1), "REFUSED": (NO_VERDICT, 2)}[word]
    detail = [ln.strip() for ln in run["out"].splitlines()
              if ln.strip().startswith((prefix + word) if prefix else word)][:4]
    if run["rc"] != want_rc:
        return _result(NO_VERDICT, [f"printed {word} but exited {run['rc']} (expected {want_rc})"]
                       + detail, **cov, caught=False if role == "control" else None)
    if role == "control":
        # These checkers have no --expect-fail: a counterexample is caught when
        # it prints STALE AND exits 1, the checker's own FAIL convention.
        caught = word == "STALE"
        return _result(verdict, detail + ["caught: STALE" if caught else
                                          f"NOT caught: printed {word}, not STALE"],
                       metrics={"tokens": tokens}, caught=caught, **cov)
    return _result(verdict, detail, metrics={"tokens": tokens}, **cov)


def interpret_rolling_record(spec, run, out, role):
    """fpga/verify_rolling_playback.py writes <outdir>/verification.json. Its own
    `state` folds a missed CONTROL into FAIL; this separates a failing candidate
    (FAIL) from an apparatus that did not show it can fail (NO VERDICT)."""
    rec, err = _load_json(out / "verification.json")
    if rec is None:
        return _result(NO_VERDICT, [err])
    state = rec.get("state")
    if run["rc"] != {"PASS": 0, "FAIL": 1}.get(state):
        return _result(NO_VERDICT, [f"exit status {run['rc']} disagrees with the record's "
                                    f"state {state!r}"])
    fails, gaps, missed = [], [], []
    clean = rec.get("clean") or {}
    if not clean:
        gaps.append("no clean host case ran")
    for key, r in clean.items():
        if not r.get("ok"):
            fails.append(f"host case {key}: {r.get('reasons')}")
    for name, c in (rec.get("controls") or {}).items():
        if not c.get("caught"):
            missed.append(f"host control {name} not caught")
    metrics = {}
    periods = {}
    for fx in spec.get("fixtures", []):
        rr = (rec.get("rtl") or {}).get(fx)
        if rr is None:
            gaps.append(f"no RTL replay recorded for {fx}")
            continue
        comp = rr.get("comparison") or {}
        periods[fx] = comp.get("periods")
        metrics[fx] = {k: comp.get(k) for k in ("periods", "writes_sent", "writes_seen",
                                                "frame_pred_bad", "wire_mismatch", "overrun",
                                                "worst_strobe_cycle")}
        if rr.get("state") == "REFUSED":
            gaps.append(f"RTL replay {fx} REFUSED: {rr.get('reason')}")
            continue
        if not comp.get("periods"):
            gaps.append(f"RTL replay {fx} compared {comp.get('periods')!r} I2S periods")
            continue
        if not comp.get("writes_sent"):
            gaps.append(f"RTL replay {fx} sent no writes")
            continue
        bad = {k: comp.get(k) for k in ("wire_mismatch", "frame_pred_bad", "writes_bad")
               if comp.get(k)}
        if comp.get("writes_seen") != comp.get("writes_sent"):
            bad["writes_seen/sent"] = f"{comp.get('writes_seen')}/{comp.get('writes_sent')}"
        if bad:
            fails.append(f"RTL replay {fx}: {bad}")
        if not (rr.get("control") or {}).get("caught"):
            missed.append(f"RTL control due+1 for {fx} not caught")
    cov = dict(expected={"rtl_fixtures": spec.get("fixtures", []), "i2s_periods": ">0"},
               observed={"i2s_periods": periods, "clean_host_cases": len(clean)})
    if fails and not gaps:
        return _result(FAIL, fails + missed, metrics=metrics, **cov)
    if gaps or missed:
        return _result(NO_VERDICT, gaps + missed + fails, metrics=metrics, **cov)
    if state != "PASS":
        return _result(NO_VERDICT, [f"record state {state} with no failing case identified"],
                       metrics=metrics, **cov)
    return _result(PASS, [], metrics=metrics, **cov)


def interpret_held_note_record(spec, run, out, role):
    """fpga/release/held_note_audible.py writes <outdir>/held_note_audible.json and
    leaves the decoded I2S at <outdir>/<name>/uart_i2s.txt. Its own verdict is
    replay-PASS AND peak >= floor; an EMPTY I2S file gives peak 0, which it
    calls FAIL -- here that is NO VERDICT (nothing was heard, nothing decoded)."""
    rec, err = _load_json(out / "held_note_audible.json")
    if rec is None:
        return _result(NO_VERDICT, [err], caught=False if role == "control" else None)
    name = rec.get("preset", "?") + ("-legacy" if rec.get("legacy_image") else "")
    i2s = out / name / "uart_i2s.txt"
    samples = 0
    try:
        for line in i2s.read_text().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    int(parts[1])
                    samples += 1
                except ValueError:
                    pass
    except OSError:
        pass
    peak, floor = rec.get("i2s_peak_lsb"), rec.get("min_peak_lsb")
    metrics = {"i2s_peak_lsb": peak, "min_peak_lsb": floor, "replay_status": rec.get("replay_status"),
               "decoded_samples": samples}
    cov = dict(expected={"decoded_i2s_samples": ">0"}, observed={"decoded_i2s_samples": samples})
    nv_caught = False if role == "control" else None
    if samples == 0:
        return _result(NO_VERDICT, [f"no decoded I2S at {name}/uart_i2s.txt: nothing was compared "
                                    "or heard"], metrics=metrics, caught=nv_caught, **cov)
    if rec.get("replay_status") not in (0, 1):
        return _result(NO_VERDICT, [f"replay did not run (status {rec.get('replay_status')})"],
                       metrics=metrics, caught=nv_caught, **cov)
    if rec.get("verdict") not in ("PASS", "FAIL") or peak is None or floor is None:
        return _result(NO_VERDICT, ["record lacks verdict/peak/floor"], metrics=metrics,
                       caught=nv_caught, **cov)
    reasons = []
    if rec["replay_status"] == 1:
        reasons.append("the UART replay is not bit-exact against the model")
    if peak < floor:
        reasons.append(f"silent: decoded I2S peak {peak} LSB below the {floor} LSB floor")
    verdict = PASS if rec["verdict"] == "PASS" else FAIL
    if (verdict == PASS) != (not reasons):
        return _result(NO_VERDICT, [f"record verdict {rec['verdict']} contradicts its own facts "
                                    f"{metrics}"], metrics=metrics, caught=nv_caught, **cov)
    if role == "control":
        caught = (verdict == FAIL and rec["replay_status"] == 0 and peak < floor
                  and run["rc"] == 0)
        reasons.append("caught: bit-exact but silent" if caught else
                       f"NOT caught for its reason (verdict {verdict}, replay "
                       f"{rec['replay_status']}, peak {peak}, exit {run['rc']})")
        return _result(verdict, reasons, metrics=metrics, caught=caught, **cov)
    if run["rc"] != (0 if verdict == PASS else 1):
        return _result(NO_VERDICT, [f"exit status {run['rc']} disagrees with the record's "
                                    f"{verdict}"], metrics=metrics, **cov)
    return _result(verdict, reasons, metrics=metrics, **cov)


INTERPRETERS = {
    "deadline_record": interpret_deadline_record,
    "bound_text": interpret_bound_text,
    "rolling_record": interpret_rolling_record,
    "held_note_record": interpret_held_note_record,
}


def judge_child(spec: dict, run: dict, out: pathlib.Path, role: str, *,
                cancelled: bool = False) -> dict:
    """Execution first, evidence second. A timeout or cancellation is NO VERDICT
    without reading anything: a record on disk from a killed run proves nothing."""
    nv_caught = False if role == "control" else None
    if cancelled:
        return _result(NO_VERDICT, ["the trial was cancelled while this child ran"],
                       caught=nv_caught)
    if run["state"] == run_all.LAUNCH_ERROR:
        return _result(NO_VERDICT, [f"could not launch: {run['out'][:200]}"], caught=nv_caught)
    if run["rc"] is None:
        return _result(NO_VERDICT, [f"timed out after {run['secs']:.0f}s; evidence not read"],
                       caught=nv_caught)
    if run["rc"] not in (0, 1, 2):
        return _result(NO_VERDICT, [f"exit status {run['rc']} is outside the checker "
                                    "convention (0/1/2): killed or crashed"], caught=nv_caught)
    r = INTERPRETERS[spec["interpret"]](spec, run, out, role)
    if run["rc"] == 2 and r["verdict"] != NO_VERDICT and role != "control":
        r = _result(NO_VERDICT, [f"exit status 2 (did not run) but the evidence reads "
                                 f"{r['verdict']}"] + r["reasons"],
                    expected=r["coverage"]["expected"], observed=r["coverage"]["observed"],
                    metrics=r["metrics"])
    if role == "control":
        r.setdefault("caught", False)       # absent evidence never counts as a catch
    return r


def composite(required: list[dict], controls: list[dict]) -> tuple[str, list[str]]:
    """docs/trials.md rule 4."""
    fails = [c for c in required if c["verdict"] == FAIL]
    if fails:
        return FAIL, [f"{c['id']}: FAIL -- " + "; ".join(c["reasons"][:3]) for c in fails]
    open_ = [c for c in required if c["verdict"] != PASS]
    if open_:
        return NO_VERDICT, [f"{c['id']}: {c['verdict']} -- " + "; ".join(c["reasons"][:3])
                            for c in open_]
    if not controls:
        return NO_VERDICT, ["no control declared: the apparatus was not shown able to fail "
                            "(docs/trials.md rule 2)"]
    missed = [c for c in controls if not c.get("caught")]
    if missed:
        return NO_VERDICT, [f"control {c['id']} not caught -- the apparatus was not shown able "
                            "to fail: " + "; ".join(c["reasons"][:3]) for c in missed]
    return PASS, []


# ---- evidence handling ------------------------------------------------------
def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage(spec: dict, root: pathlib.Path, out: pathlib.Path) -> list[str]:
    """Unpack retained, committed evidence into the child's directory, refusing
    any file whose sha256 differs from the record that retained it."""
    st = spec["stage"]
    src = root / st["gunzip"]
    rec, err = _load_json(root / st["hashes"])
    if rec is None:
        return [f"hash record {st['hashes']}: {err}"]
    hashes = rec
    for k in st["hash_key"]:
        hashes = (hashes or {}).get(k)
    if not hashes:
        return [f"{st['hashes']} holds no hashes at {'.'.join(st['hash_key'])}"]
    dst = out / st["into"]
    dst.mkdir(parents=True, exist_ok=True)
    problems = []
    for name, want in sorted(hashes.items()):
        gz = src / (name + ".gz")
        if not gz.is_file():
            problems.append(f"retained evidence missing: {st['gunzip']}/{name}.gz")
            continue
        try:
            data = gzip.decompress(gz.read_bytes())
        except (OSError, EOFError) as exc:
            problems.append(f"retained evidence unreadable: {gz.name}: {exc}")
            continue
        got = hashlib.sha256(data).hexdigest()
        if got != want:
            problems.append(f"retained evidence altered: {name} is {got[:16]}, "
                            f"{st['hashes']} records {want[:16]}")
            continue
        (dst / name).write_bytes(data)
    return problems


def artifacts(run_dir: pathlib.Path, child_dir: pathlib.Path) -> list[dict]:
    out = []
    for p in sorted(child_dir.rglob("*")):
        if p.is_file():
            out.append({"path": str(p.relative_to(run_dir)), "sha256": sha256_file(p),
                        "bytes": p.stat().st_size})
    return out


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()


def seal(receipt: dict) -> dict:
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    receipt["receipt_sha256"] = hashlib.sha256(_canonical(body)).hexdigest()
    return receipt


def write_receipt(run_dir: pathlib.Path, receipt: dict) -> pathlib.Path:
    path = run_dir / "receipt.json"
    run_all.write_json_atomic(str(path), seal(receipt))
    return path


# ---- identities -------------------------------------------------------------
def identities(root: pathlib.Path, trial: dict, children: list[dict], reg_path, env_spec_path,
               env_ident: dict | None, dag_path) -> dict:
    head = provenance.git("rev-parse", "HEAD", root=root).strip() or None
    main = provenance.git("rev-parse", "origin/main", root=root).strip() or None
    base = (provenance.git("merge-base", "HEAD", "origin/main", root=root).strip() or None
            if main else None)
    dag = json.loads(pathlib.Path(dag_path).read_text())["nodes"]
    covers = {}
    for node in trial["dag_nodes"]:
        for rel in dag[node].get("covers", []):
            p = root / rel
            if p.is_file():
                covers[rel] = sha256_file(p)
            elif p.is_dir():
                h = hashlib.sha256()
                for f in sorted(q for q in p.rglob("*") if q.is_file()):
                    h.update(str(f.relative_to(p)).encode())
                    h.update(bytes.fromhex(sha256_file(f)))
                covers[rel] = "tree:" + h.hexdigest()
            else:
                covers[rel] = "missing"
    checkers = {}
    for c in children:
        p = root / c["checker"]
        checkers[c["checker"]] = sha256_file(p) if p.is_file() else "missing"
    return {
        "source": {"head": head, **provenance.worktree_state(root)},
        "integration": {"origin_main": main, "merge_base": base,
                        "head_contains_origin_main": bool(main and base == main)},
        "registry": {"path": str(pathlib.Path(reg_path)), "sha256": sha256_file(reg_path)},
        "environment": {"spec_path": str(env_spec_path), "spec_sha256": sha256_file(env_spec_path),
                        **(env_ident or {})},
        "dag_fingerprint": {"nodes": trial["dag_nodes"], "covers_sha256": covers},
        "checkers_sha256": checkers,
    }


# ---- the run ----------------------------------------------------------------
class _Cancel:
    def __init__(self):
        self.cancelled = False
        self.procs = []

    def spawned(self, p):
        self.procs.append(p)
        if self.cancelled:
            self.kill()

    def kill(self):
        for p in self.procs:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass

    def handler(self, signum, frame):
        self.cancelled = True
        self.kill()


def _expand(args, out: pathlib.Path, root: pathlib.Path) -> list[str]:
    return [a.replace("{out}", str(out)).replace("{root}", str(root)) for a in args]


def _children(mode: dict, as_candidate: str | None) -> tuple[list, list]:
    required = [dict(c, role="required") for c in mode["required"]]
    controls = [dict(c, role="control") for c in mode.get("controls", [])]
    if as_candidate is None:
        return required, controls
    match = [c for c in controls if c["id"] == as_candidate]
    if not match:
        raise RegistryError(f"no control {as_candidate!r} in this mode")
    c = dict(match[0], role="required", id=f"candidate:{as_candidate}")
    c["args"] = [a for a in c["args"] if a != "--expect-fail"]
    return [c], []


def run_trial(tid: str, *, mode: str | None = None, as_candidate: str | None = None,
              root: pathlib.Path = ROOT, registry: pathlib.Path = REGISTRY,
              dag: pathlib.Path = DAG, env_spec: pathlib.Path | None = None,
              out_base: pathlib.Path | None = None, timeout: float | None = None,
              python: str | None = None, cancel: _Cancel | None = None,
              jobs: int | None = None) -> tuple[pathlib.Path, dict]:
    reg = load_registry(registry, dag)
    if tid not in reg["trials"]:
        raise RegistryError(f"unknown trial {tid!r}; known: {sorted(reg['trials'])}")
    trial = reg["trials"][tid]
    mode = mode or next(iter(trial["modes"]))
    if mode not in trial["modes"]:
        raise RegistryError(f"{tid} has no mode {mode!r}; modes: {sorted(trial['modes'])}")
    m = trial["modes"][mode]
    required, controls = _children(m, as_candidate)
    env_spec = pathlib.Path(env_spec or (root / reg["environment"]))
    spec = trial_env.load_spec(env_spec)
    python = python or sys.executable
    cancel = cancel or _Cancel()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    head = provenance.source_commit(root)
    name = f"{stamp}-{head}-{mode}" + (f"-cand-{as_candidate}" if as_candidate else "") \
        + f"-{os.getpid()}-{os.urandom(3).hex()}"
    run_dir = pathlib.Path(out_base or (root / "build" / "trials")) / tid / name
    run_dir.mkdir(parents=True, exist_ok=False)
    started = provenance.now()

    # -- preflight: cheap, before anything long ------------------------------
    problems = []
    for rel in m.get("requires", []):
        if not (root / rel).exists():
            problems.append(f"missing required asset: {rel}")
    for c in required + controls:
        if not (root / c["checker"]).is_file():
            problems.append(f"missing checker: {c['checker']}")
        if "stage" in c:
            for rel in (c["stage"]["gunzip"], c["stage"]["hashes"]):
                if not (root / rel).exists():
                    problems.append(f"missing retained evidence: {rel}")
    problems = list(dict.fromkeys(problems))          # one line per distinct absence
    env_problems, env_ident = trial_env.preflight(spec, tools=m.get("tools", []), root=root,
                                                  python=python)
    problems += env_problems
    ids = identities(root, trial, required + controls, registry, env_spec, env_ident, dag)
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "trial": {"id": tid, "question": trial["question"],
                  "criterion_version": trial["criterion_version"], "mode": mode,
                  "candidate": f"control:{as_candidate}" if as_candidate else "baseline",
                  "scope": trial["scope"], "preservation": trial.get("preservation", []),
                  "resource_class": trial.get("resource_class")},
        "identities": ids,
        "execution": {"status": "in-progress", "started": started, "finished": None,
                      "timeout_s": timeout or m.get("timeout_s"), "preflight": problems},
        "children": [], "controls": [],
        "verdict": NO_VERDICT, "verdict_reasons": ["in progress: no verdict yet"],
        "coverage": {},
    }
    write_receipt(run_dir, receipt)
    if problems:
        receipt["execution"].update(status="preflight-refused", finished=provenance.now())
        receipt["verdict_reasons"] = [f"preflight: {p}" for p in problems]
        receipt["coverage"] = {"required_children": len(required), "with_valid_verdict": 0,
                               "controls": len(controls), "controls_caught": 0}
        write_receipt(run_dir, receipt)
        return run_dir, receipt

    # -- stage retained evidence, then run every child in one batch ----------
    env = dict(os.environ, PATH=trial_env.tool_path(spec, root))
    plan, cmds = [], []
    staged = {}
    for c in required + controls:
        out = run_dir / c["id"].replace(":", "_")
        out.mkdir(parents=True)
        if "stage" in c:
            sp = stage(c, root, out)
            if sp:
                staged[c["id"]] = sp
                plan.append((c, out, None))
                continue
        argv = [python, str(root / c["checker"]), *_expand(c["args"], out, root)]
        plan.append((c, out, len(cmds)))
        cmds.append(shlex.join(argv))
    results = run_all.run_all(cmds, timeout=timeout or m.get("timeout_s"), env=env,
                              cwd=str(root), on_spawn=cancel.spawned, jobs=jobs) if cmds else []

    # Read once: the children are judged, and the execution status set, from
    # the same value, so check_receipt can re-derive both consistently.
    cancelled = cancel.cancelled
    for c, out, idx in plan:
        entry = {"id": c["id"], "role": c["role"], "checker": c["checker"],
                 "property": c.get("property"), "intended_reason": c.get("intended_reason"),
                 "dir": out.name,
                 "interpreter": {k: c[k] for k in INTERPRETER_KEYS if k in c}}
        if idx is None:
            entry.update(command=None, execution={"state": "NOT-RUN", "rc": None, "secs": 0.0},
                         **_result(NO_VERDICT, staged[c["id"]],
                                   caught=False if c["role"] == "control" else None))
        else:
            run = results[idx]
            (out / "log.txt").write_text(run["out"])
            entry.update(command=run["cmd"], cwd=str(root),
                         execution={"state": run["state"], "rc": run["rc"],
                                    "secs": round(run["secs"], 1)},
                         **judge_child(c, run, out, c["role"], cancelled=cancelled))
        entry["artifacts"] = artifacts(run_dir, out)
        (receipt["controls"] if c["role"] == "control" else receipt["children"]).append(entry)

    verdict, reasons = composite(receipt["children"], receipt["controls"])
    status = "complete"
    if cancelled:
        verdict, reasons, status = NO_VERDICT, ["cancelled: children killed"] + reasons, "cancelled"
    elif any(r["rc"] is None and r["state"] == run_all.NO_VERDICT for r in results):
        status = "timeout"
    receipt["execution"].update(status=status, finished=provenance.now())
    receipt["verdict"], receipt["verdict_reasons"] = verdict, reasons
    receipt["coverage"] = {
        "required_children": len(receipt["children"]),
        "with_valid_verdict": sum(c["verdict"] != NO_VERDICT for c in receipt["children"]),
        "passing": sum(c["verdict"] == PASS for c in receipt["children"]),
        "controls": len(receipt["controls"]),
        "controls_caught": sum(bool(c.get("caught")) for c in receipt["controls"]),
    }
    write_receipt(run_dir, receipt)
    return run_dir, receipt


# ---- checking a receipt later -----------------------------------------------
def _rederive(entry: dict, run_dir: pathlib.Path, cancelled: bool) -> tuple[dict | None, str | None]:
    """Judge a recorded child again, from its recorded interpreter spec, its
    recorded execution and its (already hash-checked) evidence on disk."""
    role = entry.get("role")
    spec = entry.get("interpreter")
    if not isinstance(spec, dict) or spec.get("interpret") not in INTERPRETERS:
        return None, "no known interpreter recorded, so its verdict cannot be re-derived"
    ex = entry.get("execution") or {}
    nv_caught = False if role == "control" else None
    if ex.get("state") == "NOT-RUN":
        return _result(NO_VERDICT, [], caught=nv_caught), None
    rel = entry.get("dir")
    child_dir = (run_dir / rel).resolve() if isinstance(rel, str) and rel else None
    if child_dir is None or child_dir.parent != run_dir.resolve():
        return None, f"child directory {rel!r} is not a directory of this run"
    try:
        out_text = (child_dir / "log.txt").read_text()
    except OSError as exc:
        return None, f"its log cannot be read: {exc}"
    run = {"state": ex.get("state"), "rc": ex.get("rc"), "secs": ex.get("secs") or 0.0,
           "out": out_text}
    try:
        return judge_child(dict(spec), run, child_dir, role, cancelled=cancelled), None
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        return None, f"its recorded execution cannot be re-judged: {exc!r}"


def check_receipt(path: pathlib.Path) -> tuple[bool, list[str], dict | None]:
    """(valid, problems, receipt). Valid means: the receipt is intact, every
    artifact it names is present with its recorded sha256 and nothing else is
    in the run directory, every child's verdict (and control's `caught`) is what
    its own interpreter derives from that evidence and its recorded exit status,
    the overall verdict is the one those children imply, and a PASS comes from
    a completed execution. See the module docstring for what this cannot catch."""
    path = pathlib.Path(path)
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return False, [f"unreadable receipt: {exc}"], None
    problems = []
    if rec.get("schema") != RECEIPT_SCHEMA:
        problems.append(f"schema {rec.get('schema')!r} is not {RECEIPT_SCHEMA}")
    body = {k: v for k, v in rec.items() if k != "receipt_sha256"}
    if hashlib.sha256(_canonical(body)).hexdigest() != rec.get("receipt_sha256"):
        problems.append("receipt_sha256 does not match its contents: the receipt was edited")
    if rec.get("verdict") not in VERDICTS:
        problems.append(f"verdict {rec.get('verdict')!r} is not one of {VERDICTS}")
    run_dir = path.parent
    listed = {"receipt.json"}
    for c in rec.get("children", []) + rec.get("controls", []):
        for a in c.get("artifacts", []):
            listed.add(a["path"])
            p = run_dir / a["path"]
            if not p.is_file():
                problems.append(f"artifact missing: {a['path']}")
            elif sha256_file(p) != a["sha256"]:
                problems.append(f"artifact altered: {a['path']}")
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and str(p.relative_to(run_dir)) not in listed:
            problems.append(f"unlisted file in the run directory: {p.relative_to(run_dir)}")
    status = (rec.get("execution") or {}).get("status")
    for key, want_control in (("children", False), ("controls", True)):
        for c in rec.get(key, []):
            if (c.get("role") == "control") != want_control:
                problems.append(f"{c.get('id')}: role {c.get('role')!r} is listed under {key}")
                continue
            got, why = _rederive(c, run_dir, cancelled=status == "cancelled")
            if got is None:
                problems.append(f"{c.get('id')}: {why}")
                continue
            if got["verdict"] != c.get("verdict") or got.get("caught") != c.get("caught"):
                problems.append(
                    f"{c.get('id')}: the receipt says verdict {c.get('verdict')} caught "
                    f"{c.get('caught')}, but its evidence re-derives to {got['verdict']} caught "
                    f"{got.get('caught')}")
    if status == "complete":
        want, _ = composite(rec.get("children", []), rec.get("controls", []))
        if want != rec.get("verdict"):
            problems.append(f"verdict {rec.get('verdict')} is not what its children imply ({want})")
    elif rec.get("verdict") != NO_VERDICT:
        problems.append(f"execution {status!r} did not complete, so its verdict cannot be "
                        f"{rec.get('verdict')}")
    return not problems, problems, rec


def fingerprint_drift(rec: dict, root: pathlib.Path = ROOT) -> list[str]:
    """Inputs that changed since the receipt was made (docs/trials.md rule 6: the
    result stays valid for its inputs, but may no longer qualify this tree)."""
    ids = rec["identities"]
    drift = []
    for rel, want in ids["dag_fingerprint"]["covers_sha256"].items():
        p = root / rel
        got = sha256_file(p) if p.is_file() else ("dir" if p.is_dir() else "missing")
        if p.is_dir() and str(want).startswith("tree:"):
            continue      # directory trees: compared by re-running, not here
        if got != want:
            drift.append(f"dag input {rel}")
    for rel, want in ids["checkers_sha256"].items():
        p = root / rel
        if (sha256_file(p) if p.is_file() else "missing") != want:
            drift.append(f"checker {rel}")
    return drift


def compare(a: dict, b: dict) -> list[str]:
    """Same controlled inputs -> same verdicts and same numbers. Machine, time
    and elapsed seconds are allowed to differ; nothing else is."""
    diffs = []
    for k in ("id", "criterion_version", "mode", "candidate"):
        if a["trial"][k] != b["trial"][k]:
            diffs.append(f"trial.{k}: {a['trial'][k]!r} vs {b['trial'][k]!r}")
    for k in ("covers_sha256",):
        if a["identities"]["dag_fingerprint"][k] != b["identities"]["dag_fingerprint"][k]:
            diffs.append("the DAG input fingerprints differ: not the same controlled inputs")
    if a["identities"]["checkers_sha256"] != b["identities"]["checkers_sha256"]:
        diffs.append("the checkers differ: not the same controlled inputs")
    if a["verdict"] != b["verdict"]:
        diffs.append(f"verdict {a['verdict']} vs {b['verdict']}")
    bmap = {c["id"]: c for c in b["children"] + b["controls"]}
    for c in a["children"] + a["controls"]:
        d = bmap.get(c["id"])
        if d is None:
            diffs.append(f"{c['id']}: absent from the second receipt")
            continue
        for k in ("verdict", "caught", "metrics", "coverage"):
            if c.get(k) != d.get(k):
                diffs.append(f"{c['id']}.{k}: {c.get(k)!r} vs {d.get(k)!r}")
    return diffs


# ---- CLI --------------------------------------------------------------------
def _print_receipt(run_dir: pathlib.Path, rec: dict) -> None:
    t = rec["trial"]
    print(f"trial {t['id']} [{t['mode']}, {t['candidate']}] criterion {t['criterion_version']}")
    for c in rec["children"] + rec["controls"]:
        ex = c["execution"]
        tag = c["verdict"] + (f" caught={c.get('caught')}" if c["role"] == "control" else "")
        print(f"  {c['role']:<8} {c['id']:<22} exec {ex['state']}(rc {ex['rc']}, {ex['secs']}s) "
              f"-> {tag}")
        for r in c["reasons"][:3]:
            print(f"             {r}")
    print(f"trial {t['id']}: {rec['verdict']} (execution {rec['execution']['status']})"
          + ("" if not rec["verdict_reasons"] else " -- " + "; ".join(rec["verdict_reasons"][:4])))
    print(f"receipt: {run_dir / 'receipt.json'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("trial")
    r.add_argument("--mode", default=None)
    r.add_argument("--as-candidate", default=None, metavar="CONTROL",
                   help="run a declared control as THE candidate (a known-bad DUT); "
                        "the receipt names it, so it cannot pass for the baseline")
    r.add_argument("--timeout", type=float, default=None, help="per child, seconds")
    r.add_argument("--out", type=pathlib.Path, default=None, help="receipt base directory")
    r.add_argument("--jobs", type=int, default=None)
    c = sub.add_parser("check-receipt")
    c.add_argument("receipt", type=pathlib.Path)
    c.add_argument("--require", choices=VERDICTS, default=None,
                   help="exit 0 only if the receipt is valid AND carries this verdict")
    ca = sub.add_parser("check-all")
    ca.add_argument("base", type=pathlib.Path)
    k = sub.add_parser("compare")
    k.add_argument("a", type=pathlib.Path)
    k.add_argument("b", type=pathlib.Path)
    a = ap.parse_args(argv)

    if a.cmd == "list":
        reg = load_registry()
        for tid, t in reg["trials"].items():
            print(f"{tid:<16} modes {sorted(t['modes'])}  {t['question']}")
        return 0
    if a.cmd == "run":
        cancel = _Cancel()
        signal.signal(signal.SIGTERM, cancel.handler)
        signal.signal(signal.SIGINT, cancel.handler)
        run_dir, rec = run_trial(a.trial, mode=a.mode, as_candidate=a.as_candidate,
                                 timeout=a.timeout, out_base=a.out, cancel=cancel, jobs=a.jobs)
        _print_receipt(run_dir, rec)
        return {PASS: 0, FAIL: 1}.get(rec["verdict"], 2)
    if a.cmd == "check-receipt":
        ok, problems, rec = check_receipt(a.receipt)
        for p in problems:
            print(f"REJECTED: {p}")
        if rec is not None and ok:
            drift = fingerprint_drift(rec)
            print(f"VALID: {rec['trial']['id']} {rec['verdict']} "
                  f"(execution {rec['execution']['status']})"
                  + (f"; inputs changed since: {drift}" if drift else "; inputs unchanged"))
            if a.require and rec["verdict"] != a.require:
                print(f"REJECTED: verdict {rec['verdict']} is not the required {a.require}")
                return 1
        return 0 if ok else 1
    if a.cmd == "check-all":
        paths = sorted(a.base.rglob("receipt.json"))
        bad = 0
        for p in paths:
            ok, problems, rec = check_receipt(p)
            bad += not ok
            print(f"{'VALID   ' if ok else 'REJECTED'} {rec['verdict'] if rec else '?':<10} "
                  f"{(rec or {}).get('execution', {}).get('status', '?'):<17} {p}")
            for pr in problems:
                print(f"           {pr}")
        print(f"{len(paths) - bad}/{len(paths)} receipts valid")
        return 1 if bad or not paths else 0
    ok_a, pa, ra = check_receipt(a.a)
    ok_b, pb, rb = check_receipt(a.b)
    if not (ok_a and ok_b):
        for p in pa + pb:
            print(f"REJECTED: {p}")
        return 2
    diffs = compare(ra, rb)
    for d in diffs:
        print(f"DIFFER: {d}")
    print("AGREE" if not diffs else f"DISAGREE ({len(diffs)})")
    return 0 if not diffs else 1


if __name__ == "__main__":
    sys.exit(main())
