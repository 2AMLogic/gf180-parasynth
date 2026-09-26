"""T-RELEASE-BOUND's stale controls (#278): each is caught on the real tree for
exactly the reason it injects, never edits the real manifest or tree, and is
NOT counted as caught when the apparatus under it is broken, the counterexample
is a no-op, or the checker fails for another reason."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for _p in (HERE, HERE.parent, ROOT / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import release_manifest as rm            # noqa: E402
import stale_controls as sc              # noqa: E402
import trial                             # noqa: E402


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _run(case, out):
    r = subprocess.run([sys.executable, str(HERE / "stale_controls.py"), case, "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    verdict = [ln for ln in r.stdout.splitlines() if ln.startswith(sc.PREFIX)]
    return r.returncode, verdict, r.stdout


# ---- caught on the real tree, which is only read ----------------------------------
def test_manifest_control_is_stale_at_exactly_the_substituted_fields(tmp_path):
    before = _sha(rm.MANIFEST)
    rc, verdict, out = _run("manifest", tmp_path)
    assert rc == 1, out
    assert len(verdict) == 1 and verdict[0].startswith("stale_control: STALE"), out
    assert "commands.held-default.cmds_sha256" in verdict[0]
    assert "unmodified copy: release_manifest: BOUND" in out      # the precondition held
    assert _sha(rm.MANIFEST) == before                              # the real one untouched
    stale = json.loads((tmp_path / "stale-manifest.json").read_text())
    assert stale["commands"]["held-default"]["cmds_sha256"] == _sha(sc.LEGACY_CAPTURE)


def test_binding_control_is_stale_naming_exactly_voice_dp(tmp_path):
    before = _sha(ROOT / sc.BINDING_SUBSTITUTE)
    rc, verdict, out = _run("binding", tmp_path)
    assert rc == 1, out
    assert verdict[0].startswith("stale_control: STALE") and "rtl-sketch/voice_dp.v" in verdict[0]
    assert "unmodified copy: BOUND" in out
    assert _sha(ROOT / sc.BINDING_SUBSTITUTE) == before
    rec = json.loads((tmp_path / "binding-control.json").read_text())
    assert rec["unmodified_copy"]["rc"] == 0 and rec["substituted_copy"]["rc"] == 1


# ---- when the control must NOT count as caught -------------------------------------
def test_a_manifest_checker_that_compares_nothing_is_reported_not_caught(tmp_path, monkeypatch):
    """Injected apparatus bug: release_manifest's diff sees no differences."""
    monkeypatch.setattr(rm, "_diff", lambda a, b, path="": [])
    assert sc.manifest_control(tmp_path) == 0          # BOUND: the counterexample was accepted


def test_a_noop_manifest_substitution_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "LEGACY_CAPTURE", HERE / "evidence/held-note/default/capture.cmds")
    with pytest.raises(sc.Refused, match="change nothing"):
        sc.manifest_control(tmp_path)


def test_an_unmodified_copy_that_is_not_bound_refuses(tmp_path, monkeypatch):
    """If the committed manifest is itself stale, STALE on the counterexample
    proves nothing about the counterexample."""
    m = json.loads(rm.MANIFEST.read_text())
    m["image"]["bitstream_sha256"] = "0" * 64
    p = tmp_path / "committed.json"
    p.write_text(json.dumps(m))
    monkeypatch.setattr(rm, "MANIFEST", p)
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(sc.Refused, match="not BOUND"):
        sc.manifest_control(out)


def test_stale_for_another_field_is_not_a_catch(tmp_path, monkeypatch):
    real = rm.check

    def check(path=rm.MANIFEST):
        if Path(path).name == "stale-manifest.json":
            return "STALE", "differs from a fresh derivation at: image.bitstream_sha256"
        return real(path)
    monkeypatch.setattr(rm, "check", check)
    with pytest.raises(sc.Refused, match="different reason"):
        sc.manifest_control(tmp_path)


def test_unreadable_committed_manifest_refuses_the_control(tmp_path, monkeypatch):
    p = tmp_path / "committed.json"
    p.write_bytes(rm.MANIFEST.read_bytes()[:2000])
    monkeypatch.setattr(rm, "MANIFEST", p)
    with pytest.raises(sc.Refused, match="unreadable"):
        sc.manifest_control(tmp_path)


def test_a_noop_binding_substitution_refuses(tmp_path, monkeypatch):
    """uart_bridge.v is byte-identical at the image's source commit."""
    monkeypatch.setattr(sc, "BINDING_SUBSTITUTE", "rtl-sketch/uart_bridge.v")
    with pytest.raises(sc.Refused, match="change nothing"):
        sc.binding_control(tmp_path)


# ---- the checker itself: unreadable evidence is REFUSED, not STALE -----------------
def test_unreadable_manifest_is_refused_not_stale(tmp_path):
    """Before #278 this was a JSONDecodeError traceback exiting 1 -- STALE's code."""
    p = tmp_path / "m.json"
    p.write_bytes(rm.MANIFEST.read_bytes()[:2000])
    assert rm.check(p)[0] == "REFUSED"
    assert rm.main(["--manifest", str(p)]) == 2


def test_missing_manifest_is_refused(tmp_path):
    assert rm.main(["--manifest", str(tmp_path / "absent.json")]) == 2


# ---- the committed trial ----------------------------------------------------------
def test_committed_release_bound_declares_both_stale_controls():
    mode = trial.load_registry()["trials"]["T-RELEASE-BOUND"]["modes"]["check"]
    ctl = {c["id"]: c for c in mode["controls"]}
    assert set(ctl) == {"stale-manifest", "stale-binding"}
    for c in ctl.values():
        assert c["checker"] == "fpga/release/stale_controls.py"
        assert c["interpret"] == "bound_text" and c["token_prefix"] == sc.PREFIX


def test_release_bound_trial_passes_with_both_controls_caught(tmp_path):
    run_dir, rec = trial.run_trial("T-RELEASE-BOUND", out_base=tmp_path)
    assert rec["verdict"] == trial.PASS, rec["verdict_reasons"]
    assert {c["id"]: c["caught"] for c in rec["controls"]} == {"stale-manifest": True,
                                                              "stale-binding": True}
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems


@pytest.mark.parametrize("control", ["stale-manifest", "stale-binding"])
def test_each_stale_control_as_the_candidate_is_fail(tmp_path, control):
    run_dir, rec = trial.run_trial("T-RELEASE-BOUND", as_candidate=control, out_base=tmp_path)
    assert rec["verdict"] == trial.FAIL, rec["verdict_reasons"]
    ok, problems, _ = trial.check_receipt(run_dir / "receipt.json")
    assert ok, problems
