#!/usr/bin/env python3
"""Is the committed external-reference response snapshot recent? (#622)

    python tools/reference_freshness.py [--repo .] [--dir docs/reference-freshness]

Verdicts, each its own exit code -- REFUSED is not a kind of FAIL:

    FRESH    exit 0  every required file is usable and its last commit is <= 14 days old
    STALE    exit 1  usable evidence, but the OLDEST file's last commit is > 14 days old
    REFUSED  exit 2  the check cannot answer: absent file, no/shallow history,
                     future timestamp, invalid JSON, empty rows, wrong device,
                     `not_answerable` stub, unusable rows. Names the reason.

Policy (unchanged from the retired shell check): COMMIT age, floor(elapsed UTC
seconds / 86400) days, 14 passes and 15 fails. File mtimes are never read.

LIMITATION, printed with every verdict: a recent commit does not prove a recent
measurement or acoustic correctness. A reformat or copied historical rows would
satisfy commit age. This check reports evidence availability only; it does not
validate the synth.

Bounded population: stage `response`, devices ours, surge-rk, surge-huov,
miniv3 (Diva excluded). Peak files are out of scope. The frozen aggregate
docs/reference-compare-results.json is never read or modified here.
"""
from __future__ import annotations
import argparse, json, math, subprocess, sys, time

DEVICES = ("ours", "surge-rk", "surge-huov", "miniv3")
MAX_AGE_DAYS = 14
FRESH, STALE, REFUSED = "FRESH", "STALE", "REFUSED"
EXIT = {FRESH: 0, STALE: 1, REFUSED: 2}
PRODUCER = (".venv/bin/python model/reference_compare.py --stage response "
            "--devices ours,surge-rk,surge-huov,miniv3 --out /tmp/reference-freshness")
PUBLISH = """To publish (on a provisioned reference host, after licence/readback/silence checks):
  1. run: """ + PRODUCER + """
  2. inspect the four /tmp/reference-freshness/response-<device>.json files
  3. copy them into docs/reference-freshness/ and commit them
  Do not copy frozen rows or edit docs/reference-compare-results.json.
  See docs/reference-freshness/README.md."""
LIMIT = ("NOTE: this is COMMIT age. A recent commit does not prove a recent measurement "
         "or acoustic correctness; this check does not validate the synth.")


class Refused(Exception):
    pass


def _git(repo, *args):
    p = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()


def _finite_list(v):
    return (isinstance(v, list) and v and
            all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
                for x in v))


def check_rows(device, rows, name):
    if not isinstance(rows, list) or not rows:
        raise Refused(f"{name}: empty or non-list rows")
    for r in rows:
        if not isinstance(r, dict):
            raise Refused(f"{name}: row is not an object")
        if r.get("not_answerable") is not None:
            raise Refused(f"{name}: not_answerable stub ({r['not_answerable']})")
        if r.get("ok") is False:
            raise Refused(f"{name}: row marked ok=false")
        if r.get("device") != device:
            raise Refused(f"{name}: expected device {device!r}, row has {r.get('device')!r}")
        if not (_finite_list(r.get("freqs")) and _finite_list(r.get("gain_db"))
                and len(r["freqs"]) == len(r["gain_db"])):
            raise Refused(f"{name}: row lacks usable freqs/gain_db")


def evaluate(repo, directory, now):
    """Return (verdict, message). `now` is epoch seconds (UTC)."""
    rc, out, _ = _git(repo, "rev-parse", "--is-shallow-repository")
    if rc != 0:
        return REFUSED, "not a git repository: no history to read commit age from"
    if out != "false":
        return REFUSED, "shallow checkout: commit age unknowable (need fetch-depth: 0)"
    ages = {}
    try:
        for dev in DEVICES:
            rel = f"{directory}/response-{dev}.json"
            rc, _, _ = _git(repo, "ls-files", "--error-unmatch", rel)
            if rc != 0:
                raise Refused(f"{rel}: absent (not tracked in git)")
            try:
                with open(f"{repo}/{rel}") as f:
                    data = json.load(f)
            except FileNotFoundError:
                raise Refused(f"{rel}: absent from working tree")
            except (ValueError, OSError) as e:
                raise Refused(f"{rel}: invalid JSON ({e})")
            check_rows(dev, data, rel)
            rc, ct, _ = _git(repo, "log", "-1", "--format=%ct", "--", rel)
            if rc != 0 or not ct.isdigit():
                raise Refused(f"{rel}: no commit history for file")
            if int(ct) > now:
                raise Refused(f"{rel}: last commit is in the future (clock or history wrong)")
            ages[dev] = (now - int(ct)) // 86400
    except Refused as e:
        return REFUSED, str(e)
    oldest_dev = max(ages, key=ages.get)
    age = ages[oldest_dev]
    detail = ", ".join(f"{d}={a}d" for d, a in ages.items())
    msg = (f"oldest of {len(DEVICES)} response snapshots: {age} days ({oldest_dev}); {detail}; "
           f"limit {MAX_AGE_DAYS}")
    return (STALE if age > MAX_AGE_DAYS else FRESH), msg


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".")
    ap.add_argument("--dir", default="docs/reference-freshness")
    ap.add_argument("--now", type=int, default=None, help="epoch seconds (tests)")
    a = ap.parse_args(argv)
    now = int(time.time()) if a.now is None else a.now
    print(f"reference freshness: stage=response devices={','.join(DEVICES)} dir={a.dir}")
    verdict, msg = evaluate(a.repo, a.dir, now)
    print(f"{verdict}: {msg}")
    print(LIMIT)
    if verdict != FRESH:
        print(PUBLISH)
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
