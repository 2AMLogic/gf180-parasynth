#!/usr/bin/env python3
"""The four rows of #1939 at the CALLER boundary: what each shipped entry
point does with a record that is absent, truncated or unreadable.

Every "today" below was RUN on origin/main @ e2d1bae7 (2026-11-28) before any
edit, by driving the caller -- not read off the callee. The issue's table was
an AST-walk inventory, and one of its four rows did not reproduce:

  row  callee (narrow callers)             today, before the fix
  1    manifest.load_json_refused          absent / unreadable record -> bare
       (refprofile, check_reference_       OSError: a traceback at each
       freeze, stage_case)
  2    mono_m1a_score.load_reference       NOT REACHABLE: `REFUSED`, exit 2,
       (rescore_m1a_attack_v3,             at all three callers
       measure_m1a_drive, phase_aware_m1a)
  3    mono_m5a_score.measure              frozen manifest absent ->
       (lead_drive_337, run_case)          FileNotFoundError: traceback, exit 1
                                          at lead_drive_337; a record carrying
                                          a `traceback` field at run_case
  4    werner_fig4.from_artifact           malformed record -> traceback, exit
       (werner_fig4.main, werner_fig9)     1 at main(); escapes check()

Row 2 stays in this file as the control on "not reachable": a row closed that
way is a claim about the callee, and this is what keeps it checked.

Deliberately NOT refused, so not tested as if it were: a malformed M5A/M5B
manifest (bad JSON, a wrong-typed value) is `mono_m5a_score.load_reference`'s
stated execution error (3a4648d1), and `mono_m1a_score.load_reference` keeps
the same rule for a wrong-typed value (test_m1a_refusal_boundaries.py). An
execution error stays a traceback; it is never relabelled a refusal.
"""
from __future__ import annotations

import functools
import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _d in ("model", "tools"):
    if str(ROOT / _d) not in sys.path:
        sys.path.insert(0, str(ROOT / _d))

import cymbal_band_balance as bb                                # noqa: E402
import check_reference_freeze as cf                             # noqa: E402
import lead_drive_337 as ld                                     # noqa: E402
import measure_m1a_drive as drive_m1a                           # noqa: E402
import mono_m1a_score as bass                                   # noqa: E402
import mono_m5a_score as m5a                                    # noqa: E402
import phase_aware_m1a as phase_m1a                             # noqa: E402
import refprofile as rp                                         # noqa: E402
import reference_suites as rs                                    # noqa: E402
import rescore_m1a_attack_v3 as rescore_m1a                     # noqa: E402
import run_case as rc                                           # noqa: E402
import stage_case as st                                         # noqa: E402
import werner_fig4 as wf                                        # noqa: E402
import werner_fig9 as w9                                        # noqa: E402

MALFORMED_FIG4 = {
    "truncated": b'{"curves": ',
    "non-utf8": b'{"curves": "\xff\xfe"}',
    "no-curves": b"{}",
}


def unreadable(path: pathlib.Path, how: str, text: str = "{}") -> pathlib.Path:
    """A present path that cannot be read: a directory, or mode 000. The 000
    case asserts its own precondition -- a root runner reads the file anyway,
    and a verdict from that run is about the runner, not the reader."""
    if how == "directory":
        path.mkdir()
        return path
    path.write_text(text)
    path.chmod(0o000)
    try:
        path.read_text()
    except OSError:
        return path
    pytest.skip(f"{path.name} is still readable after chmod 000 (running as root?)")


def cli(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / script), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=120)


def stated(text: str, named: pathlib.Path) -> bool:
    """The file is named and nothing is a traceback."""
    return "Traceback" not in text and str(named) in text


# ===========================================================================
# Row 1 -- manifest.load_json_refused, whose `Refused`-only callers were
# let a bare OSError escape
# ===========================================================================
@pytest.mark.parametrize("which", ["profile", "notes"])
@pytest.mark.parametrize("how", ["directory", "unreadable"])
def test_row1_refprofile_load_profile_refuses_a_file_it_cannot_read(
        tmp_path, monkeypatch, which, how):
    profile = tmp_path / "profile.json"
    broken = profile if which == "profile" else rp.notes_path_for(profile)
    good = {profile: rp.PROFILE_JSON.read_text()}
    if which == "notes":
        real_notes = rp.notes_path_for(rp.PROFILE_JSON)
        good[broken] = (real_notes.read_text() if real_notes.exists()
                        else json.dumps({"schema": rp.SCHEMA_NOTES}))
    for path, text in good.items():
        if path is not broken:
            path.write_text(text)
    if how == "directory":
        broken.mkdir()
    else:
        unreadable(broken, how, good[broken])
    monkeypatch.setattr(rp, "PROFILE_JSON", profile)
    reason = "Is a directory" if how == "directory" else "Permission denied"
    with pytest.raises(rp.Refused, match=rf"{broken.name}.*could not be read \({reason}\)"):
        rp.load_profile()


def test_row1_refprofile_load_profile_still_names_an_absent_profile(tmp_path, monkeypatch):
    """The pre-check keeps its own message: there is no file to name a way
    to restore."""
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(rp, "PROFILE_JSON", absent)
    with pytest.raises(rp.Refused, match=r"no frozen reference profile at .*absent\.json"):
        rp.load_profile()


def _freeze_root(tmp_path: pathlib.Path, how: str) -> pathlib.Path:
    """A tree the freeze tool accepts a --root for: the committed freeze
    manifest, and a corpus manifest in the state `how` names (none at all
    for `absent`)."""
    root = tmp_path / "root"
    (root / cf.CORPUS_MANIFEST).parent.mkdir(parents=True)
    shutil.copy2(rs.FREEZE_MANIFEST, root / rs.FREEZE_MANIFEST.name)
    if how != "absent":
        unreadable(root / cf.CORPUS_MANIFEST, how, (ROOT / cf.CORPUS_MANIFEST).read_text())
    return root


@pytest.mark.parametrize("how", ["directory", "unreadable"])
def test_row1_check_reference_freeze_refuses_a_corpus_manifest_it_cannot_read(
        tmp_path, capsys, how):
    root = _freeze_root(tmp_path, how)
    code = cf.main(["--root", str(root), "--freeze-manifest", str(root / rs.FREEZE_MANIFEST.name)])
    out = capsys.readouterr().out
    assert code == cf.REFUSED
    assert out.startswith("REFUSED") and "corpus-lineage.json" in out and "could not be read" in out
    assert "FAIL" not in out and "PASS" not in out


def test_row1_check_reference_freeze_still_refuses_an_absent_corpus_manifest(tmp_path, capsys):
    """The `exists()` pre-check at check_reference_freeze.py:87 fires first
    for a missing file, with its own message (the issue's edge case)."""
    root = _freeze_root(tmp_path, "absent")
    code = cf.main(["--root", str(root), "--freeze-manifest", str(root / rs.FREEZE_MANIFEST.name)])
    out = capsys.readouterr().out
    assert code == cf.REFUSED
    assert out.startswith("REFUSED") and "corpus manifest missing" in out


def test_row1_check_reference_freeze_cli_exits_two_without_a_traceback(tmp_path):
    root = _freeze_root(tmp_path, "directory")
    r = cli("check_reference_freeze.py", "--root", str(root),
            "--freeze-manifest", str(root / rs.FREEZE_MANIFEST.name))
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert r.stdout.startswith("REFUSED") and "corpus-lineage.json" in r.stdout


def test_row1_stage_case_read_refuses_a_record_it_cannot_read(tmp_path):
    record = tmp_path / "runs" / "r1" / "manifest.json"
    record.parent.mkdir(parents=True)
    unreadable(record, "directory")
    with pytest.raises(st.manifest.Refused, match=r"could not be read \(Is a directory\)"):
        st._read(tmp_path, "render", "r1")


def test_row1_check_m1a_selection_still_refuses_a_missing_record_with_exit_two(tmp_path):
    """The wide caller, unchanged in kind: exit 2 and `REFUSED:` before and
    after. (Before, the text was the bare `[Errno 2]`; the callee now states
    it.)"""
    absent = tmp_path / "absent.json"
    r = cli("check_m1a_selection.py", "--record", str(absent))
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert r.stdout.startswith("REFUSED:") and str(absent) in r.stdout


# ===========================================================================
# Row 2 -- mono_m1a_score.load_reference: the escape does not reproduce
# ===========================================================================
@pytest.mark.parametrize("module", [rescore_m1a, drive_m1a, phase_m1a],
                         ids=lambda m: m.__name__)
@pytest.mark.parametrize("how", ["absent", "truncated", "directory"])
def test_row2_m1a_callers_refuse_a_manifest_that_is_absent_truncated_or_unreadable(
        tmp_path, monkeypatch, capsys, module, how):
    """Each caller's `main()`, with the one path the callee takes pointed at
    the broken file. The file's name is NOT asserted, because it is not
    always there: for the truncated file the refusal does not name it -- a
    JSONDecodeError carries no filename, and mono_m1a_score.py:58 prints the
    exception alone (recorded in #1939's PR; a separate change)."""
    p = tmp_path / f"{how}.json"
    if how == "truncated":
        p.write_text('{"case_id": ')
    elif how == "directory":
        p.mkdir()
    monkeypatch.setattr(bass, "load_reference", functools.partial(bass.load_reference, p))
    capsys.readouterr()
    assert module.main() == 2
    out = capsys.readouterr().out
    assert out.startswith("REFUSED: M1A reference unavailable")
    assert "Traceback" not in out


# ===========================================================================
# Row 3 -- mono_m5a_score.measure: the frozen manifest read and the model WAV
# write were outside every try
# ===========================================================================
def test_row3_lead_drive_337_render_refuses_a_frozen_manifest_that_is_absent(tmp_path, monkeypatch):
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(m5a, "MANIFEST", absent)
    with pytest.raises(ld.Refused, match=r"frozen M5A manifest .*absent\.json"):
        ld.render("M5A", ld.BASE_DRIVE)


def test_row3_lead_drive_337_cli_exits_two_without_a_traceback(tmp_path):
    """The shipped `__main__` boundary, with the one override the loader
    documents for exercising a missing reference (`MANIFEST`)."""
    absent = str(tmp_path / "absent.json")
    script = ("import pathlib, runpy, sys; sys.path.insert(0, 'tools'); "
              "import mono_m5a_score as sc; "
              f"sc.MANIFEST = pathlib.Path({absent!r}); "
              "runpy.run_path('tools/lead_drive_337.py', run_name='__main__')")
    r = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 2 and "Traceback" not in r.stderr
    assert r.stderr.startswith("REFUSED:") and "absent.json" in r.stderr


def test_row3_run_case_records_a_stated_refusal_not_an_execution_error(tmp_path, monkeypatch):
    """`run_case` never showed a traceback: its deliberately-total guard
    records any exception as REFUSED. What changes is the KIND -- before, the
    note carried the exception's type name and the record a `traceback`
    field, which is how an execution error is recorded."""
    absent = tmp_path / "absent.json"
    monkeypatch.setattr(m5a, "MANIFEST", absent)
    case = next(c for c in rc.load_cases() if c["case_id"] == "M5A")
    record = rc._measure_case(case, tmp_path / "refs", keep_audio=False,
                              results_dir=str(tmp_path / "results"))
    assert record["note"].startswith("REFUSED: ") and str(absent) in record["note"]
    assert not record["note"].startswith("REFUSED: FileNotFoundError")
    assert "traceback" not in record
    assert all(not m["valid"] for m in record["metrics"].values())


# ===========================================================================
# Row 4 -- werner_fig4.from_artifact: the malformed-record classes
# ===========================================================================
@pytest.mark.parametrize("name", sorted(MALFORMED_FIG4))
def test_row4_werner_fig4_cli_refuses_a_malformed_artifact_with_exit_three(tmp_path, name):
    p = tmp_path / f"{name}.json"
    p.write_bytes(MALFORMED_FIG4[name])
    r = cli("werner_fig4.py", "--artifact", str(p), "--check")
    assert r.returncode == 3 and stated(r.stderr, p)
    assert r.stderr.startswith("REFUSED:")


@pytest.mark.parametrize("name", sorted(MALFORMED_FIG4))
def test_row4_werner_fig9_gate_fails_naming_a_malformed_figure4_record(tmp_path, monkeypatch, name):
    """`check()` catches `Refused` and turns it into a FAIL line naming the
    reason; every other class escaped it as a traceback."""
    p = tmp_path / f"{name}.json"
    p.write_bytes(MALFORMED_FIG4[name])
    data, meta, _ = w9.from_artifact()
    monkeypatch.setattr(wf, "from_artifact", functools.partial(wf.from_artifact, p))
    ok, lines = w9.check(data, meta)
    assert ok is False
    assert any("FAIL shared" in line for line in lines)
    assert any(str(p) in line for line in lines)


@pytest.mark.parametrize("name", sorted(MALFORMED_FIG4))
def test_row4_cymbal_band_balance_filter_record_still_refuses_a_malformed_record(tmp_path, name):
    """The wide caller, unchanged in kind: REFUSED before and after. Its own
    conversion of these classes at the call site is what the callee now does
    for every caller."""
    p = tmp_path / f"{name}.json"
    p.write_bytes(MALFORMED_FIG4[name])
    with pytest.raises(bb.Refused, match=r"not a readable werner_fig4 record") as raised:
        bb.filter_record(p)
    assert str(p) in str(raised.value)
