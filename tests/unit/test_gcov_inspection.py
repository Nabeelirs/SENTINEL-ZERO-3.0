"""Synthetic tiny GeoTIFFs for software tests ONLY, never NASA science evidence."""

from copy import deepcopy
from hashlib import sha256
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.ingestion.gcov import inspect_bundle, inspect_raster, main


@pytest.fixture
def bundle(tmp_path):
    source = dict(collection_id="C1-TEST", granule_id="G1-TEST",
                  producer_granule_id="SYNTHETIC_SOFTWARE_TEST_ONLY",
                  acquisition_start_utc="2026-01-01T00:00:00Z", acquisition_end_utc="2026-01-01T00:01:00Z",
                  maturity=None, processing_crid=None, product_version=None)
    artifacts = []
    for layer in ("HHHH", "mask", "numberOfLooks"):
        filename = f"{source['producer_granule_id']}_science_LSAR_GCOV_grids_frequencyA_{layer}_subsetted.tif"
        values = (np.array([[0, 1, 255], [1, 2, 1]], dtype="uint8") if layer == "mask" else
                  np.array([[1, 0, -1], [np.nan, 2, 4]], dtype="float32"))
        with rasterio.open(tmp_path / filename, "w", driver="GTiff", width=3, height=2,
                           count=1, dtype=values.dtype, crs="EPSG:32644", transform=Affine(10, 0, 400000, 0, -10, 770000),
                           nodata=255 if layer == "mask" else np.nan) as dst:
            dst.write(values, 1)
            dst.update_tags(1, units="1", description="SYNTHETIC SOFTWARE TEST ONLY")
        content = (tmp_path / filename).read_bytes()
        artifacts.append(dict(layer=layer, filename=filename, bytes=len(content), sha256=sha256(content).hexdigest()))
    return tmp_path, dict(schema_version="1.0", source=source, harmony_job_id="SYNTHETIC_JOB_ONLY",
                         conservative_prior_attempt_bytes=0, artifacts=artifacts)


def refresh(root, artifact):
    data = (root / artifact["filename"]).read_bytes()
    artifact.update(bytes=len(data), sha256=sha256(data).hexdigest())


def test_real_reads_counts_georeferencing_and_no_pair_acceptance(bundle):
    root, inventory = bundle
    result = inspect_bundle(root, inventory)
    assert result["data_inspected"] and result["all_layer_grids_match"]
    assert not result["pair_accepted_for_analysis"]
    power, mask, looks = result["rasters"]
    assert power["epsg"] == 32644 and power["resolution"] == [10, 10]
    assert power["first_pixel_center"] == [400005, 769995]
    assert power["nodata"] == "NaN" and power["band_tags"]["units"] == "1"
    assert power["pixel_counts"] == dict(total=6, nonfinite_raw=1, driver_invalid=1,
                                         finite_driver_valid=5, nonpositive_finite_driver_valid=2)
    assert (power["finite_driver_valid_min"], power["finite_driver_valid_max"]) == (-1, 4)
    assert mask["raw_mask_value_counts"] == {"0": 1, "1": 3, "2": 1, "255": 1}
    assert looks["driver_unit"] is None  # Unknown GDAL unit is not fabricated.
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("failure", ["missing", "partial", "tampered", "corrupt", "unsupported_layer", "escape"])
def test_rejects_bad_artifacts(bundle, failure):
    root, inv = bundle
    a = inv["artifacts"][0]
    path = root / a["filename"]
    if failure == "missing":
        path.unlink()
    elif failure == "partial":
        path.write_bytes(path.read_bytes()[:20])
    elif failure == "tampered":
        value = bytearray(path.read_bytes())
        value[-1] ^= 1
        path.write_bytes(value)
    elif failure == "corrupt":
        path.write_bytes(b"SYNTHETIC NOT A TIFF")
        refresh(root, a)
    elif failure == "unsupported_layer":
        a["layer"] = "HHHV"
    else:
        a["filename"] = "../outside.tif"
    with pytest.raises(AcquisitionError):
        inspect_raster(root, a, "G1-TEST")


@pytest.mark.parametrize("bad", ["crs", "transform", "complex", "too_large"])
def test_rejects_unsupported_grid_or_quantity(bundle, bad):
    root, inv = bundle
    a = inv["artifacts"][0]
    with rasterio.open(root / a["filename"]) as src:
        profile = src.profile.copy()
        data = src.read(1)
    if bad == "crs":
        profile["crs"] = None
    elif bad == "transform":
        profile["transform"] = Affine(1, 2, 3, 2, 4, 5)  # Singular, not identity.
    elif bad == "complex":
        profile.update(dtype="complex64", nodata=None)
        data = data.astype("complex64")
    else:
        profile.update(width=2001, height=1000, nodata=None)
        data = None  # Header-only large synthetic fixture, no full-scene array.
    with rasterio.open(root / a["filename"], "w", **profile) as dst:
        if data is not None:
            dst.write(data, 1)
    refresh(root, a)
    with pytest.raises(AcquisitionError):
        inspect_raster(root, a, "G1-TEST")


def test_misaligned_mask_is_visible_and_not_accepted(bundle):
    root, inv = bundle
    a = inv["artifacts"][1]
    with rasterio.open(root / a["filename"], "r+") as dst:
        dst.transform = Affine(10, 0, 400010, 0, -10, 770000)
    refresh(root, a)
    result = inspect_bundle(root, inv)
    assert not result["all_layer_grids_match"]
    assert not result["pair_accepted_for_analysis"]


@pytest.mark.parametrize("bad", ["schema", "missing_layer", "duplicate_layer", "identity", "budget", "time"])
def test_invalid_inventory(bundle, bad):
    root, inv = bundle
    inv = deepcopy(inv)
    if bad == "schema":
        inv["schema_version"] = "99"
    elif bad == "missing_layer":
        inv["artifacts"].pop()
    elif bad == "duplicate_layer":
        inv["artifacts"][1] = inv["artifacts"][0]
    elif bad == "identity":
        inv["source"]["producer_granule_id"] = "DIFFERENT_SYNTHETIC_SOURCE"
    elif bad == "budget":
        inv["conservative_prior_attempt_bytes"] = 250_000_000
    else:
        inv["source"]["acquisition_start_utc"] = inv["source"]["acquisition_end_utc"]
    with pytest.raises(AcquisitionError):
        inspect_bundle(root, inv)


def test_inspector_preserves_inputs_and_filters_unsafe_tags(bundle):
    root, inv = bundle
    a = inv["artifacts"][0]
    with rasterio.open(root / a["filename"], "r+") as dst:
        dst.update_tags(1, description="https://example.invalid/?token=SYNTHETIC_NOT_A_SECRET")
    refresh(root, a)
    before = {p.name: p.read_bytes() for p in root.glob("*.tif")}
    result = inspect_bundle(root, inv)
    assert "description" not in result["rasters"][0]["band_tags"]
    assert before == {p.name: p.read_bytes() for p in root.glob("*.tif")}


def test_cli_result_and_no_overwrite(bundle, capsys):
    root, inv = bundle
    path, out = root / "synthetic_inventory.json", root / "inspection.json"
    path.write_text(json.dumps(inv))
    args = ["--runtime-root", str(root), "--inventory", str(path), "--output", str(out)]
    assert main(args) == 0
    assert "pair_accepted=False" in capsys.readouterr().out
    before = out.read_bytes()
    assert main(args) == 2 and out.read_bytes() == before


def test_cli_missing_scientific_metadata_fails_safely(bundle):
    root, inv = bundle
    del inv["source"]["acquisition_start_utc"]
    path = root / "synthetic_inventory.json"
    path.write_text(json.dumps(inv))
    out = root / "absent_output.json"
    assert main(["--runtime-root", str(root), "--inventory", str(path), "--output", str(out)]) == 2
    assert not out.exists()


def test_all_nodata_has_no_invented_numeric_statistics(bundle):
    root, inv = bundle
    a = inv["artifacts"][0]
    with rasterio.open(root / a["filename"], "r+") as dst:
        dst.write(np.full((2, 3), np.nan, dtype="float32"), 1)
    refresh(root, a)
    result = inspect_raster(root, a, "G1-TEST")
    assert result["pixel_counts"]["finite_driver_valid"] == 0
    assert result["finite_driver_valid_min"] is None and result["finite_driver_valid_max"] is None


def test_scale_offset_are_reported_without_silently_transforming_power(bundle):
    root, inv = bundle
    a = inv["artifacts"][0]
    with rasterio.open(root / a["filename"], "r+") as dst:
        dst.scales, dst.offsets = (2.0,), (10.0,)
    refresh(root, a)
    result = inspect_raster(root, a, "G1-TEST")
    assert (result["driver_scale"], result["driver_offset"]) == (2, 10)
    assert result["finite_driver_valid_min"] == -1  # Stored data, not rescaled.


def test_mask_must_be_integer(bundle):
    root, inv = bundle
    a = inv["artifacts"][1]
    with rasterio.open(root / a["filename"], "w", driver="GTiff", count=1, width=3,
                       height=2, dtype="float32", crs="EPSG:32644",
                       transform=Affine(10, 0, 400000, 0, -10, 770000)) as dst:
        dst.write(np.full((2, 3), 1.5, dtype="float32"), 1)
    refresh(root, a)
    with pytest.raises(AcquisitionError, match="integer mask"):
        inspect_raster(root, a, "G1-TEST")
