#!/usr/bin/env python3
"""refusal -- the common base class for every module's `Refused`.

A REFUSED is a first-class outcome in this repository, distinct from pass and
fail (docs/failure-modes.md): it reports a stated missing precondition -- an
input that is missing, altered, of the wrong format, or otherwise outside
what the tool can bind -- with its reason, instead of a traceback or a
silently degraded measurement. A tool that answers when it cannot is worse
than one that is absent, because its output looks exactly like data.

Each module keeps its own `class Refused(...)` -- the name at the raise and
catch sites is deliberate and greppable (#163, plan090) -- and, from #784 on,
every new definition subclasses this base (the re-base of the existing ones
is #815). A handler written against `refusal.Refused` therefore catches any
module's refusal by class, without `except Exception`, which would relabel an
implementation error as a refusal -- the inverse defect: an unexpected
exception is an EXECUTION ERROR (plan090), never a refusal, and the
deliberately-total handlers that say so stay exactly as they are.

Exit conventions are untouched here: how a caught refusal maps to a process
exit code is #804's layer. This module only fixes the class boundary.
"""
from __future__ import annotations

from typing import Callable


class Refused(Exception):
    """A stated missing precondition: a first-class outcome, distinct from
    pass and fail, carrying its own reason."""


def require_keys(record, required, what: str, *, refused: Callable[[str], Exception]) -> None:
    """REFUSE a record that parses but cannot be read: not a JSON object, or
    missing -- or carrying `null` for -- any key in `required` (#1604).

    `manifest.load_json_refused` refuses a record it cannot PARSE. This refuses one it
    can parse but cannot READ. `record["takes"]` on `{"state": "CAPTURED"}`
    is a `KeyError`, which a `main()` that catches only its own `Refused`
    renders as a traceback and exit 1 -- FAIL, "the comparison ran and
    disagreed" -- where `tools/exit_contract.py` says an unmet precondition
    is exit 2 and nothing was measured. The traceback also reads as a bug in
    the reader rather than as a statement about the record.

    `refused` is the CALLER'S own `Refused` class, so what is raised here is
    what its `main()` already catches and reports (refusal.py: the name at
    the raise and catch sites is deliberate and greppable). It is
    keyword-only with no default on purpose: a default of this module's
    `Refused` would raise a class the caller's handler does not catch, which
    is the traceback this function exists to replace, reintroduced without a
    sound. Any class taking one message string qualifies, and every
    `Refused` in the tree is one.

    All three of these are refusals rather than a pass:

      * the record is not a JSON object (`5`, `null`, `[]`). Checked here
        because `key not in record` cannot answer it -- on a list that is
        merely true and would read as a missing key; on a number, a
        `TypeError`;
      * a required key is absent;
      * a required key is present but `null`. Every reader that subscripts a
        record dereferences what it finds (`record["takes"].items()`), and
        `None` there is the same traceback as no key. A key whose `null` is a
        VALUE -- `measurement()`'s `value=None` is an explicit refusal -- must
        not be listed in `required`: read it with `.get()` and say what the
        `null` means.

    What is NOT checked is the value's TYPE. Presence and non-`null` are all
    this asserts; that is a decision (#1639), not an oversight. The `null`
    argument above applies verbatim to a value the reader cannot dereference:
    `{"takes": 5}` passes here and then fails in the reader, where
    `record["takes"].items()` is an `AttributeError` -- a traceback and
    exit 1, the outcome an absent or `null` key is refused to prevent. It is
    left so because a mistyped record is an authoring error rather than the
    truncated write this guard exists for, and each caller alone knows the
    type it dereferences. The limit is pinned -- so that closing it cannot
    happen unnoticed -- by
    `test_require_keys_pins_the_limit_that_a_values_type_is_not_checked`
    in test_manifest.py: a type check is a deliberate change that turns that
    test red and rewrites this paragraph.

    Names every offending key, not only the first (as `validate_measurement`
    does), so a record is not regenerated once per key. A bare string in
    `required` is one key, not an iterable of characters.
    """
    if isinstance(required, str):
        required = (required,)
    if not isinstance(record, dict):
        kind = "null" if record is None else f"a JSON {type(record).__name__}"
        raise refused(f"{what} is {kind}, not a JSON object -- it carries no keys at "
                      f"all, so none of the required ones can be read: the wrong file, "
                      f"or a partial write")
    missing = [k for k in required if k not in record]
    null = [k for k in required if k in record and record[k] is None]
    stated = []
    if missing:
        stated.append(f"is missing required key(s) {', '.join(map(repr, missing))}")
    if null:
        stated.append(f"carries null for required key(s) {', '.join(map(repr, null))}")
    if stated:
        raise refused(f"{what} {' and '.join(stated)} -- a partial record is not a result: "
                      f"regenerate it with the tool that writes it rather than add the "
                      f"key by hand")
