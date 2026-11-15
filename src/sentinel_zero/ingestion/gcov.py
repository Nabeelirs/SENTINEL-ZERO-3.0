"""Read-only, bounded inspection of a declared Harmony GCOV subset bundle.

This verifies local bytes and describes actual rasters. It neither accepts a
scientific pair nor interprets backscatter change. HDF5 metadata is not implied.
Install the pinned ``inspection`` extra to use this module.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import rasterio
from rasterio.errors import RasterioError
from rasterio.windows import Window
from rasterio.warp import transform_bounds

from ..contracts import identifier, utc
from ..errors import MetadataError
from .acquisition import AcquisitionError, safe_filename, transfer_budget, verify_local_transfer

LAYERS = ("HHHH", "mask", "numberOfLooks")
MAX_PIXELS = 2_000_000  # Explicit small-pilot scope, not a full-scene scanner.
TAG_KEYS = {"description", "long_name", "units", "valid_min", "valid_max",
            "_FillValue", "grid_mapping", "AREA_OR_POINT", "title", "mission_name",
            "Conventions", "institution", "reference_document"}


def _number(value: float | None) -> float | str | None:
    if value is None or isfinite(float(value)):
        return value
    return "NaN" if np.isnan(value) else str(value)


def _tags(values: Mapping[str, str]) -> dict[str, str]:
    # Export scientific attributes only, never arbitrary URLs or authentication.
    return {key: value[:4096] for key, value in values.items()
            if key in TAG_KEYS and not any(token in value.lower()
                for token in ("https://", "http://", "token=", "signature=", "bearer "))}


def inspect_raster(root: Path, artifact: Mapping[str, Any], granule_id: str) -> dict[str, Any]:
    """Inspect all pixels through <=512x512 windows; never alter input bytes."""
    layer = artifact["layer"]
    if layer not in LAYERS:
        raise AcquisitionError("LAYER", "Unsupported pilot science layer")
    receipt = verify_local_transfer(root, artifact["filename"], expected_size=artifact["bytes"],
                                    expected_sha256=artifact["sha256"], source_granule_id=granule_id)
    target = root / artifact["filename"]  # Path containment checked by verifier.
    try:
        with rasterio.Env(GDAL_CACHEMAX=8 * 1024 * 1024), rasterio.open(target) as src:
            transform = src.transform
            if src.driver != "GTiff" or src.count != 1 or src.crs is None:
                raise AcquisitionError("GRID", "Expected a single-band georeferenced GeoTIFF")
            if (not all(isfinite(v) for v in tuple(transform)) or transform.is_identity
                    or abs(transform.a * transform.e - transform.b * transform.d) == 0):
                raise AcquisitionError("GRID", "Missing, invalid or singular raster transform")
            if src.width * src.height > MAX_PIXELS:
                raise AcquisitionError("LIMIT", "Raster exceeds the bounded scientific-pilot pixel limit")
            dtype = np.dtype(src.dtypes[0])
            if dtype.kind not in "uif" or (layer == "mask" and dtype.kind not in "ui"):
                raise AcquisitionError("RADIOMETRY", "Expected real numeric power/looks or an integer mask")
            count = nonfinite = driver_invalid = usable = nonpositive = 0
            minimum = maximum = None
            values: Counter[str] = Counter()
            for row in range(0, src.height, 512):
                for col in range(0, src.width, 512):
                    window = Window(col, row, min(512, src.width - col), min(512, src.height - row))
                    data = src.read(1, window=window)
                    valid = src.read_masks(1, window=window) > 0
                    finite = np.isfinite(data)
                    selected = data[valid & finite]
                    count += int(data.size)
                    nonfinite += int((~finite).sum())
                    driver_invalid += int((~valid).sum())
                    usable += int(selected.size)
                    nonpositive += int((selected <= 0).sum())
                    if selected.size:
                        lo, hi = float(selected.min()), float(selected.max())
                        minimum = lo if minimum is None else min(minimum, lo)
                        maximum = hi if maximum is None else max(maximum, hi)
                    if layer == "mask":
                        unique, counts = np.unique(data, return_counts=True)
                        values.update({str(int(v)): int(n) for v, n in zip(unique, counts)})
            report = {**receipt, "layer": layer, "driver": src.driver, "width": src.width,
                      "height": src.height, "bands": src.count, "dtype": src.dtypes[0],
                      "crs_wkt": src.crs.to_wkt(), "epsg": src.crs.to_epsg(),
                      "transform": list(transform)[:6], "resolution": list(src.res),
                      "bounds_native": list(src.bounds),
                      "bounds_wgs84": list(transform_bounds(src.crs, "EPSG:4326", *src.bounds)),
                      "first_pixel_center": list(src.xy(0, 0)), "nodata": _number(src.nodata),
                      "driver_unit": src.units[0], "driver_scale": src.scales[0],
                      "driver_offset": src.offsets[0], "dataset_tags": _tags(src.tags()),
                      "band_tags": _tags(src.tags(1)), "image_structure": src.tags(ns="IMAGE_STRUCTURE"),
                      "driver_mask_flags": [v.name for v in src.mask_flag_enums[0]],
                      "pixel_counts": {"total": count, "nonfinite_raw": nonfinite,
                          "driver_invalid": driver_invalid, "finite_driver_valid": usable,
                          "nonpositive_finite_driver_valid": nonpositive},
                      "finite_driver_valid_min": minimum, "finite_driver_valid_max": maximum,
                      "raw_mask_value_counts": dict(sorted(values.items())) if layer == "mask" else None,
                      "statistics_scope": "Entire small subset in bounded windows. Counts overlap; no science-mask filtering. Stored values, no scale or dB conversion.",
                      "driver_defaults_caveat": "Scale/offset/unit are GDAL-exposed values; defaults do not prove original HDF5 attributes."}
    except RasterioError:
        raise AcquisitionError("CONTENT", "Raster cannot be opened or read as supported GeoTIFF") from None
    # Detect a concurrent change while metadata/pixels were read.
    verify_local_transfer(root, artifact["filename"], expected_size=artifact["bytes"],
                          expected_sha256=artifact["sha256"], source_granule_id=granule_id)
    return report


def inspect_bundle(root: Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate declared source mapping and inspect exactly three pilot layers."""
    if manifest.get("schema_version") != "1.0":
        raise AcquisitionError("METADATA", "Unsupported inspection inventory schema")
    source = manifest["source"]
    identifier(source["collection_id"], "C")
    identifier(source["granule_id"], "G")
    producer = source["producer_granule_id"]
    safe_filename(producer + ".h5")
    if utc(source["acquisition_start_utc"]) >= utc(source["acquisition_end_utc"]):
        raise AcquisitionError("METADATA", "Invalid declared acquisition interval")
    artifacts = manifest["artifacts"]
    if len(artifacts) != 3 or {a["layer"] for a in artifacts} != set(LAYERS):
        raise AcquisitionError("LAYER", "Require exactly HHHH, mask and numberOfLooks")
    transfer_budget([a["bytes"] for a in artifacts], already_downloaded=manifest["conservative_prior_attempt_bytes"])
    for a in artifacts:
        expected = f"{producer}_science_LSAR_GCOV_grids_frequencyA_{a['layer']}_subsetted.tif"
        if a["filename"] != expected:
            raise AcquisitionError("IDENTITY", "Output filename disagrees with declared source/layer")
    results = [inspect_raster(root, a, source["granule_id"]) for a in artifacts]
    grid_keys = ("crs_wkt", "transform", "width", "height")
    grids_match = all(all(v[key] == results[0][key] for key in grid_keys) for v in results[1:])
    return {"schema_version": "1.0", "task": "SZ-004", "inspected_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_context": {k: source[k] for k in ("collection_id", "granule_id", "producer_granule_id",
                "acquisition_start_utc", "acquisition_end_utc", "maturity", "processing_crid", "product_version")},
            "source_context_basis": "Preserved catalog and authenticated Harmony job mapping; original HDF5 identification not inspected",
            "harmony_job_id": manifest["harmony_job_id"], "runtime_root": str(root),
            "data_inspected": True, "all_layer_grids_match": grids_match, "rasters": results,
            "pair_accepted_for_analysis": False, "scientific_conclusion": "INSUFFICIENT_EVIDENCE",
            "limitations": ["Only one acquisition inspected", "Provider QA and full identification metadata not inspected",
                "No original-versus-subset numerical comparison or registration validation",
                "Mask description is recorded, not applied as a flood/RFI classification",
                "Provisional RFI, incomplete-input and joint-mode banding assessment still required",
                "Local SHA-256 confirms receipt consistency, not NASA authenticity or scientific correctness"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only inspection of a declared three-layer Harmony GCOV pilot")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.suffix.lower() != ".json":
            raise AcquisitionError("EXISTS", "Output must be a new JSON evidence file")
        if args.inventory.stat().st_size > 1024 * 1024:
            raise AcquisitionError("LIMIT", "Inspection inventory exceeds 1 MiB")
        report = inspect_bundle(args.runtime_root, json.loads(args.inventory.read_text(encoding="utf-8-sig")))
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(report, indent=2, allow_nan=False) + "\n")
        print(f"DATA_INSPECTED: {len(report['rasters'])} layers; grids_match={report['all_layer_grids_match']}; pair_accepted=False")
        return 0  # Inspection success, never a scientific-pair GO.
    except (AcquisitionError, MetadataError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Inspection failed safely: {getattr(exc, 'code', 'METADATA_OR_IO')}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
