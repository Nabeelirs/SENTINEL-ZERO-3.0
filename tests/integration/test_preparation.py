"""SYNTHETIC georeferenced rasters only, except explicitly marked runtime tests."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from sentinel_zero.ingestion.acquisition import AcquisitionError
from sentinel_zero.ingestion.gcov import inspect_bundle
from sentinel_zero.science.eligibility import EligibilityPolicy
from sentinel_zero.science.preparation import (
    READY, load_prepared_pair, main, prepare_pair, read_json, sha256_file, validate_grid,
)


@pytest.fixture
def synthetic_pair(tmp_path):
    source_root = tmp_path / "sources"
    output_root = tmp_path / "prepared"
    source_root.mkdir(); output_root.mkdir()
    inventories, reports = [], []
    for day, gid in ((1, "G1-TEST"), (13, "G2-TEST")):
        producer = f"SYNTHETIC_ONLY_202601{day:02d}T000000_202601{day:02d}T000100_TEST"
        source = dict(collection_id="C1-TEST", granule_id=gid, producer_granule_id=producer,
                      acquisition_start_utc=f"2026-01-{day:02d}T00:00:00Z",
                      acquisition_end_utc=f"2026-01-{day:02d}T00:01:00Z", maturity="PROVISIONAL",
                      processing_crid="SYNTHETIC1", product_version="SYNTHETIC1")
        values = np.array([[1, np.nan, 3], [4, 5, 0]], dtype="float32") if day == 1 else np.array([[2, 3, np.nan], [5, 6, 7]], dtype="float32")
        mask = np.where(np.isfinite(values), 1, 255).astype("uint8")
        if day == 1:
            mask[1, 2] = 0
        artifacts = []
        for layer, data in (("HHHH", values), ("mask", mask), ("numberOfLooks", np.where(np.isfinite(values), 2, np.nan).astype("float32"))):
            name = producer + f"_science_LSAR_GCOV_grids_frequencyA_{layer}_subsetted.tif"
            with rasterio.open(source_root/name, "w", driver="GTiff", width=3, height=2, count=1,
                               dtype=data.dtype, crs="EPSG:32644", transform=Affine(10, 0, 400000, 0, -10, 770000),
                               nodata=255 if layer == "mask" else np.nan) as dst:
                dst.write(data, 1)
                dst.update_tags(1, units="1", long_name="SYNTHETIC " + layer,
                                description="SYNTHETIC subswath mask: partially focused=0, outside=255")
            artifacts.append(dict(layer=layer, filename=name, bytes=(source_root/name).stat().st_size, sha256=sha256_file(source_root/name)))
        inv = dict(schema_version="1.0", source=source, artifacts=artifacts,
                   conservative_prior_attempt_bytes=0, harmony_job_id="SYNTHETIC_" + gid)
        inventories.append(inv); reports.append(inspect_bundle(source_root, inv))
    gate = dict(decision="CONDITIONAL_GO", before_inspection=reports[0], after_inspection=reports[1],
                checks=[dict(check="SYNTHETIC software fixture review", critical=True, decision="PASS")],
                quality_review=dict(visual_review_complete=True, severe_unresolved_artifact=False,
                                    reviewed_granules=["G1-TEST", "G2-TEST"], unresolved_limitations=["SYNTHETIC fixture only"]))
    return source_root, output_root, inventories, gate


def test_roundtrip_native_linear_values_grid_and_portable_manifest(synthetic_pair):
    root, output, inv, gate = synthetic_pair
    original = {a["filename"]: a["sha256"] for i in inv for a in i["artifacts"]}
    run = prepare_pair(root, output, "SYNTHETIC-run", *inv, gate)
    pair = load_prepared_pair(run, root, expected_manifest_sha256=sha256_file(run/"manifest.json"))
    assert pair.common_valid.tolist() == [[True, False, False], [True, True, False]]
    assert pair.primary_exclusion.tolist() == [[0, 1, 1], [0, 0, 3]]
    assert pair.grid["first_pixel_center"] == [400005, 769995]
    assert pair.grid["bounds_native"] == [400000, 769980, 400030, 770000]
    assert pair.before_power[0, 2] == 3 and pair.after_power[1, 2] == 7
    assert np.isnan(pair.before_power[0, 1]) and pair.before_power[1, 2] == 0
    assert pair.before_looks[0, 0] == 2 and not pair.before_power.flags.writeable
    m = read_json(run/"manifest.json")
    assert m["decision"] == READY and not m["configuration"]["temporal_change_computed"]
    assert str(root) not in (run/"manifest.json").read_text()
    assert set(p.name for p in run.iterdir()) == {"common_valid.tif", "primary_exclusion.tif", "manifest.json"}
    assert all(sha256_file(root/n) == h for n, h in original.items())
    for name in ("common_valid.tif", "primary_exclusion.tif"):
        with rasterio.open(run/name) as src:
            assert src.nodata is None and src.read_masks(1).min() == 255
            assert src.xy(1, 2) == (400025, 769985)  # Last centre, no off-by-one.
    # Portable source-root resolution, not a username or absolute path contract.
    moved = root.parent / "relocated_sources"
    root.rename(moved)
    assert load_prepared_pair(run, moved).common_valid.sum() == 3


@pytest.mark.parametrize("mutation", ["granule", "date", "channel", "hash", "maturity", "gate"])
def test_wrong_identity_or_gate_cannot_publish(synthetic_pair, mutation):
    root, output, inv, gate = synthetic_pair
    inv = deepcopy(inv)
    if mutation == "granule": inv[1]["source"]["granule_id"] = "G3-TEST"
    elif mutation == "date": inv[1]["source"]["acquisition_start_utc"] = inv[0]["source"]["acquisition_start_utc"]
    elif mutation == "channel": inv[1]["artifacts"][0]["filename"] = inv[1]["artifacts"][0]["filename"].replace("HHHH", "HVHV")
    elif mutation == "hash": inv[0]["artifacts"][0]["sha256"] = "0"*64
    elif mutation == "maturity": inv[1]["source"]["maturity"] = "BETA"
    else: gate["decision"] = "INSUFFICIENT_EVIDENCE"
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "SYNTHETIC-bad", *inv, gate)
    assert not list(output.iterdir())


@pytest.mark.parametrize("change", ["shift", "empty", "mask", "units", "looks"])
def test_actual_file_mismatches_or_bad_support_block(synthetic_pair, change):
    root, output, inv, gate = synthetic_pair
    for art in inv[1]["artifacts"]:
        with rasterio.open(root/art["filename"], "r+") as dst:
            if change == "shift": dst.transform = Affine(10, 0, 400010, 0, -10, 770000)
            elif change == "empty" and art["layer"] == "HHHH": dst.write(np.zeros((2, 3), dtype="float32"), 1)
            elif change == "mask" and art["layer"] == "mask": dst.write(np.full((2, 3), 7, dtype="uint8"), 1)
            elif change == "units" and art["layer"] == "HHHH": dst.update_tags(1, units="dB")
            elif change == "looks" and art["layer"] == "numberOfLooks": dst.write(np.zeros((2, 3), dtype="float32"), 1)
        art.update(bytes=(root/art["filename"]).stat().st_size, sha256=sha256_file(root/art["filename"]))
    # Synthetic reviewed metadata matches these test files; cross-date/science
    # validation must still catch material problems independently of hashes.
    gate["after_inspection"] = inspect_bundle(root, inv[1])
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "SYNTHETIC-bad", *inv, gate)
    assert not list(output.iterdir())


def test_corrupt_actual_fixture_bytes_rejected(synthetic_pair):
    root, output, inv, gate = synthetic_pair
    (root/inv[0]["artifacts"][0]["filename"]).write_bytes(b"SYNTHETIC corruption")
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "SYNTHETIC-corrupt", *inv, gate)


def test_all_six_receipts_checked_before_loading_any_arrays(synthetic_pair, monkeypatch):
    root, output, inv, gate = synthetic_pair
    (root/inv[1]["artifacts"][2]["filename"]).write_bytes(b"SYNTHETIC sixth-file corruption")
    def forbidden_load(*args, **kwargs):
        pytest.fail("No arrays should load until all six receipt checks pass")
    monkeypatch.setattr("sentinel_zero.science.preparation.load_date", forbidden_load)
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "SYNTHETIC-preflight", *inv, gate)


def test_failure_is_not_published_and_no_source_is_changed(synthetic_pair, monkeypatch):
    root, output, inv, gate = synthetic_pair
    import sentinel_zero.science.preparation as module
    original = module._write_raster
    def fail_second(path, values, grid, kind):
        if kind == "primary_exclusion": raise OSError("SYNTHETIC write failure")
        return original(path, values, grid, kind)
    monkeypatch.setattr(module, "_write_raster", fail_second)
    with pytest.raises(OSError): prepare_pair(root, output, "SYNTHETIC-fail", *inv, gate)
    assert not list(output.iterdir())
    assert all(sha256_file(root/a["filename"]) == a["sha256"] for i in inv for a in i["artifacts"])


def test_no_overwrite_path_escape_and_output_integrity(synthetic_pair):
    root, output, inv, gate = synthetic_pair
    run = prepare_pair(root, output, "SYNTHETIC-run", *inv, gate)
    original = sha256_file(run/"manifest.json")
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "SYNTHETIC-run", *inv, gate)
    with pytest.raises(AcquisitionError): prepare_pair(root, output, "../escape", *inv, gate)
    assert sha256_file(run/"manifest.json") == original
    with pytest.raises(AcquisitionError): load_prepared_pair(run, root, expected_manifest_sha256="0"*64)
    with rasterio.open(run/"common_valid.tif", "r+") as dst:
        dst.write(np.zeros((2, 3), dtype="uint8"), 1)
    with pytest.raises(AcquisitionError): load_prepared_pair(run, root)


def test_grid_tolerance_and_pixel_centre_mismatch(synthetic_pair):
    _, _, _, gate = synthetic_pair
    a = gate["before_inspection"]["rasters"][0]
    b = deepcopy(a); b["transform"][2] += 1e-9
    validate_grid(a, b)
    b["first_pixel_center"][0] += 10
    with pytest.raises(AcquisitionError): validate_grid(a, b)


@pytest.mark.parametrize("field", ["crs_wkt", "width", "height", "bands", "resolution", "bounds_native", "rotation", "orientation"])
def test_strict_grid_contract_rejects_material_metadata(synthetic_pair, field):
    _, _, _, gate = synthetic_pair
    a = gate["before_inspection"]["rasters"][0]
    b = deepcopy(a)
    if field == "crs_wkt": b[field] = "SYNTHETIC_DIFFERENT_CRS"
    elif field in ("width", "height", "bands"): b[field] += 1
    elif field == "rotation": b["transform"][1] = 1
    elif field == "orientation": b["transform"][4] = 10
    else: b[field][0] += 10
    with pytest.raises(AcquisitionError): validate_grid(a, b)


def test_atomic_publish_failure_preserves_foreign_destination(synthetic_pair, monkeypatch):
    root, output, inv, gate = synthetic_pair
    def blocked_publish(stage, final):
        final.mkdir()
        (final/"USER_CONTENT").write_text("PRESERVE SYNTHETIC USER CONTENT")
        raise FileExistsError("SYNTHETIC competing destination")
    monkeypatch.setattr("sentinel_zero.science.preparation.os.rename", blocked_publish)
    with pytest.raises(FileExistsError): prepare_pair(root, output, "SYNTHETIC-race", *inv, gate)
    assert (output/"SYNTHETIC-race"/"USER_CONTENT").read_text() == "PRESERVE SYNTHETIC USER CONTENT"
    assert not list(output.glob("sz005-staging-*")) and not list(output.glob("*.lock"))


@pytest.mark.parametrize("tamper", ["flags", "counts", "legend", "rehashed_pixels"])
def test_rehashed_manifest_cannot_drop_limits_or_misstate_counts(synthetic_pair, tamper):
    root, output, inv, gate = synthetic_pair
    run = prepare_pair(root, output, "SYNTHETIC-run", *inv, gate)
    m = read_json(run/"manifest.json")
    if tamper == "flags": m["quality"]["flags"]["empirical_physical_coregistration"] = "VERIFIED"
    elif tamper == "counts": m["statistics"]["common_eligible"] = 6
    elif tamper == "legend": m["primary_legend"]["1"] = "SYNTHETIC misleading cause"
    else:
        with rasterio.open(run/"common_valid.tif", "r+") as dst:
            dst.write(np.zeros((2, 3), dtype="uint8"), 1)
        m["outputs"]["common_valid"].update(bytes=(run/"common_valid.tif").stat().st_size,
                                            sha256=sha256_file(run/"common_valid.tif"))
    (run/"manifest.json").write_text(json.dumps(m))
    with pytest.raises(AcquisitionError): load_prepared_pair(run, root)


def test_cli_prepare_and_verify(synthetic_pair, monkeypatch):
    root, output, inv, gate = synthetic_pair
    for name, value in (("before", inv[0]), ("after", inv[1]), ("gate", gate)):
        (output/(name+".json")).write_text(json.dumps(value))
    monkeypatch.setenv("SENTINEL_ZERO_SOURCE_ROOT", str(root))
    monkeypatch.setenv("SENTINEL_ZERO_PREP_ROOT", str(output))
    assert main(["prepare", "--run-id", "SYNTHETIC-cli", "--before-inventory", str(output/"before.json"),
                 "--after-inventory", str(output/"after.json"), "--pair-assessment", str(output/"gate.json")]) == 0
    assert main(["verify", "--run-dir", str(output/"SYNTHETIC-cli")]) == 0
    assert main(["prepare", "--run-id", "SYNTHETIC-cli", "--before-inventory", str(output/"before.json"),
                 "--after-inventory", str(output/"after.json"), "--pair-assessment", str(output/"gate.json")]) == 2


@pytest.mark.real_data
def test_real_nisar_sources_prepare_and_reread(tmp_path):
    """Actual runtime evidence, never replaced with synthetic fallback data."""
    configured = os.environ.get("SENTINEL_ZERO_SOURCE_ROOT")
    if not configured:
        pytest.skip("Real NISAR runtime absent: set SENTINEL_ZERO_SOURCE_ROOT; synthetic tests remain separate")
    root = Path(configured)
    repo = Path(__file__).resolve().parents[2]
    first = read_json(repo/"docs/evidence/sz004/resume_20261112/inspection_inventory.json")
    second = read_json(repo/"docs/evidence/sz004/second_20261114/inspection_inventory.json")
    if not all((root/a["filename"]).exists() for inv in (first, second) for a in inv["artifacts"]):
        pytest.skip("Configured runtime lacks one or more real SZ-004 rasters; no scientific conclusion from this skip")
    gate = read_json(repo/"docs/evidence/sz004/second_20261114/pair_compatibility.json")
    run = prepare_pair(root, tmp_path, "actual-nisar-test", first, second, gate)
    loaded = load_prepared_pair(run, root)
    assert loaded.common_valid.shape == (553, 553) and loaded.common_valid.sum() == 305137
    m = read_json(run/"manifest.json")
    assert m["statistics"]["common_ineligible"] == 672
    assert m["statistics"]["support_partition"]["before_only_eligible"] == m["statistics"]["support_partition"]["after_only_eligible"] == 0
    assert loaded.grid["transform"] == [10, 0, 384000, 0, -10, 768350]
    assert all(sha256_file(root/a["filename"]) == a["sha256"] for inv in (first, second) for a in inv["artifacts"])
    durable = os.environ.get("SENTINEL_ZERO_PREP_RUN")
    if durable:
        # Independently regenerate actual masks in test scratch, compare the
        # published durable rasters, and reread that published run as a consumer.
        for name in ("common_valid.tif", "primary_exclusion.tif"):
            assert sha256_file(run/name) == sha256_file(Path(durable)/name)
        downstream = load_prepared_pair(Path(durable), root)
        assert np.array_equal(downstream.common_valid, loaded.common_valid)
