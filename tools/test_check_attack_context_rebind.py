"""Re-bind provenance check: passes on the current record, red on injected defects."""
import hashlib
import json
import shutil

import pytest

import check_attack_context_rebind as chk


def staged(tmp_path, edit=None):
    imp = json.loads((chk.MODEL / "ci-import.json").read_text())
    if edit:
        edit(imp)
    (tmp_path / "ci-import.json").write_text(json.dumps(imp))
    shutil.copy(chk.MODEL / "report.json", tmp_path / "report.json")
    return tmp_path / "ci-import.json", tmp_path / "report.json"


def test_current_state_is_ok_local():
    assert chk.check(chk.MODEL / "ci-import.json", chk.MODEL / "report.json") == "OK-LOCAL"


def test_ci_record_ok(tmp_path):
    def e(i):
        i["workflow_url"] = "https://github.com/2AMLogic/gf180-parasynth/actions/runs/1"
    assert chk.check(*staged(tmp_path, e)) == "OK-CI"


@pytest.mark.parametrize("name,edit", [
    ("no-identity-evidence", lambda i: i.pop("model_wavs_byte_identical_across_rebind")),
    ("partial-identity", lambda i: i.update(model_wavs_byte_identical_across_rebind=11)),
    ("no-produced-by", lambda i: i.pop("produced_by")),
    ("no-rebound-from", lambda i: i.pop("rebound_from")),
    ("bad-url", lambda i: i.update(workflow_url="https://example.com/x")),
    ("stale-report-hash", lambda i: i.update(report_sha256="0" * 64)),
    ("commit-mismatch", lambda i: i.update(source_commit="0" * 40)),
])
def test_injected_defect_refused(tmp_path, name, edit):
    with pytest.raises(ValueError):
        chk.check(*staged(tmp_path, edit))
