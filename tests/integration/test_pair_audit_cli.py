"""SYNTHETIC two-date GeoTIFF fixtures; no NASA scientific validation."""
import hashlib
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from sentinel_zero.ingestion.pair_audit import main


def fixture_arguments(root, *, shifted=False, review=True):
    observations = []
    for day, granule in ((1, "G1-TEST"), (13, "G2-TEST")):
        producer = f"SYNTHETIC_ONLY_202601{day:02d}T000000_202601{day:02d}T000100_TEST"
        source = dict(collection_id="C1-TEST", granule_id=granule, producer_granule_id=producer,
                      acquisition_start_utc=f"2026-01-{day:02d}T00:00:00Z",
                      acquisition_end_utc=f"2026-01-{day:02d}T00:01:00Z", maturity="PROVISIONAL",
                      product_version="SYNTHETIC1", processing_crid="SYNTHETIC1")
        artifacts = []
        for layer in ("HHHH", "mask", "numberOfLooks"):
            values = np.array([[1, np.nan], [2, 3]], dtype="float32") if day == 1 else np.array([[4, 5], [np.nan, 6]], dtype="float32")
            if layer == "mask":
                values = np.where(np.isfinite(values), 1, 255).astype("uint8")
            name = producer + f"_science_LSAR_GCOV_grids_frequencyA_{layer}_subsetted.tif"
            with rasterio.open(root/name, "w", driver="GTiff", width=2, height=2, count=1,
                               dtype=values.dtype, crs="EPSG:32644",
                               transform=Affine(10, 0, 400010 if shifted and day == 13 else 400000, 0, -10, 770000),
                               nodata=255 if layer == "mask" else np.nan) as dst:
                dst.write(values, 1)
                dst.update_tags(1, units="1", long_name="SYNTHETIC " + layer,
                                description="SYNTHETIC subswath; partially focused=0; outside=255")
            data = (root/name).read_bytes()
            artifacts.append(dict(layer=layer, filename=name, bytes=len(data), sha256=hashlib.sha256(data).hexdigest()))
        inventory = dict(schema_version="1.0", source=source, artifacts=artifacts,
                         conservative_prior_attempt_bytes=0, harmony_job_id="SYNTHETIC")
        (root/f"{day}.json").write_text(json.dumps(inventory))
        observations.append(dict(granule_concept_id=granule, track=1, frame=1, stack_id="SYNTHETIC",
                                 orbit_direction="ASCENDING", look_direction="LEFT",
                                 channels=[dict(frequency="A", polarizations=["HH"], range_bandwidth_mhz=40)]))
    (root/"catalog.json").write_text(json.dumps(dict(discovery=dict(observations=observations))))
    quality = dict(visual_review_complete=True, evidence_refs=["SYNTHETIC_REVIEW"],
                   reviewed_granules=["G1-TEST", "G2-TEST"], severe_unresolved_artifact=False,
                   unresolved_limitations=["SYNTHETIC"])
    (root/"quality.json").write_text(json.dumps(quality))
    args = ["--runtime-root", str(root), "--before-inventory", str(root/"1.json"),
            "--after-inventory", str(root/"13.json"), "--catalog", str(root/"catalog.json"),
            "--output", str(root/"audit.json")]
    if review:
        args += ["--quality-review", str(root/"quality.json")]
    return args


@pytest.mark.parametrize("shifted,review,decision,code", [
    (False, True, "CONDITIONAL_GO", 0),
    (False, False, "INSUFFICIENT_EVIDENCE", 3),
    (True, True, "REJECTED", 3),
])
def test_two_actual_fixture_bundles_no_silent_alignment_or_acceptance(tmp_path, shifted, review, decision, code):
    args = fixture_arguments(tmp_path, shifted=shifted, review=review)
    assert main(args) == code
    result = json.loads((tmp_path/"audit.json").read_text())
    assert result["decision"] == decision and not result["temporal_power_difference_computed"]
    if not shifted:
        assert result["support"]["common_valid_pixels"] == 2
        assert result["support"]["before_only_pixels"] == result["support"]["after_only_pixels"] == 1
    else:
        assert result["support"] is None  # Cannot combine shifted masks by array index.


def test_corrupt_fixture_file_fails_before_output(tmp_path):
    args = fixture_arguments(tmp_path)
    source = next(tmp_path.glob("*HHHH_subsetted.tif"))
    source.write_bytes(b"SYNTHETIC CORRUPT CONTENT")
    assert main(args) == 2 and not (tmp_path/"audit.json").exists()


def test_existing_result_is_never_overwritten(tmp_path):
    args = fixture_arguments(tmp_path)
    (tmp_path/"audit.json").write_text("SYNTHETIC USER CONTENT")
    assert main(args) == 2
    assert (tmp_path/"audit.json").read_text() == "SYNTHETIC USER CONTENT"
