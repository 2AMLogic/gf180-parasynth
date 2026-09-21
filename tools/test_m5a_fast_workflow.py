"""Preconditions for the focused CI job must be asserted by its own suite."""
from pathlib import Path

import yaml


def test_m5a_fast_job_fetches_origin_main_for_the_batch_provenance_guard():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/rungs.yml").read_text())
    checkout = next(step for step in workflow["jobs"]["m5a-fast"]["steps"]
                    if step.get("uses", "").startswith("actions/checkout"))
    assert str(checkout.get("with", {}).get("fetch-depth")) == "0", (
        "tools/test_run_case.py requires origin/main; m5a-fast must fetch full history")
