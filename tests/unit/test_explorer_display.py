"""SYNTHETIC software fixture; never scientific or NASA acquisition evidence."""
from datetime import datetime, timezone
from io import BytesIO
import json
import math

import numpy as np
from PIL import Image
import pytest
from rasterio.crs import CRS

from sentinel_zero.application.data import (ExplorerData, RuntimePaths, candidates, configured_paths,
                                           magnitude_count, pixel, power_db, safe_file, summary_json)
from sentinel_zero.application.bundle import _relative_file
from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.science.change import ChangeProduct, statistics
from sentinel_zero.science.preparation import PreparedPair
from sentinel_zero.presentation.display import (CHANGE_COLOURS, CHANGE_LIMITS, EXCLUDED_RGB,
                                               POWER_COLOURS, POWER_LIMITS, comparison, rgb)


@pytest.fixture
def synthetic_explorer():
    valid = np.array([[True, False, True], [True, True, False]])
    before = np.array([[1, np.nan, 1], [2, 1, 0]], dtype="float32")
    after = np.array([[2, 2, 1], [1, 10, 0]], dtype="float32")
    delta = np.array([[3.0102999566, np.nan, 0], [-3.0102999566, 10, np.nan]], dtype="float32")
    grid = dict(epsg=32644, crs_wkt=CRS.from_epsg(32644).to_wkt(), width=3, height=2,
                transform=[10, 0, 384000, 0, -10, 768350], resolution=[10, 10], bounds_native=[384000, 768330, 384030, 768350])
    sources = [dict(collection_id="C1-SYNTHETIC", granule_id=f"G{i}-SYNTHETIC", acquisition_start_utc=date,
                    acquisition_end_utc=date, maturity="PROVISIONAL", processing_crid="SYNTHETIC", product_version="SYNTHETIC")
               for i, date in enumerate(("2026-01-01T00:00:00Z", "2026-01-13T00:00:00Z"), 1)]
    prep = dict(primary_legend={"0": "common_eligible", "1": "raster_native_nodata", "3": "before_power_nonpositive"})
    pair = PreparedPair(before, after, np.ones((2, 3)), np.ones((2, 3)), valid,
                        np.array([[0, 1, 0], [0, 0, 3]], dtype="uint8"), grid, prep, ("SYNTHETIC software test only",))
    provenance = dict(sources=sources, parameters=dict(units="dB"), outputs={}, quality_flags={"provider_qa": "UNVERIFIED"})
    product = ChangeProduct(pair, delta, statistics(delta, valid, grid), {}, provenance, pair.limitations)
    return ExplorerData(product, "SYNTHETIC test only", datetime.now(timezone.utc).isoformat(), 0)


def test_server_configuration_portable_and_no_username_assumptions(tmp_path):
    paths = configured_paths({"SENTINEL_ZERO_RUNTIME_ROOT": str(tmp_path)})
    assert paths.source == tmp_path/"sz004-pilot-20261112"
    portable = configured_paths({"SENTINEL_ZERO_BUNDLE_ROOT": str(tmp_path), "SENTINEL_ZERO_BUNDLE_SHA256": "a"*64})
    assert portable.bundle == tmp_path and portable.source == tmp_path/"sources"
    with pytest.raises(AcquisitionError): configured_paths({})
    with pytest.raises(AcquisitionError): configured_paths({"SENTINEL_ZERO_BUNDLE_ROOT": str(tmp_path)})


def test_display_db_and_sign_are_power_not_amplitude(synthetic_explorer):
    pair = synthetic_explorer.product.prepared
    result = power_db(pair.after_power, pair.common_valid)
    assert result[0, 0] == pytest.approx(10*math.log10(2))
    assert result[1, 1] == 10 and np.isnan(result[~pair.common_valid]).all()
    assert pair.after_power[0, 0] == 2 and not result.flags.writeable


@pytest.mark.parametrize("value", [0, -1, np.nan, np.inf])
def test_invalid_display_power_rejected(synthetic_explorer, value):
    values = synthetic_explorer.product.prepared.before_power.copy()
    values[0, 0] = value
    with pytest.raises(AcquisitionError): power_db(values, synthetic_explorer.product.prepared.common_valid)


def test_exact_north_up_pixel_coordinates_and_exclusion(synthetic_explorer):
    north, south = pixel(synthetic_explorer, 0, 0), pixel(synthetic_explorer, 1, 2)
    assert (north["easting_m"], north["northing_m"]) == (384005, 768345)
    assert (south["easting_m"], south["northing_m"]) == (384025, 768335)
    assert north["latitude"] == pytest.approx(6.9499532556589925, abs=1e-9)
    assert north["longitude"] == pytest.approx(79.94997498035708, abs=1e-9)
    assert north["saved_change_db"] > 0 and not south["eligible"] and south["saved_change_db"] is None
    assert south["primary_exclusion_reason"] == "before_power_nonpositive"


@pytest.mark.parametrize("r,c", [(-1, 0), (0, -1), (2, 0), (0, 3), (True, 1), (0, .5)])
def test_pixel_out_of_grid_or_noninteger_is_rejected(synthetic_explorer, r, c):
    with pytest.raises(AcquisitionError): pixel(synthetic_explorer, r, c)


def test_same_date_scales_native_orientation_mask_and_clipping(synthetic_explorer):
    panels = comparison(synthetic_explorer)
    assert len(panels) == 3 and panels[0].limits == panels[1].limits == POWER_LIMITS
    assert panels[0].colours == panels[1].colours == POWER_COLOURS
    assert panels[2].limits == CHANGE_LIMITS and panels[2].colours == CHANGE_COLOURS
    image = np.asarray(Image.open(BytesIO(panels[2].png)))
    assert image.shape == (2, 3, 3) and tuple(image[0, 1]) == EXCLUDED_RGB and tuple(image[1, 2]) == EXCLUDED_RGB
    assert tuple(image[0, 0]) != tuple(image[1, 0])  # Sign is not inverted/transposed.
    assert panels[2].clipped_cells == 1


def test_rgb_zero_centre_and_unmodified_values():
    values = np.array([[-6., 0, 6, 20]])
    original = values.copy()
    result = rgb(values, np.ones(values.shape, bool), CHANGE_LIMITS, CHANGE_COLOURS)
    assert tuple(result[0, 1]) == (247, 247, 247)
    assert tuple(result[0, 2]) == tuple(result[0, 3]) and np.array_equal(values, original)


def test_threshold_boundary_counts_and_stable_candidates(synthetic_explorer):
    assert magnitude_count(synthetic_explorer, 0) == 4
    assert magnitude_count(synthetic_explorer, 3) == 3
    assert magnitude_count(synthetic_explorer, 10) == 1
    assert [(a["row"], a["column"]) for a in candidates(synthetic_explorer, 3)] == [(1, 1), (0, 0), (1, 0)]
    for bad in (-1, np.nan, np.inf):
        with pytest.raises(AcquisitionError): magnitude_count(synthetic_explorer, bad)


def test_safe_export_retains_limitations_and_actual_software_fixture_identity(synthetic_explorer):
    record = json.loads(summary_json(synthetic_explorer))
    assert record["sources"][0]["granule_id"] == "G1-SYNTHETIC"
    assert record["statistics"]["eligible_cells"] == 4
    assert record["quality_flags"]["provider_qa"] == "UNVERIFIED"
    assert "physical causes unverified" in record["scientific_status"]
    assert "C:/Users/" not in json.dumps(record)


@pytest.mark.parametrize("name", ["../escape.json", "C:/secrets.json", "sources/../../outside", "..\\escape.tif"])
def test_path_escape_is_never_a_bundle_member_or_runtime_filename(tmp_path, name):
    with pytest.raises(AcquisitionError): safe_file(tmp_path, name)
    with pytest.raises(AcquisitionError): _relative_file(tmp_path, name)


def test_report_record_preserves_original_json_schema_and_selected_values(synthetic_explorer):
    from sentinel_zero.reporting.investigation import report_record
    record = report_record(synthetic_explorer, (0, 0), 3)
    assert record["evidence"] == json.loads(summary_json(synthetic_explorer))
    assert record["selected_cell"] == pixel(synthetic_explorer, 0, 0)
    assert record["exploration"] == dict(absolute_threshold_db=3, inclusive_eligible_count=3)
    assert report_record(synthetic_explorer)["selected_cell"] is None
    excluded = report_record(synthetic_explorer, (0, 1))["selected_cell"]
    assert not excluded["eligible"] and excluded["saved_change_db"] is None


@pytest.mark.parametrize("selection,threshold", [((3, 0), 6), ((0, 0), -1), ((0, 0), np.nan)])
def test_report_rejects_invalid_investigation_state(synthetic_explorer, selection, threshold):
    from sentinel_zero.reporting.investigation import report_record
    with pytest.raises(AcquisitionError): report_record(synthetic_explorer, selection, threshold)
