#!/usr/bin/env python3
"""Check the oscillator-1 Shape/Width table in `model/reference_rigs.py`
against the sweep it claims to transcribe, `docs/surge-waveform-mapping.txt`.

WHY THIS EXISTS. Issue #271. That comment block held a hand-written table whose
cells disagreed with the machine-generated sweep in six places, and cited a file
(`docs/surge-shape-sweep.txt`) that has never existed in this repository. The
worst cell asserted a specific waveform -- "a saw at 2*f0: the fundamental is
cancelled" -- for the one run where the sweep reports NO valid fundamental and
refuses to name a waveform at all.

That is the failure mode of the document the comment points at, one layer up:
`docs/surge-waveform-mapping.txt` exists because "the request and the label
agreed with each other and nothing compared either with the signal". A comment
that states measured values the measurement does not support is the same thing,
and hand-reconciling it would leave the same transcription risk in place. So the
transcription is checked mechanically instead.

WHAT IS CHECKED. The comment's table is parsed back out of the source file and
every cell must be reproducible from the sweep:

  1. one comment row per (Shape, Width) pair the sweep measured, and no rows for
     pairs it did not -- a row count is not enough, the SET must match;
  2. each row's `reads` column is the readback string the sweep recorded,
     character for character;
  3. each row's verdict is either the exact identification the sweep made
     (`saw`, `pulse:50.0%`) or the literal token `UNQUALIFIED` where the sweep
     refused to name a waveform. A comment may not name a waveform the sweep
     declined to name, and may not refuse one the sweep identified;
  4. every double-quoted string in the block's VERBATIM SECTION -- the refusal
     reasons, introduced by the marker line this tool names -- is a substring of
     the sweep file once whitespace is collapsed. That is what lets the comment
     quote a measured string without re-introducing paraphrase drift, and the
     collapsing is what lets it wrap a long reason across comment lines.
     Quotes OUTSIDE that section are not checked: they are labels, a waveform
     word or a parameter name, not measured values. Anything measured belongs
     inside the section, and the comment says so. The scope of this check is the
     FILE, not the row -- see the known limit recorded in
     `tools/test_check_surge_waveform_comment.py`;
  5. the block cites `docs/surge-waveform-mapping.txt`, and the dead
     `surge-shape-sweep` citation appears nowhere in the repository.

THREE OUTCOMES, following this repository's convention (see
`tools/check_doc_claims.py`): the third is the point.

  OK        every cell was derived from the sweep and agrees with it
  STALE     a cell was derived and CONTRADICTS the sweep          -> exit 1
  REFUSED   the comment table or the sweep could not be parsed    -> exit 2

REFUSED is not a pass. If the comment block is renamed, the table format is
changed, or the sweep file is missing, this tool answers REFUSED rather than
silently checking nothing -- a checker that finds zero rows and reports success
is exactly the instrument this repository keeps re-discovering it must not
build.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RIGS = REPO / "model" / "reference_rigs.py"
SWEEP = REPO / "docs" / "surge-waveform-mapping.txt"

#: The comment block runs from this marker to the constants it introduces.
BLOCK_START = "---- oscillator 1, for the waveform study"
BLOCK_END = "V_CLASSIC, V_SINE"

#: The citation the block must carry, and the one it must not.
WANT_CITATION = "docs/surge-waveform-mapping.txt"
DEAD_CITATION = "surge-shape-sweep"

#: The verbatim section opens on the line carrying this marker and closes at the
#: next blank comment line. Only quotes inside it are checked against the sweep.
VERBATIM_MARKER = "quoted from that file:"

#: This tool and its test MUST name the dead citation -- that is what they are
#: about -- so they are exempt from the repository-wide search for it. The
#: exemption is checked both ways: a listed file that has STOPPED citing it is
#: reported too, so the list cannot outlive the reason for it.
#: Its test builds the string from this module at run time instead, so the test
#: is NOT exempt -- if it ever spells the citation out, the search will say so.
DEAD_CITATION_EXEMPT = ("tools/check_surge_waveform_comment.py",)

#: `  shape 0.000 (-100.00 %) width 50.00 %: pulse:50.0%`
SWEEP_ROW = re.compile(
    r"^\s*shape (?P<shape>\d\.\d+) \((?P<reads>-?\d+\.\d\d %)\) "
    r"width (?P<width>\d+\.\d\d %): (?P<verdict>.+?)\s*$")

#: `    #   0.000  -100.00 %   50.00 %   pulse:50.0%   ...`, one line of the
#: comment's table: Shape, its readback, the Width, the verdict.
COMMENT_ROW = re.compile(
    r"^\s*#\s+(?P<shape>\d\.\d{3})\s+(?P<reads>-?\d+\.\d\d %)\s+"
    r"(?P<width>\d+\.\d\d %)\s+(?P<verdict>\S+)\s*$")

UNQUALIFIED = "UNQUALIFIED"


class Refused(Exception):
    """A precondition of the check failed, so no verdict can be given."""


def read_block(path: pathlib.Path = RIGS) -> list[str]:
    """The oscillator-1 comment block, as lines. REFUSES if its delimiters are
    not both present exactly once -- a block this tool cannot locate is not a
    block it may report OK on."""
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        raise Refused(f"cannot read {path}: {exc}") from exc
    return block_of(lines, str(path))


def block_of(lines: list[str], where: str) -> list[str]:
    starts = [i for i, ln in enumerate(lines) if BLOCK_START in ln]
    if len(starts) != 1:
        raise Refused(
            f"{where}: found {len(starts)} lines containing {BLOCK_START!r}, wanted 1")
    ends = [i for i, ln in enumerate(lines) if BLOCK_END in ln and i > starts[0]]
    if not ends:
        raise Refused(f"{where}: no {BLOCK_END!r} after the comment block")
    return lines[starts[0]:ends[0]]


def parse_sweep(text: str) -> dict[tuple[str, str], tuple[str, str, str]]:
    """(shape, width) -> (readback, verdict token, full verdict text) from the
    sweep's per-run lines. The verdict token is `UNQUALIFIED` where the sweep
    refused, and the identification itself where it did not."""
    out: dict[tuple[str, str], tuple[str, str, str]] = {}
    for ln in text.splitlines():
        m = SWEEP_ROW.match(ln)
        if not m:
            continue
        shape = f"{float(m['shape']):.3f}"
        verdict = m["verdict"]
        token = UNQUALIFIED if verdict.startswith(UNQUALIFIED) else verdict
        key = (shape, m["width"])
        row = (m["reads"], token, verdict)
        if key in out and out[key] != row:
            raise Refused(
                f"{SWEEP.name}: shape {shape} width {m['width']} appears twice "
                f"with different results ({out[key]} and {row})")
        out[key] = row
    if not out:
        raise Refused(
            f"{SWEEP.name}: no `shape ... width ...:` lines found -- nothing to check against")
    return out


def parse_comment(lines: list[str]) -> dict[tuple[str, str], tuple[str, str, int]]:
    """(shape, width) -> (readback, verdict, source line offset in the block)."""
    out: dict[tuple[str, str], tuple[str, str, int]] = {}
    for off, ln in enumerate(lines):
        m = COMMENT_ROW.match(ln)
        if not m:
            continue
        key = (m["shape"], m["width"])
        if key in out:
            raise Refused(
                f"the comment lists shape {key[0]} width {key[1]} twice "
                f"(block lines {out[key][2]} and {off})")
        out[key] = (m["reads"], m["verdict"], off)
    if not out:
        raise Refused(
            "the comment block holds no parseable `Shape reads Width verdict` rows -- "
            "either the table was removed or its format changed, and this tool "
            "will not report OK on a table it did not read")
    return out


def flatten(lines: list[str]) -> str:
    """Comment lines as one run of text: the `#` and the indentation that wraps
    a long line are layout, not content. Collapsing them is what lets a quoted
    reason span comment lines and still be compared verbatim."""
    out = []
    for ln in lines:
        s = ln.strip()
        out.append(s[1:].strip() if s.startswith("#") else s)
    return re.sub(r"\s+", " ", " ".join(out)).strip()


def verbatim_section(lines: list[str]) -> list[str]:
    """The refusal-reason lines, from the marker to the next blank comment line.
    REFUSES if the marker is not there exactly once -- a section this tool
    cannot find is a section whose quotes went unchecked, and that must not look
    like a pass."""
    at = [i for i, ln in enumerate(lines) if VERBATIM_MARKER in ln]
    if len(at) != 1:
        raise Refused(
            f"found {len(at)} lines containing {VERBATIM_MARKER!r} in the comment "
            f"block, wanted 1 -- the verbatim section is where every measured "
            f"string must live, so this tool will not check a block without one")
    end = len(lines)
    for i in range(at[0] + 1, len(lines)):
        if lines[i].strip() in ("#", ""):
            end = i
            break
    return lines[at[0]:end]


def quoted_strings(lines: list[str]) -> list[str]:
    """Every double-quoted run in the verbatim section, un-wrapped. REFUSES on
    an odd number of quote characters: an unbalanced quote makes the pairing
    arbitrary, and the wrong pairing would silently check the wrong text."""
    flat = flatten(verbatim_section(lines))
    if flat.count('"') % 2:
        raise Refused(
            f"the verbatim section has {flat.count(chr(34))} quote characters, an "
            f"odd number -- the pairing is ambiguous, so nothing can be checked")
    found = re.findall(r'"([^"]+)"', flat)
    if not found:
        raise Refused(
            "the verbatim section quotes nothing -- the refusal reasons the sweep "
            "reported are missing, and an empty section is not a passing one")
    return found


def dead_citations(repo: pathlib.Path = REPO) -> list[str]:
    """Files still citing the sweep file that never existed. Uses `git grep` so
    build output and untracked scratch cannot make this fail or pass."""
    try:
        r = subprocess.run(["git", "-C", str(repo), "grep", "-lI", DEAD_CITATION],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(f"cannot run git grep: {exc}") from exc
    if r.returncode not in (0, 1):
        raise Refused(f"git grep failed ({r.returncode}): {r.stderr.strip()}")
    return [ln for ln in r.stdout.splitlines() if ln]


def check(block: list[str], sweep_text: str,
          repo: pathlib.Path | None = REPO) -> list[str]:
    """Every disagreement between the comment block and the sweep. Empty means
    OK. Raises `Refused` when a precondition of the comparison fails."""
    sweep = parse_sweep(sweep_text)
    comment = parse_comment(block)
    bad: list[str] = []

    missing = sorted(set(sweep) - set(comment))
    extra = sorted(set(comment) - set(sweep))
    for shape, width in missing:
        bad.append(f"the sweep measured shape {shape} width {width} "
                   f"({sweep[(shape, width)][1]}) and the comment omits it")
    for shape, width in extra:
        bad.append(f"the comment has a row for shape {shape} width {width} "
                   f"({comment[(shape, width)][1]}) that the sweep never measured")

    for key in sorted(set(sweep) & set(comment)):
        want_reads, want_token, want_text = sweep[key]
        got_reads, got, _off = comment[key]
        if got_reads != want_reads:
            bad.append(
                f"shape {key[0]} width {key[1]}: the comment says Shape reads "
                f"{got_reads!r}, the sweep recorded {want_reads!r}")
        if got == want_token:
            continue
        if want_token == UNQUALIFIED:
            bad.append(
                f"shape {key[0]} width {key[1]}: the comment says {got!r} but the "
                f"sweep NAMED NO WAVEFORM -- {want_text}")
        elif got == UNQUALIFIED:
            bad.append(
                f"shape {key[0]} width {key[1]}: the comment refuses but the sweep "
                f"identified {want_token!r}")
        else:
            bad.append(
                f"shape {key[0]} width {key[1]}: the comment says {got!r}, "
                f"the sweep says {want_token!r}")

    haystack = re.sub(r"\s+", " ", sweep_text)
    for q in quoted_strings(block):
        if re.sub(r"\s+", " ", q) not in haystack:
            bad.append(f"the comment quotes {q!r}, which is not in {SWEEP.name}")

    if not any(WANT_CITATION in ln for ln in block):
        bad.append(f"the comment block does not cite {WANT_CITATION}")

    if repo is not None:
        citing = set(dead_citations(repo))
        for path in sorted(citing - set(DEAD_CITATION_EXEMPT)):
            bad.append(f"{path} still cites {DEAD_CITATION!r}, a file that has "
                       f"never existed in this repository (issue #271)")
        # Liveness of the exemption, read from disk rather than from `git grep`:
        # an exempt file must still be a file, and must still cite the dead
        # string. `git grep` sees tracked files only, and an exemption for a
        # brand-new file would otherwise look dead on its first run.
        for path in DEAD_CITATION_EXEMPT:
            try:
                text = (repo / path).read_text()
            except OSError as exc:
                bad.append(f"{path} is exempt from the {DEAD_CITATION!r} search "
                           f"but cannot be read ({exc}) -- drop the exemption "
                           f"rather than leaving a hole in the search")
                continue
            if DEAD_CITATION not in text:
                bad.append(
                    f"{path} is exempt from the {DEAD_CITATION!r} search but no "
                    f"longer cites it -- drop the exemption rather than leaving a "
                    f"hole in the search")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rigs", type=pathlib.Path, default=RIGS)
    ap.add_argument("--sweep", type=pathlib.Path, default=SWEEP)
    a = ap.parse_args(argv)
    try:
        block = read_block(a.rigs)
        bad = check(block, a.sweep.read_text())
    except OSError as exc:
        print(f"REFUSED  {exc}")
        return 2
    except Refused as exc:
        print(f"REFUSED  {exc}")
        return 2
    rows = len(parse_comment(block))
    if bad:
        print(f"STALE    {len(bad)} disagreement(s) between "
              f"{a.rigs.name} and {a.sweep.name}:")
        for b in bad:
            print(f"  - {b}")
        return 1
    print(f"OK       {rows} Shape/Width cells in {a.rigs.name} all reproduce "
          f"{a.sweep.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
