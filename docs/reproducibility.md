# Reproducibility

Use Python **3.11** and the pinned runtime in `requirements-ui.txt`. No provider
credentials are needed for the prepared pilot. Installing dependencies requires
internet or an independently verified wheel cache. Running the pilot afterward
is offline; explicit NASA CMR searches require internet.

## Install and start

Windows commands are in [README](../README.md). On Linux/macOS with Python 3.11:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install setuptools==65.5.0 wheel==0.45.1
.venv/bin/python -m pip install -r requirements-ui.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps .
export SENTINEL_ZERO_DEMO_ARCHIVE=/operator/approved-data/scientific_data.zip
.venv/bin/python -m streamlit run public_app.py --server.port 8501
```

These are portable instructions, not Linux certification; see the actual platform
checks in [release status](release-status.md). `public_app.py` invokes the accepted
Earth Atlas after verification. `atlas_app.py` directly uses the existing runtime/
bundle environment for operators; `app.py` is the preserved UIX-01 interface.

## Data identity

The source-only overlay excludes the satellite archive until redistribution is
cleared. Provision the existing archive via an operator environment path, or
place the approved exact file at `demo/scientific_data.zip`. Never put a password,
cookie, callback URL or temporary signed URL in configuration.

| Reference | SHA-256 |
|---|---|
| Scientific ZIP, 14,930,753 bytes | `6153417f06f71485a6cb85f5bc32defdc21e60495a47c2f66fdacda9f1e384cc` |
| Embedded portable receipt | `378f983519bdaa839b34a4df94dd002747273e18ab2dacf563f9f45402a0db35` |
| SZ-005 preparation manifest | `90d4a84868538ac3a8562803f6f6bdfba31369822b248d921023d3791f9a969c` |
| SZ-006 analysis manifest | `0e322f0bc968d6d6b9bc6a6cf9fdb38d9da593327551b792a10319702d948064` |
| Saved change GeoTIFF | `3d44fb5f395f86716ffb4732dd0e0c7ac244cfea1950f6307b9bdff653d2395c` |

The bootstrap verifies a fixed bounded archive and extracts into a separate
temporary cache. Existing loaders check the six source sizes/hashes, native grid,
support and saved product. Invalid/missing evidence blocks display. No scientific
result is silently recomputed or repaired. Local references do not prove NASA
authenticity or calibration.

## Tests

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-report-validation.txt
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m pip check
node --check src/sentinel_zero/presentation/earth_canvas.mjs
git diff --check
```

Node is optional frontend test tooling, not an app dependency. Real-data tests
require the approved runtime plus `SENTINEL_ZERO_SOURCE_ROOT`; the optional
`SENTINEL_ZERO_PREP_RUN` points to the original preparation. The suite labels
synthetic software fixtures separately. Real-data tests may skip without data;
this must be reported rather than interpreted as scientific acceptance. A small
allowlisted set of original public CMR/inspection JSON fixtures is included for
offline regression; bulk local evidence, logs and profiles are excluded.
The published pair-compatibility JSON is explicitly a privacy-redacted view:
four local runtime/QA path references become portable names. Scientific fields
and all source-raster hashes are unchanged. The publication manifest records its
original and redacted file hashes separately; original historical evidence is intact.

For publication preparation only:

```powershell
& .\.venv\Scripts\python.exe tools/prepare_publication.py assemble --repo . --output D:/release/source-v1
& .\.venv\Scripts\python.exe tools/prepare_publication.py verify --output D:/release/source-v1 --sha256 RECORDED_MANIFEST_SHA256
```

The output must be new and outside the checkout. The command does not stage,
commit, push, fetch scientific data or authorize publication. Keep the separately
recorded manifest digest as the review reference.
