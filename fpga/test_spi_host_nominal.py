#!/usr/bin/env python3
"""Issue #598: a completed tom bend must not become the next hit's tuning.

The host keeps a register IMAGE (what the device holds, transient writes
included) and, separately, the musician's NOMINAL tuning. A bend is built from
the nominal pair; an explicit retune changes the nominal pair; the bend's own
timed writes and the BD attack window's hot/restore writes never do.

What this proves: coefficient-write trajectories of repeated hits. It does NOT
prove anything acoustic, nor decoded I2S / deadline coverage (see the PR).

Frozen phrases (chosen before the fix, not tuned to it):
  DEVELOPMENT  accents (0.3, 1.0), spacings (4000, 9000)
  CONFIRMATION accents (0.6, 1.4, 1.9), spacings (3500, 6000, 15000) -- untouched
Spacing exceeds the bend span (2880 frames), so there is no traffic overlap.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest                                      # noqa: E402
import spi_host as sh                              # noqa: E402
import drums_fx as dx                              # noqa: E402

DEV = dict(accents=(0.3, 1.0), spacings=(4000, 9000))
CONFIRM = dict(accents=(0.6, 1.4, 1.9), spacings=(3500, 6000, 15000))
TOMS = [(dx.LT, dx.M_LT), (dx.MT, dx.M_MT), (dx.HT, dx.M_HT)]
CONGA_RETUNES = {dx.M_LT: (185.0, 44.7, 0.18), dx.M_MT: (310.0, 40.0, 0.15),
                 dx.M_HT: (420.0, 36.0, 0.12)}
T0 = 3000


class ContaminatedHost(sh.MusicHost):
    """Injected control: the pre-#598 behaviour, where every drum write
    (the bend's own timed writes included) rewrites the tuning a later bend
    reads. A checker that does not turn red on this is blind."""
    NOMINAL_EXEMPT_TAGS = frozenset()


class LiveContaminatedHost(ContaminatedHost, sh.LiveMusicHost):
    """Also reinstates the live rebuild leak: the register state is not
    replayed from the boot image, so a second schedule() starts from the
    first one's end state."""
    RESET_STATE_ON_REMATERIALIZE = False


def _pair_amp(host_kit_or_regs: dict, mode: int):
    base = dx.A_MODE + mode * dx.MODE_STRIDE
    return host_kit_or_regs[base], host_kit_or_regs[base + 1], host_kit_or_regs[base + 2]


def expected_bend(frame, mode, accent, a1, a2, amp_reg):
    """Independent of the host: the model's own sequence from a given pair."""
    f0, q = dx.poles_from_regs(a1, a2)
    return sorted((f, a, v) for f, a, v in
                  dx.tom_pitch_drop_writes(frame, mode, f0, q, amp_reg / float(1 << 15), accent))


def bends_by_hit(host, mode):
    """The host's tom-bend writes for one mode, split per hit (14 writes each)."""
    base = dx.A_MODE + mode * dx.MODE_STRIDE
    w = sorted((x.frame, x.addr, x.data) for x in host.w
               if x.tag == "tom-bend" and base <= x.addr < base + 2)
    n = 2 * (dx.TOM_DROP_STEPS + 1)
    assert len(w) % n == 0
    return [sorted(w[i:i + n]) for i in range(0, len(w), n)]


def phrase(spacings, accents):
    """(offset, accent) for every hit: each accent at each spacing, in order."""
    out, t = [], 0
    for sp in spacings:
        for ac in accents:
            out.append((t, ac))
            t += sp
    return out


def build(cls, stop, mode, ph, *, retune=None, live=False):
    h = cls().load(0)
    if retune:
        for a, v in dx.mode_writes(mode, *retune):
            h.drum(1000, a, v, tag="retune")
        if live:       # the live adapter has no tuning event: retune = boot image
            h._live_base_w = list(h.w)
    hits = [(T0 + off, stop, ac) for off, ac in ph]
    if live:
        for f, s, a in hits:
            h.submit(f, "hit", (s, a))
    else:
        h.hits(hits)
    return h


def nominal_regs(cls, mode, retune):
    h = cls().load(0)
    if retune:
        for a, v in dx.mode_writes(mode, *retune):
            h.drum(1000, a, v, tag="retune")
    return h.image


def mismatches(cls, ph, *, live=False, retune_for=None):
    """Count bend writes that differ from the independently calculated bend
    from the nominal pair. Also returns the number of writes compared, so a
    pass with zero coverage cannot read as clean."""
    bad = total = 0
    for stop, mode in TOMS:
        rt = (retune_for or {}).get(mode)
        regs = nominal_regs(sh.MusicHost, mode, rt)
        a1, a2, amp = _pair_amp(regs, mode)
        h = build(cls, stop, mode, ph, retune=rt, live=live)
        if live:
            h._materialize()
        got = bends_by_hit(h, mode)
        assert len(got) == len(ph)
        for (off, ac), g in zip(ph, got):
            want = expected_bend(T0 + off, mode, ac, a1, a2, amp)
            total += len(want)
            bad += sum(1 for x, y in zip(g, want) if x != y)
    return bad, total


def _cases():
    for name, p in (("dev", DEV), ("confirm", CONFIRM)):
        yield name, phrase(p["spacings"], p["accents"])


def test_phrases_are_frozen():
    assert phrase(**{"spacings": DEV["spacings"], "accents": DEV["accents"]})[-1][0] == 4000 * 2 + 9000
    assert min(DEV["spacings"] + CONFIRM["spacings"]) > int(round(dx.TOM_DROP_MS * 1e-3 * sh.SR))


@pytest.mark.parametrize("name,ph", list(_cases()))
@pytest.mark.parametrize("retune_for", [None, CONGA_RETUNES], ids=["toms", "congas"])
@pytest.mark.parametrize("live", [False, True], ids=["music", "live"])
def test_repeated_hits_do_not_walk_nominal_tuning(name, ph, retune_for, live):
    cls = sh.LiveMusicHost if live else sh.MusicHost
    bad, total = mismatches(cls, ph, live=live, retune_for=retune_for)
    assert total >= 3 * len(ph) * 14           # coverage: every hit of every tom compared
    assert bad == 0, f"{bad}/{total} bend writes walked away from nominal"


@pytest.mark.parametrize("name,ph", list(_cases()))
@pytest.mark.parametrize("live", [False, True], ids=["music", "live"])
def test_control_contamination_turns_it_red(name, ph, live):
    """Injected bug: reinstating image contamination must be CAUGHT."""
    cls = LiveContaminatedHost if live else ContaminatedHost
    bad, total = mismatches(cls, ph, live=live)
    assert total and bad > 0, "blind: the checker did not see image contamination"


def test_translated_hits_have_identical_trajectories():
    """Same accent, well separated: hit k equals hit 0 translated in frames."""
    ph = [(i * 7000, 1.0) for i in range(5)]
    for stop, mode in TOMS:
        for live in (False, True):
            h = build(sh.LiveMusicHost if live else sh.MusicHost, stop, mode, ph, live=live)
            if live:
                h._materialize()
            got = bends_by_hit(h, mode)
            base = [(f - ph[0][0], a, v) for f, a, v in got[0]]
            for (off, _), g in zip(ph, got):
                assert [(f - off, a, v) for f, a, v in g] == base


def test_a_deliberate_retune_changes_later_bends_but_not_earlier_ones():
    """Adversarial: a nominal image that ignores coefficient writes would pass
    the repeated-hit test and fail this one."""
    stop, mode = dx.LT, dx.M_LT
    base = dx.A_MODE + mode * dx.MODE_STRIDE
    h = sh.MusicHost().load(0)
    pre = dict(h.image)
    new = dict(dx.mode_writes(mode, 140.0, 25.0, 0.25))
    h.hits([(5000, stop, 1.0)])
    for a, v in new.items():
        h.drum(20000, a, v, tag="retune")
    h.hits([(30000, stop, 1.0), (45000, stop, 1.0)])
    got = bends_by_hit(h, mode)
    assert got[0] == expected_bend(5000, mode, 1.0, pre[base], pre[base + 1], pre[base + 2])
    for g, f in zip(got[1:], (30000, 45000)):
        assert g == expected_bend(f, mode, 1.0, new[base], new[base + 1], pre[base + 2])
        assert g != expected_bend(f, mode, 1.0, pre[base], pre[base + 1], pre[base + 2])


def test_a_deliberate_retune_changes_later_bends_on_the_live_host():
    """The live adapter has no tuning event: a retune is a boot-image change.
    It must still reach every bend, across a rebuild."""
    stop, mode = dx.LT, dx.M_LT
    base = dx.A_MODE + mode * dx.MODE_STRIDE
    new = dict(dx.mode_writes(mode, 140.0, 25.0, 0.25))
    h = sh.LiveMusicHost().load(0)
    amp = h.image[base + 2]
    for a, v in new.items():
        h.drum(100, a, v, tag="retune")
    h._live_base_w = list(h.w)
    h.submit(5000, "hit", (stop, 1.0)); h.schedule()
    h.submit(20000, "hit", (stop, 1.0)); h.schedule()
    got = bends_by_hit(h, mode)
    assert len(got) == 2
    for g, f in zip(got, (5000, 20000)):
        assert g == expected_bend(f, mode, 1.0, new[base], new[base + 1], amp)


def test_timed_bend_writes_stay_in_the_device_image_but_not_the_nominal():
    h = sh.MusicHost().load(0)
    base = dx.A_MODE + dx.M_LT * dx.MODE_STRIDE
    kit = dict(dx.kit_808())
    h.hits([(5000, dx.LT, 1.9)])
    assert h.image[base] != kit[base]                  # device state: bend end
    assert (h.nominal[base], h.nominal[base + 1]) == (kit[base], kit[base + 1])


def test_bd_attack_restore_returns_nominal_not_a_prior_transient():
    """Same class, BD: after repeated hits the restore pair is the kit's, and a
    DECAY knob between hits is honoured."""
    h = sh.MusicHost().load(0)
    kit = dict(dx.kit_808())
    base = dx.A_MODE + dx.M_BD * dx.MODE_STRIDE
    h.hits([(5000, dx.BD, 1.0), (9000, dx.BD, 1.0)])
    rest = [(w.addr, w.data) for w in h.w if w.tag == "bd-attack-restore"]
    assert rest == [(base, kit[base]), (base + 1, kit[base + 1]),
                    (base, kit[base]), (base + 1, kit[base + 1])]
    h.knob(12000, "decay", 9.0)
    h.hits([(15000, dx.BD, 1.0)])
    last = [(w.addr, w.data) for w in h.w if w.tag == "bd-attack-restore"][-2:]
    assert last == [(w.addr, w.data) for w in h.w if w.tag == "knob-decay"]


def test_schedule_rebuild_is_stable_for_live_host():
    h = sh.LiveMusicHost().load(0)
    for f in (4000, 12000, 20000):
        h.submit(f, "hit", (dx.LT, 1.0))
    a = [(p.land, p.w.addr, p.w.data) for p in h.schedule()]
    h.submit(30000, "hit", (dx.LT, 1.0))               # forces a rebuild
    b = [(p.land, p.w.addr, p.w.data) for p in h.schedule()]
    assert b[:len(a)] == a or set(a) <= set(b)
    got = bends_by_hit(h, dx.M_LT)
    assert len(got) == 4
    kit = dict(dx.kit_808())
    base = dx.A_MODE + dx.M_LT * dx.MODE_STRIDE
    for g in got:
        assert g == expected_bend(g[0][0], dx.M_LT, 1.0, kit[base], kit[base + 1], kit[base + 2])
    f0 = got[0][0][0]
    base = [(f - f0, a_, v) for f, a_, v in got[0]]
    for g in got:
        assert [(f - g[0][0], a_, v) for f, a_, v in g] == base


def test_control_rebuild_leak_turns_the_rebuild_test_red():
    h = LiveContaminatedHost().load(0)
    for f in (4000, 12000):
        h.submit(f, "hit", (dx.LT, 1.0))
    h.schedule()
    h.submit(30000, "hit", (dx.LT, 1.0))
    h.schedule()
    kit = dict(dx.kit_808())
    base = dx.A_MODE + dx.M_LT * dx.MODE_STRIDE
    got = bends_by_hit(h, dx.M_LT)
    bad = sum(g != expected_bend(g[0][0], dx.M_LT, 1.0, kit[base], kit[base + 1], kit[base + 2])
              for g in got)
    assert bad > 0


# ---- the tag allowlist fails safe (#598, judge's non-blocking note) ------------
def test_drum_tag_sets_are_disjoint():
    assert not (sh.MusicHost.TRANSIENT_DRUM_TAGS & sh.MusicHost.NOMINAL_DRUM_TAGS)


def test_an_unclassified_drum_tag_is_refused_not_counted_as_nominal():
    # the input that defeated a bare exempt-list: a new generated transient
    h = sh.MusicHost().load(0)
    a = dx.A_MODE + dx.M_LT * dx.MODE_STRIDE
    before = dict(h.nominal)
    image, writes = dict(h.image), list(h.w)
    with pytest.raises(ValueError, match="neither transient nor nominal"):
        h.drum(100, a, 12345, tag="tom-bend-v2")
    assert h.nominal == before
    # a refused write must not survive in what schedule() lays out, either
    assert h.image == image
    assert h.w == writes


def _literal_drum_tags():
    """Every tag passed to a drum write in the shipped fpga/*.py sources,
    read statically so paths no test exercises are covered too. A tag that is
    not a string literal is reported as None (it cannot be classified here)."""
    import ast
    import glob
    here = os.path.dirname(os.path.abspath(__file__))
    found = {}
    for path in sorted(glob.glob(os.path.join(here, "*.py"))):
        if os.path.basename(path).startswith("test_"):
            continue
        tree = ast.parse(open(path).read(), path)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "drum"):
                continue
            kw = [k.value for k in node.keywords if k.arg == "tag"]
            where = f"{os.path.basename(path)}:{node.lineno}"
            if not kw:
                found.setdefault("", []).append(where)
                continue
            lits = [n.value for n in ast.walk(kw[0])
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            if isinstance(kw[0], ast.Constant) or isinstance(kw[0], ast.IfExp):
                for v in lits:
                    found.setdefault(v, []).append(where)
            else:
                found.setdefault(None, []).append(where)
    return found


def test_every_shipped_drum_tag_is_classified():
    found = _literal_drum_tags()
    assert "tom-bend" in found and "select" in found        # the scan sees both kinds
    known = sh.MusicHost.TRANSIENT_DRUM_TAGS | sh.MusicHost.NOMINAL_DRUM_TAGS
    unclassified = {t: w for t, w in found.items() if t not in known}
    assert not unclassified, unclassified
