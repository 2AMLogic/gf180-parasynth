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


# --- the record readers start on a standard-library interpreter (#1623) -------
#
# `require_keys` lives here, not in manifest.py, because manifest imports NumPy:
# seven tools that only needed the key check lost `python3 -ES` startup (exit 2
# with instructions became exit 1 with ModuleNotFoundError) when they imported it.

import subprocess  # noqa: E402

_TOOLS = pathlib.Path(__file__).resolve().parent
_NUMPY_FREE_READERS = ("check_phrase_schedule", "deadline_reanalyse", "deadline_recapture_l2",
                       "r1_harvest", "r1_release_trials", "refaudio_fetch", "refaudio_local")


@pytest.mark.parametrize("module", _NUMPY_FREE_READERS)
def test_a_record_reader_imports_with_numpy_unavailable(module):
    """Control: `sys.modules['numpy'] = None` makes `import numpy` raise, on any
    interpreter, so this is red if any reader reaches manifest.py again."""
    code = ("import sys; sys.modules['numpy'] = None; "
            f"sys.path.insert(0, {str(_TOOLS)!r}); import {module}")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-400:]


def test_refaudio_fetch_without_storage_refuses_with_status_2_without_numpy():
    code = ("import sys; sys.modules['numpy'] = None; "
            f"sys.path.insert(0, {str(_TOOLS)!r}); import refaudio_fetch as f; "
            "sys.exit(f.main(['808-from-mars.zip', '/tmp/x.wav']))")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 2 and "ModuleNotFoundError" not in r.stderr, r.stderr[-400:]


def test_manifest_still_exposes_require_keys_as_the_same_function():
    pytest.importorskip("numpy")
    import manifest
    assert manifest.require_keys is refusal.require_keys
