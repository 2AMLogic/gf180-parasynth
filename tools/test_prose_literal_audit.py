"""Controls for `tools/prose_literal_audit.py` (issue #998).

The audit counts prose figures quoted as literals in `tools/probes/*.py`.
Its counts re-derive the numbers #988/#990 quoted from a scanner that sat
in no tree, so the controls here must show, per docs/verification-rules.md:

  * the scanner HAS TEETH -- a literal measured figure is flagged (the
    #988 defect shape);
  * what must NOT be flagged is not -- a figure the run derives through an
    f-string field is the #988/#990 repair class, correctly invisible, as
    is a unit without a number and a print through any other call;
  * the NOT-THE-DEFECT GENUS is flagged, and that is stated as the
    scanner's documented limit rather than hidden: a literal that is a
    closed-form consequence of constants the same function derives
    (tools/probes/balance_line_shape.py's prose block, quoting the range
    of the drop1 column the loop above it prints) matches the regex too.
    The count bounds the review set; it does not classify.  #988 found
    its third sites by reading the feeding code paths, not by the count.
  * the VERBATIM-REGEX CONSEQUENCE is flagged and documented the same
    way: the #998 regex's bare `s` has no right word boundary, so "18
    samples" reads as "18 s" -- 13 of the 200 segments on the
    introduction tree are exactly this false match.  Pinned by fixture
    so the count's known inflation is a tested fact, not a sentence.
  * the refusals fire -- a probes file that does not parse and a
    truncated probe tree must REFUSE (exit 2), never answer from a
    partial scan;
  * the real tree's census is self-consistent and NON-EMPTY -- a scanner
    answering 0 on a tree that history says is full of literals is more
    likely broken than the tree is fully derived, so zero is red here
    (a control on the instrument, not a pin on a total: totals are
    reported, never pinned, per the census precedent);
  * the audit stays UNWIRED -- #990's curator rejected a generic
    prose-lint gate, so nothing in the Makefile may reference it.
"""
from __future__ import annotations

import re
import pathlib
import textwrap

import pytest

import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import prose_literal_audit as audit_mod                     # noqa: E402

# The match regex, re-tested here so a drift in the tool cannot quietly
# change what every count in this file means.
SPEC_RE = re.compile(r"~?\d[\d.]*\s?(ms|dB|Hz|kHz|s|%)")


def _audit(tmp_path, monkeypatch, files: dict[str, str]) -> dict:
    """Write `files` (name -> source) into tmp_path and run the census
    over exactly them, with ROOT pointed at the fixture tree.  probe_files
    (the floor check) is bypassed on purpose: fixtures test the scan, the
    floor has its own controls."""
    for name, src in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src))
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    return audit_mod.audit_files(sorted(files))


def _matches(res, rel: str) -> list[str]:
    """Every matched text in one fixture file, in site order."""
    return [m for _, ms in res["files"][rel]["sites"] for m in ms]


# ------------------------------------------------------------------- teeth

def test_a_literal_quoted_figure_is_flagged(tmp_path, monkeypatch):
    """The defect shape #988 repaired: a print quoting a measured figure as
    a literal beside no derivation.  Two sites, three matches, and the
    tilde is part of the match (the regex says `~?`)."""
    res = _audit(tmp_path, monkeypatch, {"probe_a.py": '''
        def report():
            print("settling time 12.5 ms after the step")
            print("~41 ms to first floor clear, 3.85 dB under the reference")
    '''})
    assert res["total_sites"] == 2
    assert res["total_segments"] == 3
    assert _matches(res, "probe_a.py") == ["12.5 ms", "~41 ms", "3.85 dB"]


def test_the_w_print_alias_is_in_scope(tmp_path, monkeypatch):
    """Probes write report blocks through the local
    `w = lambda s="": print(s, file=out)` alias; a `w(` literal must flag
    exactly like a print."""
    res = _audit(tmp_path, monkeypatch, {"probe_w.py": '''
        def report(out):
            w = lambda s="": print(s, file=out)  # noqa: E731
            w("earliest 11.0 ms, latest 53.9 ms")
    '''})
    assert res["total_sites"] == 1
    assert _matches(res, "probe_w.py") == ["11.0 ms", "53.9 ms"]


def test_every_unit_in_the_spec_matches(tmp_path, monkeypatch):
    """ms / dB / Hz / kHz / s / % -- one literal each, one match each."""
    res = _audit(tmp_path, monkeypatch, {"probe_units.py": '''
        def report():
            print("hold 12 ms")
            print("down 3.85 dB")
            print("pitched 455 Hz")
            print("clocked 452 kHz")
            print("wait 0.5 s")
            print("clipped 50 %")
    '''})
    assert res["total_sites"] == 6
    assert res["total_segments"] == 6


# ------------------------------------------------- the repair class is not flagged

def test_a_derived_figure_is_not_flagged(tmp_path, monkeypatch):
    """The #988/#990 repair class: the figure is a field of the f-string,
    so the literal ` ms` carries no digit -- nothing to flag.  Also: a unit
    in prose without a number ("tau (ms)"), and a print through any other
    call name, are out of the scanner's answer."""
    res = _audit(tmp_path, monkeypatch, {"probe_ok.py": '''
        import log
        def report(t):
            print(f"took {t:.1f} ms")
            print("the tau (ms) column holds across all eight rows")
            log.info("a logger line quoting 12.5 ms is out of scope")
    '''})
    assert res["total_sites"] == 0
    assert res["total_segments"] == 0


def test_a_format_spec_is_not_read_as_prose(tmp_path, monkeypatch):
    """`{'tau':>9s}`: the field AND its format spec are excluded wholesale,
    so the spec's `9s` cannot be misread as `9 s` of prose."""
    res = _audit(tmp_path, monkeypatch, {"probe_spec.py": '''
        def report():
            print(f"    {'tau':>9s} {'drop':>7s}")
    '''})
    assert res["total_sites"] == 0


# ------------------------------------------------ not-the-defect genus

def test_the_not_the_defect_genus_is_flagged_and_documented(tmp_path, monkeypatch):
    """The genus #998 names: literals that are closed-form consequences of
    constants the same function derives -- the shape of
    tools/probes/balance_line_shape.py:276-283, whose prose quotes the
    range of the drop1 column the loop above it prints from T and the tau
    grid.  These ARE flagged: the scanner cannot read provenance, only
    patterns.  This test pins that fact as a documented limit -- the count
    is an upper bound on the defect class, sites to review, NOT defects --
    so no future reader mistakes a flagged genus site for a found defect
    (or an unflagged count for a clean tree)."""
    res = _audit(tmp_path, monkeypatch, {"probe_genus.py": '''
        T = 0.060
        def prose():
            print("    READ THE ratio2 COLUMN AT THE TOP THREE ROWS.")
            print("    because at tau/T <= 0.03 the drop it divides is under 0.2 dB.")
            print("    rimshot's HIGH mode is a ~1.5 ms mode on a 60 ms window.")
            print("    drop1 runs from 0.01 dB to 16.6 dB over these eight modes.")
    '''})
    assert res["total_sites"] == 3
    got = _matches(res, "probe_genus.py")
    for figure in ("0.2 dB", "~1.5 ms", "60 ms", "0.01 dB", "16.6 dB"):
        assert figure in got, f"the genus fixture lost {figure!r}"


def test_the_bare_s_word_match_is_flagged_and_documented(tmp_path, monkeypatch):
    """The verbatim-regex consequence, pinned as a fact the way the genus
    above is: the #998 regex's bare `s` has no right word boundary, so
    "18 samples" matches as "18 s" -- the exact shape
    tools/probes/dc_blocker.py prints ("rails 18 samples against").
    Measured with the committed module, 13 of the 200 segments on the
    introduction tree are exactly this false match (15 of 200 end
    mid-word; 16 of 211, 13 of them bare-`s`, at the #993 base), so the
    headline count is an upper bound inflated by a known, enumerable
    amount.  This IS flagged and must stay flagged: adding a word
    boundary would silently redefine every count the tool produces, and
    the regex is the #998 specification verbatim."""
    res = _audit(tmp_path, monkeypatch, {"probe_bare_s.py": '''
        def report():
            print("rails 18 samples against")
            print("bin 4 sits inside the loop, 6 seeds each")
    '''})
    got = _matches(res, "probe_bare_s.py")
    assert "18 s" in got, "the bare-`s` fixture stopped flagging"
    assert "4 s" in got and "6 s" in got
    assert "18 samples" not in got and "seeds each" not in got


# ------------------------------------------------------------- edge cases

def test_multiline_literal_is_one_site_with_all_its_matches(tmp_path, monkeypatch):
    """A triple-quoted print is one call site; every number-with-unit in it
    counts."""
    res = _audit(tmp_path, monkeypatch, {"probe_multi.py": '''
        def report():
            print("""settling 12.5 ms
            floor clear 16.6 dB
            done""")
    '''})
    assert res["total_sites"] == 1
    assert res["total_segments"] == 2


def test_implicitly_concatenated_prose_is_one_segment_span(tmp_path, monkeypatch):
    """The parser merges adjacent literals, so prose continued across lines
    (how the #993-repaired prose spanned its figures) is scanned as one
    text: `12.5` and ` ms` meet inside it and DO flag."""
    res = _audit(tmp_path, monkeypatch, {"probe_concat.py": '''
        def report():
            print("took 12.5"
                  " ms of the 60 ms window")
    '''})
    assert res["total_sites"] == 1
    assert _matches(res, "probe_concat.py") == ["12.5 ms", "60 ms"]


def test_matched_text_agrees_with_the_spec_regex(tmp_path, monkeypatch):
    """Whatever the tool counts, it counts with the regex #998 specified --
    spot-checked match by match."""
    res = _audit(tmp_path, monkeypatch, {"probe_re.py": '''
        def report():
            print("12.5 ms then ~41 ms then 50 %")
    '''})
    for m in _matches(res, "probe_re.py"):
        assert SPEC_RE.search(m) and SPEC_RE.fullmatch(m)


# ---------------------------------------------------------------- refusals

def test_a_probe_file_that_does_not_parse_is_refused_not_skipped(tmp_path, monkeypatch):
    """A file that does not parse would contribute zero flags while the
    totals read as a full scan -- an unknown rendered in the place where a
    result belongs.  REFUSED, naming the file."""
    with pytest.raises(audit_mod.Refused, match="probe_broken.py"):
        _audit(tmp_path, monkeypatch, {"probe_broken.py": "def broken(:\n"})


def test_a_truncated_probe_tree_is_refused(tmp_path, monkeypatch):
    """The floor guard: two files where the tree carries 86 under-reports
    and reads like a full census."""
    import subprocess
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("pass\n")
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: type("R", (), {"stdout": "a.py b.py"})())
    with pytest.raises(audit_mod.Refused, match="under the"):
        audit_mod.probe_files()


def test_git_failure_is_refused(tmp_path, monkeypatch):
    """Outside a git tree, `git ls-files` fails -- the census cannot answer
    and must say so, not report an empty scan."""
    import subprocess
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)

    def boom(*a, **k):
        raise subprocess.CalledProcessError(129, "git")
    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(audit_mod.Refused, match="git ls-files could not run"):
        audit_mod.probe_files()


# ------------------------------------------------- the real tree, unwired

def test_probe_files_on_this_tree_meets_the_floor():
    """Integration: git answers for the real tree and the floor holds."""
    files = audit_mod.probe_files()
    assert len(files) >= audit_mod.MIN_PROBE_FILES
    assert all(f.startswith("tools/probes/") and f.endswith(".py")
               for f in files)


def test_audit_on_this_tree_is_self_consistent_and_nonempty():
    """The census on the real tree: totals are the sums, every recorded
    match satisfies the spec regex, and the answer is NOT empty.  Zero on
    this tree would mean a broken scanner far more likely than a fully
    derived tree -- #988's history counted 139 sites here.  Totals are
    reported, never pinned (the census precedent), so no number is
    asserted beyond zero."""
    res = audit_mod.audit()
    assert res["n_files"] >= audit_mod.MIN_PROBE_FILES
    assert res["total_segments"] == sum(
        d["segments"] for d in res["files"].values())
    assert res["total_sites"] == sum(
        len(d["sites"]) for d in res["files"].values())
    for d in res["files"].values():
        for _, ms in d["sites"]:
            assert ms, "a site with no matches would be a phantom"
            for m in ms:
                assert SPEC_RE.fullmatch(m)
    assert res["total_sites"] > 0, (
        "the real-tree census answered ZERO flagged sites; on a tree whose "
        "probes quoted 139 such sites as recently as #988 that is far more "
        "likely a broken scanner than a fully derived tree -- check the "
        "glob, the regex and the parse before believing it")


def test_main_default_prints_the_counts_and_exits_zero(capsys):
    """The acceptance criterion: the audit prints the flagged count."""
    assert audit_mod.main([]) == 0
    out = capsys.readouterr().out
    assert f"{audit_mod.audit()['total_segments']} segments" in out
    assert "total" in out


def test_main_list_lists_sites(capsys):
    assert audit_mod.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "tools/probes/" in out
    assert re.search(r"\d[\d.]*\s?(ms|dB|Hz|kHz|s|%)", out)


def test_main_refusal_exits_two(capsys, monkeypatch):
    """The refusal wiring: exit 2, the family run_all.py honours, with the
    reason on stderr -- never a traceback, never a plausible table."""
    def refused():
        raise audit_mod.Refused("stand-in precondition failure")
    monkeypatch.setattr(audit_mod, "audit", refused)
    assert audit_mod.main([]) == 2
    err = capsys.readouterr().err
    assert "REFUSED" in err and "stand-in" in err


def test_not_wired_into_make_verify():
    """#990's curator rejected a generic prose-lint gate; #998 re-states it
    (on-demand audit only).  This pins the absence mechanically: if the
    audit is ever deliberately wired in, this test is the place that must
    be consciously changed, not a gate that appears by accident."""
    makefile = pathlib.Path(__file__).resolve().parents[1] / "Makefile"
    assert "prose_literal_audit" not in makefile.read_text(), (
        "prose_literal_audit is referenced by the Makefile -- the on-demand "
        "constraint (#998, #990 curator) has been violated or deliberately "
        "reversed; update this control and record which")
