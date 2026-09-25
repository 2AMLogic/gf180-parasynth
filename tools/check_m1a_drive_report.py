"""Consistency: every number the corrected README quotes is what report.json holds."""
import json, sys
r = json.load(open("docs/scorecard/mono-m1a-miniv3/drive-experiment/report.json"))
R = r["reference_moving_phase"]; C = r["candidates"]
want = {"h3": -0.247, "h5": -0.270, "h7": -0.064, "h9": 0.460, "h11": 1.045}
ok = all(abs(C["0.25"]["moving_phase"]["partials"][h]["ratio_to_h1_db"] - R[h]["ratio_to_h1_db"] - v) < 5e-4 for h, v in want.items())
h8 = [c["harmonic_errors"][2]["error_db"]["h8"] for c in (C["0.75"], C["0.25"])]
ok &= abs(h8[0] - 19.82) < 5e-3 and abs(h8[1] - 22.63) < 5e-3 and h8[1] > h8[0] > 0
ok &= [len(C[d]["regressions"]) for d in ("0.5", "0.25")] == [4, 6]
ok &= all(all(v for k, v in C[d]["property_passes"].items() if C["0.75"]["property_passes"].get(k)) for d in C)
ok &= r["choice"] is None
print("consistent" if ok else "INCONSISTENT"); sys.exit(0 if ok else 1)
