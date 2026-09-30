"""fpga/verify_flash.py: header parsing, chain preconditions, and the verdict.

Every case here is pure Python -- no board, so this runs in `make verify` even
though the script it covers does not. What cannot be tested without hardware
is openFPGALoader's own behaviour; what can be, and is what actually went
wrong by hand, is whether a wrong state REFUSES instead of reporting.
"""
import subprocess
import sys
from pathlib import Path

import pytest

import verify_flash as vf

MAGIC = bytes([0x0f, 0xf0] * 4 + [0x00])


def make_bit(payload: bytes, *, e_len: int = None, drop_e: bool = False) -> bytes:
    """A synthetic .bit. e_len overrides the declared length; drop_e omits 'e'."""
    out = bytearray()
    out += len(MAGIC).to_bytes(2, "big") + MAGIC
    out += (1).to_bytes(2, "big")
    for key, text in zip("abcd", [b"design;\x00", b"xc7a100tcsg324\x00",
                                  b"2026/09/29\x00", b"12:00:00\x00"]):
        out += key.encode() + len(text).to_bytes(2, "big") + text
    if not drop_e:
        out += b"e" + (len(payload) if e_len is None else e_len).to_bytes(4, "big")
        out += payload
    return bytes(out)


# ---- the header ----------------------------------------------------------

def test_payload_is_the_bytes_after_the_header():
    payload = bytes(range(256)) * 4
    assert vf.bit_payload(make_bit(payload)) == payload


def test_payload_excludes_the_header():
    """The whole file is NOT the payload; comparing it would fail every write."""
    payload = b"\xaa" * 64
    raw = make_bit(payload)
    assert len(vf.bit_payload(raw)) < len(raw)


@pytest.mark.parametrize("raw, fragment", [
    (make_bit(b"\x00" * 32, e_len=31), "declares 31"),
    (make_bit(b"\x00" * 32, e_len=33), "declares 33"),
    (make_bit(b"", drop_e=True), "no 'e' section"),
    (b"\x00\x02", "too short"),
])
def test_a_malformed_header_refuses_rather_than_guessing(raw, fragment):
    with pytest.raises(vf.Refused) as e:
        vf.bit_payload(raw)
    assert fragment in str(e.value)


def test_the_real_pads_bitstream_parses_if_it_is_here():
    """Not synthetic: the #449 image, when the branch carrying it is checked out."""
    bit = Path(__file__).parent / "reports/arty/pads-demo-2025.1/arty_pads.bit"
    if not bit.is_file():
        pytest.skip("pads-demo-2025.1 is not on this branch")
    payload = vf.bit_payload(bit.read_bytes())
    assert len(payload) == 3825788


# ---- the chain -----------------------------------------------------------

ONE = """index 0:
\tidcode 0x3631093
\tmanufacturer xilinx
\tfamily artix a7 100t
\tmodel  xc7a100
\tirlength 6
"""


def test_one_device_gives_its_model():
    assert vf.chain_model(ONE) == "xc7a100"


def test_an_empty_chain_refuses():
    with pytest.raises(vf.Refused, match="no device"):
        vf.chain_model("empty\nJtag frequency : requested 6.00MHz\n")


def test_a_multi_device_chain_refuses():
    with pytest.raises(vf.Refused, match="2 devices"):
        vf.chain_model(ONE + ONE.replace("xc7a100", "xc7a35"))


# ---- the verdict, and the control ----------------------------------------

def test_identical_buffers_have_no_difference():
    payload = bytes(range(256))
    assert vf.first_difference(payload, payload) is None


def test_one_flipped_byte_is_caught():
    """The injected-bug control: a read-back that is wrong must not pass."""
    payload = bytearray(range(256))
    corrupt = bytearray(payload)
    corrupt[137] ^= 0x01
    assert vf.first_difference(payload, corrupt) == (137, 137, 136)


def test_a_missing_bitstream_refuses_before_touching_the_board(tmp_path, capsys):
    code = vf.main(["--bit", str(tmp_path / "absent.bit")])
    assert code == vf.REFUSED
    assert "no such bitstream" in capsys.readouterr().out


def test_refused_is_distinct_from_pass_and_fail():
    assert len({vf.PASS, vf.FAIL, vf.REFUSED}) == 3


def test_the_script_runs_standalone():
    """`python3 fpga/verify_flash.py` with no board must refuse, not traceback."""
    r = subprocess.run([sys.executable, str(Path(vf.__file__)), "--bit", "/nope.bit"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == vf.REFUSED, r.stderr
    assert "REFUSED" in r.stdout
