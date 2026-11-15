"""Bounded anonymous NASA CMR searches; never retrieves product assets.

Reference: https://cmr.earthdata.nasa.gov/search/site/docs/search/api.html
Search After headers are opaque paging cursors, not authentication tokens.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import json
import socket
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .. import __version__
from ..contracts import (BoundingBox, Channel, CollectionDescriptor, DiscoveryFailure,
                         DiscoveryQuery, DiscoveryResult, ObservationDescriptor,
                         PageRecord, ProductReference, jsonable, utc)
from ..errors import DiscoveryError, MetadataError

BASE = "https://cmr.earthdata.nasa.gov/search/"
MAX_BODY = 4 * 1024 * 1024


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse: ...


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def public_http(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    """Anonymous GET only. No cookies, auth, redirects or unbounded response reads."""
    if not url.startswith(BASE) or len(url) > 6000:
        raise DiscoveryError("UNSAFE_URL", "Only bounded NASA CMR search URLs are supported")
    opener = build_opener(NoRedirect())
    try:
        with opener.open(Request(url, headers=dict(headers)), timeout=timeout) as response:
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise DiscoveryError("RESPONSE_LIMIT", "CMR page exceeds 4 MiB; lower page_size")
            return HttpResponse(response.status, dict(response.headers.items()), body)
    except HTTPError as exc:
        # Do not retain error bodies, signed redirect locations or credentials.
        result = HttpResponse(exc.code, {k: v for k, v in exc.headers.items() if k.lower() == "retry-after"}, b"")
        exc.close()
        return result
    except (TimeoutError, socket.timeout) as exc:
        raise DiscoveryError("TIMEOUT", "CMR request timed out") from exc
    except URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise DiscoveryError("TIMEOUT", "CMR request timed out") from exc
        raise DiscoveryError("NETWORK", "CMR network connection failed; no response received") from exc
    except OSError as exc:
        raise DiscoveryError("NETWORK", "CMR connection failed while reading metadata") from exc


def _headers(response: HttpResponse) -> dict[str, str]:
    return {k.lower(): v for k, v in response.headers.items()}


def decode_json(response: HttpResponse) -> dict[str, Any]:
    content_type = _headers(response).get("content-type", "").lower()
    if "json" not in content_type:
        raise DiscoveryError("CONTENT_TYPE", "CMR response is not JSON")
    if len(response.body) > MAX_BODY:
        raise DiscoveryError("RESPONSE_LIMIT", "CMR page exceeds metadata size limit")
    try:
        data = json.loads(response.body)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise DiscoveryError("MALFORMED_JSON", "CMR response is not valid JSON") from exc
    if not isinstance(data, dict):
        raise DiscoveryError("RESPONSE_SCHEMA", "CMR response must be a JSON object")
    return data


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise MetadataError("Expected nonempty text metadata")
    return value


def parse_collection(entry: dict[str, Any]) -> CollectionDescriptor:
    """Parse CMR collections.json (ECHO JSON), not a claimed UMM-C parser."""
    title = entry["dataset_id"]
    maturity = None
    if isinstance(title, str):
        for label in ("PROVISIONAL", "VALIDATED", "BETA"):
            if label in title.upper().split():
                maturity = label
                break
    level = _text(entry.get("processing_level_id"))
    return CollectionDescriptor(entry["id"], entry["short_name"], title, _text(entry.get("version_id")), "L" + level if level and not level.startswith("L") else level, maturity)


def parse_granule(item: dict[str, Any], collection: CollectionDescriptor, aoi: BoundingBox | None = None) -> ObservationDescriptor:
    """Parse documented UMM-G paths and provider attributes; no filename guessing."""
    meta, umm = item["meta"], item["umm"]
    if meta["collection-concept-id"] != collection.concept_id:
        raise MetadataError("Granule belongs to a different collection")
    reference = umm.get("CollectionReference", {})
    if reference.get("ShortName") != collection.short_name or reference.get("Version") != collection.version:
        raise MetadataError("Collection reference disagrees with collection metadata")
    attrs: dict[str, list[str]] = {}
    for entry in umm.get("AdditionalAttributes", []):
        name, values = entry["Name"], entry["Values"]
        if name in attrs or not isinstance(values, list) or not values or any(not isinstance(v, str) or not v for v in values):
            raise MetadataError("Invalid or duplicate additional attribute")
        attrs[name] = values

    def one(name: str) -> str | None:
        values = attrs.get(name)
        if values is None:
            return None
        if len(values) != 1:
            raise MetadataError("Expected single-valued attribute: " + name)
        return values[0]

    identifiers: dict[str, str] = {}
    for entry in umm.get("DataGranule", {}).get("Identifiers", []):
        key = entry.get("IdentifierName") if entry.get("IdentifierType") == "Other" else entry.get("IdentifierType")
        if key in identifiers and identifiers[key] != entry["Identifier"]:
            raise MetadataError("Conflicting data-granule identifiers")
        value = _text(entry["Identifier"])
        if value is None:
            raise MetadataError("Empty data-granule identifier")
        identifiers[key] = value
    producer = identifiers.get("ProducerGranuleId")
    if not producer:
        raise MetadataError("Missing ProducerGranuleId")
    period = umm["TemporalExtent"]["RangeDateTime"]
    warnings: list[str] = []
    polygons = []
    geometry = umm.get("SpatialExtent", {}).get("HorizontalSpatialDomain", {}).get("Geometry", {})
    for polygon in geometry.get("GPolygons", []):
        if polygon.get("ExclusiveZone"):
            warnings.append("Polygon holes unsupported; footprint comparison unavailable")
            polygons = []
            break
        polygons.append(tuple((p["Longitude"], p["Latitude"]) for p in polygon["Boundary"]["Points"]))
    channels = []
    for freq in attrs.get("FREQUENCIES", []):
        bandwidth = one(f"FREQUENCY_{freq}_RANGE_BANDWIDTH")
        channels.append(Channel(freq, tuple(attrs.get(f"FREQUENCY_{freq}_POLARIZATION", [])), float(bandwidth) if bandwidth is not None else None))
    directions = {c["Value"].upper() for p in umm.get("Platforms", []) for i in p.get("Instruments", []) for c in i.get("Characteristics", []) if c.get("Name") == "LookDirection"}
    if len(directions) > 1:
        raise MetadataError("Conflicting look directions")
    references = []
    for entry in umm.get("RelatedUrls", []):
        try:
            references.append(ProductReference(entry["URL"], entry["Type"], entry.get("Format")))
        except MetadataError:
            warnings.append("Excluded noncanonical or credential-related URL")
    level = one("PROCESSING_LEVEL")
    if level is not None and collection.product_level is not None and level != collection.product_level:
        raise MetadataError("Product level conflicts with collection")
    kwargs: dict[str, Any] = dict(
        collection_concept_id=meta["collection-concept-id"], granule_concept_id=meta["concept-id"],
        producer_granule_id=producer, acquisition_start=utc(period["BeginningDateTime"]),
        acquisition_end=utc(period["EndingDateTime"]) if period.get("EndingDateTime") else None,
        collection_short_name=collection.short_name, collection_version=collection.version,
        product_family=one("PRODUCT_TYPE"), product_level=level, maturity=collection.maturity,
        footprint=tuple(polygons), query_aoi=aoi,
        track=int(one("TRACK_NUMBER")) if one("TRACK_NUMBER") is not None else None,
        frame=int(one("FRAME_NUMBER")) if one("FRAME_NUMBER") is not None else None,
        stack_id=one("STACK_ID"), orbit_direction=one("ASCENDING_DESCENDING"),
        look_direction=next(iter(directions), None),
        orbit_numbers=tuple(o["OrbitNumber"] for o in umm.get("OrbitCalculatedSpatialDomains", [])),
        channels=tuple(channels), product_version=one("PRODUCT_VERSION"), crid=identifiers.get("CRID"),
        pge_name=umm.get("PGEVersionClass", {}).get("PGEName"), pge_version=umm.get("PGEVersionClass", {}).get("PGEVersion"),
        sas_version=identifiers.get("SASVersionId"), product_references=tuple(references),
        source_metadata_url=BASE + "concepts/" + meta["concept-id"] + ".umm_json",
        catalog_revision=meta.get("revision-id"), metadata_warnings=tuple(sorted(set(warnings))),
    )
    expected = ("acquisition_end", "collection_version", "product_family", "product_level", "maturity", "footprint", "track", "frame", "stack_id", "orbit_direction", "look_direction", "channels", "product_version", "crid", "pge_name", "pge_version", "sas_version", "product_references")
    missing = [k for k in expected if kwargs[k] is None or kwargs[k] == ()]
    for c in channels:
        if not c.polarizations:
            missing.append(f"frequency_{c.frequency}_polarizations")
        if c.range_bandwidth_mhz is None:
            missing.append(f"frequency_{c.frequency}_bandwidth")
    return ObservationDescriptor(**kwargs, missing_fields=tuple(missing))


class CMRClient:
    def __init__(self, transport: Transport = public_http, *, timeout: float = 25, retries: int = 2,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        if not 0 < timeout <= 60 or type(retries) is not int or not 0 <= retries <= 5:
            raise ValueError("Invalid timeout/retry bound")
        self.transport, self.timeout, self.retries, self.sleep, self.clock = transport, timeout, retries, sleep, clock

    def _request(self, url: str, cursor: str | None) -> tuple[HttpResponse, int]:
        headers = {"Accept": "application/json", "User-Agent": f"sentinel-zero/{__version__} public-metadata-audit"}
        if cursor is not None:
            headers["CMR-Search-After"] = cursor
        for attempt in range(self.retries + 1):
            try:
                response = self.transport(url, headers, self.timeout)
            except (TimeoutError, socket.timeout):
                error = DiscoveryError("TIMEOUT", "CMR request timed out")
                response = None
            except DiscoveryError as exc:
                error, response = exc, None
            if response is not None:
                if response.status == 200:
                    return response, attempt + 1
                error = DiscoveryError("HTTP", f"CMR returned HTTP {response.status}", status=response.status)
            transient = error.code == "TIMEOUT" or error.status in (429, 500, 502, 503, 504)
            if not transient or attempt == self.retries:
                raise error
            delay = float(2 ** attempt)
            retry_after = _headers(response).get("retry-after") if response else None
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    try:
                        delay = max(delay, (parsedate_to_datetime(retry_after) - self.clock()).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        raise DiscoveryError("RETRY_AFTER", "CMR supplied an invalid Retry-After value")
                if delay > 30:
                    raise DiscoveryError("RETRY_DEFERRED", "Retry-After exceeds 30-second retry budget; retry later", status=error.status)
            self.sleep(delay)
        raise AssertionError("Unreachable retry state")

    def search_collections(self, query: DiscoveryQuery) -> DiscoveryResult:
        return self._search(query, None)

    def search_granules(self, query: DiscoveryQuery, collection: CollectionDescriptor) -> DiscoveryResult:
        if query.collection_concept_id != collection.concept_id:
            raise MetadataError("Granule query must identify the supplied collection")
        return self._search(query, collection)

    def _search(self, query: DiscoveryQuery, collection: CollectionDescriptor | None) -> DiscoveryResult:
        params: dict[str, str | int] = {"page_size": query.page_size}
        if query.collection_concept_id:
            params["collection_concept_id"] = query.collection_concept_id
            if collection is None:
                params = {"concept_id": query.collection_concept_id, "page_size": query.page_size}
        else:
            params.update({"short_name": query.short_name or "", "options[short_name][pattern]": "true"})
        if query.bounding_box is not None:
            params["bounding_box"] = query.bounding_box.parameter()
        if query.temporal_start is not None:
            params["temporal"] = jsonable(query.temporal_start) + "," + jsonable(query.temporal_end)
        if collection is not None:
            params["sort_key"] = query.sort_key
        url = BASE + ("granules.umm_json" if collection else "collections.json") + "?" + urlencode(params)
        started = self.clock()
        pages: list[PageRecord] = []
        failures: list[DiscoveryFailure] = []
        observations: list[ObservationDescriptor] = []
        collections: list[CollectionDescriptor] = []
        seen: set[str] = set()
        cursors: set[str] = set()
        cursor, total, complete = None, None, False
        for page in range(1, query.max_pages + 1):
            try:
                requested = self.clock()
                response, attempts = self._request(url, cursor)
                retrieved = self.clock()
                headers = _headers(response)
                if any(headers.get(k, "").lower() == "true" for k in ("cmr-time-out", "cmr-timed-out")):
                    raise DiscoveryError("CMR_TIMEOUT", "CMR reported a partial server-side timeout")
                data = decode_json(response)
                raw_hits = headers["cmr-hits"]
                if not raw_hits.isascii() or not raw_hits.isdecimal():
                    raise DiscoveryError("RESPONSE_SCHEMA", "CMR-Hits must be a nonnegative integer")
                hits = int(raw_hits)
                if hits < 0 or ("hits" in data and (type(data["hits"]) is not int or data["hits"] != hits)):
                    raise DiscoveryError("COUNT_MISMATCH", "CMR hit counts disagree")
                if total is not None and hits != total:
                    raise DiscoveryError("CATALOG_CHANGED", "CMR hit count changed during pagination; repeat query")
                total = hits
                entries = data["items"] if collection else data["feed"]["entry"]
                if not isinstance(entries, list) or len(entries) > query.page_size:
                    raise DiscoveryError("RESPONSE_SCHEMA", "CMR page has invalid entries or exceeds page_size")
                next_cursor = headers.get("cmr-search-after")
                if next_cursor is not None:
                    if not next_cursor or len(next_cursor) > 8192 or "\n" in next_cursor or "\r" in next_cursor:
                        raise DiscoveryError("PAGINATION_TOKEN", "Invalid Search-After cursor")
                pages.append(PageRecord(url, requested, retrieved, response.status, attempts, hits, len(entries), sha256(response.body).hexdigest(), cursor, next_cursor))
                for entry in entries:
                    if len(seen) >= query.max_results:
                        raise DiscoveryError("RESULT_LIMIT", "Result limit reached; retrieval incomplete")
                    try:
                        record = parse_granule(entry, collection, query.bounding_box) if collection else parse_collection(entry)
                        record_id = record.granule_concept_id if isinstance(record, ObservationDescriptor) else record.concept_id
                        if record_id in seen:
                            raise DiscoveryError("DUPLICATE_RECORD", "CMR returned a duplicate concept ID")
                        seen.add(record_id)
                        if isinstance(record, ObservationDescriptor):
                            observations.append(record)
                        else:
                            collections.append(record)
                    except (MetadataError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
                        candidate_id = entry.get("meta", {}).get("concept-id") if isinstance(entry, dict) and isinstance(entry.get("meta"), dict) else entry.get("id") if isinstance(entry, dict) else None
                        failures.append(DiscoveryFailure("RECORD_SCHEMA", "Record contains missing, malformed or inconsistent metadata", page,
                                                         candidate_id if isinstance(candidate_id, str) and len(candidate_id) <= 100 else None))
                if len(seen) > total:
                    raise DiscoveryError("COUNT_MISMATCH", "More records than advertised hits")
                if next_cursor in cursors:
                    raise DiscoveryError("REPEATED_CURSOR", "CMR repeated a Search-After cursor")
                if not entries or next_cursor is None:
                    if len(seen) != total:
                        raise DiscoveryError("MISSING_CURSOR", "CMR ended paging before all unique records were retrieved")
                    complete = not failures
                    break
                if len(seen) == total:
                    # Counts reconcile: no need for an extra empty request.
                    complete = not failures
                    break
                if len(seen) >= query.max_results:
                    raise DiscoveryError("RESULT_LIMIT", "Result limit reached before all matches were retrieved")
                cursors.add(next_cursor)
                cursor = next_cursor
            except DiscoveryError as exc:
                failures.append(DiscoveryFailure(exc.code, str(exc), page, http_status=exc.status))
                break
            except (KeyError, ValueError, TypeError, AttributeError) as exc:
                failures.append(DiscoveryFailure("RESPONSE_SCHEMA", "CMR response missing valid hit count or entries", page))
                break
        else:
            failures.append(DiscoveryFailure("PAGE_LIMIT", "Page limit reached before retrieval completed", query.max_pages))
        return DiscoveryResult(query, started, self.clock(), total, len(seen), complete, tuple(observations), tuple(collections), tuple(pages), tuple(failures))
