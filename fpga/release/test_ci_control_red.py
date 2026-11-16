"""Deliberately broken, for #853's observed-red control.

The fpga-uncovered job must be SEEN to fail on a broken fpga/ test before
anyone trusts its green. Removed by the very next commit; never on main.
"""


def test_this_file_must_turn_the_fpga_uncovered_job_red():
    assert False, "#853 control: the new job must be observed failing on a broken fpga/ test"
