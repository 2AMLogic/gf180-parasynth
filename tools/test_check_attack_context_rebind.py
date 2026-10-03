"""Re-bind provenance check: passes on the current record, red on injected defects.

Every control names the reason it must refuse for (``match=``), so a refusal for
some other reason -- e.g. a malformed staging file, whose JSONDecodeError is also
a ValueError -- cannot count as the control firing.
"""
import hashlib
import json
import subprocess
import sys

import pytest

import check_attack_context_rebind as chk


def staged(tmp_path, edit=None, report_edit=None):
    imp = json.loads((chk.MODEL / "ci-import.json").read_text())
    rep_bytes = (chk.MODEL / "report.json").read_bytes()
    if report_edit:
        rep = json.loads(rep_bytes)
        report_edit(rep)
        rep_bytes = json.dumps(rep).encode()
        imp["report_sha256"] = hashlib.sha256(rep_bytes).hexdigest()
    if edit:
        edit(imp)
    (tmp_path / "ci-import.json").write_text(json.dumps(imp))
    (tmp_path / "report.json").write_bytes(rep_bytes)
    for wav in chk.MODEL.glob("*.wav"):
        (tmp_path / wav.name).symlink_to(wav)
    return tmp_path / "ci-import.json", tmp_path / "report.json"


def test_current_state_is_not_refused():
    # OK-CI is the preferred path; a correct CI re-bind must not turn this red.
    got = chk.check(chk.MODEL / "ci-import.json", chk.MODEL / "report.json")
    assert got in {"OK-CI", "OK-LOCAL"}


def test_staging_is_faithful(tmp_path):
    assert chk.check(*staged(tmp_path)) == chk.check(
        chk.MODEL / "ci-import.json", chk.MODEL / "report.json")


def test_ci_record_ok(tmp_path):
    def e(i):
        i["workflow_url"] = "https://github.com/2AMLogic/gf180-parasynth/actions/runs/1"
    assert chk.check(*staged(tmp_path, e)) == "OK-CI"


def _drop_row(r):
    r["rows"].pop()


@pytest.mark.parametrize("name,edit,report_edit,reason", [
    ("no-identity-evidence", lambda i: i.pop("model_wavs_byte_identical_across_rebind"), None,
     "byte-identical WAV evidence"),
    ("partial-identity", lambda i: i.update(model_wavs_byte_identical_across_rebind=11), None,
     "byte-identical WAV evidence"),
    ("self-consistent-but-wrong-count",
     lambda i: i.update(model_audio_files=1, model_wavs_byte_identical_across_rebind=1), None,
     "does not match the report's 12 rows"),
    ("report-row-missing", None, _drop_row, "does not match the report's 11 rows"),
    ("no-produced-by", lambda i: i.pop("produced_by"), None, r"produced_by\.host"),
    ("no-rebound-from", lambda i: i.pop("rebound_from"), None, "rebound_from"),
    ("ci-no-rebound-from", lambda i: (i.pop("rebound_from"), i.update(
        workflow_url="https://github.com/2AMLogic/gf180-parasynth/actions/runs/1")), None,
     "rebound_from"),
    ("bad-url", lambda i: i.update(workflow_url="https://example.com/x"), None,
     "Actions run URL"),
    ("foreign-repo-url", lambda i: i.update(
        workflow_url="https://github.com/evil/x/actions/runs/1"), None, "Actions run URL"),
    ("stale-report-hash", lambda i: i.update(report_sha256="0" * 64), None, "report_sha256"),
    ("commit-mismatch", lambda i: i.update(source_commit="0" * 40), None, "source_commit"),
])
def test_injected_defect_refused(tmp_path, name, edit, report_edit, reason):
    with pytest.raises(ValueError, match=reason):
        chk.check(*staged(tmp_path, edit, report_edit))


def test_wav_on_disk_differs_refused(tmp_path):
    imp, rep = staged(tmp_path)
    victim = sorted(tmp_path.glob("*.wav"))[0]
    victim.unlink()
    victim.write_bytes(b"RIFF not the rendered audio")
    with pytest.raises(ValueError, match="WAV on disk differs"):
        chk.check(imp, rep)


def test_malformed_input_refused_not_masqueraded(tmp_path):
    imp, rep = staged(tmp_path)
    imp.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        chk.check(imp, rep)


def test_missing_input_is_refused_exit_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(chk.ROOT / "tools/check_attack_context_rebind.py"),
         str(tmp_path / "absent.json"), str(chk.MODEL / "report.json")],
        capture_output=True, text=True)
    assert proc.returncode == 2, proc.stderr
    assert proc.stdout.startswith("REFUSED: cannot read input")
    assert "Traceback" not in proc.stderr
