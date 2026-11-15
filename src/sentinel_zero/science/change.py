"""SZ-006 exploratory gamma-zero power change; no environmental classification."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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

from ..errors import MetadataError
from ..ingestion.acquisition import AcquisitionError, new_destination, safe_filename, verify_local_transfer
from .preparation import PreparedPair, load_prepared_pair, read_json, sha256_file, validate_grid
from .change_preview import diagnostics, write_preview

READY = "READY_FOR_SZ007_CONDITIONAL"
VERSION = "sz006-1.0"
FORMULA = "10 * (log10(P_after) - log10(P_before))"
LABEL = "NISAR HHHH Backscatter Change (dB)"
FILES = {"change": "delta_backscatter_db.tif", "statistics": "change_statistics.json",
         "diagnostics": "quality_diagnostics.json", "preview": "comparison.html"}
PARAMETERS = dict(formula=FORMULA, intermediate_dtype="float64", output_dtype="float32",
                  nodata="NaN", units="dB", frequency="A", polarization_term="HHHH",
                  reprojection=False, resampling=False, filtering=[], clipping=False,
                  sign="positive means greater after-date backscatter power")
EXTRA_LIMITATIONS = (
    "Physical causes of radar-response change are unverified; this is not a flood or damage map",
    "Number of looks is retained, not converted to a confidence or uncertainty score",
    "Magnitude thresholds are descriptive only, not validated detection or significance thresholds",
)


def _support(values: np.ndarray, valid: np.ndarray) -> None:
    if (values.ndim != 2 or valid.dtype != np.bool_ or values.shape != valid.shape
            or not valid.any()):
        raise AcquisitionError("SUPPORT", "Require nonempty two-dimensional common boolean support")


def calculate_change(before: np.ndarray, after: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Return float32 dB with NaN elsewhere; never form a potentially overflowing ratio."""
    _support(before, valid)
    if after.shape != before.shape or any(a.dtype.kind not in "fiu" for a in (before, after)):
        raise AcquisitionError("POWER", "Require matching real numeric power arrays")
    b, a = before[valid].astype("float64"), after[valid].astype("float64")
    if not all(np.isfinite(x).all() and (x > 0).all() for x in (b, a)):
        raise AcquisitionError("POWER", "Every eligible input power must be finite and strictly positive")
    delta = 10.0 * (np.log10(a) - np.log10(b))
    stored = delta.astype("float32")
    # Nearest float32 representation: relative machine precision, with the
    # smallest subnormal as an absolute bound near zero. Not a science threshold.
    tolerance = np.maximum(np.abs(delta) * np.finfo("float32").eps, np.nextafter(np.float32(0), np.float32(1)))
    if not np.isfinite(stored).all() or np.any(np.abs(stored.astype("float64") - delta) > tolerance):
        raise AcquisitionError("NUMERIC", "Change cannot be represented faithfully as finite float32")
    result = np.full(before.shape, np.nan, dtype="float32")
    result[valid] = stored
    result.flags.writeable = False
    return result


def statistics(delta: np.ndarray, valid: np.ndarray, grid: Mapping[str, Any]) -> dict[str, Any]:
    """Statistics of stored raster values, float64 accumulation; population std."""
    _support(delta, valid)
    if not np.isfinite(delta[valid]).all() or not np.isnan(delta[~valid]).all():
        raise AcquisitionError("SUPPORT", "Finite values must coincide exactly with common support")
    x = delta[valid].astype("float64")
    quantiles = (.01, .05, .25, .5, .75, .95, .99)
    crs = rasterio.crs.CRS.from_wkt(grid["crs_wkt"]) if "crs_wkt" in grid else rasterio.crs.CRS.from_epsg(grid["epsg"])
    if not crs.is_projected or crs.linear_units != "metre":
        raise AcquisitionError("GRID", "Projected metre CRS required for grid-cell square-metre summaries")
    a, b, _, d, e, _ = grid["transform"]
    area = abs(a * e - b * d)
    return dict(
        basis="saved float32 change raster on SZ-005 common eligibility only",
        units="dB", total_cells=int(valid.size), eligible_cells=int(x.size),
        excluded_cells=int((~valid).sum()), eligible_percent=100.0*x.size/valid.size,
        excluded_percent=100.0*int((~valid).sum())/valid.size,
        minimum=float(x.min()), maximum=float(x.max()), mean=float(x.mean()),
        median=float(np.median(x)), standard_deviation=float(x.std(ddof=0)), std_ddof=0,
        quantiles={str(q): float(v) for q, v in zip(quantiles, np.quantile(x, quantiles, method="linear"))},
        quantile_method="linear", positive_cells=int((x > 0).sum()), negative_cells=int((x < 0).sum()),
        exactly_zero_cells=int((x == 0).sum()), nonfinite_eligible_cells=int((~np.isfinite(x)).sum()),
        descriptive_thresholds=dict(at_least_plus_3_db=int((x >= 3).sum()),
                                    at_most_minus_3_db=int((x <= -3).sum()),
                                    absolute_at_least_6_db=int((np.abs(x) >= 6).sum()),
                                    definition="Inclusive boundaries; illustrative magnitude only, not physical detection/significance"),
        projected_grid_cell_area_m2=area, eligible_projected_grid_area_m2=area*x.size,
        area_definition="Projected raster-cell footprint, not actual terrain area, flood extent or damage")


@dataclass(frozen=True)
class ChangeProduct:
    """Dashboard-facing data; original power/looks remain in the verified pair."""
    prepared: PreparedPair
    change_db: np.ndarray
    statistics: Mapping[str, Any]
    diagnostics: Mapping[str, Any]
    provenance: Mapping[str, Any]
    limitations: tuple[str, ...]


def _json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def _raster(path: Path, delta: np.ndarray, grid: Mapping[str, Any]) -> None:
    with rasterio.open(path, "w", driver="GTiff", width=grid["width"], height=grid["height"],
                       count=1, dtype="float32", crs=grid["crs_wkt"], transform=Affine(*grid["transform"]),
                       nodata=np.nan, compress="DEFLATE") as dst:
        dst.write(delta, 1)
        dst.set_band_description(1, LABEL)
        dst.set_band_unit(1, "dB")
        dst.update_tags(AREA_OR_POINT="Area", interpretation="exploratory radar response; physical cause unverified")
        dst.update_tags(1, units="dB", formula=FORMULA, sign=PARAMETERS["sign"])


def _read_change(path: Path, grid: Mapping[str, Any]) -> np.ndarray:
    with rasterio.open(path) as src:
        observed = dict(crs_wkt=src.crs.to_wkt() if src.crs else None, width=src.width, height=src.height,
                        bands=src.count, transform=list(src.transform)[:6], resolution=list(src.res),
                        bounds_native=list(src.bounds), first_pixel_center=list(src.xy(0, 0)))
        validate_grid(grid, observed)
        if (src.dtypes != ("float32",) or src.nodata is None or not np.isnan(src.nodata)
                or src.units != ("dB",) or src.descriptions != (LABEL,)
                or src.tags(1).get("formula") != FORMULA or src.tags().get("AREA_OR_POINT") != "Area"):
            raise AcquisitionError("OUTPUT", "Change radiometry, nodata or label metadata mismatch")
        result = src.read(1)
        if not np.array_equal(src.read_masks(1) != 0, np.isfinite(result)):
            raise AcquisitionError("OUTPUT", "Raster-native validity disagrees with stored finite values")
    result.flags.writeable = False
    return result


def load_change_product(run_dir: Path, preparation_dir: Path, source_root: Path, *,
                        preparation_sha256: str, analysis_sha256: str) -> ChangeProduct:
    """Reverify sources, upstream support, every output and full calculation before use."""
    safe_filename(run_dir.name)
    m = read_json(run_dir / "analysis_manifest.json")
    parent = m["sources"][0]["granule_id"]
    verify_local_transfer(run_dir, "analysis_manifest.json", expected_size=(run_dir/"analysis_manifest.json").stat().st_size,
                          expected_sha256=analysis_sha256, source_granule_id=parent)
    if (m["schema_version"] != "1.0" or m["task"] != "SZ-006" or m["decision"] != READY
            or m["implementation_version"] != VERSION
            or m["parameters"] != PARAMETERS or m["preparation"]["sha256"] != preparation_sha256
            or set(m["outputs"]) != set(FILES)):
        raise AcquisitionError("MANIFEST", "Unsupported analysis contract or upstream receipt")
    pair = load_prepared_pair(preparation_dir, source_root, expected_manifest_sha256=preparation_sha256)
    sources = [i["source"] for i in pair.provenance["source_inventories"]]
    limitations = tuple(dict.fromkeys([*pair.limitations, *EXTRA_LIMITATIONS]))
    if (m["sources"] != sources or m["grid"] != pair.grid or m["limitations"] != list(limitations)
            or m["input_receipts"] != pair.provenance["source_inventories"]
            or m["preparation"] != dict(run_id=pair.provenance["run_id"], filename="manifest.json",
                                        sha256=preparation_sha256, bytes=(preparation_dir/"manifest.json").stat().st_size)
            or m["quality_flags"] != pair.provenance["quality"]["flags"]):
        raise AcquisitionError("MANIFEST", "Lost or changed source identity, grid or conditional limitations")
    for kind, name in FILES.items():
        art = m["outputs"][kind]
        if art["filename"] != name:
            raise AcquisitionError("PATH", "Unexpected output filename")
        verify_local_transfer(run_dir, name, expected_size=art["bytes"], expected_sha256=art["sha256"], source_granule_id=parent)
    delta = _read_change(run_dir / FILES["change"], pair.grid)
    expected = calculate_change(pair.before_power, pair.after_power, pair.common_valid)
    if not np.array_equal(delta, expected, equal_nan=True):
        raise AcquisitionError("OUTPUT", "Saved change does not equal verified source calculation")
    stats = statistics(delta, pair.common_valid, pair.grid)
    qa = diagnostics(pair, delta)
    if (read_json(run_dir/FILES["statistics"]) != stats or m["statistics"] != stats
            or read_json(run_dir/FILES["diagnostics"]) != qa or m["diagnostics"] != qa):
        raise AcquisitionError("OUTPUT", "Saved statistics/diagnostics differ from raster recomputation")
    return ChangeProduct(pair, delta, stats, qa, m, limitations)


def analyze(preparation_dir: Path, source_root: Path, output_root: Path, run_id: str, *,
            preparation_sha256: str) -> Path:
    """Exclusive, staged publication; no source edits, transformations or network access."""
    safe_filename(run_id)
    final = new_destination(output_root, run_id)
    pair = load_prepared_pair(preparation_dir, source_root, expected_manifest_sha256=preparation_sha256)
    delta = calculate_change(pair.before_power, pair.after_power, pair.common_valid)
    stage = new_destination(output_root, "sz006-staging-" + uuid4().hex)
    lock = new_destination(output_root, run_id + ".lock")
    with lock.open("xb"):
        pass
    created = False
    try:
        stage.mkdir()
        created = True
        _raster(stage/FILES["change"], delta, pair.grid)
        reread = _read_change(stage/FILES["change"], pair.grid)
        if not np.array_equal(reread, delta, equal_nan=True):
            raise AcquisitionError("OUTPUT", "GeoTIFF serialization changed scientific values")
        stats, qa = statistics(reread, pair.common_valid, pair.grid), diagnostics(pair, reread)
        _json(stage/FILES["statistics"], stats)
        _json(stage/FILES["diagnostics"], qa)
        write_preview(stage/FILES["preview"], pair, reread, qa)
        outputs = {kind: dict(filename=name, bytes=(stage/name).stat().st_size, sha256=sha256_file(stage/name))
                   for kind, name in FILES.items()}
        try:
            checkpoint = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
                                                 text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            checkpoint = None
        m = dict(schema_version="1.0", task="SZ-006", implementation_version=VERSION, decision=READY,
                 interpretation="Restricted exploratory radar-response change; physical cause unverified",
                 run_id=run_id, created_at_utc=datetime.now(timezone.utc).isoformat(),
                 sources=[i["source"] for i in pair.provenance["source_inventories"]],
                 preparation=dict(run_id=pair.provenance["run_id"], filename="manifest.json", sha256=preparation_sha256,
                                  bytes=(preparation_dir/"manifest.json").stat().st_size),
                 input_receipts=pair.provenance["source_inventories"], grid=pair.grid, parameters=PARAMETERS,
                 outputs=outputs, statistics=stats, diagnostics=qa,
                 quality_flags=pair.provenance["quality"]["flags"],
                 limitations=list(dict.fromkeys([*pair.limitations, *EXTRA_LIMITATIONS])),
                 software=dict(python=platform.python_version(), numpy=np.__version__, rasterio=rasterio.__version__,
                               gdal=rasterio.__gdal_version__, plotly=importlib.metadata.version("plotly"),
                               package=importlib.metadata.version("sentinel-zero"), git_checkpoint=checkpoint,
                               source_modules_sha256={p.name: sha256_file(p) for p in (Path(__file__), Path(__file__).with_name("change_preview.py"))}),
                 reproduction="python -m sentinel_zero.science.change analyze --source-root SOURCE --preparation-dir PREP --preparation-sha256 SHA --output-root ROOT --run-id NEW_ID")
        _json(stage/"analysis_manifest.json", m)
        load_change_product(stage, preparation_dir, source_root, preparation_sha256=preparation_sha256,
                            analysis_sha256=sha256_file(stage/"analysis_manifest.json"))
        if final.exists():
            raise AcquisitionError("EXISTS", "Output appeared during analysis; refusing overwrite")
        os.rename(stage, final)
    except BaseException:
        if created and stage.exists():
            for name in (*FILES.values(), "analysis_manifest.json"):
                (stage/name).unlink(missing_ok=True)
            stage.rmdir()
        raise
    finally:
        lock.unlink(missing_ok=True)
    return final


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("analyze", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--source-root", type=Path, default=os.environ.get("SENTINEL_ZERO_SOURCE_ROOT"))
        command.add_argument("--preparation-dir", type=Path, required=True)
        command.add_argument("--preparation-sha256", required=True)
        if name == "analyze":
            command.add_argument("--output-root", type=Path, required=True)
            command.add_argument("--run-id", required=True)
        else:
            command.add_argument("--run-dir", type=Path, required=True)
            command.add_argument("--analysis-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.source_root is None:
            raise AcquisitionError("CONFIG", "Supply source root")
        if args.command == "analyze":
            run = analyze(args.preparation_dir, args.source_root, args.output_root, args.run_id,
                          preparation_sha256=args.preparation_sha256)
            print(READY, "; analysis manifest SHA-256:", sha256_file(run/"analysis_manifest.json"))
        else:
            product = load_change_product(args.run_dir, args.preparation_dir, args.source_root,
                                          preparation_sha256=args.preparation_sha256, analysis_sha256=args.analysis_sha256)
            print(READY, "; eligible:", product.statistics["eligible_cells"], "; mean dB:", product.statistics["mean"])
        return 0
    except (AcquisitionError, MetadataError, OSError, KeyError, ValueError, TypeError) as exc:
        print("BLOCKED:", getattr(exc, "code", "METADATA_OR_IO"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
