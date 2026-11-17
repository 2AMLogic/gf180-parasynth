"""tools/refusal.py: the shared base class catches sibling subclasses by
class, and nothing else (#784)."""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import refusal                                                    # noqa: E402


def test_refused_is_a_single_exception_subclass():
    assert issubclass(refusal.Refused, Exception)


def test_a_sibling_subclass_is_caught_by_except_refusal_refused():
    class CorpusRefused(refusal.Refused):
        """the shape a module's own Refused takes once re-based (#815)"""

    caught = None
    try:
        raise CorpusRefused("the corpus is 24-bit; reference must be 16-bit")
    except refusal.Refused as exc:
        caught = exc
    assert isinstance(caught, CorpusRefused) and "16-bit" in str(caught)


def test_an_unrelated_exception_is_not_caught_by_except_refusal_refused():
    class Unrelated(RuntimeError):
        pass

    with pytest.raises(Unrelated):
        try:
            raise Unrelated("an implementation error, not a refusal")
        except refusal.Refused as exc:      # must not catch: the inverse defect
            raise AssertionError(f"refusal.Refused caught {type(exc).__name__}")
