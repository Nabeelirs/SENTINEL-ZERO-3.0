# Architecture

A Python 3.11 modular monolith. The new `public_app.py` configures an
operator-provisioned pinned archive, then opens the accepted Earth Atlas.
Existing `atlas_app.py` and `app.py` remain unchanged. Science and evidence
modules do not import Streamlit.

```mermaid
flowchart LR
  C[NASA CMR metadata] --> D[Bounded global discovery]
  N[Recorded NASA NISAR acquisitions] --> H[Existing Harmony subsets]
  H --> V[Identity and scientific inspection]
  V --> P[SZ-005 common eligibility]
  P --> A[SZ-006 saved dB change]
  A --> U[Earth Atlas and exact-cell science]
  H --> B[EarthBOM dependencies]
  P --> B
  A --> B
  B --> T[Evidence Trust Digital Twin]
  T --> L[Disposable derived-copy challenge]
  L --> R[Restore or recompute and reverify]
  U --> E[Current-state Evidence Passport]
  R --> E
  D --> W[Candidate processing work order]
```

CMR results do not automatically become accepted pixel analysis. A new region
needs authorized known-size subsets and actual identity/channel/grid/support/QA
acceptance. The visitor workspace does not download full granules.

| Boundary | Package |
|---|---|
| Discovery / ingestion | `discovery/`, `ingestion/`, `application/global_explorer.py` |
| Protected data loading | `application/data.py`, `application/bundle.py` |
| Eligibility / saved change | `science/eligibility.py`, `preparation.py`, `change.py` |
| Environmental context | `corroboration/`; validated original-file import, no supplied rainfall |
| Evidence | `evidence/earthbom.py`, `lab.py`, `rebuild.py`, `impact.py` |
| UI | `presentation/earth_atlas.py`, local Canvas HTML/CSS/MJS and existing five-stage views |
| Reporting | `reporting/investigation.py`, independent of Streamlit |

No separate API, database, paid service, model API, CDN or map tiles are needed.
The prepared pilot is offline after installation/data provision. Explicit CMR
searches require public network access. See [deployment boundaries](deployment.md).
