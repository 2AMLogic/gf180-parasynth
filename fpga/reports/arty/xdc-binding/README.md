# The Arty constraint-binding record

`binding.json` and `binding.txt` are the output of

```
python3 fpga/verify_xdc_binding.py --outdir fpga/reports/arty/xdc-binding
```

and they are what makes `python3 tools/check_arty_evidence_binding.py --scope
publication` answerable (#436). Before this record existed that mode could
only REFUSE: **no committed record in this repository had ever hashed
`fpga/boards/arty-a7-100.xdc`.** Every `verification.json` is a digital-bench
record that hashes `sources() + roms()` — correct, because
`fpga/verify_uart_bridge.py` drives no physical pin — and every record that
*does* carry the constraint hash is a publication record, which is history by
construction.

## Why this is evidence rather than a hash pinned by hand

Nothing here can be updated by editing it. The record is written only when all
nine of the bench's properties pass **on the bytes named in its own
`source_sha256`**, and every property is a cross-check against something
maintained independently of the constraint file:

| property | checked against |
|---|---|
| `ports_declared`, `ports_constrained` | `arty_a7_top`'s port list in `fpga/rtl/arty_a7_top.v` |
| `hier_names`, `hier_separators` | the generate blocks, instances, flops and nets of the compiled RTL |
| `clock_names` | the clocks this XDC itself creates |
| `query_counts`, `exception_model` | the required match counts declared in `fpga/xdc_bindings.EXPECT` |
| `output_delay_budget` | the PCM5102 datasheet budget derived in `fpga/ext_io_timing.py` |
| `uart_gate` | `fpga/ext_io_timing.uart_gate_drift` |

`hier_separators` is #315 as a check rather than as a comment: the pre-#315
file — the constraints **R0 and R1 were actually routed against** — fails it
by name, on lines 42 and 46, in 0.2 s.

```
git show 383f10b^:fpga/boards/arty-a7-100.xdc > /tmp/pre315.xdc
python3 fpga/verify_xdc_binding.py --xdc /tmp/pre315.xdc     # exit 1
```

## Why this directory is regenerated in place

The sibling directories here (`uart-clean`, `drift-clean`, `rev14-clean`, …)
are digital-bench runs and are **never** rewritten: a published image cites
them by hash, so rewriting one would destroy the proof of a shipped bitstream.

Nothing cites this record by hash. It is not history; it is a statement about
the tree as it stands, and when the XDC or a compiled source moves it is
*wrong*, not superseded. So it is refreshed in place by re-running the command
at the top of this file — which takes 0.2 s and, crucially, cannot be made to
say PASS by any edit that does not also make the constraint file bind.

## What it does NOT claim

How many objects a query **matched**. That needs the elaborated netlist and is
asserted by `fpga/xdc_bindings.tcl_assertions` inside the Vivado build (which
exits 3 on a mismatch) and re-checked at publish time by
`fpga/xdc_bindings.check_report`. This record is constraint-**binding**
evidence, not constraint-**effect** evidence, and its own `scope` field says
so.
