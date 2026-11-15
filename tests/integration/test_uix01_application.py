"""Actual NISAR UIX regression checks; browser screenshots certify layout separately.

These tests use the protected local observations. No synthetic fixture can satisfy
their source-identity or scientific-display assertions.
"""
from io import BytesIO
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from sentinel_zero.application.data import (
    ANALYSIS_SHA256, CHANGE_SHA256, COLLECTION, GRANULES, PREPARATION_SHA256,
    configured_paths, load_explorer, pixel, watched_files,
)
from sentinel_zero.presentation.display import (
    CHANGE_LIMITS, EXCLUDED_RGB, POWER_LIMITS, comparison,
)
from sentinel_zero.presentation.explorer import STAGES
from sentinel_zero.science.preparation import sha256_file

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def actual_uix_data():
    value = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT")
    if not value:
        pytest.skip("Actual six reviewed NISAR subsets required for UIX acceptance")
    paths = configured_paths({"SENTINEL_ZERO_RUNTIME_ROOT": str(Path(value).parent)})
    return paths, load_explorer(paths)


@pytest.fixture
def actual_uix_app(actual_uix_data, tmp_path, monkeypatch):
    paths, data = actual_uix_data
    monkeypatch.setenv("SENTINEL_ZERO_RUNTIME_ROOT", str(paths.source.parent))
    monkeypatch.delenv("SENTINEL_ZERO_BUNDLE_ROOT", raising=False)
    monkeypatch.setenv("SENTINEL_ZERO_LAB_ROOT", str(tmp_path / "isolated-uix-sessions"))
    return AppTest.from_file(ROOT / "app.py", default_timeout=40).run()


@pytest.mark.real_data
def test_uix_actual_landing_preserves_verified_sources_three_views_and_scales(
    actual_uix_data, actual_uix_app,
):
    """UI changes cannot replace measured PNGs or reverse before/after ordering."""
    from sentinel_zero.presentation.theme import UI_BUILD

    paths, data = actual_uix_data
    at = actual_uix_app
    assert not at.exception
    assert at.title[0].value == "SENTINEL ZERO 3.0"
    assert at.radio(key="stage").options == list(STAGES)
    assert any(UI_BUILD in item.value for item in at.markdown)
    assert len(at.get("image")) == 3
    assert [(m.label, m.value) for m in at.metric][:2] == [
        ("Common eligible", "305,137"), ("Excluded", "672"),
    ]
    sources = data.product.provenance["sources"]
    assert tuple(source["granule_id"] for source in sources) == GRANULES
    assert all(source["collection_id"] == COLLECTION for source in sources)
    assert sha256_file(paths.preparation / "manifest.json") == PREPARATION_SHA256
    assert sha256_file(paths.analysis / "analysis_manifest.json") == ANALYSIS_SHA256
    assert sha256_file(paths.analysis / "delta_backscatter_db.tif") == CHANGE_SHA256
    panels = comparison(data)
    assert panels[0].limits == panels[1].limits == POWER_LIMITS
    assert panels[2].limits == CHANGE_LIMITS
    assert panels[2].limits[0] == -panels[2].limits[1]
    for panel in panels:
        image = np.asarray(Image.open(BytesIO(panel.png)))
        assert image.shape == (553, 553, 3)
        assert tuple(image[0, 0]) == EXCLUDED_RGB
        assert np.all(image[~data.product.prepared.common_valid] == EXCLUDED_RGB)
    assert np.count_nonzero(data.product.prepared.common_valid) == 305137


@pytest.mark.real_data
def test_uix_pixel_selection_and_navigation_preserve_current_investigation(
    actual_uix_data, actual_uix_app,
):
    paths, data = actual_uix_data
    source_identities = {path: sha256_file(path) for path in watched_files(paths)}
    at = actual_uix_app
    at.radio(key="stage").set_value("Investigate Science").run()
    at.number_input(key="pixel_row").set_value(0)
    at.number_input(key="pixel_column").set_value(0).run()
    assert not at.exception
    assert any("Excluded from analysis" in item.value for item in at.warning)
    at.number_input(key="pixel_row").set_value(157)
    at.number_input(key="pixel_column").set_value(309).run()
    assert not at.exception
    assert any(metric.label == "Saved backscatter change" and
               metric.value == "-26.151365 dB" for metric in at.metric)
    selected = pixel(data, 157, 309)
    assert selected["saved_change_db"] == pytest.approx(-26.151365280151367)
    assert (selected["easting_m"], selected["northing_m"]) == (387095, 766775)
    for stage in STAGES[2:]:
        at.radio(key="stage").set_value(stage).run()
        assert not at.exception
    assert at.session_state["selected_cell"] == (157, 309)
    assert at.get("download_button")[0].proto.label == "Download Investigation Report (PDF)"
    assert all(sha256_file(path) == digest for path, digest in source_identities.items())


@pytest.mark.real_data
def test_uix_global_workspace_remains_distinct_from_verified_pilot(actual_uix_app):
    """Navigating globally must not show the pilot as a newly selected country."""
    at = actual_uix_app
    next(button for button in at.button if button.label == "Global Earth Explorer").click().run()
    assert not at.exception
    assert at.radio(key="stage").value == "Observe Change"
    assert at.radio(key="observe_workspace").value == "Global NISAR discovery"
    assert any(item.value == "Global Earth Explorer" for item in at.subheader)
    assert not at.get("image")
    assert not at.get("download_button")  # No fabricated discovery or processing output.
    at.radio(key="observe_workspace").set_value("Verified Sri Lanka pilot").run()
    assert not at.exception and len(at.get("image")) == 3


@pytest.mark.real_data
def test_uix_twin_readable_mode_exposes_actual_failure_and_recovery(
    actual_uix_data, actual_uix_app,
):
    """The compact-screen alternative retains real dependencies and failure state."""
    paths, _ = actual_uix_data
    protected_stats = sha256_file(paths.analysis / "change_statistics.json")
    at = actual_uix_app
    at.radio(key="stage").set_value("Challenge Evidence").run()
    next(button for button in at.button if button.label == "Create isolated evidence copy").click().run()
    next(button for button in at.button if button.label == "Modify disposable copy").click().run()
    assert at.session_state["evidence_lab"].verify()["state"] == "MISMATCH"
    at.radio(key="stage").set_value("Trace Consequences").run()
    assert not at.exception and len(at.get("plotly_chart")) == 1
    assert at.selectbox(key="selected_evidence_artifact").value == "analysis:statistics"
    assert at.radio(key="evidence_view").options == ["Dependency graph", "Readable evidence list"]
    at.radio(key="evidence_view").set_value("Readable evidence list").run()
    assert not at.exception and not at.get("plotly_chart")
    text = "\n".join(item.value for item in at.markdown)
    assert 'uix-trust-list failed' in text
    assert "INTEGRITY FAILURE" in text and "analysis:statistics" in text
    assert "analysis:manifest" in text and "report:passport" in text
    assert "REVALIDATE" in text
    at.checkbox(key="evidence_full_record").set_value(True).run()
    assert not at.exception
    text = "\n".join(item.value for item in at.markdown)
    assert "17</b> visible records" in text and "62</b> recorded dependencies" in text
    at.radio(key="stage").set_value("Restore & Report").run()
    assert at.session_state["last_passport_receipt"]["local_integrity"] == "FAILED"
    next(button for button in at.button if button.label == "Restore and Revalidate").click().run()
    assert not at.exception
    assert at.session_state["evidence_lab"].verify()["state"] == "MATCH"
    assert at.session_state["last_passport_receipt"]["local_integrity"] == "CONSISTENT_WITH_LOCAL_REFERENCES"
    assert sha256_file(paths.analysis / "change_statistics.json") == protected_stats


@pytest.mark.real_data
def test_uix_evidence_guide_uses_current_measurements_and_retains_unknowns(actual_uix_data):
    from sentinel_zero.evidence.earthbom import build_earthbom, evaluate
    from sentinel_zero.presentation.components import GUIDE_QUESTIONS, guide_answer

    paths, data = actual_uix_data
    observation = guide_answer(data, GUIDE_QUESTIONS[0], None)
    assert "2026-10-06" in observation and "2026-10-18" in observation
    assert f"{data.product.statistics['median']:+.6f} dB" in observation
    assert "305,137 eligible cells" in observation and "physical cause is unverified" in observation
    limitations = guide_answer(data, GUIDE_QUESTIONS[1], None)
    assert "PROVISIONAL" in limitations and "RFI" in limitations
    assert "co-registration remain unverified" in limitations
    assert "No cell measurement has been selected" in guide_answer(data, GUIDE_QUESTIONS[2], None)
    selected = guide_answer(data, GUIDE_QUESTIONS[2], (157, 309))
    assert "E 387095.0, N 766775.0" in selected and "-26.151365280 dB" in selected
    excluded = guide_answer(data, GUIDE_QUESTIONS[2], (0, 0))
    assert "excluded" in excluded and "No saved change value is supplied" in excluded
    state = evaluate(build_earthbom(data, paths))
    recovery = guide_answer(data, GUIDE_QUESTIONS[3], (157, 309), state)
    assert state["local_integrity"] in recovery
    assert "Neither operation proves NASA authenticity" in recovery
    assert "physical cause remains unverified" in recovery
    with pytest.raises(ValueError, match="Unsupported"):
        guide_answer(data, "Invent a flood classification", None)


@pytest.mark.real_data
def test_uix_guide_actions_use_the_selected_cell_without_external_ai(actual_uix_app):
    at = actual_uix_app
    next(button for button in at.button if button.label == "Why is this conditional?").click().run()
    assert not at.exception
    assert at.session_state["uix_guide_question"] == "Why is this conditional?"
    assert any("Residual radiometric banding and RFI are possible" in item.value for item in at.markdown)
    at.radio(key="stage").set_value("Investigate Science").run()
    at.number_input(key="pixel_row").set_value(157)
    at.number_input(key="pixel_column").set_value(309).run()
    next(button for button in at.button if button.label == "Explain my selected cell").click().run()
    assert not at.exception
    assert at.session_state["uix_guide_question"] == "Explain my selected cell"
    assert any("Its saved change is -26.151365280 dB" in item.value for item in at.markdown)
    assert at.session_state["selected_cell"] == (157, 309)


@pytest.mark.real_data
def test_uix_cell_locator_preserves_native_pixels_and_places_overlay_without_transposition(actual_uix_data):
    from sentinel_zero.presentation.cell_view import cell_locator

    _, data = actual_uix_data
    pair = data.product.prepared
    arrays = (pair.before_power, pair.after_power, pair.before_looks, pair.after_looks,
              pair.common_valid, pair.primary_exclusion, data.product.change_db)
    snapshots = [array.tobytes() for array in arrays]
    native = np.asarray(Image.open(BytesIO(comparison(data)[2].png)))
    row, column = 157, 309
    content = cell_locator(data, row, column)
    assert content.startswith(b"\x89PNG\r\n\x1a\n")
    located = np.asarray(Image.open(BytesIO(content)))
    assert located.shape == native.shape == (553, 553, 3)
    ys, xs = np.indices(native.shape[:2])
    outside_overlay = (np.abs(ys - row) > 14) | (np.abs(xs - column) > 14)
    assert np.array_equal(located[outside_overlay], native[outside_overlay])
    assert np.any(located[~outside_overlay] != native[~outside_overlay])
    # Horizontal/vertical white crosshair arms, interpreted as array[row,column].
    assert tuple(located[row, column - 8]) == (255, 255, 255)
    assert tuple(located[row - 8, column]) == (255, 255, 255)
    assert np.array_equal(located[column, row], native[column, row])
    # The selected cell itself is not painted over by the surrounding locator.
    assert np.array_equal(located[row, column], native[row, column])
    assert tuple(located[0, 0]) == EXCLUDED_RGB
    assert [array.tobytes() for array in arrays] == snapshots


@pytest.mark.real_data
@pytest.mark.parametrize("row,column", [(-1, 0), (0, -1), (553, 0), (0, 553), (True, 1), (1, 0.5)])
def test_uix_cell_locator_rejects_invalid_native_indices(actual_uix_data, row, column):
    from sentinel_zero.ingestion.acquisition import AcquisitionError
    from sentinel_zero.presentation.cell_view import cell_locator

    _, data = actual_uix_data
    with pytest.raises(AcquisitionError, match="Pixel indices"):
        cell_locator(data, row, column)


@pytest.mark.real_data
def test_uix_journey_buttons_update_visible_stage_and_record_visits_not_scientific_acceptance(actual_uix_app):
    at = actual_uix_app
    assert at.session_state["uix_visited"] == ["Observe Change"]
    for index in (1, 2, 3, 4, 0):
        at.button(key=f"journey_{index}").click().run()
        assert not at.exception
        assert at.radio(key="stage").value == STAGES[index]
        assert STAGES[index] in at.session_state["uix_visited"]
    assert at.session_state["uix_visited"] == sorted(STAGES)
    assert any("visits do not indicate scientific acceptance" in item.value for item in at.caption)
