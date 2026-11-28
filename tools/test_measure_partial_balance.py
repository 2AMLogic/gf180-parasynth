"""Controls for `measure_partial_balance`'s preparation (#636).

The tool carried a private copy of `run_case.prepare` with the pre-#132 lead
clamp -- the #101 defect -- long after the original had stopped clamping. These
tests assert the thing the tool ships: that it prepares through
`run_case.prepare` and that a pair prepared in different states is REFUSED
before any metric runs. Synthetic fixtures only; no reference corpus needed.

Run: `python3 -m pytest tools/test_measure_partial_balance.py -q`
"""
import hashlib
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import exit_contract
import measure_partial_balance as tool
import preparation_contract as pc
import run_case as rc
from test_preparation_contract import _ours, _prepare_pre_132, _reference, _fields


def _sides():
    return ((_reference(), 44100, "the reference recording"),
            (_ours(), 48000, "our RS render"))


def test_the_tool_carries_no_private_prepare_or_window():
    """#636's control, kept as the stronger statement. The `window = rc.window`
    alias it used to assert lost its last caller in #772 (both remaining callers
    were fixed-window estimator copies), so the tool must now define neither
    name -- a private copy of either is the #101 drift this file pins."""
    assert not hasattr(tool, "prepare"), "a private prepare() is the #101 copy-drift"
    assert not hasattr(tool, "window"), "a private window() is the same shape"


def test_a_valid_unequal_pair_is_accepted():
    ref, ours = _sides()
    ry, oy = tool.prepare_pair(ref, ours)
    lead = rc.required_lead_samples
    assert tool.origin(ry, 44100).shape[0] == ry.shape[0] - lead(44100)
    assert tool.origin(oy, 48000).shape[0] == oy.shape[0] - lead(48000)


def test_the_pre_132_clamp_is_refused(monkeypatch):
    """Control: the clamp gave the reference 0.16 ms of lead and our render
    1.00 ms (#101). Injected into the tool's own preparation it must REFUSE."""
    monkeypatch.setattr(tool, "prepared",
                        lambda x, sr, side: _prepare_pre_132(x, sr, side=side))
    ref, ours = _sides()
    with pytest.raises(pc.Refused) as why:
        tool.prepare_pair(ref, ours)
    assert "lead" in _fields(why.value)


def test_a_record_cut_into_the_strike_is_refused_not_clamped():
    x = _reference()
    cut = x[rc._onset_index(x):]
    with pytest.raises(pc.Refused):
        tool.prepared(cut, 44100, "the reference recording")


def test_a_short_lead_is_padded_not_clamped():
    x = _reference()                       # 0.16 ms of lead, far under 540 samples
    y = tool.prepared(x, 44100, "the reference recording")
    assert rc._onset_index(y) >= rc.required_lead_samples(44100)


# --- #772: the tool must not carry its own copy of a shipped estimator, nor
# advertise a command it does not dispatch.
#
# The file's own comment at the `window = rc.window` alias records why: "A
# private copy of either is how this file came to carry the #101 clamp after
# run_case had moved on (#636)". #636 replaced the private `prepare`/`window`
# and left private `tone_ratio_db`/`band_pair_db` behind, labelled "the
# scorecard's two estimators, verbatim" -- a label that stopped being true at
# #109, when D10A/D13A's "Partial balance" moved to `balance_trajectory_db`.
#
# WHAT DEFEATS THESE CHECKS (docs/verification-rules.md rule 8):
#   - `test_no_private_copy_of_a_shipped_estimator` keys on the DOMAINS names,
#     so a copy renamed `_pre109_tone_ratio_db` passes it. That is deliberate:
#     #772's option 2 kept exactly that open, and a renamed copy no longer
#     makes the false claim this check is about (that the tool's number is the
#     shipped estimator's).
#   - `test_the_tool_computes_no_ratio_of_its_own` is the leg a rename does not
#     defeat, but it is a source scan over two named primitives. A copy built
#     from `np.fft` directly, from `scipy.signal.sosfiltfilt`, or from a third
#     `audio_measure` primitive would pass it. It catches the two shapes that
#     actually occurred here, not every conceivable one.

def test_no_private_copy_of_a_shipped_estimator():
    """No function defined in this module may share a name with a declared
    estimator. An alias bound to the shipped object (as `window` is) is fine;
    a definition of its own is the copy-drift this file exists to pin."""
    import audio_measure as am
    for name in am.DOMAINS:
        v = getattr(tool, name, None)
        assert v is None or v is getattr(rc, name, object()), (
            f"{name} is defined in measure_partial_balance rather than taken "
            f"from run_case -- the #636 copy-drift shape")
    assert not hasattr(tool, "tone_ratio_db")
    assert not hasattr(tool, "band_pair_db")


def test_the_tool_computes_no_ratio_of_its_own():
    """The two removed copies were built from `am.tone_amplitude` and
    `am.band_energy`. The tool measures through `partial_trajectory`; it has no
    business calling an estimator primitive directly."""
    src = pathlib.Path(tool.__file__).read_text()
    for prim in ("am.tone_amplitude(", "am.band_energy("):
        assert prim not in src, (
            f"{prim} in measure_partial_balance -- an estimator assembled here "
            f"is a copy that can drift from run_case's")


# --- #1568: the input helpers in the tool were still hand-copied from
# run_case, under its own comment that nothing in it is reimplemented, and
# had drifted the same way: `REF_MAIN` held two of its sixteen entries in a
# different shape; the `wavfile.read` body guarded the sample width BEFORE the
# finiteness assert, so a float32 NaN file was refused as a format mismatch
# and the NaN itself (#133, #134) was never named; and `render_ours` hardcoded
# 2.2 s where `render_drum_solo` reads `SOLO_SECONDS` for the cymbal and the
# open hat. All three are now run_case's.
#
# WHAT DEFEATS THESE CHECKS (docs/verification-rules.md rule 8):
#   - the two attribute legs and the source scan are name- and text-bound.
#     `from run_case import REF_MAIN as _REF`, `getattr(scipy.io, "wavfile")
#     .read(...)`, or a copy moved into a helper module passes all three.
#   - so the delegation is also asserted BEHAVIOURALLY, by spies on the
#     run_case objects driven through `cmd_measure` -- the command the operator
#     runs. A copy reached by any route does not call the spy, and the call log
#     is compared exactly: which function, which arguments, in which order.

def test_the_tool_carries_no_private_reference_table_or_renderer():
    """`REF_MAIN` and `render_ours` were the last two things defined again in
    this module rather than taken from run_case (#636's shape, again)."""
    assert not hasattr(tool, "REF_MAIN"), "a private REF_MAIN is a second reference mapping to go stale"
    assert not hasattr(tool, "render_ours"), "a private render_ours hardcodes its own span"


def test_the_tool_reads_no_wav_and_builds_no_drum_of_its_own():
    """The private loader was a `wavfile.read` and the private renderer a
    `DrumsFx()`. Neither primitive has a caller left in this file."""
    src = pathlib.Path(tool.__file__).read_text()
    for frag in ("wavfile.read", "DrumsFx("):
        assert frag not in src, (
            f"{frag} in measure_partial_balance -- the recording or the render "
            f"is produced here rather than by run_case, where the guards live")


def _two_line_strike(voice, sr, high_gain, lead_ms=1.0):
    """The first known case `cmd_validate` proves the estimator on for `voice`,
    after `lead_ms` of digital silence: two decaying lines, the shape
    `rc.prepare` accepts. Synthetic and analytic, not our drum model."""
    f_lo, tau_lo, f_hi, tau_hi = {"RS": (455, 0.0047, 1786, 0.0024),
                                  "CB": (540, 0.060, 800, 0.060)}[voice]
    a = int(round(lead_ms * 1e-3 * sr))
    body = tool.two_partial(f_lo, tau_lo, 1.0, f_hi, tau_hi, high_gain,
                            tool.OP[voice]["t_end"], sr)
    x = np.zeros(a + len(body))
    x[a:] = body
    return x


def test_load_reference_is_run_case_load_reference_plus_the_file_hash(tmp_path, monkeypatch):
    """The delegate keeps the tool's own return tuple -- `(x, sr, rel, sha)`,
    the hash being the provenance `cmd_measure` prints -- and loads from the
    corpus the tool resolved (`REFDIR`, which stays here for that reason)."""
    monkeypatch.setattr(tool, "REFDIR", tmp_path)
    x, rel = _two_line_strike("RS", 44100, 1.0), "rs8/RS.WAV"
    seen = []

    def spy(voice, refdir, inject=""):
        seen.append((voice, refdir, inject))
        return x, 44100, rel, "no knob"               # run_case's tuple: no hash
    monkeypatch.setattr(rc, "load_reference", spy)
    p = tmp_path / rel
    p.parent.mkdir(parents=True)
    p.write_bytes(b"the bytes that were loaded")

    got = tool.load_reference("RS")
    assert seen == [("RS", tmp_path, "")]
    assert got[0] is x
    assert got[1:] == (44100, rel, tool.sha(p))


def test_cmd_measure_loads_and_renders_through_run_case(tmp_path, monkeypatch, capsys):
    """Spies on both run_case helpers, driven through `cmd_measure` itself, so
    a copy reached by any route has to not call them. The render is called
    with the sound and nothing else: the private `render_ours` fixed the span
    at 2.2 s for every sound, which would end a cymbal's or an open hat's ring
    early where `render_drum_solo` reads `SOLO_SECONDS`. The reference is
    asked for under the corpus the tool resolved (`REFDIR`), where the files
    the spy names exist because the delegate hashes them."""
    calls = []
    monkeypatch.setattr(tool, "REFDIR", tmp_path)
    for voice in ("RS", "CB"):
        p = tmp_path / f"{voice.lower()}8" / f"{voice}.WAV"
        p.parent.mkdir(parents=True)
        p.write_bytes(voice.encode())

    def spy_load(voice, refdir, inject=""):
        calls.append(("load", voice, refdir, inject))
        return _two_line_strike(voice, 44100, 1.0), 44100, f"{voice.lower()}8/{voice}.WAV", "no knob"

    def spy_render(sound, *args, **kwargs):
        calls.append(("render", sound, args, kwargs))
        return _two_line_strike(sound, 48000, 0.5), 48000
    monkeypatch.setattr(rc, "load_reference", spy_load)
    monkeypatch.setattr(rc, "render_drum_solo", spy_render)
    # The estimator is not under test here (`cmd_validate` owns it), and its
    # 0.05 Hz line search costs ~30 s on these fixtures, so it is stubbed.
    monkeypatch.setattr(tool.PT, "find_partial",
                        lambda x, sr, f_lo, f_hi, *, seconds=None, df=0.05: 0.5 * (f_lo + f_hi))

    assert tool.cmd_measure() == 0
    out = capsys.readouterr().out
    assert calls == [("load", "RS", tmp_path, ""), ("render", "RS", (), {}),
                     ("load", "CB", tmp_path, ""), ("render", "CB", (), {})]
    # ...and what it printed is what the loader handed back: the hash of the
    # file the spy named, read from under REFDIR.
    assert f"cb8/CB.WAV sha256:{hashlib.sha256(b'CB').hexdigest()[:16]}" in out


def test_the_usage_block_advertises_exactly_the_commands_that_dispatch():
    """`apparatus` was advertised in the module docstring and reachable from
    the CLI long after nothing ran it (#772). The usage block and the dispatch
    table are now one source checked against each other."""
    advertised = {line.split("measure_partial_balance.py")[1].split()[0]
                  for line in (tool.__doc__ or "").splitlines()
                  if "measure_partial_balance.py" in line}
    assert advertised, "the docstring no longer shows how to run the tool"
    assert advertised == set(tool.COMMANDS), (
        f"advertised {sorted(advertised)} but dispatches {sorted(tool.COMMANDS)}")
    assert "apparatus" not in tool.COMMANDS
    assert not hasattr(tool, "cmd_apparatus")


def test_the_operating_point_is_the_one_the_scorecard_scores():
    """Tautological while `OP` is derived from `RS_BALANCE_OP`/`CB_BALANCE_OP`
    (#772, which found `win_ms`, `hop_ms`, `t_end` and both search ranges
    duplicated by value). It is here to fail if they are ever re-hardcoded and
    then drift -- which is how this file came to carry a stale clamp (#636) and
    stale estimators (#772)."""
    for voice, scored in (("RS", rc.RS_BALANCE_OP), ("CB", rc.CB_BALANCE_OP)):
        op = tool.OP[voice]
        assert (op["win_ms"], op["hop_ms"], op["t_end"]) == \
            (scored["win_ms"], scored["hop_ms"], scored["t_end"]), voice
        assert tuple(op["lo"]) == tuple(scored["f_lo_range"]), voice
        assert tuple(op["hi"]) == tuple(scored["f_hi_range"]), voice


# --- #1857: the exit status when no cell clears the floor.
#
# `cmd_measure` prints REFUSED in a cell whose partial stands within
# FLOOR_MARGIN_DB of its record's floor and carries on, and then returned 0
# whatever the table held. Reproduced on origin/main @ 528a228b (2026-11-28):
# with every record floor-bound the table was 12 rows of `REFUSED REFUSED -`
# and the command still exited 0 -- PASS, a RESULT -- having measured nothing.
#
# THE INPUT. `cmd_measure` reads each record's floor from its last 10 %, so a
# record that ends in noise louder than its partials has a floor nothing
# clears. The strike is the estimator's own first known case, unaltered; only
# the tail is new, and it is noise rather than silence because a silent tail is
# a zero floor, which every partial clears.
#
# WHAT DEFEATS THE CHECK (docs/verification-rules.md rule 8): the guard is per
# CELL. One side of a voice can be floor-bound throughout while the other side
# answers, and the run is 0 with no ERROR figure for that voice: its reference
# column, or ours, is all REFUSED and says so in the table.
# `test_one_floor_bound_side_does_not_refuse_...` pins that boundary rather
# than hiding it.
#
# Red-first on that tree, 2026-11-28: the refusal test failed with rc 0 and
# the two controls beside it were green.

FLOOR_BOUND_TAIL_RMS = 3.0        # three times the partials' peak amplitude
ALL_FLOOR_BOUND = (("RS", "reference"), ("RS", "ours"),
                   ("CB", "reference"), ("CB", "ours"))
AT_POINTS = sum(len(tool.OP[v]["at"]) for v in ("RS", "CB"))


def _floor_bound_strike(voice, sr):
    """The first known case for `voice`, then one second of noise: the last
    10 % of the record is noise and nothing in it clears a 6 dB margin."""
    f_lo, tau_lo, f_hi, tau_hi = {"RS": (455, 0.0047, 1786, 0.0024),
                                  "CB": (540, 0.060, 800, 0.060)}[voice]
    body = tool.two_partial(f_lo, tau_lo, 1.0, f_hi, tau_hi, 1.0, tool.OP[voice]["t_end"], sr)
    a = int(round(1e-3 * sr))
    x = np.zeros(a + len(body) + sr)
    x[a:a + len(body)] = body
    x[a + len(body):] = FLOOR_BOUND_TAIL_RMS * np.random.default_rng(0).standard_normal(sr)
    return x


def _measure(tmp_path, monkeypatch, capsys, floor_bound=()):
    """(rc, stdout) of the real `cmd_measure` on two-line strikes; the
    (voice, side) pairs in `floor_bound` get the noise tail instead."""
    def record(voice, sr, side):
        return _floor_bound_strike(voice, sr) if (voice, side) in floor_bound \
            else _two_line_strike(voice, sr, 1.0)

    monkeypatch.setattr(tool, "REFDIR", tmp_path)
    for voice in ("RS", "CB"):
        p = tmp_path / f"{voice.lower()}8" / f"{voice}.WAV"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(voice.encode())
    monkeypatch.setattr(rc, "load_reference",
                        lambda voice, refdir, inject="": (record(voice, 44100, "reference"), 44100,
                                                          f"{voice.lower()}8/{voice}.WAV", "no knob"))
    monkeypatch.setattr(rc, "render_drum_solo",
                        lambda sound, *a, **k: (record(sound, 48000, "ours"), 48000))
    monkeypatch.setattr(tool.PT, "find_partial",
                        lambda x, sr, f_lo, f_hi, *, seconds=None, df=0.05: 0.5 * (f_lo + f_hi))
    rc_ = tool.cmd_measure()
    return rc_, capsys.readouterr().out


def _table(out):
    """The balance rows: the lines whose reference and ours columns are a
    figure or REFUSED, i.e. everything but the verdict line."""
    return [ln.split() for ln in out.splitlines()
            if " ref " in ln and not ln.startswith("REFUSED")]


def test_a_run_where_no_cell_clears_the_floor_is_refused_not_a_pass(
        tmp_path, monkeypatch, capsys):
    rc, out = _measure(tmp_path, monkeypatch, capsys, ALL_FLOOR_BOUND)

    table = _table(out)
    assert len(table) == AT_POINTS, out
    assert all(row[1:4] == ["REFUSED", "REFUSED", "-"] for row in table), table
    assert rc == exit_contract.REFUSED, (rc, out)
    verdicts = [ln for ln in out.splitlines() if ln.startswith("REFUSED")]
    assert len(verdicts) == 1 and out.splitlines()[-1] == verdicts[0], out
    assert f"0 of {2 * AT_POINTS} cell(s)" in verdicts[0], verdicts[0]


def test_the_control_a_run_where_one_voice_answers_still_passes(tmp_path, monkeypatch, capsys):
    """RS floor-bound throughout, CB answering throughout: a result. Widening
    the rule to "any REFUSED cell" turns this red."""
    rc, out = _measure(tmp_path, monkeypatch, capsys, ALL_FLOOR_BOUND[:2])

    table = _table(out)
    rs = [row for row in table if row[1:4] == ["REFUSED", "REFUSED", "-"]]
    cb = [row for row in table if row[1:4] != ["REFUSED", "REFUSED", "-"]]
    assert len(rs) == len(tool.OP["RS"]["at"]) and len(cb) == len(tool.OP["CB"]["at"]), table
    assert all(row[3] != "-" for row in cb), table
    assert rc == exit_contract.PASS, (rc, out)
    assert not any(ln.startswith("REFUSED") for ln in out.splitlines()), out


def test_one_floor_bound_side_does_not_refuse_a_run_whose_other_side_answers(
        tmp_path, monkeypatch, capsys):
    """The stated boundary of the guard, and out of scope on purpose (#1857):
    the RS reference alone is floor-bound, so RS has no ERROR figure at any
    time point, and the run is still 0 because other cells answered."""
    rc, out = _measure(tmp_path, monkeypatch, capsys, (("RS", "reference"),))

    table = _table(out)
    rs = table[:len(tool.OP["RS"]["at"])]
    assert all(row[1] == "REFUSED" and row[2] != "REFUSED" and row[3] == "-" for row in rs), table
    assert rc == exit_contract.PASS, (rc, out)
    assert not any(ln.startswith("REFUSED") for ln in out.splitlines()), out


def test_the_refusal_reason_names_the_cell_not_every_partial(tmp_path, monkeypatch, capsys):
    """A cell needs only ONE weak partial to be refused, so the reason must not
    claim every partial was within the margin (#1934 review)."""
    _, out = _measure(tmp_path, monkeypatch, capsys, ALL_FLOOR_BOUND)
    verdict = [ln for ln in out.splitlines() if ln.startswith("REFUSED")][0]
    assert "every partial" not in verdict, verdict
    assert "both partials" in verdict, verdict
