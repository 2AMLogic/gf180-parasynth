#!/usr/bin/env python3
"""Verify that a board's SPI flash holds a given bitstream, by reading it back.

    python3 fpga/verify_flash.py --bit fpga/reports/arty/pads-demo-2025.1/arty_pads.bit
    python3 fpga/verify_flash.py --bit <path> --write      # flash first, then verify

NOT part of `make verify`: it needs a board on the end of a USB cable.

Exit 0 PASS, 1 FAIL, 2 REFUSED.

WHY THIS EXISTS, and it is a tooling defect of ours, not openFPGALoader's.
The pads image (#449) was flashed by hand as

    openFPGALoader -b arty_a7_100t -f --verify arty_pads.bit 2>&1 | tail -40
    echo "exit=${PIPESTATUS[0]}"

which printed `Writing ... Done` and `Verifying write ... Done`, and then
`exit=` -- EMPTY. `PIPESTATUS` does not survive into the next command, so the
one number that distinguishes a good write from a bad one was rendered as
nothing, in the place where the result belongs. That is CLAUDE.md's
`FAIL(??)` and its `exit=$?`-after-a-pipe, reproduced a third time. The fix is
not a more careful pipeline; it is `subprocess.run`, which cannot lose a
status.

So this script never reads a tool's stdout to decide whether the tool
succeeded. It reads the returncode, and then it reads THE FLASH: the verdict
is a byte-for-byte comparison between the device's contents and the payload
carved out of the .bit, which is a signal independent of anything
openFPGALoader printed about its own work.

PRECONDITIONS, asserted here rather than assumed, each a REFUSED and not a
FAIL when it does not hold:

  tool        openFPGALoader is on PATH
  chain       exactly one device is on the JTAG chain -- zero means no board
              or no power, more than one means the wrong `-b`
  part        that device's model matches --expect-part, when given. A flash
              write aimed at the wrong board is not a failing test, it is a
              question that was never asked.
  header      the .bit's 'e' section length accounts for exactly the bytes
              that remain. A misparsed header would silently compare the
              wrong span and pass.

A read-back that matches proves the flash holds these bytes. It does not
prove the FPGA will configure from them at power-on: that also needs the
board's mode jumper set to SPI (JP1 on Arty A7), which is not electrically
visible from here and is the operator's to check.
"""
import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PASS, FAIL, REFUSED = 0, 1, 2


class Refused(Exception):
    """A precondition did not hold, so no verdict is available."""


def bit_payload(raw: bytes) -> bytes:
    """The configuration payload of a Xilinx .bit -- the bytes that reach flash.

    The header is a u16-counted magic, a u16, then NUL-terminated string fields
    keyed 'a'..'d' with u16 lengths, then 'e' with a u32 length and the raw
    data. openFPGALoader strips exactly this, so comparing against the whole
    file would fail on every correct write.
    """
    if len(raw) < 4:
        raise Refused(f"bitstream is {len(raw)} bytes, too short to hold a header")
    i = 2 + int.from_bytes(raw[0:2], "big") + 2
    while i < len(raw):
        key = raw[i]
        i += 1
        if key == ord("e"):
            if i + 4 > len(raw):
                raise Refused("'e' section is truncated before its length field")
            n = int.from_bytes(raw[i:i + 4], "big")
            i += 4
            if i + n != len(raw):
                raise Refused(f"'e' section declares {n} bytes, {len(raw) - i} remain")
            return raw[i:i + n]
        if i + 2 > len(raw):
            raise Refused(f"field {chr(key)!r} is truncated before its length field")
        i += 2 + int.from_bytes(raw[i:i + 2], "big")
    raise Refused("no 'e' section in the .bit header")


def chain_model(detect_stdout: str) -> str:
    """The single device's model, or Refused if the chain is not exactly one."""
    models = re.findall(r"^\s*model\s+(\S+)", detect_stdout, re.MULTILINE)
    if not models:
        raise Refused("no device on the JTAG chain -- board unplugged, "
                      "unpowered, or the wrong --board")
    if len(models) > 1:
        raise Refused(f"{len(models)} devices on the JTAG chain ({', '.join(models)}); "
                      "this script addresses a single-device chain")
    return models[0]


def _run(argv, timeout):
    """subprocess.run, so the status cannot be lost. Never parsed for success."""
    if shutil.which(argv[0]) is None:
        raise Refused(f"{argv[0]} is not on PATH")
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def detect(board, timeout=60):
    r = _run(["openFPGALoader", "-b", board, "--detect"], timeout)
    if r.returncode != 0:
        raise Refused(f"--detect exited {r.returncode}\n{r.stderr[-2000:]}")
    return chain_model(r.stdout + r.stderr)


def write_flash(board, bit, timeout=1800):
    r = _run(["openFPGALoader", "-b", board, "-f", str(bit)], timeout)
    if r.returncode != 0:
        raise Refused(f"flash write exited {r.returncode}\n{r.stderr[-2000:]}")


def read_flash(board, nbytes, dest, timeout=1800):
    # The output path is POSITIONAL: `-o` is not an openFPGALoader option and
    # is rejected with "Argument '<path>' failed to parse", which reads like a
    # problem with the path rather than with the flag.
    r = _run(["openFPGALoader", "-b", board, "--dump-flash",
              "--file-size", str(nbytes), str(dest)], timeout)
    if r.returncode != 0:
        raise Refused(f"--dump-flash exited {r.returncode}\n{r.stderr[-2000:]}")
    got = Path(dest).read_bytes()
    if len(got) != nbytes:
        raise Refused(f"dump returned {len(got)} bytes, asked for {nbytes}")
    return got


def first_difference(want: bytes, got: bytes):
    """(offset, want_byte, got_byte) of the first mismatch, or None."""
    for k in range(len(want)):
        if want[k] != got[k]:
            return k, want[k], got[k]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bit", required=True, type=Path, help="the .bit to compare against")
    ap.add_argument("--board", default="arty_a7_100t", help="openFPGALoader -b name")
    ap.add_argument("--expect-part", default=None,
                    help="refuse unless the detected model matches, e.g. xc7a100")
    ap.add_argument("--write", action="store_true",
                    help="flash the bitstream first; without it, verify only")
    ap.add_argument("--dump", type=Path, default=None,
                    help="keep the read-back here instead of a temporary file")
    a = ap.parse_args(argv)

    try:
        if not a.bit.is_file():
            raise Refused(f"no such bitstream: {a.bit}")
        payload = bit_payload(a.bit.read_bytes())

        model = detect(a.board)
        if a.expect_part and a.expect_part not in model:
            raise Refused(f"detected {model!r}, expected {a.expect_part!r}")
        print(f"chain: one device, model {model}")
        print(f"payload: {len(payload)} bytes from {a.bit}")

        if a.write:
            print(f"writing {a.bit} to flash ...")
            write_flash(a.board, a.bit)

        with tempfile.TemporaryDirectory() as tmp:
            dest = a.dump or Path(tmp) / "flash_readback.bin"
            print("reading flash back ...")
            got = read_flash(a.board, len(payload), dest)
    except Refused as e:
        print(f"REFUSED: {e}")
        return REFUSED
    except subprocess.TimeoutExpired as e:
        print(f"REFUSED: {e.cmd[0]} timed out after {e.timeout}s")
        return REFUSED

    diff = first_difference(payload, got)
    if diff is None:
        print(f"PASS: flash matches the bitstream payload, all {len(payload)} bytes")
        print("Configuring from it at power-on also needs the board's mode jumper "
              "set to SPI (JP1 on Arty A7); this script cannot see that.")
        return PASS
    off, want, has = diff
    print(f"FAIL: flash differs from the bitstream at offset {off} "
          f"(expected 0x{want:02x}, read 0x{has:02x})")
    return FAIL


if __name__ == "__main__":
    sys.exit(main())
