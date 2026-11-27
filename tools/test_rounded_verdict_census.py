"""tools/rounded_verdict_census.py -- the #1334 search as a committed control.

PR #1421 repaired #1334: `measure_harness.descent_test` decides "re-pressing
or independent source" at FULL precision and rounds the correlation only to
display it, and `tools/measure_conga_body_spread.py` decided again from that
rounded field, so 0.94997528 displayed as 0.9500 and read as a match. The
cross-reference search that showed the repair removed the pair lived in a
dispatch worker's /tmp (issue #1434). These tests pin what that search must
see, in three layers, because a fixture is our own model of the defect:

  1. the fixture is EXECUTED, and reproduces the verdict flip -- so it is
     grounded in the defect, not in the detector that is meant to find it;
  2. the detector is run over the fixture, and over every precondition it
     refuses on -- offline, so a shallow checkout runs all of it;
  3. the detector is run over `git archive` exports of 65d5c023 (defect
     present) and 00409889 (repaired) -- skipped, and saying so, where those
     revisions are not in the clone, as in a shallow CI checkout.

Every negative test carries a positive half: the detector must show it SAW the
comparison and found no rounded store behind it. A detector that reports
nothing at all would otherwise pass every "not reported" assertion.
"""
from __future__ import annotations

import errno
import io
import os
import pathlib
import subprocess
import sys
import tarfile
import types

import pytest

TOOLS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import exit_contract                                          # noqa: E402
import refusal                                                # noqa: E402
import rounded_verdict_census as census_mod                   # noqa: E402

PRODUCER = "model/measure_harness.py"
CONSUMER = "tools/measure_conga_body_spread.py"

HISTORICAL_PRODUCER = '''\
MATCH_THRESHOLD = 0.95


def descent_test(paths, match_threshold=MATCH_THRESHOLD):
    out = []
    for path in paths:
        best = (0.94997528, 1.0, path)
        out.append(dict(
            file=path, best_match=best[2], best_correlation=round(best[0], 4),
            at_resample_ratio=round(best[1], 4),
            verdict=("a re-pressing of the reference set -- not an independent source"
                     if best[0] >= match_threshold else
                     "no reference file matches it")))
    return out
'''
"""The historical producer, verbatim where it matters (model/measure_harness.py
361-367 at 65d5c023): the verdict is decided from `best[0]` at full precision,
and `best_correlation` is stored through `round(..., 4)` to be displayed.
`at_resample_ratio` is the sibling store that no consumer reads."""

DEFECTIVE_CONSUMER = '''\
def descent_test(rows, mh):
    for r in rows:
        if "best_match" in r:
            r["verdict"] = ("a re-pressing of the Fischer set -- not a second machine"
                            if r["best_correlation"] >= mh.MATCH_THRESHOLD else
                            "no Fischer file matches it")
    return rows
'''
"""tools/measure_conga_body_spread.py 500-504 at 65d5c023: the verdict is
recomputed from the rounded display value."""

REPAIRED_CONSUMER = '''\
_HARNESS_MATCH = "a re-pressing of the reference set -- not an independent source"
_HARNESS_NO_MATCH = "no reference file matches it"


def fischer_verdict(harness_verdict):
    if harness_verdict == _HARNESS_MATCH:
        return "a re-pressing of the Fischer set -- not a second machine"
    if harness_verdict == _HARNESS_NO_MATCH:
        return "no Fischer file matches it"
    raise ValueError(f"unrecognised verdict {harness_verdict!r}")


def descent_test(rows):
    for r in rows:
        if "best_match" in r:
            r["verdict"] = fischer_verdict(r["verdict"])
    return rows


def report(rows):
    return "".join(f"{r['best_match']} r={r['best_correlation']:.3f} "
                   for r in rows)
'''
"""The same module after PR #1421: the harness verdict is forwarded, and the
rounded field is still displayed and never compared."""

STORE = "best_correlation=round(best[0], 4)"
COMPARE = 'if r["best_correlation"] >= mh.MATCH_THRESHOLD else'


def write_tree(root: pathlib.Path, producer: str | None = HISTORICAL_PRODUCER,
               consumer: str | None = DEFECTIVE_CONSUMER) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "model").mkdir(exist_ok=True)
    (root / "tools").mkdir(exist_ok=True)
    if producer is not None:
        (root / PRODUCER).write_text(producer)
    if consumer is not None:
        (root / CONSUMER).write_text(consumer)
    return root


def producer_with(store: str) -> str:
    return f"def store(row, best):\n    {store}\n    return row\n"


def consumer_with(compare: str) -> str:
    return f"def decide(r, threshold):\n    return {compare}\n"


def line_of(root: pathlib.Path, rel: str, needle: str) -> int:
    """1-based line of `needle` in a fixture file. Read, never hardcoded: the
    assertions name the SITE and survive a re-indented fixture."""
    for n, line in enumerate((root / rel).read_text().splitlines(), 1):
        if needle in line:
            return n
    raise AssertionError(f"{needle!r} is not in {rel}")


def pairs(result: dict) -> set[tuple[str, str, int, str, int]]:
    """(field, store file, store line, compare file, compare line)."""
    return {(c.stored.field, c.stored.file, c.stored.line,
             c.compared.file, c.compared.line) for c in result["candidates"]}


def seen(result: dict, kind: str, field: str = "best_correlation") -> set[tuple[str, int, str]]:
    """(file, line, form) of every store or compare the detector saw."""
    return {(s.file, s.line, s.form) for s in result[kind] if s.field == field}


def _load(path: pathlib.Path) -> types.ModuleType:
    """Execute a fixture as a module, because the consumer reads the harness
    the way the real one does: `mh.MATCH_THRESHOLD`, attribute access."""
    module = types.ModuleType(path.stem)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def test_the_fixture_executed_reproduces_the_1334_verdict_flip(tmp_path):
    """Layer 1. The fixture is the defect, not just the syntax of it: the
    harness says "no match" at full precision, and the consumer overwrites
    that with a match read off the rounded display value."""
    root = write_tree(tmp_path)
    harness = _load(root / PRODUCER)
    rows = harness.descent_test(["cand.WAV"])
    assert rows[0]["best_correlation"] == 0.95
    assert rows[0]["verdict"] == "no reference file matches it"

    defective = _load(root / CONSUMER)
    flipped = defective.descent_test(rows, harness)
    assert flipped[0]["verdict"] == "a re-pressing of the Fischer set -- not a second machine"

    (root / CONSUMER).write_text(REPAIRED_CONSUMER)
    repaired = _load(root / CONSUMER)
    forwarded = repaired.descent_test(harness.descent_test(["cand.WAV"]))
    assert forwarded[0]["verdict"] == "no Fischer file matches it"
    assert "cand.WAV r=0.950 " == repaired.report(forwarded)


def test_the_historical_shape_is_reported_with_both_sites(tmp_path, capsys):
    """Layer 2, and the validation case the issue names: the keyword store in
    one module, the subscript comparison in another, the file:line of each."""
    root = write_tree(tmp_path)
    store = (PRODUCER, line_of(root, PRODUCER, STORE))
    compared = (CONSUMER, line_of(root, CONSUMER, COMPARE))

    assert census_mod.main([str(root)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert pairs(census_mod.census(root)) == {("best_correlation", *store, *compared)}
    for needle in ("best_correlation", f"{store[0]}:{store[1]}",
                   f"{compared[0]}:{compared[1]}", "r['best_correlation'] >= mh.MATCH_THRESHOLD"):
        assert needle in out


def test_a_block_prints_each_site_once_however_many_pairs_it_holds(tmp_path, capsys):
    """Control on the report's shape. Two stores and two comparisons in four
    modules are four pairs; the block still lists four sites, each once. The
    first report printed the cross product and repeated every site per
    partner -- for the field `value` on 65d5c023, 322 pairs and 966 lines."""
    root = write_tree(tmp_path, producer=producer_with("dict(best_correlation=round(best[0], 4))"),
                      consumer=consumer_with('r["best_correlation"] >= threshold'))
    (root / "model" / "other_producer.py").write_text(
        producer_with("dict(best_correlation=np.round(best[0], 4))"))
    (root / "tools" / "other_consumer.py").write_text(
        consumer_with('r["best_correlation"] < threshold'))

    assert len(census_mod.census(root)["candidates"]) == 4
    assert census_mod.main([str(root)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "cross-module-pairs=4" in out
    assert out.count("model/measure_harness.py:2:") == 1
    assert out.count("tools/measure_conga_body_spread.py:2:") == 1
    assert len([line for line in out.splitlines() if ":2:" in line]) == 4


def test_forwarding_the_harness_verdict_removes_the_pair_and_keeps_the_rest(tmp_path):
    """The assertion the issue names: the PAIR disappears, and that alone.
    The rounded producer and the display read both survive the repair."""
    root = write_tree(tmp_path, consumer=REPAIRED_CONSUMER)
    assert "r={r['best_correlation']:.3f}" in (root / CONSUMER).read_text()

    result = census_mod.census(root)
    assert not [p for p in pairs(result) if p[0] == "best_correlation"]
    assert seen(result, "stores") == {(PRODUCER, line_of(root, PRODUCER, STORE), "keyword")}


@pytest.mark.parametrize("comparison, locations", [
    ('(previous["score"]\n     < current["score"])', [(2, 13), (3, 8)]),
    ('previous["score"] < previous["score"]', [(2, 12), (2, 32)]),
])
def test_each_operand_has_its_own_reported_site(tmp_path, capsys, comparison, locations):
    root = write_tree(tmp_path, producer='dict(score=round(x, 4))\n',
                      consumer=consumer_with(comparison))
    assert census_mod.main([str(root)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "score  cross-module-pairs=2" in out
    for line, column in locations:
        assert f"{CONSUMER}:{line}:{column} " in out


def test_identical_stores_on_one_line_are_distinct_sites(tmp_path, capsys):
    root = write_tree(tmp_path, producer='a(score=round(x)); b(score=round(x))\n',
                      consumer=consumer_with('r["score"] > 0'))
    assert census_mod.main([str(root)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "score  cross-module-pairs=2" in out
    for column in (3, 22):
        assert f"{PRODUCER}:1:{column} " in out


@pytest.mark.parametrize("label, store", [
    ("keyword round", "dict(best_correlation=round(best[0], 4))"),
    ("keyword np.round", "dict(best_correlation=np.round(best[0], 4))"),
    ("dict key", '{"best_correlation": round(best[0], 4)}'),
    ("subscript", 'row["best_correlation"] = round(best[0], 4)'),
    ("attribute", "row.best_correlation = round(best[0], 4)"),
])
def test_every_store_form_of_a_rounded_field_is_seen(tmp_path, label, store):
    root = write_tree(tmp_path, producer=producer_with(store),
                      consumer=consumer_with('r["best_correlation"] >= threshold'))
    expected = ("best_correlation", PRODUCER, 2, CONSUMER, 2)
    assert pairs(census_mod.census(root)) == {expected}, label


@pytest.mark.parametrize("op", [">=", "<=", ">", "<"])
def test_every_ordered_operator_is_a_threshold_comparison(tmp_path, op):
    root = write_tree(tmp_path, consumer=consumer_with(f'r["best_correlation"] {op} threshold'))
    expected = ("best_correlation", PRODUCER, line_of(root, PRODUCER, STORE), CONSUMER, 2)
    assert pairs(census_mod.census(root)) == {expected}, op


@pytest.mark.parametrize("op", ["==", "!=", "is", "in", "not in"])
def test_equality_and_membership_are_not_a_threshold(tmp_path, op):
    root = write_tree(tmp_path, consumer=consumer_with(f'r["best_correlation"] {op} threshold'))
    result = census_mod.census(root)
    assert len(seen(result, "compares")) == 0, op
    assert len(seen(result, "stores")) == 1
    assert pairs(result) == set()


@pytest.mark.parametrize("label, compare, form", [
    ("chained", '0 <= r["best_correlation"] < 1', "subscript"),
    ("field on the right", 'threshold <= r["best_correlation"]', "subscript"),
    (".get()", 'r.get("best_correlation", 0.0) >= threshold', "get"),
    ("attribute", "r.best_correlation >= threshold", "attribute"),
])
def test_every_read_form_of_an_ordered_comparison_is_seen(tmp_path, label, compare, form):
    root = write_tree(tmp_path, consumer=consumer_with(compare))
    result = census_mod.census(root)
    assert seen(result, "compares") == {(CONSUMER, 2, form)}, label
    assert [p[0] for p in pairs(result)] == ["best_correlation"]


def test_cli_skips_test_basenames_and_symlinked_subdirectories(tmp_path):
    root = write_tree(tmp_path / "root", producer="dict(score=round(x, 4))\n", consumer=None)
    nested = root / "tools" / "probes"
    nested.mkdir()
    consumer = nested / "excitation_energy.py"  # pytest also collects this probe
    source = consumer_with('r["score"] >= threshold')
    consumer.write_text(source)
    (nested / "notes.txt").touch()
    for name in ("test_probe.py", "probe_test.py"):
        (root / "tools" / name).write_text(source)

    cmd = [sys.executable, "-I", str(TOOLS / "rounded_verdict_census.py"), str(root)]
    ordinary = subprocess.run(cmd, capture_output=True, text=True)
    assert (ordinary.returncode, ordinary.stderr) == (exit_contract.PASS, "")
    header = ordinary.stdout.splitlines()[0].split()
    assert {"files=2", "test-files-skipped=2", "dirs-skipped=0", "cross-module-pairs=1"} <= set(header)
    assert "score  cross-module-pairs=1" in ordinary.stdout.splitlines()
    assert f"{PRODUCER}:" in ordinary.stdout
    assert "tools/probes/excitation_energy.py:" in ordinary.stdout

    moved = nested.rename(tmp_path / "outside")
    for count, link in enumerate((nested, root / "model" / "probes"), start=1):
        link.symlink_to(moved, target_is_directory=True)
        assert (link / consumer.name).samefile(moved / consumer.name)
        linked = subprocess.run(cmd, capture_output=True, text=True)
        assert (linked.returncode, linked.stderr) == (exit_contract.PASS, "")
        header = linked.stdout.splitlines()[0].split()
        assert {"files=1", "test-files-skipped=2", f"dirs-skipped={count}", "rounded-stores=1",
                "ordered-field-compares=0", "cross-module-pairs=0"} <= set(header)


def test_a_directory_outside_the_scan_is_not_read(tmp_path):
    """The census covers `tools/` and `model/`. The same comparison anywhere
    else is outside its boundary, and the header says what was scanned."""
    root = write_tree(tmp_path, consumer=None)
    (root / "fpga").mkdir()
    (root / "fpga" / "consumer.py").write_text(DEFECTIVE_CONSUMER)
    result = census_mod.census(root)
    assert result["files"] == 1
    assert len(seen(result, "stores")) == 1
    assert seen(result, "compares") == set()
    assert pairs(result) == set()


def test_the_storing_module_comparing_its_own_field_is_not_a_pair(tmp_path):
    """The census is the CROSS-MODULE reference the #1334 defect had. The same
    comparison in the storing module is seen and left out of the pairs."""
    root = write_tree(tmp_path)
    (root / PRODUCER).write_text(
        HISTORICAL_PRODUCER + "\n\ndef again(rows, threshold):\n"
        "    return [r[\"best_correlation\"] >= threshold for r in rows]\n")
    result = census_mod.census(root)
    assert len(seen(result, "compares")) == 2
    assert pairs(result) == {
        ("best_correlation", PRODUCER, line_of(root, PRODUCER, STORE), CONSUMER,
         line_of(root, CONSUMER, COMPARE))}


def test_a_full_precision_store_is_not_a_rounded_store(tmp_path):
    root = write_tree(tmp_path, producer=HISTORICAL_PRODUCER.replace(
        "best_correlation=round(best[0], 4)", "best_correlation=best[0]"))
    result = census_mod.census(root)
    assert len(seen(result, "compares")) == 1
    assert seen(result, "stores") == set()
    assert pairs(result) == set()


def test_equal_names_pair_and_unequal_names_do_not(tmp_path):
    root = write_tree(tmp_path, consumer=DEFECTIVE_CONSUMER.replace(
        'r["best_correlation"]', 'r["at_resample_ratio"]'))
    assert [p[0] for p in pairs(census_mod.census(root))] == ["at_resample_ratio"]


@pytest.mark.parametrize("label, store", [
    ("f-string re-parse", 'best_correlation=float(f"{best[0]:.3f}")'),
    ("truncation by int", "best_correlation=int(best[0] * 1000) / 1000"),
    ("truncation by floor", "best_correlation=math.floor(best[0] * 1000) / 1000"),
    ("np.clip", "best_correlation=np.clip(best[0], 0.0, 1.0)"),
    ("round nested in the stored expression", "best_correlation=abs(round(best[0], 4))"),
    ("a local named like the field", "best_correlation = round(best[0], 4)"),
    ("an augmented assignment", 'row["best_correlation"] += round(best[0], 4)'),
    ("round under another name", "best_correlation=rnd(best[0], 4)"),
    ("a subscript with a non-literal key", "row[key] = round(best[0], 4)"),
])
def test_the_documented_blind_spots_are_really_blind(tmp_path, label, store):
    """The docstring's SEARCH BOUNDARY is tested, not only stated. Widen the
    detector and this goes red until the boundary is rewritten to match."""
    root = write_tree(tmp_path, producer=producer_with(store),
                      consumer=consumer_with('r["best_correlation"] >= threshold'))
    result = census_mod.census(root)
    assert len(seen(result, "compares")) == 1, label
    assert seen(result, "stores") == set(), label
    assert pairs(result) == set(), label


@pytest.mark.parametrize("label, consumer", [
    ("through a local", ('def decide(r, threshold):\n'
                         '    score = r["best_correlation"]\n'
                         '    return score >= threshold\n')),
    ("through a non-literal key", ('def decide(r, key, threshold):\n'
                                   '    return r[key] >= threshold\n')),
])
def test_reads_the_census_cannot_see(tmp_path, label, consumer):
    """The read-side twins of the local blind spot: the field is pulled into a
    local, or its key is computed, and what gets compared is no longer a
    named slot."""
    root = write_tree(tmp_path, consumer=consumer)
    result = census_mod.census(root)
    assert len(seen(result, "stores")) == 1, label
    assert seen(result, "compares") == set(), label
    assert pairs(result) == set(), label


@pytest.mark.parametrize("label, entries, named", [
    ("an empty directory", [], "tools/ and no model/"),
    ("tools only", ["tools/x.py"], "model/"),
    ("model only", ["model/x.py"], "tools/"),
    ("no python at all", ["tools/notes.txt", "model/README.md"], "0 non-test"),
    ("test files only", ["tools/test_only.py", "model/test_only.py"], "0 non-test"),
])
def test_a_root_with_nothing_to_scan_refuses(tmp_path, capsys, label, entries, named):
    """Rule 8's adversary for the missing-root guard: a root that EXISTS and
    holds nothing to scan. An empty report would read as a clean census."""
    root = tmp_path / "root"
    root.mkdir()
    for entry in entries:
        (root / entry).parent.mkdir(parents=True, exist_ok=True)
        (root / entry).write_text("x = 1\n")
    assert census_mod.main([str(root)]) == exit_contract.REFUSED, label
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and str(root) in captured.err, label
    assert named in captured.err, label
    assert captured.out == "", label


def test_an_absent_root_refuses_with_a_diagnostic(tmp_path, capsys):
    missing = tmp_path / "no-such-tree"
    assert census_mod.main([str(missing)]) == exit_contract.REFUSED
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and str(missing) in captured.err
    assert captured.out == ""
    with pytest.raises(census_mod.Refused):
        census_mod.census(missing)
    assert issubclass(census_mod.Refused, refusal.Refused)


def test_a_file_for_a_root_refuses(tmp_path, capsys):
    not_a_dir = tmp_path / "tree.py"
    not_a_dir.write_text("x = 1\n")
    assert census_mod.main([str(not_a_dir)]) == exit_contract.REFUSED
    assert "REFUSED" in capsys.readouterr().err


def test_an_unparsable_file_refuses_and_names_it(tmp_path, capsys):
    """A file the census could not read is a part of the tree it did not
    scan. Naming it and refusing is the only way that is not silent."""
    root = write_tree(tmp_path)
    (root / "tools" / "broken.py").write_text("def broken(:\n")
    assert census_mod.main([str(root)]) == exit_contract.REFUSED
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and "tools/broken.py" in captured.err
    assert captured.out == ""


def test_a_scanned_tree_with_no_pairs_is_a_clean_census_not_a_refusal(tmp_path, capsys):
    """Positive control for every refusal: `cross-module-pairs=0` from a tree
    that WAS scanned must stay distinguishable from a tree that was not."""
    root = write_tree(tmp_path, consumer=None)
    assert census_mod.main([str(root)]) == exit_contract.PASS
    out = capsys.readouterr().out
    assert "files=1" in out and "cross-module-pairs=0" in out
    assert "REFUSED" not in out


def test_unreadable_subtree_refuses_the_entire_census(tmp_path, capsys, monkeypatch):
    root = write_tree(tmp_path)
    nested = root / "tools" / "nested"
    nested.mkdir()
    (root / CONSUMER).rename(nested / "consumer.py")
    assert census_mod.main([str(root)]) == exit_contract.PASS
    assert "best_correlation  cross-module-pairs=1" in capsys.readouterr().out

    # Inject the same scandir error as chmod 000, even on privileged workers.
    scandir = os.scandir

    def deny_nested(path):
        if pathlib.Path(path) == nested:
            raise PermissionError(errno.EACCES, "Permission denied", str(nested))
        return scandir(path)

    monkeypatch.setattr(os, "scandir", deny_nested)
    assert census_mod.main([str(root)]) == exit_contract.REFUSED
    captured = capsys.readouterr()
    assert "REFUSED" in captured.err and str(nested) in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("blocked", [".", "tools/nested"])
def test_cli_refuses_a_permission_denied_source_directory(tmp_path, blocked):
    root = write_tree(tmp_path)
    nested = root / "tools" / "nested"
    nested.mkdir()
    (root / CONSUMER).rename(nested / "consumer.py")
    directory = root / blocked
    directory.chmod(0)
    try:
        if os.access(directory, os.X_OK):
            pytest.skip("the test account can traverse a mode-000 directory")
        run = subprocess.run([sys.executable, "-I", str(TOOLS / "rounded_verdict_census.py"),
                              str(root)], capture_output=True, text=True)
    finally:
        directory.chmod(0o700)
    assert run.returncode == exit_contract.REFUSED
    assert "REFUSED" in run.stderr and str(directory) in run.stderr
    assert run.stdout == ""


@pytest.mark.parametrize("encoding, header", [
    ("latin-1", "# coding: latin-1\n"),
    ("utf-8-sig", ""),
])
def test_cli_pairs_fields_using_python_source_encoding(tmp_path, encoding, header):
    # In Latin-1 the bytes C2 B5 spell two characters. Forced UTF-8 reads one.
    field = "\u00c2\u00b5"
    consumer = r'def decide(row): return row["\u00c2\u00b5"] > 0'
    root = write_tree(tmp_path, producer=None, consumer=consumer)
    producer = header + f'row = {{"{field}": round(1.2)}}\n'
    (root / PRODUCER).write_bytes(producer.encode(encoding))
    assert _load(root / CONSUMER).decide(_load(root / PRODUCER).row)

    run = subprocess.run([sys.executable, "-I", str(TOOLS / "rounded_verdict_census.py"),
                          str(root)], capture_output=True, text=True)
    assert (run.returncode, run.stderr) == (exit_contract.PASS, "")
    assert f"{field}  cross-module-pairs=1" in run.stdout.splitlines()


def test_cli_refuses_unknown_python_source_encoding(tmp_path):
    root = write_tree(tmp_path)
    source = root / PRODUCER
    source.write_bytes(b"# coding: no-such-codec\n" + source.read_bytes())
    with pytest.raises(SyntaxError, match="no-such-codec"):
        _load(source)

    run = subprocess.run([sys.executable, "-I", str(TOOLS / "rounded_verdict_census.py"),
                          str(root)], capture_output=True, text=True)
    assert run.returncode == exit_contract.REFUSED
    assert "REFUSED" in run.stderr and PRODUCER in run.stderr and "no-such-codec" in run.stderr
    assert run.stdout == ""


def test_the_process_exits_the_declared_status_in_isolation(tmp_path):
    """Through the shipped CLI under `python -I`: the census READS a tree and
    imports nothing from it, so an exported revision needs no environment."""
    root = write_tree(tmp_path)
    tool = TOOLS / "rounded_verdict_census.py"
    clean = subprocess.run([sys.executable, "-I", str(tool), str(root)],
                           capture_output=True, text=True, check=False)
    assert (clean.returncode, clean.stderr) == (exit_contract.PASS, "")
    assert "best_correlation" in clean.stdout
    absent = subprocess.run([sys.executable, "-I", str(tool), str(tmp_path / "nope")],
                            capture_output=True, text=True, check=False)
    assert absent.returncode == exit_contract.REFUSED
    assert "REFUSED" in absent.stderr and absent.stdout == ""


REPO = TOOLS.parent
DEFECT, REPAIRED = "65d5c023", "00409889"
STORE_SITE = ("model/measure_harness.py", 363)
COMPARE_SITE = ("tools/measure_conga_body_spread.py", 503)


def _export(revision: str, dest: pathlib.Path) -> bool:
    """`git archive --format=tar <revision> tools model` into `dest`. False
    when the revision is not in this clone."""
    probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}"],
                           cwd=REPO, stdout=subprocess.DEVNULL)
    if probe.returncode == 1:
        return False
    probe.check_returncode()
    archive = subprocess.run(["git", "archive", "--format=tar", revision, "tools", "model"],
                             cwd=REPO, stdout=subprocess.PIPE, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(dest, filter="data")
    return True


def test_archive_failure_for_available_commit_keeps_gits_error(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "tar.umask")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "invalid")
    with pytest.raises(subprocess.CalledProcessError) as error:
        _export("HEAD", tmp_path)
    assert error.value.cmd[1] == "archive"
    assert "tar.umask" in capfd.readouterr().err


def test_unavailable_historical_commit_can_still_be_skipped(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--bare", "--quiet", str(tmp_path)], check=True)
    monkeypatch.setenv("GIT_DIR", str(tmp_path))
    assert _export(DEFECT, tmp_path / "export") is False


@pytest.mark.parametrize("revision, pair_present", [
    pytest.param(DEFECT, True, id="65d5c023-defect-present"),
    pytest.param(REPAIRED, False, id="00409889-repaired"),
])
def test_the_historical_pair_on_the_real_trees(tmp_path, revision, pair_present):
    """Layer 3, the external grounding of every fixture above: the trees the
    defect actually lived on. The line numbers are pinned because a revision
    cannot drift. The assertion is that ONE pair appearing, then disappearing
    while the rounded store and the display read both remain."""
    dest = tmp_path / revision
    if not _export(revision, dest):
        pytest.skip(f"{revision} is not in this clone (a shallow checkout): the "
                    "historical grounding did NOT run here. The recorded run is in "
                    "the PR that closes #1434.")
    result = census_mod.census(dest)
    assert result["files"] > 300
    assert STORE_SITE in {(s.file, s.line) for s in result["stores"]
                          if s.field == "best_correlation"}
    historical = ("best_correlation", *STORE_SITE, *COMPARE_SITE)
    assert (historical in pairs(result)) is pair_present
    source = (dest / COMPARE_SITE[0]).read_text()
    assert "r={r['best_correlation']:.3f}" in source
    assert ('fischer_verdict(r["verdict"])' in source) is (not pair_present)
