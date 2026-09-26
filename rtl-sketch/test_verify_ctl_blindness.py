#!/usr/bin/env python3
"""`verify_ctl.py`'s blindness matrix, without iverilog.

    .venv/bin/python -m pytest rtl-sketch/test_verify_ctl_blindness.py -q

docs/verification-rules.md 4: a suite that measures more than one property
reports, for every injected control, which properties MOVED and which were
BLIND. `verify_ctl` qualifies -- its comparison decomposes each run into six
properties: the four fields of a write, the write count, and the drain window
-- and the matrix is what turns "SPI_ADDR7 was caught" into "SPI_ADDR7 was
caught BY THE ADDRESS FIELD, and the other five could not have seen it."

The simulation itself is covered by `make controls`
(`verify_ctl.py --inject SPI_ADDR7 --expect-fail`, ~1 s under iverilog); what
is covered HERE is the reporting, which is the part that can be wrong while
every simulation still passes. It was: the matrix listed only the four
bit-fields, so `SPI_ANYLEN` (208 writes delivered for 206 sent) and
`SPI_DRAIN_LATE` (206 writes applied at cycle 10, at `go`) each printed four
BLIND rows and "NO FIELD MOVED" -- a coverage-hole verdict on two defects the
same comparison had just CAUGHT, by counters it had already recorded.

**Why the earlier version of this file could not see that.** Every fixture in
it was a hand-written `LAST` dict, and no hand-written dict had ever set
`count_seen != count_sent` or `late > 0` -- the only two shapes in which those
rows matter. So the fixtures below are built by driving the REAL
`compare_writes` over a fabricated register-port log (`_last_from_compare`),
with the populations and numerators the iverilog controls actually produce.
Deriving the fixture from the code under test costs nothing here (no
simulator, 0.2 s) and is the difference between testing the reporting and
testing a transcription of it.

Two refusal cases are covered for the same reason: a run whose link delivered
nothing has no matrix to print, and a property whose population is zero was
never measured. Four (or six) zeros would render as BLIND rows, i.e. as
EVIDENCE that no property sees the defect, when in fact there is no evidence
of anything. That distinction -- no evidence versus evidence of nothing -- is
the one this repository keeps getting wrong.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import verify_ctl                                                   # noqa: E402


def _matrix(last, tag="TAG", capsys=None):
    verify_ctl.LAST.clear()
    verify_ctl.LAST.update(last)
    verify_ctl.print_blindness(tag)
    return capsys.readouterr().out


def _rows(out):
    """{property: verdict} for the matrix rows."""
    got = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("MOVED", "BLIND", "REFUSED"):
            got[parts[1]] = parts[0]
    return got


def _fracs(out):
    """{property: (numerator, population)} as the row text actually prints it.

    The point of reading this back out of the text: a row whose numerator was
    counted over one population and printed against another is measuring two
    different things on the two sides of "of", which is how "0 of 206 writes
    wrong" got printed for a count taken over 155 comparisons."""
    got = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] in ("MOVED", "BLIND") and parts[3] == "of":
            got[parts[1]] = (int(parts[2]), int(parts[4]))
    return got


def _delivered(writes):
    """The register-port rows a perfect link would produce for `writes`."""
    return [(int(f), int(s), int(a) & 0xFF, int(d) & 0xFFFFFFFF) for f, s, a, d in writes]


def _fake_rtl_out(tmp_path, got, cycles):
    """The text `compare_writes` reads: `flag sec addr data [cycle]` per write
    that reached the register port. `cycles=None` omits the column entirely,
    which is a log carrying no drain-window evidence at all."""
    lines = []
    for i, row in enumerate(got):
        cells = ["x" if v is None else str(int(v)) for v in row]
        if cycles is not None:
            cells.append("x" if cycles[i] is None else str(int(cycles[i])))
        lines.append(" ".join(cells))
    p = os.path.join(str(tmp_path), "ctl_rtl_out.txt")
    with open(p, "w") as fh:
        fh.write("".join(line + "\n" for line in lines))
    return p


def _last_from_compare(writes, got, cycles, tmp_path, capsys):
    """Drive the real `compare_writes` and return `(status, LAST)`.

    Every fixture claiming to be a shape that OCCURS goes through this rather
    than through a hand-written dict -- see this module's docstring."""
    verify_ctl.LAST.clear()
    status = verify_ctl.compare_writes(writes, _fake_rtl_out(tmp_path, got, cycles))
    capsys.readouterr()                       # drop compare_writes' own FAIL text
    return status, dict(verify_ctl.LAST)


IN_WINDOW = verify_ctl.GO_CYCLE - 6           # cycle 2: what the rev-2 link measures
AT_GO = verify_ctl.GO_CYCLE + 2               # cycle 10: what SPI_DRAIN_LATE measures


# The counts below are facts of `verify_ctl.stimulus()` and move with it. Contract
# revision 13 (the clap's final strike) added three writes -- the kit's CP FRATE
# and the FRATE corners at 0x43 and 0x87 -- so 206 -> 209 writes, 105 -> 106
# addresses above 0x7F (0x87), and the 24-bit data count is unchanged at 42.
def test_a_defect_confined_to_one_field_leaves_the_other_five_blind(tmp_path, capsys):
    """The SPI_ADDR7 shape, reproduced from the stimulus rather than asserted
    from memory: truncating every address to A[6:0] makes 106 of the 209
    writes wrong and touches nothing else."""
    writes = verify_ctl.stimulus()
    got = [(f, s, a & 0x7F, d) for f, s, a, d in _delivered(writes)]
    status, last = _last_from_compare(writes, got, [IN_WINDOW] * len(got), tmp_path, capsys)
    assert status == 1
    assert last["bad_addr"] == 106 and last["compared"] == 209
    out = _matrix(last, "SPI_ADDR7", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND", "address": "MOVED",
                          "data": "BLIND", "count": "BLIND", "drain": "BLIND"}
    assert "106 of 209" in out
    assert "NO PROPERTY MOVED" not in out


def test_the_data_truncation_moves_the_data_field_and_only_that(tmp_path, capsys):
    """SPI_DATA24: 42 of 209 writes carry a datum wider than 24 bits."""
    writes = verify_ctl.stimulus()
    got = [(f, s, a, d & 0xFFFFFF) for f, s, a, d in _delivered(writes)]
    status, last = _last_from_compare(writes, got, [IN_WINDOW] * len(got), tmp_path, capsys)
    assert status == 1 and last["bad_data"] == 42
    out = _matrix(last, "SPI_DATA24", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND", "address": "BLIND",
                          "data": "MOVED", "count": "BLIND", "drain": "BLIND"}
    assert "42 of 209" in out


def test_a_write_count_mismatch_moves_the_count_property(tmp_path, capsys):
    """The SPI_ANYLEN shape, as measured under iverilog on this tree: accepting
    a mis-sized transaction instead of discarding it puts 211 writes on the
    register port for 209 sent, with all four bit-fields intact and every
    write inside the drain window.

    This is the row whose absence printed "NO FIELD MOVED" for a control that
    `--expect-fail` simultaneously reported as CAUGHT -- the report
    contradicting the verdict on the same run."""
    writes = verify_ctl.stimulus()
    got = _delivered(writes) + _delivered(writes[:2])
    status, last = _last_from_compare(writes, got, [IN_WINDOW] * len(got), tmp_path, capsys)
    assert status == 1
    assert (last["count_seen"], last["count_sent"], last["count_off"]) == (211, 209, 2)
    assert last["bad"] == 0 and last["late"] == 0
    out = _matrix(last, "SPI_ANYLEN", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND", "address": "BLIND",
                          "data": "BLIND", "count": "MOVED", "drain": "BLIND"}
    assert "2 of 209 writes unaccounted for" in out
    assert "NO PROPERTY MOVED" not in out


def test_a_drain_that_runs_at_go_moves_the_drain_property(tmp_path, capsys):
    """The SPI_DRAIN_LATE shape, as measured: all 209 writes arrive bit-exact
    and the count matches -- and all 209 are applied at cycle 10, at or after
    `go`, so the datapath had already read its registers. Bit-exactness at the
    port is not delivery on time, and only the drain row can say so."""
    writes = verify_ctl.stimulus()
    got = _delivered(writes)
    status, last = _last_from_compare(writes, got, [AT_GO] * len(got), tmp_path, capsys)
    assert status == 1
    assert last["late"] == 209 and last["cyc_seen"] == 209
    assert last["bad"] == 0 and last["count_off"] == 0
    out = _matrix(last, "SPI_DRAIN_LATE", capsys)
    assert _rows(out) == {"flag": "BLIND", "section": "BLIND", "address": "BLIND",
                          "data": "BLIND", "count": "BLIND", "drain": "MOVED"}
    assert "209 of 209 writes at or after `go`" in out
    assert "NO PROPERTY MOVED" not in out


def test_each_row_is_printed_against_the_population_it_was_counted_over(tmp_path, capsys):
    """The denominator defect. The per-field numerators accumulate over
    `zip(writes, got)` -- the writes that ARRIVED -- while "writes sent" is the
    host's count. A link that drops writes therefore printed "10 of 209" for a
    figure counted over 155 comparisons: CLAUDE.md's "37 of 155 writes"
    control-path shape with the two populations swapped. Every row must name
    its own population, and `_fracs` reads that back out of the printed text
    so the coupling is checked where a reader sees it."""
    writes = verify_ctl.stimulus()
    got = _delivered(writes)[:155]                        # 54 writes never arrived
    for i in range(10):                                   # 10 of the 155 are corrupt
        f, s, a, d = got[i]
        got[i] = (f, s, a, d ^ 1)
    status, last = _last_from_compare(writes, got, [IN_WINDOW] * len(got), tmp_path, capsys)
    assert status == 1
    assert (last["compared"], last["count_sent"], last["bad_data"]) == (155, 209, 10)
    out = _matrix(last, "SPI_DROPS", capsys)
    assert _fracs(out)["data"] == (10, 155), "the data row must be measured over the 155 compared"
    assert _fracs(out)["count"] == (54, 209), "the count row is over the 209 the host sent"
    assert "10 of 209" not in out
    assert _rows(out)["data"] == "MOVED" and _rows(out)["count"] == "MOVED"


def test_a_defect_no_property_sees_is_reported_as_a_coverage_hole_not_a_pass(tmp_path, capsys):
    """Six BLIND rows is not a clean result -- it means the control cannot turn
    this bench red, and the matrix has to say so in words rather than leaving a
    reader to notice the absence of a MOVED row. This is now the ONLY way that
    sentence can be printed: it requires a run in which every one of the six
    properties was measured and none moved."""
    writes = verify_ctl.stimulus()
    status, last = _last_from_compare(writes, _delivered(writes),
                                      [IN_WINDOW] * len(writes), tmp_path, capsys)
    assert status == 0, "a perfect link is a PASS; the matrix still has to explain itself"
    out = _matrix(last, "SPI_NOTHING", capsys)
    assert set(_rows(out).values()) == {"BLIND"}
    assert len(_rows(out)) == len(verify_ctl.PROPERTIES)
    assert "NO PROPERTY MOVED" in out
    assert "coverage has a hole" in out


def test_a_property_with_no_population_is_refused_not_reported_blind(tmp_path, capsys):
    """A register-port log with no cycle column carries no drain-window
    evidence at all. "0 of 0 writes at or after `go`" would print as BLIND --
    evidence that the property cannot see this defect -- when the truth is
    that it was never measured. REFUSED is a first-class outcome (CLAUDE.md),
    and the summary must say the matrix is incomplete."""
    writes = verify_ctl.stimulus()
    got = _delivered(writes)
    f, s, a, d = got[0]
    got[0] = (f, s, a, d ^ 1)                             # keep it off the PASS path
    status, last = _last_from_compare(writes, got, None, tmp_path, capsys)
    assert status == 1 and last["cyc_seen"] == 0 and last["late"] == 0
    out = _matrix(last, "NO_CYCLE_COLUMN", capsys)
    assert _rows(out)["drain"] == "REFUSED"
    assert "drain" not in _fracs(out), "a refused property must print no fraction at all"
    assert "INCOMPLETE" in out
    assert "NO PROPERTY MOVED" not in out                 # data moved


def test_a_run_that_delivered_nothing_refuses_the_matrix_rather_than_printing_zeros(capsys):
    """`LAST` empty means `compare_writes` never reached the comparison -- the
    link carried no writes at all, or there was no RTL output to read. Zeros
    would render as BLIND rows, i.e. as EVIDENCE that no property sees the
    defect, when in fact there is no evidence of anything."""
    verify_ctl.LAST.clear()
    verify_ctl.print_blindness("SPI_DEAD")
    out = capsys.readouterr().out
    assert "no per-field blindness matrix" in out
    assert "MOVED" not in out and "BLIND" not in out


def test_a_second_dead_run_does_not_print_the_first_runs_matrix(tmp_path, capsys):
    """Regression for issue #254(A): `compare_writes` records via
    `LAST.update(...)`, and both of its `return 2` paths returned BEFORE that
    line -- so a second call in the same process whose link delivered nothing
    (no RTL output this time) used to leave the FIRST call's counters sitting
    in `LAST`, and `print_blindness` printed a full, confident six-row matrix
    for a run that was never measured. Reproduced at 7b203d1 as exactly this
    shape: a real PASS followed by a dead second call still printing "105 of
    206" from the run before it."""
    writes = verify_ctl.stimulus()
    first_status, first_last = _last_from_compare(writes, _delivered(writes),
                                                   [IN_WINDOW] * len(writes), tmp_path, capsys)
    assert first_status == 0 and first_last, "the first run must be a real, populated PASS"
    second_status = verify_ctl.compare_writes(writes, os.path.join(str(tmp_path), "does_not_exist.txt"))
    capsys.readouterr()                                    # drop compare_writes' own text
    assert second_status == 2
    assert verify_ctl.LAST == {}, ("a dead second run must not leave the first run's counters "
                                   "behind for print_blindness to report as this run's own")
    verify_ctl.print_blindness("SECOND_RUN_DELIVERED_NOTHING")
    out = capsys.readouterr().out
    assert "no per-field blindness matrix" in out
    assert "MOVED" not in out and "BLIND" not in out
    assert "106 of 209" not in out, "the stale matrix from the first run must not reappear"


def test_simulator_never_ran_message_differs_from_comparison_recorded_nothing_message(capsys):
    """Regression for issue #254(B). `main()` reaches the empty-`LAST` branch
    of `print_blindness` two ways that are NOT the same claim: `compare_writes`
    ran and the link delivered nothing (a claim about the DUT), or `simulate()`
    returned `None` and `compare_writes` was never even called (a claim about
    the apparatus -- iverilog/vvp missing, a compile failure, a vvp failure, or
    a timeout). Wording both as "the link delivered no writes at all" attributes
    an apparatus failure to the DUT; `simulated=False` must print a distinct
    message that says nothing about the link at all."""
    verify_ctl.LAST.clear()
    verify_ctl.print_blindness("SPI_NO_SIM", simulated=False)
    sim_never_ran = capsys.readouterr().out

    verify_ctl.LAST.clear()
    verify_ctl.print_blindness("SPI_DEAD", simulated=True)
    comparison_recorded_nothing = capsys.readouterr().out

    assert sim_never_ran != comparison_recorded_nothing
    assert "simulator did not run" in sim_never_ran
    assert "simulator did not run" not in comparison_recorded_nothing
    assert "the link delivered no writes at all" in comparison_recorded_nothing
    assert "the link delivered no writes at all" not in sim_never_ran
    for out in (sim_never_ran, comparison_recorded_nothing):
        assert "MOVED" not in out and "BLIND" not in out


def test_simulator_never_ran_wins_over_a_stale_LAST_from_an_earlier_run(tmp_path, capsys):
    """`simulated=False` must be believed even when `LAST` still holds a real,
    fully-populated matrix from an earlier call in the same process: the flag
    describes THIS call, not whether `LAST` happens to look usable."""
    writes = verify_ctl.stimulus()
    status, last = _last_from_compare(writes, _delivered(writes),
                                      [IN_WINDOW] * len(writes), tmp_path, capsys)
    assert status == 0 and last                            # LAST now holds a real matrix
    verify_ctl.print_blindness("SPI_NO_SIM", simulated=False)
    out = capsys.readouterr().out
    assert "REFUSED" in out and "simulator did not run" in out
    assert "MOVED" not in out and "BLIND" not in out
    assert "the link delivered no writes at all" not in out, (
        "a run the simulator never produced must not be reported as a claim about the link")


def test_a_LAST_missing_the_count_and_drain_counters_refuses_the_whole_matrix(capsys):
    """A `LAST` shaped like the pre-fix one -- four field counters and a total,
    no `count_off`, `late`, `compared` or `cyc_seen` -- cannot be decomposed
    into the six properties the matrix reports. Printing the four rows it CAN
    reproduces exactly the defect the six-row set fixes, so the precondition is
    asserted at the point of use and the matrix refuses instead (CLAUDE.md:
    assert your apparatus's preconditions, and REFUSE rather than report)."""
    out = _matrix(dict(total=206, bad_flag=0, bad_sec=0, bad_addr=105, bad_data=0),
                  "STALE_LAST", capsys)
    assert "REFUSED" in out
    assert "MOVED" not in out and "BLIND" not in out
    for key in ("compared", "count_off", "count_sent", "cyc_seen", "late"):
        assert key in out, "the refusal has to name what is missing, not just refuse"


def test_every_counter_compare_writes_records_has_a_row_in_the_matrix(tmp_path, capsys):
    """If `compare_writes` grows a new counter, the matrix must grow a row --
    otherwise the new property is silently outside the blindness report while
    looking covered, which is precisely how the count and drain counters spent
    their existence.

    The previous version of this test asserted a hardcoded four-counter set
    against a second hardcoded four-counter set, so it was structurally
    incapable of catching the growth it was written for -- and indeed did not
    catch the two counters that were already there (the class of defect
    `model/probe_injection_discrimination.py` exists to hunt). So derive the
    set from what `compare_writes` ACTUALLY records: any key it writes into
    `LAST` that is neither a `PROPERTIES` numerator, nor a `PROPERTIES`
    population, nor declared bookkeeping, is an unreported measurement."""
    writes = verify_ctl.stimulus()
    got = [(1 - f, 1 - s, (a ^ 0x80) & 0xFF, d ^ 1)       # move all four fields
           for f, s, a, d in _delivered(writes)][:-1]     # and the count
    status, last = _last_from_compare(writes, got, [AT_GO] * len(got), tmp_path, capsys)
    assert status == 1

    accounted = {k for _, num, pop, _ in verify_ctl.PROPERTIES for k in (num, pop)}
    unaccounted = set(last) - accounted - verify_ctl.NON_PROPERTY_KEYS
    assert not unaccounted, (f"compare_writes records {sorted(unaccounted)}, which no row of the "
                             "blindness matrix reports: add a PROPERTIES entry (or declare it "
                             "bookkeeping in NON_PROPERTY_KEYS)")

    out = _matrix(last, "EVERYTHING", capsys)
    rows, fracs = _rows(out), _fracs(out)
    assert len(rows) == len(verify_ctl.PROPERTIES)
    assert set(rows.values()) == {"MOVED"}, "this run moves every property there is"
    for name, num_key, pop_key, _what in verify_ctl.PROPERTIES:
        assert fracs[name] == (last[num_key], last[pop_key]), (
            f"the {name} row must print the counter and population compare_writes recorded")
