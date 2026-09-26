#!/usr/bin/env python3
"""Start-red for tools/test_clap_d12a_probe.py: run from the repo root; exits 0 only if every closed-form test FAILS against mutated scorer estimators."""
# Start-red: the closed-form tests must FAIL against mutated scorer estimators.
import sys; sys.path.insert(0,'tools')
import run_case as rc, test_clap_d12a_probe as t
orig=rc._early_late_db; fails=n=0
for name,mut in (("split 30->40 ms", lambda s,e: orig(0.040,e)), ("window end 200->150 ms", lambda s,e: orig(s,0.150))):
    rc._early_late_db=mut
    for sr in (44100,48000):
        for kw in ({}, {'b':0.05}):
            n+=1
            try: t.test_scorer_ratio_equals_closed_form(sr,kw); print(name,sr,kw,"NOT CAUGHT")
            except AssertionError: fails+=1; print(name,sr,kw,"caught")
rc._early_late_db=orig
print(f"red run: {fails}/{n} closed-form tests failed against mutated scorer estimators")
sys.exit(0 if fails==n else 1)
