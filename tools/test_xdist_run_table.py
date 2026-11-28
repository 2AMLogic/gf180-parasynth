#!/usr/bin/env python3
"""Controls for tools/xdist_run_table.py (issue #1947).

The table is the doc's numbers. A number typed by hand from a log is a claim
(#1816's measurement recorded seven that were wrong before they were right),
so every cell is parsed from a file and each parser is checked against a
line of the exact shape the producer writes. The producers are real:
``tools/run_with_rss.py``, ``tools/mem_peak.py``, pytest's summary line, the
workflow's env record and the GitHub jobs API. A parser that cannot find its
line must say so (REFUSED), never print a blank cell that reads as zero.
"""
from __future__ import annotations
import json
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import xdist_run_table as xt  # noqa: E402

ENV = """date_utc 2026-11-28T14:38:01Z
head 7a3455a6d2c6fe082cdaa07ca2b69800849cba84
tools_tree d81276131e0bd255ef6b81bb6e16080049d8787c
python 3.12.16
cpu_count 4 affinity 4
cpu_max (exit 1, no output)
lscpu CPU(s): 4 | Model name: Intel(R) Xeon(R) Platinum 8488C | Thread(s) per core: 2 | Core(s) per socket: 2 | Socket(s): 1
meminfo MemTotal: 15963940 kB | MemAvailable: 13330192 kB
packages pytest 9.2.0 pytest-xdist 3.8.0 execnet 2.1.2 numpy 2.5.5 scipy 1.18.1 soundfile 0.14.0 PyYAML 6.0.4
iverilog Icarus Verilog version 12.0 (stable) ()
"""
LOG = """....
[rss] ulimit -s unlimited (hard unlimited)  cpus 4/4  MemTotal 15592 MB  MemAvailable 13018 MB  Swap 0 MB
[rss] thread limits {'OPENBLAS_NUM_THREADS': '1'}
[mem] sampling every 0.5s  MemTotal 15592 MB
============================= slowest durations =============================
12.34s call     tools/test_x.py::test_a
6550 passed, 27 skipped, 2 xfailed, 3 warnings in 4366.77s (1:12:46)
[rss] peak RSS 1822 MB  user 6012.3s  sys 401.7s  wall 4380.1s  minflt 1  majflt 0  MemAvailable now 12000 MB
[mem] peak tree RSS 3091 MB at 2000.5s  samples 8760  max processes 12  wall 4381.0s
[mem] exit 0
[rss] exit 0
"""
MEM = {"peak_tree_rss_mb": 3091, "peak_at_s": 2000.5, "samples": 8760,
       "interval_s": 0.5, "max_processes": 12, "wall_s": 4381.0, "exit": 0}
UNION = {"verdict": "PASS", "expected": 6678, "missing": [], "extra": [],
         "duplicate": [], "outcome_diffs": [],
         "counts": {"PASSED": 6649, "SKIPPED": 27, "XFAILED": 2}}
JOBS = {"jobs": [
    {"name": "tools-n2-r1", "conclusion": "success", "runner_name": "GitHub Actions 1000679031",
     "started_at": "2026-11-28T14:37:08Z", "completed_at": "2026-11-28T15:02:19Z",
     "steps": [{"name": "every tools/ test, by directory, on n2 workers (repeat 1)",
                "started_at": "2026-11-28T14:37:40Z", "completed_at": "2026-11-28T15:02:05Z"}]},
    {"name": "tools-n1-r1", "conclusion": "success", "runner_name": "GitHub Actions 1000679035",
     "started_at": "2026-11-28T14:37:08Z", "completed_at": "2026-11-28T15:50:40Z",
     "steps": []},
]}


def write_run(root: pathlib.Path, label: str, repeat: int, env=ENV, log=LOG,
              mem=MEM, union=UNION) -> pathlib.Path:
    d = root / f"xdist-{label}-r{repeat}"
    d.mkdir(parents=True)
    (d / "env.txt").write_text(env)
    (d / "pytest.log").write_text(log)
    (d / "mem.json").write_text(json.dumps(mem))
    (d / "union.json").write_text(json.dumps(union))
    return d


# ---- the parsers, against the producers' exact line shapes --------------------
def test_env_record_is_key_value_and_keeps_what_it_cannot_read():
    env = xt.read_env(ENV)
    assert env["cpu_count"] == 4 and env["affinity"] == 4
    assert env["head"] == "7a3455a6d2c6fe082cdaa07ca2b69800849cba84"
    assert env["MemTotal_mb"] == 15589 and env["python"] == "3.12.16"
    assert "pytest-xdist 3.8.0" in env["packages"]
    assert env["cpu_max"] == "(exit 1, no output)"   # said, not hidden


def test_pytest_log_yields_the_summary_counts_and_pytest_s_own_seconds():
    got = xt.read_pytest_log(LOG)
    assert got["passed"] == 6550 and got["skipped"] == 27 and got["xfailed"] == 2
    assert got["pytest_s"] == pytest.approx(4366.77)
    assert got["cpu_s"] == pytest.approx(6012.3 + 401.7)
    assert got["step_wall_s"] == pytest.approx(4380.1)
    assert got["max_single_rss_mb"] == 1822


@pytest.mark.parametrize("drop, named", [("6550 passed", "summary line"),
                                         ("[rss] peak RSS", "[rss] peak RSS")])
def test_a_log_missing_a_load_bearing_line_is_refused_not_zeroed(drop, named):
    with pytest.raises(xt.Refusal) as e:
        xt.read_pytest_log(LOG.replace(drop, "XXXX"))
    assert named in str(e.value), "the refusal must name the line it could not find"


def test_a_summary_line_under_xdist_is_read_the_same_way():
    got = xt.read_pytest_log(LOG.replace(" in 4366.77s (1:12:46)", " in 2512.40s (0:41:52)"))
    assert got["pytest_s"] == pytest.approx(2512.40)


def test_a_failing_summary_line_is_read_not_refused():
    """A red candidate is a finding the table must show, with its counts."""
    got = xt.read_pytest_log(LOG.replace("6550 passed", "6549 passed, 1 failed"))
    assert got["passed"] == 6549 and got["failed"] == 1


# ---- the table ------------------------------------------------------------------
def test_one_run_dir_becomes_one_row_with_every_cell_from_a_file(tmp_path):
    d = write_run(tmp_path, "n2", 1)
    run = xt.read_run_dir(d)
    assert (run.label, run.repeat) == ("n2", 1)
    assert run.job_wall_min is None            # no jobs record yet
    assert run.peak_tree_rss_mb == 3091 and run.max_processes == 12
    assert run.verdict == "PASS" and run.counts == UNION["counts"]


def test_the_jobs_api_record_supplies_whole_job_minutes_per_name(tmp_path):
    d = write_run(tmp_path, "n2", 1)
    write_run(tmp_path, "n1", 1)
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(JOBS))
    t = xt.build_table([d, tmp_path / "xdist-n1-r1"], jobs)
    by = {r.label: r for r in t.runs}
    assert by["n2"].job_wall_min == pytest.approx(25 + 11 / 60)      # 14:37:08 -> 15:02:19
    assert by["n2"].runner == "GitHub Actions 1000679031"
    assert by["n2"].step_wall_api_min == pytest.approx(24 + 25 / 60)  # 14:37:40 -> 15:02:05
    assert by["n1"].job_wall_min == pytest.approx(73 + 32 / 60)
    assert by["n1"].step_wall_api_min is None                   # no step record: said so


def test_a_run_with_no_jobs_record_keeps_a_blank_that_says_blank(tmp_path):
    t = xt.build_table([write_run(tmp_path, "n8", 3)])
    text = t.render()
    assert "n8" in text and "3" in text and "(no jobs record)" in text


def test_the_rendered_table_has_a_row_per_run_and_a_header(tmp_path):
    dirs = [write_run(tmp_path, "n1", 1), write_run(tmp_path, "n2", 1)]
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps(JOBS))
    text = xt.build_table(dirs, jobs).render()
    lines = text.splitlines()
    assert lines[0].startswith("| candidate | repeat |")
    assert len(lines) == 4                       # header, rule, two rows
    assert "6550" in text and "3091" in text and "4366.77" in text
    assert text.count("6678 ran once; outcomes identical to baseline") == 2
    assert "UNION BROKEN" not in text


def test_the_union_cell_separates_what_ran_from_how_it_came_out(tmp_path):
    """Every hosted run of #1947 passed the union; what differed was outcomes.
    A cell that said only FAIL would hide that, and one that said only PASS
    would hide the rest."""
    d = write_run(tmp_path, "n8", 1, union=dict(UNION, verdict="FAIL", outcome_diffs=[
        {"nodeid": "a::b", "baseline": "PASSED", "candidate": "FAILED"}]))
    text = xt.build_table([d]).render()
    assert "6678 ran once; 1 outcome differences vs baseline" in text
    d = write_run(tmp_path, "n8", 2, union=dict(UNION, verdict="FAIL", missing=["a::b"]))
    text = xt.build_table([d]).render()
    assert "UNION BROKEN: 1 not run exactly once" in text


def test_a_broken_record_refuses_the_whole_table_rather_than_a_row(tmp_path):
    good = write_run(tmp_path, "n1", 1)
    bad = write_run(tmp_path, "n2", 1, log=LOG.replace("[mem] peak tree RSS", "X"))
    with pytest.raises(xt.Refusal) as e:
        xt.build_table([good, bad])
    assert "n2" in str(e.value) and "xdist-n2-r1" in str(e.value)


def test_the_cli_writes_markdown(tmp_path, capsys):
    d = write_run(tmp_path, "n1", 1)
    assert xt.main([str(d)]) == 0
    assert "| candidate |" in capsys.readouterr().out
    assert xt.main([]) == 2
    assert xt.main([str(tmp_path / "nope")]) == 2


# ---- a jobs file that cannot be used is REFUSED (2), never a traceback (#2152).
# read_jobs() ran before main()'s guard, so a missing or malformed jobs.json
# reached a shell as "Traceback ... exit 1" -- the code that means FAIL. The
# last five documents are rule 8's adversary for the issue's own tuple: valid
# JSON of the wrong shape raises TypeError, and bytes that are not text raise
# UnicodeDecodeError, so each still escaped with exit 1 until the guard named
# them. read_jobs() only parses, so neither can be a bug of ours in disguise.
@pytest.mark.parametrize("doc", [
    None,
    b"not json",
    b"{}",
    json.dumps({"jobs": [{"conclusion": "success"}]}).encode(),
    b"\xff\xfe",
    b"[]",
    b"null",
    b'{"jobs": 5}',
    b'{"jobs": [[1]]}',
], ids=["missing", "not-json", "no-jobs-key", "no-name-key", "not-utf8",
        "top-level-list", "top-level-null", "jobs-not-a-list", "job-not-an-object"])
def test_a_jobs_file_that_cannot_be_used_is_refused_not_a_traceback(tmp_path, capsys, doc):
    d = write_run(tmp_path, "n1", 1)
    jobs = tmp_path / "jobs.json"
    if doc is not None:
        jobs.write_bytes(doc)
    assert xt.main([str(jobs), str(d)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("REFUSED:"), err


def test_a_jobs_path_that_is_a_directory_is_refused(tmp_path, capsys):
    """Unreadable: read_text() on a directory. Chosen over chmod because a
    test running as root can read anything."""
    d = write_run(tmp_path, "n1", 1)
    (tmp_path / "dir.json").mkdir()
    assert xt.main([str(tmp_path / "dir.json"), str(d)]) == 2
    assert capsys.readouterr().err.startswith("REFUSED:")


@pytest.mark.parametrize("doc", [None, b"[]"], ids=["missing", "top-level-list"])
def test_the_shipped_cli_exits_2_without_a_traceback(tmp_path, doc):
    """What a shell sees. The tests above drive main(); this drives
    ``python tools/xdist_run_table.py`` and reads the exit status and stderr,
    which is where the traceback was."""
    d = write_run(tmp_path, "n1", 1)
    jobs = tmp_path / "jobs.json"
    if doc is not None:
        jobs.write_bytes(doc)
    r = subprocess.run([sys.executable, str(pathlib.Path(xt.__file__)),
                        str(jobs), str(d)],
                       capture_output=True, text=True)
    assert r.returncode == 2, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    assert "REFUSED:" in r.stderr, r.stderr
