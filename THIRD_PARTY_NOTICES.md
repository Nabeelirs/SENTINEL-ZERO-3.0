# Third-party sources and notices

Original application code and its eventual license are separate from these
dependencies. Retain upstream license/copyright notices with any distribution
that requires them. No NASA or external-project endorsement is implied.

| Component | Use | Upstream |
|---|---|---|
| Python 3.11 | Application runtime prerequisite | [Python](https://www.python.org/) |
| Streamlit 1.67.0 | UI, local components, app testing | [Streamlit](https://github.com/streamlit/streamlit) |
| NumPy 2.2.6 | Array calculations | [NumPy](https://github.com/numpy/numpy) |
| Rasterio 1.4.3 | GeoTIFF I/O and coordinates | [Rasterio](https://github.com/rasterio/rasterio) |
| Plotly 6.3.1 | Charts and evidence graph | [Plotly.py](https://github.com/plotly/plotly.py) |
| ReportLab 5.1.0 | Evidence Passport PDF | [ReportLab](https://www.reportlab.com/) |
| pytest 8.3.5 | Test tooling, not app runtime | [pytest](https://github.com/pytest-dev/pytest) |
| pypdf / pypdfium2 | Optional PDF QA, not app runtime | [pypdf](https://github.com/py-pdf/pypdf), [pypdfium2](https://github.com/pypdfium2-team/pypdfium2) |

The exact tested transitive runtime pins are in `requirements-ui.txt`.
[Dependency metadata](docs/submission/third_party_dependencies.json) records
installed versions, license metadata and project references.
[Notice index](docs/submission/third_party_notices.md) links the retained installed
license texts, including Rasterio/GDAL notices. It is not a blanket legal opinion
or a substitute for reviewing the intended distribution's native/transitive rights.

NASA–ISRO NISAR, NASA ASF DAAC and Earthdata CMR/Harmony provide the scientific
data/access provenance described in [NASA sources](docs/nasa-data-sources.md).
Existing observations are real; synthetic data appear only in labelled tests.
OpenAI Codex assisted engineering and drafting as disclosed in README.
