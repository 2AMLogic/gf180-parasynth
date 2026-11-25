"""Hermit witness for the SPI knob budget. Section 5 of NUMERIC-CONTRACT
assigns the cutoff ceiling to register 0x19 and the floor to 0x18.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fpga"))
from spi_host import MusicHost


def test_cutoff_ceiling_turn_schedules_one_ceiling_write():
    host = MusicHost()
    host.knob(100, "cutoff_hi", 2400)
    writes = [row.w for row in host.schedule()]
    assert [(w.sec, w.addr, w.data) for w in writes] == [(0, 0x19, 2400)]
