#!/usr/bin/env python3
"""Check that a re-bind record of the attack-context evidence is honest.

Outcomes: OK-CI, OK-LOCAL, REFUSED (exit 2).  A record with no CI run must carry
the byte-identical-WAV evidence; without it the tool refuses rather than
reporting.  This does not replace verify_attack_context_model.py (content);
it checks provenance (who produced it, and what supports it).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL = ROOT / "docs/scorecard/mono-attack-context/model"
RUN_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/actions/runs/\d+$")


def check(ci_import: Path, report: Path) -> str:
    """Return 'OK-CI' or 'OK-LOCAL'; raise ValueError (REFUSED) otherwise."""
    imp, rep = json.loads(ci_import.read_text()), json.loads(report.read_text())
    if imp.get("report_sha256") != hashlib.sha256(report.read_bytes()).hexdigest():
        raise ValueError("report_sha256 does not match report.json")
    if imp.get("source_commit") != rep.get("source_commit"):
        raise ValueError("source_commit differs between ci-import.json and report.json")
    url = imp.get("workflow_url")
    if url is not None:
        if not RUN_URL.match(str(url)):
            raise ValueError(f"workflow_url is not an Actions run URL: {url!r}")
        return "OK-CI"
    produced = imp.get("produced_by") or {}
    for key in ("host", "command", "why_not_ci"):
        if not produced.get(key):
            raise ValueError(f"locally produced re-bind lacks produced_by.{key}")
    total = imp.get("model_audio_files")
    if not total or imp.get("model_wavs_byte_identical_across_rebind") != total:
        raise ValueError("locally produced re-bind lacks byte-identical WAV evidence "
                         "(model_wavs_byte_identical_across_rebind must equal model_audio_files)")
    if not imp.get("rebound_from"):
        raise ValueError("locally produced re-bind does not record rebound_from")
    return "OK-LOCAL"


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("ci_import", type=Path, nargs="?", default=MODEL / "ci-import.json")
    p.add_argument("report", type=Path, nargs="?", default=MODEL / "report.json")
    a = p.parse_args()
    try:
        print(check(a.ci_import, a.report))
    except ValueError as e:
        print(f"REFUSED: {e}")
        sys.exit(2)
