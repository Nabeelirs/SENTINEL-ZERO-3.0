"""Catalog-to-inspected-data work orders; operator-only reuse of native science.

Creating a work order never accepts pixels, authenticates or submits a Harmony
job. The execution boundary accepts already acquired, explicitly reviewed local
inventories. It deliberately does not expose arbitrary paths in the visitor UI.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .global_explorer import COLLECTION, GlobalQuery
from ..contracts import BoundingBox,Channel,ObservationDescriptor,ProductReference,jsonable,utc
from ..discovery.selection import assess_pair
from ..errors import MetadataError
from ..ingestion.acquisition import AcquisitionError, DEFAULT_LAYERS, safe_filename, transfer_budget


# These coordinates come from the recorded SZ-009 public CMR queries. They are
# exploratory windows, not authoritative place/basin boundaries or a geocoder.
STUDY_WINDOWS = {
    "Sri Lanka / exploratory pilot window": (79.95, 6.90, 80.00, 6.95),
    "Brazil / exploratory Amazon window": (-60.08, -3.18, -59.98, -3.08),
    "India / exploratory Brahmaputra window": (91.70, 26.10, 91.80, 26.20),
}


def _fingerprint(value: Mapping[str, Any]) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _observation(record: Mapping[str, Any]) -> ObservationDescriptor:
    """Restore versioned typed catalog data before rerunning existing screening."""
    values=dict(record)
    if values.pop("schema_version",None)!="1.0":raise MetadataError("Unsupported observation schema")
    values["channels"]=tuple(Channel(c["frequency"],tuple(c["polarizations"]),c["range_bandwidth_mhz"]) for c in values["channels"])
    values["product_references"]=tuple(ProductReference(**r) for r in values["product_references"])
    values["footprint"]=tuple(tuple(tuple(p) for p in ring) for ring in values["footprint"])
    values["query_aoi"]=BoundingBox(**values["query_aoi"]) if values.get("query_aoi") else None
    return ObservationDescriptor(**values)


def selection_order(result: Mapping[str, Any]) -> tuple[str, ...]:
    """Actual granules ordered by UTC then identifier, without invented dates."""
    observations = result.get("observations", [])
    if len(observations) > 100 or len({o["granule_concept_id"] for o in observations}) != len(observations):
        raise MetadataError("Duplicate or oversized catalog observation selection")
    return tuple(o["granule_concept_id"] for o in sorted(observations, key=lambda o: (utc(o["acquisition_start"]), o["granule_concept_id"])))


def create_work_order(result: Mapping[str, Any], before_id: str, after_id: str) -> dict[str, Any]:
    """Preserve actual metadata/checks and explicit unfulfilled scientific gates."""
    ids = selection_order(result)
    if before_id == after_id or before_id not in ids or after_id not in ids:
        raise MetadataError("Select two different observations returned by this query")
    observations = {o["granule_concept_id"]: o for o in result["observations"]}
    before, after = observations[before_id], observations[after_id]
    if utc(before["acquisition_start"]) >= utc(after["acquisition_start"]):
        raise MetadataError("Before must precede after in UTC")
    if any(o["collection_concept_id"] != COLLECTION for o in (before, after)):
        raise MetadataError("Selected collection is outside the supported GCOV workflow")
    query = GlobalQuery(**result["query"])
    query.validate()
    if result.get("query_boxes") != [dict(west=b.west, south=b.south, east=b.east, north=b.north) for b in query.boxes()]:
        raise MetadataError("Recorded query bounds do not match the requested AOI")
    assessment = next((p for p in result["pair_assessments"] if (p["before_id"], p["after_id"]) == (before_id, after_id)), None)
    if assessment is None:
        raise MetadataError("No recorded compatibility assessment for selected observations")
    recomputed=jsonable(assess_pair(_observation(before),_observation(after),frequency=query.frequency,
        polarization=query.polarization,aoi=query.boxes()[0] if len(query.boxes())==1 else None))
    if assessment!=recomputed:
        raise MetadataError("Recorded compatibility checks disagree with current deterministic screening")
    compatible = assessment["outcome"] == "PRELIMINARY_CANDIDATE"
    native_supported = query.frequency == "A" and query.polarization == "HH" and len(query.boxes()) == 1
    ready = "REQUIRES_AUTHORIZED_ACQUISITION_AND_PRODUCT_REVIEW" if compatible and native_supported else "BLOCKED_METADATA_OR_UNSUPPORTED_CHANNEL"
    source = dict(query=result["query"], query_boxes=result["query_boxes"],
                  retrieved_at_utc=result["retrieved_at_utc"], collection=result["collection"],
                  pagination_complete=result["pagination_complete"], before=before, after=after, assessment=assessment)
    return dict(schema_version="1.0", task="SZ-009B", work_order_id=_fingerprint(source),
                created_at_utc=datetime.now(timezone.utc).isoformat(), source=source,
                catalog_outcome=assessment["outcome"], processing_readiness=ready,
                scientific_acceptance=False, native_processing_supported=native_supported,
                acquisition=dict(service="NASA Harmony", collection_id=COLLECTION,
                    granule_ids=[before_id, after_id], requested_datasets=list(DEFAULT_LAYERS),
                    format="image/tiff", spatial_subset=result["query_boxes"], output_crs=None,
                    reprojection_requested=False, interpolation_requested=False,
                    expected_asset_bytes=None, transfer_permitted=False,
                    maximum_cumulative_scientific_bytes=250_000_000,
                    owner_authentication_required=True, job_submitted=False),
                steps=[
                    dict(stage="Choose area / search CMR", status="RECORDED", evidence="source.query / query_boxes / retrieved_at_utc"),
                    dict(stage="Select pair / validate metadata", status=assessment["outcome"], evidence="source.assessment.checks"),
                    dict(stage="Acquire authorized subsets", status="BLOCKED_AUTHORIZATION_AND_KNOWN_SIZE", evidence="No acquisition is triggered by this work order"),
                    dict(stage="Inspect arrays / scientific quality", status="NOT_STARTED", evidence="Requires exact two-date inventories and explicit quality review"),
                    dict(stage="Prepare common support", status="NOT_STARTED", evidence="Existing science.preparation.prepare_pair after actual-product gate"),
                    dict(stage="Compute / verify change", status="NOT_STARTED", evidence="Existing science.change.analyze and load_change_product"),
                    dict(stage="Display / provenance / report", status="NOT_STARTED", evidence="Requires a separately registered verified regional investigation"),
                ],
                limitations=["Catalog compatibility is preliminary, never pixel-level scientific acceptance.",
                    "Only Frequency A HHHH with one non-antimeridian AOI is currently supported by the reviewed native-grid processing adapter.",
                    "No automatic authentication, remote download, resampling or alternate-region pixel substitution.",
                    "Output asset sizes and genuine per-granule layer availability remain unknown until an authorized Harmony job completes."])


def authorize_known_assets(work_order: Mapping[str, Any], asset_sizes: list[int | None], *, already_downloaded: int) -> int:
    """Budget precondition only; this does not download or authorize a remote job."""
    if work_order["processing_readiness"] != "REQUIRES_AUTHORIZED_ACQUISITION_AND_PRODUCT_REVIEW":
        raise AcquisitionError("PAIR_GATE", "Catalog or requested channel is not suitable for this processing adapter")
    if len(asset_sizes) != 6:
        raise AcquisitionError("LAYER", "Require known sizes for both dates and all three scientific layers")
    return transfer_budget(asset_sizes, already_downloaded=already_downloaded)


@dataclass(frozen=True)
class InspectedInputs:
    """Operator-supplied receipt paths; not accepted from public visitor controls."""
    source_root: Path
    before_inventory: Mapping[str, Any]
    after_inventory: Mapping[str, Any]
    pair_assessment: Mapping[str, Any]
    allowed_subswaths: tuple[int, ...] = (1,)


def process_inspected_pair(work_order: Mapping[str, Any], inspected: InspectedInputs,
                           output_root: Path, run_id: str) -> dict[str, Any]:
    """Run real preparation/change only after metadata, receipts and quality gates.

    Existing science functions independently reread all six files and checksums.
    No source files are rewritten. New immutable subdirectories preserve partial
    successful stages if a later stage fails; they are not silently overwritten.
    """
    from rasterio.warp import transform_bounds
    from ..science.eligibility import EligibilityPolicy
    from ..science.preparation import prepare_pair, sha256_file, verified_inputs
    from ..science.change import analyze, load_change_product

    safe_filename(run_id)
    source = work_order["source"]
    if work_order["work_order_id"] != _fingerprint(source):
        raise AcquisitionError("IDENTITY", "Work-order metadata changed after selection")
    if (work_order["processing_readiness"] != "REQUIRES_AUTHORIZED_ACQUISITION_AND_PRODUCT_REVIEW"
            or work_order["catalog_outcome"] != "PRELIMINARY_CANDIDATE"
            or not work_order["native_processing_supported"]):
        raise AcquisitionError("PAIR_GATE", "Metadata/geometry/channel requires review before native processing")
    query = GlobalQuery(**source["query"])
    query.validate()
    recomputed=jsonable(assess_pair(_observation(source["before"]),_observation(source["after"]),
        frequency=query.frequency,polarization=query.polarization,aoi=query.boxes()[0] if len(query.boxes())==1 else None))
    if recomputed!=source["assessment"] or recomputed["outcome"]!="PRELIMINARY_CANDIDATE":
        raise AcquisitionError("PAIR_GATE","Current metadata screening does not permit this processing adapter")
    if query.frequency != "A" or query.polarization != "HH" or len(query.boxes()) != 1:
        raise AcquisitionError("LAYER", "Adapter requires inspected native Frequency A HHHH and one AOI")
    for inventory, selected in ((inspected.before_inventory, source["before"]), (inspected.after_inventory, source["after"])):
        observed = inventory["source"]
        if (observed["collection_id"] != selected["collection_concept_id"]
                or observed["granule_id"] != selected["granule_concept_id"]
                or observed["producer_granule_id"] != selected["producer_granule_id"]
                or utc(observed["acquisition_start_utc"]) != utc(selected["acquisition_start"])
                or utc(observed["acquisition_end_utc"]) != utc(selected["acquisition_end"])):
            raise AcquisitionError("IDENTITY", "Selected catalog date/granule does not match inspected local inputs")
    # This verifies six hashes, actual raster grids/units/masks, and explicit
    # two-date quality review before publishing any new scientific output.
    _, _, grid = verified_inputs(inspected.source_root,
                                 [dict(inspected.before_inventory), dict(inspected.after_inventory)], inspected.pair_assessment)
    w, s, e, n = transform_bounds(grid["crs_wkt"], "EPSG:4326", *grid["bounds_native"], densify_pts=21)
    aoi = query.boxes()[0]
    if min(e, aoi.east) <= max(w, aoi.west) or min(n, aoi.north) <= max(s, aoi.south):
        raise AcquisitionError("AOI", "Actual inspected rasters do not overlap the selected geographic area")
    if output_root.exists() and not output_root.is_dir():
        raise AcquisitionError("PATH", "Output root must be a directory")
    if not output_root.is_dir():
        raise AcquisitionError("PATH", "Operator must select an existing output directory outside OneDrive")
    # Both names checked before preparation, preventing an avoidable partial run.
    if any((output_root / (run_id + suffix)).exists() for suffix in ("-preparation", "-analysis")):
        raise AcquisitionError("EXISTS", "Regional output run already exists; select a new version")
    prepared = prepare_pair(inspected.source_root, output_root, run_id + "-preparation",
                            inspected.before_inventory, inspected.after_inventory, inspected.pair_assessment,
                            policy=EligibilityPolicy(inspected.allowed_subswaths))
    prep_hash = sha256_file(prepared / "manifest.json")
    analysed = analyze(prepared, inspected.source_root, output_root, run_id + "-analysis", preparation_sha256=prep_hash)
    analysis_hash = sha256_file(analysed / "analysis_manifest.json")
    product = load_change_product(analysed, prepared, inspected.source_root,
                                  preparation_sha256=prep_hash, analysis_sha256=analysis_hash)
    return dict(schema_version="1.0", status="EXPLORATORY_PROCESSING_VERIFIED_CONDITIONAL",
                work_order_id=work_order["work_order_id"], granule_ids=[source[k]["granule_concept_id"] for k in ("before", "after")],
                preparation=dict(directory=prepared.name, manifest_sha256=prep_hash),
                analysis=dict(directory=analysed.name, manifest_sha256=analysis_hash),
                statistics=dict(product.statistics), limitations=list(product.limitations),
                empirical_coregistration="UNVERIFIED", physical_cause="UNVERIFIED")


def main(argv: list[str] | None = None) -> int:
    """Operator CLI: local reviewed inputs only, no authentication/network code."""
    import argparse
    from ..science.preparation import read_json,sha256_file
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("work-order","source-root","before-inventory","after-inventory","pair-assessment","output-root"):
        parser.add_argument("--"+name,type=Path,required=True)
    parser.add_argument("--work-order-sha256",required=True,help="Separately recorded SHA-256 of the selected-pair work order")
    parser.add_argument("--run-id",required=True)
    parser.add_argument("--subswath",type=int,action="append",help="Reviewed fully focused subswath IDs; default 1")
    args=parser.parse_args(argv)
    try:
        if sha256_file(args.work_order) != args.work_order_sha256:
            raise AcquisitionError("CHECKSUM","Work-order bytes differ from the operator reference")
        inputs=InspectedInputs(args.source_root,read_json(args.before_inventory),read_json(args.after_inventory),
                               read_json(args.pair_assessment),tuple(args.subswath or [1]))
        result=process_inspected_pair(read_json(args.work_order),inputs,args.output_root,args.run_id)
        print(json.dumps(result,indent=2,allow_nan=False))
        return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        print("BLOCKED:",getattr(exc,"code","METADATA_OR_IO"))
        return 2


if __name__=="__main__":
    raise SystemExit(main())
