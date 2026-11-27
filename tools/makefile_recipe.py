"""Read one Makefile target's recipe text -- shared by suites that must pin
their own wiring ("check that the thing you are testing is the thing that
ships", CLAUDE.md).

`tools/test_manifest.py` uses it to assert the manifest injected-defect
controls' standalone command is enumerated in `make verify-fast`, the target
CI actually runs (that suite itself moved to the rungs.yml `tools` job's
wholesale `pytest tools/` directory run, #812, so its own CI wiring is
asserted against the workflow instead); `tools/test_sensitivity.py`
uses it to assert every injected control is enumerated in `make controls`
with the `--expect` token its exit code implies (issue #836). One parser,
not one per suite: a copied Makefile parser is exactly the wiring-drift
defect those assertions exist to catch.
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def live_recipe_lines(lines: list[str]) -> list[str]:
    """The recipe lines make actually hands to the shell: comment lines
    (leading whitespace then `#`) are dropped, because their text never runs.
    Without this, a control disabled the ordinary debugging way -- `#` after
    the leading tab, invocation text left intact -- still matches a regex or
    substring test over the joined recipe, and the wiring check stays green
    on exactly the mutation it exists to catch (issue #855, rule 8). Which
    failure mode the mutation produces depends on the recipe's shape as well
    as the line's position. A *final* recipe line is dropped silently -- rc
    0, one entry gone. A *mid-recipe* line ends the command at the `#` (the
    trailing backslash is inside the comment); in the argument-list recipes
    this filter is used on -- one continued logical line feeding quoted
    arguments to `$(RUN)`, as `controls:` and `verify-fast:` are -- the
    rest of the recipe becomes a bogus command and make fails loudly
    (`Error 127`) with every later entry dropped, while in a sequence of
    shell commands the `#` ends only that command and the next line runs
    normally (rc 0, one entry gone). Either way the entry does not run,
    which is why the filter is right in both cases."""
    return [ln for ln in lines if not ln.lstrip().startswith("#")]


def makefile_recipe(target: str, *, root: pathlib.Path = ROOT) -> str:
    """The *live* recipe lines of one Makefile target, joined. Used to assert
    that a check is enumerated where CI will actually reach it. `root` is
    injectable so tests can pin the parser against a Makefile that does not
    exist in this repository."""
    lines = (root / "Makefile").read_text().split("\n")
    out, inside = [], False
    for line in lines:
        if re.match(rf"^{re.escape(target)}\s*:", line):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace():
                break
            out.append(line)
    live = live_recipe_lines(out)
    assert live, f"no recipe found for Makefile target {target!r}"
    return "\n".join(live)
