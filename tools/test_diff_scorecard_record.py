"""Validation cases for tools/diff_scorecard_record.py.

The differ's only failure mode that matters is a **false "no figure moved"** —
that sentence is the entire evidentiary value of regenerating a record, so every
case below that plants a difference is an injected-defect control: the tool must
turn red on it, and must do so naming the leaf.

The second failure mode is answering when it cannot. A record that is not JSON,
a baseline absent from the named revision, and two records of different shape
are all `REFUSED` (exit 2) rather than "they agree over the leaves that
overlap", because that answer looks exactly like data and is not.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "diff_scorecard_record.py"
RECORD = ROOT / "docs" / "scorecard" / "cymbal-369" / "tone-render" / "tone-render.json"

sys.path.insert(0, str(ROOT / "tools"))
import diff_scorecard_record as dsr  # noqa: E402


def _run(*args):
    r = subprocess.run([sys.executable, str(TOOL), *[str(a) for a in args]],
                       capture_output=True, text=True, cwd=ROOT, timeout=120)
    return r.returncode, r.stdout + r.stderr


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj, indent=1) + "\n")
    return p


BASE = {
    "commit": "a" * 40,
    "sources_dirty": False,
    "levels": {"low": 2.9945414217305757, "short": 1.68},
    "verdict": {"properties": {"p": {"ok": True, "value_db": 0.171}}},
    "codes": [0, 25, 50],
    "note": "text",
}


# --- the answer that carries the evidence -----------------------------------

def test_two_identical_records_report_that_no_figure_moved(tmp_path):
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", BASE)
    rc, out = _run(b, "--baseline", a)
    assert rc == 0, out
    assert "no figure moved" in out
    assert "680" not in out          # the count is of THIS record, not a memory
    assert f"{len(dsr._leaves(BASE))} leaves compared" in out


def test_a_provenance_only_change_is_not_a_figure_moving(tmp_path):
    """Regenerating a record is *supposed* to change `commit` and
    `sources_dirty` -- that is the point of #429 -- so those two must not count
    as figures, and must still be shown."""
    new = json.loads(json.dumps(BASE))
    new["commit"] = "b" * 40
    new["sources_dirty"] = True
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    rc, out = _run(b, "--baseline", a)
    assert rc == 0, out
    assert "no figure moved" in out
    assert "provenance  commit" in out and "provenance  sources_dirty" in out
    assert "2 provenance field(s) updated" in out


# --- injected defects: every one of these must turn it red ------------------

@pytest.mark.parametrize("path,value", [
    (("levels", "low"), 2.99),                       # 3rd decimal
    (("verdict", "properties", "p", "value_db"), 0.172),
    (("codes", 2), 51),
])
def test_a_moved_figure_is_reported_and_named(tmp_path, path, value):
    new = json.loads(json.dumps(BASE))
    tgt = new
    for k in path[:-1]:
        tgt = tgt[k]
    tgt[path[-1]] = value
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    rc, out = _run(b, "--baseline", a)
    assert rc == 1, out
    assert "no figure moved" not in out
    assert "MOVED" in out
    leaf = ".".join(str(k) for k in path[:-1])
    assert leaf.split(".")[0] in out


def test_a_bool_that_became_a_float_is_a_type_change_not_a_numeric_move(tmp_path):
    """#429's own defect: `ok` serialised as `1.0` instead of `true`. `1.0 ==
    True` in Python, so a purely numeric comparison would call this agreement.
    It is the reason this case exists."""
    new = json.loads(json.dumps(BASE))
    new["verdict"]["properties"]["p"]["ok"] = 1.0
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    rc, out = _run(b, "--baseline", a)
    assert rc == 1, out
    assert "TYPE/VALUE" in out and "verdict.properties.p.ok" in out
    assert "MOVED" not in out


def test_a_changed_string_is_reported(tmp_path):
    new = json.loads(json.dumps(BASE))
    new["note"] = "other"
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    rc, out = _run(b, "--baseline", a)
    assert rc == 1, out
    assert "TYPE/VALUE" in out and "note" in out


# --- tolerance --------------------------------------------------------------

def test_last_bit_noise_passes_the_default_tolerance_and_fails_at_zero(tmp_path):
    """The real regeneration moved 16 leaves in their last bits (float
    reassociation). At the default tolerance that is agreement; at `--rel-tol 0`
    it is not, and both answers are correct about different questions -- which
    is why the tolerance is printed with every verdict."""
    new = json.loads(json.dumps(BASE))
    new["levels"]["low"] = 2.9945414217305766     # +8.9e-16, the observed move
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    assert _run(b, "--baseline", a)[0] == 0
    rc, out = _run(b, "--baseline", a, "--rel-tol", "0", "--abs-tol", "0")
    assert rc == 1, out
    assert "levels.low" in out


def test_the_tolerance_in_force_is_always_printed(tmp_path):
    a = _write(tmp_path, "a.json", BASE)
    rc, out = _run(a, "--baseline", a, "--rel-tol", "1e-6", "--abs-tol", "1e-9")
    assert rc == 0 and "rel=1e-06" in out and "abs=1e-09" in out


def test_nan_leaves_compare_equal_to_themselves(tmp_path):
    a = _write(tmp_path, "a.json", {"commit": "a" * 40, "sources_dirty": False,
                                    "x": float("nan")})
    rc, out = _run(a, "--baseline", a)
    assert rc == 0, out


# --- REFUSED, which is neither pass nor fail --------------------------------

def test_a_structural_difference_refuses_rather_than_comparing_the_overlap(tmp_path):
    new = json.loads(json.dumps(BASE))
    del new["levels"]["short"]
    new["levels"]["mid"] = 1.68
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    rc, out = _run(b, "--baseline", a)
    assert rc == 2, out
    assert "REFUSED" in out
    assert "levels.short" in out and "levels.mid" in out


def test_a_shorter_list_refuses_rather_than_comparing_the_common_prefix(tmp_path):
    new = json.loads(json.dumps(BASE))
    new["codes"] = [0, 25]
    a = _write(tmp_path, "a.json", BASE)
    b = _write(tmp_path, "b.json", new)
    assert _run(b, "--baseline", a)[0] == 2


def test_a_file_that_is_not_json_refuses(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all\n")
    a = _write(tmp_path, "a.json", BASE)
    rc, out = _run(bad, "--baseline", a)
    assert rc == 2 and "REFUSED" in out and "not JSON" in out


def test_a_record_that_is_not_an_object_refuses(tmp_path):
    lst = tmp_path / "list.json"
    lst.write_text("[1, 2, 3]\n")
    a = _write(tmp_path, "a.json", BASE)
    rc, out = _run(lst, "--baseline", a)
    assert rc == 2 and "not a record" in out


def test_a_missing_file_refuses(tmp_path):
    a = _write(tmp_path, "a.json", BASE)
    rc, out = _run(tmp_path / "nope.json", "--baseline", a)
    assert rc == 2 and "REFUSED" in out


def test_a_path_outside_the_repo_refuses_instead_of_guessing_a_revision(tmp_path):
    """Without `--baseline` the tool reads the *same path* from a revision. A
    path that is not in the repository has no such path, and inventing one
    would compare the wrong file."""
    a = _write(tmp_path, "a.json", BASE)
    rc, out = _run(a)
    assert rc == 2 and "outside" in out


def test_a_baseline_revision_that_lacks_the_file_refuses():
    """An empty-tree revision cannot contain the record. `4b825dc` is git's
    canonical empty tree, present in every repository."""
    rc, out = _run(RECORD, "--baseline-rev", "4b825dc642cb6eb9a060e54bf8d69288fbee4904")
    assert rc == 2, out
    assert "REFUSED" in out and "does not exist at" in out


# --- the committed record itself --------------------------------------------

@pytest.mark.skipif(not RECORD.exists(), reason="the render record is not committed")
def test_the_committed_record_agrees_with_its_own_committed_self():
    """Exercises the git-revision path against the real record. Once the record
    is committed this is the identity case; if it ever fails, the working tree
    holds an uncommitted regeneration that nobody has diffed."""
    rc, out = _run(RECORD)
    assert rc == 0, out
    assert "no figure moved" in out
