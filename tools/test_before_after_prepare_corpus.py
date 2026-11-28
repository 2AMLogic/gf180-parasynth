"""False-green cases for the two-SHA driver (#700), in the style of test_run_all."""
from __future__ import annotations
import errno, json, os, pathlib, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import before_after_prepare_corpus as ba                         # noqa: E402
import run_all as ra                                             # noqa: E402


def res(state, rc, out=""):
    return {"state": state, "rc": rc, "out": out, "cmd": "x", "secs": 0.0}


def test_corpus_absent_is_refused_not_skipped(tmp_path):
    with pytest.raises(ba.Refused):
        ba.corpus_manifest(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ba.Refused):
        ba.corpus_manifest(tmp_path / "empty")


def test_main_exits_2_when_corpus_absent(tmp_path, capsys):
    assert ba.main(["--refs", str(tmp_path / "nope")]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_manifest_changes_when_a_file_changes(tmp_path):
    (tmp_path / "a.wav").write_bytes(b"1")
    m1 = ba.corpus_manifest(tmp_path)
    (tmp_path / "a.wav").write_bytes(b"2")
    assert ba.corpus_manifest(tmp_path) != m1


def test_exit_2_is_refused_not_failed_and_exit_1_is_failed():
    assert ba.classify(res(ra.NO_VERDICT, 2)) == ba.REFUSED
    assert ba.classify(res(ra.FAIL, 1)) == ba.FAILED
    assert ba.classify(res(ra.NO_VERDICT, None)) == ba.REFUSED   # timeout
    assert ba.classify(res(ra.PASS, 0)) == ba.OK


def test_before_control_requires_headline_from_a_job_that_ran():
    ba.before_control({"j": res(ra.PASS, 0, "spread 6.07 dB")})
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.PASS, 0, "spread 5.9 dB")})
    # headline printed by a job that did NOT run proves nothing
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.NO_VERDICT, 2, "6.07")})
    # substring of another number must not satisfy it
    with pytest.raises(ba.Refused):
        ba.before_control({"j": res(ra.PASS, 0, "16.075 dB")})


# ---------------------------------------------------------------------------
# the CLI's --json destination (#2098, a slice of the #2095 class)
#
# THE DEFECT, red-first on origin/main @ 8eb04e5b (2026-11-28): `main` wrote
# `a.json` with `write_text` after both arms had run, and nothing made the
# parent directory. Under a missing one the write raised FileNotFoundError
# out of `main`: exit 1 -- this tool's "a job failed -- a RESULT" -- for two
# arms that had run, with the record lost.
#
# THE KNOWN ANSWER, from tools/exit_contract.py and not from the module
# under test: a destination that cannot be written is a precondition that was
# not met, so REFUSED (2) whichever way the arms went -- never 1, a result
# whose evidence is gone. Refused before an arm runs where the path says so
# itself; refused after them otherwise, with the table still on stdout.
#
# WHAT RUNS FOR REAL: `main`, `corpus_manifest` (one real file in a real
# directory), `before_control`, `table` and `classify`. Doubled: `run_arm`
# (counted), which in production adds a worktree per SHA and runs four jobs.
# ---------------------------------------------------------------------------
HEADLINE_JOB = "spread 6.07 dB"


class _Driver:
    """The real CLI with its two SHAs doubled."""

    def __init__(self, tmp_path, monkeypatch):
        self.refs = tmp_path / "refs"
        self.refs.mkdir()
        (self.refs / "a.WAV").write_bytes(b"clip")
        self.arms = []                      # the SHAs asked for, in order
        self.after_job = res(ra.PASS, 0, HEADLINE_JOB)
        monkeypatch.setattr(ba, "run_arm", self.run_arm)

    def run_arm(self, sha, refs, timeout, py):
        self.arms.append(sha)
        # the second arm of EACH call; the list is cumulative across calls
        job = self.after_job if len(self.arms) % 2 == 0 else res(ra.PASS, 0, HEADLINE_JOB)
        return dict(sha=f"{sha}-full", jobs={"conga_spread measure": job})


@pytest.fixture
def driver(tmp_path, monkeypatch):
    return _Driver(tmp_path, monkeypatch)


def _full_disk(target):
    """`Path.write_text` that fails for `target` only."""
    real = pathlib.Path.write_text

    def write_text(self, data, *args, **kwargs):
        if self == target:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(self, data, *args, **kwargs)

    return write_text


@pytest.mark.parametrize("target, reason", [
    ("{tmp}", "names an existing directory; name the record file"),
    ("{tmp}/a-file/before-after.json", "no record can be written there: [Errno 17] File exists"),
    ("nul\x00dir/before-after.json", "no record can be written there: embedded null byte"),
], ids=["a-directory", "parent-is-a-file", "parent-has-a-nul-byte"])
def test_an_unwritable_json_destination_is_refused_before_any_arm_runs(driver, tmp_path, capsys,
                                                                       target, reason):
    (tmp_path / "a-file").write_text("not a directory\n")
    rc = ba.main(["--refs", str(driver.refs), "--json", target.format(tmp=tmp_path)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and reason in err, err
    assert driver.arms == [], "an arm ran for a destination that could not be written"
    assert "corpus" not in out, out


def test_a_json_record_under_a_missing_directory_is_created_and_written(driver, tmp_path, capsys):
    # not under the corpus: the manifest hashes every file there, and a record
    # written into it would change what a second reading of the manifest says
    target = tmp_path / "no-such-dir" / "before-after.json"
    rc = ba.main(["--refs", str(driver.refs), "--json", str(target)])
    out, _ = capsys.readouterr()
    assert rc == 0, out
    assert driver.arms == [ba.BEFORE_DEFAULT, ba.AFTER_DEFAULT]
    record = json.loads(target.read_text())
    assert record["manifest"] == ba.corpus_manifest(driver.refs)
    assert record["before"]["sha"] == ba.BEFORE_DEFAULT + "-full"
    assert record["after"]["sha"] == ba.AFTER_DEFAULT + "-full"
    assert "conga_spread measure" in out and "output identical (did not move)" in out, out


def test_a_write_failure_after_both_arms_is_refused_with_the_table_kept(driver, tmp_path, capsys,
                                                                         monkeypatch):
    target = tmp_path / "before-after.json"
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(target))
    rc = ba.main(["--refs", str(driver.refs), "--json", str(target)])
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert len(driver.arms) == 2 and "conga_spread measure" in out, out
    assert "REFUSED" in err and "record not written" in err, err
    assert "No space left on device" in err and "Traceback" not in err, err
    assert not target.exists()


def test_a_refused_write_is_not_a_fail_when_a_job_failed(driver, tmp_path, capsys, monkeypatch):
    """The fourth corner. A FAILED after-arm is a result and exits 1 while its
    record exists; with the record unwritten the 1 must not be reached."""
    driver.after_job = res(ra.FAIL, 1, "boom")
    argv = ["--refs", str(driver.refs), "--json", str(tmp_path / "before-after.json")]
    assert ba.main(argv) == 1
    assert "corpus" in capsys.readouterr().out
    monkeypatch.setattr(pathlib.Path, "write_text", _full_disk(tmp_path / "before-after.json"))
    assert ba.main(argv) == 2
    out, err = capsys.readouterr()
    assert "conga_spread measure" in out and "FAILED" in out, out
    assert "REFUSED" in err and "record not written" in err, err


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root writes into a read-only directory")
def test_a_read_only_parent_is_refused_and_leaves_no_record(driver, tmp_path, capsys):
    """Rule 8: the input that defeats the pre-flight. The parent exists, so
    `mkdir(exist_ok=True)` accepts it; only the guard around the write can
    refuse it."""
    parent = tmp_path / "read-only"
    parent.mkdir()
    parent.chmod(0o555)
    try:
        rc = ba.main(["--refs", str(driver.refs), "--json", str(parent / "before-after.json")])
        written = list(parent.iterdir())
    finally:
        parent.chmod(0o700)
    out, err = capsys.readouterr()
    assert rc == 2, (rc, out, err)
    assert "REFUSED" in err and "record not written" in err, err
    assert "conga_spread measure" in out, out
    assert written == []
