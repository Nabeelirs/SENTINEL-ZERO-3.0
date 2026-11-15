"""SZ-004 safety/preflight utilities and bounded anonymous QA-sidecar GET.

Public capability metadata and anonymous HEAD probes never establish that an
authenticated scientific file was acquired. Preserve source identity and refuse
unknown or oversized transfers. Raster acquisition uses the separately recorded
authenticated Harmony browser session; this module does not inherit it.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5, sha256
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, build_opener
from uuid import uuid4

from ..contracts import BoundingBox, CollectionDescriptor, ObservationDescriptor, identifier, jsonable
from ..discovery.cmr import NoRedirect, parse_granule
from ..errors import MetadataError

PILOT_LIMIT_BYTES = 250_000_000  # User's total authorization: decimal 250 MB.
SOURCE_HOST = "nisar.asf.earthdatacloud.nasa.gov"
DEFAULT_LAYERS = tuple("/science/LSAR/GCOV/grids/frequencyA/" + name for name in ("HHHH", "mask", "numberOfLooks"))
TARGET_IDS = ("G4348875889-ASF", "G4378876686-ASF")


class AcquisitionError(ValueError):
    def __init__(self, code: str, message: str, *, bytes_received: int = 0, partial_filename: str | None = None):
        super().__init__(message)
        self.code = code
        self.bytes_received = bytes_received
        self.partial_filename = partial_filename


def transfer_budget(sizes: Sequence[int | None], *, already_downloaded: int = 0) -> int:
    """Authorize known total bytes only. Include partial/failed scientific reads.

    This is a precondition, not a persistent byte meter or a network downloader.
    The caller must carry cumulative consumed bytes across attempts/sessions.
    """
    if type(already_downloaded) is not int or not 0 <= already_downloaded <= PILOT_LIMIT_BYTES:
        raise AcquisitionError("BUDGET", "Invalid cumulative download accounting")
    if not sizes or any(type(size) is not int or size <= 0 for size in sizes):
        raise AcquisitionError("SIZE_UNKNOWN", "Every scientific output needs a known positive size before transfer")
    total = sum(size for size in sizes if size is not None)
    if total > PILOT_LIMIT_BYTES - already_downloaded:
        raise AcquisitionError("BUDGET", "Expected scientific transfer exceeds remaining 250 MB authorization")
    return total


def safe_filename(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,220}", name) or PureWindowsPath(name).is_reserved() or name.endswith((".", " ")):
        raise AcquisitionError("PATH", "Unsafe local artifact filename")


def _local_path(root: Path, name: str) -> Path:
    safe_filename(name)
    root = root.absolute()
    if not root.is_dir():
        raise AcquisitionError("PATH", "Runtime root must be an existing directory")
    if any(part.lower() == "onedrive" or part.lower().startswith("onedrive - ") for part in root.parts):
        raise AcquisitionError("PATH", "Choose runtime storage outside OneDrive")
    for candidate in (root, *root.parents):
        details = candidate.lstat()
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
            raise AcquisitionError("PATH", "Runtime root cannot traverse symlinks or reparse points")
    target = root / name
    if target.is_symlink() or (target.exists() and getattr(target.lstat(), "st_file_attributes", 0) & 0x400):
        raise AcquisitionError("PATH", "Artifact cannot be a link or reparse point")
    return target


def new_destination(root: Path, name: str) -> Path:
    """Validate a destination; a future writer must still create exclusively."""
    target = _local_path(root, name)
    if target.exists():
        raise AcquisitionError("EXISTS", "Existing artifacts must never be overwritten")
    return target


@dataclass(frozen=True)
class SourceAsset:
    collection_id: str
    granule_id: str
    producer_id: str
    collection_short_name: str
    url: str
    catalog_size_bytes: int | None
    advertised_checksum: str | None = None
    advertised_checksum_algorithm: str | None = None

    def __post_init__(self) -> None:
        identifier(self.collection_id, "C")
        identifier(self.granule_id, "G")
        safe_filename(self.producer_id + ".h5")
        safe_filename(self.collection_short_name)
        parts = urlsplit(self.url)
        expected = f"/NISAR/{self.collection_short_name}/{self.producer_id}/{self.producer_id}.h5"
        if parts.scheme != "https" or parts.netloc != SOURCE_HOST or parts.path != expected or parts.query or parts.fragment or parts.username or parts.password:
            raise AcquisitionError("REFERENCE", "Expected the exact unsigned NASA NISAR HDF5 source reference")
        if self.catalog_size_bytes is not None and (type(self.catalog_size_bytes) is not int or self.catalog_size_bytes <= 0):
            raise AcquisitionError("SIZE_UNKNOWN", "Invalid catalog source size")
        if (self.advertised_checksum is None) != (self.advertised_checksum_algorithm is None):
            raise AcquisitionError("CHECKSUM", "Checksum algorithm and value must be supplied together")
        if self.advertised_checksum is not None:
            length = {"MD5": 32, "SHA-256": 64}.get(self.advertised_checksum_algorithm or "")
            if length is None or re.fullmatch(r"[0-9a-fA-F]{" + str(length) + "}", self.advertised_checksum) is None:
                raise AcquisitionError("CHECKSUM", "Unsupported or malformed advertised checksum")


def source_asset(item: dict[str, Any], observation: ObservationDescriptor) -> SourceAsset:
    if item["meta"]["concept-id"] != observation.granule_concept_id or item["meta"]["collection-concept-id"] != observation.collection_concept_id:
        raise AcquisitionError("IDENTITY", "Source record disagrees with the reviewed observation")
    urls = [r.url for r in observation.product_references if r.relation == "GET DATA" and r.format == "HDF5" and r.url.startswith("https://")]
    archive = [r for r in item["umm"].get("DataGranule", {}).get("ArchiveAndDistributionInformation", []) if r.get("Name") == observation.producer_granule_id + ".h5" and r.get("Format") == "HDF5"]
    if len(urls) != 1 or len(archive) != 1 or not observation.collection_short_name:
        raise AcquisitionError("REFERENCE", "Exactly one reconciled canonical source and HDF5 size record required")
    checksum = archive[0].get("Checksum", {})
    return SourceAsset(observation.collection_concept_id, observation.granule_concept_id, observation.producer_granule_id,
                       observation.collection_short_name, urls[0], archive[0].get("SizeInBytes"), checksum.get("Value"), checksum.get("Algorithm"))


def validate_capabilities(data: dict[str, Any], collection_id: str, layers: Sequence[str]) -> dict[str, Any]:
    """Collection variable lists are possible variables, not per-granule contents."""
    if data.get("conceptId") != collection_id:
        raise AcquisitionError("IDENTITY", "Harmony capabilities belong to a different collection")
    if not layers or len(set(layers)) != len(layers) or any(layer not in DEFAULT_LAYERS for layer in layers):
        raise AcquisitionError("LAYER", "Pilot supports only explicit Frequency A HHHH/mask/numberOfLooks layers")
    available = {v["name"] for v in data.get("variables", [])}
    if not set(layers) <= available:
        raise AcquisitionError("LAYER", "Required variables are not advertised for this collection")
    matching = []
    for service in data.get("services", []):
        capabilities = service.get("capabilities", {})
        subsetting = capabilities.get("subsetting", {})
        if subsetting.get("bbox") is True and subsetting.get("variable") is True and any(f.get("mimeType") == "image/tiff" for f in capabilities.get("outputFormats", [])):
            matching.append(service["name"])
    if not matching:
        raise AcquisitionError("MODE", "No individual service advertises the required combined COG/bbox/variable capabilities")
    return {"collection_id": collection_id, "matching_services": matching, "layers": tuple(layers),
            "collection_capability_verified": True, "selected_granule_execution_verified": False,
            "warning": "Selected variables may be absent in a particular granule; inspect actual outputs and metadata"}


def anonymous_head(asset: SourceAsset, *, timeout: float = 20) -> dict[str, Any]:
    """HEAD only. No cookies/auth, redirect following, response body or assets."""
    try:
        with build_opener(NoRedirect()).open(Request(asset.url, method="HEAD", headers={"User-Agent": "sentinel-zero/0.1.0 SZ-004 HEAD-preflight"}), timeout=timeout) as response:
            return {"status": response.status, "headers": dict(response.headers.items())}
    except HTTPError as exc:
        result = {"status": exc.code, "headers": dict(exc.headers.items())}
        exc.close()
        return result
    except (URLError, OSError, TimeoutError):
        raise AcquisitionError("NETWORK", "Source HEAD request failed; no scientific body retrieved") from None


def probe_head(asset: SourceAsset, transport: Callable[[SourceAsset], dict[str, Any]] = anonymous_head) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        response = transport(asset)
    except AcquisitionError as exc:
        return {"granule_id": asset.granule_id, "requested_at": started, "retrieved_at": datetime.now(timezone.utc), "outcome": exc.code, "scientific_body_bytes": 0}
    headers = {k.lower(): v for k, v in response["headers"].items()}
    status = response["status"]
    redirect_host = urlsplit(headers.get("location", "")).hostname
    if status == 401 or (status in (301, 302, 303, 307, 308) and redirect_host in ("urs.earthdata.nasa.gov", "idfs.earthdatacloud.nasa.gov")):
        outcome = "AUTHENTICATION_REQUIRED"
    elif status == 403:
        outcome = "ACCESS_DENIED"
    elif status == 200:
        outcome = "HEAD_METADATA_ACCESSIBLE"
    else:
        outcome = "REDIRECT_OR_HTTP_FAILURE"
    raw_size = headers.get("content-length")
    size = int(raw_size) if isinstance(raw_size, str) and raw_size.isascii() and raw_size.isdecimal() else None
    return {"granule_id": asset.granule_id, "source_url": asset.url, "method": "HEAD", "requested_at": started,
            "retrieved_at": datetime.now(timezone.utc), "http_status": status, "outcome": outcome,
            "content_length": size, "accept_ranges": headers.get("accept-ranges"),
            "catalog_size_matches_head": None if size is None or status != 200 else size == asset.catalog_size_bytes,
            "scientific_body_bytes": 0, "protected_get_verified": False}


def verify_local_transfer(root: Path, name: str, *, expected_size: int | None, expected_sha256: str, source_granule_id: str) -> dict[str, Any]:
    """Verify a declared local transfer's bytes only; not scientific inspection."""
    identifier(source_granule_id, "G")
    transfer_budget([expected_size])
    if re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256) is None:
        raise AcquisitionError("CHECKSUM", "Expected a SHA-256 baseline")
    target = _local_path(root, name)
    try:
        with target.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
                raise AcquisitionError("PARTIAL_TRANSFER", "Artifact is not a regular file of the expected size")
            value = sha256()
            read_bytes = 0
            while True:
                block = stream.read(min(1024 * 1024, before.st_size - read_bytes + 1))
                if not block:
                    break
                read_bytes += len(block)
                if read_bytes > before.st_size:
                    raise AcquisitionError("CHANGED_DURING_READ", "Artifact grew during verification")
                value.update(block)
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise AcquisitionError("CHANGED_DURING_READ", "Artifact changed during verification")
    except FileNotFoundError:
        raise AcquisitionError("MISSING_FILE", "Expected local artifact is missing") from None
    observed = value.hexdigest()
    if observed != expected_sha256.lower():
        raise AcquisitionError("CHECKSUM", "Local SHA-256 differs from the supplied baseline")
    return {"source_granule_id": source_granule_id, "filename": name, "bytes": before.st_size, "sha256": observed,
            "byte_consistency": "MATCH", "scientific_acceptance": False, "source_identity_independently_verified": False}


def _qa_get(url: str) -> Any:
    """Normal anonymous provider GET, no cookies or credentials; redirects manual."""
    try:
        return build_opener(NoRedirect()).open(Request(url, headers={"User-Agent": "sentinel-zero/0.1.0 SZ-004 bounded-QA-pilot", "Accept-Encoding": "identity"}), timeout=20)
    except HTTPError as exc:
        return exc
    except (URLError, OSError, TimeoutError):
        raise AcquisitionError("NETWORK", "QA request failed without a response") from None


def download_qa_sidecar(asset: SourceAsset, *, filename: str, url: str, expected_size: int,
                        expected_md5: str, root: Path, already_downloaded: int = 0,
                        transport: Callable[[str], Any] = _qa_get) -> dict[str, Any]:
    """Acquire only an explicitly sized provider QA CSV or ISO XML sidecar.

    This is not a raster acquisition or authenticated session. NASA may make
    sidecars accessible through a public CDN. Stop at any authentication flow;
    never reuse HEAD-signed URLs for GET. Redirect URLs remain in memory only.
    No automatic retries: failed/partial bytes count toward the task's budget.
    """
    if filename not in (asset.producer_id + "_QA_SUMMARY.csv", asset.producer_id + ".h5.iso.xml"):
        raise AcquisitionError("MODE", "Only catalog-advertised QA CSV / ISO XML supported in this bounded pilot")
    safe_filename(filename)
    expected_url = asset.url.rsplit("/", 1)[0] + "/" + filename
    if url != expected_url:
        raise AcquisitionError("REFERENCE", "QA URL does not match the exact source granule")
    transfer_budget([expected_size], already_downloaded=already_downloaded)
    if re.fullmatch(r"[a-fA-F0-9]{32}", expected_md5) is None:
        raise AcquisitionError("CHECKSUM", "Provider MD5 metadata required before the sidecar pilot")
    target = new_destination(root, filename)
    temporary = new_destination(root, "part-" + uuid4().hex + ".partial")
    started = datetime.now(timezone.utc)
    current_url, received, hops = url, 0, []
    partial_created = False
    try:
        for _ in range(4):
            parts = urlsplit(current_url)
            if parts.hostname in ("urs.earthdata.nasa.gov", "idfs.earthdatacloud.nasa.gov"):
                raise AcquisitionError("AUTHENTICATION_REQUIRED", "Complete interactive Earthdata Login; no credentials accepted here")
            # CDN was observed in a HEAD redirect from this exact NASA source.
            if parts.scheme != "https" or parts.netloc not in (SOURCE_HOST, "d1mv8zhcvry6x4.cloudfront.net") or parts.username or parts.password:
                raise AcquisitionError("REDIRECT", "Unexpected QA redirect host; investigate before proceeding")
            with transport(current_url) as response:
                status = response.status
                headers = {k.lower(): v for k, v in response.headers.items()}
                hops.append({"hostname": parts.hostname, "http_status": status})
                if status in (301, 302, 303, 307, 308) and "location" in headers:
                    current_url = urljoin(current_url, headers["location"])
                    continue
                if status in (401, 403):
                    raise AcquisitionError("AUTHENTICATION_REQUIRED" if status == 401 else "ACCESS_DENIED", "Provider requires authenticated or permitted access")
                if status != 200:
                    raise AcquisitionError("HTTP", "QA provider returned a non-success status")
                if headers.get("content-encoding", "identity").lower() != "identity":
                    raise AcquisitionError("CONTENT", "Encoded QA response is unsupported for exact byte accounting")
                length = headers.get("content-length")
                if length is None or not length.isascii() or not length.isdecimal() or int(length) != expected_size:
                    raise AcquisitionError("SIZE_UNKNOWN", "Actual response size must match the advertised known size before reading")
                if "html" in headers.get("content-type", "").lower():
                    raise AcquisitionError("CONTENT", "HTML is not a scientific QA sidecar")
                sha, provider_hash = sha256(), md5(usedforsecurity=False)
                with temporary.open("xb") as stream:
                    partial_created = True
                    while received < expected_size:
                        incomplete = False
                        try:
                            block = response.read(min(64 * 1024, expected_size - received))
                        except IncompleteRead as exc:
                            block, incomplete = exc.partial, True
                        if not block:
                            raise AcquisitionError("PARTIAL_TRANSFER", "QA response ended before the expected size")
                        if len(block) > expected_size - received:
                            raise AcquisitionError("CONTENT", "Transport violated bounded read contract")
                        received += len(block)
                        stream.write(block)
                        sha.update(block)
                        provider_hash.update(block)
                        if incomplete:
                            raise AcquisitionError("PARTIAL_TRANSFER", "QA response was interrupted")
                if provider_hash.hexdigest() != expected_md5.lower():
                    raise AcquisitionError("CHECKSUM", "QA bytes disagree with advertised provider checksum")
                # Exclusive hard-link promotion never replaces an existing file.
                os.link(temporary, target)
                temporary.unlink()  # This service-created completed temporary file only.
                return {"schema_version": "1.0", "source_collection_id": asset.collection_id, "source_granule_id": asset.granule_id,
                        "producer_id": asset.producer_id, "access_method": "PROVIDER_QA_SIDECAR", "source_url": url,
                        "filename": filename, "local_path": str(target), "requested_at": started, "retrieved_at": datetime.now(timezone.utc),
                        "hops": hops, "authentication": "ANONYMOUS_NORMAL_PROVIDER_ROUTE", "bytes": received,
                        "cumulative_scientific_bytes": already_downloaded + received, "sha256": sha.hexdigest(),
                        "provider_md5_match": True, "transformations": [], "scientific_acceptance": False,
                        "limitation": "Provider QA/ISO sidecar only. No GCOV raster values, masks or full HDF5 contents inspected; hashes are not NASA authenticity proof."}
        raise AcquisitionError("REDIRECT", "QA redirect hop limit exceeded")
    except AcquisitionError as exc:
        raise AcquisitionError(exc.code, str(exc), bytes_received=received, partial_filename=temporary.name if partial_created and temporary.exists() else None) from None
    except FileExistsError:
        raise AcquisitionError("EXISTS", "Output appeared during acquisition; existing artifact preserved", bytes_received=received,
                               partial_filename=temporary.name if partial_created and temporary.exists() else None) from None
    except OSError:
        raise AcquisitionError("IO", "QA transfer or local write failed", bytes_received=received,
                               partial_filename=temporary.name if partial_created and temporary.exists() else None) from None


def run_preflight(reviewed: dict[str, Any], catalog: dict[str, Any], capabilities: dict[str, Any], *, head_transport: Callable[[SourceAsset], dict[str, Any]] = anonymous_head) -> dict[str, Any]:
    collection_values = reviewed["collection_lookup"]["collections"][0]
    collection = CollectionDescriptor(**{key: collection_values[key] for key in ("concept_id", "short_name", "title", "version", "product_level", "maturity")})
    if collection.concept_id != "C2854338529-ASF" or collection.maturity != "PROVISIONAL":
        raise AcquisitionError("IDENTITY", "This SZ-004 preflight is scoped to the approved provisional collection")
    observations = {o["granule_concept_id"]: o for o in reviewed["discovery"]["observations"]}
    records = {item["meta"]["concept-id"]: item for item in catalog["items"]}
    aoi = BoundingBox(**reviewed["discovery"]["query"]["bounding_box"])
    sources, probes = [], []
    for granule_id in TARGET_IDS:
        observation = parse_granule(records[granule_id], collection, aoi)
        if jsonable(observation) != observations[granule_id]:
            raise AcquisitionError("IDENTITY", "Preserved catalog and reviewed discovery metadata disagree")
        asset = source_asset(records[granule_id], observation)
        sources.append(asset)
        probes.append(probe_head(asset, head_transport))
    support = validate_capabilities(capabilities, collection.concept_id, DEFAULT_LAYERS)
    try:
        transfer_budget([asset.catalog_size_bytes for asset in sources])
        full_status = "WITHIN_BUDGET"
    except AcquisitionError as exc:
        full_status = exc.code
    return {"schema_version": "1.0", "task": "SZ-004", "scope": "ACCESS_PREFLIGHT_ONLY", "metadata_reconciled_with_sz003": True,
            "sources": sources, "anonymous_head_probes": probes, "harmony_capability_assessment": support,
            "full_hdf5_download_status": full_status, "full_hdf5_expected_bytes": sum(a.catalog_size_bytes or 0 for a in sources),
            "pilot": {"granule_id": TARGET_IDS[0], "aoi_label": "Exploratory nested Kelani-area rectangle; not an authoritative basin boundary",
                      "bounding_box_wgs84": [79.95, 6.90, 80.00, 6.95], "layers": DEFAULT_LAYERS,
                      "access_method": "HARMONY_COG", "reprojection": "NONE_REQUESTED", "service_job_submitted": False,
                      "output_total_bytes": None, "transfer_authorized_by_size_guard": False,
                      "download_limit_total_bytes": PILOT_LIMIT_BYTES, "scientific_bytes_downloaded": 0},
            "authentication_status": "AUTHENTICATION_REQUIRED", "data_inspected": False, "pair_accepted_for_analysis": False,
            "scientific_conclusion": "INSUFFICIENT_EVIDENCE"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SZ-004 metadata/HEAD-only access preflight; never downloads scientific data")
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--capabilities", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.suffix.lower() != ".json":
            raise AcquisitionError("EXISTS", "Output must be a new JSON evidence file")
        loaded = []
        for path in (args.reviewed, args.catalog, args.capabilities):
            if path.stat().st_size > 5 * 1024 * 1024:
                raise AcquisitionError("METADATA_LIMIT", "Input metadata exceeds 5 MiB")
            loaded.append(json.loads(path.read_text(encoding="utf-8")))
        result = run_preflight(*loaded)
        encoded = json.dumps(jsonable(result), indent=2, allow_nan=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded)
        print("Preflight recorded; AUTHENTICATION_REQUIRED; scientific bytes=0; pair acceptance=False")
        return 3  # A recorded, blocked scientific-access gate is not success.
    except (AcquisitionError, MetadataError, OSError, ValueError, KeyError, TypeError, AttributeError):
        print("Preflight failed safely: invalid, missing, inconsistent metadata or unsafe output", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
