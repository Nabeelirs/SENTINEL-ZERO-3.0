"""Server-configured access to the existing, receipt-verified SZ-006 product."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
from time import perf_counter
from typing import Mapping

import numpy as np
from rasterio.transform import Affine
from rasterio.warp import transform

from ..ingestion.acquisition import AcquisitionError, safe_filename
from ..science.change import FILES, ChangeProduct, load_change_product
from ..science.preparation import read_json, sha256_file

PREPARATION_SHA256 = "90d4a84868538ac3a8562803f6f6bdfba31369822b248d921023d3791f9a969c"
ANALYSIS_SHA256 = "0e322f0bc968d6d6b9bc6a6cf9fdb38d9da593327551b792a10319702d948064"
CHANGE_SHA256 = "3d44fb5f395f86716ffb4732dd0e0c7ac244cfea1950f6307b9bdff653d2395c"
COLLECTION = "C2854338529-ASF"
GRANULES = ("G4348875889-ASF", "G4378876686-ASF")


@dataclass(frozen=True)
class RuntimePaths:
    source: Path
    preparation: Path
    analysis: Path
    mode: str = "Verified local scientific mode"
    bundle: Path | None = None
    bundle_sha256: str | None = None


@dataclass(frozen=True)
class ExplorerData:
    product: ChangeProduct
    mode: str
    verified_at_utc: str
    verification_seconds: float


def configured_paths(environment: Mapping[str, str] | None = None) -> RuntimePaths:
    """Configuration belongs to the operator, never to unauthenticated visitors."""
    env = os.environ if environment is None else environment
    if env.get("SENTINEL_ZERO_BUNDLE_ROOT"):
        root = Path(env["SENTINEL_ZERO_BUNDLE_ROOT"])
        digest = env.get("SENTINEL_ZERO_BUNDLE_SHA256")
        if digest is None or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise AcquisitionError("CONFIG", "Portable mode requires the separately recorded bundle receipt SHA-256")
        return RuntimePaths(root/"sources", root/"preparation", root/"analysis", "Verified portable scientific mode", root, digest)
    if env.get("SENTINEL_ZERO_RUNTIME_ROOT"):
        root = Path(env["SENTINEL_ZERO_RUNTIME_ROOT"])
    elif env.get("LOCALAPPDATA"):
        root = Path(env["LOCALAPPDATA"])/"SENTINEL-ZERO/runtime"
    else:
        raise AcquisitionError("CONFIG", "Set SENTINEL_ZERO_RUNTIME_ROOT or the portable bundle configuration")
    return RuntimePaths(root/"sz004-pilot-20261112", root/"sz005/kelani-20261006-20261018-v1",
                        root/"sz006/kelani-20261006-20261018-v2")


def safe_file(root: Path, name: str) -> Path:
    """Fixed basenames under explicit local roots; reject links/reparse traversal."""
    safe_filename(name)
    root = root.absolute()
    if os.name == "nt" and len(str(root/name)) >= 260:
        raise AcquisitionError("PATH_LENGTH", "Choose a shorter local runtime root; original scientific filenames must be preserved")
    if any(p.lower() == "onedrive" or p.lower().startswith("onedrive - ") for p in root.parts):
        raise AcquisitionError("PATH", "Scientific runtime must be outside OneDrive")
    for candidate in (root, *root.parents, root/name):
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise AcquisitionError("PATH", "Scientific files cannot traverse links/reparse points")
    if not (root/name).is_file():
        raise AcquisitionError("MISSING_DATA", "Required local scientific file is unavailable")
    return root/name


def _manifest(root: Path, name: str, expected: str) -> dict:
    path = safe_file(root, name)
    if sha256_file(path) != expected:
        raise AcquisitionError("CHECKSUM", "Manifest differs from the recorded acceptance reference")
    return read_json(path)


def watched_files(paths: RuntimePaths) -> list[Path]:
    """Check manifest anchors before using any filenames from their contents."""
    prep = _manifest(paths.preparation, "manifest.json", PREPARATION_SHA256)
    _manifest(paths.analysis, "analysis_manifest.json", ANALYSIS_SHA256)
    files = [safe_file(paths.preparation, name) for name in ("manifest.json", "common_valid.tif", "primary_exclusion.tif")]
    files += [safe_file(paths.analysis, name) for name in ("analysis_manifest.json", *FILES.values())]
    files += [safe_file(paths.source, art["filename"]) for inv in prep["source_inventories"] for art in inv["artifacts"]]
    if paths.bundle is not None:
        _manifest(paths.bundle, "bundle_receipt.json", paths.bundle_sha256 or "")
        files += [safe_file(paths.bundle, "bundle_receipt.json"), safe_file(paths.bundle, "DATA_NOTICE.md")]
    return files


def fingerprint(paths: RuntimePaths) -> str:
    """Cheap cache invalidation: pinned manifest contents + per-file identity/stat.

    Full hashes and computation run on each new key, not every widget rerun.
    This detects ordinary local changes; hostile preservation of all filesystem
    metadata on a compromised host is outside the trust boundary.
    """
    rows = []
    for path in watched_files(paths):
        info = path.stat()
        rows.append([str(path.absolute()), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino])
    return hashlib.sha256(json.dumps([PREPARATION_SHA256, ANALYSIS_SHA256, rows], sort_keys=True).encode()).hexdigest()


def load_explorer(paths: RuntimePaths) -> ExplorerData:
    start = perf_counter()
    before = fingerprint(paths)
    if paths.bundle is not None:
        # Import locally to avoid a bundle/export dependency on UI imports.
        from .bundle import verify_bundle_receipt
        verify_bundle_receipt(paths.bundle, paths.bundle_sha256 or "")
    product = load_change_product(paths.analysis, paths.preparation, paths.source,
                                  preparation_sha256=PREPARATION_SHA256, analysis_sha256=ANALYSIS_SHA256)
    sources = product.provenance["sources"]
    if (tuple(s["granule_id"] for s in sources) != GRANULES or any(s["collection_id"] != COLLECTION for s in sources)
            or product.prepared.grid["epsg"] != 32644 or product.provenance["outputs"]["change"]["sha256"] != CHANGE_SHA256):
        raise AcquisitionError("IDENTITY", "This Explorer supports only the reviewed Kelani NISAR investigation")
    if fingerprint(paths) != before:
        raise AcquisitionError("CHANGED_DURING_READ", "Scientific files changed during verification; retry after resolving the change")
    return ExplorerData(product, paths.mode, datetime.now(timezone.utc).isoformat(), perf_counter()-start)


def power_db(power: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Display conversion only; never calculate temporal change here."""
    if power.shape != valid.shape or valid.dtype != np.bool_ or not np.isfinite(power[valid]).all() or (power[valid] <= 0).any():
        raise AcquisitionError("SUPPORT", "Invalid common-support power for display")
    result = np.full(power.shape, np.nan, dtype="float64")
    result[valid] = 10*np.log10(power[valid].astype("float64"))
    result.flags.writeable = False
    return result


def pixel(data: ExplorerData, row: int, column: int) -> dict:
    pair = data.product.prepared
    if type(row) is not int or type(column) is not int or not (0 <= row < pair.grid["height"] and 0 <= column < pair.grid["width"]):
        raise AcquisitionError("PIXEL", "Pixel indices must be within the native grid")
    x, y = Affine(*pair.grid["transform"]) @ (column+.5, row+.5)
    lon, lat = transform(pair.grid["crs_wkt"], "EPSG:4326", [x], [y])
    def finite(value):
        return float(value) if np.isfinite(value) else None
    def db(value):
        return 10*float(np.log10(float(value))) if np.isfinite(value) and value > 0 else None
    before, after = pair.before_power[row, column], pair.after_power[row, column]
    code = int(pair.primary_exclusion[row, column])
    return dict(row=row, column=column, easting_m=x, northing_m=y, longitude=lon[0], latitude=lat[0],
                eligible=bool(pair.common_valid[row, column]), before_linear_power=finite(before), after_linear_power=finite(after),
                before_db=db(before), after_db=db(after), saved_change_db=finite(data.product.change_db[row, column]),
                before_number_of_looks=finite(pair.before_looks[row, column]), after_number_of_looks=finite(pair.after_looks[row, column]),
                primary_exclusion_code=code, primary_exclusion_reason=pair.provenance["primary_legend"][str(code)],
                exclusion_note="Primary category only; exclusion causes can overlap")


def magnitude_count(data: ExplorerData, threshold_db: float) -> int:
    if not np.isfinite(threshold_db) or not 0 <= threshold_db <= 1000:
        raise AcquisitionError("THRESHOLD", "Require a finite nonnegative descriptive dB magnitude")
    pair = data.product.prepared
    return int((np.abs(data.product.change_db[pair.common_valid].astype("float64")) >= threshold_db).sum())


def candidates(data: ExplorerData, limit: int = 10) -> list[dict]:
    """Stable descending magnitude, ties by native row/column. No classification."""
    if type(limit) is not int or not 1 <= limit <= 20:
        raise AcquisitionError("LIMIT", "Candidate table supports 1–20 cells")
    flat = np.flatnonzero(data.product.prepared.common_valid)
    values = data.product.change_db.flat[flat].astype("float64")
    selected = np.lexsort((flat, -np.abs(values)))[:limit]
    width = data.product.prepared.grid["width"]
    return [dict(row=int(flat[i]//width), column=int(flat[i]%width), change_db=float(values[i])) for i in selected]


def summary_json(data: ExplorerData) -> bytes:
    """Safe read-only export, no account paths, remote asset links or credentials."""
    p = data.product
    return json.dumps(dict(schema_version="1.0", application="SENTINEL ZERO 3.0", mode=data.mode,
                           scientific_status="CONDITIONAL: physical causes unverified", sources=p.provenance["sources"],
                           measurement=p.provenance["parameters"], grid=p.prepared.grid, statistics=dict(p.statistics),
                           preparation_manifest_sha256=PREPARATION_SHA256, analysis_manifest_sha256=ANALYSIS_SHA256,
                           artifact_receipts=p.provenance["outputs"], quality_flags=p.provenance["quality_flags"],
                           limitations=list(p.limitations), verified_at_utc=data.verified_at_utc), indent=2, allow_nan=False).encode()
