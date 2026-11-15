"""Strict two-date metadata/support audit. No alignment or temporal differences."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import numpy as np

from ..contracts import utc
from .acquisition import AcquisitionError
from .quality import focused_support

AFFINE_ABSOLUTE_TOLERANCE = 1e-8  # map units; rtol=0. No corrective warp.


def time_mapping(source: Mapping[str, Any]) -> bool:
    match = re.search(r"_(\d{8}T\d{6})_(\d{8}T\d{6})_", source["producer_granule_id"])
    return bool(match and utc(source["acquisition_start_utc"]).strftime("%Y%m%dT%H%M%S")==match[1]
                and utc(source["acquisition_end_utc"]).strftime("%Y%m%dT%H%M%S")==match[2])


def rectangle_overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """Axis-aligned overlap in native square units; only compare common CRS."""
    for box in (a,b):
        if len(box)!=4 or not np.isfinite(box).all() or box[0]>=box[2] or box[1]>=box[3]:
            raise AcquisitionError("GRID","Invalid bounds")
    return max(0.0,min(a[2],b[2])-max(a[0],b[0]))*max(0.0,min(a[3],b[3])-max(a[1],b[1]))


def grids_equal(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return (a["crs_wkt"]==b["crs_wkt"] and (a["width"],a["height"])==(b["width"],b["height"])
            and np.allclose(a["transform"],b["transform"],rtol=0,atol=AFFINE_ABSOLUTE_TOLERANCE))


def shared_support(a: Mapping[str, np.ma.MaskedArray], b: Mapping[str, np.ma.MaskedArray],
                   *, allowed_subswaths: tuple[int, ...] = (1,)) -> dict[str, Any]:
    """Compare boolean eligibility only; never compare backscatter magnitudes."""
    first, ac = focused_support(a["HHHH"],a["mask"],a["numberOfLooks"],allowed_subswaths=allowed_subswaths)
    second, bc = focused_support(b["HHHH"],b["mask"],b["numberOfLooks"],allowed_subswaths=allowed_subswaths)
    if first.shape != second.shape:
        raise AcquisitionError("GRID","Shared support requires the same inspected grid")
    return {"before":ac,"after":bc,"common_valid_pixels":int((first & second).sum()),
            "valid_union_pixels":int((first | second).sum()),"before_only_pixels":int((first & ~second).sum()),
            "after_only_pixels":int((second & ~first).sum()),"both_excluded_pixels":int((~first & ~second).sum()),
            "interpretation":"Eligibility/support comparison only. No temporal power difference or change inference."}


def decide(checks: Sequence[Mapping[str, Any]], *, quality_review: Mapping[str, Any] | None, provisional: bool) -> str:
    critical=[c for c in checks if c["critical"]]
    if any(c["decision"]=="FAIL" for c in critical):
        return "REJECTED"
    if any(c["decision"]=="UNKNOWN" for c in critical) or not quality_review:
        return "INSUFFICIENT_EVIDENCE"
    # No accepted result is inferred from files/finite counts alone.
    if quality_review.get("visual_review_complete") is not True or not quality_review.get("evidence_refs"):
        return "INSUFFICIENT_EVIDENCE"
    return "CONDITIONAL_GO" if provisional or quality_review.get("unresolved_limitations") else "ACCEPTED_FOR_ANALYSIS"


def compare(before: Mapping[str, Any], after: Mapping[str, Any], *, profiles: Mapping[str, Any],
            support: Mapping[str, Any] | None, quality_review: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """All PASS/FAIL/UNKNOWN values include their evidence scope and limits."""
    x,y=before["source_context"],after["source_context"]
    a=next(r for r in before["rasters"] if r["layer"]=="HHHH")
    b=next(r for r in after["rasters"] if r["layer"]=="HHHH")
    checks=[]
    def add(name,av,bv,ok,evidence,critical=True):
        checks.append(dict(check=name,before=av,after=bv,decision="UNKNOWN" if ok is None else "PASS" if ok else "FAIL",critical=critical,evidence=evidence))
    def same(name,av,bv,evidence):
        add(name,av,bv,None if av is None or bv is None else av==bv,evidence)
    add("Granule and collection identity",[x["collection_id"],x["granule_id"]],[y["collection_id"],y["granule_id"]],
        x["collection_id"]==y["collection_id"] and x["granule_id"]!=y["granule_id"] and time_mapping(x) and time_mapping(y),
        "Preserved catalog + authenticated source-job/filename mapping. Original HDF5 identification remains separately unverified.")
    add("Acquisition time",x["acquisition_start_utc"],y["acquisition_start_utc"],utc(x["acquisition_end_utc"])<utc(y["acquisition_start_utc"]),"Catalog UTC intervals and Harmony temporal/source mapping; producer timestamps checked independently against source intervals.")
    same("Product maturity",x.get("maturity"),y.get("maturity"),"Catalog collection maturity, not independent site validation")
    if x.get("maturity") not in (None,"PROVISIONAL","VALIDATED") or y.get("maturity") not in (None,"PROVISIONAL","VALIDATED"):
        checks[-1]["decision"]="FAIL"
    same("Processing version/CRID",[x.get("product_version"),x.get("processing_crid")] if all(x.get(k) for k in ("product_version","processing_crid")) else None,
         [y.get("product_version"),y.get("processing_crid")] if all(y.get(k) for k in ("product_version","processing_crid")) else None,"Preserved catalog processing fields / producer CRID; original identification attributes uninspected")
    p,q=profiles.get(x["granule_id"],{}),profiles.get(y["granule_id"],{})
    keys=("track","frame","orbit_direction","look_direction","stack_id")
    av,bv=[p.get(k) for k in keys],[q.get(k) for k in keys]
    add("Orbit/track/frame/look",dict(zip(keys,av)),dict(zip(keys,bv)),None if None in av+bv else av==bv,"Preserved SZ-003 normalized catalog geometry/stack metadata")
    add("Frequency/polarization",a["band_tags"].get("long_name"),b["band_tags"].get("long_name"),
        "HHHH" in a["band_tags"].get("long_name","") and "HHHH" in b["band_tags"].get("long_name","") and "frequencyA_HHHH" in a["filename"] and "frequencyA_HHHH" in b["filename"],
        "Actual band descriptions, source filenames and explicit Frequency A job variables. Bandwidth checked in catalog profile separately.")
    channels=[next((c for c in item.get("channels",[]) if c.get("frequency")=="A"),None) for item in (p,q)]
    add("Catalog frequency/bandwidth/polarization",channels[0],channels[1],
        None if any(c is None or c.get("range_bandwidth_mhz") is None for c in channels) else
            channels[0]["range_bandwidth_mhz"]==channels[1]["range_bandwidth_mhz"] and all("HH" in c.get("polarizations",[]) for c in channels),
        "Preserved actual catalog channels; original radar metadata remains a separately qualified source")
    same("Radiometric quantity/units",[a["band_tags"].get("units"),a["driver_scale"],a["driver_offset"]],
         [b["band_tags"].get("units"),b["driver_scale"],b["driver_offset"]],"Actual units/scales; GCOV guide defines gamma-zero diagonal power; GDAL defaults are not original HDF5 calibration attributes")
    # Equal-but-wrong units must not pass.
    if any(r["band_tags"].get("units")!="1" or r["driver_scale"]!=1 or r["driver_offset"]!=0 for r in (a,b)):
        checks[-1]["decision"]="FAIL"
    same("CRS",a["crs_wkt"],b["crs_wkt"],"Actual GeoTIFF CRS/WKT")
    same("Raster dimensions",[a["width"],a["height"]],[b["width"],b["height"]],"Dimensions checked in addition to full affine, not as evidence of alignment alone")
    add("Affine transform",a["transform"],b["transform"],bool(np.allclose(a["transform"],b["transform"],rtol=0,atol=AFFINE_ABSOLUTE_TOLERANCE)),f"All six coefficients; absolute tolerance {AFFINE_ABSOLUTE_TOLERANCE} map units, rtol=0")
    same("Pixel spacing/orientation/centres",[a["resolution"],a["first_pixel_center"],a["transform"][1],a["transform"][3],a["transform"][4]],
         [b["resolution"],b["first_pixel_center"],b["transform"][1],b["transform"][3],b["transform"][4]],"Measured spacing, axis terms and first centres. Equal affine plus shape fixes all other centres; not an empirical coregistration test.")
    overlap=rectangle_overlap(a["bounds_native"],b["bounds_native"]) if a["crs_wkt"]==b["crs_wkt"] else None
    add("Spatial overlap",a["bounds_native"],b["bounds_native"],None if overlap is None else overlap>0,"Actual native bounds in common CRS; positive rectangle overlap only, not valid-data support")
    support_known = support is not None and not support["before"]["unexpected_mask_values"] and not support["after"]["unexpected_mask_values"]
    add("Valid shared support",support.get("before") if support else None,support.get("after") if support else None,
        support["common_valid_pixels"]>0 if support_known else None,"Pixel-level driver nodata, finite positive power/looks and declared subswath policy; no resampling")
    mask_desc=[next(r for r in v["rasters"] if r["layer"]=="mask")["band_tags"].get("description","") for v in (before,after)]
    add("Mask interpretation",mask_desc[0],mask_desc[1],True if all("partially focused" in d and "255" in d and "subswath" in d for d in mask_desc) else None,"Actual embedded provider descriptions + attributed GCOV reference. Do not call this a generic binary QA mask; exact specification retrieval status disclosed.")
    looks=[next(r for r in v["rasters"] if r["layer"]=="numberOfLooks") for v in (before,after)]
    add("Number of looks",looks[0]["finite_driver_valid_min"],looks[1]["finite_driver_valid_min"],
        all(r["band_tags"].get("units")=="1" and (r["finite_driver_valid_min"] or 0)>0 for r in looks),"Actual looks units and positive finite range; looks need not be equal and are not confidence percentages")
    reviewed=bool(quality_review and quality_review.get("visual_review_complete") is True and quality_review.get("evidence_refs") and set(quality_review.get("reviewed_granules",[]))=={x["granule_id"],y["granule_id"]})
    severe=quality_review.get("severe_unresolved_artifact") if reviewed else None
    add("Known product defects",quality_review.get("before_findings") if reviewed else None,quality_review.get("after_findings") if reviewed else None,
        None if severe is None else not severe,"Explicit two-date single-acquisition visual/provider-quality review; no inference from finite counts alone")
    decision=decide(checks,quality_review=quality_review,provisional=x.get("maturity")=="PROVISIONAL" or y.get("maturity")=="PROVISIONAL")
    return dict(schema_version="1.0",task="SZ-004",decision=decision,checks=checks,support=support,
                overlap_native_square_units=overlap,grids_equal=grids_equal(a,b),
                allowed_next_scope={"ACCEPTED_FOR_ANALYSIS":"SZ-005 only after authorization: common-window preparation, not proof of physical change",
                    "CONDITIONAL_GO":"Authorized SZ-005 restricted exploratory window/mask preparation; preserve quality caveats; no causal or validation claims",
                    "REJECTED":"No SZ-005 with this uncorrected pair; resolve grid/input incompatibility or select another pair",
                    "INSUFFICIENT_EVIDENCE":"Evidence collection/inspection only; no analysis-ready claim"}[decision],
                alignment_action="NONE_APPLIED" if grids_equal(a,b) else "Explicit reviewed common grid/linear-power resampling and categorical-mask handling required; no corrective transformation applied")
