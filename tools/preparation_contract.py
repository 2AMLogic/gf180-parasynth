"""The preparation contract for a paired ours-vs-reference comparison (#163).

START-RED STUB: accepts every pair. Replaced by the implementation in the next
commit; it exists so the tests in `tools/test_preparation_contract.py` can be
watched failing against code with the right interface and no behaviour.
"""
from __future__ import annotations

from typing import NamedTuple


class Refused(Exception):
    """A precondition of the apparatus failed."""


class Side(NamedTuple):
    name: str
    y: object
    sr: int


def check_prepared_pair(a, b, **_kw) -> dict:
    return {}
