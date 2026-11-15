"""Real saved NISAR evidence in the report; not synthetic acquisition evidence."""
from io import BytesIO
import json
import os

import pytest
from pypdf import PdfReader

from sentinel_zero.application.data import configured_paths, load_explorer, pixel, summary_json
from sentinel_zero.reporting.investigation import investigation_pdf, report_record


@pytest.fixture(scope="module")
def actual_report_data():
    source = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT")
    if not source:
        pytest.skip("Actual source rasters unavailable; set SENTINEL_ZERO_SOURCE_ROOT")
    from pathlib import Path
    return load_explorer(configured_paths({"SENTINEL_ZERO_RUNTIME_ROOT": str(Path(source).parent)}))


@pytest.mark.real_data
@pytest.mark.parametrize("selection", [None, (157, 309), (0, 0)])
def test_real_pdf_structure_content_exact_evidence_and_no_private_paths(actual_report_data, selection):
    data = actual_report_data
    record = report_record(data, selection, 3)
    assert record["evidence"] == json.loads(summary_json(data))
    assert record["exploration"]["inclusive_eligible_count"] == 78058
    content = investigation_pdf(data, selection, 3)
    assert content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF")
    reader = PdfReader(BytesIO(content), strict=True)
    assert len(reader.pages) == 3
    text = "\n".join(p.extract_text() for p in reader.pages)
    compact = "".join(text.split())
    assert sum(len(page.images) for page in reader.pages) == 3
    for value in ("305,137", "672", "+0.338477", "+0.342195", "-26.151365", "+23.936480",
                  "168,905", "136,231", "78,058", "C2854338529-ASF", "G4348875889-ASF", "G4378876686-ASF"):
        assert value in text
    for name in ("preparation_manifest_sha256", "analysis_manifest_sha256"):
        assert record["evidence"][name] in compact
    assert "do not prove NASA authenticity" in text and "physical causes remain unverified" in text
    assert "C:/Users/" not in text and "C:\\Users\\" not in text
    assert "PROVISIONAL" in text
    assert "noNASAendorsementimplied" in compact  # PDF line wrapping is not a missing claim.
    if selection is not None:
        assert record["selected_cell"] == pixel(data, *selection)
        if selection == (157, 309):
            assert "-26.151365280" in text
        else:
            assert "Excluded: raster_native_nodata" in text
    else:
        assert "No inspected cell was included" in text
    # Invariant report bytes for fixed verified input and selection, not a new science run.
    assert content == investigation_pdf(data, selection, 3)
