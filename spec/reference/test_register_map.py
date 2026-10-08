"""Sections 5.1 / 5.2 (the register map) cannot drift from the model again.

Revision 9 added seven host-writable voice registers and widened `wave`, and
recorded them in 6.4 / 6.9 / 6.10 / 7 but never in 5.1 / 5.2 -- the sections an
implementer reads to build the control interface (#321). Nothing was sensitive
to it: the benches drive the model's own write port.

    .venv/bin/python -m pytest spec/reference/test_register_map.py -q

What is checked, with the model's own decoder as the independent source:

  * every address `SynthTopModel._write_voice` decodes (found by PROBING all
    256 addresses, not by reading a constant table) appears in 5.2's address
    paragraph;
  * the same set equals the addresses `rtl-sketch/voice_dp.v`'s write case
    decodes on the voice page (the thing that ships);
  * the seven revision-9 registers are named WITH their address in 5.2 and
    carry, in 5.1, exactly the width the model's mask enforces (probed by
    writing 0xFFFFFFFF);
  * every `wave` code 6.4 defines fits the width 5.1 states for `wave[k]`, and
    that width is the one the model masks to.

Each check has an injected-defect control (rule 2 of verification-rules.md): a
mutated copy of the real contract (or decoded set) is built in memory and the
check must go red. The parsers REFUSE rather than pass when they match nothing.
"""
import copy
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
CONTRACT = os.path.join(ROOT, "spec", "NUMERIC-CONTRACT.md")
VOICE_DP = os.path.join(ROOT, "rtl-sketch", "voice_dp.v")
sys.path.insert(0, os.path.join(ROOT, "model"))


class Refused(Exception):
    """The document cannot be read by this check: neither pass nor fail."""


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def section(text, start, end):
    m = re.search(r"^### %s[^\n]*\n(.*?)(?=^%s)" % (re.escape(start), end), text, re.M | re.S)
    if not m:
        raise Refused("section %s not found" % start)
    return m.group(1)


# ---- the model's decoder, by probing ----------------------------------------
def model_voice_addresses():
    """Every voice-page address the model's write decoder acts on."""
    import synth_top_model as m
    top = m.SynthTopModel()
    return {a for a in range(256) if top._write_voice(0, a, 0xFFFFFFFF) is not None}


def model_mask_bits(addr):
    """Bit width the model keeps at `addr`, by writing all ones and counting."""
    import synth_top_model as m
    top = m.SynthTopModel()
    before = copy.deepcopy(top.img)
    r = top._write_voice(0, addr, 0xFFFFFFFF)
    if isinstance(r, tuple) and len(r) > 1 and isinstance(r[1], int) and not isinstance(r[1], bool):
        return r[1].bit_length()
    changed = []
    for k, v in top.img.items():
        old = before[k]
        vals, olds = (v, old) if isinstance(v, list) else ([v], [old])
        changed += [x.bit_length() for x, o in zip(vals, olds) if x != o and isinstance(x, int)]
    if not changed:
        raise Refused("write to 0x%02X left no trace in the image" % addr)
    return max(changed)


def rtl_voice_addresses():
    """Addresses in voice_dp.v's `case (wr_addr)` block."""
    text = read(VOICE_DP)
    i = text.find("case (wr_addr)")
    if i < 0:
        raise Refused("no `case (wr_addr)` in voice_dp.v")
    body = text[i:text.find("endcase", i)]
    found = set()
    for line in body.splitlines():
        line = line.split("//")[0]
        for lhs in re.findall(r"((?:8'h[0-9A-Fa-f]{2}\s*,?\s*)+):", line):
            found |= {int(h, 16) for h in re.findall(r"8'h([0-9A-Fa-f]{2})", lhs)}
    if len(found) < 20:
        raise Refused("only %d addresses parsed from the RTL write case" % len(found))
    return found


# ---- the contract ------------------------------------------------------------
_HEX = re.compile(r"0x([0-9A-Fa-f]{2})(?:\s*[–-]\s*(?:0x)?([0-9A-Fa-f]{2}))?")


def listed_addresses(text):
    """Addresses named (singly or as ranges) in 5.2."""
    body = section(text, "5.2", "### 5.3")
    out = set()
    for lo, hi in _HEX.findall(body):
        out |= set(range(int(lo, 16), int(hi or lo, 16) + 1))
    if not out:
        raise Refused("no hex addresses in 5.2")
    return out


def named_addresses(text):
    """{`NAME`: address} for every `NAME` 0xAD pair in 5.2."""
    body = section(text, "5.2", "### 5.3")
    return {n: int(a, 16) for n, a in re.findall(r"`([A-Z0-9_]+)`\s+0x([0-9A-Fa-f]{2})\b", body)}


def table_width(text, name):
    """The width cell (leading integer) of 5.1's row for `name`."""
    body = section(text, "5.1", "### 5.2")
    for line in body.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0].startswith("`%s" % name):
            m = re.match(r"(\d+)", cells[1])
            if m:
                return int(m.group(1))
    raise Refused("5.1 has no row for %s" % name)


def wave_codes(text):
    """Highest wave code 6.4 defines (a range row `9–15` counts to its top)."""
    body = section(text, "6.4", "### 6.5")
    top = None
    for line in body.splitlines():
        m = re.match(r"\|\s*(\d+)(?:\s*[–-]\s*(\d+))?\s*\|", line)
        if m:
            top = max(top or 0, int(m.group(2) or m.group(1)))
    if top is None:
        raise Refused("no code rows in 6.4")
    return top


REV9 = {"WN": 0x0B, "NSEL": 0x1B, "MROUTE": 0x1F, "MMIX": 0x24,
        "MWHEEL": 0x25, "MPD": 0x26, "MFD": 0x27}


def problems(text, decoded=None, rtl=None):
    decoded = model_voice_addresses() if decoded is None else decoded
    rtl = rtl_voice_addresses() if rtl is None else rtl
    found = []
    missing = decoded - listed_addresses(text)
    if missing:
        found.append("5.2 omits decoded address(es) %s"
                     % ", ".join("0x%02X" % a for a in sorted(missing)))
    if decoded != rtl:
        found.append("model and RTL decode different addresses: %s"
                     % sorted(decoded ^ rtl))
    names = named_addresses(text)
    for n, a in REV9.items():
        if names.get(n) != a:
            found.append("5.2 does not pair `%s` with 0x%02X" % (n, a))
    top = wave_codes(text)
    w = table_width(text, "wave[k]")
    if top >= 1 << w:
        found.append("wave[k] is %d bits in 5.1 but 6.4 defines code %d" % (w, top))
    return found


# ---- the checks ---------------------------------------------------------------
def test_the_parsers_see_what_they_claim_to_see():
    text = read(CONTRACT)
    assert len(model_voice_addresses()) >= 30
    assert {0x00, 0x2D, 0x23} <= model_voice_addresses()
    assert len(listed_addresses(text)) >= 30
    assert wave_codes(text) == 15


def test_5_1_and_5_2_cover_everything_the_model_and_rtl_decode():
    assert problems(read(CONTRACT)) == []


@pytest.mark.parametrize("name,addr,bits", [
    ("WN", 0x0B, 16), ("NSEL", 0x1B, 1), ("MROUTE", 0x1F, 3), ("MMIX", 0x24, 16),
    ("MWHEEL", 0x25, 16), ("MPD", 0x26, 16), ("MFD", 0x27, 16)])
def test_rev9_register_widths_in_5_1_are_the_widths_enforced(name, addr, bits):
    assert model_mask_bits(addr) == bits
    assert table_width(read(CONTRACT), name) == bits


def test_wave_width_in_5_1_is_the_width_the_model_masks_to():
    assert model_mask_bits(0x04) == table_width(read(CONTRACT), "wave[k]") == 4


# ---- controls: each injected defect must turn the check red -------------------
def _mutate(text, old, new):
    out = text.replace(old, new, 1)
    assert out != text, "injection did not apply; update the control: %r" % old
    return out


def test_control_an_omitted_address_turns_this_red():
    """The #321 defect: a register decoded by the model but absent from 5.2."""
    text = read(CONTRACT)
    assert problems(text) == []
    body = section(text, "5.2", "### 5.3")
    bad = text.replace(body, re.sub(r"0x0B\b", "0x--", body))
    assert bad != text
    assert any("0x0B" in p for p in problems(bad)), problems(bad)


def test_control_a_register_added_to_the_model_alone_turns_this_red():
    """A future register that lands in the model (and RTL) but not in 5.2."""
    dec = model_voice_addresses() | {0x3E}
    rtl = rtl_voice_addresses() | {0x3E}
    found = problems(read(CONTRACT), decoded=dec, rtl=rtl)
    assert any("0x3E" in p for p in found), found


def test_control_a_model_rtl_disagreement_turns_this_red():
    found = problems(read(CONTRACT), rtl=rtl_voice_addresses() - {0x27})
    assert any("model and RTL" in p for p in found), found


def test_control_the_old_three_bit_wave_turns_this_red():
    text = read(CONTRACT)
    body = section(text, "5.1", "### 5.2")
    bad = text.replace(body, re.sub(r"(\| `wave\[k\]` \| )4( \|)", r"\g<1>3\2", body))
    assert bad != text
    assert any("wave[k] is 3 bits" in p for p in problems(bad)), problems(bad)


def test_control_a_wrong_stated_width_turns_this_red():
    text = read(CONTRACT)
    body = section(text, "5.1", "### 5.2")
    bad = text.replace(body, re.sub(r"(\| `MROUTE` \| )3( \|)", r"\g<1>2\2", body))
    assert bad != text
    assert table_width(bad, "MROUTE") != model_mask_bits(0x1F)


def test_control_an_unreadable_document_is_refused_not_passed():
    with pytest.raises(Refused):
        problems(_mutate(read(CONTRACT), "### 5.2 Writes and their semantics", "#### 5.2 Writes and their semantics"))
