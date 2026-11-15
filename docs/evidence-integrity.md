# Evidence integrity

The pilot EarthBOM records 17 nodes and 62 dependency edges. Relationships describe
the available processing/provenance path, not complete NASA-side chain of custody
or environmental causation.

| Dimension | Status meaning |
|---|---|
| Local integrity | Bytes match independently recorded local references, or are missing/mismatched |
| Scientific quality | Conditional PROVISIONAL science; calibration/registration limits persist |
| Physical interpretation | Cause remains unverified |

SHA-256 matching does not authenticate NASA, establish calibration or prove
scientific correctness. A compromised host may undermine code/references; local
history is not an externally signed audit anchor.

## Controlled challenge and recovery

Verified reference → create disposable statistics copy → modify its bytes →
detect mismatch → trace affected manifest/report dependencies → restore/rebuild.
This is an explicit local demonstration, not evidence of an attack on NASA.

Only a service-created UUID directory and fixed derived-artifact filename are
mutable. Visitors cannot supply a target path. Reference hashing, containment,
link/reparse/hardlink guards and atomic replacement constrain writes. The
challenged copy never provides scientific measurement values.

Restore copies independently verified baseline bytes. Rebuild reloads saved
change/common-support inputs and recomputes statistics, checking numerical
equivalence, byte identity and readback. No acceptance reference is rewritten.
Recovery does not remove scientific uncertainty.

Public hosting needs tested request/resource budgets and session retention. The
current shared lab parent admits at most 100 UUID directories; automatic expiry
of disconnected sessions is not established. Configure and test lifecycle/admission
before unattended use; never delete arbitrary visitor paths or protected evidence.
