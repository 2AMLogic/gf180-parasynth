# DC-blocker centroid gate qualification

The draft candidate's preservation vector allows only a 1% change in global
spectral centroid. That gate is not an independent measure of audible-band
preservation: a 10 Hz contaminant can move the global centroid by 15.8% while
the 1 kHz component remains unchanged.

The closed-form two-tone probe in
`tools/probes/dc_centroid_gate_qualification.py` reproduces that failure. It
also measures the centroid after excluding frequencies below 20 Hz. The
qualified value stays at 1 kHz before and after the contaminant is removed.

This qualifies the gate; it does not qualify the DC-blocker candidate. The
candidate still misses its required rimshot improvement and remains disabled.
The existing candidate measurements must be rerun with the qualified
preservation definition before any acceptance decision.
