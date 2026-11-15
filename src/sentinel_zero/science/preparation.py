"""SZ-005 native-grid preparation and verified SZ-006 loading contract."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any, Mapping, Sequence
from uuid import uuid4

import numpy as np
import rasterio
from rasterio.transform import Affine

from ..contracts import utc
from ..errors import MetadataError
from ..ingestion.acquisition import AcquisitionError, new_destination, safe_filename, verify_local_transfer
from ..ingestion.compatibility import AFFINE_ABSOLUTE_TOLERANCE, rectangle_overlap, time_mapping
from ..ingestion.quality import load_date
from .eligibility import EligibilityPolicy, EligibilityResult, PRIMARY_LEGEND, PRIMARY_REASONS, eligibility

SCHEMA_VERSION = "1.0"
IMPLEMENTATION_VERSION = "sz005-1.0"
READY = "READY_FOR_SZ006_CONDITIONAL"
OUTPUTS = {"common_valid": "common_valid.tif", "primary_exclusion": "primary_exclusion.tif"}
GRID_NUMERIC = ("transform", "resolution", "bounds_native", "first_pixel_center")
UNKNOWN_FLAGS = {key: "UNVERIFIED" for key in (
    "provider_qa_iso", "original_hdf5_metadata", "source_subset_numerical_fidelity",
    "empirical_physical_coregistration", "absolute_geolocation", "artifact_absence", "authoritative_basin_boundary")}
LIMITATIONS = [
    "PROVISIONAL products; restricted exploratory use only",
    "Provider QA/ISO and full original HDF5 metadata not inspected",
    "Source-to-Harmony numerical fidelity and internal resampling not independently verified",
    "Matching declared grids do not establish empirical physical co-registration",
    "Residual joint-mode 40 MHz radiometric banding, RFI/filtering and incomplete-input artifacts remain possible",
    "Focusing/subswath mask is not universal QA, radiometric reliability or a flood mask",
    "Exploratory rectangle is not a verified Kelani Basin polygon",
    "Hashes establish consistency with recorded inputs, not NASA authenticity or independent calibration",
]


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 2_000_000:
        raise AcquisitionError("LIMIT", "Preparation metadata exceeds 2 MB")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise AcquisitionError("METADATA", "Expected a JSON object")
    return value


def validate_grid(reference: Mapping[str, Any], observed: Mapping[str, Any]) -> None:
    """A metadata tolerance only, never an alignment correction."""
    if (reference["crs_wkt"] != observed["crs_wkt"] or reference["width"] != observed["width"]
            or reference["height"] != observed["height"] or observed["bands"] != 1):
        raise AcquisitionError("GRID", "CRS, shape or band-count mismatch; no corrective warp allowed")
    for key in GRID_NUMERIC:
        if not np.allclose(reference[key], observed[key], rtol=0, atol=AFFINE_ABSOLUTE_TOLERANCE):
            raise AcquisitionError("GRID", "Material grid/centre/bounds mismatch; preparation BLOCKED")
    a, b, _, d, e, _ = observed["transform"]
    if a <= 0 or e >= 0 or abs(b) > AFFINE_ABSOLUTE_TOLERANCE or abs(d) > AFFINE_ABSOLUTE_TOLERANCE:
        raise AcquisitionError("GRID", "Only unrotated eastward-column/southward-row native grids supported")
    if rectangle_overlap(reference["bounds_native"], observed["bounds_native"]) <= 0:
        raise AcquisitionError("GRID", "No common spatial overlap")


def _scientific_metadata(raster: Mapping[str, Any]) -> None:
    layer = raster["layer"]
    tags = raster["band_tags"]
    expected_dtype = "uint8" if layer == "mask" else "float32"
    if raster["dtype"] != expected_dtype:
        raise AcquisitionError("RADIOMETRY", "Unsupported dtype for inspected pilot contract")
    if raster["driver_scale"] != 1 or raster["driver_offset"] != 0:
        raise AcquisitionError("RADIOMETRY", "Unexpected scale/offset; no silent normalization")
    if layer == "mask":
        description = tags.get("description", "")
        if not all(term in description for term in ("subswath", "partially focused", "255")) or raster["nodata"] != 255:
            raise AcquisitionError("MASK_METADATA", "Mask semantics/fill require explicit review")
    elif (tags.get("units") != "1" or raster["nodata"] != "NaN"
          or (layer == "HHHH" and "HHHH" not in tags.get("long_name", ""))):
        raise AcquisitionError("RADIOMETRY", "Linear HHHH/looks identity, units or fill not confirmed")
    if raster["dataset_tags"].get("AREA_OR_POINT") != "Area":
        raise AcquisitionError("GRID", "Expected inspected pixel-area convention")


def verified_inputs(source_root: Path, inventories: Sequence[dict[str, Any]], gate: Mapping[str, Any]
                    ) -> tuple[list[dict[str, Any]], list[dict[str, np.ma.MaskedArray]], dict[str, Any]]:
    if len(inventories) != 2 or gate.get("decision") not in ("CONDITIONAL_GO", "ACCEPTED_FOR_ANALYSIS"):
        raise AcquisitionError("PAIR_GATE", "Two explicitly reviewed observations required")
    review = gate.get("quality_review") or {}
    if (not gate.get("checks") or any(c["decision"] != "PASS" for c in gate["checks"] if c["critical"])
            or review.get("visual_review_complete") is not True or review.get("severe_unresolved_artifact") is not False
            or set(review.get("reviewed_granules", [])) != {inv["source"]["granule_id"] for inv in inventories}):
        raise AcquisitionError("PAIR_GATE", "Explicit two-date review and passing critical checks required")
    baselines = []
    for label, inv in zip(("before", "after"), inventories):
        expected = gate[label + "_inspection"]
        if inv["source"] != expected["source_context"] or not time_mapping(inv["source"]):
            raise AcquisitionError("IDENTITY", "Date/granule/source context disagrees with reviewed pair")
        if inv["harmony_job_id"] != expected["harmony_job_id"]:
            raise AcquisitionError("IDENTITY", "Source job identity mismatch")
        baseline = {r["layer"]: r for r in expected["rasters"]}
        if len(inv["artifacts"]) != 3 or {a["layer"] for a in inv["artifacts"]} != {"HHHH", "mask", "numberOfLooks"}:
            raise AcquisitionError("IDENTITY", "Require exactly the six reviewed date/layer inputs")
        for art in inv["artifacts"]:
            if any(art[k] != baseline[art["layer"]][k] for k in ("filename", "bytes", "sha256")):
                raise AcquisitionError("IDENTITY", "Source receipt differs from reviewed baseline")
            verify_local_transfer(source_root, art["filename"], expected_size=art["bytes"],
                                  expected_sha256=art["sha256"], source_granule_id=inv["source"]["granule_id"])
        baselines.append(baseline)
    x, y = (inv["source"] for inv in inventories)
    if (x["granule_id"] == y["granule_id"] or x["collection_id"] != y["collection_id"]
            or utc(x["acquisition_end_utc"]) >= utc(y["acquisition_start_utc"])
            or x["maturity"] != y["maturity"] or x["maturity"] not in ("PROVISIONAL", "VALIDATED")
            or any(x[k] != y[k] or not x[k] for k in ("processing_crid", "product_version"))):
        raise AcquisitionError("IDENTITY", "Incompatible temporal, maturity or processing context")
    # Every source identity/receipt passed before either date's arrays are loaded.
    reports, arrays = [], []
    for inv, baseline in zip(inventories, baselines):
        report, values = load_date(source_root, inv)
        for r in report["rasters"]:
            _scientific_metadata(r)
            validate_grid(baseline[r["layer"]], r)
        reports.append(report)
        arrays.append(values)
    reference = reports[0]["rasters"][0]
    for report in reports:
        for r in report["rasters"]:
            validate_grid(reference, r)
    grid = {k: reference[k] for k in ("crs_wkt", "epsg", "width", "height", *GRID_NUMERIC)}
    grid.update(pixel_convention="Area", metadata_absolute_tolerance=AFFINE_ABSOLUTE_TOLERANCE,
                metadata_relative_tolerance=0, reprojection=False, resampling=False, cropping=False,
                declared_grids_match=True, empirical_coregistration="UNVERIFIED")
    return reports, arrays, grid


def _require_support(result: EligibilityResult) -> None:
    if any(result.statistics["unexpected_mask_values"].values()):
        raise AcquisitionError("MASK_UNVERIFIED", "Undeclared mask IDs; review subswath policy before publication")
    if result.statistics["common_eligible"] == 0:
        raise AcquisitionError("EMPTY_SUPPORT", "No common eligible support; preparation BLOCKED")


def _compact_gate(gate: Mapping[str, Any]) -> dict[str, Any]:
    return {"decision": gate["decision"],
        "checks": [{k: c[k] for k in ("check", "decision", "critical")} for c in gate["checks"]],
        "quality_review": {k: gate["quality_review"][k] for k in
                           ("visual_review_complete", "severe_unresolved_artifact", "reviewed_granules")},
        **{label + "_inspection": {
        "source_context": gate[label + "_inspection"]["source_context"],
        "harmony_job_id": gate[label + "_inspection"]["harmony_job_id"],
        "rasters": [{k: r[k] for k in ("layer", "filename", "bytes", "sha256", "crs_wkt", "width", "height", "bands", *GRID_NUMERIC)}
                    for r in gate[label + "_inspection"]["rasters"]]}
        for label in ("before", "after")}}


def _write_raster(path: Path, values: np.ndarray, grid: Mapping[str, Any], kind: str) -> None:
    with rasterio.open(path, "w", driver="GTiff", width=grid["width"], height=grid["height"],
                       count=1, dtype="uint8", crs=grid["crs_wkt"], transform=Affine(*grid["transform"]),
                       nodata=None, compress="DEFLATE", predictor=1) as dst:
        dst.write(values.astype("uint8"), 1)
        dst.update_tags(task="SZ-005", schema_version=SCHEMA_VERSION, support_only="true", AREA_OR_POINT="Area")
        legend = {0: "excluded", 1: "eligible"} if kind == "common_valid" else PRIMARY_LEGEND
        dst.update_tags(1, long_name=kind, legend=json.dumps(legend, sort_keys=True),
                        interpretation="Support eligibility only; not radar change or flood extent")


@dataclass(frozen=True)
class PreparedPair:
    """Native arrays (unchanged linear power, including invalid cells) plus support."""
    before_power: np.ndarray
    after_power: np.ndarray
    before_looks: np.ndarray
    after_looks: np.ndarray
    common_valid: np.ndarray
    primary_exclusion: np.ndarray
    grid: Mapping[str, Any]
    provenance: Mapping[str, Any]
    limitations: tuple[str, ...]


def load_prepared_pair(run_dir: Path, source_root: Path, *, expected_manifest_sha256: str | None = None) -> PreparedPair:
    """Reread/hash sources and outputs, then independently recompute all support."""
    manifest_path = run_dir / "manifest.json"
    m = read_json(manifest_path)
    parent_id = m["source_inventories"][0]["source"]["granule_id"]
    # Existing path validator also rejects reparse traversal and OneDrive roots.
    verify_local_transfer(run_dir, "manifest.json", expected_size=manifest_path.stat().st_size,
                          expected_sha256=expected_manifest_sha256 or sha256_file(manifest_path),
                          source_granule_id=parent_id)
    if m.get("schema_version") != SCHEMA_VERSION or m.get("task") != "SZ-005" or m.get("decision") != READY:
        raise AcquisitionError("MANIFEST", "Unsupported/incomplete preparation contract")
    policy = EligibilityPolicy(tuple(m["configuration"]["allowed_subswaths"]))
    cfg = m["configuration"]
    if (cfg["transformations"] or cfg["filters"] or cfg["temporal_change_computed"] is not False
            or cfg["native_linear_power"] is not True or cfg["positive_power"] is not True
            or cfg["positive_looks"] is not True or cfg["primary_precedence"] != list(PRIMARY_REASONS)
            or m["quality"]["flags"] != UNKNOWN_FLAGS or not set(LIMITATIONS).issubset(m["quality"]["limitations"])):
        raise AcquisitionError("MANIFEST", "Unsupported policy or missing conditional limitations")
    reports, arrays, grid = verified_inputs(source_root, m["source_inventories"], m["reviewed_pair_gate"])
    result = eligibility(*arrays, policy)
    _require_support(result)
    if m["statistics"] != result.statistics or m["grid"] != grid or m["primary_legend"] != {str(k): v for k, v in PRIMARY_LEGEND.items()}:
        raise AcquisitionError("MANIFEST", "Recorded preparation differs from actual recomputation")
    outputs = m["outputs"]
    if set(outputs) != set(OUTPUTS):
        raise AcquisitionError("OUTPUT", "Require both declared preparation rasters")
    for kind, expected in (("common_valid", result.common_valid), ("primary_exclusion", result.primary_exclusion)):
        art = outputs[kind]
        if art["filename"] != OUTPUTS[kind]:
            raise AcquisitionError("PATH", "Unexpected output artifact identity")
        verify_local_transfer(run_dir, art["filename"], expected_size=art["bytes"], expected_sha256=art["sha256"], source_granule_id=parent_id)
        with rasterio.open(run_dir / art["filename"]) as src:
            observed = dict(crs_wkt=src.crs.to_wkt() if src.crs else None, width=src.width, height=src.height,
                            bands=src.count, transform=list(src.transform)[:6], resolution=list(src.res),
                            bounds_native=list(src.bounds), first_pixel_center=list(src.xy(0, 0)))
            validate_grid(grid, observed)
            if src.dtypes != ("uint8",) or src.nodata is not None or not np.array_equal(src.read(1), expected):
                raise AcquisitionError("OUTPUT", "Prepared raster does not match recomputed support")
            legend = {0: "excluded", 1: "eligible"} if kind == "common_valid" else PRIMARY_LEGEND
            if src.tags().get("support_only") != "true" or json.loads(src.tags(1).get("legend", "{}")) != {str(k): v for k, v in legend.items()}:
                raise AcquisitionError("OUTPUT", "Output support legend/interpretation mismatch")
    powers = [np.asarray(a["HHHH"]) for a in arrays]
    looks = [np.asarray(a["numberOfLooks"]) for a in arrays]
    for array in (*powers, *looks):
        array.flags.writeable = False
    return PreparedPair(*powers, *looks, result.common_valid, result.primary_exclusion, grid,
                        m, tuple(m["quality"]["limitations"]))


def prepare_pair(source_root: Path, output_root: Path, run_id: str, before_inventory: Mapping[str, Any],
                 after_inventory: Mapping[str, Any], pair_assessment: Mapping[str, Any], *,
                 policy: EligibilityPolicy = EligibilityPolicy()) -> Path:
    """Build privately, verify, then atomically publish a new immutable run folder."""
    safe_filename(run_id)
    final = new_destination(output_root, run_id)
    reports, arrays, grid = verified_inputs(source_root, [dict(before_inventory), dict(after_inventory)], pair_assessment)
    result = eligibility(*arrays, policy)
    _require_support(result)
    stage = new_destination(output_root, "sz005-staging-" + uuid4().hex)
    lock = new_destination(output_root, run_id + ".lock")
    # Cooperating producers cannot race this run ID. Windows rename refuses an
    # existing destination; an untrusted host is outside the assurance boundary.
    with lock.open("xb"):
        pass
    stage_created = False
    try:
        stage.mkdir()
        stage_created = True
        try:
            artifacts = {}
            for kind, values in (("common_valid", result.common_valid), ("primary_exclusion", result.primary_exclusion)):
                p = stage / OUTPUTS[kind]
                _write_raster(p, values, grid, kind)
                artifacts[kind] = dict(filename=p.name, bytes=p.stat().st_size, sha256=sha256_file(p))
            try:
                checkpoint = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
                                                     text=True, stderr=subprocess.DEVNULL).strip()
            except (OSError, subprocess.CalledProcessError):
                checkpoint = None
            package_root = Path(__file__).resolve().parents[1]
            modules = [Path(__file__), Path(__file__).with_name("eligibility.py"), package_root / "contracts.py",
                       *(package_root / "ingestion" / (name + ".py") for name in ("acquisition", "gcov", "quality", "compatibility"))]
            module_hashes = {p.relative_to(package_root).as_posix(): sha256_file(p) for p in modules}
            inventories = []
            for inv in (before_inventory, after_inventory):
                record = {k: inv[k] for k in ("schema_version", "source", "harmony_job_id", "conservative_prior_attempt_bytes")}
                record["artifacts"] = [{k: a[k] for k in ("layer", "filename", "bytes", "sha256")} for a in inv["artifacts"]]
                inventories.append(record)
            inherited = pair_assessment.get("quality_review", {}).get("unresolved_limitations", [])
            m = dict(schema_version=SCHEMA_VERSION, task="SZ-005", decision=READY, run_id=run_id,
                     created_at_utc=datetime.now(timezone.utc).isoformat(),
                     source_root_configuration=dict(cli="--source-root", environment="SENTINEL_ZERO_SOURCE_ROOT", absolute_paths_stored=False),
                     source_inventories=inventories, reviewed_pair_gate=_compact_gate(pair_assessment),
                     grid=grid, statistics=result.statistics, outputs=artifacts,
                     primary_legend={str(k): v for k, v in PRIMARY_LEGEND.items()},
                     configuration=dict(allowed_subswaths=list(policy.allowed_subswaths), primary_precedence=list(PRIMARY_REASONS),
                                        native_linear_power=True, positive_power=True, positive_looks=True, filters=[],
                                        transformations=[], temporal_change_computed=False,
                                        mask_semantics="Declared fully-focused subswaths; 0 partial/invalid; 255 outside. Not universal QA."),
                     quality=dict(pair_decision=pair_assessment["decision"], flags=UNKNOWN_FLAGS,
                                  limitations=list(dict.fromkeys([*LIMITATIONS, *inherited]))),
                     implementation=dict(version=IMPLEMENTATION_VERSION, git_checkpoint=checkpoint,
                                         source_modules_sha256=module_hashes, python=platform.python_version(),
                                         numpy=np.__version__, rasterio=rasterio.__version__, gdal=rasterio.__gdal_version__,
                                         package=importlib.metadata.version("sentinel-zero")))
            with (stage / "manifest.json").open("x", encoding="utf-8") as stream:
                json.dump(m, stream, indent=2, allow_nan=False)
            load_prepared_pair(stage, source_root, expected_manifest_sha256=sha256_file(stage / "manifest.json"))
            if final.exists():
                raise AcquisitionError("EXISTS", "Run appeared during preparation; no overwrite")
            os.rename(stage, final)
        except BaseException:
            # Only these service-created staged artifacts are removed, never inputs.
            if stage_created and stage.exists():
                for name in (*OUTPUTS.values(), "manifest.json"):
                    p = stage / name
                    if p.exists():
                        p.unlink()
                stage.rmdir()
            raise
    finally:
        lock.unlink(missing_ok=True)
    return final


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    verify = sub.add_parser("verify")
    for command in (prep, verify):
        command.add_argument("--source-root", type=Path, default=os.environ.get("SENTINEL_ZERO_SOURCE_ROOT"))
    prep.add_argument("--output-root", type=Path, default=os.environ.get("SENTINEL_ZERO_PREP_ROOT"))
    for name in ("before-inventory", "after-inventory", "pair-assessment"):
        prep.add_argument("--" + name, type=Path, required=True)
    prep.add_argument("--run-id", required=True)
    prep.add_argument("--subswath", type=int, action="append", help="Explicit documented supported IDs; default: 1")
    verify.add_argument("--run-dir", type=Path, required=True)
    verify.add_argument("--manifest-sha256")
    args = parser.parse_args(argv)
    try:
        if args.source_root is None:
            raise AcquisitionError("CONFIG", "Supply --source-root or SENTINEL_ZERO_SOURCE_ROOT")
        if args.command == "prepare":
            if args.output_root is None:
                raise AcquisitionError("CONFIG", "Supply --output-root or SENTINEL_ZERO_PREP_ROOT")
            result = prepare_pair(args.source_root, args.output_root, args.run_id, read_json(args.before_inventory),
                                  read_json(args.after_inventory), read_json(args.pair_assessment),
                                  policy=EligibilityPolicy(tuple(args.subswath or [1])))
            print(READY, "; manifest SHA-256:", sha256_file(result / "manifest.json"))
        else:
            pair = load_prepared_pair(args.run_dir, args.source_root, expected_manifest_sha256=args.manifest_sha256)
            print(READY, "; common eligible:", int(pair.common_valid.sum()))
        return 0
    except (AcquisitionError, MetadataError, OSError, KeyError, ValueError, TypeError) as exc:
        print("BLOCKED:", getattr(exc, "code", "METADATA_OR_IO"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
