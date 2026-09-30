"""#354: the ladder's (x * gain) >> 11 register must hold every value its
ports can produce. Known answer, independent of either implementation:
x is a 17-bit signed port and gain a 20-bit unsigned register, so the
extremes are -2^16 * (2^20 - 1) >> 11 = -33,554,400 and
(2^16 - 1) * (2^20 - 1) >> 11 = 33,553,888 -- 26 bits signed. R1's 25-bit
`xg` wrapped there, and the all-maximum voice image flipped the output's sign."""
import pathlib
import re

RTL = pathlib.Path(__file__).resolve().parents[1] / "rtl-sketch" / "ladder_dp_n.v"


def _xg_bits(src: str, control: bool) -> int:
    SW = int(re.search(r"parameter SW\s*=\s*(\d+)", src).group(1))
    body = src.split("`ifdef INJECT_BUG_LADDER_XG25", 1)[1]
    ctl, clean = body.split("`else", 1)
    decl = ctl if control else clean.split("`endif", 1)[0]
    m = re.search(r"reg signed \[SW(\+(\d+))?:0\]\s+xg;", decl)
    return SW + 1 + (int(m.group(2)) if m.group(2) else 0)


def _needed_bits(x_bits=17, gain_bits=20, shift=11) -> int:
    lo = (-(1 << (x_bits - 1)) * ((1 << gain_bits) - 1)) >> shift
    hi = (((1 << (x_bits - 1)) - 1) * ((1 << gain_bits) - 1)) >> shift
    assert (lo, hi) == (-33554400, 33553888)
    return max(abs(lo).bit_length(), hi.bit_length()) + 1


def test_xg_holds_every_port_product():
    assert _xg_bits(RTL.read_text(), control=False) >= _needed_bits() == 26


def test_the_r1_width_is_kept_as_a_control_and_is_too_narrow():
    assert _xg_bits(RTL.read_text(), control=True) == 25 < _needed_bits()
