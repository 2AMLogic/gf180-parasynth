"""Injected-kill and truncated-reader controls for the tracked-JSON record
writers migrated to `run_all.write_json_atomic` (#882; class sweep remainder
of #881).

A tracked JSON written truncate-then-write is the #842 shape on an experiment
record: a kill landing inside the write leaves the file short, and a later
reader -- a gate, a scorecard, the tool's own next run -- strands on it until
a human restores the file. Every writer migrated below now replaces through
`run_all.write_json_atomic`; these controls pin both halves:

- the write half: BOTH kill hooks from the #881 ledger control
  (test_manifest.test_a_kill_during_the_ledger_write_leaves_the_previous_ledger_intact)
  -- a truncate-then-half-write-then-die on `Path.write_text` (the old shape:
  pre-migration this hook fired and corrupted the record, so the control is
  red until the migration lands) and a die-after-tmp-before-replace on
  `os.replace` (the new shape's residual risk). Post-migration the previous
  record survives both and no tmp sibling is left behind.
- the read half: a truncated, empty or non-UTF-8 record must REFUSE naming
  its path -- never a bare JSONDecodeError that reads as a bug in the reader.

The routing guard (`test_no_module_writes_a_record_inline_any_more`) reads
each module's source and fails on every inline record-write spelling (#923
widened it after the serialise-then-write spelling `open(..., "w")` +
`fh.write(json.dumps(...))` reported a clean bill on a write exactly as
corruptible as the one it was written to catch): `.write_text(`,
`.write_bytes(`, `json.dump(`, `json.dumps(` outside a per-module allowance
for verified stdout prints, and `open(` / `.open(` in write, append or
exclusive mode. What the guard does NOT cover is stated on
`inline_record_write_findings`, and the defeating spellings ship as
committed probe cases so the next reader can see the boundary without
editing a real module.

The writer controls call each module's `write_json` seam directly: the same
function the tool's main() calls at its (formerly inline) write site, with no
render or reference corpus behind it, so a kill is simulated per writer
without running any of the pipelines the records describe.
"""
from __future__ import annotations

import importlib
import inspect
import json
import os
import pathlib
import re
import sys

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "model"))
sys.path.insert(0, str(HERE))


#: (module, writer attribute) -- one entry per migrated write site; refprofile
#: writes profile and notes through the same `write_json` seam, so one entry
#: covers both calls.
WRITERS = [
    ("condition_boundary", "write_json"),
    ("regen_m1a_units", "write_json"),
    ("refprofile", "write_json"),
    ("qualify_m1a_attack", "write_json"),
    ("measure_m1a_drive", "write_json"),
    ("phase_aware_m1a", "write_json"),
    ("analyse_m1a_phase_cycle", "write_json"),
    ("measure_m5a_attack_bias", "write_json"),
    ("measure_m5a_pulse_duty", "write_json"),
    ("clap_d12a_probe", "write_json"),
    ("clap_burst_timing_qual", "write_json"),
    ("clap_final_strike_experiment", "write_json"),
]


#: `json.dumps(` occurrences that are stdout display, not record writes --
#: verified line-by-line when this table was written (#923): each is the
#: serialiser inside a `print(...)` (measure_m1a_drive:235, phase_aware_m1a:378,
#: analyse_m1a_phase_cycle:402 and :460, measure_m5a_pulse_duty:158) and none
#: hands the serialised text to a file. Checked by exact COUNT per module and
#: by a position-aware print-line test -- a `json.dumps(` counts toward the
#: allowance only if `print(` precedes it on the same line: a new serialise
#: call raises the count and goes red, deleting a verified print lowers it and
#: goes red too, and a record write whose line merely carries a `print(` token
#: after the match goes red (`print-line-camouflage`, #944 review). What the
#: test cannot see is a line crafted to carry a literal `print(` BEFORE the
#: match (`print(); fh.write(json.dumps(x))`) -- a textual guard's ordinary
#: limit, the same class as a mode passed as a variable -- which is why the
#: count check and the committed probes carry the weight. A new module needs
#: no entry (default 0): its serialisation belongs through the seam, and
#: stdout display earns its allowance here, consciously.
STDOUT_DUMPS_ALLOWLIST = {
    "measure_m1a_drive": 1,
    "phase_aware_m1a": 1,
    "analyse_m1a_phase_cycle": 2,
    "measure_m5a_pulse_duty": 1,
}

#: a write/append/exclusive mode literal, either quote style, with optional
#: binary/+ suffixes: "w", "wb", "w+", "a", "xb" ... -- never "r"/"rb"/"r+".
_WRITE_MODE_LIT = r"""["'][wax][b+]*["']"""

#: `open(` -- builtin or `.open(` method -- whose mode argument is a
#: write-mode literal, positional or `mode=`. The builtin takes the mode as
#: its SECOND argument; the method form takes it FIRST (`RECORD.open("w")`
#: -- the pre-widening probe case that caught this as a gap). One level of
#: paren nesting is allowed in the builtin's path argument
#: (`open(str(p), "w")` matches); two or more, or a mode passed as a
#: variable, is a documented miss (see `inline_record_write_findings`).
_WRITE_OPEN_BUILTIN_RE = re.compile(rf"""\bopen\(             # builtin open(
    (?:[^()]|\([^()]*\))*?    # path argument, one nesting level
    ,\s*(?:mode\s*=\s*)?      # mode: second arg, positional or keyword
    {_WRITE_MODE_LIT}         # write / append / exclusive
    """, re.VERBOSE)
_WRITE_OPEN_METHOD_RE = re.compile(rf"""\.open\(              # path.open( / handle.open(
    \s*(?:mode\s*=\s*)?       # mode: FIRST arg for the method form
    {_WRITE_MODE_LIT}         # write / append / exclusive
    """, re.VERBOSE)


def inline_record_write_findings(src: str, *, dumps_allowance: int = 0) -> list[str]:
    """Every inline record-write shape `src` exhibits, as readable findings.

    Covered: `.write_text(`, `.write_bytes(`, `json.dump(` (handle form),
    `json.dumps(` -- allowed only as the serialiser inside a stdout `print(`
    with `print(` preceding the match on the same line, exactly
    `dumps_allowance` of them, both directions of count drift -- and `open(` /
    `.open(` with a write/append/exclusive mode literal (positional or
    `mode=`, either quote style, one nesting level).

    NOT covered, deliberately: bare `.write(` of non-record content
    (`refprofile`'s `wavfile.write(` is a legitimate non-JSON write); a
    read-mode `open(`; a mode passed as a variable rather than a literal
    (`mode = "w"` defeats the literal match -- tracking values needs AST
    analysis, rejected in #923 as more machinery than the gap warrants); a
    serialiser inside a multi-line `print(` (`print(` must precede the match
    on the same line, so one needs its own conscious extension); and
    `open(` arguments nested more than one paren deep.
    """
    findings: list[str] = []
    plain = ((".write_text(", re.compile(r"\.write_text\(")),
             (".write_bytes(", re.compile(r"\.write_bytes\(")),
             ("json.dump(", re.compile(r"json\.dump\(")),
             ("write-mode open(", _WRITE_OPEN_BUILTIN_RE),
             ("write-mode .open(", _WRITE_OPEN_METHOD_RE))
    for name, pattern in plain:
        for m in pattern.finditer(src):
            line_no = src[: m.start()].count("\n") + 1
            findings.append(f"{name} at line {line_no}")
    dumps = list(re.finditer(r"json\.dumps\(", src))
    lines = src.splitlines()
    off_print = []
    for m in dumps:
        line_no = src[: m.start()].count("\n")
        col = m.start() - (src.rfind("\n", 0, m.start()) + 1)
        if "print(" not in lines[line_no][:col]:
            off_print.append(m)
    for m in off_print:
        line_no = src[: m.start()].count("\n") + 1
        findings.append(
            f"json.dumps( at line {line_no} is not a stdout print: "
            f"{lines[src[: m.start()].count('\n')].strip()!r}")
    if len(dumps) > dumps_allowance:
        findings.append(
            f"{len(dumps) - dumps_allowance} json.dumps( call(s) beyond the "
            f"allowance of {dumps_allowance} -- record serialisation belongs "
            f"through run_all.write_json_atomic; stdout display earns its "
            f"allowance in STDOUT_DUMPS_ALLOWLIST consciously")
    elif len(dumps) < dumps_allowance:
        findings.append(
            f"STDOUT_DUMPS_ALLOWLIST entry is stale: allows {dumps_allowance} "
            f"json.dumps( call(s) but the source has {len(dumps)} -- revisit "
            f"the allowance")
    return findings


@pytest.mark.parametrize("modname,fn", WRITERS)
def test_a_kill_during_the_record_write_leaves_the_previous_record_intact(
        tmp_path, monkeypatch, modname, fn):
    """THE CONTROL (start red): failed against the bare `write_text` shape.

    Both hooks simulate the same physical event -- a kill inside the write --
    on whichever writer is live: truncate-then-half-write-then-die for the old
    `write_text(json.dumps(...))` shape, die-after-tmp-before-replace for
    `write_json_atomic`. Pre-migration the first hook fired and the record was
    left truncated; post-migration the previous record survives both."""
    writer = getattr(importlib.import_module(modname), fn)
    target = tmp_path / "record.json"
    previous = {"schema": "previous-record", "value": 3.14}
    target.write_text(json.dumps(previous, indent=2) + "\n")
    original = target.read_bytes()

    def kill_mid_truncate_write(self, data, *a, **k):
        with self.open("w") as fh:
            fh.write(data[: len(data) // 2])
        raise RuntimeError("injected: killed mid truncate-then-write")

    def kill_before_replace(src, dst):
        raise RuntimeError("injected: killed after tmp write, before replace")

    monkeypatch.setattr(pathlib.Path, "write_text", kill_mid_truncate_write)
    monkeypatch.setattr(os, "replace", kill_before_replace)

    with pytest.raises(RuntimeError):
        writer(target, {"schema": "previous-record", "value": 3.15, "moved": True})

    assert target.read_bytes() == original, (
        f"a kill during {modname}.{fn} corrupted the previous complete record")
    assert list(tmp_path.glob("*tmp*")) == [], (
        f"a tmp sibling survived the failed write in {modname}: "
        f"{list(tmp_path.glob('*tmp*'))}")


@pytest.mark.parametrize("modname,fn", WRITERS)
def test_no_module_writes_a_record_inline_any_more(modname, fn):
    """The routing half of the control above. The injected kill drives the
    `write_json` seam directly, so it stays green even if a module's write
    site stops calling the seam and writes inline again -- the exact
    regression this migration repaired (found by reverting one module to
    `RECORD.write_text(json.dumps(...))` and watching the kill control stay
    green). This guard reads each module's source and fails on every inline
    record-write spelling (#923 widened it after the serialise-then-write
    spelling `open(..., "w")` + `fh.write(json.dumps(...))` -- the most
    ordinary alternative way to write the same bug -- reported a clean bill
    on the pre-widening patterns, because `json\\.dump\\(` does not match
    `json.dumps(` and no `.write_text(` is present): `.write_text(`,
    `.write_bytes(`, `json.dump(`, `json.dumps(` outside the module's
    verified stdout allowance, and write-mode `open(` / `.open(`. What stays
    out of scope is on `inline_record_write_findings`; the defeating
    spellings are pinned by the probe cases below. A new tracked-JSON write
    belongs through the shared seam, and a new legitimate shape extends this
    file consciously rather than bypassing it."""
    src = inspect.getsource(importlib.import_module(modname))
    findings = inline_record_write_findings(
        src, dumps_allowance=STDOUT_DUMPS_ALLOWLIST.get(modname, 0))
    assert not findings, (
        f"{modname} writes inline: {findings} -- tracked records must go "
        f"through the {fn} seam (run_all.write_json_atomic)")


#: Probe 2 from #923 and its siblings: inputs the guard must go red on, each
#: a synthetic source snippet fed through the same `inline_record_write_findings`
#: the tree guard runs, NOT an edit to a real module, so the guard's boundary
#: is visible without touching the tree. The entries are not all the same kind
#: of defeat, and the numbers matter:
#: - 12 of 15 defeated the pre-widening guard: under origin/main's two patterns
#:   (`.write_text(`, `json.dump(`) they were green -- a clean bill on a
#:   non-atomic truncate-then-write of serialised JSON.
#: - `write-text` and `json-dump-handle` are the old guard's OWN two patterns:
#:   red then and red now, kept as regression pins, not defeats.
#: - `print-line-camouflage` defeated THIS PR's first allowance check (#944
#:   review, probe B): a record write whose line carries a `print(` token
#:   satisfied the substring print-line test at full allowance. It is red only
#:   under the position-aware test and exists to pin it -- remove the position
#:   logic and this entry goes green.
DEFEATING_SPELLINGS = {
    "open-w-then-write-dumps-probe2": (
        'with open(RECORD, "w") as fh:\n'
        '    fh.write(json.dumps(new, indent=2) + "\\n")\n', {}),
    "open-mode-keyword": ('fh = open(RECORD, mode="w")\n', {}),
    "open-single-quoted-binary": ("fh = open(RECORD, 'wb')\n", {}),
    "open-append": ('fh = open(RECORD, "a")\n', {}),
    "open-exclusive": ("fh = open(RECORD, 'x')\n", {}),
    "path-open-method": ('fh = RECORD.open("w")\n', {}),
    "open-nested-first-arg": ('fh = open(str(tmp / "record.json"), "w")\n', {}),
    "write-bytes": ("RECORD.write_bytes(blob)\n", {}),
    "write-text": ("RECORD.write_text(blob)\n", {}),
    "json-dump-handle": ("json.dump(new, fh)\n", {}),
    "dumps-off-a-print-line": ('blob = json.dumps(new)\nfh.write(blob)\n', {}),
    "dumps-beyond-allowance": (
        'print(json.dumps(a))\nblob = json.dumps(b)\n', {"dumps_allowance": 1}),
    "dumps-count-is-stale-low": (
        'print(json.dumps(summary))\n', {"dumps_allowance": 2}),
    "dumps-count-is-stale-high": (
        'print(json.dumps(summary))\n', {"dumps_allowance": 0}),
    "print-line-camouflage": (
        'fh.write(json.dumps(x))  # print(\n', {"dumps_allowance": 1}),
}

#: spellings that must stay green, with the reason each is legitimate.
GREEN_SPELLINGS = {
    "read-mode-open-rb": (
        'with open(path, "rb") as f:\n    f.read()\n', {}),
    "read-open-explicit-r": ('f = open(path, mode="r")\n', {}),
    "plain-write-of-non-record-content": (
        "wavfile.write(str(part), sr, y)\n", {}),
    "stdout-dumps-within-allowance": (
        'print(json.dumps(summary, indent=1))\n', {"dumps_allowance": 1}),
}


@pytest.mark.parametrize("label", sorted(DEFEATING_SPELLINGS))
def test_the_guard_goes_red_on_the_spellings_that_defeated_it(label):
    """#923's probe 2, committed: the guard must visibly go red on the
    input that used to defeat it, through the same helper the tree guard
    runs. 12 of the 15 entries are shapes the pre-widening guard reported
    a clean bill on; the provenance split (old-guard regression pins,
    print-line camouflage) is on DEFEATING_SPELLINGS itself."""
    src, kwargs = DEFEATING_SPELLINGS[label]
    assert inline_record_write_findings(src, **kwargs), (
        f"the guard is green on {label!r} -- a non-atomic record write it "
        f"claims to cover")


@pytest.mark.parametrize("label", sorted(GREEN_SPELLINGS))
def test_the_guard_stays_green_on_legitimate_shapes(label):
    """The complement of the probe above: read-mode opens, non-record
    writes and within-allowance stdout display are not violations. This is
    what keeps the widened patterns from being red on today's tree."""
    src, kwargs = GREEN_SPELLINGS[label]
    assert not inline_record_write_findings(src, **kwargs), (
        f"the guard is red on {label!r} -- a legitimate shape it must leave "
        f"alone")


# ---------------------------------------------------------------------------
# the read half: a damaged record refuses with its path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["record", "candidate", "baseline"])
@pytest.mark.parametrize("damage", ["truncated", "empty"])
def test_a_damaged_m1a_input_is_refused_naming_that_file(
        tmp_path, monkeypatch, capsys, which, damage):
    """`check_m1a_selection` reads three tracked records (#882): the refusal
    must name the file that is unreadable, not print a bare
    `Expecting value: line 1 column 1` for any of the three."""
    import check_m1a_selection as sel

    paths = {"record": tmp_path / "M1A.json",
             "candidate": tmp_path / "candidate.json",
             "baseline": tmp_path / "baseline.json"}
    valid = json.dumps({"schema": "m1a-test", "value": 1})
    for name, p in paths.items():
        p.write_text(valid[:12] if name == which and damage == "truncated"
                     else ("" if name == which and damage == "empty" else valid))
    monkeypatch.setattr(sel, "CANDIDATE", paths["candidate"])
    monkeypatch.setattr(sel, "BASELINE", paths["baseline"])

    assert sel.main(["--record", str(paths["record"])]) == 2
    assert str(paths[which]) in capsys.readouterr().out


def test_a_truncated_f1_record_is_refused_with_its_path(tmp_path, capsys):
    """Regression pin for `check_f1_rtl_record`, whose refusal already names
    the path (#882 found it so); a discovery change that drops the path from
    the message must turn this red."""
    import check_f1_rtl_record as f1

    p = tmp_path / "f1-record.json"
    p.write_text('{"case_id": "F1"')
    assert f1.main([str(p)]) == 2
    assert str(p) in capsys.readouterr().err


def test_a_truncated_profile_is_refused_with_its_path(tmp_path, monkeypatch):
    """`refprofile.load_profile` on the #842 shape: a profile left short by a
    killed write must refuse with the path and the way to restore it, and the
    refusal must be this module's `Refused` -- the type every caller of
    `load_profile` already catches and prints as REFUSED."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_text('{"schema": "refprofile/1", "clips": [')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(pj) in str(e.value)


def test_a_truncated_notes_sibling_is_refused_with_its_path(tmp_path, monkeypatch):
    """The notes file rides along with the profile (#129); a truncated one is
    refused with its own path, not the profile's."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_text(json.dumps({"schema": "refprofile/1", "clips": []}))
    nj = rp.notes_path_for(pj)
    nj.write_text('{"note":')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(nj) in str(e.value)


def test_a_non_utf8_profile_is_refused_with_its_path(tmp_path, monkeypatch):
    """The #909 shape: a record cut mid-UTF-8-character dies in `read_text()`
    before json.loads ever sees it -- UnicodeDecodeError is not a
    JSONDecodeError, so it needs its own clause (manifest.load_json_refused
    carries one) and must refuse with the path all the same."""
    import refprofile as rp

    pj = tmp_path / "profile.json"
    pj.write_bytes(b'{"schema": "\xff\xfe", ')
    monkeypatch.setattr(rp, "PROFILE_JSON", pj)
    with pytest.raises(rp.Refused) as e:
        rp.load_profile()
    assert str(pj) in str(e.value)
