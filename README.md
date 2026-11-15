# SENTINEL ZERO 3.0

**Earth Evidence Intelligence Platform**
Detect the Change. Investigate the Evidence. Protect the Decision.

Satellite maps can reveal a numerical difference without explaining its cause—or
whether the evidence supporting a report is still intact. SENTINEL ZERO joins
real NASA–ISRO NISAR measurements with an inspectable processing path, exact-cell
investigation and a controlled evidence-integrity challenge.

This source-only package contains no NISAR raster data, scientific archive,
mission-derived screenshots or private acceptance evidence.

Independent NASA Space Apps 2026 project for **Dancing with the SARs**. NASA,
ISRO and ASF provide scientific resources; they do not endorse this application.
**Public application: not deployed or independently verified.** Release
preparation is not completed NASA submission.

## One real investigation, an extensible workflow

The verified study is an exploratory **5.53 km × 5.53 km** projected window
near the Kelani area of Sri Lanka, not the entire Kelani River Basin.

| Scientific record | Actual evidence |
|---|---|
| Product | NISAR L2 **PROVISIONAL** GCOV, Frequency A / HHHH gamma-zero power |
| Before / after | 6 / 18 October 2026, about 00:14:46–00:15:21 UTC |
| Granules | `G4348875889-ASF` / `G4378876686-ASF` |
| Collection | `C2854338529-ASF`; DOI [10.5067/NIL2GCOV-P1](https://doi.org/10.5067/NIL2GCOV-P1) |
| Declared grid | EPSG:32644; 553 × 553 cells; nominal 10 m posting |
| Common support | 305,137 eligible cells; 672 excluded |
| Measured change | Mean +0.338477 dB; median +0.342195 dB |

Positive means greater radar power on the second date; negative means lower.
**Neither establishes flooding, damage or a physical cause.** PROVISIONAL
radiometry and unresolved quality/registration limitations remain visible.

## Follow the evidence

1. **Observe Change** — actual Before, After, Change and aligned Compare modes;
   pan, zoom and select a native cell. Global Explorer queries real CMR metadata.
2. **Investigate Science** — exact power/change, coordinates, eligibility, looks,
   distributions and descriptive magnitude thresholds.
3. **Challenge Evidence** — EarthBOM/local checks; modify only a disposable
   statistics copy and detect a real mismatch.
4. **Trace Consequences** — follow recorded Digital Twin dependencies and see
   which reports need revalidation.
5. **Restore & Report** — restore bytes or genuinely recompute statistics;
   export a readable current-state Evidence Passport PDF.

Global **catalog discovery** works; worldwide validated **pixel processing** is
not complete. Historical rainfall import is supported, but original usable
observations are not supplied. Physics Witness starts in an honest
insufficient-evidence state. No synthetic observations replace missing data.

## Run the verified demonstration

Use **Python 3.11**. The source-only publication contains no NASA/ISRO raster,
archive or screenshot. Public data access does not establish redistribution
rights. Provision real data only after separate data-rights and hosting approval;
the application does not request Earthdata credentials from visitors.

```powershell
py -3.11 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install setuptools==65.5.0 wheel==0.45.1
& .\.venv\Scripts\python.exe -m pip install -r requirements-ui.txt
& .\.venv\Scripts\python.exe -m pip install --no-build-isolation --no-deps .
$env:SENTINEL_ZERO_DEMO_ARCHIVE = 'D:\approved-data\scientific_data.zip'
& .\.venv\Scripts\python.exe -m streamlit run public_app.py --server.port 8501
```

The example path is operator configuration, not a bundled file or visitor input.
Open http://127.0.0.1:8501; confirm **SZ-UIX-02 / EARTH ATLAS 02**. Missing or
corrupt data blocks display. [Linux commands, hashes and reproduction](docs/reproducibility.md).
Accepted private development previews remain on 8507 and 8508.

## Judge walkthrough and technical record

- [3–5 minute demonstration](docs/judge-demo.md)
- [Architecture](docs/architecture.md) · [Scientific methodology](docs/scientific-methodology.md)
- [NASA sources and acknowledgments](docs/nasa-data-sources.md)
- [Evidence integrity](docs/evidence-integrity.md) · [Limitations](docs/limitations.md)
- [Deployment preparation](docs/deployment.md) · [Release status](docs/release-status.md)
- [Source licensing](LICENSING.md) · [Third-party notices](THIRD_PARTY_NOTICES.md)

## Verification and attribution

Accepted scientific/application baseline: **601 tests passed**. The release status
records new checks and their scope. Actual-data tests are distinct from labelled
synthetic fixtures; missing-runtime skips do not establish scientific acceptance.

Acknowledgments: NASA–ISRO NISAR, NASA ASF DAAC, Earthdata CMR/Harmony and the
cited open-source projects. The project owner supplied direction and reported
manual desktop review. OpenAI Codex assisted code, tests, documentation and UI
refinement. No mission imagery is bundled. No independent calibration, peer
review, detection accuracy or award is claimed.
