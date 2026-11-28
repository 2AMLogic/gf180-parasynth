"""The output-path census against shapes whose answer is known by reading.

A census can be wrong two ways: crying NOT-MADE (or UNKNOWN) at a tool that
does make the directory, or calling MADE a write whose directory nothing
makes.  The first happened five times while this tool was written, each found
by reading its output against the code rather than by inspecting it, and each
is pinned here with the shape that defeated it:

  * `pathlib.Path` compared against the bare name `Path`, so the five-line
    sample itself was missed (caught by the tool's own self-check, which
    refuses to count);
  * `Path(x).resolve()`, which takes no argument, read as an unknown shape
    (`fpga/verify_arty.py`, `fpga/build_arty.py`) -- and, after that was
    mended, `x.resolve()` read like `x.name`, a string read off `x`, so the
    write had no candidate at all;
  * `os.makedirs(os.path.dirname(os.path.abspath(x)))` -- a second idiom for
    "the parent directory" (`rtl-sketch/verify_deadline.py`,
    `verify_synth_top.py`) -- and `os.makedirs` itself not read as a mkdir;
  * a local bound twice in one function (`path = a.resume.parent / ...`, then
    `path = a.out / ...`), whose two candidates collapsed into one
    (`tools/measure_mono_attack_context.py`).

Every case below is a few lines whose answer you can see without the tool.
"""
import textwrap

import pytest

import output_path_census as oc

AP = """\
import argparse, json, pathlib
ap = argparse.ArgumentParser()
ap.add_argument("--json")
a = ap.parse_args()"""


def census(*parts: str) -> list:
    """The candidates in the given blocks, each dedented, joined by newlines,
    so a block's line numbers are the ones you read."""
    source = "\n".join(textwrap.dedent(p).strip("\n") for p in parts)
    return oc.scan(source, "tools/sample.py").candidates


def test_the_2040_shape_is_not_made_and_unguarded():
    (c,) = census(AP, """
        pathlib.Path(a.json).write_text(json.dumps({}))
    """)
    assert (c.verdict, c.guarded, c.root, c.line) == ("NOT-MADE", False, "a.json", 5)
    assert c.need == ["parent"]


def test_a_parent_made_on_the_line_before_counts_and_is_named():
    (c,) = census(AP, """
        out = pathlib.Path(a.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({}))
    """)
    assert c.verdict == "MADE"
    assert [mk.lineno for mk, _, _, _ in c.earlier] == [6]


def test_a_parent_made_by_a_helper_the_write_does_not_call_counts():
    """The shape of the #2040 fix: one function makes the directory, another
    writes the file, and nothing in the second mentions a directory."""
    (c,) = census(AP, """
        def ensure(path):
            path.parent.mkdir(parents=True, exist_ok=True)
        def record(name):
            pathlib.Path(name).write_text(json.dumps({}))
        ensure(pathlib.Path(a.json))
        record(a.json)
    """)
    assert c.verdict == "MADE"
    assert [mk.lineno for mk, _, _, _ in c.earlier] == [6]


def test_a_directory_made_before_a_child_is_written_counts():
    (c,) = census("""
        import argparse, json, pathlib
        ap = argparse.ArgumentParser()
        ap.add_argument("--outdir")
        a = ap.parse_args()
        pathlib.Path(a.outdir).mkdir(parents=True, exist_ok=True)
        (pathlib.Path(a.outdir) / "report.json").write_text(json.dumps({}))
    """)
    assert (c.verdict, c.steps, c.need) == ("MADE", ["child"], [])


def test_a_mkdir_after_the_write_is_not_made():
    """`tools/measure_repeatability.py`: one path writes `--json` bare, a
    later one makes its parent first.  The mkdir exists; it does not run
    before this write, so the verdict is the one that says read it."""
    (c,) = census(AP, """
        out = pathlib.Path(a.json)
        out.write_text(json.dumps({}))
        out.parent.mkdir(parents=True, exist_ok=True)
    """)
    assert c.verdict == "MADE?"


def test_the_handler_is_reported_apart_from_the_verdict():
    (c,) = census(AP, """
        try:
            pathlib.Path(a.json).write_text(json.dumps({}))
        except OSError as exc:
            print(f"REFUSED -- {exc}")
            raise SystemExit(2)
    """)
    assert (c.verdict, c.guarded) == ("NOT-MADE", True)


def test_a_path_the_command_line_did_not_name_is_not_a_candidate():
    assert census("""
        import json, pathlib, tempfile
        out = pathlib.Path(tempfile.mkdtemp()) / "report.json"
        out.write_text(json.dumps({}))
    """) == []


def test_a_filename_read_off_another_path_does_not_make_that_path_a_root():
    """`shutil.copyfile(f, Path(a.dst) / f.name)`: the target is built on
    `a.dst`; where `f` was listed from is not where the file is written."""
    (c,) = census("""
        import argparse, pathlib, shutil
        ap = argparse.ArgumentParser()
        ap.add_argument("--src")
        ap.add_argument("--dst")
        a = ap.parse_args()
        for f in sorted(pathlib.Path(a.src).iterdir()):
            shutil.copyfile(f, pathlib.Path(a.dst) / f.name)
    """)
    assert c.root == "a.dst"


def test_the_dirname_of_abspath_idiom_names_the_parent():
    (c,) = census("""
        import argparse, json, os
        ap = argparse.ArgumentParser()
        ap.add_argument("--json", default=None)
        a = ap.parse_args()
        if a.json:
            os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
            with open(a.json, "w") as fh:
                json.dump({}, fh)
    """)
    assert (c.verdict, c.sink) == ("MADE", "open")


def test_os_makedirs_is_a_mkdir():
    (c,) = census("""
        import argparse, json, os, pathlib
        ap = argparse.ArgumentParser()
        ap.add_argument("--outdir")
        a = ap.parse_args()
        os.makedirs(a.outdir, exist_ok=True)
        (pathlib.Path(a.outdir) / "report.json").write_text(json.dumps({}))
    """)
    assert c.verdict == "MADE"


def test_a_path_method_that_takes_no_argument_is_still_the_same_path():
    """`fpga/verify_arty.py`: `directory = args.outdir.resolve()`."""
    (c,) = census("""
        import argparse, pathlib
        ap = argparse.ArgumentParser()
        ap.add_argument("--outdir")
        a = ap.parse_args()
        directory = a.outdir.resolve()
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "verification.txt").write_text("done")
    """)
    assert (c.verdict, c.root) == ("MADE", "a.outdir")


def test_a_name_bound_twice_keeps_a_candidate_for_each_path():
    """`tools/measure_mono_attack_context.py`: `path` is read from one place
    and written to another.  Both candidates must survive, each on its own
    root: collapsing them kept the NOT-MADE one and lost the MADE one."""
    first, second = census("""
        import argparse, os
        ap = argparse.ArgumentParser()
        ap.add_argument("--out")
        ap.add_argument("--resume")
        a = ap.parse_args()
        for row in rows:
            path = os.path.join(os.path.dirname(a.resume), row["wav"])
        for key in keys:
            path = os.path.join(a.out, key + ".wav")
            open(path, "w").close()
    """)
    assert {first.root, second.root} == {"a.out", "a.resume"}


def test_a_chain_of_calls_is_followed_out_to_the_command_line():
    (c,) = census("""
        import argparse, pathlib
        ap = argparse.ArgumentParser()
        ap.add_argument("--out")
        a = ap.parse_args()
        def publish(dest):
            dest.write_bytes(b"x")
        def build(dest):
            publish(dest)
        def main(a):
            out = pathlib.Path(a.out)
            out.mkdir(parents=True, exist_ok=True)
            build(out / "bitstream.bin")
        main(a)
    """)
    assert c.verdict == "MADE"
    assert c.via == "publish() L6 <- build() L8 <- main() L12"


def test_a_write_through_a_parameter_nothing_supplies_is_counted_not_guessed():
    scanned = oc.scan("def write_config(path, text):\n    path.write_text(text)\n",
                      "tools/sample.py")
    assert scanned.candidates == []
    assert [(fn.name, node.lineno) for fn, node in scanned.unresolved] == [("write_config", 2)]


def test_the_self_check_refuses_when_a_detector_goes_blind(monkeypatch):
    oc.self_check()                                   # the apparatus as it ships
    monkeypatch.setattr(oc, "PATH_CALLS", frozenset())
    with pytest.raises(oc.Refused, match="#2040 shape"):
        oc.self_check()


def test_an_unknown_verdict_column_is_refused_before_anything_is_counted(capsys):
    assert oc.main(["--only", "NOT-MADE,BOGUS"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_only_test_files_are_left_out():
    for name in ("tools/test_x.py", "model/x_test.py", "tools/conftest.py",
                 "tools/probes/test_bench.py"):
        assert oc.TEST_NAME.search(name), name
    for name in ("tools/latest.py", "tools/contest.py", "tools/census.py"):
        assert not oc.TEST_NAME.search(name), name
